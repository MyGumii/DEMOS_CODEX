"""Bridge code_assist_v1 chat turns to the local Codex CLI."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections import deque
from typing import Callable, Iterable

from code_assist_v1.config import WORKSPACE_DIR
from code_assist_v1.job_manager import redact_sensitive
from code_assist_v1.model_invocation import InvocationGuard


CODEX_MODEL_ID = "codex-local"
DEFAULT_MAX_PROMPT_CHARS = 60000


class CodexBridgeError(RuntimeError):
    """A stable, non-secret failure category from one Codex CLI execution."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def codex_model_config() -> dict:
    return {
        "env_id": CODEX_MODEL_ID,
        "runtime": "codex",
        "model": "codex-cli",
        "name": "Codex Local Agent",
        "capabilities": ["code", "agent", "workspace", "second-brain"],
        "context_window": 128000,
        "priority": 0,
        "cost_tier": "local",
        "codex_cwd": os.environ.get("CODE_ASSIST_CODEX_CWD", WORKSPACE_DIR),
        "codex_sandbox": os.environ.get("CODE_ASSIST_CODEX_SANDBOX", "workspace-write"),
        "codex_ephemeral": os.environ.get("CODE_ASSIST_CODEX_EPHEMERAL", "1"),
    }


def codex_cli_available() -> bool:
    return bool(_resolve_codex_bin())


def build_codex_prompt(
    messages: list[dict],
    max_chars: int | None = None,
    *,
    preserve_input: bool = False,
) -> str:
    if type(preserve_input) is not bool:
        raise ValueError("preserve_input must be a boolean")
    parts = [
        "You are Codex Local Agent running behind code_assist_v1.",
        "Follow the evidence priorities in the embedded messages. Web, shell, files, active skills, connectors, memory, project context, and workspace context remain available when needed; do not call a tool merely to repeat sufficient supplied evidence.",
        "If you modify files, keep changes scoped to the configured working directory unless the user explicitly asks otherwise.",
        "If you use tools or modify files, report what changed, what you could not verify, and any material commands or external sources used.",
        "For equations in normal prose, use readable Unicode/plain text notation instead of raw LaTeX.",
        "Do not wrap equations in code fences or inline backticks unless the user explicitly asks for LaTeX source.",
        "When an equation is important, put it on its own separated line/block, then explain variables in prose below it.",
        "For multi-step equations, start continuation lines with '=' so each step is visually separated.",
        "For a long right-hand side, put the left side on one line and start the next line with '='.",
    ]
    for msg in messages:
        role = str(msg.get("role") or "user").upper()
        content = _message_text(msg.get("content"))
        if not content.strip():
            continue
        parts.append(f"\n## {role}\n{content.strip()}")

    prompt = "\n".join(parts).strip()
    # Canonical callers already carry an exact coverage ledger. Head/tail
    # truncation here would falsify that ledger and can split its JSON. Let the
    # provider reject an oversized complete prompt rather than invent coverage.
    if preserve_input:
        return prompt
    limit = max_chars or int(os.environ.get("CODE_ASSIST_CODEX_MAX_PROMPT_CHARS", DEFAULT_MAX_PROMPT_CHARS))
    if len(prompt) <= limit:
        return prompt
    head_budget = max(10000, int(limit * 0.35))
    tail_budget = max(10000, limit - head_budget - 200)
    return (
        prompt[:head_budget]
        + "\n\n[... context truncated for Codex CLI prompt budget ...]\n\n"
        + prompt[-tail_budget:]
    )


def stream_codex_exec(
    messages: list[dict],
    cfg: dict,
    timeout_s: int = 900,
    *,
    preserve_input: bool = False,
    attempt_guard: Callable[[], None] | None = None,
) -> Iterable[dict]:
    guard = None if attempt_guard is None else InvocationGuard(attempt_guard)
    codex_bin = _resolve_codex_bin()
    if not codex_bin:
        requested_bin = os.environ.get("CODE_ASSIST_CODEX_BIN", "codex")
        yield {
            "code": "CODEX_CLI_NOT_FOUND",
            "error": f"Codex CLI not found: {requested_bin}",
            "retryable": False,
            "status": 503,
        }
        return
    if not _codex_cli_authenticated(codex_bin):
        yield {
            "code": "CODEX_NOT_AUTHENTICATED",
            "error": "Codex CLI is not authenticated.",
            "retryable": False,
            "status": 401,
        }
        return

    cwd = str(cfg.get("codex_cwd") or os.environ.get("CODE_ASSIST_CODEX_CWD") or WORKSPACE_DIR)
    os.makedirs(cwd, exist_ok=True)
    sandbox = str(cfg.get("codex_sandbox") or os.environ.get("CODE_ASSIST_CODEX_SANDBOX") or "workspace-write")
    cli_model = str(cfg.get("codex_cli_model") or os.environ.get("CODE_ASSIST_CODEX_CLI_MODEL") or "").strip()
    reasoning_effort = str(
        cfg.get("codex_reasoning_effort")
        or os.environ.get("CODE_ASSIST_CODEX_REASONING_EFFORT")
        or ""
    ).strip().lower()
    if reasoning_effort and reasoning_effort not in {
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    }:
        yield {
            "code": "CODEX_CONFIG_INVALID",
            "error": "Codex reasoning effort is invalid.",
            "retryable": False,
            "status": 500,
        }
        return
    prompt = build_codex_prompt(messages, preserve_input=preserve_input)

    args = [
        codex_bin,
        "exec",
        "--json",
        "--color",
        "never",
        "--sandbox",
        sandbox,
        "--skip-git-repo-check",
        "--cd",
        cwd,
    ]
    if _truthy(cfg.get("codex_ephemeral", os.environ.get("CODE_ASSIST_CODEX_EPHEMERAL", "1"))):
        args.append("--ephemeral")
    if cli_model:
        args.extend(["--model", cli_model])
    if reasoning_effort:
        args.extend(
            ["--config", f'model_reasoning_effort="{reasoning_effort}"']
        )
    args.append("-")

    stderr_tail: deque[str] = deque(maxlen=40)
    proc: subprocess.Popen | None = None
    emitted = ""
    timed_out = threading.Event()
    timeout_timer: threading.Timer | None = None
    stdin_errors: list[BaseException] = []
    stdin_thread: threading.Thread | None = None
    stderr_thread: threading.Thread | None = None
    buffered_events: list[dict] = []

    def deliver(event: dict) -> tuple[dict, ...]:
        # Guarded canonical calls already collect one complete answer. Do not
        # expose a delta before the writer's post-flush fence and child cleanup.
        if guard is None:
            return (event,)
        buffered_events.append(event)
        return ()

    try:
        if guard is not None:
            guard()
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        def _enforce_timeout() -> None:
            assert proc is not None
            if proc.poll() is None:
                timed_out.set()
                _terminate_process_tree(proc)

        timeout_timer = threading.Timer(max(1, int(timeout_s)), _enforce_timeout)
        timeout_timer.daemon = True
        timeout_timer.start()

        def _read_stderr() -> None:
            assert proc is not None and proc.stderr is not None
            for line in proc.stderr:
                if line.strip():
                    stderr_tail.append(line.strip())

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        def _write_stdin() -> None:
            assert proc is not None and proc.stdin is not None
            try:
                if guard is not None:
                    guard()
                proc.stdin.write(prompt)
            except BaseException as exc:
                stdin_errors.append(exc)
            finally:
                try:
                    proc.stdin.close()
                except BaseException as exc:
                    stdin_errors.append(exc)
                if guard is not None:
                    try:
                        # close flushes the text stream. Validate submission
                        # return before accepting any model output.
                        guard()
                    except BaseException as exc:
                        stdin_errors.append(exc)
                if stdin_errors and proc.poll() is None:
                    _terminate_process_tree(proc)

        stdin_thread = threading.Thread(target=_write_stdin, daemon=True)
        stdin_thread.start()

        assert proc.stdout is not None
        for line in proc.stdout:
            if guard is not None:
                guard.raise_if_failed()
            raw = line.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if _event_is_error(event):
                yield from deliver({
                    "code": "CODEX_EXEC_FAILED",
                    "error": _event_error_text(event),
                    "retryable": True,
                    "status": 500,
                })
                continue
            text = _event_delta_text(event)
            if text:
                emitted += text
                yield from deliver({"delta": text})
                continue
            full_text = _event_full_text(event)
            if full_text:
                if full_text.startswith(emitted):
                    delta = full_text[len(emitted):]
                elif emitted and full_text in emitted:
                    delta = ""
                else:
                    delta = ("\n\n" if emitted else "") + full_text
                if delta:
                    emitted += delta
                    yield from deliver({"delta": delta})

        rc = proc.wait(timeout=5)
        stdin_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        if guard is not None:
            guard()
        if stdin_errors:
            raise stdin_errors[0]
        if timed_out.is_set():
            yield from deliver({
                "code": "CODEX_EXEC_TIMEOUT",
                "error": f"codex exec timed out after {timeout_s}s",
                "retryable": True,
                "status": 504,
            })
        elif rc != 0:
            detail = "\n".join(stderr_tail) or f"codex exec exited with code {rc}"
            yield from deliver({
                "code": "CODEX_EXEC_FAILED",
                "error": redact_sensitive(detail),
                "retryable": True,
                "status": 500,
            })
    except subprocess.TimeoutExpired:
        if proc and proc.poll() is None:
            _terminate_process_tree(proc)
        yield from deliver({
            "code": "CODEX_EXEC_TIMEOUT",
            "error": f"codex exec timed out after {timeout_s}s",
            "retryable": True,
            "status": 504,
        })
    except GeneratorExit:
        if proc and proc.poll() is None:
            _terminate_process_tree(proc)
        raise
    except Exception as e:
        if proc and proc.poll() is None:
            _terminate_process_tree(proc)
        if guard is not None:
            guard.raise_if_failed()
        yield from deliver({
            "code": "CODEX_EXEC_FAILED",
            "error": redact_sensitive(e),
            "retryable": True,
            "status": 500,
        })
    finally:
        if timeout_timer is not None:
            timeout_timer.cancel()
        if guard is not None:
            try:
                if proc is not None:
                    _cleanup_guarded_child(proc, stdin_thread, stderr_thread)
            finally:
                # Cleanup failures must not mask the original scope failure.
                guard.raise_if_failed()
    if guard is not None:
        guard()
        yield from buffered_events


def _cleanup_guarded_child(proc, stdin_thread, stderr_thread) -> None:
    """Bounded child/pipe cleanup; never close a stream a live thread owns."""
    if proc.poll() is None:
        _terminate_process_tree(proc)
    try:
        proc.wait(timeout=2)
    except Exception:
        # Still join the pipe workers, even if wait/reaping itself failed.
        pass
    for thread in (stdin_thread, stderr_thread):
        if thread is not None:
            thread.join(timeout=2)
    writer_alive = stdin_thread is not None and stdin_thread.is_alive()
    stderr_alive = stderr_thread is not None and stderr_thread.is_alive()
    for stream, busy in ((proc.stdin, writer_alive), (proc.stdout, False), (proc.stderr, stderr_alive)):
        close = getattr(stream, "close", None)
        if not busy and callable(close):
            try:
                close()
            except Exception:
                pass
    if proc.poll() is None or writer_alive or stderr_alive:
        raise CodexBridgeError("CODEX_EXEC_FAILED", "Codex child cleanup did not finish.", retryable=False)


def _truthy(value) -> bool:
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """Terminate the exact Codex process tree so inherited pipes cannot hang."""

    if proc.poll() is not None:
        return
    pid = getattr(proc, "pid", None)
    if os.name == "nt" and isinstance(pid, int) and pid > 0:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except Exception:
            pass
    if proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            pass


def _resolve_codex_bin() -> str:
    configured = os.environ.get("CODE_ASSIST_CODEX_BIN", "").strip()
    candidates = [configured] if configured else []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            candidates.extend([
                os.path.join(
                    appdata,
                    "npm",
                    "node_modules",
                    "@openai",
                    "codex",
                    "node_modules",
                    "@openai",
                    "codex-win32-x64",
                    "vendor",
                    "x86_64-pc-windows-msvc",
                    "bin",
                    "codex.exe",
                ),
                os.path.join(appdata, "npm", "codex.CMD"),
                os.path.join(appdata, "npm", "codex.cmd"),
                os.path.join(appdata, "npm", "codex.exe"),
            ])
    candidates.append("codex")
    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isfile(candidate):
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return ""


def _codex_cli_authenticated(codex_bin: str) -> bool:
    """Check local CLI auth without exposing credential or account details."""

    try:
        completed = subprocess.run(
            [codex_bin, "login", "status"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    return completed.returncode == 0


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    out.append(str(part.get("text") or ""))
                elif part.get("text"):
                    out.append(str(part.get("text")))
                elif part.get("image_url") or part.get("input_image"):
                    out.append("[image input omitted for Codex CLI bridge]")
            else:
                out.append(str(part))
        return "\n".join(x for x in out if x)
    return str(content or "")


def _event_is_error(event: dict) -> bool:
    event_type = str(event.get("type") or "")
    return event_type in {"error", "turn.failed"} or bool(event.get("error") and event_type != "item.completed")


def _event_error_text(event: dict) -> str:
    err = event.get("error")
    if isinstance(err, dict):
        return redact_sensitive(err.get("message") or err)
    return redact_sensitive(err or event.get("message") or event)


def _event_delta_text(event: dict) -> str:
    event_type = str(event.get("type") or "")
    if "delta" not in event_type:
        return ""
    for key in ("delta", "text"):
        value = event.get(key)
        if isinstance(value, str):
            return value
    item = event.get("item")
    if isinstance(item, dict):
        for key in ("delta", "text"):
            value = item.get(key)
            if isinstance(value, str):
                return value
    params = event.get("params")
    if isinstance(params, dict):
        for key in ("delta", "text"):
            value = params.get(key)
            if isinstance(value, str):
                return value
    return ""


def _event_full_text(event: dict) -> str:
    item = event.get("item")
    if not isinstance(item, dict):
        return ""
    item_type = str(item.get("type") or "")
    if item_type not in {"agent_message", "assistant_message", "message"}:
        return ""
    value = item.get("text") or item.get("content")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return _message_text(value)
    return ""

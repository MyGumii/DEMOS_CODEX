from __future__ import annotations

import threading
import time

from code_assist_v1 import codex_bridge


class _BlockingStdout:
    def __init__(self, killed: threading.Event) -> None:
        self._killed = killed

    def __iter__(self):
        return self

    def __next__(self) -> str:
        self._killed.wait(10)
        raise StopIteration


class _EmptyStream:
    def __iter__(self):
        return iter(())

    def write(self, _value: str) -> None:
        return None

    def close(self) -> None:
        return None


class _BlockingProcess:
    def __init__(self) -> None:
        self.killed = threading.Event()
        self.stdin = _EmptyStream()
        self.stdout = _BlockingStdout(self.killed)
        self.stderr = _EmptyStream()

    def poll(self):
        return 1 if self.killed.is_set() else None

    def kill(self) -> None:
        self.killed.set()

    def wait(self, timeout=None) -> int:
        assert self.killed.wait(timeout)
        return 1


def test_bridge_timeout_interrupts_a_silent_child_process(tmp_path, monkeypatch) -> None:
    process = _BlockingProcess()
    monkeypatch.setattr(codex_bridge, "_resolve_codex_bin", lambda: "codex")
    monkeypatch.setattr(codex_bridge, "_codex_cli_authenticated", lambda _bin: True)
    monkeypatch.setattr(codex_bridge.subprocess, "Popen", lambda *_a, **_k: process)

    started = time.monotonic()
    events = list(
        codex_bridge.stream_codex_exec(
            [{"role": "user", "content": "ping"}],
            {
                "codex_cwd": str(tmp_path),
                "codex_sandbox": "read-only",
                "codex_ephemeral": True,
            },
            timeout_s=1,
        )
    )

    assert time.monotonic() - started < 3
    assert process.killed.is_set()
    assert events == [
        {
            "code": "CODEX_EXEC_TIMEOUT",
            "error": "codex exec timed out after 1s",
            "retryable": True,
            "status": 504,
        }
    ]


def test_bridge_fails_fast_when_cli_has_no_local_authentication(monkeypatch) -> None:
    monkeypatch.setattr(codex_bridge, "_resolve_codex_bin", lambda: "codex")
    monkeypatch.setattr(
        codex_bridge, "_codex_cli_authenticated", lambda _bin: False
    )
    monkeypatch.setattr(
        codex_bridge.subprocess,
        "Popen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not spawn")),
    )

    assert list(
        codex_bridge.stream_codex_exec(
            [{"role": "user", "content": "ping"}],
            {},
            timeout_s=30,
        )
    ) == [
        {
            "code": "CODEX_NOT_AUTHENTICATED",
            "error": "Codex CLI is not authenticated.",
            "retryable": False,
            "status": 401,
        }
    ]


def test_bridge_prompt_preserves_authorized_second_brain_context() -> None:
    prompt = codex_bridge.build_codex_prompt(
        [
            {
                "role": "system",
                "content": (
                    "=== Second Brain Wiki Context ===\n"
                    "wiki_id=source/approved.md\nsource_sha256=abc123"
                ),
            },
            {"role": "user", "content": "Summarize only the supplied evidence."},
        ]
    )

    assert "wiki_id=source/approved.md" in prompt
    assert "source_sha256=abc123" in prompt
    assert "Summarize only the supplied evidence." in prompt


def test_bridge_preserves_codex_tool_configuration(tmp_path, monkeypatch) -> None:
    process = _BlockingProcess()
    captured: dict[str, object] = {}
    monkeypatch.setattr(codex_bridge, "_resolve_codex_bin", lambda: "codex")
    monkeypatch.setattr(codex_bridge, "_codex_cli_authenticated", lambda _bin: True)

    def popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(codex_bridge.subprocess, "Popen", popen)
    events = list(
        codex_bridge.stream_codex_exec(
            [{"role": "user", "content": "Use the supplied Wiki evidence."}],
            {
                "codex_cwd": str(tmp_path),
                "codex_reasoning_effort": "none",
                "codex_sandbox": "read-only",
            },
            timeout_s=1,
        )
    )

    args = captured["args"]
    assert "--ignore-user-config" not in args
    assert 'web_search="disabled"' not in args
    assert "tools.web_search=false" not in args
    assert "features.shell_tool=false" not in args
    assert "agents.enabled=false" not in args
    assert ["--config", 'model_reasoning_effort="none"'] == args[
        args.index("--config") : args.index("--config") + 2
    ]
    assert events[-1]["code"] == "CODEX_EXEC_TIMEOUT"

    prompt = codex_bridge.build_codex_prompt(
        [{"role": "user", "content": "Use the supplied Wiki evidence."}],
    )
    assert "active skills" in prompt
    assert "remain available when needed" in prompt
    assert "do not call a tool merely to repeat sufficient supplied evidence" in prompt
    assert "evidence-only text generation turn" not in prompt

"""Bounded Codex exec JSONL accounting, separate from API finish reasons.

This collector stores only the last completed agent message, not reasoning,
tool output, deltas or all events. Its receipt is a local report, not proof of
model truth, actual execution, or a Chat Completions finish_reason=stop.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Mapping


CODEX_RESPONSE_VERSION = "codex-exec-jsonl-completion-v1"


class CodexResponseError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.retryable = False


@dataclass(frozen=True, slots=True)
class CodexResponsePolicy:
    prompt_bytes: int
    stdout_bytes: int
    stderr_bytes: int
    line_bytes: int
    event_count: int
    text_bytes: int

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in asdict(self).values()):
            raise ValueError("Codex response budgets must be explicit positive integers.")
        if self.line_bytes > self.stdout_bytes:
            raise ValueError("A Codex line budget cannot exceed the stdout budget.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError("Invalid JSON constant.")


class CodexOutputCollector:
    def __init__(self, policy: CodexResponsePolicy, prompt: bytes):
        if type(policy) is not CodexResponsePolicy or not isinstance(prompt, bytes):
            raise ValueError("A Codex policy and exact binary prompt are required.")
        if not prompt or len(prompt) > policy.prompt_bytes:
            raise CodexResponseError("CODEX_PROMPT_TOO_LARGE", "Codex prompt does not fit its byte budget.")
        self.policy = policy
        self._prompt_bytes, self._prompt_hash = len(prompt), sha256(prompt).hexdigest()
        self._lock = RLock()
        self._failure = None
        self._stdout_bytes = self._stderr_bytes = self._events = self._agent_messages = 0
        self._stdout_hash, self._stderr_hash = sha256(), sha256()
        self._thread_started = self._turn_started = self._completed = self._stdout_eof = False
        self._final_text = ""
        self._final_message_id = None
        self._final_phase = None
        self._completed_agent_ids: set[str] = set()
        self._sealed = False

    def _check(self):
        if self._failure is not None:
            raise self._failure
        if self._sealed:
            raise CodexResponseError("CODEX_OUTPUT_INVALID", "Codex output is already finalized.")

    def _fail(self, code, message):
        self._final_text = ""
        if self._failure is None:
            self._failure = CodexResponseError(code, message)
        raise self._failure

    def stderr_line(self, raw: bytes):
        with self._lock:
            self._check()
            if not isinstance(raw, bytes) or len(raw) > self.policy.line_bytes:
                self._fail("CODEX_OUTPUT_TOO_LARGE", "Codex stderr line exceeded its budget.")
            if self._stderr_bytes + len(raw) > self.policy.stderr_bytes:
                self._fail("CODEX_OUTPUT_TOO_LARGE", "Codex stderr exceeded its byte budget.")
            self._stderr_bytes += len(raw)
            self._stderr_hash.update(raw)

    def stdout_line(self, raw: bytes):
        with self._lock:
            self._check()
            if (self._stdout_eof or not isinstance(raw, bytes)
                    or len(raw) > self.policy.line_bytes):
                self._fail("CODEX_OUTPUT_INVALID", "Invalid or oversized Codex stdout line.")
            if self._stdout_bytes + len(raw) > self.policy.stdout_bytes:
                self._fail("CODEX_OUTPUT_TOO_LARGE", "Codex stdout exceeded its byte budget.")
            self._stdout_bytes += len(raw)
            self._stdout_hash.update(raw)
            if not raw.strip():
                return
            if self._completed:
                self._fail("CODEX_OUTPUT_INVALID", "Codex emitted another event after completion.")
            self._events += 1
            if self._events > self.policy.event_count:
                self._fail("CODEX_OUTPUT_TOO_LARGE", "Codex event budget was exhausted.")
            try:
                event = json.loads(raw.decode("utf-8", errors="strict"),
                                   object_pairs_hook=_unique_object, parse_constant=_bad_constant)
            except (ValueError, UnicodeError, RecursionError):
                self._fail("CODEX_OUTPUT_INVALID", "Codex stdout is not valid UTF-8 JSONL.")
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                self._fail("CODEX_OUTPUT_INVALID", "Invalid Codex event shape.")
            kind = event["type"]
            if kind in {"error", "turn.failed"} or event.get("error") is not None:
                self._fail("CODEX_EXEC_FAILED", "Codex reported an execution error.")
            if kind == "thread.started":
                if (self._thread_started or self._turn_started
                        or not isinstance(event.get("thread_id"), str) or not event["thread_id"].strip()):
                    self._fail("CODEX_OUTPUT_INVALID", "Invalid Codex thread start.")
                self._thread_started = True
                return
            if kind == "turn.started":
                if not self._thread_started or self._turn_started:
                    self._fail("CODEX_OUTPUT_INVALID", "Invalid Codex turn start.")
                self._turn_started = True
                return
            if not self._turn_started:
                # The CLI can report bounded, non-agent setup diagnostics after
                # thread.started but before turn.started.  They are not answer
                # text; completion still requires a final agent message,
                # turn.completed, clean EOF, and zero process exit.
                if kind == "item.completed" and self._thread_started:
                    item = event.get("item")
                    if isinstance(item, dict):
                        item_type, identity = item.get("type"), item.get("id")
                        if (isinstance(item_type, str) and item_type.strip()
                                and item_type != "agent_message"
                                and isinstance(identity, str) and identity.strip()
                                and len(item_type) <= 160 and len(identity) <= 160
                                and all(ord(c) >= 32 for c in item_type + identity)):
                            return
                self._fail("CODEX_OUTPUT_INVALID", "Codex output arrived outside its turn.")
            if kind == "turn.completed":
                if not self._final_text or self._final_phase == "commentary":
                    self._fail("CODEX_OUTPUT_INCOMPLETE", "Codex completed without a final agent message.")
                self._completed = True
                return
            if kind.startswith("turn.") or kind.startswith("thread."):
                self._fail("CODEX_OUTPUT_INVALID", "Unexpected Codex lifecycle event.")
            if kind != "item.completed":
                # Intermediate deltas may include reasoning and tool output.
                # The completed final item is authoritative for this lane.
                return
            item = event.get("item")
            if not isinstance(item, dict):
                self._fail("CODEX_OUTPUT_INVALID", "Invalid completed Codex item.")
            item_type, identity = item.get("type"), item.get("id")
            if any(not isinstance(value, str) or not value.strip() or len(value) > 160
                   or any(ord(c) < 32 for c in value) for value in (item_type, identity)):
                self._fail("CODEX_OUTPUT_INVALID", "Invalid completed Codex item identity or type.")
            if item_type != "agent_message":
                # A recoverable tool item's own error is not a failed Codex
                # turn. A top-level event error was already rejected above.
                return
            if item.get("error") is not None:
                self._fail("CODEX_EXEC_FAILED", "Codex final-message item reported an error.")
            text, phase = item.get("text"), item.get("phase")
            if (not isinstance(text, str) or not isinstance(identity, str) or not identity
                    or len(identity) > 160 or any(ord(c) < 32 for c in identity)
                    or phase not in (None, "commentary", "final_answer")):
                self._fail("CODEX_OUTPUT_INVALID", "Invalid Codex agent message.")
            try:
                size = len(text.encode("utf-8", errors="strict"))
            except UnicodeError:
                self._fail("CODEX_OUTPUT_INVALID", "Codex message is not valid UTF-8.")
            if size > self.policy.text_bytes:
                self._fail("CODEX_OUTPUT_TOO_LARGE", "Codex agent message exceeded its text budget.")
            if identity in self._completed_agent_ids:
                self._fail("CODEX_OUTPUT_INVALID", "Codex completed the same agent message twice.")
            self._completed_agent_ids.add(identity)
            self._agent_messages += 1
            self._final_text, self._final_message_id, self._final_phase = text, identity, phase

    def stdout_eof(self):
        with self._lock:
            self._check()
            self._stdout_eof = True

    def finish(self, *, exit_code: int, cleanup_completed: bool) -> tuple[str, dict]:
        with self._lock:
            self._check()
            if type(exit_code) is not int or exit_code != 0:
                self._fail("CODEX_EXEC_FAILED", "Codex process did not exit successfully.")
            if cleanup_completed is not True or not self._completed or not self._stdout_eof:
                self._fail("CODEX_OUTPUT_INCOMPLETE", "Codex output did not complete cleanly.")
            # Preserve exact item text so both byte counts and hashes can be
            # validated by callers. Presentation may strip only after receipt
            # validation; whitespace-only output is still incomplete.
            text = self._final_text
            if not text.strip():
                self._fail("CODEX_OUTPUT_INCOMPLETE", "Codex returned an empty final message.")
            raw_content, encoded = self._final_text.encode("utf-8"), text.encode("utf-8")
            receipt = {
                "schema_version": 1, "format": CODEX_RESPONSE_VERSION,
                "policy": asdict(self.policy), "prompt_bytes": self._prompt_bytes,
                "prompt_sha256": self._prompt_hash, "stdout_bytes": self._stdout_bytes,
                "stdout_sha256": self._stdout_hash.hexdigest(), "stderr_bytes": self._stderr_bytes,
                "stderr_sha256": self._stderr_hash.hexdigest(), "event_count": self._events,
                "agent_message_count": self._agent_messages, "final_message_id": self._final_message_id,
                "final_message_phase": self._final_phase, "content_bytes": len(raw_content),
                "content_sha256": sha256(raw_content).hexdigest(), "text_bytes": len(encoded),
                "text_sha256": sha256(encoded).hexdigest(), "completion_signal": "turn.completed",
                "exit_code": exit_code, "cleanup_reported_complete": True,
                "provider_finish_reason": None, "absolute_deadline_verified": False,
            }
            self._sealed = True
            self._final_text = ""
            return text, receipt


def validate_codex_response_receipt(value: object, *, text: str, policy: CodexResponsePolicy,
                                    expected_prompt: bytes | None = None) -> dict:
    """Validate a reported result; not a process-local execution attestation."""
    keys = {"schema_version", "format", "policy", "prompt_bytes", "prompt_sha256",
            "stdout_bytes", "stdout_sha256", "stderr_bytes", "stderr_sha256", "event_count",
            "agent_message_count", "final_message_id", "final_message_phase", "content_bytes",
            "content_sha256", "text_bytes", "text_sha256", "completion_signal", "exit_code",
            "cleanup_reported_complete", "provider_finish_reason", "absolute_deadline_verified"}

    def invalid():
        raise CodexResponseError("CODEX_OUTPUT_INVALID", "Invalid Codex completion receipt.")

    if type(policy) is not CodexResponsePolicy or not isinstance(value, Mapping) or set(value) != keys:
        invalid()
    reported_policy = value["policy"]
    if (not isinstance(reported_policy, Mapping) or set(reported_policy) != set(asdict(policy))
            or any(type(limit) is not int for limit in reported_policy.values())):
        invalid()
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["format"] != CODEX_RESPONSE_VERSION or value["policy"] != asdict(policy)
            or value["completion_signal"] != "turn.completed"
            or type(value["exit_code"]) is not int or value["exit_code"] != 0
            or value["cleanup_reported_complete"] is not True
            or value["provider_finish_reason"] is not None or value["absolute_deadline_verified"] is not False
            or value["final_message_phase"] not in (None, "final_answer")):
        invalid()
    for key in ("prompt_bytes", "stdout_bytes", "event_count", "agent_message_count", "content_bytes", "text_bytes"):
        if type(value[key]) is not int or value[key] <= 0:
            invalid()
    if type(value["stderr_bytes"]) is not int or value["stderr_bytes"] < 0:
        invalid()
    for key in ("prompt_sha256", "stdout_sha256", "stderr_sha256", "content_sha256", "text_sha256"):
        if not isinstance(value[key], str) or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None:
            invalid()
    identity = value["final_message_id"]
    if not isinstance(identity, str) or not identity or len(identity) > 160 or any(ord(c) < 32 for c in identity):
        invalid()
    if (value["prompt_bytes"] > policy.prompt_bytes or value["stdout_bytes"] > policy.stdout_bytes
            or value["stderr_bytes"] > policy.stderr_bytes or value["event_count"] > policy.event_count
            or value["agent_message_count"] > value["event_count"]
            or not value["text_bytes"] == value["content_bytes"] <= policy.text_bytes
            or value["content_bytes"] > value["stdout_bytes"]
            or value["text_bytes"] != len(text.encode("utf-8"))
            or value["text_sha256"] != sha256(text.encode("utf-8")).hexdigest()
            or value["content_sha256"] != value["text_sha256"]):
        invalid()
    if expected_prompt is not None and (len(expected_prompt) != value["prompt_bytes"]
                                       or sha256(expected_prompt).hexdigest() != value["prompt_sha256"]):
        invalid()
    return dict(value)

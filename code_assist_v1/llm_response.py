"""Opt-in, bounded non-streaming Chat Completions response consumption.

The HTTP client must use stream=True, suppress redirect body consumption, and
close the borrowed response. This bounds the identity-encoded JSON entity body
retained by the application, not headers, socket buffers, JSON object overhead,
provider memory, or absolute elapsed time. No model calls occur in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Callable, Mapping


CHAT_RESPONSE_VERSION = "chat-completions-identity-json-v1"
CHAT_FAILURE_DIAGNOSTICS_VERSION = "chat-completions-failure-diagnostics-v1"
_INCOMPLETE_FINISH_REASONS = frozenset({"length", "tool_calls", "function_call", "content_filter"})
_USAGE_COUNTERS = frozenset({"prompt_tokens", "completion_tokens", "total_tokens"})
_USAGE_DETAILS = {"completion_tokens_details": "reasoning_tokens", "prompt_tokens_details": "cached_tokens"}
_READ_CHUNK_BYTES = 16_384


class ModelResponseError(ValueError):
    """Content-free failure category; never expose provider response bodies."""

    def __init__(self, code: str, message: str, *, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = False
        # Numeric transport metadata only. No provider body/header text is kept.
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None


@dataclass(frozen=True, slots=True)
class ChatResponsePolicy:
    envelope_bytes: int
    text_bytes: int

    def __post_init__(self):
        for size in (self.envelope_bytes, self.text_bytes):
            if type(size) is not int or size <= 0:
                raise ValueError("Response envelope/text byte budgets must be positive integers.")


def _invalid(_value=None):
    raise ModelResponseError("MODEL_RESPONSE_INVALID", "Invalid Chat Completions response.")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _diagnostic_sink(value):
    if value is not None and (type(value) is not dict or value):
        raise ValueError("Failure diagnostics require an empty invocation-local dictionary.")


def validate_chat_failure_diagnostics(value: object) -> dict:
    """Validate content-free accounting; not provider attestation or source truth."""
    keys = {"schema_version", "format", "failure_code", "finish_reason", "finish_reason_state",
            "envelope_bytes", "envelope_sha256", "content_bytes", "reasoning_content_bytes", "usage"}
    if type(value) is not dict or set(value) != keys:
        _invalid()
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["format"]) is not str or value["format"] != CHAT_FAILURE_DIAGNOSTICS_VERSION
            or type(value["failure_code"]) is not str or value["failure_code"] != "MODEL_RESPONSE_INCOMPLETE"
            or type(value["envelope_bytes"]) is not int or value["envelope_bytes"] <= 0
            or type(value["envelope_sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", value["envelope_sha256"]) is None):
        _invalid()
    state, finish = value["finish_reason_state"], value["finish_reason"]
    if (type(state) is not str or state not in {"reported", "missing", "null", "unrecognized"}
            or (state == "reported" and (type(finish) is not str or finish not in _INCOMPLETE_FINISH_REASONS))
            or (state != "reported" and finish is not None)):
        _invalid()
    for key in ("content_bytes", "reasoning_content_bytes"):
        size = value[key]
        if size is not None and (type(size) is not int or not 0 <= size <= value["envelope_bytes"]):
            _invalid()
    usage = value["usage"]
    if type(usage) is not dict or not set(usage) <= _USAGE_COUNTERS | _USAGE_DETAILS.keys():
        _invalid()
    copied_usage = {}
    for key, counter in usage.items():
        if key in _USAGE_COUNTERS:
            if type(counter) is not int or counter < 0:
                _invalid()
            copied_usage[key] = counter
        else:
            detail_key = _USAGE_DETAILS[key]
            if (type(counter) is not dict or set(counter) != {detail_key}
                    or type(counter[detail_key]) is not int or counter[detail_key] < 0):
                _invalid()
            copied_usage[key] = {detail_key: counter[detail_key]}
    return {**value, "usage": copied_usage}


def _incomplete_diagnostics(raw, data, choice):
    finish = choice.get("finish_reason")
    reported = type(finish) is str and finish in _INCOMPLETE_FINISH_REASONS
    state = ("reported" if reported else "missing" if "finish_reason" not in choice
             else "null" if finish is None else "unrecognized")
    usage, supplied_usage = {}, data.get("usage")
    if type(supplied_usage) is dict:
        for key in _USAGE_COUNTERS:
            counter = supplied_usage.get(key)
            if type(counter) is int and counter >= 0:
                usage[key] = counter
        for key, detail_key in _USAGE_DETAILS.items():
            detail = supplied_usage.get(key)
            counter = detail.get(detail_key) if type(detail) is dict else None
            if type(counter) is int and counter >= 0:
                usage[key] = {detail_key: counter}
    message = choice.get("message")

    def byte_count(key):
        text = message.get(key) if type(message) is dict else None
        if type(text) is not str:
            return None
        try:
            return len(text.encode("utf-8", errors="strict"))
        except UnicodeError:
            return None

    return validate_chat_failure_diagnostics({
        "schema_version": 1, "format": CHAT_FAILURE_DIAGNOSTICS_VERSION,
        "failure_code": "MODEL_RESPONSE_INCOMPLETE", "finish_reason": finish if reported else None,
        "finish_reason_state": state, "envelope_bytes": len(raw), "envelope_sha256": sha256(raw).hexdigest(),
        "content_bytes": byte_count("content"), "reasoning_content_bytes": byte_count("reasoning_content"),
        "usage": usage,
    })


def parse_chat_response(raw: bytes | bytearray, policy: ChatResponsePolicy, *,
                        failure_diagnostics: dict | None = None) -> tuple[str, dict]:
    """Parse one already-bounded UTF-8 JSON entity; reject partial completions."""
    _diagnostic_sink(failure_diagnostics)
    if type(policy) is not ChatResponsePolicy or not isinstance(raw, (bytes, bytearray)):
        _invalid()
    if len(raw) > policy.envelope_bytes:
        raise ModelResponseError("MODEL_RESPONSE_TOO_LARGE", "API response envelope exceeded its byte budget.")
    try:
        data = json.loads(raw.decode("utf-8", errors="strict"),
                          object_pairs_hook=_object, parse_constant=_invalid)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ModelResponseError("MODEL_RESPONSE_INVALID", "Invalid Chat Completions JSON response.") from exc
    if (not isinstance(data, dict) or data.get("error") is not None
            or not isinstance(data.get("choices"), list) or len(data["choices"]) != 1):
        _invalid()
    choice = data["choices"][0]
    if (not isinstance(choice, dict) or type(choice.get("index")) is not int
            or choice["index"] != 0):
        _invalid()
    finish = choice.get("finish_reason")
    if finish != "stop":
        # Includes missing/null, token-limit stops, tool requests and refusal.
        # This adapter requests a final text result, not an agent tool loop.
        if failure_diagnostics is not None:
            failure_diagnostics.update(_incomplete_diagnostics(raw, data, choice))
        raise ModelResponseError("MODEL_RESPONSE_INCOMPLETE", "API did not report a complete text response.")
    message = choice.get("message")
    if (not isinstance(message, dict) or message.get("role") != "assistant"
            or message.get("tool_calls") not in (None, [])
            or message.get("function_call") is not None
            or message.get("refusal") not in (None, "")
            or not isinstance(message.get("content"), str)):
        _invalid()
    try:
        content = message["content"].encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ModelResponseError("MODEL_RESPONSE_INVALID", "API text is not valid UTF-8.") from exc
    # Account whitespace too, before the legacy runner's strip convention.
    if len(content) > policy.text_bytes:
        raise ModelResponseError("MODEL_RESPONSE_TOO_LARGE", "API response text exceeded its byte budget.")
    text = message["content"].strip()
    if not text:
        raise ModelResponseError("MODEL_EMPTY_OUTPUT", "API returned no final text.")
    encoded_text = text.encode("utf-8")
    return text, {
        "schema_version": 1, "format": CHAT_RESPONSE_VERSION,
        "finish_reason": finish, "http_status": 200,
        "content_encoding": "identity",
        "envelope_bytes": len(raw), "envelope_sha256": sha256(raw).hexdigest(),
        "content_bytes": len(content), "content_sha256": sha256(content).hexdigest(),
        "text_bytes": len(encoded_text),
        "text_sha256": sha256(encoded_text).hexdigest(),
        "maximum_envelope_bytes": policy.envelope_bytes,
        "maximum_text_bytes": policy.text_bytes,
        "absolute_deadline_verified": False,
    }


def consume_chat_response(response, policy: ChatResponsePolicy,
                          guard: Callable[[], None] | None = None, *,
                          failure_diagnostics: dict | None = None) -> tuple[str, dict]:
    """Read incrementally without response.content/text/json or decompression."""
    _diagnostic_sink(failure_diagnostics)
    if type(policy) is not ChatResponsePolicy:
        _invalid()
    if guard is not None:
        guard()
    if response.status_code != 200:
        # Redirects/errors are never consumed or followed, and body details are
        # not copied into logs. The configured URL must be the final endpoint.
        raise ModelResponseError("MODEL_RESPONSE_HTTP_STATUS", "API did not return HTTP 200.",
                                 http_status=response.status_code)
    encoding = str(response.headers.get("Content-Encoding", "")).strip().lower()
    if encoding not in ("", "identity"):
        raise ModelResponseError("MODEL_RESPONSE_ENCODING_UNSUPPORTED", "API ignored identity content encoding.")
    media_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
    if media_type and media_type != "application/json" and not media_type.endswith("+json"):
        _invalid()
    declared = response.headers.get("Content-Length")
    if declared is not None:
        if not isinstance(declared, str) or not re.fullmatch(r"[0-9]{1,20}", declared.strip()):
            _invalid()
        declared = int(declared)
        if declared > policy.envelope_bytes:
            raise ModelResponseError("MODEL_RESPONSE_TOO_LARGE", "Declared API response exceeds its byte budget.")
    # Trusting Content-Length alone is insufficient: it can be absent or wrong.
    chunks = iter(response.iter_content(chunk_size=min(_READ_CHUNK_BYTES, policy.envelope_bytes + 1),
                                        decode_unicode=False))
    retained = bytearray()
    while True:
        if guard is not None:
            guard()
        try:
            chunk = next(chunks)
        except StopIteration:
            break
        finally:
            if guard is not None:
                guard()
        if not isinstance(chunk, bytes):
            _invalid()
        if len(retained) + len(chunk) > policy.envelope_bytes:
            raise ModelResponseError("MODEL_RESPONSE_TOO_LARGE", "Received API response exceeded its byte budget.")
        retained.extend(chunk)
    if declared is not None and declared != len(retained):
        _invalid()
    result = parse_chat_response(retained, policy, failure_diagnostics=failure_diagnostics)
    if guard is not None:
        guard()
    return result


def validate_chat_response_receipt(value: object, *, text: str, policy: ChatResponsePolicy) -> dict:
    """Cross-check adapter accounting, not semantic truth or remote attestation."""
    keys = {"schema_version", "format", "finish_reason", "http_status", "content_encoding", "envelope_bytes",
            "envelope_sha256", "content_bytes", "content_sha256", "text_bytes", "text_sha256",
            "maximum_envelope_bytes", "maximum_text_bytes", "absolute_deadline_verified"}
    if not isinstance(value, Mapping) or set(value) != keys:
        _invalid()
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["format"] != CHAT_RESPONSE_VERSION or value["finish_reason"] != "stop"
            or type(value["http_status"]) is not int or value["http_status"] != 200
            or value["content_encoding"] != "identity"
            or value["absolute_deadline_verified"] is not False):
        _invalid()
    for key in ("envelope_bytes", "content_bytes", "text_bytes", "maximum_envelope_bytes", "maximum_text_bytes"):
        if type(value[key]) is not int or value[key] <= 0:
            _invalid()
    for key in ("envelope_sha256", "content_sha256", "text_sha256"):
        if not isinstance(value[key], str) or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None:
            _invalid()
    if (value["maximum_envelope_bytes"] != policy.envelope_bytes
            or value["maximum_text_bytes"] != policy.text_bytes
            or value["envelope_bytes"] > policy.envelope_bytes
            or not value["text_bytes"] <= value["content_bytes"] <= policy.text_bytes
            or value["content_bytes"] > value["envelope_bytes"]
            or (value["content_bytes"] == value["text_bytes"]
                and value["content_sha256"] != value["text_sha256"])
            or value["text_bytes"] != len(text.encode("utf-8"))
            or value["text_sha256"] != sha256(text.encode("utf-8")).hexdigest()):
        _invalid()
    return dict(value)

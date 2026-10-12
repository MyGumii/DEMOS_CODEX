"""Opt-in bounded structured context-refusal evidence, never provider prose.

Legacy calls use llm_response unchanged. This lane does not change a request,
increase context, retry, subdivide, or accept an incomplete Wiki response.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re

from .llm_response import ChatResponsePolicy, ModelResponseError, consume_chat_response

CONTEXT_REFUSAL_OPT_IN = "pdf-source-continuation-v1"
CONTEXT_REFUSAL_FORMAT = "provider-context-refusal-v1"
CONTEXT_REFUSAL_CODE = "MODEL_API_CONTEXT_LIMIT"
MAX_ERROR_BODY_BYTES = 16_384
_STATUSES = frozenset({400, 413, 422})
_TYPES = frozenset({"exceed_context_size_error", "invalid_request_error", "context_length_exceeded"})
_KEYS = frozenset({"format", "http_status", "raw_body_sha256", "raw_body_size_bytes",
                   "error_code", "error_type", "request_sha256"})


def _hex(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_context_refusal(value, *, request_sha256=None):
    """Check content-free evidence shape; not a remote cryptographic signature."""
    if type(value) is not dict or set(value) != _KEYS:
        raise ValueError("Invalid context refusal evidence shape.")
    code, kind = value["error_code"], value["error_type"]
    if (value["format"] != CONTEXT_REFUSAL_FORMAT
            or type(value["http_status"]) is not int or value["http_status"] not in _STATUSES
            or not _hex(value["raw_body_sha256"]) or not _hex(value["request_sha256"])
            or type(value["raw_body_size_bytes"]) is not int
            or not 0 < value["raw_body_size_bytes"] <= MAX_ERROR_BODY_BYTES
            or not (code is None or type(code) is int and code == value["http_status"]
                    or type(code) is str and code == "context_length_exceeded")
            or not (kind is None or type(kind) is str and kind in _TYPES)
            or not (code == "context_length_exceeded" or kind == "exceed_context_size_error")):
        raise ValueError("Invalid structured context refusal evidence.")
    if request_sha256 is not None and (not _hex(request_sha256) or value['request_sha256'] != request_sha256):
        raise ValueError("The context refusal differs from its exact request.")
    return dict(value)


def _duplicates_rejected(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key.")
        value[key] = item
    return value


def _constant_rejected(_value):
    raise ValueError("Non-JSON numeric constant.")


def _http_failure(status):
    return ModelResponseError("MODEL_RESPONSE_HTTP_STATUS", "API did not return HTTP 200.",
                              http_status=status)


def consume_context_aware_chat_response(response, policy, guard=None, *,
                                        request_sha256, context_refusal, failure_diagnostics=None):
    """Consume only small error envelopes with exact typed context-limit codes.

    Never inspect error.message, follow redirects, read response.text/content,
    infer from HTTP400 alone, or pass raw provider errors into model prompts.
    Guard exceptions escape before/after every read, including cancellation.
    """
    if (type(policy) is not ChatResponsePolicy or not _hex(request_sha256)
            or type(context_refusal) is not dict or context_refusal):
        raise ValueError("Invalid invocation-local context refusal capture.")
    if guard is not None:
        guard()
    status = response.status_code
    if status == 200:
        return consume_chat_response(response, policy, guard,
                                     failure_diagnostics=failure_diagnostics)
    if type(status) is not int or status not in _STATUSES:
        raise _http_failure(status)
    encoding = str(response.headers.get("Content-Encoding", "")).strip().lower()
    media = str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
    if encoding not in ("", "identity") or not (media == "application/json" or media.endswith("+json")):
        raise _http_failure(status)
    budget = min(policy.envelope_bytes, MAX_ERROR_BODY_BYTES)
    declared = response.headers.get("Content-Length")
    if declared is not None:
        if type(declared) is not str or re.fullmatch(r"[0-9]{1,20}", declared.strip()) is None:
            raise _http_failure(status)
        declared = int(declared)
        if declared > budget:
            raise _http_failure(status)
    chunks = iter(response.iter_content(chunk_size=min(4096, budget + 1), decode_unicode=False))
    raw = bytearray()
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
        if type(chunk) is not bytes or len(raw) + len(chunk) > budget:
            raise _http_failure(status)
        raw.extend(chunk)
    if not raw or declared is not None and declared != len(raw):
        raise _http_failure(status)
    try:
        envelope = json.loads(raw.decode("utf-8", errors="strict"),
                              object_pairs_hook=_duplicates_rejected,
                              parse_constant=_constant_rejected)
        error = envelope.get("error") if type(envelope) is dict else None
        if type(error) is not dict:
            raise ValueError("Missing structured error.")
        evidence = validate_context_refusal({
            "format": CONTEXT_REFUSAL_FORMAT, "http_status": status,
            "raw_body_sha256": sha256(raw).hexdigest(), "raw_body_size_bytes": len(raw),
            "error_code": error.get("code"), "error_type": error.get("type"),
            "request_sha256": request_sha256,
        })
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _http_failure(status) from None
    if guard is not None:
        guard()
    context_refusal.update(evidence)
    failure = ModelResponseError(CONTEXT_REFUSAL_CODE, "API reported a structured context limit.",
                                 http_status=status)
    failure.context_refusal = evidence
    raise failure

"""Pure exact Chat-Completions serialization shared by adapter and receipt checks.

No provider discovery, credentials, transport or application configuration.
"""
from hashlib import sha256
import json


CHAT_WIRE_VERSION = "chat-completions-utf8-v2"


def chat_payload(model, messages, *, max_tokens, temperature):
    return {"model": model, "messages": messages, "temperature": temperature,
            "max_tokens": max_tokens, "stream": False, "tool_choice": "none"}


def encode_chat_payload(payload):
    return json.dumps(payload, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8", errors="strict")


def request_body_receipt(payload, body):
    model = payload.get("model")
    if (not isinstance(model, str) or not model.strip() or len(model) > 160
            or any(ord(char) < 32 for char in model)):
        raise ValueError("The exact transport model must be a valid identifier")
    return {"serializer": CHAT_WIRE_VERSION, "model": model,
            "body_bytes": len(body), "body_sha256": sha256(body).hexdigest()}

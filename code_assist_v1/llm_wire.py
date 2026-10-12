"""Pure exact Chat-Completions serialization shared by adapter and receipt checks.

No provider discovery, credentials, transport or application configuration.
"""
from hashlib import sha256
import json


CHAT_WIRE_VERSION = "chat-completions-utf8-v2"
CHAT_WIRE_VERSION_V3 = "chat-completions-utf8-v3"


class ChatRequestBudgetExceeded(ValueError):
    """The exact encoded request exceeds an explicit pre-transmission budget."""


def validate_chat_template_kwargs(value):
    """One explicit non-thinking mode; no arbitrary provider extension fields."""
    if value is None:
        return None
    if (type(value) is not dict or set(value) != {"enable_thinking"}
            or value["enable_thinking"] is not False):
        raise ValueError("Invalid explicit non-thinking template mode.")
    return {"enable_thinking": False}


def chat_payload(model, messages, *, max_tokens, temperature, response_format=None,
                 chat_template_kwargs=None):
    template_kwargs = validate_chat_template_kwargs(chat_template_kwargs)
    result = {"model": model, "messages": messages, "temperature": temperature,
            "max_tokens": max_tokens, "stream": False, "tool_choice": "none"}
    if response_format is not None:
        if response_format != {"type": "json_object"}:
            if (type(response_format) is not dict or set(response_format)!={'type','json_schema'}
                    or response_format['type']!='json_schema'
                    or type(response_format['json_schema']) is not dict
                    or set(response_format['json_schema'])!={'name','strict','schema'}
                    or response_format['json_schema']['name']!='wiki_section'
                    or response_format['json_schema']['strict'] is not True
                    or type(response_format['json_schema']['schema']) is not dict):
                raise ValueError("Invalid explicit structured-output mode.")
        result["response_format"] = dict(response_format)
    if template_kwargs is not None:
        result["chat_template_kwargs"] = template_kwargs
    return result


def encode_chat_payload(payload):
    return json.dumps(payload, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8", errors="strict")


def request_body_receipt(payload, body):
    model = payload.get("model")
    if (not isinstance(model, str) or not model.strip() or len(model) > 160
            or any(ord(char) < 32 for char in model)):
        raise ValueError("The exact transport model must be a valid identifier")
    serializer = CHAT_WIRE_VERSION
    if "chat_template_kwargs" in payload:
        if validate_chat_template_kwargs(payload["chat_template_kwargs"]) is None:
            raise ValueError("An explicit template mode cannot be null.")
        serializer = CHAT_WIRE_VERSION_V3
    return {"serializer": serializer, "model": model,
            "body_bytes": len(body), "body_sha256": sha256(body).hexdigest()}

"""Lossless provenance factoring at the already-authorized answer boundary.

No retrieval, source reads, tokenizer assumptions or provider discovery. Body,
history, manifest and coverage rows are never truncated or reordered here.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

from code_assist_v1.llm_wire import CHAT_WIRE_VERSION


ANSWER_INPUT_FORMAT = "canonical-answer-input-v2-factored-sources"
_SOURCE_FIELDS = frozenset({
    "item_id", "revision_id", "source_space_id", "title", "artifacts", "citation_handle",
})


class AnswerInputError(ValueError):
    pass


def encode_input(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8", errors="strict")


def _body_identities(payload: dict) -> dict[str, dict]:
    result = {}
    for lane, item_key, revision_key in (
        ("retrieval_candidates", "item_id", "revision_id"),
        ("wiki_body_evidence", "item_id", "revision_id"),
        ("pins", "item_id", "revision_id"),
        ("semantic_neighbors", "neighbor_item_id", "neighbor_revision_id"),
        ("mandatory_contexts", "target_item_id", "target_revision_id"),
    ):
        for row in payload.get(lane, []):
            handle = row.get("citation_handle")
            if handle is None:
                continue
            identity = {"item_id": row.get(item_key), "revision_id": row.get(revision_key)}
            if None in identity.values():
                raise AnswerInputError("A supplied citation has no body identity.")
            previous = result.get(handle)
            if previous is not None and any(previous[key] != value for key, value in identity.items()):
                raise AnswerInputError("A supplied citation has conflicting body identities.")
            identity["source_space_id"] = row.get("source_space_id")
            if previous is not None:
                if (previous["source_space_id"] is not None and identity["source_space_id"] is not None
                        and previous["source_space_id"] != identity["source_space_id"]):
                    raise AnswerInputError("A supplied citation has conflicting source spaces.")
                identity["source_space_id"] = previous["source_space_id"] or identity["source_space_id"]
            result[handle] = identity
    return result


def factor_source_documents(payload: dict) -> dict:
    """Join duplicate provenance through the existing turn-bound citation handle.

All nonduplicate fields, including full original-artifact metadata, survive in
``citation_sources[].source_metadata``. The original payload is not mutated.
The exact former source_documents array can be reconstructed, including order.
"""
    if "input_format" in payload or "source_document_order" in payload:
        raise AnswerInputError("An answer input must be prepared exactly once.")
    result = deepcopy(payload)
    sources = result.pop("source_documents", [])
    identities = _body_identities(result)
    citations = {}
    for row in result.get("citation_sources", []):
        handle = row.get("handle")
        if not isinstance(handle, str) or handle in citations or "source_metadata" in row:
            raise AnswerInputError("Invalid answer citation registry.")
        citations[handle] = row
    order = []
    seen = set()
    for source in sources:
        if not _SOURCE_FIELDS <= source.keys():
            raise AnswerInputError("Incomplete server source metadata.")
        handle = source["citation_handle"]
        if handle not in identities or handle not in citations or handle in seen:
            raise AnswerInputError("A source is not bound to supplied body evidence.")
        citation = citations[handle]
        shared = {**identities[handle], "title": citation["title"],
                  "artifacts": [], "citation_handle": handle}
        # Only exact duplicate fields disappear. Unknown fields and differing
        # display labels remain explicit; never infer equality from a filename.
        remainder = {key: value for key, value in source.items()
                     if key not in shared or shared[key] != value}
        if remainder:
            citation["source_metadata"] = remainder
        order.append(handle)
        seen.add(handle)
    result["input_format"] = ANSWER_INPUT_FORMAT
    result["source_document_order"] = order
    return result


def restore_source_documents(payload: dict) -> dict:
    """Lossless inverse used by contract checks, never a new source authority."""
    if payload.get("input_format") != ANSWER_INPUT_FORMAT:
        raise AnswerInputError("Unsupported answer input format.")
    result = deepcopy(payload)
    result.pop("input_format")
    order = result.pop("source_document_order")
    identities = _body_identities(result)
    citations = {row["handle"]: row for row in result.get("citation_sources", [])}
    result["source_documents"] = [
        {**identities[handle], "title": citations[handle]["title"], "artifacts": [],
         "citation_handle": handle, **citations[handle].get("source_metadata", {})}
        for handle in order
    ]
    for citation in citations.values():
        citation.pop("source_metadata", None)
    return result


def answer_messages(payload: dict, *, system: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": system},
            {"role": "user", "content": encode_input(payload).decode("utf-8")}]


def input_receipt(payload: dict, messages: list[dict[str, str]], *, max_output_tokens: int) -> dict:
    """Exact logical input, NOT a provider-wire byte count or token estimate."""
    encoded_payload = encode_input(payload)
    encoded_messages = encode_input(messages)
    return {
        "schema_version": 1,
        "measurement_boundary": "executor_messages_before_provider",
        "serializer": "logical-messages-json-utf8-v1",
        "input_format": payload.get("input_format"),
        "payload_bytes": len(encoded_payload),
        "payload_sha256": sha256(encoded_payload).hexdigest(),
        "messages_bytes": len(encoded_messages),
        "messages_sha256": sha256(encoded_messages).hexdigest(),
        "message_content_bytes": sum(len(row["content"].encode("utf-8")) for row in messages),
        "max_output_tokens": max_output_tokens,
        "input_tokens": None,
        "provider_context_limit_tokens": None,
    }


def validated_api_receipt(value: object) -> dict | None:
    """Copy only the trusted adapter's bounded, content-free transport receipt."""
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("serializer") != CHAT_WIRE_VERSION:
        raise AnswerInputError("Invalid model transport receipt.")
    model, size, digest = value.get("model"), value.get("body_bytes"), value.get("body_sha256")
    if (not isinstance(model, str) or not model.strip() or len(model) > 160
            or any(ord(char) < 32 for char in model)
            or type(size) is not int or size <= 0 or not isinstance(digest, str)
            or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)):
        raise AnswerInputError("Invalid model transport receipt.")
    return {"serializer": CHAT_WIRE_VERSION, "model": model, "body_bytes": size, "body_sha256": digest}

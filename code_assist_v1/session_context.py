"""Session context accounting and compaction helpers."""
from __future__ import annotations

import math
import re
import time
from typing import Any


WARN_RATIO = 0.65
AUTO_COMPACT_RATIO = 0.75
FORCE_COMPACT_RATIO = 0.85
DEFAULT_RECENT_MESSAGES = 12
SUMMARY_MAX_CHARS = 6000


def text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                if item.get("type") in {"text", "input_text"}:
                    parts.append(text_value(item.get("text")))
                elif item.get("text"):
                    parts.append(text_value(item.get("text")))
                elif item.get("image_url") or item.get("input_image"):
                    parts.append("[image]")
            else:
                parts.append(text_value(item))
        return "\n".join(p for p in parts if p.strip())
    if isinstance(value, dict):
        if "text" in value:
            return text_value(value.get("text"))
        return " ".join(text_value(v) for v in value.values())
    return str(value)


def message_text(message: dict[str, Any]) -> str:
    return text_value((message or {}).get("content"))


def estimate_tokens(text: Any) -> int:
    """Cheap cross-model token estimate.

    It deliberately overestimates Korean/CJK-heavy text a bit so status and
    compact thresholds fail conservative across API, GGUF, and Codex bridge
    models when no exact tokenizer is available.
    """
    s = text_value(text)
    if not s:
        return 0
    cjk = len(re.findall(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af\u3040-\u30ff\u3400-\u9fff]", s))
    other = max(0, len(s) - cjk)
    # CJK often sits near 1 char/token. Latin/code is usually 3-5 chars/token.
    return max(1, math.ceil(cjk * 0.9 + other / 3.7))


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for msg in messages or []:
        total += 4
        total += estimate_tokens((msg or {}).get("role", "message"))
        total += estimate_tokens(message_text(msg))
    return total


def summarize_for_status(messages: list[dict[str, Any]], max_items: int = 6) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in (messages or [])[-max_items:]:
        text = re.sub(r"\s+", " ", message_text(msg)).strip()
        out.append({
            "role": (msg or {}).get("role", "message"),
            "chars": len(text),
            "tokens": estimate_tokens(text),
            "preview": text[:120],
        })
    return out


def recent_messages(messages: list[dict[str, Any]], keep_recent: int = DEFAULT_RECENT_MESSAGES) -> list[dict[str, Any]]:
    if len(messages or []) <= keep_recent:
        return list(messages or [])
    return list((messages or [])[-keep_recent:])


def compactible_messages(
    messages: list[dict[str, Any]],
    keep_recent: int = DEFAULT_RECENT_MESSAGES,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    messages = list(messages or [])
    if len(messages) <= keep_recent:
        return [], messages
    return messages[:-keep_recent], messages[-keep_recent:]


def build_compact_summary(
    messages: list[dict[str, Any]],
    previous_summary: str = "",
    keep_recent: int = DEFAULT_RECENT_MESSAGES,
    max_chars: int = SUMMARY_MAX_CHARS,
) -> tuple[str, int]:
    older, _recent = compactible_messages(messages, keep_recent=keep_recent)
    if not older and previous_summary.strip():
        return previous_summary.strip(), 0
    lines: list[str] = [
        "This is a compacted summary of earlier conversation context.",
        "Use it only to preserve continuity; newer explicit user messages override this summary.",
    ]
    if previous_summary.strip():
        lines.extend(["", "Previous compact summary:", previous_summary.strip()[: max_chars // 2]])
    if older:
        lines.extend(["", "Newly compacted transcript highlights:"])
    used = len("\n".join(lines))
    for idx, msg in enumerate(older, start=1):
        role = str((msg or {}).get("role") or "message")
        body = re.sub(r"\s+", " ", message_text(msg)).strip()
        if not body:
            continue
        body = body[:700]
        item = f"- {role}: {body}"
        if used + len(item) + 1 > max_chars:
            lines.append("- ... earlier details omitted by compaction budget.")
            break
        lines.append(item)
        used += len(item) + 1
    lines.extend([
        "",
        f"Compacted at: {int(time.time())}",
        f"Compacted message count: {len(older)}",
    ])
    return "\n".join(lines).strip(), len(older)


def compact_system_block(summary: str) -> dict[str, str] | None:
    summary = (summary or "").strip()
    if not summary:
        return None
    return {
        "role": "system",
        "content": (
            "=== Compacted Session Context ===\n"
            "The following summary preserves earlier conversation context that no longer fits in full.\n"
            "Do not treat it as canonical Second Brain knowledge.\n\n"
            f"{summary}"
        ),
    }


def build_context_status(
    *,
    model_id: str = "",
    model_kind: str = "",
    context_window: int,
    reserved_output_tokens: int,
    final_messages: list[dict[str, Any]],
    compact_summary: str = "",
    auto_compact_limit: int | None = None,
) -> dict[str, Any]:
    context_window = max(1, int(context_window or 1))
    reserved_output_tokens = max(0, int(reserved_output_tokens or 0))
    input_tokens = estimate_messages_tokens(final_messages)
    total_reserved = input_tokens + reserved_output_tokens
    remaining = max(0, context_window - total_reserved)
    ratio = total_reserved / context_window
    limit = int(auto_compact_limit or max(1, context_window * AUTO_COMPACT_RATIO))
    return {
        "model": model_id,
        "model_kind": model_kind,
        "context_window": context_window,
        "input_tokens_est": input_tokens,
        "reserved_output_tokens": reserved_output_tokens,
        "total_reserved_tokens_est": total_reserved,
        "remaining_tokens_est": remaining,
        "used_ratio": round(ratio, 4),
        "warn_threshold_tokens": int(context_window * WARN_RATIO),
        "auto_compact_threshold_tokens": limit,
        "force_compact_threshold_tokens": int(context_window * FORCE_COMPACT_RATIO),
        "should_warn": ratio >= WARN_RATIO,
        "should_auto_compact": total_reserved >= limit or ratio >= AUTO_COMPACT_RATIO,
        "should_force_compact": ratio >= FORCE_COMPACT_RATIO or total_reserved >= int(context_window * 0.9),
        "compact_summary_tokens_est": estimate_tokens(compact_summary),
        "message_count": len(final_messages or []),
        "recent_messages": summarize_for_status(final_messages),
    }


def should_auto_compact(status: dict[str, Any]) -> bool:
    return bool(status.get("should_auto_compact") or status.get("should_force_compact"))

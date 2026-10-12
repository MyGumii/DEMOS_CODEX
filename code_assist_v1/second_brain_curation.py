"""
LLM-assisted Second Brain curation helpers.

The curator proposes titles, merge/replace drafts, and review recommendations.
It never writes data by itself; routes/store code decide whether to persist a
proposal after user action.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import time
from typing import Any

from code_assist_v1.config import ENV_CONFIG, MODEL_REGISTRY
from code_assist_v1.gguf_loader import resolve_gguf_paths


BAD_TITLE_RE = re.compile(
    r"^(pages?\s*\d|slide\s*\d|document\s+(part|chunk)|raw\s+chunk|chunk\s+\d|untitled|the\s+|this\s+)",
    re.I,
)


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


_CURATOR_MAX_TOKENS = _env_int("SECOND_BRAIN_CURATOR_MAX_TOKENS", 512, minimum=128, maximum=1200)
_CURATOR_SOURCE_CHARS = _env_int("SECOND_BRAIN_CURATOR_SOURCE_CHARS", 1800, minimum=400, maximum=6000)
_CURATOR_ITEM_CHARS = _env_int("SECOND_BRAIN_CURATOR_ITEM_CHARS", 1200, minimum=300, maximum=4000)
_CURATOR_SIMILAR_LIMIT = _env_int("SECOND_BRAIN_CURATOR_SIMILAR_LIMIT", 3, minimum=1, maximum=8)
_CURATOR_EDGE_LIMIT = _env_int("SECOND_BRAIN_CURATOR_EDGE_LIMIT", 5, minimum=1, maximum=12)
_CURATOR_CACHE_TTL = _env_int("SECOND_BRAIN_CURATOR_CACHE_TTL", 3600, minimum=0, maximum=86400)
_CURATOR_CACHE_MAX = _env_int("SECOND_BRAIN_CURATOR_CACHE_MAX", 128, minimum=0, maximum=2048)
_CURATION_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def needs_title_regeneration(title: str | None) -> bool:
    text = str(title or "").strip()
    if not text:
        return True
    if BAD_TITLE_RE.search(text):
        return True
    if len(text) < 8 or len(text.split()) > 14:
        return True
    if re.match(r"^[a-f0-9-]{12,}$", text, re.I):
        return True
    return False


def heuristic_title(item: dict[str, Any], source_context: str = "") -> str:
    text = str(item.get("content") or item.get("text") or source_context or item.get("title") or "").strip()
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[#>*_`~\[\]{}]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    sentence = re.split(r"(?<=[.!?。！？])\s+|\n+", text)[0] if text else ""
    if not sentence:
        sentence = str(item.get("title") or item.get("id") or "Second Brain thought")
    words = sentence.split()
    if len(words) > 11:
        sentence = " ".join(words[:11])
    sentence = sentence.strip(" .:-")
    if len(sentence) > 90:
        sentence = sentence[:90].rsplit(" ", 1)[0].strip()
    if not sentence:
        sentence = "Second Brain thought"
    memory_type = str(item.get("memory_type") or "").strip()
    suffix = {
        "procedural": "rule",
        "semantic": "definition",
        "episodic": "case",
        "reflective": "lesson",
        "thesis": "claim",
        "topic": "topic",
    }.get(memory_type, "")
    if suffix and suffix.lower() not in sentence.lower():
        sentence = f"{sentence} {suffix}"
    return sentence[:120]


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return {}
    return {}


def _config_enabled(cfg: dict[str, Any]) -> bool:
    if cfg.get("enabled") is False or cfg.get("disabled") is True:
        return False
    return True


def _text_only_config(cfg: dict[str, Any]) -> dict[str, Any]:
    out = dict(cfg)
    gguf_cfg = dict(out.get("gguf") or {})
    gguf_cfg["mmproj_mode"] = "text-only"
    gguf_cfg.pop("mmproj_pattern", None)
    gguf_cfg.pop("mmproj_patterns", None)
    out["gguf"] = gguf_cfg
    out.pop("_mmproj_path", None)
    return out


def _cache_key(task: str, payload: dict[str, Any], max_tokens: int) -> str:
    raw = json.dumps(
        {"task": task, "payload": payload, "max_tokens": max_tokens},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def _cache_get(key: str) -> dict[str, Any] | None:
    if not _CURATOR_CACHE_MAX or not _CURATOR_CACHE_TTL:
        return None
    entry = _CURATION_CACHE.get(key)
    if not entry:
        return None
    ts, value = entry
    if time.time() - ts > _CURATOR_CACHE_TTL:
        _CURATION_CACHE.pop(key, None)
        return None
    out = dict(value)
    out["_curator_cache"] = "hit"
    return out


def _cache_put(key: str, value: dict[str, Any]) -> None:
    if not _CURATOR_CACHE_MAX or not _CURATOR_CACHE_TTL:
        return
    if len(_CURATION_CACHE) >= _CURATOR_CACHE_MAX:
        oldest = min(_CURATION_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _CURATION_CACHE.pop(oldest, None)
    _CURATION_CACHE[key] = (time.time(), dict(value))


def _stop_tokens(cfg: dict[str, Any]) -> list[str]:
    gguf_cfg = cfg.get("gguf") if isinstance(cfg.get("gguf"), dict) else {}
    raw = gguf_cfg.get("stop") or cfg.get("stop")
    if isinstance(raw, list):
        stops = [str(x) for x in raw if str(x)]
    elif isinstance(raw, str) and raw:
        stops = [raw]
    else:
        stops = ["<end_of_turn>", "<eos>", "</s>"]
    return stops


def _candidate_model_configs() -> list[dict[str, Any]]:
    preferred = os.environ.get("SECOND_BRAIN_CURATOR_MODEL", "").strip()
    out: list[dict[str, Any]] = []
    keys = [preferred] if preferred else []
    keys.extend([k for k in ENV_CONFIG.keys() if k not in keys])
    keys.extend([k for k in MODEL_REGISTRY.keys() if k not in keys])
    for key in keys:
        cfg = dict(ENV_CONFIG.get(key) or MODEL_REGISTRY.get(key) or {})
        if not cfg:
            continue
        if not _config_enabled(cfg):
            continue
        runtime = str(cfg.get("runtime") or "").lower()
        env_id = str(cfg.get("env_id") or key)
        if runtime != "gguf" and not env_id.startswith("gguf-"):
            continue
        cfg.setdefault("env_id", env_id)
        if not cfg.get("_gguf_path"):
            resolved = resolve_gguf_paths(cfg)
            if resolved.get("model_path"):
                cfg["_gguf_path"] = resolved["model_path"]
            if resolved.get("mmproj_path"):
                cfg["_mmproj_path"] = resolved["mmproj_path"]
        if cfg.get("_gguf_path"):
            out.append(cfg)
    return out


def _call_local_gguf_json(
    task: str,
    payload: dict[str, Any],
    max_tokens: int | None = None,
) -> tuple[dict[str, Any] | None, str]:
    max_tokens = min(_CURATOR_MAX_TOKENS, max(64, int(max_tokens or _CURATOR_MAX_TOKENS)))
    cache_key = _cache_key(task, payload, max_tokens)
    cached = _cache_get(cache_key)
    if cached:
        return cached, ""
    try:
        from code_assist_v1.internal_llm import run_internal_json

        parsed, meta = run_internal_json(
            task,
            payload,
            system_prompt=(
                "You are a Second Brain curation assistant. "
                "Return compact valid JSON only. Do not include chain-of-thought, markdown, or prose. "
                "Titles must be meaningful node labels: subject + behavior/claim + type."
            ),
            max_tokens=max_tokens,
            temperature=0.1,
        )
    except Exception as e:
        return None, f"internal curator call failed: {e}"
    if not parsed:
        return None, (meta or {}).get("error") or "internal curator unavailable"
    parsed["_curator_model"] = parsed.get("_internal_model_used") or parsed.get("_internal_model_id") or ""
    parsed["_curator_provider"] = parsed.get("_internal_model_kind") or ""
    parsed["_curator_fallback_errors"] = parsed.get("_internal_fallback_errors") or []
    parsed["_curator_cache"] = "miss"
    _cache_put(cache_key, parsed)
    return parsed, ""


def generate_title(item: dict[str, Any], source_context: str = "") -> dict[str, Any]:
    fallback = heuristic_title(item, source_context)
    parsed, error = _call_local_gguf_json(
        "generate_title",
        {
            "item": _compact_item(item),
            "source_context": source_context[:_CURATOR_SOURCE_CHARS],
            "title_rules": "subject + claim/behavior + type, max 12 words, no raw page/chunk labels",
        },
        max_tokens=300,
    )
    title = str((parsed or {}).get("proposed_title") or (parsed or {}).get("title") or "").strip()
    if title and not needs_title_regeneration(title):
        return {"ok": True, "proposed_title": title[:140], "fallback": False, "error": ""}
    return {"ok": False, "proposed_title": fallback, "fallback": True, "error": error or "title fallback used"}


def review_candidate(
    candidate: dict[str, Any],
    similar_thoughts: list[dict[str, Any]] | None = None,
    source_context: str = "",
) -> dict[str, Any]:
    parsed, error = _call_local_gguf_json(
        "review_candidate",
        {
            "candidate": _compact_item(candidate),
            "similar_thoughts": [_compact_item(x) for x in (similar_thoughts or [])[:_CURATOR_SIMILAR_LIMIT]],
            "source_context": source_context[:_CURATOR_SOURCE_CHARS],
            "allowed_actions": ["approve", "merge", "replace", "skip"],
        },
        max_tokens=_CURATOR_MAX_TOKENS,
    )
    if parsed:
        return _normalize_recommendation(parsed, candidate, fallback_error="")
    return _fallback_review(candidate, similar_thoughts or [], error)


def propose_merge(candidate: dict[str, Any], target_thought: dict[str, Any], source_context: str = "") -> dict[str, Any]:
    parsed, error = _call_local_gguf_json(
        "propose_merge",
        {
            "candidate": _compact_item(candidate),
            "target_thought": _compact_item(target_thought),
            "source_context": source_context[:_CURATOR_SOURCE_CHARS],
        },
        max_tokens=_CURATOR_MAX_TOKENS,
    )
    if parsed:
        return _normalize_recommendation(parsed, candidate, target_thought, recommended_action="merge")
    title = heuristic_title({"content": target_thought.get("content") or candidate.get("content"), **candidate}, source_context)
    content = str(target_thought.get("content") or "").rstrip()
    addition = str(candidate.get("content") or "").strip()
    if addition and addition not in content:
        content += "\n\n## Merged source note\n\n" + addition
    return {
        "ok": False,
        "recommended_action": "merge",
        "target_id": _target_id(target_thought),
        "confidence": 0.4,
        "reason": "LLM merge unavailable; appended candidate as source note.",
        "proposed_title": title,
        "proposed_content": content.strip(),
        "proposed_edges": [],
        "risks": [error or "local curator model unavailable"],
        "requires_human_review": True,
        "fallback": True,
    }


def propose_replace(candidate: dict[str, Any], target_thought: dict[str, Any], source_context: str = "") -> dict[str, Any]:
    parsed, error = _call_local_gguf_json(
        "propose_replace",
        {
            "candidate": _compact_item(candidate),
            "target_thought": _compact_item(target_thought),
            "source_context": source_context[:_CURATOR_SOURCE_CHARS],
            "required_edge": "new supersedes old",
        },
        max_tokens=_CURATOR_MAX_TOKENS,
    )
    if parsed:
        return _normalize_recommendation(parsed, candidate, target_thought, recommended_action="replace")
    title = heuristic_title(candidate, source_context)
    return {
        "ok": False,
        "recommended_action": "replace",
        "target_id": _target_id(target_thought),
        "confidence": 0.35,
        "reason": "LLM replace unavailable; candidate content will supersede target if user confirms.",
        "proposed_title": title,
        "proposed_content": str(candidate.get("content") or "").strip(),
        "proposed_edges": [{
            "edge_type": "supersedes",
            "source": "new-thought-id",
            "target": _target_id(target_thought),
            "reason": "user-selected replace action",
        }],
        "risks": [error or "local curator model unavailable"],
        "requires_human_review": True,
        "fallback": True,
    }


def propose_edges(candidate_or_thought: dict[str, Any], nearby_thoughts: list[dict[str, Any]]) -> dict[str, Any]:
    parsed, error = _call_local_gguf_json(
        "propose_edges",
        {
            "item": _compact_item(candidate_or_thought),
            "nearby_thoughts": [_compact_item(x) for x in nearby_thoughts[:_CURATOR_EDGE_LIMIT]],
        },
        max_tokens=_CURATOR_MAX_TOKENS,
    )
    if parsed:
        return parsed
    return {"ok": False, "proposed_edges": [], "error": error or "local curator model unavailable"}


def _compact_item(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    return {
        "id": item.get("id") or meta.get("id"),
        "title": item.get("title") or meta.get("title"),
        "memory_type": item.get("memory_type") or meta.get("memory_type"),
        "domain": item.get("domain") or meta.get("domain"),
        "service": item.get("service") or meta.get("service"),
        "topics": item.get("topics") or meta.get("topics") or [],
        "content": str(item.get("content") or item.get("_content") or "")[:_CURATOR_ITEM_CHARS],
        "sources": item.get("sources") or meta.get("sources") or [],
        "status": item.get("status") or meta.get("status"),
    }


def _target_id(target_thought: dict[str, Any]) -> str:
    meta = target_thought.get("meta") if isinstance(target_thought.get("meta"), dict) else {}
    return str(target_thought.get("id") or meta.get("id") or "")


def _normalize_recommendation(
    data: dict[str, Any],
    candidate: dict[str, Any],
    target_thought: dict[str, Any] | None = None,
    recommended_action: str | None = None,
    fallback_error: str = "",
) -> dict[str, Any]:
    action = str(recommended_action or data.get("recommended_action") or "approve").strip().lower()
    if action not in {"approve", "merge", "replace", "skip"}:
        action = "approve"
    title = str(data.get("proposed_title") or data.get("title") or "").strip()
    if not title or needs_title_regeneration(title):
        title = heuristic_title(candidate, str(data.get("proposed_content") or ""))
    content = str(data.get("proposed_content") or candidate.get("content") or "").strip()
    return {
        "ok": not bool(fallback_error),
        "recommended_action": action,
        "target_id": str(data.get("target_id") or _target_id(target_thought or {}) or ""),
        "confidence": float(data.get("confidence") or 0.5),
        "reason": str(data.get("reason") or fallback_error or "LLM curator recommendation"),
        "proposed_title": title[:140],
        "proposed_content": content,
        "proposed_edges": data.get("proposed_edges") if isinstance(data.get("proposed_edges"), list) else [],
        "risks": data.get("risks") if isinstance(data.get("risks"), list) else ([] if not fallback_error else [fallback_error]),
        "requires_human_review": bool(data.get("requires_human_review", True)),
        "fallback": bool(fallback_error),
        "curator_model": data.get("_curator_model", ""),
    }


def _fallback_review(candidate: dict[str, Any], similar_thoughts: list[dict[str, Any]], error: str) -> dict[str, Any]:
    duplicates = candidate.get("duplicates") if isinstance(candidate.get("duplicates"), list) else []
    best = duplicates[0] if duplicates else {}
    action = "merge" if float(best.get("score") or 0) >= 12 else "approve"
    title = heuristic_title(candidate)
    return {
        "ok": False,
        "recommended_action": action,
        "target_id": best.get("thought_id") or (similar_thoughts[0].get("id") if similar_thoughts else ""),
        "confidence": min(0.75, max(0.25, float(best.get("score") or 0) / 20.0)),
        "reason": "LLM curator unavailable; heuristic recommendation only.",
        "proposed_title": title,
        "proposed_content": str(candidate.get("content") or "").strip(),
        "proposed_edges": candidate.get("suggested_edges") if isinstance(candidate.get("suggested_edges"), list) else [],
        "risks": [error or "local curator model unavailable"],
        "requires_human_review": True,
        "fallback": True,
    }

"""
User-scoped long-term memory for Second Brain Agent.

This is a small Mem0-like layer, not the canonical knowledge store. It keeps
personal/project preferences, recurring decisions, and agent lessons separate
from approved Wiki/spec knowledge.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from code_assist_v1.auth import safe_user_id, user_data_dir


MEMORY_TYPES = {"preference", "project", "decision", "lesson", "conversation"}
MEMORY_STATUSES = {"candidate", "active", "archived", "deleted"}
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}|[가-힣]{2,}")


def _memory_dir(user_id: str | None) -> str:
    uid = safe_user_id(user_id)
    if not uid:
        raise ValueError("authenticated user required")
    return user_data_dir(uid, "memory")


def _memory_path(user_id: str | None) -> str:
    return os.path.join(_memory_dir(user_id), "memories.jsonl")


def _event_path(user_id: str | None) -> str:
    return os.path.join(_memory_dir(user_id), "events.jsonl")


def _now() -> int:
    return int(time.time())


def _text(value: Any) -> str:
    return str(value or "").strip()


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) >= 2]


def _make_id(text: str, memory_type: str = "memory") -> str:
    import hashlib

    base = re.sub(r"[^a-z0-9가-힣_-]+", "-", text.lower()).strip("-_")[:48]
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:8]
    prefix = re.sub(r"[^a-z0-9_-]+", "-", memory_type.lower()).strip("-") or "memory"
    return f"{prefix}-{base or 'item'}-{digest}"


def _read_all(user_id: str | None) -> list[dict[str, Any]]:
    path = _memory_path(user_id)
    if not os.path.isfile(path):
        return []
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("status") != "deleted":
                out.append(item)
    return out


def _write_all(user_id: str | None, items: list[dict[str, Any]]) -> None:
    path = _memory_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def _append_event(user_id: str | None, event: dict[str, Any]) -> None:
    path = _event_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    event = {"at": _now(), **event}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def list_memories(
    user_id: str | None,
    status: str | None = "active",
    memory_type: str | None = None,
    q: str = "",
) -> list[dict[str, Any]]:
    items = _read_all(user_id)
    if status:
        items = [m for m in items if m.get("status") == status]
    if memory_type:
        items = [m for m in items if m.get("memory_type") == memory_type]
    if q.strip():
        scored = search_memories(q, user_id=user_id, max_results=200, statuses={status} if status else None)
        keep = {m["id"] for m in scored}
        items = [m for m in items if m.get("id") in keep]
    items.sort(key=lambda m: (m.get("updated_at") or m.get("created_at") or 0), reverse=True)
    return items


def save_memory(
    user_id: str | None,
    text: str,
    memory_type: str = "preference",
    status: str = "active",
    scope: str = "user",
    tags: list[str] | None = None,
    source: dict[str, Any] | None = None,
    confidence: float = 0.8,
    memory_id: str | None = None,
) -> dict[str, Any]:
    text = _text(text)
    if not text:
        raise ValueError("memory text required")
    if memory_type not in MEMORY_TYPES:
        memory_type = "conversation"
    if status not in MEMORY_STATUSES:
        status = "candidate"
    now = _now()
    item = {
        "id": memory_id or _make_id(text, memory_type),
        "text": text[:2000],
        "memory_type": memory_type,
        "status": status,
        "scope": scope or "user",
        "tags": tags or _guess_tags(text),
        "source": source or {},
        "confidence": round(float(confidence), 3),
        "created_at": now,
        "updated_at": now,
        "last_used_at": 0,
        "use_count": 0,
    }
    items = _read_all(user_id)
    replaced = False
    for idx, old in enumerate(items):
        if old.get("id") == item["id"]:
            item["created_at"] = old.get("created_at") or now
            item["use_count"] = old.get("use_count", 0)
            item["last_used_at"] = old.get("last_used_at", 0)
            items[idx] = item
            replaced = True
            break
    if not replaced:
        # Avoid exact duplicate active memories.
        norm = _normalize_text(text)
        for old in items:
            if old.get("status") != "deleted" and _normalize_text(old.get("text", "")) == norm:
                old["updated_at"] = now
                old["status"] = status if old.get("status") == "candidate" else old.get("status", status)
                _write_all(user_id, items)
                return old
        items.append(item)
    _write_all(user_id, items)
    _append_event(user_id, {"action": "save", "id": item["id"], "status": item["status"], "type": item["memory_type"]})
    return item


def update_memory(user_id: str | None, memory_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    items = _read_all(user_id)
    now = _now()
    for item in items:
        if item.get("id") != memory_id:
            continue
        for key in ("text", "memory_type", "status", "scope", "tags", "confidence"):
            if key in updates:
                item[key] = updates[key]
        if item.get("memory_type") not in MEMORY_TYPES:
            item["memory_type"] = "conversation"
        if item.get("status") not in MEMORY_STATUSES:
            item["status"] = "candidate"
        item["updated_at"] = now
        _write_all(user_id, items)
        _append_event(user_id, {"action": "update", "id": memory_id, "status": item.get("status")})
        return item
    raise FileNotFoundError(memory_id)


def delete_memory(user_id: str | None, memory_id: str, purge: bool = False) -> dict[str, Any]:
    items = _read_all(user_id)
    next_items = []
    found = None
    for item in items:
        if item.get("id") == memory_id:
            found = item
            if not purge:
                item["status"] = "deleted"
                item["updated_at"] = _now()
                next_items.append(item)
            continue
        next_items.append(item)
    if not found:
        raise FileNotFoundError(memory_id)
    _write_all(user_id, next_items)
    _append_event(user_id, {"action": "purge" if purge else "delete", "id": memory_id})
    return {"id": memory_id, "status": "purged" if purge else "deleted"}


def search_memories(
    query: str,
    user_id: str | None,
    max_results: int = 5,
    statuses: set[str] | None = None,
) -> list[dict[str, Any]]:
    q_tokens = _tokens(query)
    if not q_tokens:
        return []
    statuses = statuses or {"active"}
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in _read_all(user_id):
        if item.get("status") not in statuses:
            continue
        hay_parts = [
            item.get("text", ""),
            item.get("memory_type", ""),
            item.get("scope", ""),
            " ".join(item.get("tags") or []),
        ]
        hay = " ".join(hay_parts).lower()
        score = 0.0
        for tok in q_tokens:
            if tok in hay:
                score += 2.0
            if tok in [str(t).lower() for t in item.get("tags") or []]:
                score += 3.0
        if item.get("memory_type") in {"preference", "project"}:
            score += 0.8
        score *= max(0.4, min(1.2, float(item.get("confidence") or 0.8)))
        if score > 0:
            clone = {k: v for k, v in item.items() if k != "source"}
            clone["score"] = round(score, 3)
            clone["source"] = item.get("source") or {}
            scored.append((score, clone))
    scored.sort(key=lambda x: (x[0], x[1].get("updated_at") or 0), reverse=True)
    return [x[1] for x in scored[:max_results]]


def build_memory_context(query: str, user_id: str | None, max_results: int = 5) -> tuple[str, list[str]]:
    results = search_memories(query, user_id=user_id, max_results=max_results)
    if not results:
        return "", []
    lines = [
        "=== User / Project Memory ===",
        "- 아래 내용은 승인된 공식 지식이 아니라 사용자/프로젝트 장기 기억입니다.",
        "- Wiki/spec/source와 충돌하면 Wiki/spec/source를 우선하고 충돌을 언급하세요.",
        "",
    ]
    ids = []
    for item in results:
        ids.append(item["id"])
        lines.append(
            f"- ({item.get('memory_type')}, {item.get('scope')}, confidence {item.get('confidence')}) "
            f"{item.get('text')}"
        )
    return "\n".join(lines), ids


def propose_from_user_message(
    user_id: str | None,
    message: str,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """Create a memory from explicit user intent, or a candidate from weak signals."""
    text = _text(message)
    if len(text) < 12:
        return None
    decision = classify_memory_candidate(text)
    if not decision:
        return None
    source = {"type": "chat", "session_id": session_id or ""}
    return save_memory(
        user_id=user_id,
        text=decision["text"],
        memory_type=decision["memory_type"],
        status=decision["status"],
        scope=decision["scope"],
        tags=decision.get("tags") or [],
        source=source,
        confidence=decision["confidence"],
    )


def classify_memory_candidate(text: str) -> dict[str, Any] | None:
    low = text.lower()
    if _has_forget_intent(low):
        return None

    explicit = any(k in low for k in ("기억해", "remember", "remember this", "저장해", "앞으로", "항상", "기본적으로"))
    clean = _clean_memory_text(text)
    if not clean:
        return None

    signal = _infer_memory_signal(low, explicit=explicit)
    if not signal:
        return None
    if not explicit and _looks_transient_or_question(low, text):
        return None

    memory_type = signal["memory_type"]
    scope = signal["scope"]
    confidence = signal["confidence"]
    status = signal["status"]
    return {
        "text": clean,
        "memory_type": memory_type,
        "scope": scope,
        "status": status,
        "confidence": confidence,
        "tags": _guess_tags(clean),
    }


def _has_forget_intent(low: str) -> bool:
    return any(k in low for k in (
        "기억하지마",
        "기억하지 마",
        "저장하지마",
        "저장하지 마",
        "잊어",
        "forget this",
        "don't remember",
        "do not remember",
        "dont remember",
    ))


def _infer_memory_signal(low: str, explicit: bool) -> dict[str, Any] | None:
    decision_markers = (
        "결정",
        "회의",
        "하기로",
        "기로 했다",
        "기로 했",
        "기로함",
        "하자",
        "정했다",
        "채택",
        "합의",
        "decided",
        "decision",
        "let's",
        "we decided",
        "원칙은",
        "규칙은",
        "정책은",
        "rule",
    )
    project_markers = (
        "이 프로젝트",
        "현재 프로젝트",
        "우리 프로젝트",
        "프로젝트에서는",
        "이 repo",
        "이 레포",
        "repo",
        "repository",
        "this project",
        "this repo",
        "code_assist",
        "app_code",
        "second brain",
        "codex",
        "브릿지",
        "기본 포트",
        "포트는",
        "모델은",
        "구조는",
        "실행 방식",
    )
    lesson_markers = (
        "실패 원인",
        "원인은",
        "주의",
        "다음부터",
        "재발",
        "workaround",
        "해결 방법은",
        "lesson",
    )
    preference_markers = (
        "선호",
        "좋아",
        "싫어",
        "원해",
        "편해",
        "불편",
        "스타일",
        "말투",
        "답변은",
        "내 환경",
        "내 계정",
        "나는 보통",
        "나는 주로",
        "내가 주로",
        "저는 보통",
        "저는 주로",
        "windows",
        "윈도우",
        "powershell",
        "prefer",
        "preference",
        "usually",
        "i like",
        "i dislike",
        "my environment",
        "my account",
    )

    if any(k in low for k in decision_markers):
        return {"memory_type": "decision", "scope": "project", "status": "active", "confidence": 0.76}
    if any(k in low for k in project_markers):
        return {"memory_type": "project", "scope": "project", "status": "active", "confidence": 0.74}
    if any(k in low for k in lesson_markers):
        return {"memory_type": "lesson", "scope": "project", "status": "active", "confidence": 0.7}
    if any(k in low for k in preference_markers):
        return {"memory_type": "preference", "scope": "user", "status": "active", "confidence": 0.72}
    if explicit:
        return {"memory_type": "preference", "scope": "user", "status": "active", "confidence": 0.86}
    return None


def _looks_transient_or_question(low: str, text: str) -> bool:
    stripped = text.strip()
    if "?" in stripped or stripped.endswith(("？", "?", "까", "나", "나요", "인가", "인가요")):
        return True
    return any(k in low for k in (
        "어떻게",
        "무엇",
        "뭐",
        "왜",
        "how",
        "what",
        "why",
        "can you",
        "could you",
        "언제",
        "어디",
        "누구",
        "가능",
        "할 수 있",
        "알려줘",
        "설명해",
        "찾아줘",
        "바꿔줘",
        "고쳐줘",
        "실행해",
        "확인해",
        "분석해",
        "비교해",
        "만들어줘",
        "추가해줘",
        "수정해줘",
        "변경해줘",
    ))


def _clean_memory_text(text: str) -> str:
    text = re.sub(r"^\s*(앞으로|기억해줘|기억해|remember this:?|remember:?|저장해줘|저장해)[,:.\s。·-]*", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1000]


def _guess_tags(text: str) -> list[str]:
    tokens = _tokens(text)
    stop = {"앞으로", "항상", "기본적으로", "please", "remember", "this", "that", "사용자", "프로젝트"}
    out = []
    for tok in tokens:
        if tok in stop:
            continue
        if tok not in out:
            out.append(tok)
        if len(out) >= 8:
            break
    return out


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", _text(text).lower())

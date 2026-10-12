"""Bounded human-readable changes between two authorized page snapshots."""

from __future__ import annotations

from typing import Any


MAX_PREVIEW_ITEMS = 3
MAX_PREVIEW_TEXT = 140


def _brief(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[: MAX_PREVIEW_TEXT - 1] + "…" if len(text) > MAX_PREVIEW_TEXT else text


def _blocks(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("blocks"), list):
        return []
    return [block for block in snapshot["blocks"] if isinstance(block, dict)
            and isinstance(block.get("id"), str) and block["id"]]


def _block_text(block: dict[str, Any]) -> str:
    content = _brief(block.get("content"))
    if content:
        return content
    kind = str(block.get("type") or block.get("block_type") or "")
    return {
        "table": "데이터베이스", "simple_table": "표", "image": "이미지",
        "file": "파일", "audio": "오디오", "video": "동영상", "pdf": "PDF",
        "child_page": "하위 페이지", "linked_database": "연결된 데이터베이스",
    }.get(kind, "빈 블록")


def _databases(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("databases"), list):
        return []
    return [database for database in snapshot["databases"]
            if isinstance(database, dict) and isinstance(database.get("id"), str)
            and database["id"]]


def page_change_preview(
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> dict[str, Any]:
    """Return only a few changed block labels; never infer edits from timestamps."""
    if not isinstance(after, dict):
        raise ValueError("page snapshot required")
    old_blocks = _blocks(before)
    new_blocks = _blocks(after)
    old_by_id = {block["id"]: block for block in old_blocks}
    new_by_id = {block["id"]: block for block in new_blocks}
    changes: list[dict[str, str]] = []
    if before is None or before.get("title") != after.get("title"):
        changes.append({"kind": "title", "text": _brief(after.get("title")) or "제목 없음"})
    if before is not None and before.get("icon") != after.get("icon"):
        changes.append({"kind": "icon", "text": "페이지 아이콘 변경"})
    if before is not None and before.get("cover") != after.get("cover"):
        changes.append({"kind": "cover", "text": "페이지 커버 변경"})
    old_settings = before.get("settings") if isinstance(before, dict) else None
    new_settings = after.get("settings")
    if (isinstance(old_settings, dict) and isinstance(new_settings, dict)
            and bool(old_settings.get("locked")) != bool(new_settings.get("locked"))):
        changes.append({"kind": "locked", "text": "페이지 잠금" if new_settings.get("locked") else "페이지 잠금 해제"})
    for block in new_blocks:
        old = old_by_id.get(block["id"])
        if old is None:
            changes.append({"kind": "added", "text": _block_text(block)})
        elif (old.get("type") != block.get("type")
              or old.get("content") != block.get("content")
              or old.get("props") != block.get("props")):
            changes.append({"kind": "edited", "text": _block_text(block)})
    for block in old_blocks:
        if block["id"] not in new_by_id:
            changes.append({"kind": "deleted", "text": _block_text(block)})
    old_databases = {database["id"]: database for database in _databases(before)}
    new_databases = {database["id"]: database for database in _databases(after)}
    for database in _databases(after):
        old = old_databases.get(database["id"])
        if old != database:
            changes.append({"kind": "database", "text": _brief(database.get("title")) or "데이터베이스"})
    for database in _databases(before):
        if database["id"] not in new_databases:
            changes.append({"kind": "deleted", "text": _brief(database.get("title")) or "데이터베이스"})
    return {
        "items": changes[:MAX_PREVIEW_ITEMS],
        "remaining": max(0, len(changes) - MAX_PREVIEW_ITEMS),
    }


__all__ = ["page_change_preview"]

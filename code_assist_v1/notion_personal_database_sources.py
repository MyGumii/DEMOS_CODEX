"""Authorized catalog for databases embedded in legacy personal pages."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from code_assist_v1 import notion_store as store


LINKABLE_TYPES = frozenset({"table", "board", "list", "gallery", "calendar", "timeline"})


def _source_block(page: dict[str, Any], block_id: str) -> dict[str, Any]:
    for block in page.get("blocks") or []:
        props = block.get("props") if isinstance(block.get("props"), dict) else {}
        if (str(block.get("id") or "") == block_id
                and block.get("type") in LINKABLE_TYPES
                and not props.get("linked_database")):
            return block
    raise FileNotFoundError("database source not found or permission denied")


def read_source(actor: str, page_id: str, block_id: str) -> dict[str, Any]:
    # get_page rechecks the current grant; a catalog result grants no lasting access.
    page = store.get_page(actor, page_id)
    block = _source_block(page, block_id)
    props = block.get("props") if isinstance(block.get("props"), dict) else {}
    raw_views = props.get("views")
    views = (
        [view for view in raw_views
         if isinstance(view, dict) and view.get("type") in LINKABLE_TYPES]
        if isinstance(raw_views, list) else []
    )
    if not views:
        views = [{"name": "현재 보기", "type": block["type"], "config": {}}]
    settings = page["page"].get("settings") or {}
    return {
        "document_id": page_id,
        "page_title": str(page["page"].get("title") or "제목 없음"),
        "editable": bool(page["page"].get("can_edit")
                         and page.get("capabilities", {}).get("atomic_save")
                         and not settings.get("locked")),
        "source_revision": page.get("revision"),
        "block": block,
        "database": {
            "id": block_id,
            "title": str(block.get("content") or "데이터베이스"),
            "views": views,
        },
    }


def create_item(actor: str, page_id: str, block_id: str, *, base_revision: int,
                operation_id: str) -> dict[str, Any]:
    """Create one source row and its page in the same replay-safe transaction."""
    uid = store._uid(actor)
    if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 1:
        raise ValueError("base_revision must be a positive integer")
    if not isinstance(operation_id, str) or not 1 <= len(operation_id.strip()) <= 128:
        raise ValueError("operation_id must contain 1 to 128 characters")
    operation_id = operation_id.strip()
    owner, _role, _page = store._require_page(uid, page_id, "edit")
    receipt_id = "personal-linked-item-v1:" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    request_hash = hashlib.sha256(json.dumps({
        "actor": uid, "page_id": page_id, "block_id": block_id,
        "base_revision": base_revision, "operation_id": operation_id,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    child_id = store.page_id_for_operation(
        uid, namespace=f"personal-linked-item-v1:{owner}", operation_id=operation_id,
    )
    with store._write_connection(owner) as con:
        con.execute("BEGIN IMMEDIATE")
        if not store._atomic_schema_ready(con):
            raise store.AtomicSaveUnavailable("atomic save schema migration is required for this workspace")
        page = con.execute("SELECT * FROM pages WHERE id=? AND archived=0", (page_id,)).fetchone()
        if page is None:
            raise FileNotFoundError("source page not found or permission denied")
        store._effective_edit_role(con, uid, owner, page_id)
        current_revision = max(1, int(page["revision"] or 1))
        prior = con.execute(
            "SELECT request_hash,result_json FROM page_save_operations "
            "WHERE requester_user_id=? AND operation_id=?", (uid, receipt_id),
        ).fetchone()
        if prior is not None:
            if prior["request_hash"] != request_hash:
                raise store.OperationIdConflict(operation_id, current_revision)
            result = store._json_load(prior["result_json"], {})
            if not isinstance(result, dict):
                raise RuntimeError("stored item creation result is invalid")
            child = con.execute("SELECT archived FROM pages WHERE id=?",
                                (result.get("item_page_id"),)).fetchone()
            if child is None or int(child["archived"] or 0):
                raise FileNotFoundError("created item not found or permission denied")
            con.rollback()
            return result
        if current_revision != base_revision:
            raise store.PageRevisionConflict(current_revision)
        if store._page_write_locked(store._json_load(page["settings_json"], {}), uid, page_id):
            raise PermissionError("source page is locked")
        blocks = store._page_blocks_in_connection(con, page_id)
        block = _source_block({"blocks": blocks}, block_id)
        props = block.get("props") if isinstance(block.get("props"), dict) else {}
        rows = props.get("rows") if isinstance(props.get("rows"), list) else props.get("events")
        if not isinstance(rows, list):
            rows = []
        if len(rows) >= 5000:
            raise ValueError("database contains too many items")
        properties = props.get("properties") if isinstance(props.get("properties"), list) else []
        title_field = next((str(prop.get("id")) for prop in properties
                            if isinstance(prop, dict) and prop.get("type") == "title" and prop.get("id")), "title")
        if con.execute("SELECT 1 FROM pages WHERE id=?", (child_id,)).fetchone():
            raise store.OperationIdConflict(operation_id, current_revision)
        now = store._now()
        title = "새 페이지"
        position = con.execute(
            "SELECT COALESCE(MAX(position),0)+1 FROM pages WHERE archived=0 AND parent_id=?",
            (page_id,),
        ).fetchone()[0]
        con.execute(
            "INSERT INTO pages(id,owner_user_id,parent_id,title,icon,cover,settings_json,position,"
            "archived,archived_at,created_at,updated_at) "
            "VALUES(?,?,?,?,?,'','{}',?,0,NULL,?,?)",
            (child_id, owner, page_id, title, "", position, now, now),
        )
        store._replace_blocks_in_connection(con, owner, child_id,
                                            [{"type": "paragraph", "content": "", "props": {}}], now=now)
        store._record_page_version(con, owner, child_id, now=now)
        timestamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
        row = {title_field: title, "page_id": child_id,
               "_created_time": timestamp, "_last_edited_time": timestamp,
               "_created_by": uid, "_last_edited_by": uid}
        for prop in properties:
            if not isinstance(prop, dict) or not prop.get("id") or prop.get("type") == "title":
                continue
            row[str(prop["id"])] = False if prop.get("type") == "checkbox" else ""
        rows.append(row)
        props["rows"] = rows
        props.pop("events", None)
        store._replace_blocks_in_connection(con, owner, page_id, blocks, now=now)
        store._bump_page_revisions(con, {page_id}, now=now)
        store._record_page_version(con, owner, page_id, now=now)
        result = {"item_page_id": child_id, "source_revision": current_revision + 1,
                  "row_index": len(rows) - 1}
        con.execute(
            "INSERT INTO page_save_operations(requester_user_id,operation_id,page_id,owner_user_id,"
            "request_hash,result_revision,result_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid, receipt_id, page_id, owner, request_hash, result["source_revision"],
             store._json_dump(result), now),
        )
        con.commit()
        return result


def list_sources(actor: str, *, query: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
    folded = str(query or "").strip().casefold()
    found = []
    for summary in store.list_pages(actor):
        page_id = str(summary.get("id") or "")
        try:
            page = store.get_page(actor, page_id)
        except (FileNotFoundError, PermissionError):
            continue
        page_title = str(page["page"].get("title") or "제목 없음")
        for block in page.get("blocks") or []:
            props = block.get("props") if isinstance(block.get("props"), dict) else {}
            if block.get("type") not in LINKABLE_TYPES or props.get("linked_database"):
                continue
            title = str(block.get("content") or "데이터베이스")
            if folded and folded not in title.casefold() and folded not in page_title.casefold():
                continue
            found.append({
                "id": str(block.get("id") or ""),
                "document_id": page_id,
                "title": title,
                "page_title": page_title,
                "type": block["type"],
            })
    size, skip = max(1, min(100, int(limit))), max(0, int(offset))
    return {
        "items": found[skip:skip + size],
        "count": len(found),
        "has_more": skip + size < len(found),
        "offset": skip,
    }

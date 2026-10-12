"""Legacy page creation from a local UI draft, using the atomic-save receipt store.

No draft is persisted before this call. The page, initial blocks, parent link and
namespaced operation receipt commit together; existing schemas are not migrated
by this path. Canonical workspace creation retains its owning service contract.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from code_assist_v1 import notion_store as ns


def create_draft_page(
    user_id: str | None, *, operation_id: str, title: str,
    parent_id: str = "", blocks: list[dict[str, Any]], icon: str = "",
) -> dict[str, Any]:
    actor = ns._uid(user_id)
    if not isinstance(operation_id, str) or not 1 <= len(operation_id.strip()) <= 128:
        raise ValueError("operation_id must contain 1 to 128 characters")
    if not isinstance(title, str) or len(title) > 200:
        raise ValueError("title must be a string of at most 200 characters")
    if not isinstance(parent_id, str) or len(parent_id) > 160:
        raise ValueError("parent_id must be a string of at most 160 characters")
    if not isinstance(icon, str) or len(icon) > 64:
        raise ValueError("icon must be a string of at most 64 characters")
    operation_id = operation_id.strip()
    parent_id = parent_id.strip()
    prepared = ns._prepare_blocks(blocks)
    normalized_title = " ".join(title.strip().split())[:160] or "제목 없음"
    request_hash = hashlib.sha256(json.dumps(
        {"kind": "local-page-draft-v1", "title": title, "parent_id": parent_id, "blocks": blocks, "icon": icon},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    receipt_id = "page-create-v1:" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    owner = actor
    if parent_id:
        owner, _role, _parent = ns._require_page(actor, parent_id, "edit")
    page_id = ns.page_id_for_operation(actor, namespace="local-page-draft-v1:" + owner, operation_id=operation_id)
    with ns._write_connection(owner, bootstrap_if_missing=True) as con:
        con.execute("BEGIN IMMEDIATE")
        if not ns._atomic_schema_ready(con):
            raise ns.AtomicSaveUnavailable("atomic save schema migration is required for this workspace")
        role = "owner"
        if parent_id:
            parent = con.execute("SELECT * FROM pages WHERE id=? AND archived=0", (parent_id,)).fetchone()
            if parent is None:
                raise FileNotFoundError("parent page not found or permission denied")
            role = ns._effective_edit_role(con, actor, owner, parent_id)
            if ns._page_write_locked(ns._json_load(parent["settings_json"], {}), actor, parent_id):
                raise PermissionError("parent page is locked")
        prior = con.execute(
            "SELECT request_hash,result_json,result_revision FROM page_save_operations WHERE requester_user_id=? AND operation_id=?",
            (actor, receipt_id),
        ).fetchone()
        if prior is not None:
            if prior["request_hash"] != request_hash:
                raise ns.OperationIdConflict(operation_id, int(prior["result_revision"]))
            current = con.execute("SELECT id FROM pages WHERE id=? AND archived=0", (page_id,)).fetchone()
            if current is None:
                raise FileNotFoundError("created page not found or permission denied")
            ns._effective_edit_role(con, actor, owner, page_id)
            return ns._json_load(prior["result_json"], {})
        now = ns._now()
        position = con.execute(
            "SELECT COALESCE(MAX(position),0)+1 FROM pages WHERE archived=0 AND COALESCE(parent_id,'')=?",
            (parent_id,),
        ).fetchone()[0]
        con.execute(
            """INSERT INTO pages(id,owner_user_id,parent_id,title,icon,cover,settings_json,position,
               archived,archived_at,created_at,updated_at) VALUES(?,?,?,?,?,'','{}',?,0,NULL,?,?)""",
            (page_id, owner, parent_id or None, normalized_title, icon, position, now, now),
        )
        ns._replace_blocks_in_connection(con, owner, page_id, prepared, now=now)
        ns._record_page_version(con, owner, page_id, now=now)
        if parent_id:
            parent_blocks = ns._page_blocks_in_connection(con, parent_id)
            parent_blocks.append({"type": "child_page", "content": normalized_title,
                                  "props": {"page_id": page_id, "title": normalized_title, "icon": icon}})
            ns._replace_blocks_in_connection(con, owner, parent_id, parent_blocks, now=now)
            ns._bump_page_revisions(con, {parent_id}, now=now)
            ns._record_page_version(con, owner, parent_id, now=now)
        result = ns._page_payload_in_connection(con, actor, owner, role, page_id)
        result["capabilities"]["local_page_draft_create"] = True
        result["revision"] = int(result["page"]["revision"])
        con.execute(
            """INSERT INTO page_save_operations(requester_user_id,operation_id,page_id,owner_user_id,
               request_hash,result_revision,result_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
            (actor, receipt_id, page_id, owner, request_hash, result["revision"], ns._json_dump(result), now),
        )
        con.commit()
        return result

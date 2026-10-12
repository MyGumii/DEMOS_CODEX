"""Workspace-scoped, reusable custom emoji with exact image and access checks."""
from __future__ import annotations

import hashlib
import re
import unicodedata

from code_assist_v1 import notion_workspace_store as nws
from code_assist_v1 import notion_workspace_custom_emoji_migration as migration
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout_migration
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default_migration
from code_assist_v1.notion_workspace_images import MAX_BYTES, validate_image


CONTRACT = "code_assist.notion.workspace_custom_emoji.v1"
MAX_EMOJIS_PER_WORKSPACE = 250
ID_RE = re.compile(r"we_[a-f0-9]{40}\Z")


def _name(value):
    if not isinstance(value, str):
        raise ValueError("emoji name is required")
    name = unicodedata.normalize("NFC", value.strip())
    if not 1 <= len(name) <= 64 or any(
        unicodedata.category(char)[0] == "C" or char in "/\\:<>" for char in name
    ):
        raise ValueError("emoji name must be 1–64 characters without controls or path separators")
    key = name.casefold()
    if len(key) > 64:
        raise ValueError("emoji name is too long after normalization")
    return name, key


def _installed(con):
    marker = con.execute(
        "SELECT version,name,checksum,actual_schema_sha256 FROM workspace_schema_migrations "
        "ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return marker is not None and tuple(marker) in {
        (item.TARGET_VERSION, item.TARGET_NAME, item.TARGET_CHECKSUM,
         item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration, item_layout_migration, template_default_migration)
    }


def supported(store):
    con = store._read_connection()
    try:
        return _installed(con)
    finally:
        con.close()


def create_custom_emoji(store, actor_subject_id, workspace_id, *, name, raw,
                        mime_type, base_version, operation_id):
    actor = nws._subject(actor_subject_id, field="actor_subject_id")
    workspace = nws._identifier(workspace_id, prefix="ws", field="workspace_id")
    version = nws._expected_version(base_version)
    clean_name, name_key = _name(name)
    if not supported(store):
        raise nws.WorkspaceSubstrateUnavailable("custom emoji registry is not installed")
    store.iam._require_active(actor)
    digest = validate_image(raw, mime_type)
    emoji_id = "we_" + hashlib.sha256(
        (workspace + "\0" + name_key + "\0" + digest).encode()
    ).hexdigest()[:40]
    request = {"workspace_id": workspace, "name": clean_name, "name_key": name_key,
               "mime_type": mime_type, "content_sha256": digest,
               "size_bytes": len(raw), "base_version": version}

    def authorize(con):
        store.iam._require_active(actor)
        if not _installed(con):
            raise nws.WorkspaceSubstrateUnavailable("custom emoji registry is not installed")
        return store._require_member(con, workspace, actor)

    def handler(con):
        authorize(con)
        store._assert_version(con, workspace, version)
        if con.execute(
            "SELECT 1 FROM workspace_custom_emojis WHERE workspace_id=? AND name_key=?",
            (workspace, name_key),
        ).fetchone():
            raise ValueError("emoji name is already in use")
        count = con.execute(
            "SELECT COUNT(*) FROM workspace_custom_emojis WHERE workspace_id=?", (workspace,)
        ).fetchone()[0]
        if count >= MAX_EMOJIS_PER_WORKSPACE:
            raise ValueError("workspace custom emoji limit reached")
        now = nws._now()
        con.execute(
            "INSERT INTO workspace_custom_emojis(id,workspace_id,name,name_key,mime_type,size_bytes,"
            "content_sha256,image_bytes,uploader_subject_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (emoji_id, workspace, clean_name, name_key, mime_type, len(raw), digest, raw, actor, now),
        )
        current = store._bump_workspace(con, workspace, security_change=False)
        item = {"id": emoji_id, "name": clean_name, "icon": "ne:" + emoji_id,
                "mime_type": mime_type, "created_at": now}
        return nws._MutationEffect(
            result={"contract": CONTRACT, "emoji": item, "workspace_version": int(current["row_version"])},
            workspace_id=workspace, target_type="workspace_custom_emoji", target_id=emoji_id,
            event_payload={"workspace_id": workspace, "emoji_id": emoji_id, "name": clean_name,
                           "content_sha256": digest},
            authorization=store._authorization("workspace.member", workspace),
        )

    return store._mutate(
        actor_subject_id=actor, operation_id=operation_id,
        action="workspace.custom_emoji.created", request=request,
        handler=handler, replay_authorizer=lambda con, _: authorize(con),
    )


def list_custom_emojis(store, actor_subject_id, workspace_id):
    actor = nws._subject(actor_subject_id, field="actor_subject_id")
    workspace = nws._identifier(workspace_id, prefix="ws", field="workspace_id")
    store.iam._require_active(actor)
    con = store._read_connection()
    try:
        con.execute("BEGIN")
        if not _installed(con):
            raise FileNotFoundError("workspace custom emoji not found")
        store.iam._require_workspace_visibility(con, actor, workspace)
        rows = con.execute(
            "SELECT id,name,mime_type,created_at FROM workspace_custom_emojis "
            "WHERE workspace_id=? ORDER BY name_key,id LIMIT ?",
            (workspace, MAX_EMOJIS_PER_WORKSPACE),
        ).fetchall()
        return [{"id": row["id"], "name": row["name"], "icon": "ne:" + row["id"],
                 "mime_type": row["mime_type"], "created_at": row["created_at"]} for row in rows]
    finally:
        con.close()


def read_custom_emoji(store, actor_subject_id, emoji_id):
    actor = nws._subject(actor_subject_id, field="actor_subject_id")
    if not isinstance(emoji_id, str) or ID_RE.fullmatch(emoji_id) is None:
        raise FileNotFoundError("workspace custom emoji not found")
    store.iam._require_active(actor)
    con = store._read_connection()
    try:
        con.execute("BEGIN")
        if not _installed(con):
            raise FileNotFoundError("workspace custom emoji not found")
        row = con.execute(
            "SELECT workspace_id,mime_type,size_bytes,content_sha256,image_bytes "
            "FROM workspace_custom_emojis WHERE id=?", (emoji_id,),
        ).fetchone()
        if row is None:
            raise FileNotFoundError("workspace custom emoji not found")
        store.iam._require_workspace_visibility(con, actor, row["workspace_id"])
        raw = bytes(row["image_bytes"])
        if len(raw) != row["size_bytes"] or "sha256:" + hashlib.sha256(raw).hexdigest() != row["content_sha256"]:
            raise ValueError("workspace custom emoji image is corrupt")
        return {"raw": raw, "mime_type": row["mime_type"], "content_sha256": row["content_sha256"]}
    finally:
        con.close()

"""Appearance belongs to a personal workspace, never to a hidden page or file."""
from __future__ import annotations

import hashlib
import json
import re
import time
from uuid import uuid4

from code_assist_v1 import auth, notion_icons, notion_store as ns
from code_assist_v1 import notion_personal_workspace_schema as schema
from code_assist_v1.notion_workspace_images import MAX_BYTES, validate_image

CONTRACT = "code_assist.notion.personal_workspace_appearance.v1"


class AppearanceConflict(Exception):
    pass


def _active(user_id):
    user = next((item for item in auth.list_public_users() if item["id"] == user_id and not item.get("disabled")), None)
    if not user:
        raise FileNotFoundError("workspace not found")
    return user


def _visible(con, actor, owner):
    _active(actor); _active(owner)
    if actor == owner:
        return
    row = con.execute("SELECT 1 FROM page_shares s JOIN pages p ON p.id=s.page_id "
                      "WHERE s.shared_user_id=? AND s.revoked_at IS NULL AND p.archived=0 "
                      "AND s.role IN ('view','comment','edit') LIMIT 1", (actor,)).fetchone()
    if not row:
        raise FileNotFoundError("workspace not found")


def _metadata(owner, icon="", version=0, *, actor, supported=False):
    user = _active(owner)
    return {"contract": CONTRACT, "workspace": {"id": owner, "owner_user_id": owner,
            "name": (user.get("display_name") or owner) + "의 Notion", "icon": icon, "version": version,
            "access": {"role": "owner" if actor == owner else "guest"}},
            "capabilities": {"workspace_appearance": supported, "workspace_image": supported}}


def read(actor_user_id, owner_user_id):
    actor, owner = ns._uid(actor_user_id), ns._uid(owner_user_id)
    _active(actor); _active(owner)
    if not ns._database_exists(owner):
        if actor != owner:
            raise FileNotFoundError("workspace not found")
        return _metadata(owner, actor=actor)
    with ns._connection(owner) as con:
        con.execute("BEGIN")
        _visible(con, actor, owner)
        if not schema.ready(con):
            return _metadata(owner, actor=actor)
        row = con.execute("SELECT * FROM personal_workspace_appearance WHERE owner_user_id=?", (owner,)).fetchone()
        if row is None:
            raise ns.AtomicSaveUnavailable("personal workspace ownership mismatch")
        return _metadata(owner, row["icon"], row["version"], actor=actor, supported=True)


def list_visible(actor_user_id):
    actor = ns._uid(actor_user_id)
    _active(actor)
    owners = {actor, *(str(page["owner_user_id"]) for page in ns.list_pages(actor))}
    items = []
    for owner in sorted(owners):
        try:
            items.append(read(actor, owner))
        except FileNotFoundError:
            continue  # A revoked share must disappear during a concurrent refresh.
    return {"items": items}


def _validate_icon(icon):
    if not isinstance(icon, str) or len(icon) > 64:
        raise ValueError("icon must be a string of at most 64 characters")
    value = icon.strip()
    if value.startswith(("nf:", "nw:", "np:")):
        raise ValueError("image references are assigned by the upload service")
    if value.startswith("ni:"):
        catalog = notion_icons._catalog()
        if value not in {f"ni:{v['id']}:{c['id']}" for v in catalog["vectors"] for c in catalog["colors"]}:
            raise ValueError("unknown icon or color")
    return value


def update(actor_user_id, owner_user_id, *, base_version, operation_id, icon=None, raw=None, mime_type=None):
    actor, owner = ns._uid(actor_user_id), ns._uid(owner_user_id)
    _active(actor); _active(owner)
    if actor != owner:
        raise PermissionError("only the personal workspace owner can change its icon")
    if isinstance(base_version, bool) or not isinstance(base_version, int) or base_version < 1:
        raise ValueError("positive base_version required")
    if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128 or not operation_id.strip():
        raise ValueError("operation_id required")
    image_id = None
    request = {"owner": owner, "base_version": base_version}
    if raw is not None:
        if icon is not None:
            raise ValueError("choose icon or image")
        digest = validate_image(raw, mime_type)
        image_id = "pi_" + hashlib.sha256((owner + ":" + digest).encode()).hexdigest()[:40]
        value = f"np:{owner}:{image_id}"
        request.update(image_sha256=digest, mime_type=mime_type)
    else:
        value = _validate_icon(icon)
        request["icon"] = value
    request_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    with ns._write_connection(owner) as con:
        try:
            con.execute("BEGIN IMMEDIATE")
            _active(actor); _active(owner)
            if not schema.ready(con):
                raise ns.AtomicSaveUnavailable("personal workspace appearance migration required")
            previous = con.execute("SELECT * FROM personal_workspace_operations WHERE actor_user_id=? AND operation_id=?", (actor, operation_id)).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise AppearanceConflict("operation_id was used for a different change")
                return json.loads(previous["result_json"])
            current = con.execute("SELECT version FROM personal_workspace_appearance WHERE owner_user_id=?", (owner,)).fetchone()
            if not current or current[0] != base_version:
                raise AppearanceConflict("workspace icon changed; reload before applying again")
            now = int(time.time())
            con.execute("UPDATE personal_workspace_appearance SET icon=?,version=version+1,updated_at=?,image_id=?,mime_type=?,image_bytes=? WHERE owner_user_id=?",
                        (value, now, image_id, mime_type if image_id else None, raw if image_id else None, owner))
            result = _metadata(owner, value, base_version + 1, actor=actor, supported=True)
            encoded = json.dumps(result, ensure_ascii=False)
            event_id = "pwa_" + uuid4().hex
            con.execute("INSERT INTO personal_workspace_operations VALUES(?,?,?,?,?)", (actor, operation_id, request_hash, encoded, now))
            con.execute("INSERT INTO personal_workspace_audit VALUES(?,?,?,?,?,?)", (event_id, actor, owner, "workspace.appearance.updated", json.dumps({"icon": value, "version": base_version + 1}), now))
            con.execute("INSERT INTO personal_workspace_outbox(event_id) VALUES(?)", (event_id,))
            _active(actor); _active(owner)
            con.commit()
            return result
        except Exception:
            con.rollback()
            raise


def read_image(actor_user_id, owner_user_id, image_id):
    actor, owner = ns._uid(actor_user_id), ns._uid(owner_user_id)
    if not re.fullmatch(r"pi_[a-f0-9]{40}", image_id):
        raise FileNotFoundError("image not found")
    with ns._connection(owner) as con:
        con.execute("BEGIN")
        _visible(con, actor, owner)
        if not schema.ready(con):
            raise FileNotFoundError("image not found")
        row = con.execute("SELECT image_bytes,mime_type FROM personal_workspace_appearance WHERE owner_user_id=? AND image_id=? AND icon=?",
                          (owner, image_id, f"np:{owner}:{image_id}")).fetchone()
        if not row:
            raise FileNotFoundError("image not found")
        _active(actor); _active(owner)
        return {"raw": bytes(row[0]), "mime_type": row[1]}

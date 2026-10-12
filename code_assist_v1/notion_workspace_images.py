"""Atomic, workspace-owned images; page file permissions are never reused."""
from __future__ import annotations

import hashlib
import io
import re
import warnings

from code_assist_v1 import notion_workspace_store as nws
from code_assist_v1.notion_collaboration.workspace_image_authority import marker_current

CONTRACT = "code_assist.notion.workspace_image.v1"
MAX_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 16_777_216


def validate_image(raw, mime_type):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("icon image must be at most 2 MiB")
    expected = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}.get(mime_type)
    if expected is None:
        raise ValueError("PNG, JPEG or WebP image required")
    from PIL import Image, UnidentifiedImageError
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != expected or image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("a still image of at most 16 megapixels is required")
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
    except (OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("invalid icon image") from exc
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def supported(store):
    con = store._read_connection()
    try:
        return marker_current(con)
    finally:
        con.close()


def _active(store, actor):
    store.iam._require_active(actor)


def update_workspace_image(store, actor_subject_id, workspace_id, *, raw, mime_type, base_version, operation_id):
    actor = nws._subject(actor_subject_id, field="actor_subject_id")
    workspace = nws._identifier(workspace_id, prefix="ws", field="workspace_id")
    version = nws._expected_version(base_version)
    if not supported(store):
        raise nws.WorkspaceSubstrateUnavailable("workspace images are not installed")
    _active(store, actor)
    # Decode and hash bounded content before opening the mutation transaction.
    digest = validate_image(raw, mime_type)
    image_id = "wi_" + hashlib.sha256((workspace + ":" + digest).encode()).hexdigest()[:40]
    icon = "nw:" + image_id
    request = {"workspace_id": workspace, "mime_type": mime_type, "content_sha256": digest,
               "size_bytes": len(raw), "base_version": version}

    def authorize(con):
        _active(store, actor)
        if not marker_current(con):
            raise nws.WorkspaceSubstrateUnavailable("workspace images are not installed")
        return store._require_workspace_admin(con, workspace, actor)

    def handler(con):
        membership = authorize(con)
        store._assert_version(con, workspace, version)
        con.execute("INSERT INTO workspace_icon_images(workspace_id,image_id,mime_type,size_bytes,content_sha256,"
                    "image_bytes,uploader_subject_id,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(workspace_id) DO UPDATE SET image_id=excluded.image_id,mime_type=excluded.mime_type,"
                    "size_bytes=excluded.size_bytes,content_sha256=excluded.content_sha256,image_bytes=excluded.image_bytes,"
                    "uploader_subject_id=excluded.uploader_subject_id,updated_at=excluded.updated_at",
                    (workspace, image_id, mime_type, len(raw), digest, raw, actor, nws._now()))
        con.execute("UPDATE workspaces SET icon=? WHERE id=?", (icon, workspace))
        row = store._bump_workspace(con, workspace, security_change=False)
        result = store._workspace_public(row)
        result["access"] = {"role": str(membership["role"]), "epoch": int(membership["membership_epoch"])}
        return nws._MutationEffect(result={"contract": CONTRACT, "workspace": result}, workspace_id=workspace,
            target_type="workspace", target_id=workspace,
            event_payload={"workspace_id": workspace, "icon": icon, "content_sha256": digest},
            authorization=store._authorization("workspace.admin", workspace))

    return store._mutate(actor_subject_id=actor, operation_id=operation_id, action="workspace.image.updated",
        request=request, handler=handler, replay_authorizer=lambda con, _: authorize(con))


def read_workspace_image(store, actor_subject_id, image_id):
    actor = nws._subject(actor_subject_id, field="actor_subject_id")
    if not re.fullmatch(r"wi_[a-f0-9]{40}", image_id) or not supported(store):
        raise FileNotFoundError("workspace image not found")
    _active(store, actor)
    con = store._read_connection()
    try:
        con.execute("BEGIN")
        if not marker_current(con):
            raise FileNotFoundError("workspace image not found")
        row = con.execute("SELECT i.* FROM workspace_icon_images i JOIN workspaces w ON w.id=i.workspace_id "
                          "WHERE i.image_id=? AND w.icon=?", (image_id, "nw:" + image_id)).fetchone()
        if row is None:
            raise FileNotFoundError("workspace image not found")
        # Reuse the current workspace visibility rule, including legitimate guests.
        store.iam._require_workspace_visibility(con, actor, row["workspace_id"])
        _active(store, actor)
        return {"raw": bytes(row["image_bytes"]), "mime_type": row["mime_type"], "content_sha256": row["content_sha256"]}
    finally:
        con.close()

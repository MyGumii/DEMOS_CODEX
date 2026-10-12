"""UI-WS-IMAGE: owned bytes, exact retries, revocation and atomic metadata."""
import io
import sqlite3
from pathlib import Path

from flask import Flask
from PIL import Image
import pytest

from code_assist_v1 import notion_workspace_icon_image_migration as migration
from code_assist_v1 import notion_workspace_images as images, notion_workspace_store as nws
from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1.notion_workspace_appearance import update_workspace_icon
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.tests.notion_collaboration.test_search_authority import _authority, _grant, IDENTITIES
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply


def png(color="blue"):
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture()
def environment(tmp_path):
    old, root, catalog = _authority(tmp_path)
    ws = old.read_document("owner", "page-e1")["workspace_id"]
    _apply(migration, catalog, root, "before-2411.db")
    store = CollaborativeWorkspaceStore(catalog, user_data_root=root,
        identity_directory=InMemoryIdentityDirectory((*IDENTITIES, "admin")))
    return store, ws


def save(store, ws, *, raw=None, operation="upload", version=None, actor="owner"):
    return images.update_workspace_image(store, actor, ws, raw=png() if raw is None else raw,
        mime_type="image/png", base_version=version or store.get_workspace("owner", ws)["version"], operation_id=operation)


def dump(store):
    with sqlite3.connect(f"{Path(store.db_path).as_uri()}?mode=ro", uri=True) as con:
        return tuple(con.iterdump())


def test_ui_workspace_image_migration_and_existing_authorities(environment):
    store, ws = environment
    caps = store.capabilities()
    for capability in ("live_collaboration", "collaborative_editing", "database_features", "collaborative_files",
                       "collaborative_history", "collaborative_search", "workspace_image"):
        assert caps[capability] is True
    assert caps["schema_version"] == migration.TARGET_VERSION
    before = dump(store)
    assert store.iam.read_document("owner", "page-e1")["workspace_id"] == ws
    assert images.supported(store)
    assert dump(store) == before
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    con = transport._connect(query_only=True)
    con.close()


def test_ui_workspace_image_replay_replace_remove_and_body_preservation(environment):
    store, ws = environment
    page = store.iam.read_document("owner", "page-e1")
    original = store.get_workspace("owner", ws)
    first = save(store, ws, version=original["version"])
    ref = first["workspace"]["icon"]
    assert ref.startswith("nw:wi_") and len(ref) <= 64
    assert first["workspace"]["security_epoch"] == original["security_epoch"]
    assert images.read_workspace_image(store, "owner", ref[3:])["raw"] == png()
    assert store.iam.read_document("owner", "page-e1") == page
    second = save(store, ws, raw=png("red"), operation="replace")
    before = dump(store)
    assert save(store, ws, version=original["version"]) == first
    assert dump(store) == before and store.get_workspace("owner", ws)["icon"] == second["workspace"]["icon"]
    with pytest.raises(FileNotFoundError):
        images.read_workspace_image(store, "owner", ref[3:])
    with pytest.raises(nws.WorkspaceOperationConflict):
        save(store, ws, raw=png("red"), version=original["version"])
    with pytest.raises(nws.WorkspaceRevisionConflict):
        save(store, ws, operation="stale", version=original["version"])
    update_workspace_icon(store, "owner", ws, icon="", base_version=second["workspace"]["version"], operation_id="remove")
    with pytest.raises(FileNotFoundError):
        images.read_workspace_image(store, "owner", second["workspace"]["icon"][3:])


def test_ui_workspace_image_admin_revocation_and_cross_workspace(environment):
    store, ws = environment
    store.set_member("owner", ws, "admin", role="membership_admin",
        base_version=store.get_workspace("owner", ws)["version"], operation_id="add-admin")
    version = store.get_workspace("owner", ws)["version"]
    result = save(store, ws, actor="admin", version=version)
    ref = result["workspace"]["icon"]
    store.revoke_member("owner", ws, "admin", base_version=store.get_workspace("owner", ws)["version"], operation_id="revoke-admin")
    with pytest.raises(FileNotFoundError):
        save(store, ws, actor="admin", version=version)
    with pytest.raises((FileNotFoundError, PermissionError)):
        images.read_workspace_image(store, "admin", ref[3:])
    with pytest.raises((FileNotFoundError, PermissionError)):
        images.read_workspace_image(store, "outsider", ref[3:])
    _grant(store.iam, "page-e1", "outsider", "ui-image-guest-read")
    assert images.read_workspace_image(store, "outsider", ref[3:])["raw"] == png()
    with pytest.raises((FileNotFoundError, PermissionError)):
        save(store, ws, actor="outsider", operation="guest-write-denied")
    page = store.iam.read_document("owner", "page-e1")
    store.iam.revoke_page_grant("owner", "page-e1", "subject", "outsider",
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"], operation_id="ui-image-guest-revoke")
    with pytest.raises((FileNotFoundError, PermissionError)):
        images.read_workspace_image(store, "outsider", ref[3:])
    with pytest.raises((FileNotFoundError, PermissionError)):
        save(store, ws, actor="content-editor", operation="no-admin")
    with pytest.raises(ValueError):
        update_workspace_icon(store, "owner", ws, icon=ref,
            base_version=store.get_workspace("owner", ws)["version"], operation_id="copy-reference")


def test_ui_workspace_image_rollback_after_bytes_before_receipt(environment, monkeypatch):
    store, ws = environment
    save(store, ws)
    before = dump(store)
    def fail(*args, **kwargs):
        raise RuntimeError("before receipt")
    monkeypatch.setattr(store, "_bump_workspace", fail)
    with pytest.raises(RuntimeError, match="before receipt"):
        save(store, ws, raw=png("red"), operation="failed")
    assert dump(store) == before


def test_ui_workspace_image_http_capabilities_and_visibility(environment, monkeypatch):
    store, ws = environment
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "current_user_id", lambda: "owner")
    app = Flask(__name__); app.config["TESTING"] = True
    routes.register_notion_workspace_routes(app)
    client = app.test_client()
    assert client.get(f"/api/notion/workspaces/{ws}").json["capabilities"]["workspace_image"]
    headers = {"X-Notion-Base-Version": str(store.get_workspace("owner", ws)["version"]), "X-Notion-Operation-Id": "http"}
    url = f"/api/notion/workspaces/{ws}/icon-image"
    first = client.post(url, data=png(), content_type="image/png", headers=headers)
    assert first.status_code == 200, first.json
    assert client.post(url, data=png(), content_type="image/png", headers=headers).json == first.json
    image_url = "/api/notion/workspace-images/" + first.json["workspace"]["icon"][3:]
    response = client.get(image_url)
    assert response.data == png() and response.mimetype == "image/png"
    assert "no-store" in response.headers["Cache-Control"] and response.headers["X-Content-Type-Options"] == "nosniff"
    assert client.post(url, data=png(), content_type="image/png").status_code == 400
    monkeypatch.setattr(routes, "current_user_id", lambda: "outsider")
    assert client.get(image_url).status_code == 404


def test_ui_workspace_image_rejects_corrupt_oversized_and_wrong_mime():
    for raw, mime in [(b"\x89PNG\r\n\x1a\n", "image/png"), (png(), "image/jpeg"),
                      (b"<svg/>", "image/svg+xml"), (b"x" * (images.MAX_BYTES + 1), "image/png")]:
        with pytest.raises(ValueError):
            images.validate_image(raw, mime)

"""A reusable emoji registry must not replace the workspace avatar image."""

import base64
import io
import sqlite3

import pytest
from flask import Flask

from code_assist_v1 import notion_workspace_custom_emoji_migration as migration
from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement
from code_assist_v1 import notion_workspace_custom_emoji as emoji
from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1.notion_collaboration import database_row_page_placement
from code_assist_v1.notion_collaboration import page_copy
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_appearance import update_page_appearance
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import request as copy_request
from code_assist_v1.tests.notion_collaboration.test_workspace_images import png
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment
from code_assist_v1.tests.notion_collaboration.test_database_row_icon_batch import (
    icon_batch_environment, _request as row_icon_request, environment, appearance_environment,
    body_environment, team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment, image_environment,
)


@pytest.mark.integration
@pytest.mark.regression
def test_custom_emoji_registry_is_forward_only_and_workspace_scoped(icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    _apply(placement, store.db_path, store.user_data_root, "before-row-placement.db")
    plan = migration.build_plan(store.db_path, store.user_data_root)
    assert plan["state"] == "migration_required"
    _apply(migration, store.db_path, store.user_data_root, "before-custom-emoji.db")
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"

    con = sqlite3.connect(store.db_path)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        original_icon = con.execute("SELECT icon FROM workspaces WHERE id=?", (workspace_id,)).fetchone()[0]
        raw = b"test-image"
        digest = "sha256:" + "a" * 64
        con.execute(
            "INSERT INTO workspace_custom_emojis(id,workspace_id,name,name_key,mime_type,size_bytes,"
            "content_sha256,image_bytes,uploader_subject_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("we_" + "b" * 40, workspace_id, "Team", "team", "image/png", len(raw), digest, raw, "owner", 1),
        )
        assert con.execute("SELECT icon FROM workspaces WHERE id=?", (workspace_id,)).fetchone()[0] == original_icon
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO workspace_custom_emojis(id,workspace_id,name,name_key,mime_type,size_bytes,"
                "content_sha256,image_bytes,uploader_subject_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("we_" + "c" * 40, workspace_id, "TEAM", "team", "image/png", len(raw), digest, raw, "owner", 2),
            )
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO workspace_custom_emojis(id,workspace_id,name,name_key,mime_type,size_bytes,"
                "content_sha256,image_bytes,uploader_subject_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("we_" + "d" * 40, "ws_missing", "Other", "other", "image/png", len(raw), digest, raw, "owner", 3),
            )
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        con.execute("DELETE FROM workspace_custom_emojis WHERE id=?", ("we_" + "b" * 40,))
        con.commit()
    finally:
        con.close()
    reopened = CollaborativeWorkspaceStore(
        store.db_path, user_data_root=store.user_data_root,
        identity_directory=store.identity_directory,
    )
    assert reopened.capabilities()["schema_version"] == migration.TARGET_VERSION
    assert reopened.capabilities()["workspace_image"] is True
    assert reopened.capabilities()["database_row_content"] is True
    assert reopened.iam.read_document("owner", "page-e1")["document_id"] == "page-e1"
    with reopened.iam._reader() as con:
        assert database_row_page_placement.installed(con)

    version = reopened.get_workspace("owner", workspace_id)["version"]
    created = emoji.create_custom_emoji(
        reopened, "owner", workspace_id, name="Team", raw=png(),
        mime_type="image/png", base_version=version, operation_id="custom-emoji-create",
    )
    item = created["emoji"]
    assert item["icon"] == "ne:" + item["id"]
    assert emoji.list_custom_emojis(reopened, "owner", workspace_id) == [item]
    assert emoji.read_custom_emoji(reopened, "owner", item["id"])["raw"] == png()
    assert emoji.create_custom_emoji(
        reopened, "owner", workspace_id, name="Team", raw=png(),
        mime_type="image/png", base_version=version, operation_id="custom-emoji-create",
    ) == created
    assert reopened.get_workspace("owner", workspace_id)["icon"] == original_icon
    page = reopened.iam.read_document("owner", "page-e1")
    appearance = dict(expected_document_sequence=page["document_sequence"],
                      expected_workspace_epoch=page["workspace_epoch"],
                      expected_document_epoch=page["document_epoch"])
    saved_page = update_page_appearance(
        reopened.iam, "owner", workspace_id, "page-e1",
        updates={"icon": item["icon"]}, operation_id="custom-emoji-page-icon", **appearance,
    )
    assert saved_page["page"]["icon"] == item["icon"]
    exported = reopened.iam.export_html("owner", "page-e1")["html"]
    assert "data:image/png;base64," + base64.b64encode(png()).decode() in exported
    assert "/api/notion/custom-emoji/" not in exported
    copied = page_copy.copy_page(
        reopened.iam, "owner", workspace_id, "page-e1",
        operation_id="custom-emoji-page-copy", **copy_request(reopened),
    )
    copied_page_id = copied["page"]["id"]
    assert reopened.iam.read_document("owner", copied_page_id)["icon"] == item["icon"]
    assert "data:image/png;base64," + base64.b64encode(png()).decode() in (
        reopened.iam.export_html("owner", copied_page_id)["html"]
    )
    with pytest.raises(NotFoundOrDenied):
        update_page_appearance(
            reopened.iam, "owner", workspace_id, "page-e1",
            updates={"icon": "ne:we_" + "f" * 40},
            operation_id="custom-emoji-foreign-page-icon",
            **{**appearance, "expected_document_sequence": saved_page["document_sequence"]},
        )
    with pytest.raises(ValueError, match="already in use"):
        emoji.create_custom_emoji(
            reopened, "owner", workspace_id, name="TEAM", raw=png("red"),
            mime_type="image/png", base_version=reopened.get_workspace("owner", workspace_id)["version"],
            operation_id="custom-emoji-duplicate-name",
        )
    with pytest.raises((FileNotFoundError, PermissionError)):
        emoji.read_custom_emoji(reopened, "unknown-reader", item["id"])
    with pytest.raises((FileNotFoundError, PermissionError)):
        emoji.create_custom_emoji(
            reopened, "unknown-reader", workspace_id, name="Outsider", raw=png(),
            mime_type="image/png", base_version=created["workspace_version"],
            operation_id="custom-emoji-outsider",
        )

    monkeypatch.setattr(routes, "_store", lambda: reopened)
    monkeypatch.setattr(routes, "current_user_id", lambda: "owner")
    app = Flask(__name__)
    app.config["TESTING"] = True
    routes.register_notion_workspace_routes(app)
    client = app.test_client()
    catalog_url = f"/api/notion/workspaces/{workspace_id}/custom-emoji"
    assert client.get(f"/api/notion/workspaces/{workspace_id}").json["capabilities"]["custom_emoji"]
    assert [entry["id"] for entry in client.get(catalog_url).json["items"]] == [item["id"]]
    response = client.post(catalog_url, data={
        "name": "Team 2", "base_version": str(reopened.get_workspace("owner", workspace_id)["version"]),
        "operation_id": "custom-emoji-http", "image": (io.BytesIO(png("red")), "team.png"),
    }, content_type="multipart/form-data")
    assert response.status_code == 201, response.json
    http_item = response.json["emoji"]
    image_response = client.get("/api/notion/custom-emoji/" + http_item["id"])
    assert image_response.data == png("red")
    assert image_response.mimetype == "image/png"
    assert image_response.headers["X-Content-Type-Options"] == "nosniff"
    assert client.post(catalog_url, data={"name": "missing image"}).status_code == 400
    monkeypatch.setattr(routes, "current_user_id", lambda: "unknown-reader")
    assert client.get("/api/notion/custom-emoji/" + http_item["id"]).status_code in {403, 404}
    changed_rows = reopened.iam.execute_row_icon_batch(
        "owner", "page-e1", **row_icon_request(
            reopened, operation="custom-emoji-row-icons", icon=item["icon"]),
    )
    assert all(row["appearance"]["icon"] == item["icon"] for row in changed_rows["rows"])
    with pytest.raises(NotFoundOrDenied):
        reopened.iam.execute_row_icon_batch(
            "owner", "page-e1", **row_icon_request(
                reopened, operation="custom-emoji-missing-row-icons",
                icon="ne:we_" + "f" * 40),
        )

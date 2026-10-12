"""UI-R10: a directly shared database item body has its own write boundary."""
from __future__ import annotations

import hashlib

import pytest

from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default
from code_assist_v1 import notion_workspace_database_sharing_migration as migration
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope
from code_assist_v1.notion_collaboration import database_access
from code_assist_v1.notion_collaboration.database_updates import read_database_updates_scoped
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply, SUBJECTS
from code_assist_v1.tests.notion_collaboration.test_database_move_commit import _upload_png
from code_assist_v1.tests.notion_collaboration.test_database_row_icon_batch import (
    icon_batch_environment, appearance_environment, body_environment,
    team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment,
    image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import (
    activity_environment, environment,
)
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for
from code_assist_v1.routes_notion_files import register_notion_file_routes


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_database_only_body_http_replay_file_boundary_and_revoke(
        icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    unrelated_file, png = _upload_png(store, "database-http-body-unrelated")
    client, actor = client_for(monkeypatch, store)
    register_notion_file_routes(client.application)
    base = f"/api/notion/workspaces/{workspace_id}/databases/database-e1"
    endpoint = base + "/rows/row-e1/body"
    assert client.post(endpoint, json={}).status_code == 404
    for target, backup in (
        (placement, "before-http-body-placement.db"),
        (custom_emoji, "before-http-body-emoji.db"),
        (item_layout, "before-http-body-layout.db"),
        (template_default, "before-http-body-default.db"),
        (migration, "before-http-body-sharing.db"),
        (upload_scope, "before-http-body-upload.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    viewer = "database-http-body-editor"
    observer = "database-http-body-observer"
    store.iam.identity_directory.replace([*SUBJECTS, viewer, observer])
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role="view", expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-http-body-view-grant",
    )
    actor["id"] = viewer
    projection = client.get(base).json
    row = next(item for item in projection["database"]["rows"] if item["id"] == "row-e1")
    request = {
        "blocks": [{"id": "body-http-e1", "type": "paragraph",
                    "content": "Body shared by this database", "props": {}}],
        "expected_target_version": row["version"],
        "expected_document_sequence": projection["document_sequence"],
        "expected_workspace_epoch": projection["workspace_epoch"],
        "expected_document_epoch": projection["document_epoch"],
        "expected_database_epoch": projection["database_epoch"],
        "operation_id": "database-http-body-save",
    }
    assert client.post(endpoint, json=request).status_code == 404
    actor["id"] = "owner"
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role="edit_content", expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-http-body-edit-grant",
    )
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", observer,
        role="view", expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-http-body-observer-grant",
    )
    actor["id"] = viewer
    projection = client.get(base).json
    request.update(expected_document_sequence=projection["document_sequence"],
                   expected_workspace_epoch=projection["workspace_epoch"],
                   expected_document_epoch=projection["document_epoch"],
                   expected_database_epoch=projection["database_epoch"])
    with pytest.raises(InjectedFailure):
        database_access.change_row_body(
            store.iam, viewer, workspace_id, "database-e1", "row-e1",
            fault="after_domain", **request)
    assert client.get(base).json["document_sequence"] == projection["document_sequence"]
    saved = client.post(endpoint, json=request)
    assert saved.status_code == 201, saved.json
    assert saved.json["replayed"] is False
    assert saved.json["body"]["blocks"][0]["content"] == "Body shared by this database"
    assert "page-e1" not in saved.get_data(as_text=True)
    updates = read_database_updates_scoped(store.iam, viewer, workspace_id,
                                           "database-e1")
    assert any(item["command_kind"] == "database.row.content.set"
               and item.get("row_id") == "row-e1" for item in updates["items"])
    assert "Body shared by this database" not in str(updates)
    assert "page-e1" not in str(updates)
    assert client.post(endpoint, json=request).json["replayed"] is True
    assert client.post(endpoint, json={**request, "blocks": []}).status_code == 409
    latest = client.get(base).json
    assert next(item for item in latest["database"]["rows"]
                if item["id"] == "row-e1")["body"]["blocks"][0]["content"] == "Body shared by this database"
    forbidden = {**request,
        "blocks": [{"id": "body-http-image", "type": "image", "content": "",
                    "props": {"file_id": unrelated_file}}],
        "expected_document_sequence": latest["document_sequence"],
        "expected_target_version": saved.json["target_version"],
        "operation_id": "database-http-body-forbidden-file"}
    assert client.post(endpoint, json=forbidden).status_code == 404
    assert client.get(base).json["document_sequence"] == latest["document_sequence"]
    assert client.post(endpoint, json={
        **forbidden,
        "blocks": [{"id": "body-private-target", "type": "link_to_page",
                    "content": "Private page label", "props": {"page_id": "page-e1"}}],
        "operation_id": "database-http-body-forbidden-page-link",
    }).status_code == 404
    assert client.get(base).json["document_sequence"] == latest["document_sequence"]
    assert client.post(base.replace("database-e1", "database-other")
                       + "/rows/row-e1/body",
                       json={**forbidden, "operation_id": "database-http-body-other-db"}).status_code == 404
    upload_base = base + "/files"
    manifest = {
        "original_name": "body.png", "mime_type": "image/png",
        "total_size": len(png), "chunk_size": 65536,
        "expected_sha256": "sha256:" + hashlib.sha256(png).hexdigest(),
        "expected_document_sequence": latest["document_sequence"],
        "expected_workspace_epoch": latest["workspace_epoch"],
        "expected_document_epoch": latest["document_epoch"],
        "expected_database_epoch": latest["database_epoch"],
        "operation_id": "database-http-body-upload-begin",
    }
    begun = client.post(upload_base + "/uploads", json=manifest)
    assert begun.status_code == 201, begun.json
    session_url = upload_base + "/uploads/" + begun.json["session_id"]
    chunk = client.put(session_url + "/chunks/0", data=png,
                       content_type="application/octet-stream",
                       headers={"X-Notion-Operation-Id": "database-http-body-upload-chunk"})
    assert chunk.status_code == 200, chunk.json
    finalized = client.post(session_url + "/finalize",
                            json={"operation_id": "database-http-body-upload-finalize"})
    assert finalized.status_code == 200, finalized.json
    file_url = finalized.json["file"]["url"]
    actor["id"] = observer
    assert client.get(file_url).status_code == 404
    actor["id"] = viewer
    assert client.get(file_url).data == png
    after_upload = client.get(base).json
    attached = client.post(endpoint, json={
        **request, "blocks": [{"id": "body-http-attached", "type": "image",
                                "content": "", "props": {
                                    "file_id": finalized.json["file"]["id"]}}],
        "expected_target_version": saved.json["target_version"],
        "expected_document_sequence": after_upload["document_sequence"],
        "operation_id": "database-http-body-attach-file",
    })
    assert attached.status_code == 201, attached.json
    assert attached.json["body"]["blocks"][0]["props"]["file_id"] == finalized.json["file"]["id"]
    actor["id"] = observer
    assert client.get(file_url).data == png
    actor["id"] = "owner"
    owner_projection = client.get(base).json
    owner_row = next(item for item in owner_projection["database"]["rows"]
                     if item["id"] == "row-e1")
    owner_private_body = client.post(endpoint, json={
        "blocks": [
            {"id": "body-public", "type": "paragraph", "content": "Shared notes", "props": {}},
            {"id": "body-private-link", "type": "paragraph", "content": "Private page label",
             "props": {"rich_text_html": '<a href="/page/page-e1">Private page label</a>'}},
            {"id": "body-private-encoded-link", "type": "paragraph",
             "content": "Encoded private page label",
             "props": {"rich_text_html":
                       '<a href="%252Fpage%252Fpage-e1">Encoded private page label</a>'}},
        ],
        "expected_target_version": owner_row["version"],
        "expected_document_sequence": owner_projection["document_sequence"],
        "expected_workspace_epoch": owner_projection["workspace_epoch"],
        "expected_document_epoch": owner_projection["document_epoch"],
        "expected_database_epoch": owner_projection["database_epoch"],
        "operation_id": "database-http-body-owner-private-link",
    })
    assert owner_private_body.status_code == 201, owner_private_body.json
    actor["id"] = viewer
    restricted = client.get(base).json
    restricted_row = next(item for item in restricted["database"]["rows"]
                          if item["id"] == "row-e1")
    assert restricted_row["body_unavailable"] is True
    assert [block["content"] for block in restricted_row["body"]["blocks"]] == ["Shared notes"]
    assert "page-e1" not in str(restricted)
    assert "Private page label" not in str(restricted)
    assert "Encoded private page label" not in str(restricted)
    assert client.post(endpoint, json={
        "blocks": [{"id": "body-public", "type": "paragraph",
                    "content": "Overwritten notes", "props": {}}],
        "expected_target_version": restricted_row["version"],
        "expected_document_sequence": restricted["document_sequence"],
        "expected_workspace_epoch": restricted["workspace_epoch"],
        "expected_document_epoch": restricted["document_epoch"],
        "expected_database_epoch": restricted["database_epoch"],
        "operation_id": "database-http-body-hidden-link-overwrite",
    }).status_code == 404
    actor["id"] = "owner"
    owner_after = client.get(base).json
    assert "Private page label" in str(next(item for item in owner_after["database"]["rows"]
                                       if item["id"] == "row-e1")["body"])
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role=None, expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-http-body-revoke",
    )
    actor["id"] = viewer
    assert client.post(endpoint, json=request).status_code == 404
    assert client.get(base).status_code == 404
    actor["id"] = "owner"
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role="edit_content", expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-http-body-regrant",
    )
    actor["id"] = viewer
    assert client.get(base).status_code == 200
    assert client.post(endpoint, json=request).status_code == 404

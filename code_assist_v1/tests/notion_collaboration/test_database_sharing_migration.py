"""UI-R10: independent database-sharing storage starts without new access."""

from contextlib import closing
from io import BytesIO
import hashlib
import json
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default
from code_assist_v1 import notion_workspace_database_sharing_migration as migration
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope_migration
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply, _schema, _surface, SUBJECTS
from code_assist_v1.tests.notion_collaboration.test_database_move_commit import (
    _destination, _move, _upload_png,
)
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import copy as copy_owned_database
from code_assist_v1.notion_collaboration import database_access
from code_assist_v1.notion_collaboration import database_file_upload
from code_assist_v1.notion_collaboration import linked_databases
from code_assist_v1.notion_collaboration.database_search import search_database_titles_scoped
from code_assist_v1.notion_collaboration.database_updates import read_database_updates_scoped
from code_assist_v1.notion_collaboration.canonical_authority import (
    InjectedFailure, NotFoundOrDenied, OperationConflict,
)
from code_assist_v1.notion_collaboration.file_authority import UploadStateConflict
from code_assist_v1.notion_collaboration.iam_runtime import collaborative_page_payload
from code_assist_v1.notion_collaboration.database_move import DatabaseMoveShapeUnavailable
from code_assist_v1.tests.notion_collaboration.test_database_row_icon_batch import (
    icon_batch_environment, appearance_environment, body_environment,
    team_environment, file_environment, member_environment, native_environment,
    archive_environment, guest_environment, image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment, environment
from code_assist_v1.tests.notion_collaboration.test_linked_database_copy import setup_pair
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for
from code_assist_v1.routes_notion_files import register_notion_file_routes
from code_assist_v1.routes_notion_search import register_notion_search_routes


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_database_only_blank_row_creation_is_atomic_and_scoped(
        icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    client, http_actor = client_for(monkeypatch, store)
    base = f"/api/notion/workspaces/{workspace_id}/databases/database-e1"
    assert client.post(base + "/rows", json={}).status_code == 404
    _schema(store.iam, "database.property.create", "direct-row-title",
            {"name": "Name", "type": "title", "config": {}},
            "direct-row-title-property")
    for target, backup in (
        (placement, "before-direct-create-placement.db"),
        (custom_emoji, "before-direct-create-emoji.db"),
        (item_layout, "before-direct-create-layout.db"),
        (template_default, "before-direct-create-default.db"),
        (migration, "before-direct-create-sharing.db"),
        (upload_scope_migration, "before-direct-create-upload.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    editor, viewer = "direct-row-editor", "direct-row-viewer"
    store.iam.identity_directory.replace([*SUBJECTS, editor, viewer])
    for subject, role in ((editor, "edit_content"), (viewer, "view")):
        owner = database_access.resolve(store.iam, "owner", workspace_id,
                                        "database-e1")
        database_access.change_grant(
            store.iam, "owner", workspace_id, "database-e1", "subject",
            subject, role=role,
            expected_workspace_epoch=owner.workspace_epoch,
            expected_document_epoch=owner.document_epoch,
            expected_database_epoch=owner.database_epoch,
            operation_id=f"direct-create-grant-{subject}",
        )
    projection = database_access.read_projection(store.iam, editor,
                                                  workspace_id, "database-e1")
    request = dict(title="Scoped new item",
                   expected_document_sequence=projection["document_sequence"],
                   expected_workspace_epoch=projection["workspace_epoch"],
                   expected_document_epoch=projection["document_epoch"],
                   expected_database_epoch=projection["database_epoch"],
                   operation_id="direct-blank-row-create")
    with pytest.raises(NotFoundOrDenied):
        database_access.create_blank_row(store.iam, viewer, workspace_id,
                                         "database-e1", **request)
    with pytest.raises(InjectedFailure):
        database_access.create_blank_row(store.iam, editor, workspace_id,
                                         "database-e1", **request,
                                         fault="after_domain")
    assert not any(row["cells"].get("direct-row-title") == "Scoped new item"
                   for row in database_access.read_projection(
                       store.iam, editor, workspace_id,
                       "database-e1")["database"]["rows"])
    created = database_access.create_blank_row(store.iam, editor,
                                                workspace_id, "database-e1",
                                                **request)
    assert created["command_kind"] == "database.row.create"
    assert created["replayed"] is False
    assert "page-e1" not in str(created)
    assert database_access.create_blank_row(store.iam, editor, workspace_id,
                                            "database-e1", **request)["replayed"] is True
    assert any(row["id"] == created["row_id"]
               and row["cells"]["direct-row-title"] == "Scoped new item"
               for row in database_access.read_projection(
                   store.iam, viewer, workspace_id,
                   "database-e1")["database"]["rows"])
    with pytest.raises(OperationConflict):
        database_access.create_blank_row(store.iam, editor, workspace_id,
                                         "database-e1", **{**request,
                                              "title": "Different title"})
    with pytest.raises(NotFoundOrDenied):
        database_access.create_blank_row(store.iam, editor, workspace_id,
                                         "database-other", **{**request,
                                              "operation_id": "wrong-database-create"})
    with pytest.raises(NotFoundOrDenied):
        store.iam.read_document(editor, "page-e1")
    latest = database_access.read_projection(store.iam, editor,
                                             workspace_id, "database-e1")
    http_request = {**request, "title": "Created over HTTP",
                    "expected_document_sequence": latest["document_sequence"],
                    "operation_id": "direct-http-row-create"}
    http_actor["id"] = viewer
    assert client.post(base + "/rows", json=http_request).status_code == 404
    http_actor["id"] = editor
    posted = client.post(base + "/rows", json=http_request)
    assert posted.status_code == 201, posted.json
    assert "page-e1" not in posted.get_data(as_text=True)
    assert client.post(base + "/rows", json=http_request).json["replayed"] is True
    assert any(row["id"] == posted.json["row_id"]
               and row["cells"]["direct-row-title"] == "Created over HTTP"
               for row in client.get(base).json["database"]["rows"])
    owner = database_access.resolve(store.iam, "owner", workspace_id,
                                    "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject",
        editor, role=None,
        expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="direct-http-create-revoke",
    )
    assert client.post(base + "/rows", json=http_request).status_code == 404


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_direct_row_creation_uses_default_template_without_parent_access(
        icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    _schema(store.iam, "database.property.create", "direct-template-title",
            {"name": "Name", "type": "title", "config": {}},
            "direct-template-title-property")
    for target, backup in (
        (placement, "before-direct-template-placement.db"),
        (custom_emoji, "before-direct-template-emoji.db"),
        (item_layout, "before-direct-template-layout.db"),
        (template_default, "before-direct-template-default.db"),
        (migration, "before-direct-template-sharing.db"),
        (upload_scope_migration, "before-direct-template-upload.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    _schema(store.iam, "database.template.create", "direct-default-template",
            {"name": "Default", "body": [{"id": "template-note", "type": "paragraph",
                                           "content": "Starting notes", "props": {}}],
             "default_values": {"direct-template-title": "Preset title"}},
            "direct-template-create")
    _surface(store.iam, "database.template_default.set", "database-e1",
             {"template_id": "direct-default-template"},
             "direct-template-select", target_version=0, actor="schema-editor")
    editor = "direct-template-editor"
    store.iam.identity_directory.replace([*SUBJECTS, editor])
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", editor,
        role="edit_content", expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="direct-template-grant",
    )
    def create(title, operation, *, blank=False):
        projection = database_access.read_projection(
            store.iam, editor, workspace_id, "database-e1")
        return database_access.create_blank_row(
            store.iam, editor, workspace_id, "database-e1", title=title,
            expected_document_sequence=projection["document_sequence"],
            expected_workspace_epoch=projection["workspace_epoch"],
            expected_document_epoch=projection["document_epoch"],
            expected_database_epoch=projection["database_epoch"],
            operation_id=operation, use_default_template=not blank,
        )
    assert database_access.resolve(store.iam, editor, workspace_id, "database-e1").role == "edit_content"
    with pytest.raises(NotFoundOrDenied):
        store.iam.read_document(editor, "page-e1")
    first = create("", "direct-template-first")
    second = create("Explicit title", "direct-template-second")
    assert first["template_id"] == second["template_id"] == "direct-default-template"
    assert first["title_cell_version"] == 1
    assert second["title_cell_version"] == 2
    after_create = database_access.read_projection(
        store.iam, editor, workspace_id, "database-e1")
    rows = {row["id"]: row for row in after_create["database"]["rows"]}
    assert after_create["database"]["last_edit"]["actor_subject_id"] == editor
    assert after_create["database"]["last_edit"]["document_sequence"] == second["document_sequence"]
    assert rows[first["row_id"]]["cells"]["direct-template-title"] == "Preset title"
    assert rows[second["row_id"]]["cells"]["direct-template-title"] == "Explicit title"
    assert rows[first["row_id"]]["body"]["blocks"][0]["content"] == "Starting notes"
    assert rows[second["row_id"]]["body"]["blocks"][0]["id"] != rows[first["row_id"]]["body"]["blocks"][0]["id"]
    with closing(sqlite3.connect(store.db_path)) as con:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(
            "INSERT INTO workspace_collaboration_databases("
            "id,document_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("direct-template-target", "page-e1", "Private target", 1, 1),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("direct-template-target-row", "direct-template-target", 0, 1, 1),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("direct-template-relation", "database-e1", "Related", "relation", 99,
             json.dumps({"target_database_id": "direct-template-target"}), 1, 1),
        )
        con.commit()
    _schema(store.iam, "database.template.update", "direct-default-template",
            {"name": "Default", "body": [{"id": "template-note", "type": "paragraph",
                                           "content": "Starting notes", "props": {}}],
             "default_values": {"direct-template-title": "Preset title",
                                "direct-template-relation": ["direct-template-target-row"]}},
            "direct-template-relation-update", target_version=1)
    with closing(sqlite3.connect(store.db_path)) as con:
        before = con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_database_rows "
            "WHERE database_id='database-e1'",
        ).fetchone()[0]
    with pytest.raises(NotFoundOrDenied):
        create("", "direct-template-denied-target")
    denied_projection = database_access.read_projection(
        store.iam, editor, workspace_id, "database-e1")
    assert next(item for item in denied_projection["database"]["templates"]
                if item["id"] == "direct-default-template")["default_unavailable"] is True
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_database_rows "
            "WHERE database_id='database-e1'",
        ).fetchone()[0] == before
    client, http_actor = client_for(monkeypatch, store)
    http_actor["id"] = editor
    _schema(store.iam, "database.template.create", "direct-named-template",
            {"name": "Named", "body": [{"id": "named-note", "type": "paragraph",
                                           "content": "Named notes", "props": {}}],
             "default_values": {"direct-template-title": "Named title"}},
            "direct-named-template-create")
    projection = database_access.read_projection(
        store.iam, editor, workspace_id, "database-e1")
    named_request = {"title": "", "template_id": "direct-named-template",
                     "expected_document_sequence": projection["document_sequence"],
                     "expected_workspace_epoch": projection["workspace_epoch"],
                     "expected_document_epoch": projection["document_epoch"],
                     "expected_database_epoch": projection["database_epoch"],
                     "operation_id": "direct-named-template-http"}
    named_response = client.post(
        f"/api/notion/workspaces/{workspace_id}/databases/database-e1/rows",
        json=named_request)
    assert named_response.status_code == 201, named_response.json
    assert named_response.json["template_id"] == "direct-named-template"
    assert client.post(
        f"/api/notion/workspaces/{workspace_id}/databases/database-e1/rows",
        json=named_request).json["replayed"] is True
    named_row = next(row for row in database_access.read_projection(
        store.iam, editor, workspace_id, "database-e1")["database"]["rows"]
                     if row["id"] == named_response.json["row_id"])
    assert named_row["cells"]["direct-template-title"] == "Named title"
    assert named_row["body"]["blocks"][0]["content"] == "Named notes"
    assert client.post(
        f"/api/notion/workspaces/{workspace_id}/databases/database-e1/rows",
        json={**named_request, "template_id": "direct-default-template"}).status_code == 409
    projection = database_access.read_projection(
        store.iam, editor, workspace_id, "database-e1")
    blank_request = {"title": "", "use_default_template": False,
                     "expected_document_sequence": projection["document_sequence"],
                     "expected_workspace_epoch": projection["workspace_epoch"],
                     "expected_document_epoch": projection["document_epoch"],
                     "expected_database_epoch": projection["database_epoch"],
                     "operation_id": "direct-template-http-blank"}
    response = client.post(
        f"/api/notion/workspaces/{workspace_id}/databases/database-e1/rows",
        json=blank_request)
    assert response.status_code == 201, response.json
    assert "template_id" not in response.json
    assert "page-e1" not in response.get_data(as_text=True)
    assert client.post(
        f"/api/notion/workspaces/{workspace_id}/databases/database-e1/rows",
        json=blank_request).json["replayed"] is True
    blank = create("", "direct-template-explicit-blank", blank=True)
    blank_row = next(row for row in database_access.read_projection(
        store.iam, editor, workspace_id, "database-e1")["database"]["rows"]
                     if row["id"] == blank["row_id"])
    assert blank_row["cells"]["direct-template-title"] == ""
    assert "body" not in blank_row
    target = database_access.resolve(
        store.iam, "owner", workspace_id, "direct-template-target")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "direct-template-target", "subject", editor,
        role="view", expected_workspace_epoch=target.workspace_epoch,
        expected_document_epoch=target.document_epoch,
        expected_database_epoch=target.database_epoch,
        operation_id="direct-template-target-grant",
    )
    related = create("", "direct-template-visible-target")
    visible = database_access.read_projection(
        store.iam, editor, workspace_id, "database-e1")
    assert next(row for row in visible["database"]["rows"]
                if row["id"] == related["row_id"])["cells"]["direct-template-relation"] == [
                    "direct-template-target-row"]
    with closing(sqlite3.connect(store.db_path)) as con:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("direct-template-inverse", "direct-template-target", "Backlinks", "relation", 0,
             json.dumps({"target_database_id": "database-e1",
                         "reciprocal_property_id": "direct-template-relation"}), 1, 1),
        )
        con.execute(
            "UPDATE workspace_collaboration_database_properties "
            "SET config_json=?,row_version=row_version+1 WHERE id=?",
            (json.dumps({"target_database_id": "direct-template-target",
                         "reciprocal_property_id": "direct-template-inverse"}),
             "direct-template-relation"),
        )
        con.commit()
    paired_view = database_access.read_projection(
        store.iam, editor, workspace_id, "database-e1")
    assert next(item for item in paired_view["database"]["templates"]
                if item["id"] == "direct-default-template")["default_unavailable"] is True
    with pytest.raises(NotFoundOrDenied):
        create("", "direct-template-paired-view-denied")
    target = database_access.resolve(
        store.iam, "owner", workspace_id, "direct-template-target")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "direct-template-target", "subject", editor,
        role="edit_content", expected_workspace_epoch=target.workspace_epoch,
        expected_document_epoch=target.document_epoch,
        expected_database_epoch=target.database_epoch,
        operation_id="direct-template-target-edit-grant",
    )
    paired = create("", "direct-template-paired-edit")
    target_view = database_access.read_projection(
        store.iam, editor, workspace_id, "direct-template-target")
    assert next(row for row in target_view["database"]["rows"]
                if row["id"] == "direct-template-target-row")["cells"]["direct-template-inverse"] == [
                    paired["row_id"]]
    _schema(store.iam, "database.template.update", "direct-default-template",
            {"name": "Default", "body": [{"id": "private-template-page",
                                           "type": "paragraph",
                                           "content": "Private page label",
                                           "props": {"rich_text_html":
                                                     '<a href="/page/page-e1">Private page label</a>'}},
                                         {"id": "private-template-encoded-page",
                                          "type": "paragraph",
                                          "content": "Encoded private page label",
                                          "props": {"rich_text_html":
                                                    '<a href="%252Fpage%252Fpage-e1">Encoded private page label</a>'}}],
             "default_values": {"direct-template-title": "Preset title"}},
            "direct-template-private-update", target_version=2)
    restricted = database_access.read_projection(
        store.iam, editor, workspace_id, "database-e1")
    private_template = next(item for item in restricted["database"]["templates"]
                            if item["id"] == "direct-default-template")
    assert private_template["body"] == []
    assert private_template["body_unavailable"] is True
    assert "Private page label" not in json.dumps(restricted, ensure_ascii=False)
    assert "Encoded private page label" not in json.dumps(restricted, ensure_ascii=False)
    with closing(sqlite3.connect(store.db_path)) as con:
        before = con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_database_rows "
            "WHERE database_id='database-e1'",
        ).fetchone()[0]
    with pytest.raises(NotFoundOrDenied):
        create("", "direct-template-private-body-denied")
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_database_rows "
            "WHERE database_id='database-e1'",
        ).fetchone()[0] == before


@pytest.mark.parametrize("body,expected", [
    ([{"type": "child_page", "props": {"page_id": "private"}}], True),
    ([{"type": "paragraph", "content": "[Private](/page/private)"}], True),
    ([{"type": "paragraph", "props": {"rich_text_html":
      '<a href="https://app.example/page/private">Private</a>'}}], True),
    ([{"type": "paragraph", "props": {"rich_text_html":
      '<a href="&#47;page&#47;private">Private</a>'}}], True),
    ([{"type": "paragraph", "props": {"url": "%2Fpage%2Fprivate"}}], True),
    ([{"type": "paragraph", "props": {"url": "%252Fpage%252Fprivate"}}], True),
    ([{"type": "paragraph", "props": {"rich_text_html":
      '<a href="&amp;#47;page&amp;#47;private">Private</a>'}}], True),
    ([{"type": "paragraph", "props": {"url": "https://remote.example/article"}}], False),
    ([{"type": "paragraph", "props": {"url": "https%253A%252F%252Fremote.example%252Farticle"}}], False),
])
def test_ui_r10_direct_template_page_references_need_page_authority(body, expected):
    assert database_access._template_body_has_unscoped_reference(body) is expected


def test_ui_r10_direct_body_nested_page_link_encodings_fail_closed():
    from urllib.parse import quote

    link = "/page/private"
    for _ in range(18):
        link = quote(link, safe="")
        assert database_access._template_body_has_unscoped_reference(
            [{"type": "paragraph", "content": f"[Private]({link})"}]
        )


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_database_search_requires_independent_sharing_schema(
        icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    client, _actor = client_for(monkeypatch, store)
    register_notion_search_routes(client.application)
    response = client.get(
        f"/api/notion/workspaces/{workspace_id}/database-search",
        query_string={"q": "Database"},
    )
    assert response.status_code == 404


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_independent_database_cells_http_replay_and_revoke(
        icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    client, actor = client_for(monkeypatch, store)
    base = f"/api/notion/workspaces/{workspace_id}/databases/database-e1"
    assert client.post(base + "/cells", json={}).status_code == 404
    for target, backup in (
        (placement, "before-http-direct-cells-placement.db"),
        (custom_emoji, "before-http-direct-cells-emoji.db"),
        (item_layout, "before-http-direct-cells-layout.db"),
        (template_default, "before-http-direct-cells-default.db"),
        (migration, "before-http-direct-cells-sharing.db"),
        (upload_scope_migration, "before-http-direct-cells-upload.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    viewer = "database-http-cell-editor"
    store.iam.identity_directory.replace([*SUBJECTS, viewer])
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role="view", expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-http-cells-view-grant",
    )
    actor["id"] = viewer
    projection = client.get(base).json
    assert projection["role"] == "view"
    row = projection["database"]["rows"][0]
    title_property = "property-e1"
    assert title_property in {item["id"] for item in projection["database"]["properties"]}
    body = {
        "cells": [{"row_id": row["id"], "property_id": title_property,
                   "value": "Direct database edit",
                   "expected_target_version": row["cell_versions"].get(title_property, 0)}],
        "expected_document_sequence": projection["document_sequence"],
        "expected_workspace_epoch": projection["workspace_epoch"],
        "expected_document_epoch": projection["document_epoch"],
        "expected_database_epoch": projection["database_epoch"],
        "operation_id": "database-http-cells-edit",
    }
    assert client.post(base + "/cells", json=body).status_code == 404
    actor["id"] = "owner"
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role="edit_content", expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-http-cells-edit-grant",
    )
    actor["id"] = viewer
    projection = client.get(base).json
    body.update(expected_document_sequence=projection["document_sequence"],
                expected_workspace_epoch=projection["workspace_epoch"],
                expected_document_epoch=projection["document_epoch"],
                expected_database_epoch=projection["database_epoch"])
    saved = client.post(base + "/cells", json=body)
    assert saved.status_code == 201, saved.json
    assert saved.json["replayed"] is False
    assert saved.json["database_id"] == "database-e1"
    assert "page-e1" not in saved.get_data(as_text=True)
    assert client.post(base + "/cells", json=body).json["replayed"] is True
    assert client.post(base + "/cells", json={**body, "cells": [
        {**body["cells"][0], "value": "Different edit"}]}).status_code == 409
    assert client.post(base.replace("database-e1", "database-other") + "/cells",
                       json={**body, "operation_id": "database-http-cells-other-db"}).status_code == 404
    latest = client.get(base).json
    assert latest["database"]["rows"][0]["cells"][title_property] == "Direct database edit"
    actor["id"] = "owner"
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role=None, expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-http-cells-revoke",
    )
    actor["id"] = viewer
    assert client.post(base + "/cells", json=body).status_code == 404
    assert client.get(base).status_code == 404


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_database_only_upload_http_lifecycle_and_revoke(
        icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    _unrelated_file, png = _upload_png(store, "database-http-upload-bytes")
    client, actor = client_for(monkeypatch, store)
    register_notion_file_routes(client.application)
    base = f"/api/notion/workspaces/{workspace_id}/databases/database-e1/files"
    assert client.post(base + "/uploads", json={}).status_code == 404
    for target, backup in (
        (placement, "before-http-upload-placement.db"),
        (custom_emoji, "before-http-upload-emoji.db"),
        (item_layout, "before-http-upload-layout.db"),
        (template_default, "before-http-upload-default.db"),
        (migration, "before-http-upload-sharing.db"),
        (upload_scope_migration, "before-http-upload-scope.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    viewer = "database-http-uploader"
    store.iam.identity_directory.replace([*SUBJECTS, viewer])
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role="edit_content", expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-http-upload-grant",
    )
    actor["id"] = viewer
    source = database_access.read_projection(
        store.iam, viewer, workspace_id, "database-e1")
    manifest = {
        "original_name": "shared-upload.png", "mime_type": "image/png",
        "total_size": len(png), "chunk_size": 65536,
        "expected_sha256": "sha256:" + hashlib.sha256(png).hexdigest(),
        "expected_document_sequence": source["document_sequence"],
        "expected_workspace_epoch": source["workspace_epoch"],
        "expected_document_epoch": source["document_epoch"],
        "expected_database_epoch": source["database_epoch"],
        "operation_id": "database-http-upload-begin",
    }
    begun = client.post(base + "/uploads", json=manifest)
    assert begun.status_code == 201, begun.json
    assert begun.json["database_id"] == "database-e1"
    assert "page-e1" not in begun.get_data(as_text=True)
    session = begun.json["session_id"]
    assert client.post(base + "/uploads", json=manifest).json["session_id"] == session
    status_url = base + "/uploads/" + session
    status = client.get(status_url)
    assert status.status_code == 200
    assert status.json["database_id"] == "database-e1"
    assert "page-e1" not in status.get_data(as_text=True)
    chunk_url = status_url + "/chunks/0"
    chunk = client.put(chunk_url, data=png,
                       content_type="application/octet-stream",
                       headers={"X-Notion-Operation-Id": "database-http-upload-chunk"})
    assert chunk.status_code == 200, chunk.json
    assert client.put(chunk_url, data=png,
                      content_type="application/octet-stream",
                      headers={"X-Notion-Operation-Id": "database-http-upload-chunk"}).json["replayed"] is True
    finalized = client.post(status_url + "/finalize",
                            json={"operation_id": "database-http-upload-finalize"})
    assert finalized.status_code == 200, finalized.json
    file = finalized.json["file"]
    assert file["database_id"] == "database-e1"
    assert "page-e1" not in finalized.get_data(as_text=True)
    assert client.get(file["url"]).data == png
    latest = database_access.read_projection(
        store.iam, viewer, workspace_id, "database-e1")
    cancel_manifest = {
        **manifest, "expected_document_sequence": latest["document_sequence"],
        "operation_id": "database-http-upload-cancel-begin",
    }
    canceled_session = client.post(base + "/uploads", json=cancel_manifest)
    assert canceled_session.status_code == 201
    canceled_url = base + "/uploads/" + canceled_session.json["session_id"]
    canceled = client.post(canceled_url + "/cancel",
                           json={"operation_id": "database-http-upload-cancel"})
    assert canceled.status_code == 200 and canceled.json["state"] == "canceled"
    assert client.get(canceled_url).json["state"] in {"canceled", "cleaned"}
    assert client.post(canceled_url + "/cancel",
                       json={"operation_id": "database-http-upload-cancel"}).status_code == 200
    assert client.get(canceled_url.replace("database-e1", "database-other")).status_code == 404
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role=None, expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-http-upload-revoke",
    )
    assert client.get(status_url).status_code == 404
    assert client.get(file["url"]).status_code == 404
    assert client.post(status_url + "/finalize",
                       json={"operation_id": "database-http-upload-finalize"}).status_code == 404


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_database_only_file_download_checks_live_reference_and_revoke(
        icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    attached_file, attached_bytes = _upload_png(store, "database-http-attached")
    unrelated_file, _ = _upload_png(store, "database-http-unrelated")
    _schema(store.iam, "database.property.create", "database-http-attachment",
            {"name": "Attachment", "type": "files", "config": {}},
            "database-http-files-property")
    page = store.iam.read_document("owner", "page-e1")
    store.iam.execute_typed_command(
        "owner", "page-e1", command_kind="database.cell.set",
        target_id="row-e1:database-http-attachment",
        payload={"row_id": "row-e1", "property_id": "database-http-attachment",
                 "value": [{"file_id": attached_file}]},
        expected_target_version=0,
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="database-http-files-cell",
    )
    client, actor = client_for(monkeypatch, store)
    register_notion_file_routes(client.application)
    url = (f"/api/notion/workspaces/{workspace_id}/databases/database-e1"
           f"/files/{attached_file}")
    assert client.get(url).status_code == 404
    for target, backup in (
        (placement, "before-http-file-placement.db"),
        (custom_emoji, "before-http-file-emoji.db"),
        (item_layout, "before-http-file-layout.db"),
        (template_default, "before-http-file-default.db"),
        (migration, "before-http-file-sharing.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    viewer = "database-http-viewer"
    store.iam.identity_directory.replace([*SUBJECTS, viewer])
    source = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role="view", expected_workspace_epoch=source.workspace_epoch,
        expected_document_epoch=source.document_epoch,
        expected_database_epoch=source.database_epoch,
        operation_id="database-http-file-grant",
    )
    actor["id"] = viewer
    result = client.get(url)
    assert result.status_code == 200 and result.data == attached_bytes
    assert result.headers["Cache-Control"] == "private, no-store, max-age=0"
    assert result.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get(url + "?download=1").headers["Content-Disposition"].startswith("attachment;")
    assert client.get(url.replace(attached_file, unrelated_file)).status_code == 404
    assert client.get(f"/api/notion/collaboration/files/{attached_file}").status_code == 404
    source = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role=None, expected_workspace_epoch=source.workspace_epoch,
        expected_document_epoch=source.document_epoch,
        expected_database_epoch=source.database_epoch,
        operation_id="database-http-file-revoke",
    )
    assert client.get(url).status_code == 404


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_database_only_member_creates_linked_page_without_parent_id(icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    viewer = "database-only-page-creator"
    store.iam.identity_directory.replace([*SUBJECTS, viewer])
    version = lambda: store.get_workspace("owner", workspace_id)["version"]
    store.set_member("owner", workspace_id, viewer, role="member",
                     base_version=version(), operation_id="linked-page-member")
    teamspace_id = store.create_teamspace(
        "owner", workspace_id, name="Linked page team", visibility="closed",
        base_version=version(), operation_id="linked-page-team",
    )["teamspace"]["id"]
    store.set_teamspace_member(
        "owner", workspace_id, teamspace_id, viewer, role="member",
        base_version=version(), operation_id="linked-page-team-member",
    )
    for target, backup in (
        (placement, "before-id-only-page-placement.db"),
        (custom_emoji, "before-id-only-page-emoji.db"),
        (item_layout, "before-id-only-page-layout.db"),
        (template_default, "before-id-only-page-default.db"),
        (migration, "before-id-only-page-sharing.db"),
        (upload_scope_migration, "before-id-only-page-upload.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    source = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role="view", expected_workspace_epoch=source.workspace_epoch,
        expected_document_epoch=source.document_epoch,
        expected_database_epoch=source.database_epoch,
        operation_id="linked-page-source-grant",
    )
    with pytest.raises(NotFoundOrDenied):
        store.iam.read_document(viewer, "page-e1")
    with store.iam._reader() as con:
        workspace_version = con.execute(
            "SELECT row_version FROM workspaces WHERE id=?", (workspace_id,),
        ).fetchone()[0]
    client, actor = client_for(monkeypatch, store)
    actor["id"] = viewer
    url = f"/api/notion/workspaces/{workspace_id}/pages"
    body = {
        "base_version": workspace_version, "operation_id": "linked-page-database-only",
        "parent_id": "", "teamspace_id": teamspace_id,
        "title": "Shared table", "template": "linked_database",
        "source_database_id": "database-e1",
    }
    created = client.post(url, json=body)
    assert created.status_code == 201, created.json
    assert "page-e1" not in created.get_data(as_text=True)
    page_id = created.json["page"]["id"]
    block = store.iam.read_document(viewer, page_id)["blocks"][0]
    assert block["props"]["linked_database"] == {"database_id": "database-e1"}
    assert "page-e1" not in json.dumps(store.iam.read_document(viewer, page_id))
    linked = client.get(f"{url}/{page_id}/linked-databases/{block['id']}")
    assert linked.status_code == 200
    assert "page-e1" not in linked.get_data(as_text=True)
    assert client.post(url, json=body).json["replayed"] is True
    source = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role=None, expected_workspace_epoch=source.workspace_epoch,
        expected_document_epoch=source.document_epoch,
        expected_database_epoch=source.database_epoch,
        operation_id="linked-page-source-revoke",
    )
    assert client.post(url, json=body).status_code == 404
    assert client.get(f"{url}/{page_id}/linked-databases/{block['id']}").status_code == 404


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_linked_view_requires_instance_and_independent_source_grants(icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    instance_page, _title_property = setup_pair(store, workspace_id)
    for target, backup in (
        (placement, "before-linked-scope-placement.db"),
        (custom_emoji, "before-linked-scope-emoji.db"),
        (item_layout, "before-linked-scope-layout.db"),
        (template_default, "before-linked-scope-default.db"),
        (migration, "before-linked-scope-sharing.db"),
        (upload_scope_migration, "before-linked-scope-upload.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    store.iam.identity_directory.replace([*SUBJECTS, "linked-db-viewer",
                                          "database-search-outsider"])
    page = store.iam.read_document("owner", instance_page)
    store.iam.set_page_grant(
        "owner", instance_page, "subject", "linked-db-viewer", role="view",
        inherit_to_descendants=False,
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="linked-instance-view-grant",
    )
    with pytest.raises(NotFoundOrDenied):
        linked_databases.read_linked_database_scoped(
            store.iam, "linked-db-viewer", workspace_id, instance_page,
            "linked-block",
        )
    hidden_page = linked_databases.read_linked_instance_page_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
    )
    hidden_block = next(block for block in hidden_page["snapshot"]["blocks"]
                        if block["id"] == "linked-block")
    assert hidden_block["props"] == {"linked_database_unavailable": True}
    assert "page-e1" not in json.dumps(hidden_page, ensure_ascii=False)
    hidden_document = store.iam.read_document("linked-db-viewer", instance_page)
    assert hidden_document["parent_document_id"] is None
    assert next(block for block in hidden_document["blocks"]
                if block["id"] == "linked-block")["props"] == {
                    "linked_database_unavailable": True}
    assert "page-e1" not in json.dumps(hidden_document, ensure_ascii=False)
    hidden_payload = collaborative_page_payload(
        store.iam, "linked-db-viewer", instance_page,
    )
    assert hidden_payload["page"]["parent_id"] == ""
    assert next(block for block in hidden_payload["blocks"]
                if block["id"] == "linked-block")["props"] == {
                    "linked_database_unavailable": True}
    assert "page-e1" not in json.dumps(hidden_payload, ensure_ascii=False)
    assert "database-e1" not in {
        item["id"] for item in linked_databases.list_linkable_databases_scoped(
            store.iam, "linked-db-viewer", workspace_id,
        )["items"]}
    with pytest.raises(NotFoundOrDenied):
        linked_databases.read_linkable_database_source_scoped(
            store.iam, "linked-db-viewer", workspace_id, "database-e1",
        )
    page = store.iam.read_document("owner", instance_page)
    store.iam.set_page_grant(
        "owner", instance_page, "subject", "linked-db-viewer",
        role="edit_content", inherit_to_descendants=False,
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="linked-instance-content-grant",
    )
    instance_without_source = store.iam.read_document(
        "linked-db-viewer", instance_page,
    )
    with pytest.raises(NotFoundOrDenied):
        linked_databases.insert_linked_database_scoped(
            store.iam, "linked-db-viewer", workspace_id, instance_page,
            "database-e1", "linked-denied-block",
            expected_document_sequence=instance_without_source["document_sequence"],
            expected_workspace_epoch=instance_without_source["workspace_epoch"],
            expected_document_epoch=instance_without_source["document_epoch"],
            operation_id="linked-no-source-grant-insert-denied",
        )
    source = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject",
        "linked-db-viewer", role="edit_content",
        expected_workspace_epoch=source.workspace_epoch,
        expected_document_epoch=source.document_epoch,
        expected_database_epoch=source.database_epoch,
        operation_id="linked-source-content-grant",
    )
    writable = linked_databases.read_linked_database_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
        "linked-block",
    )
    visible_page = linked_databases.read_linked_instance_page_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
    )
    visible_block = next(block for block in visible_page["snapshot"]["blocks"]
                         if block["id"] == "linked-block")
    assert visible_block["props"]["linked_database"] == {
        "database_id": "database-e1"}
    assert "page-e1" not in json.dumps(visible_page, ensure_ascii=False)
    visible_document = store.iam.read_document("linked-db-viewer", instance_page)
    assert visible_document["parent_document_id"] is None
    assert next(block for block in visible_document["blocks"]
                if block["id"] == "linked-block")["props"]["linked_database"] == {
                    "database_id": "database-e1"}
    assert "page-e1" not in json.dumps(visible_document, ensure_ascii=False)
    visible_payload = collaborative_page_payload(
        store.iam, "linked-db-viewer", instance_page,
    )
    assert next(block for block in visible_payload["blocks"]
                if block["id"] == "linked-block")["props"]["linked_database"] == {
                    "database_id": "database-e1"}
    assert "page-e1" not in json.dumps(visible_payload, ensure_ascii=False)
    owner_payload = collaborative_page_payload(store.iam, "owner", instance_page)
    assert owner_payload["page"]["parent_id"] == "page-e1"
    assert next(block for block in owner_payload["blocks"]
                if block["id"] == "linked-block")["props"]["linked_database"] == {
                    "document_id": "page-e1", "database_id": "database-e1"}
    assert next(block for block in store.iam.read_document(
        "owner", instance_page,
    )["blocks"] if block["id"] == "linked-block")["props"]["linked_database"] == {
        "document_id": "page-e1", "database_id": "database-e1"}
    discovered = linked_databases.read_linkable_database_source_scoped(
        store.iam, "linked-db-viewer", workspace_id, "database-e1",
    )["source"]
    assert discovered["id"] == "database-e1"
    assert discovered["effective_role"] == "edit_content"
    assert "document_id" not in discovered and "page_title" not in discovered
    assert [item["id"] for item in linked_databases.list_linkable_databases_scoped(
        store.iam, "linked-db-viewer", workspace_id,
        query=discovered["title"],
    )["items"]] == ["database-e1"]
    assert "page-e1" not in json.dumps(discovered, ensure_ascii=False)
    owner_source = linked_databases.read_linkable_database_source_scoped(
        store.iam, "owner", workspace_id, "database-e1",
    )["source"]
    assert owner_source["document_id"] == "page-e1"
    parent_title = owner_source["page_title"]
    assert parent_title.casefold() not in discovered["title"].casefold()
    assert "database-e1" in {
        item["id"] for item in linked_databases.list_linkable_databases_scoped(
            store.iam, "owner", workspace_id, query=parent_title,
        )["items"]}
    assert "database-e1" not in {
        item["id"] for item in linked_databases.list_linkable_databases_scoped(
            store.iam, "linked-db-viewer", workspace_id, query=parent_title,
        )["items"]}
    client, actor = client_for(monkeypatch, store)
    register_notion_search_routes(client.application)
    actor["id"] = "linked-db-viewer"
    sources_url = f"/api/notion/workspaces/{workspace_id}/database-sources"
    database_search_url = f"/api/notion/workspaces/{workspace_id}/database-search"
    actor["id"] = "database-search-outsider"
    assert client.get(database_search_url,
                      query_string={"q": discovered["title"]}).status_code == 404
    actor["id"] = "linked-db-viewer"
    title_search = client.get(database_search_url,
                              query_string={"q": discovered["title"]})
    assert title_search.status_code == 200
    assert title_search.json["items"] == search_database_titles_scoped(
        store.iam, actor["id"], workspace_id, query=discovered["title"],
    )["items"]
    assert [(item["kind"], item["database_id"]) for item in
            title_search.json["items"]] == [("database", "database-e1")]
    direct_only = client.get(database_search_url,
                             query_string={"q": discovered["title"],
                                           "direct_only": "1"})
    assert direct_only.status_code == 200
    assert direct_only.json["direct_only"] is True
    assert [item["database_id"] for item in direct_only.json["items"]] == ["database-e1"]
    actor["id"] = "owner"
    assert client.get(database_search_url,
                      query_string={"q": discovered["title"],
                                    "direct_only": "1"}).json["items"] == []
    actor["id"] = "linked-db-viewer"
    assert "page-e1" not in title_search.get_data(as_text=True)
    assert "page_title" not in title_search.get_data(as_text=True)
    assert client.get(database_search_url,
                      query_string={"q": parent_title}).json["items"] == []
    search_response = client.get(sources_url, query_string={"q": discovered["title"]})
    assert search_response.status_code == 200
    assert [item["id"] for item in search_response.json["items"]] == ["database-e1"]
    assert "page-e1" not in search_response.get_data(as_text=True)
    assert "page_title" not in search_response.json["items"][0]
    assert client.get(sources_url, query_string={"q": parent_title}).json["items"] == []
    source_response = client.get(sources_url + "/database-e1")
    assert source_response.status_code == 200
    assert source_response.json["source"] == discovered
    assert "page-e1" not in source_response.get_data(as_text=True)
    direct_database_url = f"/api/notion/workspaces/{workspace_id}/databases/database-e1"
    direct_database = client.get(direct_database_url)
    assert direct_database.status_code == 200
    assert direct_database.json["database"]["id"] == "database-e1"
    assert "page-e1" not in direct_database.get_data(as_text=True)
    linked_url = (f"/api/notion/workspaces/{workspace_id}/pages/{instance_page}"
                  "/linked-databases/linked-block")
    linked_response = client.get(linked_url)
    assert linked_response.status_code == 200
    assert linked_response.json["source"] == {"database_id": "database-e1"}
    assert linked_response.json["database"]["id"] == "database-e1"
    assert "page-e1" not in linked_response.get_data(as_text=True)
    actor["id"] = "owner"
    with store.iam._reader() as con:
        workspace_version = con.execute(
            "SELECT row_version FROM workspaces WHERE id=?", (workspace_id,),
        ).fetchone()[0]
    new_page_body = {
        "base_version": workspace_version, "operation_id": "linked-id-only-page-create",
        "parent_id": "", "title": "Connected source", "template": "linked_database",
        "source_database_id": "database-e1",
    }
    created_page = client.post(
        f"/api/notion/workspaces/{workspace_id}/pages", json=new_page_body,
    )
    assert created_page.status_code == 201, created_page.json
    linked_page_id = created_page.json["page"]["id"]
    linked_page_block = store.iam.read_document("owner", linked_page_id)["blocks"][0]
    assert linked_page_block["props"]["linked_database"] == {
        "database_id": "database-e1"}
    assert linked_databases.read_linked_database_scoped(
        store.iam, "owner", workspace_id, linked_page_id, linked_page_block["id"],
    )["database"]["id"] == "database-e1"
    assert client.post(f"/api/notion/workspaces/{workspace_id}/pages",
                       json=new_page_body).json["replayed"] is True
    actor["id"] = "linked-db-viewer"
    row = writable["database"]["rows"][0]
    command = {
        "source": writable["source"],
        "instance_witness": writable["instance_witness"],
        "source_access": writable["access"],
        "cells": [{"row_id": row["id"], "property_id": _title_property,
                   "value": "Edited from linked view",
                   "expected_target_version": row["cell_versions"].get(
                       _title_property, 0)}],
        "operation_id": "linked-database-scoped-cell-write",
    }
    saved = linked_databases.execute_linked_cells_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
        "linked-block", **command,
    )
    assert saved["replayed"] is False
    assert "page-e1" not in json.dumps(saved, ensure_ascii=False)
    replayed = linked_databases.execute_linked_cells_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
        "linked-block", **command,
    )
    assert replayed["replayed"] is True
    linked_write_url = linked_url + "/cells"
    http_replay = client.post(linked_write_url, json=command)
    assert http_replay.status_code == 201
    assert http_replay.json["replayed"] is True
    assert "page-e1" not in http_replay.get_data(as_text=True)
    assert client.post(linked_write_url, json={**command, "source_witness": {}}).status_code == 400
    assert linked_databases.read_linked_database_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
        "linked-block",
    )["database"]["rows"][0]["cells"][_title_property] == "Edited from linked view"
    with pytest.raises(NotFoundOrDenied):
        store.iam.read_document("linked-db-viewer", "page-e1")
    instance_before = store.iam.read_document("linked-db-viewer", instance_page)
    insert = dict(
        expected_document_sequence=instance_before["document_sequence"],
        expected_workspace_epoch=instance_before["workspace_epoch"],
        expected_document_epoch=instance_before["document_epoch"],
        operation_id="linked-database-scoped-block-insert",
        after_block_id="linked-block",
    )
    insert_url = (f"/api/notion/workspaces/{workspace_id}/pages/{instance_page}"
                  "/linked-databases")
    insert_body = {
        "database_id": "database-e1", "block_id": "linked-only-block", **insert,
        "view_name": "Filtered table",
        "view_config": {"filters": [{"property": _title_property,
                                    "operator": "contains", "value": "Edited"}]},
        "layout": {"indent": 1},
    }
    insert_response = client.post(insert_url, json=insert_body)
    assert insert_response.status_code == 201, insert_response.json
    assert "page-e1" not in insert_response.get_data(as_text=True)
    inserted = insert_response.json
    assert inserted["target_id"] == "linked-only-block"
    assert linked_databases.insert_linked_database_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
        "database-e1", "linked-only-block", **insert,
        view_name="Filtered table", view_config=insert_body["view_config"],
        layout=insert_body["layout"],
    ) == inserted
    stored = next(block for block in store.iam.read_document(
        "linked-db-viewer", instance_page,
    )["blocks"] if block["id"] == "linked-only-block")
    assert stored["props"]["linked_database"] == {"database_id": "database-e1"}
    assert stored["props"]["views"][0]["config"] == insert_body["view_config"]
    assert stored["props"]["indent"] == 1
    staged_page = linked_databases.read_linked_instance_page_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
    )
    assert {block["id"] for block in staged_page["snapshot"]["blocks"]
            if block["props"].get("linked_database") == {
                "database_id": "database-e1"}} >= {
                    "linked-block", "linked-only-block"}
    assert "page-e1" not in json.dumps(staged_page, ensure_ascii=False)
    id_only_view = linked_databases.read_linked_database_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
        "linked-only-block",
    )
    id_only_response = client.get(linked_url.replace("linked-block", "linked-only-block"))
    assert id_only_response.status_code == 200
    assert id_only_response.json["source"] == {"database_id": "database-e1"}
    assert "page-e1" not in id_only_response.get_data(as_text=True)
    assert id_only_view["database"]["rows"][0]["cells"][_title_property] == "Edited from linked view"
    id_only_row = id_only_view["database"]["rows"][0]
    id_only_command = dict(
        source=id_only_view["source"],
        instance_witness=id_only_view["instance_witness"],
        source_access=id_only_view["access"],
        cells=[{"row_id": id_only_row["id"], "property_id": _title_property,
                "value": "Edited from ID-only view",
                "expected_target_version": id_only_row["cell_versions"].get(
                    _title_property, 0)}],
        operation_id="linked-id-only-cell-write",
    )
    id_only_write_url = linked_url.replace("linked-block", "linked-only-block") + "/cells"
    http_saved = client.post(id_only_write_url, json=id_only_command)
    assert http_saved.status_code == 201
    assert http_saved.json["replayed"] is False
    assert "page-e1" not in http_saved.get_data(as_text=True)
    assert linked_databases.execute_linked_cells_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
        "linked-only-block", **id_only_command,
    )["replayed"] is True
    assert linked_databases.read_linked_database_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
        "linked-only-block",
    )["database"]["rows"][0]["cells"][_title_property] == "Edited from ID-only view"
    updates = read_database_updates_scoped(
        store.iam, "linked-db-viewer", workspace_id, "database-e1",
    )
    assert updates["database_id"] == "database-e1"
    assert "document_id" not in updates
    assert any(item["event_type"] == "database.surface.committed"
               for item in updates["items"])
    assert any(item.get("row_id") == "row-e1"
               for item in updates["items"]
               if item["event_type"] == "database.surface.committed")
    assert any(item["command_kind"] == "database.cells.set"
               for item in updates["items"]
               if item["event_type"] == "database.surface.committed")
    assert "page-e1" not in json.dumps(updates, ensure_ascii=False)
    assert "Edited from ID-only view" not in json.dumps(updates, ensure_ascii=False)
    updates_url = f"/api/notion/workspaces/{workspace_id}/databases/database-e1/updates"
    response = client.get(updates_url)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.json["database_id"] == "database-e1"
    assert "document_id" not in response.json
    assert "page-e1" not in response.get_data(as_text=True)
    assert "Edited from ID-only view" not in response.get_data(as_text=True)
    page = store.iam.read_document("owner", instance_page)
    store.iam.set_page_grant(
        "owner", instance_page, "subject", "linked-db-viewer", role="view",
        inherit_to_descendants=False,
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="linked-instance-write-downgrade",
    )
    denied_write = linked_databases.read_linked_database_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
        "linked-block",
    )
    denied_row = denied_write["database"]["rows"][0]
    with pytest.raises(NotFoundOrDenied):
        linked_databases.execute_linked_cells_scoped(
            store.iam, "linked-db-viewer", workspace_id, instance_page,
            "linked-block", source=denied_write["source"],
            instance_witness=denied_write["instance_witness"],
            source_access=denied_write["access"],
            cells=[{"row_id": denied_row["id"], "property_id": _title_property,
                    "value": "Denied from read-only instance",
                    "expected_target_version": denied_row["cell_versions"].get(
                        _title_property, 0)}],
            operation_id="linked-database-view-only-write-denied",
        )
    assert client.post(linked_write_url, json=command).status_code == 404
    page = store.iam.read_document("owner", instance_page)
    store.iam.revoke_page_grant(
        "owner", instance_page, "subject", "linked-db-viewer",
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="linked-instance-content-revoke",
    )
    with pytest.raises(NotFoundOrDenied):
        linked_databases.execute_linked_cells_scoped(
            store.iam, "linked-db-viewer", workspace_id, instance_page,
            "linked-block", **command,
        )
    page = store.iam.read_document("owner", instance_page)
    store.iam.set_page_grant(
        "owner", instance_page, "subject", "linked-db-viewer", role="view",
        inherit_to_descendants=False,
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="linked-instance-after-write-view-grant",
    )
    with pytest.raises(NotFoundOrDenied):
        store.iam.read_document("linked-db-viewer", "page-e1")
    source = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject",
        "linked-db-viewer", role="view",
        expected_workspace_epoch=source.workspace_epoch,
        expected_document_epoch=source.document_epoch,
        expected_database_epoch=source.database_epoch,
        operation_id="linked-source-view-grant",
    )
    opened = linked_databases.read_linked_database_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
        "linked-block",
    )
    assert opened["source"] == {"database_id": "database-e1"}
    assert opened["database"]["effective_role"] == "view"
    assert opened["database"]["rows"]
    assert "page-e1" not in json.dumps(opened, ensure_ascii=False)
    page = store.iam.read_document("owner", instance_page)
    store.iam.revoke_page_grant(
        "owner", instance_page, "subject", "linked-db-viewer",
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="linked-instance-view-revoke",
    )
    assert database_access.resolve(
        store.iam, "linked-db-viewer", workspace_id, "database-e1",
    ).role == "view"
    with pytest.raises(NotFoundOrDenied):
        linked_databases.read_linked_database_scoped(
            store.iam, "linked-db-viewer", workspace_id, instance_page,
            "linked-block",
        )
    assert "database-e1" in {
        item["id"] for item in linked_databases.list_linkable_databases_scoped(
            store.iam, "linked-db-viewer", workspace_id,
        )["items"]}
    page = store.iam.read_document("owner", instance_page)
    store.iam.set_page_grant(
        "owner", instance_page, "subject", "linked-db-viewer", role="view",
        inherit_to_descendants=False,
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="linked-instance-view-restore",
    )
    assert linked_databases.read_linked_database_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
        "linked-block",
    )["database"]["id"] == "database-e1"
    source = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject",
        "linked-db-viewer", role=None,
        expected_workspace_epoch=source.workspace_epoch,
        expected_document_epoch=source.document_epoch,
        expected_database_epoch=source.database_epoch,
        operation_id="linked-source-view-revoke",
    )
    with pytest.raises(NotFoundOrDenied):
        linked_databases.read_linked_database_scoped(
            store.iam, "linked-db-viewer", workspace_id, instance_page,
            "linked-block",
        )
    assert "database-e1" not in {
        item["id"] for item in linked_databases.list_linkable_databases_scoped(
            store.iam, "linked-db-viewer", workspace_id,
        )["items"]}
    with pytest.raises(NotFoundOrDenied):
        linked_databases.read_linkable_database_source_scoped(
            store.iam, "linked-db-viewer", workspace_id, "database-e1",
        )
    assert client.get(sources_url + "/database-e1").status_code == 404
    assert client.get(sources_url, query_string={"q": discovered["title"]}).json["items"] == []
    assert client.get(database_search_url,
                      query_string={"q": discovered["title"]}).json["items"] == []
    assert client.get(direct_database_url).status_code == 404
    assert client.get(linked_url).status_code == 404
    assert client.post(linked_write_url, json=command).status_code == 404
    assert client.post(insert_url, json=insert_body).status_code == 404
    page = store.iam.read_document("owner", instance_page)
    store.iam.set_page_grant(
        "owner", instance_page, "subject", "linked-db-viewer",
        role="edit_content", inherit_to_descendants=False,
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="linked-instance-after-source-revoke-edit",
    )
    with pytest.raises(NotFoundOrDenied):
        linked_databases.read_linked_database_scoped(
            store.iam, "linked-db-viewer", workspace_id, instance_page,
            "linked-only-block",
        )
    revoked_page = linked_databases.read_linked_instance_page_scoped(
        store.iam, "linked-db-viewer", workspace_id, instance_page,
    )
    assert all(block["props"].get("linked_database_unavailable") is True
               for block in revoked_page["snapshot"]["blocks"]
               if block["id"] in {"linked-block", "linked-only-block"})
    assert "page-e1" not in json.dumps(revoked_page, ensure_ascii=False)
    revoked_document = store.iam.read_document("linked-db-viewer", instance_page)
    assert all(block["props"].get("linked_database_unavailable") is True
               for block in revoked_document["blocks"]
               if block["id"] in {"linked-block", "linked-only-block"})
    assert "page-e1" not in json.dumps(revoked_document, ensure_ascii=False)
    revoked_payload = collaborative_page_payload(
        store.iam, "linked-db-viewer", instance_page,
    )
    assert all(block["props"].get("linked_database_unavailable") is True
               for block in revoked_payload["blocks"]
               if block["id"] in {"linked-block", "linked-only-block"})
    assert "page-e1" not in json.dumps(revoked_payload, ensure_ascii=False)
    with pytest.raises(NotFoundOrDenied):
        linked_databases.insert_linked_database_scoped(
            store.iam, "linked-db-viewer", workspace_id, instance_page,
            "database-e1", "linked-only-block", **insert,
        )
    with pytest.raises(NotFoundOrDenied):
        read_database_updates_scoped(
            store.iam, "linked-db-viewer", workspace_id, "database-e1",
        )
    assert client.get(updates_url).status_code == 404


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_database_upload_scope_forward_schema_is_empty_and_immutable(icon_batch_environment):
    store, workspace_id = icon_batch_environment
    file_id, _bytes = _upload_png(store, "database-upload-scope-existing")
    for target, backup in (
        (placement, "before-upload-scope-placement.db"),
        (custom_emoji, "before-upload-scope-emoji.db"),
        (item_layout, "before-upload-scope-layout.db"),
        (template_default, "before-upload-scope-default.db"),
        (migration, "before-upload-scope-sharing.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    assert upload_scope_migration.build_plan(
        store.db_path, store.user_data_root,
    )["state"] == "migration_required"
    _apply(upload_scope_migration, store.db_path, store.user_data_root,
           "before-upload-scope.db")
    assert upload_scope_migration.build_plan(
        store.db_path, store.user_data_root,
    )["state"] == "already_current"
    with closing(sqlite3.connect(store.db_path)) as con:
        con.execute("PRAGMA foreign_keys=ON")
        assert con.execute("SELECT COUNT(*) FROM "
            "workspace_collaboration_database_upload_scopes").fetchone()[0] == 0
        session_id = con.execute(
            "SELECT upload_session_id FROM workspace_collaboration_files WHERE id=?",
            (file_id,),
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO workspace_collaboration_database_upload_scopes("
                "session_id,database_id,database_epoch,created_at) VALUES(?,?,?,?)",
                ("missing-session", "database-e1", 1, 1),
            )
        con.execute(
            "INSERT INTO workspace_collaboration_database_upload_scopes("
            "session_id,database_id,database_epoch,created_at) VALUES(?,?,?,?)",
            (session_id, "database-e1", 1, 1),
        )
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "UPDATE workspace_collaboration_database_upload_scopes "
                "SET database_epoch=2 WHERE session_id=?", (session_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "DELETE FROM workspace_collaboration_database_upload_scopes "
                "WHERE session_id=?", (session_id,),
            )
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert database_access.read_projection(
        store.iam, "owner", workspace_id, "database-e1",
    )["database"]["id"] == "database-e1"


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_database_only_upload_lifecycle_and_revoke(icon_batch_environment):
    store, workspace_id = icon_batch_environment
    unrelated_file, png = _upload_png(store, "database-upload-unrelated")
    _schema(store.iam, "database.property.create", "database-upload-property",
            {"name": "Attachment", "type": "files", "config": {}},
            "database-upload-property-create")
    for target, backup in (
        (placement, "before-db-upload-placement.db"),
        (custom_emoji, "before-db-upload-emoji.db"),
        (item_layout, "before-db-upload-layout.db"),
        (template_default, "before-db-upload-default.db"),
        (migration, "before-db-upload-sharing.db"),
        (upload_scope_migration, "before-db-upload-scope.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    page_file, _ = _upload_png(store, "page-upload-after-database-scope")
    assert store.iam.get_file("owner", page_file)["document_id"] == "page-e1"
    store.iam.identity_directory.replace(
        [*SUBJECTS, "database-viewer", "database-auditor",
         "database-other-editor"])
    owner_access = database_access.resolve(
        store.iam, "owner", workspace_id, "database-e1",
    )
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject",
        "database-viewer", role="edit_content",
        expected_workspace_epoch=owner_access.workspace_epoch,
        expected_document_epoch=owner_access.document_epoch,
        expected_database_epoch=owner_access.database_epoch,
        operation_id="database-upload-content-grant",
    )
    owner_access = database_access.resolve(
        store.iam, "owner", workspace_id, "database-e1",
    )
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject",
        "database-auditor", role="view",
        expected_workspace_epoch=owner_access.workspace_epoch,
        expected_document_epoch=owner_access.document_epoch,
        expected_database_epoch=owner_access.database_epoch,
        operation_id="database-upload-auditor-grant",
    )
    owner_access = database_access.resolve(
        store.iam, "owner", workspace_id, "database-e1",
    )
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject",
        "database-other-editor", role="edit_content",
        expected_workspace_epoch=owner_access.workspace_epoch,
        expected_document_epoch=owner_access.document_epoch,
        expected_database_epoch=owner_access.database_epoch,
        operation_id="database-upload-other-editor-grant",
    )
    projection = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    fences = {
        "expected_document_sequence": projection["document_sequence"],
        "expected_workspace_epoch": projection["workspace_epoch"],
        "expected_document_epoch": projection["document_epoch"],
        "expected_database_epoch": projection["database_epoch"],
    }
    manifest = dict(
        original_name="private-table.png", mime_type="image/png",
        total_size=len(png),
        expected_sha256="sha256:" + hashlib.sha256(png).hexdigest(),
        chunk_size=65536,
    )
    begun = database_file_upload.begin_upload(
        store.iam, "database-viewer", workspace_id, "database-e1",
        operation_id="database-only-upload-begin", **fences, **manifest,
    )
    assert "document_id" not in begun
    assert begun["database_id"] == "database-e1"
    assert database_file_upload.begin_upload(
        store.iam, "database-viewer", workspace_id, "database-e1",
        operation_id="database-only-upload-begin", **fences, **manifest,
    ) == begun
    with pytest.raises(NotFoundOrDenied):
        database_access.get_file(
            store.iam, "database-viewer", workspace_id, "database-e1",
            unrelated_file,
        )
    status = store.iam.upload_status("database-viewer", begun["session_id"])
    assert status["database_id"] == "database-e1"
    assert "document_id" not in status
    store.iam.stage_chunk(
        "database-viewer", begun["session_id"], chunk_index=0,
        stream=BytesIO(png), operation_id="database-only-upload-chunk",
    )
    completed = store.iam.finalize_upload(
        "database-viewer", begun["session_id"],
        operation_id="database-only-upload-finalize",
    )
    assert completed["database_id"] == "database-e1"
    assert "document_id" not in completed
    file_id = completed["file_id"]
    assert database_access.get_file(
        store.iam, "database-viewer", workspace_id, "database-e1", file_id,
    )["path"].read_bytes() == png
    with pytest.raises(NotFoundOrDenied):
        database_access.get_file(
            store.iam, "database-auditor", workspace_id, "database-e1", file_id,
        )
    with pytest.raises(NotFoundOrDenied):
        database_access.get_file(
            store.iam, "database-other-editor", workspace_id, "database-e1",
            file_id,
        )
    with pytest.raises(NotFoundOrDenied):
        database_access.change_cells(
            store.iam, "database-other-editor", workspace_id, "database-e1",
            cells=[{"row_id": "row-e1", "property_id": "database-upload-property",
                    "value": [{"file_id": file_id}],
                    "expected_target_version": 0}],
            expected_document_sequence=completed["document_sequence"],
            expected_workspace_epoch=fences["expected_workspace_epoch"],
            expected_document_epoch=fences["expected_document_epoch"],
            expected_database_epoch=fences["expected_database_epoch"],
            operation_id="database-upload-other-editor-attach-denied",
        )
    with closing(sqlite3.connect(store.db_path)) as con:
        con.execute(
            "INSERT INTO workspace_collaboration_databases("
            "id,document_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("database-upload-sibling", "page-e1", "Sibling", 1, 1),
        )
        con.commit()
    sibling_owner = database_access.resolve(
        store.iam, "owner", workspace_id, "database-upload-sibling",
    )
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-upload-sibling", "subject",
        "database-viewer", role="view",
        expected_workspace_epoch=sibling_owner.workspace_epoch,
        expected_document_epoch=sibling_owner.document_epoch,
        expected_database_epoch=sibling_owner.database_epoch,
        operation_id="database-upload-sibling-view",
    )
    with pytest.raises(NotFoundOrDenied):
        database_access.get_file(
            store.iam, "database-viewer", workspace_id,
            "database-upload-sibling", file_id,
        )
    changed = database_access.change_cells(
        store.iam, "database-viewer", workspace_id, "database-e1",
        cells=[{"row_id": "row-e1", "property_id": "database-upload-property",
                "value": [{"file_id": file_id}], "expected_target_version": 0}],
        expected_document_sequence=completed["document_sequence"],
        expected_workspace_epoch=fences["expected_workspace_epoch"],
        expected_document_epoch=fences["expected_document_epoch"],
        expected_database_epoch=fences["expected_database_epoch"],
        operation_id="database-only-upload-attach",
    )
    assert changed["cells"][0]["property_id"] == "database-upload-property"
    assert database_access.get_file(
        store.iam, "database-auditor", workspace_id, "database-e1", file_id,
    )["path"].read_bytes() == png
    assert database_access.get_file(
        store.iam, "database-other-editor", workspace_id, "database-e1",
        file_id,
    )["path"].read_bytes() == png
    with pytest.raises(UploadStateConflict):
        database_file_upload.delete_file(
            store.iam, "database-viewer", workspace_id, "database-e1", file_id,
            expected_document_sequence=changed["document_sequence"],
            expected_workspace_epoch=fences["expected_workspace_epoch"],
            expected_document_epoch=fences["expected_document_epoch"],
            expected_database_epoch=fences["expected_database_epoch"],
            operation_id="database-only-upload-attached-delete-denied",
        )
    disposable_session = database_file_upload.begin_upload(
        store.iam, "database-viewer", workspace_id, "database-e1",
        operation_id="database-only-upload-disposable",
        **{**fences, "expected_document_sequence": changed["document_sequence"]},
        **manifest,
    )
    store.iam.stage_chunk(
        "database-viewer", disposable_session["session_id"], chunk_index=0,
        stream=BytesIO(png), operation_id="database-only-upload-disposable-chunk",
    )
    disposable = store.iam.finalize_upload(
        "database-viewer", disposable_session["session_id"],
        operation_id="database-only-upload-disposable-finalize",
    )
    deleted = database_file_upload.delete_file(
        store.iam, "database-viewer", workspace_id, "database-e1",
        disposable["file_id"],
        expected_document_sequence=disposable["document_sequence"],
        expected_workspace_epoch=fences["expected_workspace_epoch"],
        expected_document_epoch=fences["expected_document_epoch"],
        expected_database_epoch=fences["expected_database_epoch"],
        operation_id="database-only-upload-disposable-delete",
    )
    assert deleted["state"] == "deleted" and "document_id" not in deleted
    assert database_file_upload.delete_file(
        store.iam, "database-viewer", workspace_id, "database-e1",
        disposable["file_id"],
        expected_document_sequence=disposable["document_sequence"],
        expected_workspace_epoch=fences["expected_workspace_epoch"],
        expected_document_epoch=fences["expected_document_epoch"],
        expected_database_epoch=fences["expected_database_epoch"],
        operation_id="database-only-upload-disposable-delete",
    ) == deleted
    with pytest.raises(NotFoundOrDenied):
        database_access.get_file(
            store.iam, "database-viewer", workspace_id, "database-e1",
            disposable["file_id"],
        )
    assert database_access.get_file(
        store.iam, "database-viewer", workspace_id, "database-e1", file_id,
    )["path"].read_bytes() == png
    canceled_session = database_file_upload.begin_upload(
        store.iam, "database-viewer", workspace_id, "database-e1",
        operation_id="database-only-upload-cancelable",
        **{**fences, "expected_document_sequence": deleted["document_sequence"]},
        **manifest,
    )
    canceled = store.iam.cancel_upload(
        "database-viewer", canceled_session["session_id"],
        operation_id="database-only-upload-cancel",
    )
    assert canceled["state"] == "canceled"
    assert "document_id" not in canceled
    next_session = database_file_upload.begin_upload(
        store.iam, "database-viewer", workspace_id, "database-e1",
        operation_id="database-only-upload-pending",
        **{**fences, "expected_document_sequence": deleted["document_sequence"]},
        **manifest,
    )
    store.iam.stage_chunk(
        "database-viewer", next_session["session_id"], chunk_index=0,
        stream=BytesIO(png), operation_id="database-only-upload-pending-chunk",
    )
    owner_access = database_access.resolve(
        store.iam, "owner", workspace_id, "database-e1",
    )
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject",
        "database-viewer", role=None,
        expected_workspace_epoch=owner_access.workspace_epoch,
        expected_document_epoch=owner_access.document_epoch,
        expected_database_epoch=owner_access.database_epoch,
        operation_id="database-only-upload-revoke",
    )
    with pytest.raises(NotFoundOrDenied):
        store.iam.upload_status("database-viewer", next_session["session_id"])
    with pytest.raises(NotFoundOrDenied):
        store.iam.finalize_upload(
            "database-viewer", next_session["session_id"],
            operation_id="database-only-upload-revoked-finalize",
        )
    with pytest.raises(NotFoundOrDenied):
        store.iam.stage_chunk(
            "database-viewer", next_session["session_id"], chunk_index=0,
            stream=BytesIO(png), operation_id="database-only-upload-revoked-chunk",
        )
    with pytest.raises(NotFoundOrDenied):
        database_access.get_file(
            store.iam, "database-viewer", workspace_id, "database-e1", file_id,
        )
    with pytest.raises(NotFoundOrDenied):
        store.iam.read_document("database-viewer", "page-e1")


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_database_upload_move_requires_resolved_files_and_preserves_attachment(icon_batch_environment):
    store, workspace_id = icon_batch_environment
    _destination(store, workspace_id)
    _unrelated, png = _upload_png(store, "database-move-unrelated")
    _schema(store.iam, "database.property.create", "database-move-attachment",
            {"name": "Move attachment", "type": "files", "config": {}},
            "database-move-attachment-create")
    for target, backup in (
        (placement, "before-db-upload-move-placement.db"),
        (custom_emoji, "before-db-upload-move-emoji.db"),
        (item_layout, "before-db-upload-move-layout.db"),
        (template_default, "before-db-upload-move-default.db"),
        (migration, "before-db-upload-move-sharing.db"),
        (upload_scope_migration, "before-db-upload-move-scope.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    store.iam.identity_directory.replace([*SUBJECTS, "database-viewer"])
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject",
        "database-viewer", role="edit_content",
        expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-move-uploader-grant",
    )
    projection = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    fences = {
        "expected_document_sequence": projection["document_sequence"],
        "expected_workspace_epoch": projection["workspace_epoch"],
        "expected_document_epoch": projection["document_epoch"],
        "expected_database_epoch": projection["database_epoch"],
    }
    manifest = dict(
        original_name="move.png", mime_type="image/png", total_size=len(png),
        expected_sha256="sha256:" + hashlib.sha256(png).hexdigest(),
        chunk_size=65536,
    )
    pending = database_file_upload.begin_upload(
        store.iam, "database-viewer", workspace_id, "database-e1",
        operation_id="database-move-pending-begin", **fences, **manifest,
    )
    with pytest.raises(DatabaseMoveShapeUnavailable, match="pending database uploads"):
        _move(store, workspace_id, operation_id="database-move-pending-denied")
    store.iam.cancel_upload("database-viewer", pending["session_id"],
                            operation_id="database-move-pending-cancel")
    unreferenced_session = database_file_upload.begin_upload(
        store.iam, "database-viewer", workspace_id, "database-e1",
        operation_id="database-move-unreferenced-begin", **fences, **manifest,
    )
    store.iam.stage_chunk(
        "database-viewer", unreferenced_session["session_id"], chunk_index=0,
        stream=BytesIO(png), operation_id="database-move-unreferenced-chunk",
    )
    uploaded = store.iam.finalize_upload(
        "database-viewer", unreferenced_session["session_id"],
        operation_id="database-move-unreferenced-finalize",
    )
    with pytest.raises(DatabaseMoveShapeUnavailable, match="unreferenced database uploads"):
        _move(store, workspace_id, operation_id="database-move-unreferenced-denied")
    database_access.change_cells(
        store.iam, "database-viewer", workspace_id, "database-e1",
        cells=[{"row_id": "row-e1", "property_id": "database-move-attachment",
                "value": [{"file_id": uploaded["file_id"]}],
                "expected_target_version": 0}],
        expected_document_sequence=uploaded["document_sequence"],
        expected_workspace_epoch=fences["expected_workspace_epoch"],
        expected_document_epoch=fences["expected_document_epoch"],
        expected_database_epoch=fences["expected_database_epoch"],
        operation_id="database-move-attachment-set",
    )
    moved = _move(store, workspace_id, operation_id="database-move-attached-file")
    assert moved["copied_file_count"] == 1
    restored = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    attached = next(row for row in restored["database"]["rows"]
                    if row["id"] == "row-e1")["cells"]["database-move-attachment"][0]["file_id"]
    assert attached != uploaded["file_id"]
    assert database_access.get_file(
        store.iam, "database-viewer", workspace_id, "database-e1", attached,
    )["path"].read_bytes() == png
    with pytest.raises(NotFoundOrDenied):
        database_access.get_file(
            store.iam, "database-viewer", workspace_id, "database-e1",
            uploaded["file_id"],
        )
    with pytest.raises(NotFoundOrDenied):
        store.iam.read_document("database-viewer", "page-db-destination")


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_database_copy_does_not_inherit_direct_grants(icon_batch_environment):
    store, workspace_id = icon_batch_environment
    for target, backup in (
        (placement, "before-db-share-copy-placement.db"),
        (custom_emoji, "before-db-share-copy-emoji.db"),
        (item_layout, "before-db-share-copy-layout.db"),
        (template_default, "before-db-share-copy-default.db"),
        (migration, "before-db-share-copy-sharing.db"),
        (upload_scope_migration, "before-db-share-copy-scope.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    store.iam.identity_directory.replace([*SUBJECTS, "database-viewer"])
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject",
        "database-viewer", role="view",
        expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-copy-source-view",
    )
    assert database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )["database"]["id"] == "database-e1"
    copied = copy_owned_database(
        store, workspace_id, operation="database-sharing-copy",
    )
    assert copied["database_id"] != "database-e1"
    with pytest.raises(NotFoundOrDenied):
        database_access.read_projection(
            store.iam, "database-viewer", workspace_id,
            copied["database_id"],
        )
    assert database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )["database"]["id"] == "database-e1"
    assert database_access.read_projection(
        store.iam, "owner", workspace_id, copied["database_id"],
    )["database"]["id"] == copied["database_id"]
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_database_access_grants "
            "WHERE database_id=?", (copied["database_id"],),
        ).fetchone()[0] == 0


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_database_sharing_forward_schema_preserves_existing_access(icon_batch_environment):
    store, workspace_id = icon_batch_environment
    for target, backup in (
        (placement, "before-sharing-placement.db"),
        (custom_emoji, "before-sharing-emoji.db"),
        (item_layout, "before-sharing-layout.db"),
        (template_default, "before-sharing-default.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    with closing(sqlite3.connect(store.db_path)) as con:
        before_databases = con.execute(
            "SELECT id,document_id,title,row_version FROM workspace_collaboration_databases ORDER BY id"
        ).fetchall()
        before_grants = con.execute(
            "SELECT * FROM workspace_collaboration_access_grants ORDER BY document_id,principal_type,principal_id"
        ).fetchall()

    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "migration_required"
    _apply(migration, store.db_path, store.user_data_root, "before-sharing.db")
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"
    with closing(sqlite3.connect(store.db_path)) as con:
        con.execute("PRAGMA foreign_keys=ON")
        assert con.execute(
            "SELECT id,document_id,title,row_version FROM workspace_collaboration_databases ORDER BY id"
        ).fetchall() == before_databases
        assert con.execute(
            "SELECT * FROM workspace_collaboration_access_grants ORDER BY document_id,principal_type,principal_id"
        ).fetchall() == before_grants
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_database_access_state").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_database_access_grants").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO workspace_collaboration_database_access_state(database_id,updated_at) "
                        "VALUES('missing-database',1)")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO workspace_collaboration_database_access_grants("
                        "database_id,principal_type,principal_id,role,granted_by_subject_id,"
                        "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                        ("database-e1", "subject", "content-editor", "owner", "owner", 1, 1))
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # A grant-only database viewer must not acquire the private parent page.
    store.iam.identity_directory.replace([*SUBJECTS, "database-viewer"])
    def resolve(actor):
        with store.iam._reader() as con:
            con.execute("BEGIN")
            parent_role = store.iam._resolution_on_connection(
                con, actor, workspace_id, "page-e1")[0]
            return parent_role, database_access.resolve_on_connection(
                store.iam, con, actor, workspace_id, "database-e1")

    with pytest.raises(NotFoundOrDenied):
        resolve("database-viewer")
    with closing(sqlite3.connect(store.db_path)) as con:
        con.execute("INSERT INTO workspace_collaboration_database_access_grants("
                    "database_id,principal_type,principal_id,role,granted_by_subject_id,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    ("database-e1", "subject", "database-viewer", "view", "owner", 1, 1))
        con.execute(
            "INSERT INTO workspace_collaboration_databases("
            "id,document_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("database-secret", "page-e1", "Private sibling table", 1, 1),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("relation-secret", "database-e1", "Related", "relation", 99,
             json.dumps({"target_database_id": "database-secret"}), 1, 1),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("secret-salary", "database-secret", "Salary", "number", 0,
             "{}", 1, 1),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("rollup-secret", "database-e1", "Hidden total", "rollup", 100,
             json.dumps({"relation_property_id": "relation-secret",
                         "target_property_id": "secret-salary",
                         "aggregation": "sum"}), 1, 1),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("row-secret-hidden", "database-secret", 2, 1, 1),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_cells("
            "row_id,property_id,value_json,updated_at) VALUES(?,?,?,?)",
            ("row-e2", "relation-secret", '["row-secret-hidden"]', 1),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_views("
            "id,database_id,name,view_type,position,config_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("view-secret-filter", "database-e1", "Filtered", "table", 99,
             json.dumps({"filters": [{"property": "relation-secret",
                                     "operator": "contains",
                                     "value": ["row-secret-hidden"]}],
                         "filter_group": {"logic": "and", "children": [
                             {"property": "relation-secret", "operator": "contains",
                              "value": ["row-secret-hidden"]}]},
                         "sorts": [{"property": "rollup-secret", "direction": "asc"}],
                         "group_property": "relation-secret",
                         "group_order": ["row-secret-hidden"],
                         "property_order": ["property-e1", "relation-secret"],
                         "property_visibility": {"property-e1": True,
                                                 "rollup-secret": True},
                         "show_vertical_lines": True}), 1, 1),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_templates("
            "id,database_id,name,body_json,default_values_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("template-secret", "database-e1", "Template", "[]",
             json.dumps({"relation-secret": ["row-secret-hidden"],
                         "rollup-secret": "secret-salary",
                         "property-e1": "Keep"}), 1, 1),
        )
        con.commit()
    parent_role, access = resolve("database-viewer")
    assert parent_role is None
    assert access.role == "view" and access.database_epoch == 1
    assert [source["scope"] for source in access.sources] == ["database"]
    projection = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    assert projection["database"]["id"] == "database-e1"
    assert projection["database"]["effective_role"] == "view"
    assert next(item for item in projection["database"]["properties"]
                if item["id"] == "relation-secret")["config"] == {"unavailable": True}
    assert next(item for item in projection["database"]["properties"]
                if item["id"] == "rollup-secret")["config"] == {"unavailable": True}
    assert next(item for item in projection["database"]["rows"]
                if item["id"] == "row-e2")["computed"]["rollup-secret"] is None
    assert next(item for item in projection["database"]["templates"]
                if item["id"] == "template-secret")["default_values"] == {
                    "property-e1": "Keep"}
    assert next(item for item in projection["database"]["views"]
                if item["id"] == "view-secret-filter")["config"] == {
                    "filters": [], "filter_group": {"logic": "and", "children": []},
                    "sorts": [], "property_order": ["property-e1"],
                    "property_visibility": {"property-e1": True},
                    "show_vertical_lines": True}
    encoded = json.dumps(projection, ensure_ascii=False)
    assert "page-e1" not in encoded
    assert "database-secret" not in encoded
    assert "secret-salary" not in encoded
    assert "row-secret-hidden" not in encoded
    assert "Private sibling table" not in encoded
    assert "row-e2" in {row["id"] for row in projection["database"]["rows"]}
    scoped_updates = read_database_updates_scoped(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    assert "document_id" not in scoped_updates
    assert "page-e1" not in json.dumps(scoped_updates, ensure_ascii=False)
    assert "database-secret" not in json.dumps(scoped_updates, ensure_ascii=False)
    with pytest.raises(NotFoundOrDenied):
        read_database_updates_scoped(
            store.iam, "database-viewer", workspace_id, "database-secret",
        )
    with closing(sqlite3.connect(store.db_path)) as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_row_trash_state("
            "row_id,database_id,archive_workspace_id,archive_document_id,"
            "archived_row_version,archived_by_subject_id,archived_at,"
            "archive_operation_id) VALUES(?,?,?,?,?,?,?,?)",
            ("row-e2", "database-e1", workspace_id, "page-e1", 1, "owner", 1,
             "database-projection-test-archive"),
        )
        con.commit()
    projection = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    assert "row-e2" not in {row["id"] for row in projection["database"]["rows"]}
    with pytest.raises(NotFoundOrDenied):
        database_access.read_projection(
            store.iam, "database-viewer", workspace_id, "database-secret",
        )
    sibling_owner = database_access.resolve(
        store.iam, "owner", workspace_id, "database-secret",
    )
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-secret", "subject",
        "database-viewer", role="view",
        expected_workspace_epoch=sibling_owner.workspace_epoch,
        expected_document_epoch=sibling_owner.document_epoch,
        expected_database_epoch=sibling_owner.database_epoch,
        operation_id="database-sibling-view",
    )
    projection = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    assert next(item for item in projection["database"]["properties"]
                if item["id"] == "relation-secret")["config"] == {
                    "target_database_id": "database-secret",
                }
    assert next(item for item in projection["database"]["properties"]
                if item["id"] == "rollup-secret")["config"]["target_property_id"] == "secret-salary"
    assert next(item for item in projection["database"]["templates"]
                if item["id"] == "template-secret")["default_values"]["relation-secret"] == ["row-secret-hidden"]
    assert "Private sibling table" not in json.dumps(projection, ensure_ascii=False)
    with closing(sqlite3.connect(store.db_path)) as con:
        for index, row_id in enumerate(("row-secret-one", "row-secret-two")):
            con.execute(
                "INSERT INTO workspace_collaboration_database_rows("
                "id,database_id,position,created_at,updated_at) VALUES(?,?,?,?,?)",
                (row_id, "database-secret", index, 1, 1),
            )
        con.commit()
    source_owner = database_access.resolve(
        store.iam, "owner", workspace_id, "database-e1",
    )
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject",
        "database-viewer", role="edit_content",
        expected_workspace_epoch=source_owner.workspace_epoch,
        expected_document_epoch=source_owner.document_epoch,
        expected_database_epoch=source_owner.database_epoch,
        operation_id="database-source-content-edit",
    )
    source = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    def relation_change(value, operation, expected_version):
        return database_access.change_cells(
            store.iam, "database-viewer", workspace_id, "database-e1",
            cells=[{"row_id": "row-e1", "property_id": "relation-secret",
                    "value": value, "expected_target_version": expected_version}],
            expected_document_sequence=source["document_sequence"],
            expected_workspace_epoch=source["workspace_epoch"],
            expected_document_epoch=source["document_epoch"],
            expected_database_epoch=source["database_epoch"],
            operation_id=operation,
        )
    relation_change(["row-secret-one"], "database-unpaired-relation", 0)
    source = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    assert next(item for item in source["database"]["rows"]
                if item["id"] == "row-e1")["cells"]["relation-secret"] == ["row-secret-one"]
    with closing(sqlite3.connect(store.db_path)) as con:
        con.execute(
            "UPDATE workspace_collaboration_database_properties SET config_json=? "
            "WHERE id='relation-secret'",
            (json.dumps({"target_database_id": "database-secret",
                         "reciprocal_property_id": "relation-inverse"}),),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("relation-inverse", "database-secret", "Inverse", "relation", 0,
             json.dumps({"target_database_id": "database-e1",
                         "reciprocal_property_id": "relation-secret"}), 1, 1),
        )
        con.commit()
    with pytest.raises(NotFoundOrDenied):
        relation_change(["row-secret-two"], "database-paired-view-denied", 1)
    source = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    assert next(item for item in source["database"]["rows"]
                if item["id"] == "row-e1")["cells"]["relation-secret"] == ["row-secret-one"]
    sibling_owner = database_access.resolve(
        store.iam, "owner", workspace_id, "database-secret",
    )
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-secret", "subject",
        "database-viewer", role="edit_content",
        expected_workspace_epoch=sibling_owner.workspace_epoch,
        expected_document_epoch=sibling_owner.document_epoch,
        expected_database_epoch=sibling_owner.database_epoch,
        operation_id="database-sibling-content-edit",
    )
    relation_change(["row-secret-two"], "database-paired-content-edit", 1)
    sibling = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-secret",
    )
    assert next(item for item in sibling["database"]["rows"]
                if item["id"] == "row-secret-two")["cells"]["relation-inverse"] == ["row-e1"]
    assert next(item for item in sibling["database"]["rows"]
                if item["id"] == "row-secret-one")["cells"]["relation-inverse"] == []
    sibling_owner = database_access.resolve(
        store.iam, "owner", workspace_id, "database-secret",
    )
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-secret", "subject",
        "database-viewer", role=None,
        expected_workspace_epoch=sibling_owner.workspace_epoch,
        expected_document_epoch=sibling_owner.document_epoch,
        expected_database_epoch=sibling_owner.database_epoch,
        operation_id="database-sibling-revoked-again",
    )
    redacted_again = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    assert next(item for item in redacted_again["database"]["rows"]
                if item["id"] == "row-e1")["cells"]["relation-secret"] == []
    assert next(item for item in redacted_again["database"]["rows"]
                if item["id"] == "row-e1")["computed"]["rollup-secret"] is None
    assert "row-secret-two" not in json.dumps(redacted_again, ensure_ascii=False)
    assert resolve("owner")[1].role == "full_access"
    with closing(sqlite3.connect(store.db_path)) as con:
        con.execute("UPDATE workspace_collaboration_documents SET archived=1,archived_at=1 "
                    "WHERE id='page-e1'")
        con.commit()
    with pytest.raises(NotFoundOrDenied):
        resolve("database-viewer")


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_database_grant_receipt_epoch_and_revoke_are_atomic(icon_batch_environment):
    store, workspace_id = icon_batch_environment
    attached_file, attached_bytes = _upload_png(store, "database-share-attached")
    unrelated_file, _ = _upload_png(store, "database-share-unrelated")
    _schema(store.iam, "database.property.create", "database-attachment",
            {"name": "Attachment", "type": "files", "config": {}},
            "database-share-files-property")
    page = store.iam.read_document("owner", "page-e1")
    store.iam.execute_typed_command(
        "owner", "page-e1", command_kind="database.cell.set",
        target_id="row-e1:database-attachment",
        payload={"row_id": "row-e1", "property_id": "database-attachment",
                 "value": [{"file_id": attached_file}]},
        expected_target_version=0,
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="database-share-files-cell",
    )
    for target, backup in (
        (placement, "before-grant-placement.db"),
        (custom_emoji, "before-grant-emoji.db"),
        (item_layout, "before-grant-layout.db"),
        (template_default, "before-grant-default.db"),
        (migration, "before-grant-sharing.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    store.iam.identity_directory.replace([*SUBJECTS, "database-viewer"])
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    options = dict(expected_workspace_epoch=owner.workspace_epoch,
                   expected_document_epoch=owner.document_epoch,
                   expected_database_epoch=owner.database_epoch)
    def change(actor="owner", role="view", operation="database-grant-set", **changes):
        return database_access.change_grant(
            store.iam, actor, workspace_id, "database-e1", "subject", "database-viewer",
            role=role, operation_id=operation, **{**options, **changes},
        )

    created = change()
    assert created["database_epoch"] == 2 and created["grant_epoch"] == 1
    assert created["replayed"] is False
    assert database_access.resolve(store.iam, "database-viewer", workspace_id,
                                   "database-e1").role == "view"
    assert change()["replayed"] is True
    with pytest.raises(OperationConflict):
        change(role="comment")
    with pytest.raises(NotFoundOrDenied):
        change(operation="database-grant-stale")
    with pytest.raises(NotFoundOrDenied):
        change(actor="database-viewer", operation="database-grant-escalation",
               expected_database_epoch=2)

    with closing(sqlite3.connect(store.db_path)) as con:
        before = [con.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                  for table in ("workspace_collaboration_database_access_grants",
                                "workspace_collaboration_operations",
                                "workspace_collaboration_audit_events",
                                "workspace_collaboration_outbox")]
    with pytest.raises(InjectedFailure):
        change(role="comment", operation="database-grant-fault",
               expected_database_epoch=2, fault="after_outbox")
    with closing(sqlite3.connect(store.db_path)) as con:
        after = [con.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                 for table in ("workspace_collaboration_database_access_grants",
                               "workspace_collaboration_operations",
                               "workspace_collaboration_audit_events",
                               "workspace_collaboration_outbox")]
    assert after == before

    revoked = change(role=None, operation="database-grant-revoke",
                     expected_database_epoch=2)
    assert revoked["database_epoch"] == 3 and revoked["grant_epoch"] == 2
    with pytest.raises(NotFoundOrDenied):
        database_access.resolve(store.iam, "database-viewer", workspace_id,
                                "database-e1")
    assert change(role=None, operation="database-grant-revoke",
                  expected_database_epoch=2)["replayed"] is True

    # Revoking the page grant must not retire a guest with a separate DB grant.
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    change(operation="database-guest-regrant",
           expected_database_epoch=owner.database_epoch)
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    store.iam.set_page_grant(
        "owner", "page-e1", "subject", "database-viewer", role="view",
        inherit_to_descendants=False,
        expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        operation_id="database-guest-page-grant",
    )
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    store.iam.revoke_page_grant(
        "owner", "page-e1", "subject", "database-viewer",
        expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        operation_id="database-guest-page-revoke",
    )
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute(
            "SELECT revoked_at FROM workspace_guests WHERE workspace_id=? "
            "AND subject_id='database-viewer'", (workspace_id,),
        ).fetchone()[0] is None
    assert database_access.resolve(store.iam, "database-viewer", workspace_id,
                                   "database-e1").role == "view"
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    change(role=None, operation="database-guest-final-revoke",
           expected_document_epoch=owner.document_epoch,
           expected_database_epoch=owner.database_epoch)
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute(
            "SELECT revoked_at FROM workspace_guests WHERE workspace_id=? "
            "AND subject_id='database-viewer'", (workspace_id,),
        ).fetchone()[0] is not None

    # Removing a member also revokes every direct database grant atomically.
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    store.iam.set_workspace_member(
        "owner", workspace_id, "content-editor", role="member",
        expected_workspace_epoch=owner.workspace_epoch,
        operation_id="database-member-set",
    )
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    member_grant = database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject",
        "content-editor", role="view",
        expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-member-grant",
    )
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    with pytest.raises(InjectedFailure):
        store.iam.revoke_workspace_member(
            "owner", workspace_id, "content-editor",
            expected_workspace_epoch=owner.workspace_epoch,
            operation_id="database-member-revoke-fault", fault="after_outbox",
        )
    assert database_access.resolve(store.iam, "content-editor", workspace_id,
                                   "database-e1").database_epoch == member_grant["database_epoch"]
    member_revoke = store.iam.revoke_workspace_member(
        "owner", workspace_id, "content-editor",
        expected_workspace_epoch=owner.workspace_epoch,
        operation_id="database-member-revoke",
    )
    assert "database-e1" in member_revoke["affected_database_ids"]
    with pytest.raises(NotFoundOrDenied):
        database_access.resolve(store.iam, "content-editor", workspace_id,
                                "database-e1")
    with closing(sqlite3.connect(store.db_path)) as con:
        con.row_factory = sqlite3.Row
        grant = con.execute(
            "SELECT active,grant_epoch FROM workspace_collaboration_database_access_grants "
            "WHERE database_id='database-e1' AND principal_type='subject' "
            "AND principal_id='content-editor'",
        ).fetchone()
        state = con.execute(
            "SELECT access_epoch FROM workspace_collaboration_database_access_state "
            "WHERE database_id='database-e1'",
        ).fetchone()
        assert (grant["active"], grant["grant_epoch"]) == (0, 2)
        assert state["access_epoch"] == member_grant["database_epoch"] + 1

    # A database-only content editor may write values, but not schema or stale state.
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject",
        "database-viewer", role="edit_content",
        expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-content-editor-grant",
    )
    editor = database_access.resolve(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    epochs = dict(expected_workspace_epoch=editor.workspace_epoch,
                  expected_document_epoch=editor.document_epoch,
                  expected_database_epoch=editor.database_epoch)
    with store.iam._reader() as con:
        con.execute("BEGIN")
        def guard(minimum_role="edit_content", **changes):
            return database_access.require_write_on_connection(
                store.iam, con, "database-viewer", workspace_id, "database-e1",
                minimum_role=minimum_role, **{**epochs, **changes},
            )
        assert guard().role == "edit_content"
        with pytest.raises(NotFoundOrDenied):
            guard("edit")
        for field in epochs:
            with pytest.raises(NotFoundOrDenied):
                guard(**{field: epochs[field] + 1})
    assert database_access.read_file_metadata(
        store.iam, "database-viewer", workspace_id, "database-e1",
        attached_file,
    )["file_id"] == attached_file
    scoped_file = database_access.get_file(
        store.iam, "database-viewer", workspace_id, "database-e1",
        attached_file,
    )
    assert scoped_file["path"].read_bytes() == attached_bytes
    assert "document_id" not in scoped_file
    with pytest.raises(NotFoundOrDenied):
        database_access.read_file_metadata(
            store.iam, "database-viewer", workspace_id, "database-e1",
            unrelated_file,
        )
    with pytest.raises(NotFoundOrDenied):
        database_access.get_file(
            store.iam, "database-viewer", workspace_id, "database-e1",
            unrelated_file,
        )
    with closing(sqlite3.connect(store.db_path)) as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("database-estimate", "database-e1", "Estimate", "number", 99,
             "{}", 1, 1),
        )
        con.commit()
    projection = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    row = next(item for item in projection["database"]["rows"]
               if item["id"] == "row-e1")
    command = dict(
        cells=[{"row_id": "row-e1", "property_id": "property-e1",
                "value": "DB-only edit", "expected_target_version":
                row["cell_versions"].get("property-e1", 0)},
               {"row_id": "row-e1", "property_id": "database-estimate",
                "value": 42, "expected_target_version": 0}],
        expected_document_sequence=projection["document_sequence"],
        **epochs,
    )
    with pytest.raises(InjectedFailure):
        database_access.change_cells(
            store.iam, "database-viewer", workspace_id, "database-e1",
            operation_id="database-content-edit-fault", fault="after_outbox", **command,
        )
    assert database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )["document_sequence"] == projection["document_sequence"]
    edited = database_access.change_cells(
        store.iam, "database-viewer", workspace_id, "database-e1",
        operation_id="database-content-edit", **command,
    )
    assert edited["replayed"] is False
    assert "document_id" not in edited and "content_sha256" not in edited
    assert edited["document_sequence"] == projection["document_sequence"] + 1
    assert database_access.change_cells(
        store.iam, "database-viewer", workspace_id, "database-e1",
        operation_id="database-content-edit", **command,
    )["replayed"] is True
    with pytest.raises(OperationConflict):
        database_access.change_cells(
            store.iam, "database-viewer", workspace_id, "database-e1",
            operation_id="database-content-edit",
            **{**command, "cells": [{**command["cells"][0], "value": "other"}]},
        )
    projection = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    assert next(item for item in projection["database"]["rows"]
                if item["id"] == "row-e1")["cells"]["property-e1"] == "DB-only edit"
    row = next(item for item in projection["database"]["rows"]
               if item["id"] == "row-e1")
    assert row["cells"]["database-estimate"] == 42
    assert projection["database"]["last_edit"]["actor_subject_id"] == "database-viewer"
    late_invalid = {
        **command,
        "expected_document_sequence": projection["document_sequence"],
        "cells": [
            {**command["cells"][0], "value": "must roll back",
             "expected_target_version": row["cell_versions"]["property-e1"]},
            {**command["cells"][1], "value": "not a number",
             "expected_target_version": row["cell_versions"]["database-estimate"]},
        ],
    }
    with pytest.raises(ValueError):
        database_access.change_cells(
            store.iam, "database-viewer", workspace_id, "database-e1",
            operation_id="database-content-late-invalid", **late_invalid,
        )
    database_access.change_cells(
        store.iam, "database-viewer", workspace_id, "database-e1",
        cells=[{"row_id": "row-e1", "property_id": "database-attachment",
                "value": [{"file_id": attached_file},
                          {"url": "https://example.com/doc", "name": "External"}],
                "expected_target_version": row["cell_versions"]["database-attachment"]}],
        expected_document_sequence=projection["document_sequence"],
        operation_id="database-content-existing-file", **epochs,
    )
    file_projection = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    file_row = next(item for item in file_projection["database"]["rows"]
                    if item["id"] == "row-e1")
    assert len(file_row["cells"]["database-attachment"]) == 2
    with pytest.raises(NotFoundOrDenied):
        database_access.change_cells(
            store.iam, "database-viewer", workspace_id, "database-e1",
            cells=[{"row_id": "row-e1", "property_id": "database-attachment",
                    "value": [{"file_id": unrelated_file}],
                    "expected_target_version": file_row["cell_versions"]["database-attachment"]}],
            expected_document_sequence=file_projection["document_sequence"],
            operation_id="database-content-unrelated-file", **epochs,
        )
    unchanged = database_access.read_projection(
        store.iam, "database-viewer", workspace_id, "database-e1",
    )
    assert unchanged["document_sequence"] == file_projection["document_sequence"]
    assert next(item for item in unchanged["database"]["rows"]
                if item["id"] == "row-e1")["cells"]["property-e1"] == "DB-only edit"
    with closing(sqlite3.connect(store.db_path)) as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_row_trash_state("
            "row_id,database_id,archive_workspace_id,archive_document_id,"
            "archived_row_version,archived_by_subject_id,archived_at,"
            "archive_operation_id) VALUES(?,?,?,?,?,?,?,?)",
            ("row-e1", "database-e1", workspace_id, "page-e1", 1, "owner", 1,
             "database-file-reference-archive"),
        )
        con.commit()
    with pytest.raises(NotFoundOrDenied):
        database_access.read_file_metadata(
            store.iam, "database-viewer", workspace_id, "database-e1",
            attached_file,
        )
    with pytest.raises(NotFoundOrDenied):
        database_access.get_file(
            store.iam, "database-viewer", workspace_id, "database-e1",
            attached_file,
        )
    with pytest.raises(NotFoundOrDenied):
        store.iam.read_document("database-viewer", "page-e1")
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject",
        "database-viewer", role=None,
        expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-content-editor-revoke",
    )
    with store.iam._reader() as con:
        con.execute("BEGIN")
        with pytest.raises(NotFoundOrDenied):
            database_access.require_write_on_connection(
                store.iam, con, "database-viewer", workspace_id, "database-e1",
                minimum_role="edit_content", **epochs,
            )
    with pytest.raises(NotFoundOrDenied):
        database_access.change_cells(
            store.iam, "database-viewer", workspace_id, "database-e1",
            operation_id="database-content-edit", **command,
        )

"""UI-LINKED-ROWS: linked row creation writes the source under both grants."""
import json
import sqlite3
from contextlib import closing

import pytest

from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default
from code_assist_v1 import notion_workspace_database_sharing_migration as sharing
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope
from code_assist_v1.notion_collaboration import linked_databases
from code_assist_v1.notion_collaboration import database_access
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, NotFoundOrDenied
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply, _schema, SUBJECTS
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import (
    activity_environment, environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_row_icon_batch import (
    icon_batch_environment, appearance_environment, body_environment,
    team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment,
    image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_linked_database_copy import setup_pair
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for


@pytest.mark.integration
@pytest.mark.regression
def test_ui_linked_row_create_scope_replay_and_http(icon_batch_environment, monkeypatch):
    store, workspace = icon_batch_environment
    instance, _title_property = setup_pair(store, workspace)
    for target, backup in (
        (placement, "before-linked-row-placement.db"),
        (custom_emoji, "before-linked-row-emoji.db"),
        (item_layout, "before-linked-row-layout.db"),
        (template_default, "before-linked-row-default.db"),
        (sharing, "before-linked-row-sharing.db"),
        (upload_scope, "before-linked-row-upload.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    _schema(store.iam, "database.template.create", "linked-row-template",
            {"name": "Meeting", "body": [{"id": "template-note",
             "type": "paragraph", "content": "Agenda", "props": {}}]},
            "linked-row-template-create", actor="owner")
    current = linked_databases.read_linked_database_scoped(
        store.iam, "owner", workspace, instance, "linked-block",
    )
    request = {
        "source": current["source"],
        "instance_witness": current["instance_witness"],
        "source_access": current["access"],
        "title": "Created through linked view",
        "operation_id": "linked-row-create",
    }
    instance_before = store.iam.read_document("owner", instance)
    rows_before = len(current["database"]["rows"])
    client, _actor = client_for(monkeypatch, store)
    url = (f"/api/notion/workspaces/{workspace}/pages/{instance}"
           "/linked-databases/linked-block/rows")
    invalid = {**request, "source": {"database_id": "unrelated-database"}}
    assert client.post(url, json=invalid).status_code == 409
    assert len(store.iam.read_database("owner", "database-e1")["rows"]) == rows_before

    response = client.post(url, json=request)
    assert response.status_code == 201, response.json
    created = response.json
    assert created["linked_instance"] == {"document_id": instance, "block_id": "linked-block"}
    assert created["command_kind"] == "database.row.create"
    assert "page-e1" not in json.dumps(created, ensure_ascii=False)
    assert response.headers["Cache-Control"].startswith("no-store")
    replay = client.post(url, json=request)
    assert replay.status_code == 201 and replay.json["replayed"] is True
    assert replay.json["row_id"] == created["row_id"]
    assert client.post(url, json={**request, "title": "Changed operation"}).status_code == 409
    assert client.post(url, json={**request, "source_access": {}}).status_code == 400
    named_access = linked_databases.read_linked_database_scoped(
        store.iam, "owner", workspace, instance, "linked-block",
    )
    named = client.post(url, json={
        **request, "source_access": named_access["access"],
        "operation_id": "linked-row-named", "template_id": "linked-row-template",
    })
    assert named.status_code == 201, named.json
    assert named.json["template_id"] == "linked-row-template"
    blank_access = linked_databases.read_linked_database_scoped(
        store.iam, "owner", workspace, instance, "linked-block",
    )
    blank = client.post(url, json={
        **request, "source_access": blank_access["access"],
        "operation_id": "linked-row-blank", "use_default_template": False,
    })
    assert blank.status_code == 201, blank.json
    assert "template_id" not in blank.json
    assert store.iam.read_document("owner", instance) == instance_before
    source_rows = store.iam.read_database("owner", "database-e1")["rows"]
    linked_rows = linked_databases.read_linked_database_scoped(
        store.iam, "owner", workspace, instance, "linked-block",
    )["database"]["rows"]
    assert len(source_rows) == len(linked_rows) == rows_before + 3
    assert {created["row_id"], named.json["row_id"], blank.json["row_id"]} <= {
        row["id"] for row in source_rows}
    named_row = next(row for row in source_rows if row["id"] == named.json["row_id"])
    assert named_row["body"]["blocks"][0]["content"] == "Agenda"
    assert any(row["id"] == created["row_id"] for row in linked_rows)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_linked_blank_template_create_requires_both_grants_and_hides_parent(
        icon_batch_environment, monkeypatch):
    store, workspace = icon_batch_environment
    instance, title_property = setup_pair(store, workspace)
    for target, backup in (
        (placement, "before-linked-template-placement.db"),
        (custom_emoji, "before-linked-template-emoji.db"),
        (item_layout, "before-linked-template-layout.db"),
        (template_default, "before-linked-template-default.db"),
        (sharing, "before-linked-template-sharing.db"),
        (upload_scope, "before-linked-template-upload.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    editor = "linked-template-editor"
    store.iam.identity_directory.replace([*SUBJECTS, editor])
    page = store.iam.read_document("owner", instance)
    store.iam.set_page_grant(
        "owner", instance, "subject", editor, role="edit_content",
        inherit_to_descendants=False,
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="linked-template-instance-grant",
    )
    owner_access = database_access.resolve(store.iam, "owner", workspace, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace, "database-e1", "subject", editor,
        role="edit_content", expected_workspace_epoch=owner_access.workspace_epoch,
        expected_document_epoch=owner_access.document_epoch,
        expected_database_epoch=owner_access.database_epoch,
        operation_id="linked-template-source-grant",
    )
    content_access = linked_databases.read_linked_database_scoped(
        store.iam, editor, workspace, instance, "linked-block",
    )
    content_request = {
        "source": content_access["source"],
        "instance_witness": content_access["instance_witness"],
        "source_access": content_access["access"],
        "name": "권한 없는 템플릿", "target_id": "linked-template-denied",
        "operation_id": "linked-template-content-denied",
    }
    client, actor = client_for(monkeypatch, store)
    actor["id"] = editor
    url = (f"/api/notion/workspaces/{workspace}/pages/{instance}"
           "/linked-databases/linked-block/templates")
    assert client.post(url, json=content_request).status_code == 404
    owner_access = database_access.resolve(store.iam, "owner", workspace, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace, "database-e1", "subject", editor,
        role="edit", expected_workspace_epoch=owner_access.workspace_epoch,
        expected_document_epoch=owner_access.document_epoch,
        expected_database_epoch=owner_access.database_epoch,
        operation_id="linked-template-source-promote",
    )
    with pytest.raises(NotFoundOrDenied):
        store.iam.read_document(editor, "page-e1")
    current = linked_databases.read_linked_database_scoped(
        store.iam, editor, workspace, instance, "linked-block",
    )
    request = {
        "source": current["source"],
        "instance_witness": current["instance_witness"],
        "source_access": current["access"],
        "name": "회의 기록", "target_id": "linked-template-new",
        "operation_id": "linked-template-create",
    }
    wrong = client.post(url, json={**request, "source": {"database_id": "other"}})
    assert wrong.status_code == 409
    assert client.post(url, json={**request, "body": []}).status_code == 400
    created = client.post(url, json=request)
    assert created.status_code == 201, created.json
    assert created.json["target_id"] == "linked-template-new"
    assert created.json["target_version"] == 1
    assert created.json["linked_instance"] == {
        "document_id": instance, "block_id": "linked-block"}
    assert "page-e1" not in created.get_data(as_text=True)
    assert "document_id" not in created.json or created.json["document_id"] == instance
    replay = client.post(url, json=request)
    assert replay.status_code == 201 and replay.json["target_id"] == "linked-template-new"
    assert client.post(url, json={**request, "name": "another"}).status_code == 409
    latest = linked_databases.read_linked_database_scoped(
        store.iam, editor, workspace, instance, "linked-block",
    )
    template = next(item for item in latest["database"]["templates"]
                    if item["id"] == "linked-template-new")
    assert template["name"] == "회의 기록" and template["body"] == []
    assert template["default_values"] == {}
    assert "page-e1" not in json.dumps(latest, ensure_ascii=False)
    update_url = url + "/linked-template-new"
    update_body = [{"id": "linked-template-note", "type": "paragraph",
                    "content": "안건", "props": {}}]
    update_request = {
        "source": latest["source"],
        "instance_witness": latest["instance_witness"],
        "source_access": latest["access"],
        "name": "회의 기록 수정", "body": update_body,
        "default_values": {title_property: "회의 제목"},
        "expected_target_version": 1,
        "operation_id": "linked-template-update",
    }
    hidden = client.patch(update_url, json={**update_request,
        "body": [{**update_body[0], "content": "비공개 /page/hidden"}]})
    assert hidden.status_code == 404
    assert next(item for item in store.iam.read_database("owner", "database-e1")
                ["templates"] if item["id"] == "linked-template-new")["version"] == 1
    updated = client.patch(update_url, json=update_request)
    assert updated.status_code == 200, updated.json
    assert updated.json["target_version"] == 2
    assert "page-e1" not in updated.get_data(as_text=True)
    replay_update = client.patch(update_url, json=update_request)
    assert replay_update.status_code == 200
    assert replay_update.json["target_version"] == 2
    assert client.patch(update_url, json={**update_request,
        "name": "다른 요청"}).status_code == 409
    after_update = linked_databases.read_linked_database_scoped(
        store.iam, editor, workspace, instance, "linked-block",
    )
    template = next(item for item in after_update["database"]["templates"]
                    if item["id"] == "linked-template-new")
    assert template["name"] == "회의 기록 수정"
    assert template["body"][0]["content"] == "안건"
    assert template["default_values"][title_property] == "회의 제목"
    stale_request = {**update_request,
        "source_access": after_update["access"],
        "operation_id": "linked-template-stale-version"}
    assert client.patch(update_url, json=stale_request).status_code == 409
    hidden_file = {**update_request,
        "source_access": after_update["access"],
        "expected_target_version": 2,
        "operation_id": "linked-template-hidden-file",
        "body": [{"id": "unavailable-image", "type": "image", "content": "",
                  "props": {"file_id": "unavailable-file"}}]}
    assert client.patch(update_url, json=hidden_file).status_code == 404
    created_row = linked_databases.execute_linked_row_create_scoped(
        store.iam, editor, workspace, instance, "linked-block",
        source=after_update["source"],
        instance_witness=after_update["instance_witness"],
        source_access=after_update["access"], title="",
        template_id="linked-template-new",
        operation_id="linked-template-row-create",
    )
    source_row = next(item for item in store.iam.read_database("owner", "database-e1")
                      ["rows"] if item["id"] == created_row["row_id"])
    assert source_row["body"]["blocks"][0]["content"] == "안건"
    assert source_row["cells"][title_property] == "회의 제목"
    latest = linked_databases.read_linked_database_scoped(
        store.iam, editor, workspace, instance, "linked-block",
    )
    before_failure = store.iam.read_database("owner", "database-e1")
    with pytest.raises(InjectedFailure):
        linked_databases.execute_linked_template_create_scoped(
            store.iam, editor, workspace, instance, "linked-block",
            source=latest["source"],
            instance_witness=latest["instance_witness"],
            source_access=latest["access"], name="실패한 템플릿",
            target_id="linked-template-rollback",
            operation_id="linked-template-fault", fault="after_domain",
        )
    assert store.iam.read_database("owner", "database-e1") == before_failure
    owner_access = database_access.resolve(store.iam, "owner", workspace, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace, "database-e1", "subject", editor,
        role=None, expected_workspace_epoch=owner_access.workspace_epoch,
        expected_document_epoch=owner_access.document_epoch,
        expected_database_epoch=owner_access.database_epoch,
        operation_id="linked-template-source-revoke",
    )
    assert client.post(url, json=request).status_code == 404


@pytest.mark.integration
@pytest.mark.regression
def test_ui_linked_template_cross_database_relation_checks_target_grant(
        icon_batch_environment, monkeypatch):
    store, workspace = icon_batch_environment
    instance, _ = setup_pair(store, workspace)
    for target, backup in (
        (placement, "before-linked-cross-relation-placement.db"),
        (custom_emoji, "before-linked-cross-relation-emoji.db"),
        (item_layout, "before-linked-cross-relation-layout.db"),
        (template_default, "before-linked-cross-relation-default.db"),
        (sharing, "before-linked-cross-relation-sharing.db"),
        (upload_scope, "before-linked-cross-relation-upload.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    editor = "linked-cross-relation-editor"
    store.iam.identity_directory.replace([*SUBJECTS, editor])
    page = store.iam.read_document("owner", instance)
    store.iam.set_page_grant(
        "owner", instance, "subject", editor, role="edit_content",
        inherit_to_descendants=False,
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="linked-cross-relation-instance-grant",
    )
    source_access = database_access.resolve(
        store.iam, "owner", workspace, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace, "database-e1", "subject", editor,
        role="edit", expected_workspace_epoch=source_access.workspace_epoch,
        expected_document_epoch=source_access.document_epoch,
        expected_database_epoch=source_access.database_epoch,
        operation_id="linked-cross-relation-source-grant",
    )
    with pytest.raises(NotFoundOrDenied):
        store.iam.read_document(editor, "page-e1")
    with closing(sqlite3.connect(store.db_path)) as con:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(
            "INSERT INTO workspace_collaboration_databases("
            "id,document_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("linked-cross-target", "page-e1", "Private target", 1, 1),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("linked-cross-target-row", "linked-cross-target", 0, 1, 1),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("linked-cross-relation", "database-e1", "Related", "relation", 99,
             json.dumps({"target_database_id": "linked-cross-target"}), 1, 1),
        )
        con.commit()
    _schema(store.iam, "database.template.create", "linked-cross-template",
            {"name": "Cross relation", "body": [], "default_values": {}},
            "linked-cross-template-create", actor="owner")
    client, actor = client_for(monkeypatch, store)
    actor["id"] = editor
    url = (f"/api/notion/workspaces/{workspace}/pages/{instance}"
           "/linked-databases/linked-block/templates/linked-cross-template")

    def request(operation, version=1):
        current = linked_databases.read_linked_database_scoped(
            store.iam, editor, workspace, instance, "linked-block")
        return {"source": current["source"],
                "instance_witness": current["instance_witness"],
                "source_access": current["access"],
                "name": "Cross relation", "body": [],
                "default_values": {"linked-cross-relation": ["linked-cross-target-row"]},
                "expected_target_version": version, "operation_id": operation}

    denied = client.patch(url, json=request("linked-cross-template-denied"))
    assert denied.status_code == 404
    assert next(item for item in store.iam.read_database("owner", "database-e1")
                ["templates"] if item["id"] == "linked-cross-template")["version"] == 1
    target_access = database_access.resolve(
        store.iam, "owner", workspace, "linked-cross-target")
    database_access.change_grant(
        store.iam, "owner", workspace, "linked-cross-target", "subject", editor,
        role="view", expected_workspace_epoch=target_access.workspace_epoch,
        expected_document_epoch=target_access.document_epoch,
        expected_database_epoch=target_access.database_epoch,
        operation_id="linked-cross-relation-target-grant",
    )
    invalid_row = request("linked-cross-template-missing-row")
    invalid_row["default_values"] = {
        "linked-cross-relation": ["linked-cross-missing-row"]}
    assert client.patch(url, json=invalid_row).status_code == 404
    visible = client.patch(url, json=request("linked-cross-template-allowed"))
    assert visible.status_code == 200, visible.json
    assert visible.json["target_version"] == 2
    assert "page-e1" not in visible.get_data(as_text=True)
    current = linked_databases.read_linked_database_scoped(
        store.iam, editor, workspace, instance, "linked-block")
    template = next(item for item in current["database"]["templates"]
                    if item["id"] == "linked-cross-template")
    assert template["default_values"]["linked-cross-relation"] == [
        "linked-cross-target-row"]
    assert template.get("default_unavailable", False) is False
    created = linked_databases.execute_linked_row_create_scoped(
        store.iam, editor, workspace, instance, "linked-block",
        source=current["source"],
        instance_witness=current["instance_witness"],
        source_access=current["access"], title="",
        template_id="linked-cross-template",
        operation_id="linked-cross-relation-row-create",
    )
    created_row = next(
        row for row in store.iam.read_database("owner", "database-e1")["rows"]
        if row["id"] == created["row_id"])
    assert created_row["cells"]["linked-cross-relation"] == [
        "linked-cross-target-row"]
    with closing(sqlite3.connect(store.db_path)) as con:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("linked-cross-inverse", "linked-cross-target", "Backlinks", "relation", 0,
             json.dumps({"target_database_id": "database-e1",
                         "reciprocal_property_id": "linked-cross-relation"}), 1, 1),
        )
        con.execute(
            "UPDATE workspace_collaboration_database_properties "
            "SET config_json=?,row_version=row_version+1 WHERE id=?",
            (json.dumps({"target_database_id": "linked-cross-target",
                         "reciprocal_property_id": "linked-cross-inverse"}),
             "linked-cross-relation"),
        )
        con.commit()
    paired_denied = client.patch(
        url, json=request("linked-cross-paired-view-denied", version=2))
    assert paired_denied.status_code == 404
    target_access = database_access.resolve(
        store.iam, "owner", workspace, "linked-cross-target")
    database_access.change_grant(
        store.iam, "owner", workspace, "linked-cross-target", "subject", editor,
        role="edit_content", expected_workspace_epoch=target_access.workspace_epoch,
        expected_document_epoch=target_access.document_epoch,
        expected_database_epoch=target_access.database_epoch,
        operation_id="linked-cross-relation-target-edit-grant",
    )
    paired_allowed = client.patch(
        url, json=request("linked-cross-paired-edit-allowed", version=2))
    assert paired_allowed.status_code == 200, paired_allowed.json
    assert paired_allowed.json["target_version"] == 3
    paired_current = linked_databases.read_linked_database_scoped(
        store.iam, editor, workspace, instance, "linked-block")
    paired_row = linked_databases.execute_linked_row_create_scoped(
        store.iam, editor, workspace, instance, "linked-block",
        source=paired_current["source"],
        instance_witness=paired_current["instance_witness"],
        source_access=paired_current["access"], title="",
        template_id="linked-cross-template",
        operation_id="linked-cross-paired-row-create",
    )
    paired_source_row = next(
        row for row in store.iam.read_database("owner", "database-e1")["rows"]
        if row["id"] == paired_row["row_id"])
    paired_target_row = next(
        row for row in store.iam.read_database("owner", "linked-cross-target")["rows"]
        if row["id"] == "linked-cross-target-row")
    assert paired_source_row["cells"]["linked-cross-relation"] == [
        "linked-cross-target-row"]
    assert paired_row["row_id"] in paired_target_row["cells"]["linked-cross-inverse"]
    target_access = database_access.resolve(
        store.iam, "owner", workspace, "linked-cross-target")
    database_access.change_grant(
        store.iam, "owner", workspace, "linked-cross-target", "subject", editor,
        role=None, expected_workspace_epoch=target_access.workspace_epoch,
        expected_document_epoch=target_access.document_epoch,
        expected_database_epoch=target_access.database_epoch,
        operation_id="linked-cross-relation-target-revoke",
    )
    revoked = linked_databases.read_linked_database_scoped(
        store.iam, editor, workspace, instance, "linked-block")
    template = next(item for item in revoked["database"]["templates"]
                    if item["id"] == "linked-cross-template")
    assert template["default_unavailable"] is True
    source_rows_before = store.iam.read_database("owner", "database-e1")["rows"]
    target_rows_before = store.iam.read_database("owner", "linked-cross-target")["rows"]
    with pytest.raises(NotFoundOrDenied):
        linked_databases.execute_linked_row_create_scoped(
            store.iam, editor, workspace, instance, "linked-block",
            source=revoked["source"],
            instance_witness=revoked["instance_witness"],
            source_access=revoked["access"], title="",
            template_id="linked-cross-template",
            operation_id="linked-cross-revoked-row-denied",
        )
    assert store.iam.read_database("owner", "database-e1")["rows"] == source_rows_before
    assert store.iam.read_database("owner", "linked-cross-target")["rows"] == target_rows_before

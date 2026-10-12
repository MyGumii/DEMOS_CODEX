"""UI-R2/R5: one database's update feed excludes parent-page edits."""
import pytest

from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.database_updates import read_database_updates
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import (
    activity_environment, request, appearance_environment, body_environment,
    team_environment, file_environment, member_environment, native_environment,
    archive_environment, guest_environment, image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import environment
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for
from code_assist_v1.tests.notion_collaboration.test_database_row_move_batch import (
    _destination, _request as row_move_request,
)
from code_assist_v1.tests.notion_collaboration.test_database_row_icon_batch import icon_batch_environment
from code_assist_v1.notion_collaboration.database_row_move import move_owned_rows


def test_ui_r2_database_updates_are_authorized_scoped_and_paginated(activity_environment, monkeypatch):
    store, workspace_id = activity_environment
    read = lambda actor="owner", **options: read_database_updates(
        store.iam, actor, workspace_id, "database-e1", **options)
    assert read()["items"] == []

    schema = _schema(store.iam, "database.property.create", "updates-property",
        {"name": "Updates", "type": "text", "config": {}}, "updates-schema", actor="owner")
    row = store.iam.read_database("owner", "database-e1")["rows"][0]
    cell = store.iam.execute_typed_command("owner", "page-e1",
        command_kind="database.cell.set", target_id=f"{row['id']}:updates-property",
        payload={"row_id": row["id"], "property_id": "updates-property", "value": "private value"},
        expected_target_version=0, operation_id="updates-cell", **request(store))
    store.iam.execute_schema_command("owner", "page-e1",
        database_id="updates-other-database", command_kind="database.create",
        target_id="updates-other-database", payload={"title": "Other"},
        operation_id="updates-other-database", **request(store))
    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert",
        target_id="updates-unrelated", payload={"type": "paragraph", "content": "unrelated",
            "after_block_id": None}, operation_id="updates-parent-edit", **request(store))

    feed = read("content-editor", limit=1)
    assert len(feed["items"]) == 1
    assert feed["items"][0]["document_sequence"] == cell["document_sequence"]
    assert feed["items"][0]["command_kind"] == "database.cell.set"
    assert feed["items"][0]["row_id"] == row["id"]
    assert "private value" not in str(feed)
    older = read("content-editor", before_sequence=feed["next_before_sequence"])
    assert [item["document_sequence"] for item in older["items"]] == [schema["document_sequence"]]
    assert older["next_before_sequence"] is None

    client, actor = client_for(monkeypatch, store)
    actor["id"] = "content-editor"
    response = client.get(
        f"/api/notion/workspaces/{workspace_id}/databases/database-e1/updates?limit=1"
    )
    assert response.status_code == 200
    assert response.json["document_id"] == "page-e1"
    assert response.json["items"][0]["command_kind"] == "database.cell.set"

    with pytest.raises(NotFoundOrDenied):
        read("unrelated-user")
    with pytest.raises(NotFoundOrDenied):
        read_database_updates(store.iam, "owner", workspace_id, "missing-database")
    with pytest.raises(ValueError):
        read(limit=101)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r2_historical_cell_update_stays_with_source_after_row_move(icon_batch_environment):
    store, workspace_id = icon_batch_environment
    authority = store.iam
    _destination(store)
    _schema(authority, "database.property.create", "updates-moved-property",
            {"name": "Moved history", "type": "text", "config": {}},
            "updates-moved-schema", actor="owner")
    page = authority.read_document("owner", "page-e1")
    cell = authority.execute_typed_command(
        "owner", "page-e1", command_kind="database.cell.set",
        target_id="row-e1:updates-moved-property",
        payload={"row_id": "row-e1", "property_id": "updates-moved-property",
                 "value": "source only"},
        expected_target_version=0,
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="updates-cell-before-row-move",
    )
    row = next(item for item in authority.read_database("owner", "database-e1")["rows"]
               if item["id"] == "row-e1")
    move_owned_rows(authority, **row_move_request(
        store, workspace_id, rows=[row], operation="updates-row-move"))

    source = read_database_updates(authority, "owner", workspace_id, "database-e1")
    destination = read_database_updates(authority, "owner", workspace_id, "database-e2")
    assert any(item["document_sequence"] == cell["document_sequence"]
               for item in source["items"])
    assert not any(item["document_sequence"] == cell["document_sequence"]
                   for item in destination["items"])
    assert "source only" not in str(source) + str(destination)

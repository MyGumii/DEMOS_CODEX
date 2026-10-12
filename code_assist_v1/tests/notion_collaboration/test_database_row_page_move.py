"""Selected canonical rows become ordinary child pages in one transaction."""

from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
import json
import multiprocessing
import socket
import sqlite3
import time
import urllib.error
import urllib.request

import pytest
from flask import Flask

from code_assist_v1 import notion_workspace_database_row_page_placement_migration as migration
from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure
from code_assist_v1.notion_collaboration.database_row_page_move import (
    RETURN_ACTION, list_page_database_destinations, move_owned_rows_to_page,
    read_page_return_target,
    return_page_to_owned_database,
)
from code_assist_v1.notion_collaboration.database_row_move import (
    move_owned_rows, read_owned_row_location,
)
from code_assist_v1.notion_collaboration.database_relation_pair import (
    disable_relation_pair, enable_relation_pair,
)
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.editing_authority import (
    DocumentSequenceConflict, TargetVersionConflict,
)
from code_assist_v1.notion_collaboration.page_move import move_page
from code_assist_v1.notion_collaboration.iam_runtime import (
    CollaborativeWorkspaceStore, collaborative_page_payload,
)
from code_assist_v1.notion_collaboration.iam_authority import (
    InMemoryIdentityDirectory,
)
from code_assist_v1.notion_collaboration.editing_authority import (
    CollaborativeEditingTransportAuthority,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply, _surface
from code_assist_v1.tests.notion_collaboration.test_database_move_commit import _upload_png
from code_assist_v1.tests.notion_collaboration.test_database_row_move_batch import (
    _cross_destination, _destination,
)
from code_assist_v1.tests.notion_collaboration.test_database_trash_http import _client
from code_assist_v1.tests.notion_collaboration.test_title_projection import (
    append as append_live_title, yjs,
)
from code_assist_v1.tests.notion_collaboration.test_database_row_page_placement_migration import (
    icon_batch_environment, activity_environment, environment,
    appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment,
    archive_environment, guest_environment, image_environment,
)


@pytest.fixture()
def placement_environment(icon_batch_environment):
    old, workspace_id = icon_batch_environment
    _apply(migration, old.db_path, old.user_data_root,
           "before-row-to-page-2424.db")
    store = CollaborativeWorkspaceStore(
        old.db_path, user_data_root=old.user_data_root,
        identity_directory=old.identity_directory,
    )
    with store.iam._transaction() as con:
        for property_id, name, kind, position in (
                ("property-title", "이름", "title", 1),
                ("property-date", "날짜", "date", 2)):
            con.execute(
                "INSERT INTO workspace_collaboration_database_properties("
                "id,database_id,name,property_type,position,config_json,"
                "row_version,created_at,updated_at) VALUES(?, 'database-e1',"
                "?,?,?,'{}',1,1,1)",
                (property_id, name, kind, position),
            )
        for row_id, title in (("row-e1", "페이지로 이동 A"),
                              ("row-e2", "페이지로 이동 B")):
            con.execute(
                "INSERT INTO workspace_collaboration_database_cells("
                "row_id,property_id,value_json,row_version,updated_at) "
                "VALUES(?,'property-title',?,1,1)",
                (row_id, '"' + title + '"'),
            )
        con.execute(
            "INSERT INTO workspace_collaboration_database_cells("
            "row_id,property_id,value_json,row_version,updated_at) "
            "VALUES('row-e1','property-date','\"2026-09-15\"',1,1)"
        )
    return store, workspace_id


def _request(store, workspace_id, operation="row-to-page-two", *,
             parent_id="page-e1", row_ids=None):
    page = store.iam.read_document("owner", "page-e1")
    parent = store.iam.read_document("owner", parent_id)
    database = store.iam.read_database("owner", "database-e1")
    return dict(
        actor_subject_id="owner", workspace_id=workspace_id,
        source_document_id="page-e1", source_database_id="database-e1",
        parent_document_id=parent_id,
        rows=[{"row_id": item["id"], "expected_target_version": item["version"]}
              for item in database["rows"] if row_ids is None or item["id"] in row_ids],
        expected_source_sequence=page["document_sequence"],
        expected_parent_sequence=parent["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_source_epoch=page["document_epoch"],
        expected_parent_epoch=parent["document_epoch"],
        operation_id=operation,
    )


def _return_request(store, workspace_id, operation="page-to-original-database"):
    authority = store.iam
    page = authority.read_document("owner", "row-e1")
    parent = authority.read_document("owner", page["parent_document_id"])
    destination = authority.read_document("owner", "page-e1")
    with authority._reader() as con:
        row_version = con.execute(
            "SELECT row_version FROM workspace_collaboration_database_rows "
            "WHERE id='row-e1'",
        ).fetchone()[0]
    return dict(
        actor_subject_id="owner", workspace_id=workspace_id,
        page_document_id="row-e1", destination_document_id="page-e1",
        destination_database_id="database-e1",
        expected_page_sequence=page["document_sequence"],
        expected_parent_sequence=parent["document_sequence"],
        expected_destination_sequence=destination["document_sequence"],
        expected_row_version=row_version,
        expected_workspace_epoch=page["workspace_epoch"],
        expected_page_epoch=page["document_epoch"],
        expected_parent_epoch=parent["document_epoch"],
        expected_destination_epoch=destination["document_epoch"],
        operation_id=operation,
    )


def _page_return_worker(args):
    catalog, root, request = args
    store = CollaborativeWorkspaceStore(
        catalog, user_data_root=root,
        identity_directory=InMemoryIdentityDirectory(("owner",)))
    return return_page_to_owned_database(store.iam, **request)


def _relation_pair_enable_worker(args):
    catalog, root, request = args
    store = CollaborativeWorkspaceStore(
        catalog, user_data_root=root,
        identity_directory=InMemoryIdentityDirectory(("owner",)))
    return enable_relation_pair(store.iam, **request)


def _relation_pair_permission_race_worker(args):
    catalog, root, barrier, operation, request, delay = args
    store = CollaborativeWorkspaceStore(
        catalog, user_data_root=root,
        identity_directory=InMemoryIdentityDirectory(("owner", "schema-editor")))
    barrier.wait(timeout=30)
    if delay:
        time.sleep(delay)
    try:
        if operation == "enable":
            enable_relation_pair(store.iam, **request)
        else:
            store.iam.revoke_page_grant(
                "owner", "page-e2", "subject", "schema-editor", **request)
        return operation, "committed"
    except Exception as error:
        return operation, type(error).__name__


def _relation_pair_cell_worker(args):
    catalog, root, document_id, request = args
    store = CollaborativeWorkspaceStore(
        catalog, user_data_root=root,
        identity_directory=InMemoryIdentityDirectory(("owner",)))
    try:
        result = store.iam.execute_typed_command("owner", document_id, **request)
        return "committed", result["document_sequence"]
    except (DocumentSequenceConflict, TargetVersionConflict) as error:
        return type(error).__name__, None


def _page_return_http_worker(catalog, root, port):
    store = CollaborativeWorkspaceStore(
        catalog, user_data_root=root,
        identity_directory=InMemoryIdentityDirectory(("owner",)))
    capabilities = store.capabilities
    store.capabilities = lambda: {
        **capabilities(), "collaborative_page_to_database_row": True}
    routes._store = lambda: store
    routes._collaborative_runtime_enabled = lambda: True
    routes.current_user_id = lambda: "owner"
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-page-return-http")
    routes.register_notion_workspace_routes(app)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


def _available_local_port():
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _page_return_http_json(url, body=None):
    request = urllib.request.Request(
        url, data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="GET" if body is None else "POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.load(response)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_rows_move_to_ordinary_page_atomically_with_hidden_date_and_comments(
        placement_environment):
    store, workspace_id = placement_environment
    authority = store.iam
    _surface(authority, "database.row.content.set", "row-e1",
             {"blocks": [{"id": "row-page-body", "type": "paragraph",
                          "content": "Keep this body", "props": {}}]},
             "seed-row-page-body", actor="owner", target_version=1)
    _surface(authority, "database.row.appearance.set", "row-e1",
             {"icon": "😀"}, "seed-row-page-icon", actor="owner",
             target_version=2)
    page = authority.read_document("owner", "page-e1")
    authority.add_comment(
        "owner", "page-e1", "row-page-comment",
        content="Keep this comment",
        anchor={"kind": "database_row", "database_id": "database-e1",
                "row_id": "row-e1"},
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="seed-row-page-comment",
    )
    request = _request(store, workspace_id)
    before = authority.read_database("owner", "database-e1")

    with pytest.raises(InjectedFailure):
        move_owned_rows_to_page(authority, **request, fault="after_domain")
    assert authority.read_database("owner", "database-e1") == before
    with authority._reader() as con:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_database_row_page_placements"
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_documents "
            "WHERE id IN ('row-e1','row-e2')"
        ).fetchone()[0] == 0

    result = move_owned_rows_to_page(authority, **request)
    assert move_owned_rows_to_page(authority, **request) == result
    assert [item["page_id"] for item in result["rows"]] == ["row-e1", "row-e2"]
    assert result["document_sequence"] == request["expected_source_sequence"] + 4
    assert authority.read_database("owner", "database-e1")["rows"] == []
    child = authority.read_document("owner", "row-e1")
    assert child["title"] == "페이지로 이동 A"
    assert child["icon"] == "😀"
    assert child["blocks"][0]["content"] == "Keep this body"
    assert child["parent_document_id"] == "page-e1"
    assert read_owned_row_location(authority, "owner", workspace_id, "row-e1") == {
        "contract": "code_assist.notion.database_row_location.v1",
        "workspace_id": workspace_id, "document_id": "row-e1",
        "row_id": "row-e1", "placement": "page",
    }
    with pytest.raises(NotFoundOrDenied):
        read_owned_row_location(authority, "owner", "other-workspace", "row-e1")
    assert authority.list_comments("owner", "row-e1")["items"][0]["id"] == (
        "row-page-comment")
    parent = authority.read_document("owner", "page-e1")
    links = [item for item in parent["blocks"] if item["type"] == "child_page"]
    assert [item["props"]["page_id"] for item in links] == ["row-e1", "row-e2"]
    with authority._reader() as con:
        date = con.execute(
            "SELECT value_json FROM workspace_collaboration_database_cells "
            "WHERE row_id='row-e1' AND property_id='property-date'"
        ).fetchone()[0]
        assert date == '"2026-09-15"'
        assert not con.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_row_to_page_cross_parent_keeps_attachment_and_both_sequences(
        placement_environment):
    store, workspace_id = placement_environment
    authority = store.iam
    source = authority.read_document("owner", "page-e1")
    authority.create_document(
        "owner", workspace_id, "page-e2", title="Different parent",
        blocks=[{"id": "different-parent-body", "type": "paragraph",
                 "content": "Unrelated content", "props": {}}],
        expected_workspace_epoch=source["workspace_epoch"],
        operation_id="row-page-create-parent",
    )
    file_id, png = _upload_png(store, "row-page-file")
    _surface(authority, "database.row.content.set", "row-e1",
             {"blocks": [{"id": "row-page-file-block", "type": "file",
                          "content": "Attachment", "props": {"file_id": file_id}}]},
             "seed-row-page-file-body", actor="owner", target_version=1)
    _surface(authority, "database.row.appearance.set", "row-e1",
             {"icon": "nf:" + file_id}, "seed-row-page-file-icon",
             actor="owner", target_version=2)
    request = _request(store, workspace_id, "row-to-other-parent",
                       parent_id="page-e2", row_ids={"row-e1"})
    result = move_owned_rows_to_page(authority, **request)
    assert result["document_sequence"] == request["expected_source_sequence"] + 1
    assert result["parent_document_sequence"] == request["expected_parent_sequence"] + 2
    assert result["copied_file_count"] == 1
    assert [item["id"] for item in authority.read_database("owner", "database-e1")["rows"]] == ["row-e2"]
    child = authority.read_document("owner", "row-e1")
    copied_id = child["blocks"][0]["props"]["file_id"]
    assert copied_id != file_id
    assert child["icon"] == "nf:" + copied_id
    assert authority.get_file("owner", copied_id)["path"].read_bytes() == png
    assert authority.get_file("owner", file_id)["path"].read_bytes() == png
    parent = authority.read_document("owner", "page-e2")
    assert parent["blocks"][-1]["props"]["page_id"] == "row-e1"
    with authority._reader() as con:
        file = con.execute(
            "SELECT document_id,source_file_id,source_document_id FROM "
            "workspace_collaboration_files WHERE id=?", (copied_id,),
        ).fetchone()
        assert tuple(file) == ("row-e1", file_id, "page-e1")
        assert not con.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_row_to_page_http_gate_and_replay(placement_environment,
                                                 monkeypatch):
    store, workspace_id = placement_environment
    request = _request(store, workspace_id, "row-page-http")
    body = {
        "database_id": "database-e1", "target_id": "database-e1",
        "command_kind": "database.rows.move_to_page",
        "payload": {
            "parent_document_id": "page-e1",
            "expected_parent_sequence": request["expected_parent_sequence"],
            "expected_parent_epoch": request["expected_parent_epoch"],
            "rows": request["rows"],
        },
        "expected_document_sequence": request["expected_source_sequence"],
        "expected_workspace_epoch": request["expected_workspace_epoch"],
        "expected_document_epoch": request["expected_source_epoch"],
        "expected_target_version": None,
        "operation_id": request["operation_id"],
    }
    path = (f"/api/notion/workspaces/{workspace_id}/pages/page-e1/"
            "databases/commands")
    client, _actor = _client(store, monkeypatch, enabled=False)
    assert client.post(path, json=body).status_code == 503
    capabilities = store.capabilities
    monkeypatch.setattr(store, "capabilities", lambda: {
        **capabilities(), "collaborative_database_row_to_page_batch": True})
    response = client.post(path, json=body)
    assert response.status_code == 201, response.json
    assert response.json["command_kind"] == "database.rows.move_to_page"
    assert client.post(path, json=body).json == response.json
    assert store.iam.read_database("owner", "database-e1")["rows"] == []
    location_path = (f"/api/notion/workspaces/{workspace_id}/"
                     "database-rows/row-e1/location")
    location = client.get(location_path)
    assert location.status_code == 200
    assert location.json == {
        "contract": "code_assist.notion.database_row_location.v1",
        "workspace_id": workspace_id, "document_id": "row-e1",
        "row_id": "row-e1", "placement": "page",
    }


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_date_reminder_follows_row_page_and_original_database(
        placement_environment):
    store, workspace_id = placement_environment
    authority = store.iam
    reminder = {
        "at": "2027-01-14T00:00:00.000Z", "lead": 0,
        "user_id": "owner", "preset": "day_1", "zone": "Asia/Seoul",
    }
    value = {"start": "2027-01-15", "reminder": reminder}
    page = authority.read_document("owner", "page-e1")
    authority.execute_cell_batch(
        "owner", "page-e1", database_id="database-e1",
        target_id="database-e1",
        payload={"cells": [{"row_id": "row-e1", "property_id": "property-date",
                            "value": value, "expected_target_version": 1}]},
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="date-reminder-before-page-move",
    )
    stored = next(row for row in authority.read_database(
        "owner", "database-e1")["rows"] if row["id"] == "row-e1")[
            "cells"]["property-date"]
    initial = authority.list_reminders("owner", workspace_id)["items"]
    assert len(initial) == 1
    assert initial[0]["page_id"] == "page-e1"

    request = _request(store, workspace_id, "date-reminder-to-page",
                       row_ids={"row-e1"})
    move_receipt = move_owned_rows_to_page(authority, **request)
    assert move_owned_rows_to_page(authority, **request) == move_receipt
    assert authority.read_document("owner", "row-e1")["title"] == "페이지로 이동 A"
    with authority._reader() as con:
        assert json.loads(con.execute(
            "SELECT value_json FROM workspace_collaboration_database_cells "
            "WHERE row_id='row-e1' AND property_id='property-date'"
        ).fetchone()[0]) == stored
    during = authority.list_reminders("owner", workspace_id)["items"]
    assert len(during) == 1
    assert during[0]["page_id"] == "row-e1"
    assert during[0]["remind_at"] == reminder["at"]

    return_request = _return_request(
        store, workspace_id, "date-reminder-back-to-database")
    return_receipt = return_page_to_owned_database(authority, **return_request)
    assert return_page_to_owned_database(authority, **return_request) == return_receipt
    restored = next(row for row in authority.read_database(
        "owner", "database-e1")["rows"] if row["id"] == "row-e1")
    assert restored["cells"]["property-date"] == stored
    after = authority.list_reminders("owner", workspace_id)["items"]
    assert len(after) == 1
    assert after[0]["page_id"] == "page-e1"
    assert after[0]["remind_at"] == reminder["at"]


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_date_reminder_follows_ordinary_page_to_other_database(
        placement_environment):
    store, workspace_id = placement_environment
    authority = store.iam
    _cross_destination(store, workspace_id)
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,"
            "row_version,created_at,updated_at) VALUES("
            "'property-title-e2','database-e2','Name','title',1,'{}',1,1,1)")
    reminder = {
        "at": "2027-01-14T00:00:00.000Z", "lead": 0,
        "user_id": "owner", "preset": "day_1", "zone": "Asia/Seoul",
    }
    page = authority.read_document("owner", "page-e1")
    authority.execute_cell_batch(
        "owner", "page-e1", database_id="database-e1",
        target_id="database-e1",
        payload={"cells": [{"row_id": "row-e1", "property_id": "property-date",
                            "value": {"start": "2027-01-15", "reminder": reminder},
                            "expected_target_version": 1}]},
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="date-reminder-before-other-db-return",
    )
    stored = next(row for row in authority.read_database(
        "owner", "database-e1")["rows"] if row["id"] == "row-e1")[
            "cells"]["property-date"]
    move_owned_rows_to_page(authority, **_request(
        store, workspace_id, "date-reminder-page-to-other-db",
        parent_id="page-e2", row_ids={"row-e1"}))
    middle = authority.list_reminders("owner", workspace_id)["items"]
    assert len(middle) == 1 and middle[0]["page_id"] == "row-e1"

    request = _return_request(
        store, workspace_id, "date-reminder-into-other-db")
    destination = authority.read_document("owner", "page-e2")
    request.update(destination_document_id="page-e2",
                   destination_database_id="database-e2",
                   expected_destination_sequence=destination["document_sequence"],
                   expected_destination_epoch=destination["document_epoch"])
    receipt = return_page_to_owned_database(authority, **request)
    assert return_page_to_owned_database(authority, **request) == receipt
    created_date = next(item["id"] for item in receipt["created_properties"]
                        if item["name"] == "날짜" and item["type"] == "date")
    moved = next(row for row in authority.read_database(
        "owner", "database-e2")["rows"] if row["id"] == "row-e1")
    assert moved["cells"][created_date] == stored
    after = authority.list_reminders("owner", workspace_id)["items"]
    assert len(after) == 1
    assert after[0]["page_id"] == "page-e2"
    assert after[0]["block_id"] == "database-e2"
    assert after[0]["remind_at"] == reminder["at"]


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_page_returns_to_original_database_with_edited_body_and_hidden_date(
        placement_environment):
    store, workspace_id = placement_environment
    authority = store.iam
    _surface(authority, "database.row.content.set", "row-e1",
             {"blocks": [{"id": "row-return-body", "type": "paragraph",
                          "content": "Original row body", "props": {}}]},
             "seed-row-return-body", actor="owner", target_version=1)
    move_owned_rows_to_page(authority, **_request(
        store, workspace_id, "row-to-page-before-return", row_ids={"row-e1"}))
    page = authority.read_document("owner", "row-e1")
    body = [{**page["blocks"][0], "content": "Edited as an ordinary page"}]
    authority.save_document(
        "owner", "row-e1", title="Edited page title", blocks=body,
        expected_revision=page["revision"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="edit-before-page-return",
    )
    request = _return_request(store, workspace_id)
    before = authority.read_database("owner", "database-e1")
    with pytest.raises(InjectedFailure):
        return_page_to_owned_database(authority, **request, fault="after_domain")
    assert authority.read_database("owner", "database-e1") == before
    assert authority.read_document("owner", "row-e1")["title"] == "Edited page title"

    result = return_page_to_owned_database(authority, **request)
    assert return_page_to_owned_database(authority, **request) == result
    assert result["row_id"] == result["page_id"] == "row-e1"
    row = authority.read_database("owner", "database-e1")["rows"][0]
    assert row["id"] == "row-e1"
    assert row["cells"]["property-title"] == "Edited page title"
    assert row["cells"]["property-date"] == "2026-09-15"
    assert row["body"]["blocks"][0]["content"] == "Edited as an ordinary page"
    assert read_owned_row_location(authority, "owner", workspace_id, "row-e1")[
        "database_id"] == "database-e1"
    parent = authority.read_document("owner", "page-e1")
    assert not any(block["type"] == "child_page" and block["props"].get(
        "page_id") == "row-e1" for block in parent["blocks"])
    with authority._reader() as con:
        placed = con.execute(
            "SELECT placement,parent_document_id FROM "
            "workspace_collaboration_database_row_page_placements WHERE row_id='row-e1'"
        ).fetchone()
        assert tuple(placed) == ("database", None)
        assert con.execute(
            "SELECT archived FROM workspace_collaboration_documents "
            "WHERE id='row-e1'"
        ).fetchone()[0] == 1
        assert not con.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_inbound_relation_hides_during_ordinary_page_and_returns(
        placement_environment):
    store, workspace_id = placement_environment
    authority = store.iam
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_databases("
            "id,document_id,title,row_version,created_at,updated_at) "
            "VALUES('database-inbound','page-e1','Inbound',1,1,1)")
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,"
            "row_version,created_at,updated_at) VALUES("
            "'property-inbound','database-inbound','Back','relation',0,?,1,1,1)",
            (json.dumps({'target_database_id': 'database-e1'}),))
        con.execute(
            "INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,row_version,created_at,updated_at) "
            "VALUES('row-inbound','database-inbound',0,1,1,1)")
        con.execute(
            "INSERT INTO workspace_collaboration_database_cells("
            "row_id,property_id,value_json,row_version,updated_at) "
            "VALUES('row-inbound','property-inbound',?,1,1)",
            (json.dumps(['row-e1']),))
    assert authority.read_database('owner', 'database-inbound')['rows'][0][
        'cells']['property-inbound'] == ['row-e1']

    move_owned_rows_to_page(authority, **_request(
        store, workspace_id, 'inbound-to-ordinary-page', row_ids={'row-e1'}))
    assert authority.read_database('owner', 'database-inbound')['rows'][0][
        'cells']['property-inbound'] == []
    current_page = authority.read_document('owner', 'page-e1')
    with pytest.raises(NotFoundOrDenied):
        authority.execute_typed_command(
            'owner', 'page-e1', command_kind='database.cell.set',
            target_id='row-e1:property-title',
            payload={'row_id': 'row-e1', 'property_id': 'property-title',
                     'value': 'Hidden edit'},
            expected_document_sequence=current_page['document_sequence'],
            expected_workspace_epoch=current_page['workspace_epoch'],
            expected_document_epoch=current_page['document_epoch'],
            expected_target_version=1,
            operation_id='page-placed-row-cannot-edit-cell')
    with pytest.raises(NotFoundOrDenied):
        authority.execute_cell_batch(
            'owner', 'page-e1', database_id='database-e1',
            target_id='database-e1',
            payload={'cells': [{'row_id': 'row-e1',
                'property_id': 'property-date', 'value': '2026-09-16',
                'expected_target_version': 1}]},
            expected_document_sequence=current_page['document_sequence'],
            expected_workspace_epoch=current_page['workspace_epoch'],
            expected_document_epoch=current_page['document_epoch'],
            operation_id='page-placed-row-cannot-batch-edit-cell')
    with pytest.raises(NotFoundOrDenied):
        authority.execute_cell_batch(
            'owner', 'page-e1', database_id='database-inbound',
            target_id='database-inbound',
            payload={'cells': [{'row_id': 'row-inbound',
                'property_id': 'property-inbound', 'value': ['row-e1'],
                'expected_target_version': 1}]},
            expected_document_sequence=current_page['document_sequence'],
            expected_workspace_epoch=current_page['workspace_epoch'],
            expected_document_epoch=current_page['document_epoch'],
            operation_id='inbound-relation-cannot-reselect-page-row')
    with authority._reader() as con:
        assert json.loads(con.execute(
            "SELECT value_json FROM workspace_collaboration_database_cells "
            "WHERE row_id='row-inbound' AND property_id='property-inbound'"
        ).fetchone()[0]) == ['row-e1']
    request = _return_request(store, workspace_id,
                              'inbound-from-ordinary-page')
    receipt = return_page_to_owned_database(authority, **request)
    assert return_page_to_owned_database(authority, **request) == receipt
    assert authority.read_database('owner', 'database-inbound')['rows'][0][
        'cells']['property-inbound'] == ['row-e1']


@pytest.mark.regression
def test_ui_r5_enabling_pair_backfills_existing_relation_atomically(
        placement_environment):
    store, workspace_id = placement_environment
    _cross_destination(store, workspace_id)
    authority = store.iam
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,"
            "row_version,created_at,updated_at) VALUES("
            "'relation-source','database-e1','Related','relation',3,?,1,1,1)",
            (json.dumps({'target_database_id': 'database-e2'}),))
        con.execute(
            "INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,row_version,created_at,updated_at) "
            "VALUES('row-target','database-e2',0,1,1,1)")
        con.execute(
            "INSERT INTO workspace_collaboration_database_cells("
            "row_id,property_id,value_json,row_version,updated_at) "
            "VALUES('row-e1','relation-source',?,1,1)",
            (json.dumps(['row-target']),))
    source_page = authority.read_document('owner', 'page-e1')
    target_page = authority.read_document('owner', 'page-e2')
    with pytest.raises(ValueError, match='atomic pair command'):
        authority.execute_schema_command(
            'owner', 'page-e1', database_id='database-e1',
            command_kind='database.property.create',
            target_id='forged-relation-pair',
            payload={'name': 'Forged', 'type': 'relation',
                     'config': {'target_database_id': 'database-e2',
                                'reciprocal_property_id': 'relation-back'}},
            expected_document_sequence=source_page['document_sequence'],
            expected_workspace_epoch=source_page['workspace_epoch'],
            expected_document_epoch=source_page['document_epoch'],
            expected_target_version=0,
            operation_id='paired-forged-create-guard')
    request = dict(actor_subject_id='owner', workspace_id=workspace_id,
        source_document_id='page-e1', source_database_id='database-e1',
        source_property_id='relation-source', target_document_id='page-e2',
        target_database_id='database-e2',
        inverse_property_id='relation-back', inverse_name='Back',
        expected_source_sequence=source_page['document_sequence'],
        expected_target_sequence=target_page['document_sequence'],
        expected_workspace_epoch=source_page['workspace_epoch'],
        expected_source_epoch=source_page['document_epoch'],
        expected_target_epoch=target_page['document_epoch'],
        expected_source_property_version=1,
        operation_id='paired-enable-existing-link')
    with pytest.raises(InjectedFailure):
        enable_relation_pair(authority, **request, fault='after_domain')
    assert authority.read_document('owner', 'page-e1') == source_page
    assert authority.read_document('owner', 'page-e2') == target_page
    assert not any(item['id'] == 'relation-back' for item in
        authority.read_database('owner', 'database-e2')['properties'])
    enabled = enable_relation_pair(authority, **request)
    assert enable_relation_pair(authority, **request) == enabled
    assert enabled['reciprocal_documents'][0]['document_sequence'] == \
        target_page['document_sequence'] + 1
    target_database = authority.read_database('owner', 'database-e2')
    assert target_database['rows'][0]['cells']['relation-back'] == ['row-e1']
    assert target_database['last_edit']['document_sequence'] == \
        target_page['document_sequence'] + 1
    assert authority.read_database('owner', 'database-e1')['rows'][0][
        'cells']['relation-source'] == ['row-target']

    source_page = authority.read_document('owner', 'page-e1')
    target_page = authority.read_document('owner', 'page-e2')
    with pytest.raises(ValueError, match='atomic pair command'):
        authority.execute_schema_command(
            'owner', 'page-e1', database_id='database-e1',
            command_kind='database.property.update',
            target_id='relation-source',
            payload={'name': 'Related', 'type': 'relation',
                     'config': {'target_database_id': 'database-e2'}},
            expected_document_sequence=source_page['document_sequence'],
            expected_workspace_epoch=source_page['workspace_epoch'],
            expected_document_epoch=source_page['document_epoch'],
            expected_target_version=2,
            operation_id='paired-generic-schema-guard')
    with pytest.raises(ValueError, match='disabled before deletion'):
        authority.execute_surface_command(
            'owner', 'page-e1', database_id='database-e1',
            command_kind='database.property.delete',
            target_id='relation-source', payload={},
            expected_document_sequence=source_page['document_sequence'],
            expected_workspace_epoch=source_page['workspace_epoch'],
            expected_document_epoch=source_page['document_epoch'],
            expected_target_version=2,
            operation_id='paired-generic-delete-guard')
    disable_request = dict(actor_subject_id='owner', workspace_id=workspace_id,
        retained_document_id='page-e1', retained_database_id='database-e1',
        retained_property_id='relation-source',
        expected_retained_sequence=source_page['document_sequence'],
        expected_inverse_sequence=target_page['document_sequence'],
        expected_workspace_epoch=source_page['workspace_epoch'],
        expected_retained_epoch=source_page['document_epoch'],
        expected_inverse_epoch=target_page['document_epoch'],
        expected_retained_property_version=2,
        expected_inverse_property_version=1,
        operation_id='paired-disable-retain-original')
    disabled = disable_relation_pair(authority, **disable_request)
    assert disable_relation_pair(authority, **disable_request) == disabled
    assert not any(item['id'] == 'relation-back' for item in
        authority.read_database('owner', 'database-e2')['properties'])
    source = authority.read_database('owner', 'database-e1')
    assert source['rows'][0]['cells']['relation-source'] == ['row-target']
    assert 'reciprocal_property_id' not in next(item for item in
        source['properties'] if item['id'] == 'relation-source')['config']


@pytest.mark.regression
def test_ui_r5_relation_pair_requires_both_edit_grants_and_fresh_target(
        placement_environment):
    store, workspace_id = placement_environment
    _cross_destination(store, workspace_id)
    authority = store.iam
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,"
            "created_at,updated_at) VALUES("
            "'relation-source','database-e1','Related','relation',3,?,1,1,1)",
            (json.dumps({'target_database_id': 'database-e2'}),))

    def grant(document, operation):
        page = authority.read_document('owner', document)
        authority.set_page_grant(
            'owner', document, 'subject', 'schema-editor', role='edit',
            inherit_to_descendants=False,
            expected_workspace_epoch=page['workspace_epoch'],
            expected_document_epoch=page['document_epoch'],
            operation_id=operation)

    grant('page-e1', 'pair-grant-source-only')
    source = authority.read_document('owner', 'page-e1')
    target = authority.read_document('owner', 'page-e2')
    request = dict(actor_subject_id='schema-editor', workspace_id=workspace_id,
        source_document_id='page-e1', source_database_id='database-e1',
        source_property_id='relation-source', target_document_id='page-e2',
        target_database_id='database-e2', inverse_property_id='relation-back',
        inverse_name='Back', expected_source_sequence=source['document_sequence'],
        expected_target_sequence=target['document_sequence'],
        expected_workspace_epoch=source['workspace_epoch'],
        expected_source_epoch=source['document_epoch'],
        expected_target_epoch=target['document_epoch'],
        expected_source_property_version=1,
        operation_id='pair-denied-target')
    with pytest.raises(NotFoundOrDenied):
        enable_relation_pair(authority, **request)
    assert not any(item['id'] == 'relation-back' for item in
        authority.read_database('owner', 'database-e2')['properties'])

    grant('page-e2', 'pair-grant-target')
    source = authority.read_document('owner', 'page-e1')
    target = authority.read_document('owner', 'page-e2')
    request.update(expected_source_sequence=source['document_sequence'],
        expected_target_sequence=target['document_sequence'],
        expected_workspace_epoch=source['workspace_epoch'],
        expected_source_epoch=source['document_epoch'],
        expected_target_epoch=target['document_epoch'],
        operation_id='pair-stale-target')
    authority.execute_typed_command(
        'owner', 'page-e2', command_kind='block.insert',
        target_id='pair-other-client-block',
        payload={'type': 'paragraph', 'content': 'Another client edit',
                 'after_block_id': 'other-body'},
        expected_document_sequence=target['document_sequence'],
        expected_workspace_epoch=target['workspace_epoch'],
        expected_document_epoch=target['document_epoch'],
        operation_id='pair-other-client-edit')
    with pytest.raises(DocumentSequenceConflict):
        enable_relation_pair(authority, **request)
    assert not any(item['id'] == 'relation-back' for item in
        authority.read_database('owner', 'database-e2')['properties'])

    request.update(expected_target_sequence=authority.read_document(
        'owner', 'page-e2')['document_sequence'],
        expected_source_property_version=2,
        operation_id='pair-stale-property')
    with pytest.raises(TargetVersionConflict):
        enable_relation_pair(authority, **request)
    assert not any(item['id'] == 'relation-back' for item in
        authority.read_database('owner', 'database-e2')['properties'])
    request.update(expected_source_property_version=1,
        operation_id='pair-fresh-after-other-client')
    result = enable_relation_pair(authority, **request)
    assert result['inverse_property_id'] == 'relation-back'


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_relation_pair_two_process_replay_has_one_inverse_property(
        placement_environment):
    store, workspace_id = placement_environment
    _cross_destination(store, workspace_id)
    authority = store.iam
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,"
            "created_at,updated_at) VALUES("
            "'relation-source','database-e1','Related','relation',3,?,1,1,1)",
            (json.dumps({'target_database_id': 'database-e2'}),))
    source = authority.read_document('owner', 'page-e1')
    target = authority.read_document('owner', 'page-e2')
    request = dict(actor_subject_id='owner', workspace_id=workspace_id,
        source_document_id='page-e1', source_database_id='database-e1',
        source_property_id='relation-source', target_document_id='page-e2',
        target_database_id='database-e2', inverse_property_id='relation-back',
        inverse_name='Back', expected_source_sequence=source['document_sequence'],
        expected_target_sequence=target['document_sequence'],
        expected_workspace_epoch=source['workspace_epoch'],
        expected_source_epoch=source['document_epoch'],
        expected_target_epoch=target['document_epoch'],
        expected_source_property_version=1,
        operation_id='pair-two-process-replay')
    args = (str(store.db_path), str(store.user_data_root), request)
    with ProcessPoolExecutor(max_workers=2,
            mp_context=multiprocessing.get_context('spawn')) as pool:
        results = list(pool.map(_relation_pair_enable_worker, (args, args)))
    assert results[0] == results[1]
    inverse = [item for item in authority.read_database(
        'owner', 'database-e2')['properties'] if item['id'] == 'relation-back']
    assert len(inverse) == 1
    assert authority.read_document('owner', 'page-e1')[
        'document_sequence'] == source['document_sequence'] + 1
    assert authority.read_document('owner', 'page-e2')[
        'document_sequence'] == target['document_sequence'] + 1
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_operations "
            "WHERE action=? AND operation_id=?",
            ('collaboration.database.relation_pair.enable',
             request['operation_id'])).fetchone()[0] == 1


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('delayed_operation', ('enable', 'revoke'))
def test_ui_r5_relation_pair_enable_and_target_grant_revoke_serialize(
        placement_environment, delayed_operation):
    store, workspace_id = placement_environment
    _cross_destination(store, workspace_id)
    authority = store.iam
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,"
            "created_at,updated_at) VALUES("
            "'relation-source','database-e1','Related','relation',3,?,1,1,1)",
            (json.dumps({'target_database_id': 'database-e2'}),))
    for document in ('page-e1', 'page-e2'):
        page = authority.read_document('owner', document)
        authority.set_page_grant(
            'owner', document, 'subject', 'schema-editor', role='edit',
            inherit_to_descendants=False,
            expected_workspace_epoch=page['workspace_epoch'],
            expected_document_epoch=page['document_epoch'],
            operation_id='pair-race-grant-' + document)
    source = authority.read_document('owner', 'page-e1')
    target = authority.read_document('owner', 'page-e2')
    enable_request = dict(actor_subject_id='schema-editor', workspace_id=workspace_id,
        source_document_id='page-e1', source_database_id='database-e1',
        source_property_id='relation-source', target_document_id='page-e2',
        target_database_id='database-e2', inverse_property_id='relation-back',
        inverse_name='Back', expected_source_sequence=source['document_sequence'],
        expected_target_sequence=target['document_sequence'],
        expected_workspace_epoch=source['workspace_epoch'],
        expected_source_epoch=source['document_epoch'],
        expected_target_epoch=target['document_epoch'],
        expected_source_property_version=1, operation_id='pair-race-enable')
    revoke_request = dict(expected_workspace_epoch=target['workspace_epoch'],
        expected_document_epoch=target['document_epoch'],
        operation_id='pair-race-revoke-target')
    with multiprocessing.Manager() as manager:
        barrier = manager.Barrier(2)
        with ProcessPoolExecutor(max_workers=2,
                mp_context=multiprocessing.get_context('spawn')) as pool:
            outcomes = dict(pool.map(_relation_pair_permission_race_worker, (
                (str(store.db_path), str(store.user_data_root), barrier,
                 'enable', enable_request,
                 1.0 if delayed_operation == 'enable' else 0.0),
                (str(store.db_path), str(store.user_data_root), barrier,
                 'revoke', revoke_request,
                 1.0 if delayed_operation == 'revoke' else 0.0))))
    assert outcomes['revoke'] == 'committed', outcomes
    if delayed_operation == 'revoke':
        assert outcomes['enable'] == 'committed', outcomes
    else:
        assert outcomes['enable'] in {'NotFoundOrDenied', 'EpochConflict'}, outcomes
    with pytest.raises(NotFoundOrDenied):
        authority.read_database('schema-editor', 'database-e2')
    source_relation = next(item for item in authority.read_database(
        'owner', 'database-e1')['properties'] if item['id'] == 'relation-source')
    inverse = [item for item in authority.read_database(
        'owner', 'database-e2')['properties'] if item['id'] == 'relation-back']
    assert (len(inverse) == 1) == (outcomes['enable'] == 'committed')
    assert ('reciprocal_property_id' in source_relation['config']) == bool(inverse)
    with authority._reader() as con:
        receipt_count = con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_operations "
            "WHERE action='collaboration.database.relation_pair.enable' "
            "AND operation_id='pair-race-enable'").fetchone()[0]
        assert receipt_count == len(inverse)


@pytest.mark.regression
def test_ui_r5_relation_pair_http_gate_enable_and_disable(
        placement_environment, monkeypatch):
    store, workspace_id = placement_environment
    _cross_destination(store, workspace_id)
    authority = store.iam
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,"
            "row_version,created_at,updated_at) VALUES("
            "'relation-source','database-e1','Related','relation',3,?,1,1,1)",
            (json.dumps({'target_database_id': 'database-e2'}),))
        con.execute(
            "INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,row_version,created_at,updated_at) "
            "VALUES('row-target','database-e2',0,1,1,1)")
    client, _actor = _client(store, monkeypatch, enabled=False)
    path = (f'/api/notion/workspaces/{workspace_id}/pages/page-e1/'
            'databases/commands')
    source_page = authority.read_document('owner', 'page-e1')
    target_page = authority.read_document('owner', 'page-e2')
    enable_body = {
        'database_id': 'database-e1',
        'command_kind': 'database.relation_pair.enable',
        'target_id': 'relation-source',
        'payload': {'target_document_id': 'page-e2',
                    'target_database_id': 'database-e2',
                    'inverse_property_id': 'relation-back',
                    'inverse_name': 'Back',
                    'expected_target_sequence': target_page['document_sequence'],
                    'expected_target_epoch': target_page['document_epoch']},
        'expected_document_sequence': source_page['document_sequence'],
        'expected_workspace_epoch': source_page['workspace_epoch'],
        'expected_document_epoch': source_page['document_epoch'],
        'expected_target_version': 1,
        'operation_id': 'relation-pair-http-enable',
    }
    assert client.post(path, json=enable_body).status_code == 503
    original_capabilities = store.capabilities
    monkeypatch.setattr(store, 'capabilities', lambda: {
        **original_capabilities(), 'collaborative_database_relation_pair': True})
    enabled = client.post(path, json=enable_body)
    assert enabled.status_code == 201, enabled.json
    assert client.post(path, json=enable_body).json == enabled.json
    assert authority.read_database('owner', 'database-e2')['properties'][-1][
        'id'] == 'relation-back'

    source_page = authority.read_document('owner', 'page-e1')
    target_page = authority.read_document('owner', 'page-e2')
    disable_body = {
        'database_id': 'database-e1',
        'command_kind': 'database.relation_pair.disable',
        'target_id': 'relation-source',
        'payload': {'expected_inverse_sequence': target_page['document_sequence'],
                    'expected_inverse_epoch': target_page['document_epoch'],
                    'expected_inverse_property_version': 1},
        'expected_document_sequence': source_page['document_sequence'],
        'expected_workspace_epoch': source_page['workspace_epoch'],
        'expected_document_epoch': source_page['document_epoch'],
        'expected_target_version': 2,
        'operation_id': 'relation-pair-http-disable',
    }
    disabled = client.post(path, json=disable_body)
    assert disabled.status_code == 201, disabled.json
    assert client.post(path, json=disable_body).json == disabled.json
    assert not any(item['id'] == 'relation-back' for item in
        authority.read_database('owner', 'database-e2')['properties'])


@pytest.mark.regression
def test_ui_r5_same_document_paired_relation_advances_once(
        placement_environment):
    store, workspace_id = placement_environment
    _destination(store)
    authority = store.iam
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,"
            "row_version,created_at,updated_at) VALUES("
            "'relation-source','database-e1','Related','relation',3,?,1,1,1)",
            (json.dumps({'target_database_id': 'database-e2'}),))
        con.execute(
            "INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,row_version,created_at,updated_at) "
            "VALUES('row-target','database-e2',0,1,1,1)")
    page = authority.read_document('owner', 'page-e1')
    enabled = enable_relation_pair(
        authority, 'owner', workspace_id, 'page-e1', 'database-e1',
        'relation-source', 'page-e1', 'database-e2', 'relation-back', 'Back',
        expected_source_sequence=page['document_sequence'],
        expected_target_sequence=page['document_sequence'],
        expected_workspace_epoch=page['workspace_epoch'],
        expected_source_epoch=page['document_epoch'],
        expected_target_epoch=page['document_epoch'],
        expected_source_property_version=1,
        operation_id='same-document-pair-enable')
    assert enabled['reciprocal_documents'] == []
    assert authority.read_document('owner', 'page-e1')[
        'document_sequence'] == page['document_sequence'] + 1
    page = authority.read_document('owner', 'page-e1')
    changed = authority.execute_typed_command(
        'owner', 'page-e1', command_kind='database.cell.set',
        target_id='row-e1:relation-source',
        payload={'row_id': 'row-e1', 'property_id': 'relation-source',
                 'value': ['row-target']},
        expected_document_sequence=page['document_sequence'],
        expected_workspace_epoch=page['workspace_epoch'],
        expected_document_epoch=page['document_epoch'],
        expected_target_version=0,
        operation_id='same-document-pair-cell')
    assert 'reciprocal_documents' not in changed
    assert authority.read_document('owner', 'page-e1')[
        'document_sequence'] == page['document_sequence'] + 1
    target_database = authority.read_database('owner', 'database-e2')
    assert target_database['rows'][0]['cells']['relation-back'] == ['row-e1']
    assert target_database['last_edit']['document_sequence'] == \
        page['document_sequence'] + 1


@pytest.mark.regression
def test_ui_r5_paired_relation_edits_update_both_documents_atomically(
        placement_environment):
    store, workspace_id = placement_environment
    _cross_destination(store, workspace_id)
    authority = store.iam
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,"
            "row_version,created_at,updated_at) VALUES("
            "'relation-source','database-e1','Related','relation',3,?,1,1,1)",
            (json.dumps({'target_database_id': 'database-e2',
                         'reciprocal_property_id': 'relation-back'}),))
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,"
            "row_version,created_at,updated_at) VALUES("
            "'relation-back','database-e2','Back','relation',1,?,1,1,1)",
            (json.dumps({'target_database_id': 'database-e1',
                         'reciprocal_property_id': 'relation-source'}),))
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,"
            "row_version,created_at,updated_at) VALUES("
            "'property-title-e2','database-e2','Name','title',2,'{}',1,1,1)")
        con.execute(
            "INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,row_version,created_at,updated_at) "
            "VALUES('row-target','database-e2',0,1,1,1)")

    source_page = authority.read_document('owner', 'page-e1')
    target_page = authority.read_document('owner', 'page-e2')
    request = dict(command_kind='database.cell.set',
        target_id='row-e1:relation-source',
        payload={'row_id': 'row-e1', 'property_id': 'relation-source',
                 'value': ['row-target']},
        expected_document_sequence=source_page['document_sequence'],
        expected_workspace_epoch=source_page['workspace_epoch'],
        expected_document_epoch=source_page['document_epoch'],
        expected_target_version=0, operation_id='paired-source-connect')
    connected = authority.execute_typed_command('owner', 'page-e1', **request)
    assert authority.execute_typed_command('owner', 'page-e1', **request) == connected
    assert connected['reciprocal_documents'][0]['document_sequence'] == \
        target_page['document_sequence'] + 1
    assert authority.read_database('owner', 'database-e1')['rows'][0][
        'cells']['relation-source'] == ['row-target']
    assert authority.read_database('owner', 'database-e2')['rows'][0][
        'cells']['relation-back'] == ['row-e1']

    target_page = authority.read_document('owner', 'page-e2')
    disconnected = authority.execute_typed_command(
        'owner', 'page-e2', command_kind='database.cell.set',
        target_id='row-target:relation-back',
        payload={'row_id': 'row-target', 'property_id': 'relation-back',
                 'value': []},
        expected_document_sequence=target_page['document_sequence'],
        expected_workspace_epoch=target_page['workspace_epoch'],
        expected_document_epoch=target_page['document_epoch'],
        expected_target_version=1, operation_id='paired-target-disconnect')
    assert disconnected['reciprocal_documents'][0]['document_id'] == 'page-e1'
    assert authority.read_database('owner', 'database-e1')['rows'][0][
        'cells']['relation-source'] == []
    assert authority.read_database('owner', 'database-e2')['rows'][0][
        'cells']['relation-back'] == []

    target_page = authority.read_document('owner', 'page-e2')
    reconnected = authority.execute_cell_batch(
        'owner', 'page-e2', database_id='database-e2',
        target_id='database-e2',
        payload={'cells': [{'row_id': 'row-target',
            'property_id': 'relation-back', 'value': ['row-e1'],
            'expected_target_version': 2}]},
        expected_document_sequence=target_page['document_sequence'],
        expected_workspace_epoch=target_page['workspace_epoch'],
        expected_document_epoch=target_page['document_epoch'],
        operation_id='paired-target-batch-reconnect')
    assert reconnected['reciprocal_documents'][0]['document_id'] == 'page-e1'
    assert authority.read_database('owner', 'database-e1')['rows'][0][
        'cells']['relation-source'] == ['row-target']
    source_page = authority.read_document('owner', 'page-e1')
    target_page = authority.read_document('owner', 'page-e2')
    with pytest.raises(InjectedFailure):
        authority.execute_typed_command(
            'owner', 'page-e1', command_kind='database.cell.set',
            target_id='row-e1:relation-source',
            payload={'row_id': 'row-e1', 'property_id': 'relation-source',
                     'value': []},
            expected_document_sequence=source_page['document_sequence'],
            expected_workspace_epoch=source_page['workspace_epoch'],
            expected_document_epoch=source_page['document_epoch'],
            expected_target_version=3, operation_id='paired-rollback',
            fault='after_domain')
    assert authority.read_document('owner', 'page-e1') == source_page
    assert authority.read_document('owner', 'page-e2') == target_page
    assert authority.read_database('owner', 'database-e2')['rows'][0][
        'cells']['relation-back'] == ['row-e1']
    move_owned_rows_to_page(authority, **_request(
        store, workspace_id, 'paired-to-ordinary-page',
        row_ids={'row-e1'}))
    assert authority.read_database('owner', 'database-e2')['rows'][0][
        'cells']['relation-back'] == []
    return_page_to_owned_database(authority, **_return_request(
        store, workspace_id, 'paired-return-from-ordinary-page'))
    assert authority.read_database('owner', 'database-e2')['rows'][0][
        'cells']['relation-back'] == ['row-e1']
    source_page = authority.read_document('owner', 'page-e1')
    target_page = authority.read_document('owner', 'page-e2')
    source_row = next(item for item in authority.read_database(
        'owner', 'database-e1')['rows'] if item['id'] == 'row-e1')
    move_owned_rows(
        authority, actor_subject_id='owner', workspace_id=workspace_id,
        source_document_id='page-e1', source_database_id='database-e1',
        destination_document_id='page-e2', destination_database_id='database-e2',
        rows=[{'row_id': 'row-e1',
               'expected_target_version': source_row['version']}],
        expected_source_sequence=source_page['document_sequence'],
        expected_destination_sequence=target_page['document_sequence'],
        expected_workspace_epoch=source_page['workspace_epoch'],
        expected_source_epoch=source_page['document_epoch'],
        expected_destination_epoch=target_page['document_epoch'],
        operation_id='paired-to-other-database')
    assert authority.read_database('owner', 'database-e2')['rows'][0][
        'cells']['relation-back'] == []
    source_page = authority.read_document('owner', 'page-e1')
    target_page = authority.read_document('owner', 'page-e2')
    moved = next(item for item in authority.read_database(
        'owner', 'database-e2')['rows'] if item['id'] == 'row-e1')
    move_owned_rows(
        authority, actor_subject_id='owner', workspace_id=workspace_id,
        source_document_id='page-e2', source_database_id='database-e2',
        destination_document_id='page-e1', destination_database_id='database-e1',
        rows=[{'row_id': 'row-e1',
               'expected_target_version': moved['version']}],
        expected_source_sequence=target_page['document_sequence'],
        expected_destination_sequence=source_page['document_sequence'],
        expected_workspace_epoch=source_page['workspace_epoch'],
        expected_source_epoch=target_page['document_epoch'],
        expected_destination_epoch=source_page['document_epoch'],
        operation_id='paired-return-from-other-database')
    assert next(item for item in authority.read_database(
        'owner', 'database-e1')['rows'] if item['id'] == 'row-e1')[
            'cells']['relation-source'] == ['row-target']
    assert authority.read_database('owner', 'database-e2')['rows'][0][
        'cells']['relation-back'] == ['row-e1']


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_separate_processes_cannot_commit_stale_opposite_pair_edits(
        placement_environment):
    store, workspace_id = placement_environment
    _cross_destination(store, workspace_id)
    authority = store.iam
    with authority._transaction() as con:
        for property_id, database_id, name, position, target_id, inverse_id in (
                ('relation-source', 'database-e1', 'Related', 3,
                 'database-e2', 'relation-back'),
                ('relation-back', 'database-e2', 'Back', 1,
                 'database-e1', 'relation-source')):
            con.execute(
                "INSERT INTO workspace_collaboration_database_properties("
                "id,database_id,name,property_type,position,config_json,"
                "row_version,created_at,updated_at) VALUES("
                "?,?,?,'relation',?,?,1,1,1)",
                (property_id, database_id, name, position,
                 json.dumps({'target_database_id': target_id,
                             'reciprocal_property_id': inverse_id})))
        con.execute(
            "INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,row_version,created_at,updated_at) "
            "VALUES('row-target','database-e2',0,1,1,1)")
    source_page = authority.read_document('owner', 'page-e1')
    target_page = authority.read_document('owner', 'page-e2')
    source_request = dict(command_kind='database.cell.set',
        target_id='row-e1:relation-source',
        payload={'row_id': 'row-e1', 'property_id': 'relation-source',
                 'value': ['row-target']},
        expected_document_sequence=source_page['document_sequence'],
        expected_workspace_epoch=source_page['workspace_epoch'],
        expected_document_epoch=source_page['document_epoch'],
        expected_target_version=0, operation_id='opposite-source-edit')
    target_request = dict(command_kind='database.cell.set',
        target_id='row-target:relation-back',
        payload={'row_id': 'row-target', 'property_id': 'relation-back',
                 'value': ['row-e1']},
        expected_document_sequence=target_page['document_sequence'],
        expected_workspace_epoch=target_page['workspace_epoch'],
        expected_document_epoch=target_page['document_epoch'],
        expected_target_version=0, operation_id='opposite-target-edit')
    with ProcessPoolExecutor(
            max_workers=2,
            mp_context=multiprocessing.get_context('spawn')) as pool:
        outcomes = list(pool.map(_relation_pair_cell_worker, (
            (store.db_path, store.user_data_root, 'page-e1', source_request),
            (store.db_path, store.user_data_root, 'page-e2', target_request))))
    assert sorted(item[0] for item in outcomes) in (
        ['DocumentSequenceConflict', 'committed'],
        ['TargetVersionConflict', 'committed'])
    assert authority.read_database('owner', 'database-e1')['rows'][0][
        'cells']['relation-source'] == ['row-target']
    assert authority.read_database('owner', 'database-e2')['rows'][0][
        'cells']['relation-back'] == ['row-e1']
    assert authority.read_document('owner', 'page-e1')[
        'document_sequence'] == source_page['document_sequence'] + 1
    assert authority.read_document('owner', 'page-e2')[
        'document_sequence'] == target_page['document_sequence'] + 1
    with authority._reader() as con:
        receipts = con.execute(
            "SELECT action,operation_id FROM workspace_collaboration_operations "
            "WHERE operation_id IN ('opposite-source-edit','opposite-target-edit')"
        ).fetchall()
        assert len(receipts) == 1, [tuple(item) for item in receipts]


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_relation_survives_ordinary_page_other_database_and_return(
        placement_environment):
    store, workspace_id = placement_environment
    authority = store.iam
    _cross_destination(store, workspace_id)
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,"
            "created_at,updated_at) VALUES("
            "'property-title-e2','database-e2','Name','title',1,'{}',1,1,1)")
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,"
            "created_at,updated_at) VALUES("
            "'property-relation','database-e1','Related','relation',3,?,1,1,1)",
            (json.dumps({'target_database_id': 'database-e2'}),))
        con.execute(
            "INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,row_version,created_at,updated_at) "
            "VALUES('row-target','database-e2',0,1,1,1)")
        con.execute(
            "INSERT INTO workspace_collaboration_database_cells("
            "row_id,property_id,value_json,row_version,updated_at) "
            "VALUES('row-e1','property-relation',?,1,1)",
            (json.dumps(['row-target']),))

    move_owned_rows_to_page(authority, **_request(
        store, workspace_id, 'relation-to-ordinary-page',
        row_ids={'row-e1'}))
    target = read_page_return_target(
        authority, 'owner', workspace_id, 'row-e1',
        'page-e2', 'database-e2')
    assert target['destination_database_id'] == 'database-e2'
    request = _return_request(store, workspace_id,
                              'relation-page-to-other-db')
    request.update(
        destination_document_id='page-e2',
        destination_database_id='database-e2',
        expected_destination_sequence=target['expected_destination_sequence'],
        expected_destination_epoch=target['expected_destination_epoch'])
    return_page_to_owned_database(authority, **request)
    target_rows = authority.read_database('owner', 'database-e2')['rows']
    moved = next(row for row in target_rows if row['id'] == 'row-e1')
    assert 'property-relation' not in moved['cells']

    source_page = authority.read_document('owner', 'page-e2')
    destination_page = authority.read_document('owner', 'page-e1')
    move_owned_rows(authority,
        actor_subject_id='owner', workspace_id=workspace_id,
        source_document_id='page-e2', source_database_id='database-e2',
        destination_document_id='page-e1', destination_database_id='database-e1',
        rows=[{'row_id': 'row-e1', 'expected_target_version': moved['version']}],
        expected_source_sequence=source_page['document_sequence'],
        expected_destination_sequence=destination_page['document_sequence'],
        expected_workspace_epoch=source_page['workspace_epoch'],
        expected_source_epoch=source_page['document_epoch'],
        expected_destination_epoch=destination_page['document_epoch'],
        operation_id='relation-back-from-other-db')
    restored = next(row for row in authority.read_database(
        'owner', 'database-e1')['rows'] if row['id'] == 'row-e1')
    assert restored['cells']['property-relation'] == ['row-target']
    assert restored['cells']['property-date'] == '2026-09-15'
    with authority._reader() as con:
        assert not con.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_row_page_moves_to_other_database_then_back_with_date(
        placement_environment, monkeypatch):
    store, workspace_id = placement_environment
    authority = store.iam
    _cross_destination(store, workspace_id)
    with authority._transaction() as con:
        for property_id, name, kind, position in (
                ("property-title-e2", "Name", "title", 1),
                ("property-date-text-e2", "날짜", "text", 2)):
            con.execute(
                "INSERT INTO workspace_collaboration_database_properties("
                "id,database_id,name,property_type,position,config_json,"
                "row_version,created_at,updated_at) VALUES(?, 'database-e2',"
                "?,?,?,'{}',1,1,1)",
                (property_id, name, kind, position),
            )
    file_id, png = _upload_png(store, "other-db-hidden-file")
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,"
            "row_version,created_at,updated_at) VALUES("
            "'property-files','database-e1','Attachment','files',3,'{}',1,1,1)")
        con.execute(
            "INSERT INTO workspace_collaboration_database_cells("
            "row_id,property_id,value_json,row_version,updated_at) "
            "VALUES('row-e1','property-files',?,1,1)",
            ('[{"file_id":"' + file_id + '"}]',),
        )
    _surface(authority, "database.row.content.set", "row-e1",
             {"blocks": [{"id": "other-db-body", "type": "paragraph",
                          "content": "Keep this body", "props": {}}]},
             "seed-other-db-body", actor="owner", target_version=1)
    move_owned_rows_to_page(authority, **_request(
        store, workspace_id, "other-db-row-to-page", parent_id="page-e2",
        row_ids={"row-e1"}))
    parent_for_grant = authority.read_document("owner", "page-e2")
    authority.set_page_grant(
        "owner", "page-e2", "subject", "content-editor",
        role="edit_content", inherit_to_descendants=True,
        expected_workspace_epoch=parent_for_grant["workspace_epoch"],
        expected_document_epoch=parent_for_grant["document_epoch"],
        operation_id="other-db-content-editor-grant")
    authority.read_document("content-editor", "row-e1")
    authority.read_document("content-editor", "page-e2")
    target = read_page_return_target(
        authority, "owner", workspace_id, "row-e1", "page-e2", "database-e2")
    assert target["destination_database_id"] == "database-e2"
    assert target["destination_document_id"] == "page-e2"
    choices = list_page_database_destinations(
        authority, "owner", workspace_id, "row-e1")
    assert choices["items"] == [{"document_id": "page-e2",
                                 "database_id": "database-e2",
                                 "title": "Destination"}]
    assert list_page_database_destinations(
        authority, "content-editor", workspace_id, "row-e1")["items"] == []
    with pytest.raises(NotFoundOrDenied):
        read_page_return_target(authority, "content-editor", workspace_id,
                                "row-e1", "page-e2", "database-e2")
    client, _ = _client(store, monkeypatch, enabled=False)
    capabilities = store.capabilities
    monkeypatch.setattr(store, "capabilities", lambda: {
        **capabilities(), "collaborative_page_to_database_row": True})
    http_target = client.get(
        f"/api/notion/workspaces/{workspace_id}/pages/row-e1/"
        "return-to-database-target?destination_document_id=page-e2"
        "&destination_database_id=database-e2")
    assert http_target.status_code == 200, http_target.json
    assert http_target.json == target
    choices_response = client.get(
        f"/api/notion/workspaces/{workspace_id}/pages/row-e1/"
        "database-destinations?q=Destination")
    assert choices_response.status_code == 200, choices_response.json
    assert choices_response.json["items"] == choices["items"]
    request = _return_request(store, workspace_id,
                              "ordinary-page-to-other-database")
    destination_page = authority.read_document("owner", "page-e2")
    request.update(destination_document_id="page-e2",
                   destination_database_id="database-e2",
                   expected_destination_sequence=destination_page[
                       "document_sequence"],
                   expected_destination_epoch=destination_page["document_epoch"])
    before = authority.read_database("owner", "database-e1")
    with pytest.raises(NotFoundOrDenied):
        return_page_to_owned_database(authority, **{**request,
            "actor_subject_id": "content-editor",
            "operation_id": "ordinary-page-to-other-db-no-schema-role"})
    assert authority.read_database("owner", "database-e1") == before
    assert authority.read_database("owner", "database-e2")["rows"] == []
    with pytest.raises(InjectedFailure):
        return_page_to_owned_database(authority, **request, fault="after_domain")
    assert authority.read_database("owner", "database-e1") == before
    assert authority.read_database("owner", "database-e2")["rows"] == []

    result = return_page_to_owned_database(authority, **request)
    assert return_page_to_owned_database(authority, **request) == result
    assert result["source_database_id"] == "database-e1"
    assert result["destination_database_id"] == "database-e2"
    with authority._reader() as con:
        assert con.execute(
            "SELECT document_id FROM workspace_collaboration_events WHERE id=?",
            (result["source_event_id"],)).fetchone()[0] == "page-e1"
    assert {(item["name"], item["type"]) for item in
            result["created_properties"]} == {("날짜 1", "date"),
                                                ("Attachment", "files")}
    date_id = next(item["id"] for item in result["created_properties"]
                   if item["name"] == "날짜 1")
    files_id = next(item["id"] for item in result["created_properties"]
                    if item["name"] == "Attachment")
    row = authority.read_database("owner", "database-e2")["rows"][0]
    assert row["id"] == "row-e1"
    assert row["cells"]["property-title-e2"] == "페이지로 이동 A"
    assert row["cells"][date_id] == "2026-09-15"
    copied_file = row["cells"][files_id][0]["file_id"]
    assert copied_file != file_id
    assert authority.get_file("owner", copied_file)["path"].read_bytes() == png
    assert "property-date-text-e2" not in row["cells"]
    assert row["body"]["blocks"][0]["content"] == "Keep this body"
    assert read_owned_row_location(authority, "owner", workspace_id,
                                   "row-e1")["database_id"] == "database-e2"

    source_page = authority.read_document("owner", "page-e2")
    destination_page = authority.read_document("owner", "page-e1")
    move_owned_rows(
        authority, "owner", workspace_id, "page-e2", "database-e2",
        "page-e1", "database-e1",
        rows=[{"row_id": row["id"], "expected_target_version": row["version"]}],
        expected_source_sequence=source_page["document_sequence"],
        expected_destination_sequence=destination_page["document_sequence"],
        expected_workspace_epoch=source_page["workspace_epoch"],
        expected_source_epoch=source_page["document_epoch"],
        expected_destination_epoch=destination_page["document_epoch"],
        operation_id="other-database-back-to-original")
    original = next(item for item in authority.read_database(
        "owner", "database-e1")["rows"] if item["id"] == "row-e1")
    assert original["cells"]["property-date"] == "2026-09-15"
    assert authority.get_file("owner", original["cells"]["property-files"][0][
        "file_id"])["path"].read_bytes() == png
    move_owned_rows_to_page(authority, **_request(
        store, workspace_id, "other-db-row-to-page-again", parent_id="page-e2",
        row_ids={"row-e1"}))
    content_target = read_page_return_target(
        authority, "content-editor", workspace_id, "row-e1",
        "page-e2", "database-e2")
    assert {item["database_id"] for item in list_page_database_destinations(
        authority, "content-editor", workspace_id, "row-e1")["items"]} == {
            "database-e2"}
    content_request = _return_request(
        store, workspace_id, "ordinary-page-to-other-db-no-new-columns")
    content_request.update(
        actor_subject_id="content-editor",
        destination_document_id="page-e2",
        destination_database_id="database-e2",
        expected_destination_sequence=content_target[
            "expected_destination_sequence"],
        expected_destination_epoch=content_target["expected_destination_epoch"])
    content_result = return_page_to_owned_database(authority, **content_request)
    assert content_result["created_properties"] == []
    assert next(item for item in authority.read_database(
        "owner", "database-e2")["rows"] if item["id"] == "row-e1")[
            "cells"][date_id] == "2026-09-15"
    with authority._reader() as con:
        assert not con.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_page_returns_to_other_database_in_same_source_document(
        placement_environment):
    store, workspace_id = placement_environment
    authority = store.iam
    source = authority.read_document("owner", "page-e1")
    authority.create_document(
        "owner", workspace_id, "page-e2", title="Ordinary parent", blocks=[],
        expected_workspace_epoch=source["workspace_epoch"],
        operation_id="same-document-other-db-parent")
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_databases("
            "id,document_id,title,row_version,created_at,updated_at) "
            "VALUES('database-e3','page-e1','Same document target',1,1,1)")
        for property_id, name, kind, position in (
                ("property-title-e3", "Name", "title", 0),
                ("property-date-text-e3", "날짜", "text", 1)):
            con.execute(
                "INSERT INTO workspace_collaboration_database_properties("
                "id,database_id,name,property_type,position,config_json,"
                "row_version,created_at,updated_at) "
                "VALUES(?,'database-e3',?,?,?,'{}',1,1,1)",
                (property_id, name, kind, position))
    source = authority.read_document("owner", "page-e1")
    authority.execute_typed_command(
        "owner", "page-e1", command_kind="block.insert",
        target_id="same-document-other-db-block",
        payload={"type": "table", "content": "Same document target",
                 "props": {"database_id": "database-e3"}},
        expected_document_sequence=source["document_sequence"],
        expected_workspace_epoch=source["workspace_epoch"],
        expected_document_epoch=source["document_epoch"],
        operation_id="same-document-other-db-block")
    move_owned_rows_to_page(authority, **_request(
        store, workspace_id, "same-document-row-to-page", parent_id="page-e2",
        row_ids={"row-e1"}))
    target = read_page_return_target(
        authority, "owner", workspace_id, "row-e1", "page-e1", "database-e3")
    request = _return_request(
        store, workspace_id, "same-document-page-to-other-db")
    request["destination_database_id"] = "database-e3"
    result = return_page_to_owned_database(authority, **request)
    assert result["source_document_id"] == result["destination_document_id"]
    assert result["source_document_sequence"] == result["document_sequence"]
    assert "source_event_id" not in result
    assert result["document_sequence"] > target["expected_destination_sequence"]
    assert {(item["name"], item["type"]) for item in
            result["created_properties"]} == {("날짜 1", "date"),
                                                ("Status", "text")}
    date_id = next(item["id"] for item in result["created_properties"]
                   if item["name"] == "날짜 1")
    row = authority.read_database("owner", "database-e3")["rows"][0]
    assert row["id"] == "row-e1"
    assert row["cells"][date_id] == "2026-09-15"
    assert [item["id"] for item in authority.read_database(
        "owner", "database-e1")["rows"]] == ["row-e2"]
    with authority._reader() as con:
        assert not con.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_page_return_two_process_replay_has_one_effect(
        placement_environment):
    store, workspace_id = placement_environment
    source = store.iam.read_document("owner", "page-e1")
    store.iam.create_document(
        "owner", workspace_id, "page-e2", title="Return parent", blocks=[],
        expected_workspace_epoch=source["workspace_epoch"],
        operation_id="two-process-return-parent")
    move_owned_rows_to_page(store.iam, **_request(
        store, workspace_id, "two-process-row-to-page", parent_id="page-e2",
        row_ids={"row-e1"}))
    request = _return_request(
        store, workspace_id, "two-process-page-to-database")
    worker_args = (str(store.db_path), str(store.user_data_root), request)
    with ProcessPoolExecutor(
            max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(_page_return_worker, (worker_args, worker_args)))
    assert results[0] == results[1]
    assert results[0]["row_id"] == "row-e1"
    assert [item["id"] for item in store.iam.read_database(
        "owner", "database-e1")["rows"]] == ["row-e1", "row-e2"]
    assert store.iam.read_document("owner", "page-e2")["blocks"] == []
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_operations "
            "WHERE action=? AND operation_id=?",
            (RETURN_ACTION, request["operation_id"])).fetchone()[0] == 1
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_page_return_http_replays_across_two_server_processes(
        placement_environment):
    store, workspace_id = placement_environment
    source = store.iam.read_document("owner", "page-e1")
    store.iam.create_document(
        "owner", workspace_id, "page-e2", title="HTTP return parent", blocks=[],
        expected_workspace_epoch=source["workspace_epoch"],
        operation_id="two-http-return-parent")
    move_owned_rows_to_page(store.iam, **_request(
        store, workspace_id, "two-http-row-to-page", parent_id="page-e2",
        row_ids={"row-e1"}))
    request = _return_request(
        store, workspace_id, "two-http-page-to-database")
    body = {key: value for key, value in request.items() if key not in {
        "actor_subject_id", "workspace_id", "page_document_id"}}
    first_port = _available_local_port()
    second_port = _available_local_port()
    while second_port == first_port:
        second_port = _available_local_port()
    ports = (first_port, second_port)
    context = multiprocessing.get_context("spawn")
    workers = [context.Process(
        target=_page_return_http_worker,
        args=(str(store.db_path), str(store.user_data_root), port))
        for port in ports]
    for worker in workers:
        worker.start()
    try:
        paths = [f"http://127.0.0.1:{port}/api/notion/workspaces/"
                 f"{workspace_id}/pages/row-e1/return-to-database"
                 for port in ports]
        deadline = time.monotonic() + 40
        for path in paths:
            target_url = path + "-target"
            while True:
                if any(worker.exitcode is not None for worker in workers):
                    raise RuntimeError("isolated page-return HTTP server stopped")
                try:
                    status, target = _page_return_http_json(target_url)
                    assert status == 200
                    assert target["row_id"] == "row-e1"
                    break
                except (urllib.error.URLError, TimeoutError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("isolated page-return HTTP server timed out")
                    time.sleep(0.2)
        first_status, first = _page_return_http_json(paths[0], body)
        second_status, second = _page_return_http_json(paths[1], body)
        assert first_status == second_status == 200
        assert first == second
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=10)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=10)
    assert [item["id"] for item in store.iam.read_database(
        "owner", "database-e1")["rows"]] == ["row-e1", "row-e2"]
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_operations "
            "WHERE action=? AND operation_id=?",
            (RETURN_ACTION, request["operation_id"])).fetchone()[0] == 1
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_same_row_page_identity_survives_two_complete_roundtrips(
        placement_environment, yjs):
    store, workspace_id = placement_environment
    authority = store.iam
    _surface(authority, "database.row.content.set", "row-e1",
             {"blocks": [{"id": "first-row-body", "type": "paragraph",
                          "content": "First body", "props": {}}]},
             "seed-first-row-body", actor="owner", target_version=1)
    first = move_owned_rows_to_page(authority, **_request(
        store, workspace_id, "first-row-placement", row_ids={"row-e1"}))
    assert first["rows"][0]["page_id"] == "row-e1"
    transport = CollaborativeEditingTransportAuthority.open(
        store.db_path, store.user_data_root)
    append_live_title(authority, transport, workspace_id, [yjs["base"]],
                      document="row-e1")
    return_page_to_owned_database(authority, **_return_request(
        store, workspace_id, "first-row-return"))
    row = authority.read_database("owner", "database-e1")["rows"][0]
    assert row["cells"]["property-title"] == "Collaborative editing"
    _surface(authority, "database.row.content.set", "row-e1",
             {"blocks": [{"id": "second-row-body", "type": "paragraph",
                          "content": "Second body", "props": {}}]},
             "seed-second-row-body", actor="owner", target_version=row["version"])
    row = authority.read_database("owner", "database-e1")["rows"][0]
    source = authority.read_document("owner", "page-e1")
    authority.execute_typed_command(
        "owner", "page-e1", command_kind="database.cell.set",
        target_id="row-e1:property-title",
        payload={"row_id": "row-e1", "property_id": "property-title",
                 "value": "Second title"},
        expected_target_version=row["cell_versions"]["property-title"],
        expected_document_sequence=source["document_sequence"],
        expected_workspace_epoch=source["workspace_epoch"],
        expected_document_epoch=source["document_epoch"],
        operation_id="second-row-title",
    )
    with authority._reader() as con:
        old_generation = con.execute(
            "SELECT generation FROM workspace_collaboration_crdt_generations "
            "WHERE document_id='row-e1' AND state='current'").fetchone()[0]
    second_request = _request(
        store, workspace_id, "second-row-placement", row_ids={"row-e1"})
    with pytest.raises(InjectedFailure):
        move_owned_rows_to_page(authority, **second_request,
                                fault="after_domain")
    assert authority.read_database("owner", "database-e1")["rows"][0][
        "body"]["blocks"][0]["content"] == "Second body"
    with authority._reader() as con:
        assert con.execute(
            "SELECT archived FROM workspace_collaboration_documents "
            "WHERE id='row-e1'").fetchone()[0] == 1
        assert con.execute(
            "SELECT placement FROM workspace_collaboration_database_row_page_placements "
            "WHERE row_id='row-e1'").fetchone()[0] == "database"
        assert con.execute(
            "SELECT generation FROM workspace_collaboration_crdt_generations "
            "WHERE document_id='row-e1' AND state='current'").fetchone()[0] == old_generation
    second = move_owned_rows_to_page(authority, **second_request)
    assert move_owned_rows_to_page(authority, **second_request) == second
    page = authority.read_document("owner", "row-e1")
    assert second["rows"][0]["page_id"] == "row-e1"
    assert [block["id"] for block in page["blocks"]] == ["second-row-body"]
    assert page["blocks"][0]["content"] == "Second body"
    assert page["title"] == "Second title"
    assert collaborative_page_payload(authority, "owner", "row-e1")[
        "page"]["title"] == "Second title"
    assert page["parent_document_id"] == "page-e1"
    with authority._reader() as con:
        generations = con.execute(
            "SELECT generation,state FROM workspace_collaboration_crdt_generations "
            "WHERE document_id='row-e1' ORDER BY generation").fetchall()
        assert [tuple(item) for item in generations] == [
            (old_generation, "retired"), (old_generation + 1, "current")]
        assert not con.execute("PRAGMA foreign_key_check").fetchall()
    return_page_to_owned_database(authority, **_return_request(
        store, workspace_id, "second-row-return"))
    returned = authority.read_database("owner", "database-e1")["rows"][0]
    assert returned["id"] == "row-e1"
    assert returned["cells"]["property-date"] == "2026-09-15"
    assert returned["body"]["blocks"][0]["content"] == "Second body"


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_page_return_cross_parent_rehomes_files_and_comments(
        placement_environment):
    store, workspace_id = placement_environment
    authority = store.iam
    source = authority.read_document("owner", "page-e1")
    authority.create_document(
        "owner", workspace_id, "page-e2", title="Other parent",
        blocks=[], expected_workspace_epoch=source["workspace_epoch"],
        operation_id="row-return-create-parent",
    )
    original_file, data = _upload_png(store, "row-return-original-file")
    _surface(authority, "database.row.content.set", "row-e1",
             {"blocks": [{"id": "row-return-file-block", "type": "file",
                          "content": "Attachment",
                          "props": {"file_id": original_file}}]},
             "seed-row-return-file-body", actor="owner", target_version=1)
    move_owned_rows_to_page(authority, **_request(
        store, workspace_id, "row-to-other-parent-before-return",
        parent_id="page-e2", row_ids={"row-e1"}))
    page = authority.read_document("owner", "row-e1")
    on_page_file = page["blocks"][0]["props"]["file_id"]
    authority.add_comment(
        "owner", "row-e1", "row-return-comment",
        content="Page comment",
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="row-return-add-page-comment",
    )
    request = _return_request(store, workspace_id,
                              "page-to-original-db-cross-parent")
    result = return_page_to_owned_database(authority, **request)
    assert result["copied_file_count"] == 1
    row = authority.read_database("owner", "database-e1")["rows"][0]
    returned_file = row["body"]["blocks"][0]["props"]["file_id"]
    assert len({original_file, on_page_file, returned_file}) == 3
    assert authority.get_file("owner", returned_file)["path"].read_bytes() == data
    assert authority.list_comments("owner", "page-e1", item_target={
        "kind": "database_row", "database_id": "database-e1",
        "row_id": "row-e1"})["items"][0]["id"] == "row-return-comment"
    assert not any(block["type"] == "child_page" and block["props"].get(
        "page_id") == "row-e1" for block in authority.read_document(
            "owner", "page-e2")["blocks"])
    with authority._reader() as con:
        assert not con.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_page_return_http_gate_and_replay(placement_environment,
                                                 monkeypatch):
    store, workspace_id = placement_environment
    move_owned_rows_to_page(store.iam, **_request(
        store, workspace_id, "row-to-page-before-http-return",
        row_ids={"row-e1"}))
    request = _return_request(store, workspace_id, "page-return-http")
    body = {key: value for key, value in request.items() if key not in {
        "actor_subject_id", "workspace_id", "page_document_id"}}
    path = (f"/api/notion/workspaces/{workspace_id}/pages/row-e1/"
            "return-to-database")
    target_path = path + "-target"
    client, _actor = _client(store, monkeypatch, enabled=False)
    assert client.post(path, json=body).status_code == 503
    assert client.get(target_path).status_code == 503
    capabilities = store.capabilities
    monkeypatch.setattr(store, "capabilities", lambda: {
        **capabilities(), "collaborative_page_to_database_row": True})
    target = client.get(target_path)
    assert target.status_code == 200
    assert target.json["contract"] == (
        "code_assist.notion.page_to_database_row_target.v1")
    assert target.json["destination_database_id"] == "database-e1"
    assert target.json["expected_row_version"] == request["expected_row_version"]
    with pytest.raises(NotFoundOrDenied):
        read_page_return_target(store.iam, "owner", "other-workspace", "row-e1")
    response = client.post(path, json=body)
    assert response.status_code == 200, response.json
    assert response.json["row_id"] == "row-e1"
    assert client.post(path, json=body).json == response.json
    location_path = (f"/api/notion/workspaces/{workspace_id}/"
                     "database-rows/row-e1/location")
    assert client.get(location_path).json["database_id"] == "database-e1"


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_generic_page_move_updates_row_placement_parent_before_return(
        placement_environment):
    store, workspace_id = placement_environment
    authority = store.iam
    source = authority.read_document("owner", "page-e1")
    authority.create_document(
        "owner", workspace_id, "page-e2", title="Moved page parent",
        blocks=[], expected_workspace_epoch=source["workspace_epoch"],
        operation_id="row-page-create-generic-parent",
    )
    move_owned_rows_to_page(authority, **_request(
        store, workspace_id, "row-page-before-generic-move",
        row_ids={"row-e1"}))
    page = authority.read_document("owner", "row-e1")
    move_page(
        authority, "owner", workspace_id, "row-e1", parent_id="page-e2",
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="row-page-generic-move",
    )
    with authority._reader() as con:
        placement = con.execute(
            "SELECT placement,parent_document_id FROM "
            "workspace_collaboration_database_row_page_placements "
            "WHERE row_id='row-e1'"
        ).fetchone()
        assert tuple(placement) == ("page", "page-e2")
    request = _return_request(store, workspace_id,
                              "row-page-return-after-generic-move")
    return_page_to_owned_database(authority, **request)
    row = authority.read_database("owner", "database-e1")["rows"][0]
    assert row["id"] == "row-e1"
    assert row["cells"]["property-date"] == "2026-09-15"


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_root_row_page_returns_without_a_parent_link(
        placement_environment, monkeypatch):
    store, workspace_id = placement_environment
    authority = store.iam
    move_owned_rows_to_page(authority, **_request(
        store, workspace_id, "root-row-first-placement", row_ids={"row-e1"}))
    page = authority.read_document("owner", "row-e1")
    move_page(
        authority, "owner", workspace_id, "row-e1", parent_id="",
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="root-row-page-move",
    )
    capabilities = store.capabilities
    monkeypatch.setattr(store, "capabilities", lambda: {
        **capabilities(), "collaborative_page_to_database_row": True})
    client, _actor = _client(store, monkeypatch, enabled=False)
    path = (f"/api/notion/workspaces/{workspace_id}/pages/row-e1/"
            "return-to-database")
    target_response = client.get(path + "-target")
    assert target_response.status_code == 200, target_response.json
    target = target_response.json
    assert target["parent_document_id"] == ""
    assert target["expected_parent_sequence"] == 0
    assert target["expected_parent_epoch"] == 0
    request = {key: value for key, value in target.items() if key.startswith(
        "expected_") or key in ("destination_document_id",
                             "destination_database_id")}
    response = client.post(path, json={**request,
        "operation_id": "root-row-page-return"})
    assert response.status_code == 200, response.json
    assert client.post(path, json={**request,
        "operation_id": "root-row-page-return"}).json == response.json
    result = response.json
    assert result["parent_document_id"] == ""
    assert result["parent_document_sequence"] == 0
    assert read_owned_row_location(authority, "owner", workspace_id,
                                   "row-e1")["database_id"] == "database-e1"
    assert authority.read_database("owner", "database-e1")["rows"][0][
        "cells"]["property-date"] == "2026-09-15"

"""UI-D03: atomic, versioned cell ranges on isolated canonical databases."""
from copy import deepcopy
from contextlib import closing
import multiprocessing
import sqlite3
from types import SimpleNamespace

from flask import Flask
import pytest

from code_assist_v1 import routes_notion_workspace
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied, OperationConflict
from code_assist_v1.notion_collaboration.editing_authority import TargetVersionConflict
from code_assist_v1.notion_collaboration.live_authority import replay_live_domain_events_on_connection
from code_assist_v1.tests.notion_collaboration.test_database_features import _authority, _page, _schema


def setup_batch(tmp_path):
    authority, _root, catalog = _authority(tmp_path)
    _schema(authority, "database.property.create", "estimate", {"name":"Estimate", "type":"number", "config":{}}, "op-ui-d03-estimate-create")
    page = _page(authority, "content-editor")
    command = {
        "database_id":"database-e1", "target_id":"database-e1",
        "payload":{"cells":[
            {"row_id":"row-e1", "property_id":"property-e1", "value":"done", "expected_target_version":1},
            {"row_id":"row-e1", "property_id":"estimate", "value":42, "expected_target_version":0},
        ]},
        "expected_document_sequence":page["document_sequence"],
        "expected_workspace_epoch":page["workspace_epoch"],
        "expected_document_epoch":page["document_epoch"],
        "operation_id":"op-ui-d03-batch-0001",
    }
    return authority, catalog, command


def snapshot(catalog):
    with closing(sqlite3.connect(catalog)) as con:
        return {table:con.execute(f"SELECT * FROM {table} ORDER BY 1,2,3").fetchall() for table in (
            "workspace_collaboration_database_cells", "workspace_collaboration_documents",
            "workspace_collaboration_events", "workspace_collaboration_audit_events",
            "workspace_collaboration_outbox", "workspace_collaboration_operations",
        )}


def test_database_changes_replay_to_another_live_editor(tmp_path):
    authority, _root, _catalog = _authority(tmp_path)
    before = _page(authority, "owner")["document_sequence"]
    _schema(authority, "database.property.create", "estimate", {"name": "Estimate", "type": "number", "config": {}}, "op-live-estimate-create")
    page = _page(authority, "content-editor")
    authority.execute_cell_batch(
        "content-editor", "page-e1",
        database_id="database-e1", target_id="database-e1",
        payload={"cells": [{"row_id": "row-e1", "property_id": "property-e1", "value": "updated", "expected_target_version": 1}]},
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="op-live-cell-update",
    )
    with authority._reader() as con:
        workspace, document, _role = authority._access(con, "owner", "page-e1", minimum_role="view")
        replay = replay_live_domain_events_on_connection(
            con, workspace=workspace, document=document, document_id="page-e1",
            after_document_sequence=before,
        )
    assert [event.event_type for event in replay.events] == [
        "database.schema.committed", "database.surface.committed",
    ]
    assert replay.events[-1].payload["database_id"] == "database-e1"
    assert replay.next_document_sequence == _page(authority, "owner")["document_sequence"]


def test_ui_d03_atomic_range_commits_once_and_replays_with_authority(tmp_path):
    authority, catalog, command = setup_batch(tmp_path)
    before = snapshot(catalog)
    first = authority.execute_cell_batch("content-editor", "page-e1", **command)
    assert first["document_sequence"] == command["expected_document_sequence"] + 1
    assert [cell["target_version"] for cell in first["cells"]] == [2,1]
    after = snapshot(catalog)
    assert authority.execute_cell_batch("content-editor", "page-e1", **command) == first
    assert snapshot(catalog) == after
    for table in ("events", "audit_events", "outbox", "operations"):
        key = "workspace_collaboration_" + table
        assert len(after[key]) == len(before[key]) + 1
    changed = deepcopy(command); changed["payload"]["cells"][0]["value"] = "different"
    with pytest.raises(OperationConflict):
        authority.execute_cell_batch("content-editor", "page-e1", **changed)
    page = _page(authority, "owner")
    authority.set_page_grant("owner", "page-e1", "subject", "content-editor", role="view", inherit_to_descendants=False,
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"], operation_id="op-ui-d03-revoke-edit")
    with pytest.raises(NotFoundOrDenied):
        authority.execute_cell_batch("content-editor", "page-e1", **command)


def test_ui_d03_invalid_late_cell_and_faults_roll_back_all_state(tmp_path):
    authority, catalog, command = setup_batch(tmp_path)
    before = snapshot(catalog)
    invalid = deepcopy(command);invalid["payload"]["cells"][1]["value"] = "invalid number"
    with pytest.raises(ValueError):authority.execute_cell_batch("content-editor", "page-e1", **invalid)
    assert snapshot(catalog) == before
    stale = deepcopy(command);stale["payload"]["cells"][1]["expected_target_version"] = 9
    with pytest.raises(TargetVersionConflict):authority.execute_cell_batch("content-editor", "page-e1", **stale)
    assert snapshot(catalog) == before
    missing = deepcopy(command);missing["payload"]["cells"][1]["row_id"] = "another-database-row"
    with pytest.raises(NotFoundOrDenied):authority.execute_cell_batch("content-editor", "page-e1", **missing)
    assert snapshot(catalog) == before
    duplicate = deepcopy(command);duplicate["payload"]["cells"].append(deepcopy(duplicate["payload"]["cells"][0]))
    with pytest.raises(ValueError):authority.execute_cell_batch("content-editor", "page-e1", **duplicate)
    for fault in ("after_domain", "after_event", "after_audit", "after_outbox"):
        with pytest.raises(RuntimeError):authority.execute_cell_batch("content-editor", "page-e1", **command, fault=fault)
        assert snapshot(catalog) == before


def test_ui_d03_date_range_is_one_versioned_cell_and_invalid_ranges_do_not_write(tmp_path):
    authority, catalog, _ = setup_batch(tmp_path)
    _schema(authority, "database.property.create", "due", {"name": "Due", "type": "date", "config": {}}, "op-ui-d03-date-create")
    page = _page(authority, "content-editor")
    command = {
        "database_id": "database-e1", "target_id": "database-e1",
        "payload": {"cells": [{"row_id": "row-e1", "property_id": "due",
                              "value": {"start": "2026-09-16", "end": "2026-09-18"},
                              "expected_target_version": 0}]},
        "expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
        "operation_id": "op-ui-d03-date-range-create",
    }
    receipt = authority.execute_cell_batch("content-editor", "page-e1", **command)
    assert receipt["cells"][0]["target_version"] == 1
    database = authority.read_database("content-editor", "database-e1")
    assert database["rows"][0]["cells"]["due"] == {"start": "2026-09-16", "end": "2026-09-18"}
    assert database["rows"][0]["cell_versions"]["due"] == 1
    before = snapshot(catalog)
    for index, value in enumerate((
        {"start": "2026-09-19", "end": "2026-09-18"},
        {"start": "2026-02-29", "end": None},
        {"start": "2026-09-16", "end": "2026-09-18", "unexpected": True},
    )):
        invalid = deepcopy(command)
        invalid["payload"]["cells"][0].update(value=value, expected_target_version=1)
        invalid["expected_document_sequence"] = receipt["document_sequence"]
        invalid["operation_id"] = f"op-ui-d03-date-range-invalid-{index}"
        with pytest.raises(ValueError):
            authority.execute_cell_batch("content-editor", "page-e1", **invalid)
        assert snapshot(catalog) == before


def test_ui_d03_date_time_and_format_are_one_versioned_cell(tmp_path):
    authority, catalog, _ = setup_batch(tmp_path)
    _schema(authority, "database.property.create", "due", {"name": "Due", "type": "date", "config": {}}, "op-ui-d03-date-meta-create")
    page = _page(authority, "content-editor")
    value = {"start": "2026-09-16", "end": "2026-09-18", "time": "09:00",
             "end_time": "10:30", "date_format": "short", "time_format": "24h"}
    command = {
        "database_id": "database-e1", "target_id": "database-e1",
        "payload": {"cells": [{"row_id": "row-e1", "property_id": "due", "value": value,
                              "expected_target_version": 0}]},
        "expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
        "operation_id": "op-ui-d03-date-meta-write",
    }
    receipt = authority.execute_cell_batch("content-editor", "page-e1", **command)
    stored = authority.read_database("content-editor", "database-e1")["rows"][0]
    assert stored["cells"]["due"] == value
    assert stored["cell_versions"]["due"] == receipt["cells"][0]["target_version"] == 1
    before = snapshot(catalog)
    for index, patch in enumerate((
        {"time": "25:00"}, {"end_time": "10:xx"}, {"date_format": "unexpected"},
        {"time_format": []}, {"end": "2026-09-16", "end_time": "08:00"},
    )):
        invalid = deepcopy(command)
        invalid["payload"]["cells"][0]["value"].update(patch)
        invalid["payload"]["cells"][0]["expected_target_version"] = 1
        invalid["expected_document_sequence"] = receipt["document_sequence"]
        invalid["operation_id"] = f"op-ui-d03-date-meta-invalid-{index}"
        with pytest.raises(ValueError):
            authority.execute_cell_batch("content-editor", "page-e1", **invalid)
        assert snapshot(catalog) == before

    hidden = deepcopy(command)
    hidden["payload"]["cells"][0]["value"]["time_format"] = "hidden"
    hidden["payload"]["cells"][0]["expected_target_version"] = 1
    hidden["expected_document_sequence"] = receipt["document_sequence"]
    hidden["operation_id"] = "op-ui-d03-date-meta-hidden"
    hidden_receipt = authority.execute_cell_batch("content-editor", "page-e1", **hidden)
    stored = authority.read_database("content-editor", "database-e1")["rows"][0]
    assert stored["cells"]["due"]["time"] == "09:00"
    assert stored["cells"]["due"]["time_format"] == "hidden"
    assert stored["cell_versions"]["due"] == hidden_receipt["cells"][0]["target_version"] == 2


def test_ui_d03_date_time_zone_rejects_unknown_and_nonexistent_wall_times(tmp_path):
    authority, catalog, _ = setup_batch(tmp_path)
    _schema(authority, "database.property.create", "due", {"name": "Due", "type": "date", "config": {}}, "op-ui-d03-zone-create")
    page = _page(authority, "content-editor")
    command = {
        "database_id": "database-e1", "target_id": "database-e1",
        "payload": {"cells": [{"row_id": "row-e1", "property_id": "due",
                              "value": {"start": "2026-09-16", "time": "09:00",
                                        "time_zone": "America/New_York"},
                              "expected_target_version": 0}]},
        "expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
        "operation_id": "op-ui-d03-zone-write",
    }
    receipt = authority.execute_cell_batch("content-editor", "page-e1", **command)
    stored = authority.read_database("content-editor", "database-e1")["rows"][0]["cells"]["due"]
    assert stored["time_zone"] == "America/New_York"
    before = snapshot(catalog)
    for index, value in enumerate((
        {"start": "2026-09-16", "time": "09:00", "time_zone": "Invalid/Zone"},
        {"start": "2026-03-08", "time": "02:30", "time_zone": "America/New_York"},
        {"start": "2026-03-07", "end": "2026-03-08", "time": "09:00",
         "end_time": "02:30", "time_zone": "America/New_York"},
    )):
        invalid = deepcopy(command)
        invalid["payload"]["cells"][0].update(value=value, expected_target_version=1)
        invalid["expected_document_sequence"] = receipt["document_sequence"]
        invalid["operation_id"] = f"op-ui-d03-zone-invalid-{index}"
        with pytest.raises(ValueError):
            authority.execute_cell_batch("content-editor", "page-e1", **invalid)
        assert snapshot(catalog) == before


def test_ui_d03_http_batch_round_trip_and_validation(tmp_path, monkeypatch):
    authority, catalog, command = setup_batch(tmp_path)
    monkeypatch.setattr(routes_notion_workspace, "_store", lambda: SimpleNamespace(iam=authority))
    monkeypatch.setattr(routes_notion_workspace, "_collaborative_runtime_enabled", lambda: True)
    monkeypatch.setattr(routes_notion_workspace, "_uid", lambda: "content-editor")
    app=Flask("ui-d03-batch");app.config.update(TESTING=True,SECRET_KEY="synthetic-ui-d03")
    routes_notion_workspace.register_notion_workspace_routes(app)
    client=app.test_client();workspace=_page(authority)["workspace_id"]
    path=f"/api/notion/workspaces/{workspace}/pages/page-e1/databases"
    body={**command,"command_kind":"database.cells.set"}
    bad=deepcopy(body);bad["payload"]["cells"][1]["value"]="invalid"
    before=snapshot(catalog)
    assert client.post(path+"/commands",json=bad).status_code == 400
    assert snapshot(catalog) == before
    response=client.post(path+"/commands",json=body)
    assert response.status_code == 201, response.get_json()
    database=client.get(path+"/database-e1").get_json()["database"]
    assert database["capabilities"]["atomic_cell_batch"] is True
    row=database["rows"][0]
    assert row["cells"]["estimate"] == 42
    assert row["cells"]["property-e1"] == "done"
    assert client.post(path+"/commands",json=body).get_json() == response.get_json()


def _batch_writer(catalog, root, command, barrier, queue):
    from code_assist_v1.notion_collaboration.database_features import CollaborativeDatabaseAuthority
    from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    authority=CollaborativeDatabaseAuthority.open(catalog, root, InMemoryIdentityDirectory(("owner","content-editor","schema-editor")))
    barrier.wait(timeout=30)
    try:
        result=authority.execute_cell_batch("content-editor","page-e1",**command)
        queue.put(("committed",command["operation_id"],result["document_sequence"]))
    except DocumentSequenceConflict as error:
        queue.put(("conflict",command["operation_id"],error.current_document_sequence))


def test_ui_d03_cross_process_competing_ranges_have_one_atomic_winner(tmp_path):
    authority,catalog,command=setup_batch(tmp_path)
    other=deepcopy(command);other["operation_id"]="op-ui-d03-batch-competitor"
    other["payload"]["cells"][0]["value"]="other";other["payload"]["cells"][1]["value"]=99
    context=multiprocessing.get_context("spawn");barrier=context.Barrier(2);queue=context.Queue()
    workers=[context.Process(target=_batch_writer,args=(catalog,catalog.parent.parent,body,barrier,queue)) for body in (command,other)]
    try:
        for worker in workers:worker.start()
        results=[queue.get(timeout=45) for _ in workers]
        for worker in workers:worker.join(timeout=10);assert worker.exitcode == 0
        assert sorted(item[0] for item in results) == ["committed","conflict"]
        row=authority.read_database("owner","database-e1")["rows"][0]
        assert (row["cells"]["property-e1"],row["cells"]["estimate"]) in (("done",42),("other",99))
        assert row["cell_versions"] == {"property-e1":2,"estimate":1}
    finally:
        for worker in workers:
            if worker.is_alive():worker.terminate();worker.join(timeout=10)
        queue.close();queue.join_thread()

from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace

from flask import Flask
import pytest

from code_assist_v1 import notion_workspace_database_features_migration as migration
from code_assist_v1 import notion_workspace_history_authority_migration as history_migration
from code_assist_v1 import notion_workspace_search_authority_migration as search_migration
from code_assist_v1 import notion_workspace_second_brain_publication_migration as publication_migration
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.approved_dev_pair import _prepare_gateway_catalog
from code_assist_v1.notion_collaboration.database_features import CollaborativeDatabaseAuthority
from code_assist_v1.notion_collaboration.editing_authority import TargetVersionConflict
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.page_settings import update_page_settings
from code_assist_v1 import routes_notion_workspace
from code_assist_v1.tests.notion_collaboration.test_collaborative_editing_authority import (
    _environment,
)


SUBJECTS = ("owner", "content-editor", "schema-editor")


def test_locked_collaborative_page_rejects_database_cell_schema_and_row_writes(tmp_path: Path) -> None:
    authority, _root, catalog = _authority(tmp_path)
    page = authority.read_document("schema-editor", "page-e1")
    workspace_id = page["workspace_id"]
    update_page_settings(
        authority, "schema-editor", workspace_id, "page-e1", updates={"locked": True},
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id="locked-database-page",
    )
    page = authority.read_document("schema-editor", "page-e1")
    common = {
        "database_id": "database-e1",
        "expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
    }
    with sqlite3.connect(catalog) as con:
        before = con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations").fetchone()[0]

    with pytest.raises(PermissionError, match="page is locked"):
        authority.execute_cell_batch(
            "content-editor", "page-e1", **common, target_id="database-e1",
            payload={"cells": [{"row_id": "row-e1", "property_id": "property-e1",
                                "value": "bypass", "expected_target_version": 1}]},
            operation_id="locked-cell-batch",
        )
    with pytest.raises(PermissionError, match="page is locked"):
        authority.execute_schema_command(
            "schema-editor", "page-e1", **common, command_kind="database.property.create",
            target_id="locked-property", payload={"name": "Bypass", "type": "text", "config": {}},
            operation_id="locked-schema-command",
        )
    with pytest.raises(PermissionError, match="page is locked"):
        authority.execute_surface_command(
            "content-editor", "page-e1", **common, command_kind="database.row.create",
            target_id="locked-row", payload={}, operation_id="locked-row-command",
        )
    assert authority.read_document("schema-editor", "page-e1") == page
    with sqlite3.connect(catalog) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations").fetchone()[0] == before


def _backup(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(target_connection)
        target_connection.commit()
    finally:
        target_connection.close()
        source_connection.close()
    return destination


def _apply(target, catalog: Path, root: Path, name: str) -> None:
    backup = _backup(catalog, root / "backups" / name)
    plan = target.build_plan(catalog, root)
    target.apply_plan(
        catalog,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
    )


def _authority(tmp_path: Path) -> tuple[CollaborativeDatabaseAuthority, Path, Path]:
    _editing, _transport, root, catalog, _workspace_id = _environment(tmp_path)
    _apply(publication_migration, catalog, root, "before-2406.db")
    _apply(migration, catalog, root, "before-2407.db")
    return (
        CollaborativeDatabaseAuthority.open(
            catalog, root, InMemoryIdentityDirectory(SUBJECTS)
        ),
        root,
        catalog,
    )


def _page(authority: CollaborativeDatabaseAuthority, subject: str = "schema-editor") -> dict:
    return authority.read_document(subject, "page-e1")


def _schema(
    authority: CollaborativeDatabaseAuthority,
    kind: str,
    target: str,
    payload: dict,
    operation: str,
    *,
    actor: str = "schema-editor",
    target_version: int | None = None,
) -> dict:
    page = _page(authority, actor)
    return authority.execute_schema_command(
        actor,
        "page-e1",
        database_id="database-e1",
        command_kind=kind,
        target_id=target,
        payload=payload,
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        expected_target_version=target_version,
        operation_id=operation,
    )


def _surface(
    authority: CollaborativeDatabaseAuthority,
    kind: str,
    target: str,
    payload: dict,
    operation: str,
    *,
    actor: str = "content-editor",
    target_version: int | None = None,
) -> dict:
    page = _page(authority, actor)
    return authority.execute_surface_command(
        actor,
        "page-e1",
        database_id="database-e1",
        command_kind=kind,
        target_id=target,
        payload=payload,
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        expected_target_version=target_version,
        operation_id=operation,
    )


@pytest.mark.integration
def test_database_template_defaults_apply_atomically_when_creating_row(tmp_path: Path) -> None:
    authority, _root, catalog = _authority(tmp_path)
    _schema(authority, "database.property.create", "property-template-number",
            {"name": "Estimate", "type": "number", "config": {}},
            "template-number-property")
    before = _page(authority, "content-editor")["document_sequence"]
    with pytest.raises(PermissionError):
        _schema(authority, "database.template.create", "template-no-schema-role",
                {"name": "No schema role", "body": [], "default_values": {}},
                "template-no-schema-role-create", actor="content-editor", target_version=0)
    assert _page(authority, "content-editor")["document_sequence"] == before
    _schema(authority, "database.template.create", "template-task",
            {"name": "Task", "body": [], "default_values": {
                "property-e1": "Templated title", "property-template-number": 3}},
            "template-defaults-create")
    before = _page(authority, "content-editor")["document_sequence"]
    with pytest.raises(PermissionError):
        _schema(authority, "database.template.update", "template-task",
                {"name": "Unauthorized rename", "body": [], "default_values": {}},
                "template-no-schema-role-update", actor="content-editor", target_version=1)
    assert _page(authority, "content-editor")["document_sequence"] == before

    created = _surface(authority, "database.row.create", "row-template-applied",
                       {"template_id": "template-task"}, "template-row-create",
                       target_version=0)
    assert created["template_id"] == "template-task"
    assert created["applied_defaults"] == {
        "property-e1": "Templated title", "property-template-number": 3}
    row = next(item for item in authority.read_database("content-editor", "database-e1")["rows"]
               if item["id"] == "row-template-applied")
    assert row["cells"] == created["applied_defaults"]
    assert row["cell_versions"] == {"property-e1": 1, "property-template-number": 1}

    existing = next(item for item in authority.read_database("content-editor", "database-e1")["rows"]
                    if item["id"] == "row-e1")
    applied = _surface(authority, "database.row.template.apply", "row-e1",
                       {"template_id": "template-task"}, "template-existing-without-body",
                       target_version=existing["version"])
    assert applied["applied_defaults"] == {"property-template-number": 3}
    updated = next(item for item in authority.read_database("content-editor", "database-e1")["rows"]
                   if item["id"] == "row-e1")
    assert updated["cells"]["property-e1"] == existing["cells"]["property-e1"]
    assert updated["cells"]["property-template-number"] == 3

    _schema(authority, "database.template.create", "template-invalid",
            {"name": "Invalid", "body": [], "default_values": {
                "property-e1": "Should roll back", "property-template-number": "wrong type"}},
            "template-invalid-create")
    before = _page(authority, "content-editor")["document_sequence"]
    with pytest.raises(ValueError, match="number property"):
        _surface(authority, "database.row.create", "row-template-invalid",
                 {"template_id": "template-invalid"}, "template-row-invalid",
                 target_version=0)
    assert _page(authority, "content-editor")["document_sequence"] == before
    with sqlite3.connect(catalog) as con:
        assert con.execute("SELECT 1 FROM workspace_collaboration_database_rows WHERE id=?",
                           ("row-template-invalid",)).fetchone() is None

    with pytest.raises(NotFoundOrDenied):
        _surface(authority, "database.row.create", "row-template-missing",
                 {"template_id": "unknown-template"}, "template-row-missing",
                 target_version=0)


@pytest.mark.integration
def test_f0_p1_successor_approved_development_catalog_forwards_fresh_root_to_2410(
    tmp_path: Path,
) -> None:
    catalog = _prepare_gateway_catalog(tmp_path / "approved-pair")
    con = sqlite3.connect(catalog)
    try:
        marker = con.execute(
            "SELECT version,name,checksum,actual_schema_sha256 "
            "FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    assert marker == (
        search_migration.TARGET_VERSION,
        search_migration.TARGET_NAME,
        search_migration.TARGET_CHECKSUM,
        search_migration.TARGET_ACTUAL_SCHEMA_SHA256,
    )


@pytest.mark.integration
def test_f0_p1_forward_migration_is_frozen_and_direct_sql_guards_are_fail_closed(
    tmp_path: Path,
) -> None:
    authority, _root, catalog = _authority(tmp_path)
    assert migration.TARGET_CHECKSUM.startswith("sha256:")
    assert migration.TARGET_ACTUAL_SCHEMA_SHA256.startswith("sha256:")
    assert authority.read_database("owner", "database-e1")["id"] == "database-e1"

    con = sqlite3.connect(catalog)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="property definition"):
            con.execute(
                "INSERT INTO workspace_collaboration_database_properties("
                "id,database_id,name,property_type,position,config_json,row_version,created_at,updated_at) "
                "VALUES('bad-formula','database-e1','Bad','formula',99,'{}',1,1,1)"
            )
        con.rollback()
        command = _schema(
            authority,
            "database.view.create",
            "view-table",
            {"name": "Table", "type": "table", "config": {}},
            "op-f0-p1-view-create-0001",
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute(
                "UPDATE workspace_collaboration_database_schema_commands "
                "SET result_target_version=2 WHERE operation_id=?",
                (command["operation_id"],),
            )
    finally:
        con.close()


@pytest.mark.integration
def test_f0_p1_schema_role_round_trip_formula_rollup_relation_and_restart(
    tmp_path: Path,
) -> None:
    authority, root, catalog = _authority(tmp_path)

    with pytest.raises(NotFoundOrDenied):
        _schema(
            authority,
            "database.view.create",
            "view-denied",
            {"name": "Denied", "type": "table", "config": {}},
            "op-f0-p1-view-denied-0001",
            actor="content-editor",
        )

    _schema(
        authority,
        "database.property.create",
        "property-estimate",
        {"name": "Estimate", "type": "number", "config": {}},
        "op-f0-p1-property-estimate-0001",
    )
    _schema(
        authority,
        "database.property.create",
        "property-related",
        {
            "name": "Related",
            "type": "relation",
            "config": {"target_database_id": "database-e1"},
        },
        "op-f0-p1-property-related-0001",
    )
    _schema(
        authority,
        "database.property.create",
        "property-total",
        {
            "name": "Total",
            "type": "rollup",
            "config": {
                "relation_property_id": "property-related",
                "target_property_id": "property-estimate",
                "aggregation": "sum",
            },
        },
        "op-f0-p1-property-total-0001",
    )
    _schema(
        authority,
        "database.property.create",
        "property-double",
        {
            "name": "Double",
            "type": "formula",
            "config": {"expression": 'prop("Estimate") * 2'},
        },
        "op-f0-p1-property-double-0001",
    )
    _schema(
        authority,
        "database.view.create",
        "view-timeline",
        {
            "name": "Timeline",
            "type": "timeline",
            "config": {"group_by": "property-e1", "sort": ["property-estimate"]},
        },
        "op-f0-p1-view-timeline-0001",
    )
    _schema(
        authority,
        "database.template.create",
        "template-task",
        {
            "name": "Task",
            "body": [{"type": "paragraph", "content": "New task"}],
            "default_values": {"property-e1": "todo", "property-estimate": 1},
        },
        "op-f0-p1-template-task-0001",
    )

    page = _page(authority, "content-editor")
    estimate = authority.execute_typed_command(
        "content-editor",
        "page-e1",
        command_kind="database.cell.set",
        target_id="row-e1:property-estimate",
        payload={"row_id": "row-e1", "property_id": "property-estimate", "value": 5},
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        expected_target_version=0,
        operation_id="op-f0-p1-estimate-set-0001",
    )
    page = _page(authority, "content-editor")
    relation = authority.execute_typed_command(
        "content-editor",
        "page-e1",
        command_kind="database.cell.set",
        target_id="row-e1:property-related",
        payload={
            "row_id": "row-e1",
            "property_id": "property-related",
            "value": ["row-e1"],
        },
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        expected_target_version=0,
        operation_id="op-f0-p1-relation-set-0001",
    )
    assert estimate["target_version"] == relation["target_version"] == 1

    page = _page(authority, "content-editor")
    with pytest.raises(TargetVersionConflict):
        authority.execute_typed_command(
            "content-editor",
            "page-e1",
            command_kind="database.cell.set",
            target_id="row-e1:property-estimate",
            payload={"row_id": "row-e1", "property_id": "property-estimate", "value": 7},
            expected_document_sequence=int(page["document_sequence"]),
            expected_workspace_epoch=int(page["workspace_epoch"]),
            expected_document_epoch=int(page["document_epoch"]),
            expected_target_version=0,
            operation_id="op-f0-p1-estimate-stale-0001",
        )

    first = authority.read_database("content-editor", "database-e1")
    row = first["rows"][0]
    assert row["computed"]["property-double"] == 10
    assert row["computed"]["property-total"] == 5
    assert first["views"][0]["type"] == "timeline"
    assert first["templates"][0]["default_values"]["property-estimate"] == 1

    restarted = CollaborativeDatabaseAuthority.open(
        catalog, root, InMemoryIdentityDirectory(SUBJECTS)
    )
    assert restarted.read_database("content-editor", "database-e1") == first


@pytest.mark.integration
def test_f0_p5_adopted_ui_surface_commands_are_atomic_role_scoped_and_restart_safe(
    tmp_path: Path,
) -> None:
    authority, root, catalog = _authority(tmp_path)

    created = _surface(
        authority,
        "database.row.create",
        "row-ui-round-trip",
        {},
        "op-f0-p5-row-create-0001",
        target_version=0,
    )
    assert created["effective_role"] == "edit_content"
    assert created["target_version"] == 1

    page = _page(authority, "content-editor")
    written = authority.execute_typed_command(
        "content-editor",
        "page-e1",
        command_kind="database.cell.set",
        target_id="row-ui-round-trip:property-e1",
        payload={
            "row_id": "row-ui-round-trip",
            "property_id": "property-e1",
            "value": "canonical round trip",
        },
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        expected_target_version=0,
        operation_id="op-f0-p5-cell-set-0001",
    )
    assert written["target_version"] == 1

    with pytest.raises(NotFoundOrDenied):
        _surface(
            authority,
            "database.update",
            "database-e1",
            {"title": "Denied"},
            "op-f0-p5-title-denied-0001",
            target_version=1,
        )

    renamed = _surface(
        authority,
        "database.update",
        "database-e1",
        {"title": "Adopted canonical tasks"},
        "op-f0-p5-title-update-0001",
        actor="schema-editor",
        target_version=1,
    )
    assert renamed["target_version"] == 2

    _schema(
        authority,
        "database.property.create",
        "property-ui-temporary",
        {"name": "Temporary", "type": "text", "config": {}},
        "op-f0-p5-property-create-0001",
    )
    deleted_property = _surface(
        authority,
        "database.property.delete",
        "property-ui-temporary",
        {},
        "op-f0-p5-property-delete-0001",
        actor="schema-editor",
        target_version=1,
    )
    assert deleted_property["deleted"] is True

    before_restart = authority.read_database("content-editor", "database-e1")
    ui_row = next(
        item for item in before_restart["rows"] if item["id"] == "row-ui-round-trip"
    )
    assert ui_row["cells"]["property-e1"] == "canonical round trip"
    assert ui_row["cell_versions"]["property-e1"] == 1

    reopened = CollaborativeDatabaseAuthority.open(
        catalog, root, InMemoryIdentityDirectory(SUBJECTS)
    )
    after_restart = reopened.read_databases("content-editor", "page-e1")
    assert len(after_restart) == 1
    assert after_restart[0]["title"] == "Adopted canonical tasks"
    assert all(
        item["id"] != "property-ui-temporary"
        for item in after_restart[0]["properties"]
    )
    assert next(
        item
        for item in after_restart[0]["rows"]
        if item["id"] == "row-ui-round-trip"
    )["cells"]["property-e1"] == "canonical round trip"

    removed = _surface(
        reopened,
        "database.row.delete",
        "row-ui-round-trip",
        {},
        "op-f0-p5-row-delete-0001",
        target_version=1,
    )
    assert removed["deleted"] is True
    assert all(
        item["id"] != "row-ui-round-trip"
        for item in reopened.read_database("content-editor", "database-e1")["rows"]
    )

    con = sqlite3.connect(catalog)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_audit_events "
            "WHERE action='collaboration.database_surface_command'"
        ).fetchone()[0] == 4
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_outbox "
            "WHERE event_type='database.surface.committed'"
        ).fetchone()[0] == 4
    finally:
        con.close()


@pytest.mark.integration
def test_f0_p5_database_http_round_trip_and_view_type_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority, _root, _catalog = _authority(tmp_path)
    workspace_id = str(_page(authority)["workspace_id"])
    actor = {"id": "content-editor"}
    store = SimpleNamespace(iam=authority)
    monkeypatch.setattr(routes_notion_workspace, "_store", lambda: store)
    monkeypatch.setattr(
        routes_notion_workspace, "_collaborative_runtime_enabled", lambda: True
    )
    monkeypatch.setattr(routes_notion_workspace, "_uid", lambda: actor["id"])

    app = Flask("f0-p5-database-http")
    app.config.update(TESTING=True, SECRET_KEY="f0-p5-database-http")
    routes_notion_workspace.register_notion_workspace_routes(app)
    client = app.test_client()
    base = f"/api/notion/workspaces/{workspace_id}/pages/page-e1/databases"

    loaded = client.get(base + "/database-e1")
    assert loaded.status_code == 200
    assert loaded.get_json()["database"]["rows"][0]["cell_versions"] == {
        "property-e1": 1
    }

    page = _page(authority, actor["id"])
    created = client.post(
        base + "/commands",
        json={
            "database_id": "database-e1",
            "command_kind": "database.row.create",
            "target_id": "row-http",
            "payload": {},
            "expected_document_sequence": page["document_sequence"],
            "expected_workspace_epoch": page["workspace_epoch"],
            "expected_document_epoch": page["document_epoch"],
            "expected_target_version": 0,
            "operation_id": "op-f0-p5-http-row-create-0001",
        },
    )
    assert created.status_code == 201, created.get_json()
    assert created.get_json()["command_kind"] == "database.row.create"

    page = _page(authority, actor["id"])
    denied = client.post(
        base + "/commands",
        json={
            "database_id": "database-e1",
            "command_kind": "database.update",
            "target_id": "database-e1",
            "payload": {"title": "Denied"},
            "expected_document_sequence": page["document_sequence"],
            "expected_workspace_epoch": page["workspace_epoch"],
            "expected_document_epoch": page["document_epoch"],
            "expected_target_version": 1,
            "operation_id": "op-f0-p5-http-title-denied-0001",
        },
    )
    assert denied.status_code == 404

    actor["id"] = "schema-editor"
    page = _page(authority, actor["id"])
    unsupported = client.post(
        base + "/commands",
        json={
            "database_id": "database-e1",
            "command_kind": "database.view.create",
            "target_id": "view-chart-denied",
            "payload": {"name": "Chart", "type": "chart", "config": {}},
            "expected_document_sequence": page["document_sequence"],
            "expected_workspace_epoch": page["workspace_epoch"],
            "expected_document_epoch": page["document_epoch"],
            "expected_target_version": 0,
            "operation_id": "op-f0-p5-http-chart-denied-0001",
        },
    )
    assert unsupported.status_code == 400
    assert unsupported.get_json()["code"] == "invalid_request"

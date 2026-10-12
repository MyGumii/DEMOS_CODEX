"""UI-R5: offline, recoverable database-row Trash membership on synthetic catalogs."""
import json
import sqlite3
import time
from pathlib import Path

import pytest

from code_assist_v1 import notion_workspace_database_trash_migration as database_trash_migration
from code_assist_v1 import notion_workspace_database_row_trash_migration as migration
from code_assist_v1.notion_collaboration import (
    database_move, database_row_trash, database_trash, page_copy,
)
from code_assist_v1.notion_collaboration.canonical_authority import (
    InjectedFailure, NotFoundOrDenied, OperationConflict, _canonical_json, _sha256,
)
from code_assist_v1.notion_collaboration.coordinated_workspace_backup import _notion_observation
from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import (
    _connection, activity_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema
from code_assist_v1.tests.notion_collaboration.test_database_move_commit import (
    _destination, _request as move_request, _upload_png,
)
from code_assist_v1.tests.notion_collaboration.test_database_row_content import _apply, _backup
from code_assist_v1.notion_collaboration.page_appearance import update_page_appearance
from code_assist_v1.notion_collaboration.page_copy_source import (
    CopySourceDocument, _exclude_archived_row_only_files,
)
from code_assist_v1.tests.notion_collaboration.test_page_appearance import request as appearance_request
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, owned_database_block,
    request as command_request, upload,
)


def test_ui_r5_database_row_trash_migration_preserves_rows_and_rejects_orphans(
        activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    with _connection(store.db_path) as con:
        con.execute("INSERT INTO workspace_collaboration_database_row_bodies "
            "(row_id,blocks_json) VALUES('row-e1',?)",
            ('[{"id":"row-body-synthetic","type":"paragraph","content":"Retained body","props":{}}]',))
        con.execute("INSERT INTO workspace_collaboration_database_row_appearance "
            "(row_id,appearance_json) VALUES('row-e1',?)",
            ('{"icon":"⭐","cover":"","cover_position":0.5}',))
        row_before = con.execute("SELECT * FROM workspace_collaboration_database_rows "
            "WHERE id='row-e1'").fetchone()
        cells_before = con.execute("SELECT * FROM workspace_collaboration_database_cells "
            "WHERE row_id='row-e1' ORDER BY property_id").fetchall()
    assert row_before is not None
    _apply(database_trash_migration, store.db_path, store.user_data_root,
        "before-database-row-trash-source.db")
    plan = migration.build_plan(store.db_path, store.user_data_root)
    backup = _backup(store.db_path,
        store.user_data_root / "backups" / "before-database-row-trash.db")
    options = dict(exact_plan_checksum=plan["plan_checksum"], backup_confirmed=True,
        catalog_quiesced_confirmed=True, backup_path=backup)
    with pytest.raises(migration.MigrationRefused):
        migration.apply_plan(store.db_path, store.user_data_root,
            **{**options, "exact_plan_checksum": "sha256:" + "0" * 64})

    def fail(stage):
        if stage == "after_schema":
            raise RuntimeError("synthetic database-row Trash migration failure")

    with pytest.raises(RuntimeError):
        migration.apply_plan(store.db_path, store.user_data_root,
            **options, fault_hook=fail)
    with _connection(store.db_path) as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE "
            "name='workspace_collaboration_database_row_trash_state'").fetchone() is None
        assert con.execute("SELECT * FROM workspace_collaboration_database_rows "
            "WHERE id='row-e1'").fetchone() == row_before

    migration.apply_plan(store.db_path, store.user_data_root, **options)
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"
    assert _notion_observation(Path(store.db_path))["schema_version"] == migration.TARGET_VERSION
    with _connection(store.db_path) as con:
        con.row_factory = sqlite3.Row
        assert database_row_trash.installed(con)
        assert database_trash.installed(con)
        document = store.iam._document(con, "page-e1")
        before_snapshot = store.iam._editing_snapshot(con, document)
        before_rows = next(item["rows"] for item in before_snapshot["databases"]
            if item["id"] == "database-e1")
        assert any(row["id"] == "row-e1" for row in before_rows)
        assert con.execute("SELECT * FROM workspace_collaboration_database_rows "
            "WHERE id='row-e1'").fetchone()[:] == row_before
        assert [tuple(row) for row in con.execute(
            "SELECT * FROM workspace_collaboration_database_cells "
            "WHERE row_id='row-e1' ORDER BY property_id").fetchall()] == cells_before
        assert con.execute("SELECT COUNT(*) FROM "
            "workspace_collaboration_database_row_trash_state").fetchone()[0] == 0
        version = con.execute("SELECT row_version FROM workspace_collaboration_database_rows "
            "WHERE id='row-e1'").fetchone()[0]
        values = ("row-e1", "database-e1", workspace_id, "page-e1", version,
            "owner", 1, "synthetic-row-trash")
        con.execute("INSERT INTO workspace_collaboration_database_row_trash_state "
            "(row_id,database_id,archive_workspace_id,archive_document_id,"
            "archived_row_version,archived_by_subject_id,archived_at,archive_operation_id) "
            "VALUES(?,?,?,?,?,?,?,?)", values)
        assert database_row_trash.archived_row_ids(con, "database-e1") == {"row-e1"}
        hidden_snapshot = store.iam._editing_snapshot(con, document)
        hidden_rows = next(item["rows"] for item in hidden_snapshot["databases"]
            if item["id"] == "database-e1")
        assert all(row["id"] != "row-e1" for row in hidden_rows)
        with pytest.raises(NotFoundOrDenied):
            database_row_trash.require_active(con, "database-e1", "row-e1")
        with pytest.raises(NotFoundOrDenied):
            store.iam._apply_surface_domain(con, document_id="page-e1",
                database_id="database-e1", command_kind="database.row.delete",
                target_id="row-e1", payload={}, expected_target_version=version,
                created_at=2)
        first_property = next(prop["id"] for db_item in before_snapshot["databases"]
            if db_item["id"] == "database-e1" for prop in db_item["properties"])
        with pytest.raises(NotFoundOrDenied):
            store.iam._apply_typed_domain(con, actor_subject_id="owner",
                document_id="page-e1", command_id="staged-trash-cell",
                command_kind="database.cell.set",
                target_id=f"row-e1:{first_property}",
                payload={"row_id": "row-e1", "property_id": first_property,
                    "value": "Hidden row must not change"},
                expected_target_version=None, document_sequence=1,
                created_at=2)
        assert con.execute("SELECT * FROM workspace_collaboration_database_rows "
            "WHERE id='row-e1'").fetchone()[:] == row_before
        assert [tuple(row) for row in con.execute(
            "SELECT * FROM workspace_collaboration_database_cells "
            "WHERE row_id='row-e1' ORDER BY property_id").fetchall()] == cells_before
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("DELETE FROM workspace_collaboration_database_rows "
                "WHERE id='row-e1'")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO workspace_collaboration_database_row_trash_state "
                "(row_id,database_id,archive_workspace_id,archive_document_id,"
                "archived_row_version,archived_by_subject_id,archived_at,archive_operation_id) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("missing-row", "database-e1", workspace_id, "page-e1", 1,
                    "owner", 2, "invalid-row-trash"))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE workspace_collaboration_database_row_trash_state "
                "SET database_id='wrong-database' WHERE row_id='row-e1'")
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        con.execute("DELETE FROM workspace_collaboration_database_row_trash_state "
            "WHERE row_id='row-e1'")
        database_row_trash.require_active(con, "database-e1", "row-e1")
        restored_snapshot = store.iam._editing_snapshot(con, document)
        restored_rows = next(item["rows"] for item in restored_snapshot["databases"]
            if item["id"] == "database-e1")
        assert any(row["id"] == "row-e1" for row in restored_rows)


def test_ui_r5_database_row_trash_service_is_atomic_replayable_and_recoverable(
        activity_environment, monkeypatch):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _schema(store.iam, "database.property.create", "row-trash-due",
        {"name": "Due", "type": "date", "config": {}},
        "row-trash-due-property")
    _schema(store.iam, "database.property.create", "row-trash-related",
        {"name": "Related", "type": "relation",
         "config": {"target_database_id": "database-e1"}},
        "row-trash-related-property")
    page_for_reminder = store.iam.read_document("content-editor", "page-e1")
    store.iam.execute_cell_batch("content-editor", "page-e1",
        database_id="database-e1", target_id="database-e1",
        payload={"cells": [{"row_id": "row-e1", "property_id": "row-trash-due",
            "value": {"start": "2027-01-01", "reminder": {
                "at": "2027-01-01T09:00:00+09:00", "lead": 0,
                "user_id": "content-editor"}}, "expected_target_version": 0}]},
        expected_document_sequence=page_for_reminder["document_sequence"],
        expected_workspace_epoch=page_for_reminder["workspace_epoch"],
        expected_document_epoch=page_for_reminder["document_epoch"],
        operation_id="row-trash-due-reminder")
    with _connection(store.db_path) as con:
        con.execute("INSERT INTO workspace_collaboration_database_row_bodies "
            "(row_id,blocks_json) VALUES('row-e1',?)",
            ('[{"id":"row-body-synthetic","type":"paragraph","content":"Retained body","props":{}}]',))
        con.execute("INSERT INTO workspace_collaboration_database_row_appearance "
            "(row_id,appearance_json) VALUES('row-e1',?)",
            ('{"icon":"⭐","cover":"","cover_position":0.5}',))
        row_before = con.execute("SELECT * FROM workspace_collaboration_database_rows "
            "WHERE id='row-e1'").fetchone()
        cells_before = con.execute("SELECT * FROM workspace_collaboration_database_cells "
            "WHERE row_id='row-e1' ORDER BY property_id").fetchall()
    before = store.iam.read_document("owner", "page-e1")
    store.iam.add_comment("owner", "page-e1", "row-trash-root-comment",
        content="Retained row discussion",
        anchor={"kind": "database_row", "database_id": "database-e1",
            "row_id": "row-e1"}, mention_subject_ids=["content-editor"],
        expected_workspace_epoch=before["workspace_epoch"],
        expected_document_epoch=before["document_epoch"],
        operation_id="row-trash-root-comment-add")
    before = store.iam.read_document("owner", "page-e1")
    store.iam.add_comment("owner", "page-e1", "row-trash-reply-comment",
        content="Retained reply", parent_comment_id="row-trash-root-comment",
        anchor={"kind": "database_row", "database_id": "database-e1",
            "row_id": "row-e1"}, expected_workspace_epoch=before["workspace_epoch"],
        expected_document_epoch=before["document_epoch"],
        operation_id="row-trash-reply-comment-add")
    _apply(database_trash_migration, store.db_path, store.user_data_root,
        "before-row-service-source.db")
    _apply(migration, store.db_path, store.user_data_root,
        "before-row-service.db")

    def staged_connect(*, query_only):
        con = sqlite3.connect(
            f"{Path(store.db_path).as_uri()}?mode=ro" if query_only else store.db_path,
            uri=query_only, timeout=5.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        if query_only:
            con.execute("PRAGMA query_only=ON")
        assert database_row_trash.installed(con)
        return con

    monkeypatch.setattr(store.iam, "_connect", staged_connect)
    assert len(store.iam.list_reminders("content-editor", workspace_id)["items"]) == 1
    assert "2027" in store.iam.export_csv(
        "owner", "page-e1", block_id="owned-db-source-block")["csv"]

    def relation_projection(con, snapshot):
        source = next(item for item in snapshot["databases"]
            if item["id"] == "database-e1")
        title_id = "synthetic-target-property"
        relation_id = "synthetic-relation-to-archived-row"
        rollup_id = "synthetic-rollup-from-archived-row"
        projected = store.iam._materialize_rows(con, "owner", {
            **source,
            "properties": [*source["properties"],
                {"id": relation_id, "name": "Relation", "type": "relation",
                 "config": {"target_database_id": "database-e1"}},
                {"id": rollup_id, "name": "Count", "type": "rollup",
                 "config": {"relation_property_id": relation_id,
                    "target_property_id": title_id, "aggregation": "count"}}],
            "rows": [{"id": "synthetic-other-row",
                "cells": {relation_id: ["row-e1"]}}],
        })[0]
        return projected["cells"][relation_id], projected["computed"][rollup_id]

    with store.iam._reader() as con:
        page = store.iam._document(con, "page-e1")
        before_snapshot = store.iam._editing_snapshot(con, page)
        assert relation_projection(con, before_snapshot) == (["row-e1"], 1)
        original_row = next(row for database_item in before_snapshot["databases"]
            if database_item["id"] == "database-e1"
            for row in database_item["rows"] if row["id"] == "row-e1")
        assert original_row["body"]["blocks"][0]["content"] == "Retained body"
        assert original_row["appearance"]["icon"] == "⭐"
        database = con.execute("SELECT * FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone()
        fences = {"expected_document_sequence": int(page["document_sequence"]),
            "expected_workspace_epoch": int(store.iam._workspace(
                con, workspace_id)["security_epoch"]),
            "expected_document_epoch": int(page["access_epoch"]),
            "expected_database_version": int(database["row_version"]),
            "expected_row_version": int(row_before[3])}
    base = (store.iam, "owner", workspace_id, "page-e1", "database-e1", "row-e1")
    item_target = {"kind": "database_row", "database_id": "database-e1",
        "row_id": "row-e1"}
    assert database_row_trash.list_trashed_database_rows(
        store.iam, "owner", workspace_id, "page-e1", "database-e1")["count"] == 0
    assert {item["id"] for item in store.iam.list_comments(
        "owner", "page-e1", item_target=item_target)["items"]} == {
            "row-trash-root-comment", "row-trash-reply-comment"}
    with pytest.raises(InjectedFailure):
        database_row_trash.set_database_row_trashed(*base, archived=True,
            operation_id="row-trash-fault", fault="after_domain", **fences)
    with _connection(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM "
            "workspace_collaboration_database_row_trash_state").fetchone()[0] == 0
        assert con.execute("SELECT * FROM workspace_collaboration_database_rows "
            "WHERE id='row-e1'").fetchone() == row_before
    archived = database_row_trash.set_database_row_trashed(*base,
        archived=True, operation_id="row-trash-archive", **fences)
    assert archived["archived"] and not archived["replayed"]
    observation = _notion_observation(Path(store.db_path))
    assert observation["schema_version"] == migration.TARGET_VERSION
    assert observation["event_continuity"]["event_order_mismatch_count"] == 0
    recovered = database_row_trash.read_database_row_trash_result(*base,
        archived=True, operation_id="row-trash-archive", **fences)
    assert recovered["event_id"] == archived["event_id"] and recovered["replayed"]
    listing = database_row_trash.list_trashed_database_rows(
        store.iam, "content-editor", workspace_id, "page-e1", "database-e1")
    assert listing["count"] == 1 and listing["effective_role"] == "edit_content"
    assert listing["items"][0]["row_id"] == "row-e1"
    assert listing["items"][0]["row_version"] == archived["row_version"]
    assert listing["items"][0]["cells"]["row-trash-due"]["start"] == "2027-01-01"
    assert listing["items"][0]["body"]["blocks"][0]["content"] == "Retained body"
    assert listing["items"][0]["appearance"]["icon"] == "⭐"
    assert archived["row_version"] == fences["expected_row_version"] + 1
    assert store.iam.list_reminders("content-editor", workspace_id)["items"] == []
    assert "2027" not in store.iam.export_csv(
        "owner", "page-e1", block_id="owned-db-source-block")["csv"]
    assert store.iam.list_comments("owner", "page-e1")["count"] == 0
    assert all(item["id"] not in {"row-trash-root-comment", "row-trash-reply-comment"}
        for item in store.iam.read_document("owner", "page-e1")["comments"])
    assert not any(item["comment_id"] == "row-trash-root-comment" for item in
        store.iam.list_inbox("content-editor", workspace_id=workspace_id)["items"])
    with pytest.raises(NotFoundOrDenied):
        store.iam.list_comments("owner", "page-e1", item_target=item_target)
    assert database_row_trash.set_database_row_trashed(*base,
        archived=True, operation_id="row-trash-archive", **fences)["replayed"]
    with pytest.raises(OperationConflict):
        database_row_trash.set_database_row_trashed(*base,
            archived=True, operation_id="row-trash-archive",
            **{**fences, "expected_row_version": archived["row_version"]})
    with store.iam._reader() as con:
        page = store.iam._document(con, "page-e1")
        snapshot = store.iam._editing_snapshot(con, page)
        rows = next(item["rows"] for item in snapshot["databases"]
            if item["id"] == "database-e1")
        assert all(item["id"] != "row-e1" for item in rows)
        assert relation_projection(con, snapshot) == ([], 0)
        assert page["content_sha256"] == _sha256(_canonical_json(snapshot))
        relation_property = con.execute(
            "SELECT * FROM workspace_collaboration_database_properties "
            "WHERE id='row-trash-related'").fetchone()
        with pytest.raises(NotFoundOrDenied):
            store.iam._clean_database_cell_value(con,
                actor_subject_id="owner", property_row=relation_property,
                value=["row-e1"], previous_value=None)
    with _connection(store.db_path) as con:
        assert con.execute("SELECT * FROM workspace_collaboration_database_cells "
            "WHERE row_id='row-e1' ORDER BY property_id").fetchall() == cells_before
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_database_row_trash_state "
            "WHERE row_id='row-e1'").fetchone()[0] == 1
    restore_fences = {**fences,
        "expected_document_sequence": archived["document_sequence"],
        "expected_row_version": archived["row_version"]}
    with pytest.raises(DocumentSequenceConflict):
        database_row_trash.set_database_row_trashed(*base,
            archived=False, operation_id="row-trash-stale-restore", **fences)
    with pytest.raises(InjectedFailure):
        database_row_trash.set_database_row_trashed(*base,
            archived=False, operation_id="row-trash-restore-fault",
            fault="after_domain", **restore_fences)
    with _connection(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_database_row_trash_state "
            "WHERE row_id='row-e1'").fetchone()[0] == 1
    restored = database_row_trash.set_database_row_trashed(*base,
        archived=False, operation_id="row-trash-restore", **restore_fences)
    assert not restored["archived"] and not restored["replayed"]
    assert database_row_trash.list_trashed_database_rows(
        store.iam, "owner", workspace_id, "page-e1", "database-e1")["count"] == 0
    assert database_row_trash.read_database_row_trash_result(*base,
        archived=False, operation_id="row-trash-restore",
        **restore_fences)["event_id"] == restored["event_id"]
    assert len(store.iam.list_reminders("content-editor", workspace_id)["items"]) == 1
    assert "2027" in store.iam.export_csv(
        "owner", "page-e1", block_id="owned-db-source-block")["csv"]
    assert {item["id"] for item in store.iam.list_comments(
        "owner", "page-e1", item_target=item_target)["items"]} == {
        "row-trash-root-comment", "row-trash-reply-comment"}
    assert any(item["comment_id"] == "row-trash-root-comment" for item in
        store.iam.list_inbox("content-editor", workspace_id=workspace_id)["items"])
    assert database_row_trash.set_database_row_trashed(*base,
        archived=False, operation_id="row-trash-restore", **restore_fences)["replayed"]
    with store.iam._reader() as con:
        page = store.iam._document(con, "page-e1")
        snapshot = store.iam._editing_snapshot(con, page)
        rows = next(item["rows"] for item in snapshot["databases"]
            if item["id"] == "database-e1")
        assert next(item for item in rows if item["id"] == "row-e1") == {
            **original_row, "version": restored["row_version"]}
        assert relation_projection(con, snapshot) == (["row-e1"], 1)
        relation_property = con.execute(
            "SELECT * FROM workspace_collaboration_database_properties "
            "WHERE id='row-trash-related'").fetchone()
        assert store.iam._clean_database_cell_value(con,
            actor_subject_id="owner", property_row=relation_property,
            value=["row-e1"], previous_value=None) == ["row-e1"]
        assert page["content_sha256"] == _sha256(_canonical_json(snapshot))
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_database_row_trash_state "
            "WHERE row_id='row-e1'").fetchone()[0] == 0
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        editor_fences = {**fences,
            "expected_document_sequence": int(page["document_sequence"]),
            "expected_row_version": restored["row_version"]}
    editor_archived = database_row_trash.set_database_row_trashed(
        store.iam, "content-editor", workspace_id, "page-e1",
        "database-e1", "row-e1", archived=True,
        operation_id="row-trash-editor-archive", **editor_fences)
    assert editor_archived["effective_role"] == "edit_content"
    editor_restore_fences = {**editor_fences,
        "expected_document_sequence": editor_archived["document_sequence"],
        "expected_row_version": editor_archived["row_version"]}
    database_row_trash.set_database_row_trashed(*base, archived=False,
        operation_id="row-trash-editor-cycle-restore", **editor_restore_fences)


def test_ui_r5_database_row_trash_files_hide_exclusive_and_keep_shared(
        activity_environment, monkeypatch):
    store, workspace_id = activity_environment
    owned_database_block(store)
    exclusive = upload(store, operation="row-trash-exclusive-upload")["file_id"]
    shared = upload(store, operation="row-trash-shared-upload")["file_id"]
    page_shared, _png = _upload_png(store, "row-trash-page-shared-icon")
    _schema(store.iam, "database.property.create", "row-trash-files",
        {"name": "Attachments", "type": "files", "config": {}},
        "row-trash-files-property")
    store.iam.execute_typed_command("owner", "page-e1",
        command_kind="database.cell.set", target_id="row-e1:row-trash-files",
        payload={"row_id": "row-e1", "property_id": "row-trash-files",
            "value": [{"file_id": exclusive}, {"file_id": shared},
                {"file_id": page_shared}]},
        expected_target_version=0, operation_id="row-trash-files-cell",
        **command_request(store))
    store.iam.execute_typed_command("owner", "page-e1",
        command_kind="block.insert", target_id="row-trash-shared-active-block",
        payload={"type": "file", "content": "Shared attachment",
            "props": {"file_id": shared}, "after_block_id": "owned-db-source-block"},
        operation_id="row-trash-shared-active-block", **command_request(store))
    update_page_appearance(store.iam, "owner", workspace_id, "page-e1",
        **appearance_request(store.iam, "nf:" + page_shared,
            "row-trash-page-icon"))
    _apply(database_trash_migration, store.db_path, store.user_data_root,
        "before-row-trash-file-source.db")
    _apply(migration, store.db_path, store.user_data_root,
        "before-row-trash-files.db")

    def staged_connect(*, query_only):
        con = sqlite3.connect(
            f"{Path(store.db_path).as_uri()}?mode=ro" if query_only else store.db_path,
            uri=query_only, timeout=5.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        if query_only:
            con.execute("PRAGMA query_only=ON")
        assert database_row_trash.installed(con)
        return con

    monkeypatch.setattr(store.iam, "_connect", staged_connect)
    assert store.iam.get_file("owner", exclusive)["file_id"] == exclusive
    assert store.iam.get_file("owner", shared)["file_id"] == shared
    assert store.iam.get_file("owner", page_shared)["file_id"] == page_shared
    with store.iam._reader() as con:
        page = store.iam._document(con, "page-e1")
        workspace = store.iam._workspace(con, workspace_id)
        database = con.execute("SELECT row_version FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone()
        row = con.execute("SELECT row_version FROM workspace_collaboration_database_rows "
            "WHERE id='row-e1'").fetchone()
        fences = {"expected_document_sequence": int(page["document_sequence"]),
            "expected_workspace_epoch": int(workspace["security_epoch"]),
            "expected_document_epoch": int(page["access_epoch"]),
            "expected_database_version": int(database["row_version"]),
            "expected_row_version": int(row["row_version"])}
    base = (store.iam, "owner", workspace_id, "page-e1", "database-e1", "row-e1")
    archived = database_row_trash.set_database_row_trashed(*base, archived=True,
        operation_id="row-trash-files-archive", **fences)
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", exclusive)
    assert store.iam.get_file("owner", shared)["file_id"] == shared
    assert store.iam.get_file("owner", page_shared)["file_id"] == page_shared
    listed = {item["id"] for item in store.iam.list_files("owner", "page-e1")["files"]}
    assert exclusive not in listed and {shared, page_shared} <= listed
    with store.iam._reader() as con:
        page = store.iam._document(con, "page-e1")
        source = CopySourceDocument("page-e1", None, 0, (),
            _canonical_json(store.iam._editing_snapshot(con, page)),
            _canonical_json([dict(item) for item in con.execute(
                "SELECT * FROM workspace_collaboration_files "
                "WHERE document_id='page-e1' AND state='active' ORDER BY id")] ))
        filtered = _exclude_archived_row_only_files(con, source)
        copied_file_ids = {item["id"] for item in json.loads(filtered.files_json)}
        assert exclusive not in copied_file_ids
        assert {shared, page_shared} <= copied_file_ids
    page = store.iam.read_document("owner", "page-e1")
    copied_page = page_copy.copy_page(store.iam, "owner", workspace_id,
        "page-e1", operation_id="row-trash-page-copy",
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"])["page"]["id"]
    copied_databases = store.iam.read_databases("owner", copied_page)
    assert len(copied_databases) == 1 and copied_databases[0]["rows"] == []
    with store.iam._reader() as con:
        copied_sources = {str(item[0]) for item in con.execute(
            "SELECT source_file_id FROM workspace_collaboration_files "
            "WHERE document_id=? AND origin_kind='copy'", (copied_page,)).fetchall()}
        assert exclusive not in copied_sources
        assert {shared, page_shared} <= copied_sources
    for file_id in (exclusive, shared, page_shared):
        with pytest.raises(NotFoundOrDenied):
            store.iam.delete_file("owner", file_id,
                expected_document_sequence=archived["document_sequence"],
                expected_workspace_epoch=archived["workspace_epoch"],
                expected_document_epoch=archived["document_epoch"],
                operation_id=f"row-trash-deny-file-delete-{file_id}")
    with store.iam._reader() as con:
        page = store.iam._document(con, "page-e1")
        workspace = store.iam._workspace(con, workspace_id)
        restore_fences = {**fences,
            "expected_document_sequence": int(page["document_sequence"]),
            "expected_workspace_epoch": int(workspace["security_epoch"]),
            "expected_document_epoch": int(page["access_epoch"]),
            "expected_row_version": archived["row_version"]}
    database_row_trash.set_database_row_trashed(*base, archived=False,
        operation_id="row-trash-files-restore", **restore_fences)
    assert store.iam.get_file("owner", exclusive)["file_id"] == exclusive
    assert {exclusive, shared, page_shared}.issubset({item["id"] for item in
        store.iam.list_files("owner", "page-e1")["files"]})


def test_ui_r5_archived_row_move_preserves_restore_version_and_resources(
        activity_environment, monkeypatch):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    file_id, png = _upload_png(store, "row-trash-move-file")
    _schema(store.iam, "database.property.create", "row-trash-move-files",
        {"name": "Attachments", "type": "files", "config": {}},
        "row-trash-move-property")
    store.iam.execute_typed_command("owner", "page-e1",
        command_kind="database.cell.set", target_id="row-e1:row-trash-move-files",
        payload={"row_id": "row-e1", "property_id": "row-trash-move-files",
            "value": [{"file_id": file_id}]}, expected_target_version=0,
        operation_id="row-trash-move-cell", **command_request(store))
    page = store.iam.read_document("owner", "page-e1")
    store.iam.add_comment("owner", "page-e1", "row-trash-move-comment",
        content="Retain this row discussion",
        anchor={"kind": "database_row", "database_id": "database-e1",
            "row_id": "row-e1"},
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="row-trash-move-comment-add")
    with _connection(store.db_path) as con:
        con.execute("INSERT INTO workspace_collaboration_database_row_bodies "
            "(row_id,blocks_json) VALUES('row-e1',?)", (_canonical_json([
                {"id": "row-trash-move-body", "type": "file", "content": "",
                 "props": {"file_id": file_id}}]),))
        con.execute("INSERT INTO workspace_collaboration_database_row_appearance "
            "(row_id,appearance_json) VALUES('row-e1',?)",
            (_canonical_json({"icon": "nf:" + file_id, "cover": "",
                "cover_position": 0.5}),))
    _apply(database_trash_migration, store.db_path, store.user_data_root,
        "before-row-move-trash-source.db")
    _apply(migration, store.db_path, store.user_data_root,
        "before-row-move-trash.db")

    def staged_connect(*, query_only):
        con = sqlite3.connect(
            f"{Path(store.db_path).as_uri()}?mode=ro" if query_only else store.db_path,
            uri=query_only, timeout=5.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        if query_only:
            con.execute("PRAGMA query_only=ON")
        return con

    monkeypatch.setattr(store.iam, "_connect", staged_connect)
    with store.iam._reader() as con:
        page = store.iam._document(con, "page-e1")
        workspace = store.iam._workspace(con, workspace_id)
        database = con.execute("SELECT row_version FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone()
        row = con.execute("SELECT row_version FROM workspace_collaboration_database_rows "
            "WHERE id='row-e1'").fetchone()
        fences = {"expected_document_sequence": int(page["document_sequence"]),
            "expected_workspace_epoch": int(workspace["security_epoch"]),
            "expected_document_epoch": int(page["access_epoch"]),
            "expected_database_version": int(database["row_version"]),
            "expected_row_version": int(row["row_version"])}
    archived = database_row_trash.set_database_row_trashed(
        store.iam, "owner", workspace_id, "page-e1", "database-e1", "row-e1",
        archived=True, operation_id="row-trash-move-archive", **fences)
    with store.iam._reader() as con:
        page = store.iam._document(con, "page-e1")
        snapshot = store.iam._editing_snapshot(con, page)
        database = next(item for item in snapshot["databases"]
            if item["id"] == "database-e1")
        assert all(row["id"] != "row-e1" for row in database["rows"])
        block = next(item for item in snapshot["blocks"]
            if item["id"] == "owned-db-source-block")
        plan = database_move._database_file_plan(con, "owner", workspace_id,
            "page-e1", "page-db-destination", database, block,
            "row-trash-move-operation")
    assert {item[0] for item in plan["files"]} == {file_id}
    assert {item[0] for item in plan["cells"]} == {"row-e1"}
    assert len(plan["rows"]) == 1 and plan["rows"][0][-1] is True
    copied_id = plan["files"][0][1]
    con = staged_connect(query_only=False)
    try:
        con.execute("BEGIN")
        database_move._copy_database_files(con, plan, "owner", workspace_id,
            "page-e1", "page-db-destination", "row-trash-move-operation",
            frozenset(), int(time.time()))
        membership = con.execute("SELECT archived_row_version FROM "
            "workspace_collaboration_database_row_trash_state WHERE row_id='row-e1'").fetchone()
        current = con.execute("SELECT row_version FROM workspace_collaboration_database_rows "
            "WHERE id='row-e1'").fetchone()
        assert int(current[0]) == archived["row_version"]
        assert int(membership[0]) + 1 == int(current[0])
        assert con.execute("SELECT state FROM workspace_collaboration_files "
            "WHERE id=?", (file_id,)).fetchone()[0] == "deleted"
        assert con.execute("SELECT document_id,state FROM workspace_collaboration_files "
            "WHERE id=?", (copied_id,)).fetchone()[:] == (
                "page-db-destination", "active")
        assert json.loads(con.execute("SELECT value_json FROM "
            "workspace_collaboration_database_cells WHERE row_id='row-e1' "
            "AND property_id='row-trash-move-files'").fetchone()[0]) == [
                {"file_id": copied_id}]
        assert json.loads(con.execute("SELECT blocks_json FROM "
            "workspace_collaboration_database_row_bodies WHERE row_id='row-e1'").fetchone()[0]
            )[0]["props"]["file_id"] == copied_id
        assert json.loads(con.execute("SELECT appearance_json FROM "
            "workspace_collaboration_database_row_appearance WHERE row_id='row-e1'").fetchone()[0]
            )["icon"] == "nf:" + copied_id
    finally:
        con.rollback()
        con.close()

    # Only the move service accepts exact 2423 here; the runtime stays closed.
    move_fences = move_request(store)
    with pytest.raises(NotFoundOrDenied):
        database_move.move_owned_database(store.iam, "content-editor", workspace_id,
            "page-e1", "page-db-destination", "database-e1",
            operation_id="row-trash-move-editor-denied", **move_fences)
    with pytest.raises(InjectedFailure):
        database_move.move_owned_database(store.iam, "owner", workspace_id,
            "page-e1", "page-db-destination", "database-e1",
            operation_id="row-trash-move-fault", fault="after_domain",
            **move_fences)
    with store.iam._reader() as con:
        assert con.execute("SELECT document_id FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone()[0] == "page-e1"
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_files "
            "WHERE copy_operation_id='row-trash-move-fault'").fetchone()[0] == 0
        assert con.execute("SELECT row_version FROM workspace_collaboration_database_rows "
            "WHERE id='row-e1'").fetchone()[0] == archived["row_version"]
    moved = database_move.move_owned_database(store.iam, "owner", workspace_id,
        "page-e1", "page-db-destination", "database-e1",
        operation_id="row-trash-move-operation", **move_fences)
    assert moved["copied_file_count"] == 1
    assert moved["retargeted_comment_count"] == 1
    assert database_move.move_owned_database(store.iam, "owner", workspace_id,
        "page-e1", "page-db-destination", "database-e1",
        operation_id="row-trash-move-operation", **move_fences) == {
            **moved, "replayed": True}
    assert database_move.read_owned_database_move_result(store.iam, "owner",
        workspace_id, "page-e1", "page-db-destination", "database-e1",
        operation_id="row-trash-move-operation", **move_fences)["state"] == "committed"
    with store.iam._reader() as con:
        assert con.execute("SELECT document_id FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone()[0] == "page-db-destination"
        assert con.execute("SELECT document_id FROM workspace_collaboration_comments "
            "WHERE id='row-trash-move-comment'").fetchone()[0] == "page-db-destination"
        membership = con.execute("SELECT archived_row_version FROM "
            "workspace_collaboration_database_row_trash_state "
            "WHERE row_id='row-e1'").fetchone()
        current = con.execute("SELECT row_version FROM workspace_collaboration_database_rows "
            "WHERE id='row-e1'").fetchone()
        assert int(membership[0]) + 1 == int(current[0]) == archived["row_version"]
        database_version = int(con.execute("SELECT row_version FROM "
            "workspace_collaboration_databases WHERE id='database-e1'").fetchone()[0])
    assert database_row_trash.list_trashed_database_rows(store.iam, "owner",
        workspace_id, "page-db-destination", "database-e1")["items"][0]["row_id"] == "row-e1"
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", copied_id)
    assert store.iam.read_database("owner", "database-e1")["rows"] == []
    row_target = {"kind": "database_row", "database_id": "database-e1",
        "row_id": "row-e1"}
    assert "row-trash-move-comment" not in {item["id"] for item in
        store.iam.list_comments("owner", "page-db-destination")["items"]}
    with pytest.raises(NotFoundOrDenied):
        store.iam.list_comments("owner", "page-db-destination",
            item_target=row_target)
    restored = database_row_trash.set_database_row_trashed(
        store.iam, "owner", workspace_id, "page-db-destination",
        "database-e1", "row-e1", archived=False,
        operation_id="row-trash-move-restore",
        expected_document_sequence=moved["document_sequence"],
        expected_workspace_epoch=moved["workspace_epoch"],
        expected_document_epoch=moved["destination_document_epoch"],
        expected_database_version=database_version,
        expected_row_version=archived["row_version"])
    assert not restored["archived"]
    assert any(row["id"] == "row-e1" for row in
        store.iam.read_database("owner", "database-e1")["rows"])
    assert store.iam.get_file("owner", copied_id)["path"].read_bytes() == png
    assert {item["id"] for item in store.iam.list_comments(
        "owner", "page-db-destination", item_target=row_target)["items"]} == {
                "row-trash-move-comment"}

    with store.iam._reader() as con:
        page = store.iam._document(con, "page-db-destination")
        workspace = store.iam._workspace(con, workspace_id)
        database = con.execute("SELECT row_version FROM "
            "workspace_collaboration_databases WHERE id='database-e1'").fetchone()
        row = con.execute("SELECT row_version FROM "
            "workspace_collaboration_database_rows WHERE id='row-e1'").fetchone()
        nested_fences = {"expected_document_sequence": int(page["document_sequence"]),
            "expected_workspace_epoch": int(workspace["security_epoch"]),
            "expected_document_epoch": int(page["access_epoch"]),
            "expected_database_version": int(database["row_version"]),
            "expected_row_version": int(row["row_version"])}
    row_archived_again = database_row_trash.set_database_row_trashed(
        store.iam, "owner", workspace_id, "page-db-destination",
        "database-e1", "row-e1", archived=True,
        operation_id="row-trash-move-rearchive", **nested_fences)
    database_archived = database_trash.set_owned_database_trashed(
        store.iam, "owner", workspace_id, "page-db-destination",
        "database-e1", archived=True, operation_id="row-trash-move-db-archive",
        expected_document_sequence=row_archived_again["document_sequence"],
        expected_workspace_epoch=row_archived_again["workspace_epoch"],
        expected_document_epoch=row_archived_again["document_epoch"],
        expected_database_version=row_archived_again["database_version"])
    assert any(item["database_id"] == "database-e1" for item in
        database_trash.list_trashed_owned_databases(
            store.iam, "owner", workspace_id)["items"])
    database_restored = database_trash.set_owned_database_trashed(
        store.iam, "owner", workspace_id, "page-db-destination",
        "database-e1", archived=False, operation_id="row-trash-move-db-restore",
        expected_document_sequence=database_archived["document_sequence"],
        expected_workspace_epoch=database_archived["workspace_epoch"],
        expected_document_epoch=database_archived["document_epoch"],
        expected_database_version=database_archived["database_version"])
    assert database_row_trash.list_trashed_database_rows(store.iam, "owner",
        workspace_id, "page-db-destination", "database-e1")["count"] == 1
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", copied_id)
    database_row_trash.set_database_row_trashed(
        store.iam, "owner", workspace_id, "page-db-destination",
        "database-e1", "row-e1", archived=False,
        operation_id="row-trash-move-final-restore",
        expected_document_sequence=database_restored["document_sequence"],
        expected_workspace_epoch=database_restored["workspace_epoch"],
        expected_document_epoch=database_restored["document_epoch"],
        expected_database_version=database_restored["database_version"],
        expected_row_version=row_archived_again["row_version"])
    assert store.iam.get_file("owner", copied_id)["path"].read_bytes() == png


@pytest.mark.parametrize("other_lifecycle", ("row", "database"))
def test_ui_r5_database_move_retains_file_shared_by_other_trash_member(
        activity_environment, monkeypatch, other_lifecycle):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    file_id, png = _upload_png(store, "row-trash-shared-move-file")
    _schema(store.iam, "database.property.create", "row-trash-source-files",
        {"name": "Attachments", "type": "files", "config": {}},
        "row-trash-source-property")
    store.iam.execute_typed_command("owner", "page-e1",
        command_kind="database.cell.set", target_id="row-e1:row-trash-source-files",
        payload={"row_id": "row-e1", "property_id": "row-trash-source-files",
            "value": [{"file_id": file_id}]}, expected_target_version=0,
        operation_id="row-trash-source-cell", **command_request(store))

    def schema_other(kind, target, payload, operation):
        page = store.iam.read_document("owner", "page-e1")
        return store.iam.execute_schema_command("owner", "page-e1",
            database_id="row-trash-other-db", command_kind=kind,
            target_id=target, payload=payload,
            expected_document_sequence=page["document_sequence"],
            expected_workspace_epoch=page["workspace_epoch"],
            expected_document_epoch=page["document_epoch"],
            expected_target_version=0, operation_id=operation)

    schema_other("database.create", "row-trash-other-db",
        {"title": "Other database"}, "row-trash-other-db-create")
    store.iam.execute_typed_command("owner", "page-e1",
        command_kind="block.insert", target_id="row-trash-other-block",
        payload={"type": "table", "content": "",
            "props": {"database_id": "row-trash-other-db"},
            "after_block_id": "owned-db-source-block"},
        operation_id="row-trash-other-block-insert", **command_request(store))
    schema_other("database.property.create", "row-trash-other-files",
        {"name": "Attachments", "type": "files", "config": {}},
        "row-trash-other-property")
    page = store.iam.read_document("owner", "page-e1")
    store.iam.execute_surface_command("owner", "page-e1",
        database_id="row-trash-other-db", command_kind="database.row.create",
        target_id="row-trash-other-row", payload={},
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        expected_target_version=0, operation_id="row-trash-other-row-create")
    store.iam.execute_typed_command("owner", "page-e1",
        command_kind="database.cell.set",
        target_id="row-trash-other-row:row-trash-other-files",
        payload={"row_id": "row-trash-other-row",
            "property_id": "row-trash-other-files",
            "value": [{"file_id": file_id}]}, expected_target_version=0,
        operation_id="row-trash-other-cell", **command_request(store))

    _apply(database_trash_migration, store.db_path, store.user_data_root,
        "before-row-shared-move-trash-source.db")
    _apply(migration, store.db_path, store.user_data_root,
        "before-row-shared-move-trash.db")

    def staged_connect(*, query_only):
        con = sqlite3.connect(
            f"{Path(store.db_path).as_uri()}?mode=ro" if query_only else store.db_path,
            uri=query_only, timeout=5.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        if query_only:
            con.execute("PRAGMA query_only=ON")
        return con

    monkeypatch.setattr(store.iam, "_connect", staged_connect)

    def row_fences(database_id, row_id):
        with store.iam._reader() as con:
            page = store.iam._document(con, "page-e1")
            workspace = store.iam._workspace(con, workspace_id)
            database = con.execute("SELECT row_version FROM "
                "workspace_collaboration_databases WHERE id=?",
                (database_id,)).fetchone()
            row = con.execute("SELECT row_version FROM "
                "workspace_collaboration_database_rows WHERE id=?",
                (row_id,)).fetchone()
            return {"expected_document_sequence": int(page["document_sequence"]),
                "expected_workspace_epoch": int(workspace["security_epoch"]),
                "expected_document_epoch": int(page["access_epoch"]),
                "expected_database_version": int(database["row_version"]),
                "expected_row_version": int(row["row_version"])}

    database_row_trash.set_database_row_trashed(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", "row-e1", archived=True,
        operation_id="row-trash-shared-move-source-archive",
        **row_fences("database-e1", "row-e1"))
    other_fences = row_fences("row-trash-other-db", "row-trash-other-row")
    if other_lifecycle == "row":
        database_row_trash.set_database_row_trashed(store.iam, "owner", workspace_id,
            "page-e1", "row-trash-other-db", "row-trash-other-row", archived=True,
            operation_id="row-trash-shared-move-other-archive", **other_fences)
    else:
        database_trash.set_owned_database_trashed(store.iam, "owner", workspace_id,
            "page-e1", "row-trash-other-db", archived=True,
            operation_id="row-trash-shared-move-other-db-archive",
            **{key: value for key, value in other_fences.items()
                if key != "expected_row_version"})
    moved = database_move.move_owned_database(store.iam, "owner", workspace_id,
        "page-e1", "page-db-destination", "database-e1",
        operation_id="row-trash-shared-move-operation", **move_request(store))
    assert moved["copied_file_count"] == 1
    assert moved["retired_source_file_count"] == 0
    with store.iam._reader() as con:
        assert con.execute("SELECT state,document_id FROM workspace_collaboration_files "
            "WHERE id=?", (file_id,)).fetchone()[:] == ("active", "page-e1")
        assert database_trash.trashed_file_ids(con, "page-e1") == {file_id}
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", file_id)
    restore_fences = row_fences("row-trash-other-db", "row-trash-other-row")
    if other_lifecycle == "row":
        database_row_trash.set_database_row_trashed(store.iam, "owner", workspace_id,
            "page-e1", "row-trash-other-db", "row-trash-other-row", archived=False,
            operation_id="row-trash-shared-move-other-restore", **restore_fences)
    else:
        database_trash.set_owned_database_trashed(store.iam, "owner", workspace_id,
            "page-e1", "row-trash-other-db", archived=False,
            operation_id="row-trash-shared-move-other-db-restore",
            **{key: value for key, value in restore_fences.items()
                if key != "expected_row_version"})
    assert store.iam.get_file("owner", file_id)["path"].read_bytes() == png

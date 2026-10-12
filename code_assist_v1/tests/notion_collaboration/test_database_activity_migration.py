"""UI-R5: forward, offline database activity schema on synthetic catalogs."""
from contextlib import contextmanager
import multiprocessing
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_database_activity_migration as migration
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, NotFoundOrDenied
from code_assist_v1.notion_collaboration import page_copy
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema
from code_assist_v1.tests.notion_collaboration.test_database_row_content import (
    _apply, _backup, blocks as body_blocks, checkpoint, execute as row_execute,
    request as body_request,
    restore,
)
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, copy, owned_database_block, request,
)
from code_assist_v1.tests.notion_collaboration.test_database_row_appearance import (
    request as appearance_request,
)
from code_assist_v1.tests.notion_collaboration.test_native_page_creation import (
    create as create_page,
)


@contextmanager
def _connection(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with con:
            yield con
    finally:
        con.close()


def test_ui_r5_database_activity_migration_is_atomic_and_preserves_owned_database(environment):
    store, _workspace_id = environment
    with _connection(store.db_path) as con:
        before = con.execute("SELECT * FROM workspace_collaboration_databases ORDER BY id").fetchall()
    plan = migration.build_plan(store.db_path, store.user_data_root)
    backup = _backup(store.db_path, store.user_data_root / "backups" / "before-database-activity.db")
    options = dict(exact_plan_checksum=plan["plan_checksum"], backup_confirmed=True,
        catalog_quiesced_confirmed=True, backup_path=backup)
    with pytest.raises(migration.MigrationRefused):
        migration.apply_plan(store.db_path, store.user_data_root,
            **{**options, "exact_plan_checksum": "sha256:" + "0" * 64})

    def fail(stage):
        if stage == "after_schema":
            raise RuntimeError("synthetic activity migration failure")

    with pytest.raises(RuntimeError):
        migration.apply_plan(store.db_path, store.user_data_root, **options, fault_hook=fail)
    with _connection(store.db_path) as con:
        assert con.execute("SELECT * FROM workspace_collaboration_databases ORDER BY id").fetchall() == before
        assert con.execute("SELECT name FROM sqlite_master WHERE name='workspace_collaboration_database_activity'").fetchone() is None
    migration.apply_plan(store.db_path, store.user_data_root, **options)
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"
    with _connection(store.db_path) as con:
        assert con.execute("SELECT * FROM workspace_collaboration_databases ORDER BY id").fetchall() == before
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_database_activity").fetchone()[0] == 0
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        page = con.execute("SELECT document_id FROM workspace_collaboration_databases WHERE id='database-e1'").fetchone()[0]
        con.execute("INSERT INTO workspace_collaboration_database_activity VALUES(?,?,?,?,?)",
            ("database-e1", page, "owner", 1, 1))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO workspace_collaboration_database_activity VALUES(?,?,?,?,?)",
                ("unbound-database", "other-page", "owner", 2, 2))


@pytest.fixture()
def activity_environment(environment):
    old, workspace_id = environment
    _apply(migration, old.db_path, old.user_data_root, "before-database-activity-runtime.db")
    return CollaborativeWorkspaceStore(old.db_path, user_data_root=old.user_data_root,
        identity_directory=old.identity_directory), workspace_id


def _activity(store, database_id="database-e1"):
    with _connection(store.db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM workspace_collaboration_database_activity WHERE database_id=?",
            (database_id,),
        ).fetchone()
        return dict(row) if row else None


def test_ui_r5_database_activity_follows_committed_db_edits_only(activity_environment):
    store, workspace_id = activity_environment
    assert _activity(store) is None  # Historical activity cannot be inferred.
    assert store.iam.read_database("owner", "database-e1")["last_edit"] is None
    owned_database_block(store)
    assert _activity(store) is None  # A block reference is not a DB edit.

    schema = _schema(store.iam, "database.property.create", "activity-property",
        {"name": "Activity", "type": "text", "config": {}}, "activity-schema", actor="owner")
    first = _activity(store)
    assert first["last_editor_subject_id"] == "owner"
    assert first["document_sequence"] == schema["document_sequence"]
    assert first["last_edited_at"] > 0

    body = row_execute(store.iam, body_request(store.iam, "activity-body"))
    second = _activity(store)
    assert second["last_editor_subject_id"] == "content-editor"
    assert second["document_sequence"] == body["document_sequence"]

    row = store.iam.read_database("owner", "database-e1")["rows"][0]
    cell = store.iam.execute_typed_command("owner", "page-e1",
        command_kind="database.cell.set", target_id=f"{row['id']}:activity-property",
        payload={"row_id": row["id"], "property_id": "activity-property", "value": "edited"},
        expected_target_version=0, operation_id="activity-cell", **request(store))
    third = _activity(store)
    assert third["last_editor_subject_id"] == "owner"
    assert third["document_sequence"] == cell["document_sequence"]
    assert store.iam.read_database("content-editor", "database-e1")["last_edit"] == {
        "actor_subject_id": "owner", "edited_at": third["last_edited_at"],
        "document_sequence": third["document_sequence"],
    }
    assert store.iam.read_databases("owner", "page-e1")[0]["last_edit"]["document_sequence"] == cell["document_sequence"]

    batch_fences = request(store)
    batch = store.iam.execute_cell_batch("content-editor", "page-e1",
        database_id="database-e1", target_id="database-e1",
        payload={"cells": [{"row_id": row["id"], "property_id": "activity-property",
            "value": "batch", "expected_target_version": 1}]},
        operation_id="activity-batch", **batch_fences)
    assert _activity(store)["last_editor_subject_id"] == "content-editor"
    assert _activity(store)["document_sequence"] == batch["document_sequence"]
    assert store.iam.execute_cell_batch("content-editor", "page-e1",
        database_id="database-e1", target_id="database-e1",
        payload={"cells": [{"row_id": row["id"], "property_id": "activity-property",
            "value": "batch", "expected_target_version": 1}]},
        operation_id="activity-batch", **batch_fences) == batch

    renamed = store.iam.execute_typed_command("owner", "page-e1",
        command_kind="database.property.rename", target_id="activity-property",
        payload={"name": "Renamed activity"}, expected_target_version=1,
        operation_id="activity-rename", **request(store))
    assert _activity(store)["document_sequence"] == renamed["document_sequence"]
    appearance = row_execute(store.iam, appearance_request(store.iam, "activity-appearance"))
    third = _activity(store)
    assert third["last_editor_subject_id"] == "content-editor"
    assert third["document_sequence"] == appearance["document_sequence"]

    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert",
        target_id="activity-unrelated", payload={"type": "paragraph", "content": "unrelated",
            "after_block_id": None}, operation_id="activity-unrelated-block", **request(store))
    assert _activity(store) == third

    copy_fences = request(store)
    copied = copy(store, workspace_id, operation="activity-copy", fences=copy_fences)
    copy_activity = _activity(store, copied["database_id"])
    assert copy_activity["last_editor_subject_id"] == "owner"
    assert copy_activity["document_sequence"] == copied["document_sequence"]
    assert store.iam.read_database("owner", copied["database_id"])["last_edit"]["actor_subject_id"] == "owner"
    assert _activity(store) == third
    assert copy(store, workspace_id, operation="activity-copy", fences=copy_fences)["replayed"] is True
    assert _activity(store, copied["database_id"]) == copy_activity

    with pytest.raises(InjectedFailure):
        store.iam.execute_schema_command("owner", "page-e1", database_id="database-e1",
            command_kind="database.property.create", target_id="activity-rolled-back",
            payload={"name": "Rolled back", "type": "text", "config": {}},
            operation_id="activity-rollback", fault="after_event", **request(store))
    assert _activity(store) == third
    with _connection(store.db_path) as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []

    created = store.iam.execute_schema_command("owner", "page-e1",
        database_id="activity-new-database", command_kind="database.create",
        target_id="activity-new-database", payload={"title": "New database"},
        operation_id="activity-database-create", **request(store))
    assert _activity(store, "activity-new-database")["document_sequence"] == created["document_sequence"]
    assert _activity(store) == third

    current = store.iam.read_document("owner", "page-e1")
    store.iam.revoke_page_grant("owner", "page-e1", "subject", "content-editor",
        expected_workspace_epoch=current["workspace_epoch"],
        expected_document_epoch=current["document_epoch"],
        operation_id="activity-revoke-content-editor")
    with pytest.raises(NotFoundOrDenied):
        store.iam.read_database("content-editor", "database-e1")


def test_ui_r5_database_activity_restore_and_page_copy(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    before_body = checkpoint(store.iam, "activity-before-body")
    row_execute(store.iam, body_request(store.iam, "activity-body-for-restore"))
    body_activity = _activity(store)
    assert body_activity["last_editor_subject_id"] == "content-editor"

    restored = restore(store.iam, before_body["revision"], "activity-restore-body")
    restored_activity = _activity(store)
    assert restored_activity["last_editor_subject_id"] == "owner"
    assert restored_activity["document_sequence"] == restored["document_sequence"]

    unchanged = checkpoint(store.iam, "activity-before-unrelated-restore")
    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert",
        target_id="activity-unrelated-restore", payload={"type": "paragraph",
            "content": "unrelated restore", "after_block_id": None},
        operation_id="activity-unrelated-before-restore", **request(store))
    restore(store.iam, unchanged["revision"], "activity-restore-unrelated")
    assert _activity(store) == restored_activity

    copy_fences = request(store)
    copied = page_copy.copy_page(store.iam, "owner", workspace_id, "page-e1",
        operation_id="activity-page-copy", **copy_fences)
    copied_page_id = copied["page"]["id"]
    copied_database = store.iam.read_databases("owner", copied_page_id)[0]
    copied_activity = _activity(store, copied_database["id"])
    assert copied_activity["document_id"] == copied_page_id
    assert copied_activity["last_editor_subject_id"] == "owner"
    assert copied_activity["document_sequence"] == 1
    assert _activity(store) == restored_activity
    assert page_copy.copy_page(store.iam, "owner", workspace_id, "page-e1",
        operation_id="activity-page-copy", **copy_fences)["replayed"] is True
    assert _activity(store, copied_database["id"]) == copied_activity


def _activity_writer(catalog, root, actor, command, barrier, queue):
    try:
        store = CollaborativeWorkspaceStore(catalog, user_data_root=root,
            identity_directory=InMemoryIdentityDirectory(["owner", "content-editor", "schema-editor"]))
        barrier.wait(30)
        result = row_execute(store.iam, command, actor=actor)
        queue.put(("committed", actor, result["document_sequence"]))
    except DocumentSequenceConflict as exc:
        queue.put(("conflict", actor, exc.current_document_sequence))
    except Exception as exc:
        queue.put((type(exc).__name__, actor, str(exc)))


def test_ui_r5_database_activity_concurrent_processes_keep_winning_editor(activity_environment):
    store, _workspace_id = activity_environment
    first = body_request(store.iam, "activity-concurrent-content")
    second = {**first, "operation_id": "activity-concurrent-owner",
        "payload": {"blocks": body_blocks("owner writes")}}
    context = multiprocessing.get_context("spawn")
    barrier, queue = context.Barrier(2), context.Queue()
    processes = [
        context.Process(target=_activity_writer, args=(store.db_path, store.user_data_root,
            actor, command, barrier, queue))
        for actor, command in (("content-editor", first), ("owner", second))
    ]
    try:
        for process in processes:
            process.start()
        results = [queue.get(timeout=60) for _ in processes]
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        assert sorted(result[0] for result in results) == ["committed", "conflict"]
        winner = next(result for result in results if result[0] == "committed")
        activity = _activity(store)
        assert activity["last_editor_subject_id"] == winner[1]
        assert activity["document_sequence"] == winner[2]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(10)
        queue.close()
        queue.join_thread()


def test_ui_r5_database_template_creation_starts_with_creator_activity(activity_environment):
    store, workspace_id = activity_environment
    created = create_page(store, workspace_id, template="database",
        operation="activity-template-create")
    document_id = created["page"]["id"]
    database = store.iam.read_databases("owner", document_id)[0]
    activity = _activity(store, database["id"])
    assert activity["document_id"] == document_id
    assert activity["last_editor_subject_id"] == "owner"
    assert activity["document_sequence"] == 1

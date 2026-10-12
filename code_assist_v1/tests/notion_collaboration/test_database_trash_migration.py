"""UI-R5: offline, reversible database Trash membership on synthetic catalogs."""
import json
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_database_trash_migration as migration
from code_assist_v1.notion_collaboration import database_trash, linked_databases
from code_assist_v1.notion_collaboration import search_projection
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, NotFoundOrDenied
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import (
    _connection, activity_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_row_content import _apply, _backup
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, owned_database_block,
)


def test_ui_r5_database_trash_migration_is_atomic_and_preserves_owned_database(
        activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    document = store.iam.read_document("owner", "page-e1")
    block = next(item for item in document["blocks"]
        if item["id"] == "owned-db-source-block")
    store.iam.add_comment("owner", "page-e1", "db-trash-owned-block-comment",
        content="Keep this discussion", block_id=block["id"],
        operation_id="db-trash-migration-comment",
        expected_workspace_epoch=document["workspace_epoch"],
        expected_document_epoch=document["document_epoch"])
    with _connection(store.db_path) as con:
        before_database = con.execute("SELECT * FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone()
        before_block = con.execute("SELECT * FROM workspace_collaboration_blocks "
            "WHERE id='owned-db-source-block'").fetchone()
        before_comment = con.execute("SELECT * FROM workspace_collaboration_comments "
            "WHERE id='db-trash-owned-block-comment'").fetchone()
    plan = migration.build_plan(store.db_path, store.user_data_root)
    backup = _backup(store.db_path,
        store.user_data_root / "backups" / "before-database-trash.db")
    options = dict(exact_plan_checksum=plan["plan_checksum"], backup_confirmed=True,
        catalog_quiesced_confirmed=True, backup_path=backup)
    with pytest.raises(migration.MigrationRefused):
        migration.apply_plan(store.db_path, store.user_data_root,
            **{**options, "exact_plan_checksum": "sha256:" + "0" * 64})

    def fail(stage):
        if stage == "after_schema":
            raise RuntimeError("synthetic database Trash migration failure")

    with pytest.raises(RuntimeError):
        migration.apply_plan(store.db_path, store.user_data_root,
            **options, fault_hook=fail)
    with _connection(store.db_path) as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE "
            "name='workspace_collaboration_database_trash_state'").fetchone() is None
        assert con.execute("SELECT * FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone() == before_database

    migration.apply_plan(store.db_path, store.user_data_root, **options)
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"
    reopened = CollaborativeWorkspaceStore(store.db_path,
        user_data_root=store.user_data_root,
        identity_directory=store.identity_directory)
    assert reopened.iam.read_document("owner", "page-e1")["document_id"] == "page-e1"
    active_database = reopened.iam.read_database("owner", "database-e1")
    assert active_database["id"] == "database-e1"
    assert active_database["capabilities"]["row_content"]
    assert "last_edit" in active_database
    with _connection(store.db_path) as con:
        assert con.execute("SELECT * FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone() == before_database
        assert con.execute("SELECT * FROM workspace_collaboration_blocks "
            "WHERE id='owned-db-source-block'").fetchone() == before_block
        assert con.execute("SELECT * FROM workspace_collaboration_comments "
            "WHERE id='db-trash-owned-block-comment'").fetchone() == before_comment
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_database_trash_state"
            ).fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_database_trash_block_comments"
            ).fetchone()[0] == 0
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        values = ("database-e1", workspace_id, "page-e1", block["id"],
            json.dumps(block), document["blocks"].index(block),
            before_block[6], before_block[7], "{}",
            before_database[3], "owner", 1, "synthetic-db-trash")
        con.execute("INSERT INTO workspace_collaboration_database_trash_state "
            "(database_id,workspace_id,document_id,block_id,block_snapshot_json,"
            "source_position,block_row_version,block_created_at,restore_anchor_json,"
            "database_row_version,archived_by_subject_id,archived_at,archive_operation_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
        con.execute("INSERT INTO workspace_collaboration_database_trash_block_comments "
            "(database_id,comment_id) VALUES(?,?)",
            ("database-e1", "db-trash-owned-block-comment"))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO workspace_collaboration_database_trash_block_comments "
                "(database_id,comment_id) VALUES(?,?)",
                ("database-e1", "missing-comment"))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO workspace_collaboration_database_trash_state "
                "(database_id,workspace_id,document_id,block_id,block_snapshot_json,"
                "source_position,block_row_version,block_created_at,"
                "database_row_version,archived_by_subject_id,archived_at,"
                "archive_operation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("missing-database", workspace_id, "page-e1", "missing-block",
                    json.dumps({**block, "id": "missing-block"}),
                    0, 1, before_block[7], 1, "owner", 2, "invalid-db-trash"))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE workspace_collaboration_database_trash_state "
                "SET workspace_id=? WHERE database_id='database-e1'", ("other-workspace",))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE workspace_collaboration_database_trash_state "
                "SET block_snapshot_json=? WHERE database_id='database-e1'", ("not-json",))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE workspace_collaboration_database_trash_state "
                "SET block_snapshot_json=? WHERE database_id='database-e1'",
                (json.dumps({**block, "id": "another-block"}),))
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    visible = reopened.iam.read_document("owner", "page-e1")
    assert all(item["id"] != block["id"] for item in visible["blocks"])
    assert all(item["id"] != "db-trash-owned-block-comment"
        for item in visible["comments"])
    assert reopened.iam.read_databases("owner", "page-e1") == []
    with pytest.raises(NotFoundOrDenied):
        reopened.iam.read_database("owner", "database-e1")
    current = reopened.iam.read_document("owner", "page-e1")
    property_item = active_database["properties"][0]
    with pytest.raises(NotFoundOrDenied):
        reopened.iam.execute_typed_command("owner", "page-e1",
            command_kind="database.property.rename", target_id=property_item["id"],
            payload={"name": "Should stay hidden"},
            expected_target_version=property_item["version"],
            expected_document_sequence=current["document_sequence"],
            expected_workspace_epoch=current["workspace_epoch"],
            expected_document_epoch=current["document_epoch"],
            operation_id="db-trash-deny-rename")


def test_ui_r5_owned_database_trash_restores_same_block_rows_and_comments(
        activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    before = store.iam.read_document("owner", "page-e1")
    store.iam.add_comment("owner", "page-e1", "db-trash-block-comment",
        content="Block discussion", block_id="owned-db-source-block",
        mention_subject_ids=["content-editor"],
        expected_workspace_epoch=before["workspace_epoch"],
        expected_document_epoch=before["document_epoch"],
        operation_id="db-trash-block-comment-add")
    before = store.iam.read_document("owner", "page-e1")
    store.iam.add_comment("owner", "page-e1", "db-trash-block-reply",
        content="Reply to block discussion",
        parent_comment_id="db-trash-block-comment", anchor={},
        expected_workspace_epoch=before["workspace_epoch"],
        expected_document_epoch=before["document_epoch"],
        operation_id="db-trash-block-reply-add")
    before = store.iam.read_document("owner", "page-e1")
    store.iam.add_comment("owner", "page-e1", "db-trash-item-comment",
        content="Item discussion", anchor={"kind": "database_row",
            "database_id": "database-e1", "row_id": "row-e1"},
        expected_workspace_epoch=before["workspace_epoch"],
        expected_document_epoch=before["document_epoch"],
        operation_id="db-trash-item-comment-add")
    before = store.iam.read_document("owner", "page-e1")
    store.iam.add_comment("owner", "page-e1", "db-trash-page-comment",
        content="Unrelated page discussion", anchor={},
        expected_workspace_epoch=before["workspace_epoch"],
        expected_document_epoch=before["document_epoch"],
        operation_id="db-trash-page-comment-add")
    _apply(migration, store.db_path, store.user_data_root,
        "before-database-trash-service.db")
    store = CollaborativeWorkspaceStore(store.db_path,
        user_data_root=store.user_data_root,
        identity_directory=store.identity_directory)
    def live_block_ids():
        with store.iam._reader() as con:
            frames = _frames(con, "page-e1", _generation(con, "page-e1"))
        return [item["id"] for item in search_projection.materialize([frames])[0]["blocks"]]

    original = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    assert store.iam.search("owner", workspace_id,
        query=database["title"])["count"] >= 1
    assert any(item["id"] == "database-e1" for item in
        linked_databases.list_linkable_databases(store.iam, "owner", workspace_id)["items"])
    assert any(item["comment_id"] == "db-trash-block-comment" for item in
        store.iam.list_inbox("content-editor", workspace_id=workspace_id)["items"])
    fences = {"expected_document_sequence": original["document_sequence"],
        "expected_workspace_epoch": original["workspace_epoch"],
        "expected_document_epoch": original["document_epoch"],
        "expected_database_version": database["version"]}
    with pytest.raises(NotFoundOrDenied):
        database_trash.set_owned_database_trashed(store.iam,
            "content-editor", workspace_id, "page-e1", "database-e1",
            archived=True, operation_id="db-trash-editor-denied", **fences)
    with pytest.raises(InjectedFailure):
        database_trash.set_owned_database_trashed(store.iam,
            "owner", workspace_id, "page-e1", "database-e1", archived=True,
            operation_id="db-trash-archive-fault", fault="after_domain", **fences)
    assert store.iam.read_database("owner", "database-e1")["id"] == "database-e1"
    assert any(item["id"] == "owned-db-source-block"
        for item in store.iam.read_document("owner", "page-e1")["blocks"])
    archived = database_trash.set_owned_database_trashed(store.iam,
        "owner", workspace_id, "page-e1", "database-e1", archived=True,
        operation_id="db-trash-archive", **fences)
    assert archived["archived"] and not archived["replayed"]
    assert archived["block_comment_count"] == 1
    assert database_trash.set_owned_database_trashed(store.iam,
        "owner", workspace_id, "page-e1", "database-e1", archived=True,
        operation_id="db-trash-archive", **fences)["replayed"]
    hidden = store.iam.read_document("owner", "page-e1")
    assert not any(item["id"] == "owned-db-source-block" for item in hidden["blocks"])
    assert "owned-db-source-block" not in live_block_ids()
    assert [item["id"] for item in hidden["comments"]] == ["db-trash-page-comment"]
    assert [item["id"] for item in store.iam.list_comments(
        "owner", "page-e1")["items"]] == ["db-trash-page-comment"]
    with pytest.raises(NotFoundOrDenied):
        store.iam.list_comments("owner", "page-e1", item_target={
            "kind": "database_row", "database_id": "database-e1", "row_id": "row-e1"})
    assert not any(item["comment_id"] == "db-trash-block-comment" for item in
        store.iam.list_inbox("content-editor", workspace_id=workspace_id)["items"])
    assert not any(item["id"] == "database-e1" for item in
        linked_databases.list_linkable_databases(store.iam, "owner", workspace_id)["items"])
    assert store.iam.read_databases("owner", "page-e1") == []
    assert store.iam.search("owner", workspace_id,
        query=database["title"])["count"] == 0
    with pytest.raises(NotFoundOrDenied):
        store.iam.read_database("owner", "database-e1")
    with _connection(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_database_rows "
            "WHERE database_id='database-e1'").fetchone()[0] == len(database["rows"])
        assert con.execute("SELECT block_id FROM workspace_collaboration_comments "
            "WHERE id='db-trash-block-comment'").fetchone() == (None,)
        assert con.execute("SELECT comment_id FROM "
            "workspace_collaboration_database_trash_block_comments").fetchall() == [
                ("db-trash-block-comment",)]
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    restore_fences = {"expected_document_sequence": hidden["document_sequence"],
        "expected_workspace_epoch": hidden["workspace_epoch"],
        "expected_document_epoch": hidden["document_epoch"],
        "expected_database_version": archived["database_version"]}
    with pytest.raises(NotFoundOrDenied):
        database_trash.set_owned_database_trashed(store.iam,
            "content-editor", workspace_id, "page-e1", "database-e1",
            archived=False, operation_id="db-trash-editor-restore-denied",
            **restore_fences)
    with pytest.raises(InjectedFailure):
        database_trash.set_owned_database_trashed(store.iam,
            "owner", workspace_id, "page-e1", "database-e1", archived=False,
            operation_id="db-trash-restore-fault", fault="after_domain",
            **restore_fences)
    with pytest.raises(NotFoundOrDenied):
        store.iam.read_database("owner", "database-e1")
    restored = database_trash.set_owned_database_trashed(store.iam,
        "owner", workspace_id, "page-e1", "database-e1", archived=False,
        operation_id="db-trash-restore", **restore_fences)
    assert not restored["archived"] and restored["block_comment_count"] == 1
    visible = store.iam.read_document("owner", "page-e1")
    assert [item["id"] for item in visible["blocks"]] == [
        item["id"] for item in original["blocks"]]
    assert live_block_ids() == [item["id"] for item in original["blocks"]]
    assert {item["id"] for item in visible["comments"]} == {
        "db-trash-block-comment", "db-trash-block-reply",
        "db-trash-item-comment", "db-trash-page-comment"}
    assert store.iam.read_database("owner", "database-e1")["rows"] == database["rows"]
    assert store.iam.search("owner", workspace_id,
        query=database["title"])["count"] >= 1
    assert any(item["id"] == "database-e1" for item in
        linked_databases.list_linkable_databases(store.iam, "owner", workspace_id)["items"])
    assert any(item["comment_id"] == "db-trash-block-comment" for item in
        store.iam.list_inbox("content-editor", workspace_id=workspace_id)["items"])
    with _connection(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_database_trash_state"
            ).fetchone()[0] == 0
        assert con.execute("SELECT block_id FROM workspace_collaboration_comments "
            "WHERE id='db-trash-block-comment'").fetchone() == ("owned-db-source-block",)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []

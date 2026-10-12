"""UI-PAGE-COPY file provenance schema and existing file-authority behavior.

Copy file rows below are explicit temporary schema fixtures, not the unfinished
page-copy service. Production insertion still belongs to its atomic transaction.
"""
from contextlib import contextmanager
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_file_copy_migration as migration
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, NotFoundOrDenied
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_member_navigation import (
    environment as member_environment, native_environment, archive_environment, guest_environment, image_environment,
    create, dump, _apply, _backup, preferences, favorite,
)
from code_assist_v1.tests.notion_collaboration.test_file_authority import _begin, _stage_all
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import request
from code_assist_v1.tests.notion_collaboration.test_search_authority import _grant
from code_assist_v1.tests.notion_collaboration.test_guest_navigation import revoke


@contextmanager
def connection(store):
    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with con:
            yield con
    finally:
        con.close()


def upload(store, payload=b"canonical copy evidence", operation="provenance-upload"):
    session = _begin(store.iam, payload, operation)
    _stage_all(store.iam, session, payload)
    return store.iam.finalize_upload("content-editor", session["session_id"], operation_id=operation + "-final")


def upgrade(old):
    _apply(migration, old.db_path, old.user_data_root, "before-file-copy-2416.db")
    return CollaborativeWorkspaceStore(old.db_path, user_data_root=old.user_data_root,
        identity_directory=old.identity_directory)


@pytest.fixture()
def environment(member_environment):
    old, ws = member_environment
    return upgrade(old), ws


def original_rows(store):
    with connection(store) as con:
        tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            if row[0] != "workspace_schema_migrations"]
        result = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            cursor = con.execute("SELECT * FROM " + quoted)
            result[table] = ([item[0] for item in cursor.description], sorted([tuple(row) for row in cursor], key=repr))
        return result


def seed_copy_file(store, ws, source_id, destination, *, id="file_copy_schema_fixture", **overrides):
    with connection(store) as con:
        source = dict(con.execute("SELECT * FROM workspace_collaboration_files WHERE id=?", (source_id,)).fetchone())
        row = {**source, "id": id, "workspace_id": ws, "document_id": destination, "upload_session_id": None,
            "state": "active", "row_version": 1, "deleted_at": None, "deleted_by_subject_id": None,
            "origin_kind": "copy", "source_file_id": source_id, "source_document_id": source["document_id"],
            "copied_by_subject_id": "owner", "copy_operation_id": "copy-schema-fixture-" + id, **overrides}
        fields = list(row)
        con.execute("INSERT INTO workspace_collaboration_files(" + ",".join(fields) + ") VALUES(" + ",".join("?" for _ in fields) + ")", tuple(row.values()))
    return id


def test_ui_file_copy_migration_preserves_all_original_rows_and_upload_behavior(member_environment):
    old, ws = member_environment
    first, deleted = upload(old), upload(old, b"old deleted file", "old-deleted")
    old.iam.delete_file("owner", deleted["file_id"], operation_id="delete-before-upgrade", **request(old))
    before = original_rows(old)
    store = upgrade(old)
    with connection(store) as con:
        for table, (columns, rows) in before.items():
            quoted_table = '"' + table.replace('"', '""') + '"'
            quoted_columns = ",".join('"' + key.replace('"', '""') + '"' for key in columns)
            assert sorted([tuple(row) for row in con.execute("SELECT " + quoted_columns + " FROM " + quoted_table)], key=repr) == rows, table
        files = con.execute("SELECT origin_kind,source_file_id,source_document_id,copied_by_subject_id,copy_operation_id FROM workspace_collaboration_files").fetchall()
        assert [tuple(row) for row in files] == [("upload", None, None, None, None)] * 2
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('workspace_collaboration_files_replacement','idx_collab_files_copy_migration_identity')").fetchone()[0] == 0
    assert store.iam.get_file("owner", first["file_id"])["path"].read_bytes() == b"canonical copy evidence"
    new = upload(store, b"upload after upgrade", "new-upload")
    assert store.iam.get_file("content-editor", new["file_id"])["path"].read_bytes() == b"upload after upgrade"
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"


def test_ui_file_copy_migration_exact_plan_backup_quiescence_and_rollback(member_environment):
    store, _ws = member_environment
    upload(store)
    before = dump(store)
    plan = migration.build_plan(store.db_path, store.user_data_root)
    backup = _backup(store.db_path, store.user_data_root / "backups" / "provenance-plan.db")
    options = dict(exact_plan_checksum=plan["plan_checksum"], backup_confirmed=True,
        catalog_quiesced_confirmed=True, backup_path=backup)
    for changed in ({"backup_confirmed": False}, {"catalog_quiesced_confirmed": False},
                    {"exact_plan_checksum": "sha256:" + "0" * 64}):
        with pytest.raises(migration.MigrationRefused):
            migration.apply_plan(store.db_path, store.user_data_root, **{**options, **changed})
        assert dump(store) == before
    def fail(stage):
        if stage == "after_schema":
            raise RuntimeError("injected file table rebuild failure")
    with pytest.raises(RuntimeError, match="injected"):
        migration.apply_plan(store.db_path, store.user_data_root, **options, fault_hook=fail)
    assert dump(store) == before
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "migration_required"


def test_ui_file_copy_schema_origin_guards_and_immutable_provenance(environment):
    store, ws = environment
    source = upload(store)
    destination = create(store, ws, parent="", operation="copy-guard-destination")["page"]["id"]
    copy_id = seed_copy_file(store, ws, source["file_id"], destination)
    for i, fields in enumerate(({"origin_kind": "upload"}, {"copy_operation_id": None},
            {"copied_by_subject_id": ""}, {"source_document_id": "wrong-source-document"},
            {"source_file_id": "missing-source-file"}, {"upload_session_id": source["session_id"]})):
        with pytest.raises(sqlite3.IntegrityError):
            seed_copy_file(store, ws, source["file_id"], destination, id="file_invalid_" + str(i), **fields)
    with connection(store) as con:
        original = tuple(con.execute("SELECT * FROM workspace_collaboration_files WHERE id=?", (copy_id,)).fetchone())
        for field, value in (("source_file_id", None), ("copy_operation_id", "changed"),
                ("copied_by_subject_id", "content-editor"), ("upload_session_id", source["session_id"])):
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(f"UPDATE workspace_collaboration_files SET {field}=?,state='deleted',deleted_at=updated_at,deleted_by_subject_id='owner',row_version=row_version+1 WHERE id=?", (value, copy_id))
            assert tuple(con.execute("SELECT * FROM workspace_collaboration_files WHERE id=?", (copy_id,)).fetchone()) == original
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("DELETE FROM workspace_collaboration_files WHERE id=?", (copy_id,))
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_ui_file_copy_authorizes_destination_and_keeps_blob_after_source_deletion(environment):
    store, ws = environment
    source = upload(store)
    destination = create(store, ws, parent="", operation="copy-read-destination")["page"]["id"]
    copy_id = seed_copy_file(store, ws, source["file_id"], destination)
    before = store.iam.get_file("owner", copy_id)
    assert before["document_id"] == destination
    assert before["path"].read_bytes() == b"canonical copy evidence"
    # Even the original uploader has no access to the owner's new private page.
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("content-editor", copy_id)
    _grant(store.iam, destination, "outsider", "copy-reader-grant")
    revoke(store, ws)
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("outsider", source["file_id"])
    assert store.iam.get_file("outsider", copy_id)["path"].read_bytes() == b"canonical copy evidence"
    store.iam.delete_file("owner", source["file_id"], operation_id="delete-original", **request(store))
    assert before["path"].is_file()
    assert store.iam.get_file("outsider", copy_id)["path"].read_bytes() == b"canonical copy evidence"
    store.iam.delete_file("owner", copy_id, operation_id="delete-copy", **request(store, destination))
    # History-capable authorities retain committed bytes even after all active
    # references are deleted. Provenance must preserve that existing contract.
    assert before["path"].read_bytes() == b"canonical copy evidence"
    with connection(store) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_files WHERE state='active'").fetchone()[0] == 0
    assert store.iam.cleanup_orphans()["removed_blobs"] == 0
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", copy_id)


def test_ui_file_copy_marker_retains_navigation_creation_archive_and_transport(environment):
    store, ws = environment
    caps = store.capabilities()
    assert caps["schema_version"] == migration.TARGET_VERSION
    for feature in ("guest_navigation", "collaborative_page_creation", "collaborative_page_archive",
            "live_collaboration", "collaborative_editing", "database_features", "collaborative_files",
            "collaborative_history", "collaborative_search", "workspace_image"):
        assert caps[feature], feature
    assert not caps.get("collaborative_page_copy", False)
    CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)._connect(query_only=True).close()
    root = create(store, ws, parent="", operation="after-copy-schema")["page"]["id"]
    store.add_page_favorite("owner", ws, **favorite(source_page_id=root))
    store.replace_sidebar_preferences("owner", ws, **preferences())
    store.replace_sidebar_preferences("outsider", ws, **preferences())
    assert store.get_navigation_state("owner", ws)["favorites"][0]["source_page_id"] == root
    assert store.get_navigation_state("outsider", ws)["sidebar"]["width_px"] == 320
    from code_assist_v1.tests.notion_collaboration.test_page_archive import change
    change(store, ws, root, operation="after-copy-schema-archive")
    change(store, ws, root, archived=False, operation="after-copy-schema-restore")
    with connection(store) as con:
        con.execute("UPDATE workspace_schema_migrations SET checksum='invalid' WHERE version=?", (migration.TARGET_VERSION,))
    try:
        assert store.capabilities()["workspace_v2"] is False
        with pytest.raises(AuthorityUnavailable):
            store.iam.read_document("owner", root)
    finally:
        with connection(store) as con:
            con.execute("UPDATE workspace_schema_migrations SET checksum=? WHERE version=?", (migration.TARGET_CHECKSUM, migration.TARGET_VERSION))


@pytest.mark.parametrize("copied", [False, True])
def test_ui_file_copy_history_restores_uploads_and_copies_without_rewriting_origin(environment, copied):
    store, ws = environment
    source = upload(store)
    document, file_id = "page-e1", source["file_id"]
    if copied:
        document = create(store, ws, parent="", operation="history-copy-destination")["page"]["id"]
        file_id = seed_copy_file(store, ws, source["file_id"], document)
    with connection(store) as con:
        provenance = tuple(con.execute("SELECT origin_kind,upload_session_id,source_file_id,source_document_id,copied_by_subject_id,copy_operation_id FROM workspace_collaboration_files WHERE id=?", (file_id,)).fetchone())
    page = store.iam.read_document("owner", document)
    checkpoint = store.iam.create_revision_checkpoint("owner", document, expected_revision=page["revision"],
        operation_id="history-copy-checkpoint", **request(store, document))
    store.iam.delete_file("owner", file_id, operation_id="history-copy-delete", **request(store, document))
    if copied:
        store.iam.delete_file("owner", source["file_id"], operation_id="history-original-delete", **request(store))
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", file_id)
    page = store.iam.read_document("owner", document)
    restored = store.iam.restore_revision("owner", document, checkpoint["revision"],
        expected_revision=page["revision"], operation_id="history-copy-restore", **request(store, document))
    assert restored["revision"] == page["revision"] + 1
    assert store.iam.get_file("owner", file_id)["path"].read_bytes() == b"canonical copy evidence"
    with connection(store) as con:
        assert tuple(con.execute("SELECT origin_kind,upload_session_id,source_file_id,source_document_id,copied_by_subject_id,copy_operation_id FROM workspace_collaboration_files WHERE id=?", (file_id,)).fetchone()) == provenance
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    if copied:
        with pytest.raises(NotFoundOrDenied):
            store.iam.get_file("owner", source["file_id"])

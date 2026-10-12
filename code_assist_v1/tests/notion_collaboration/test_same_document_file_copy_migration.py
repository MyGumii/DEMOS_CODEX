"""UI-DB-COPY: forward file provenance without aliasing an original attachment."""
import pytest

from code_assist_v1 import notion_workspace_same_document_file_copy_migration as migration
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_database_row_appearance import (
    environment as appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_row_content import _backup
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import (
    connection, original_rows, request, seed_copy_file, upload,
)


def test_ui_same_document_file_copy_migration_preserves_existing_rows_and_rejects_bad_plan(appearance_environment):
    old, workspace_id = appearance_environment
    original = upload(old)["file_id"]
    page = old.iam.read_document("owner", "page-e1")
    old.iam.create_document("owner", workspace_id, "existing-file-copy-target",
        title="Existing copy target", blocks=[],
        expected_workspace_epoch=page["workspace_epoch"],
        operation_id="existing-file-copy-target-create")
    prior_copy = seed_copy_file(old, workspace_id, original, "existing-file-copy-target")
    before = original_rows(old)
    plan = migration.build_plan(old.db_path, old.user_data_root)
    backup = _backup(old.db_path, old.user_data_root / "backups" / "before-same-document-file-copy.db")
    options = dict(exact_plan_checksum=plan["plan_checksum"], backup_confirmed=True,
        catalog_quiesced_confirmed=True, backup_path=backup)
    with pytest.raises(migration.MigrationRefused):
        migration.apply_plan(old.db_path, old.user_data_root,
            **{**options, "exact_plan_checksum": "sha256:" + "0" * 64})
    assert original_rows(old) == before
    def fail(stage):
        if stage == "after_schema":
            raise RuntimeError("synthetic file rebuild failure")
    with pytest.raises(RuntimeError):
        migration.apply_plan(old.db_path, old.user_data_root, **options, fault_hook=fail)
    assert original_rows(old) == before
    migration.apply_plan(old.db_path, old.user_data_root, **options)
    store = CollaborativeWorkspaceStore(old.db_path, user_data_root=old.user_data_root,
        identity_directory=old.identity_directory)
    assert original_rows(store) == before
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"
    with connection(store) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT source_file_id FROM workspace_collaboration_files WHERE id=?",
            (prior_copy,)).fetchone()[0] == original


def test_ui_same_document_file_copy_has_independent_identity_and_blob_lifetime(appearance_environment):
    old, workspace_id = appearance_environment
    original = upload(old)["file_id"]
    plan = migration.build_plan(old.db_path, old.user_data_root)
    backup = _backup(old.db_path, old.user_data_root / "backups" / "before-file-identity.db")
    migration.apply_plan(old.db_path, old.user_data_root,
        exact_plan_checksum=plan["plan_checksum"], backup_confirmed=True,
        catalog_quiesced_confirmed=True, backup_path=backup)
    store = CollaborativeWorkspaceStore(old.db_path, user_data_root=old.user_data_root,
        identity_directory=old.identity_directory)
    copied = seed_copy_file(store, workspace_id, original, "page-e1", id="file_same_document_fixture")
    assert store.iam.get_file("owner", copied)["path"].read_bytes() == b"canonical copy evidence"
    store.iam.delete_file("owner", original, operation_id="delete-original-after-same-page-copy",
        **request(store))
    assert store.iam.get_file("owner", copied)["path"].read_bytes() == b"canonical copy evidence"
    with connection(store) as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []

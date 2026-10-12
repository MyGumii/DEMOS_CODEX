from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_collaboration_migration as migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.notion_collaboration.canonical_authority import CanonicalAuthority
from code_assist_v1.notion_collaboration.cutover import (
    AdditiveForwardCompatibleWorkspaceStore,
)


def _backup(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_con = sqlite3.connect(source)
    target_con = sqlite3.connect(destination)
    try:
        source_con.backup(target_con)
        target_con.commit()
    finally:
        target_con.close()
        source_con.close()
    return destination


def _source(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "a0b-user-data"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(root))
    store = workspace_store.WorkspaceStore(catalog)
    assert store.bootstrap()["schema_version"] == migration.SOURCE_VERSION
    created = store.create_workspace(
        "owner", name="A0B workspace", operation_id="op-a0b-workspace-create-0001"
    )
    return root, catalog, str(created["workspace"]["id"])


def _marker(path: Path) -> tuple[int, str, str, str]:
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return tuple(
            con.execute(
                "SELECT version,name,checksum,actual_schema_sha256 "
                "FROM workspace_schema_migrations"
            ).fetchone()
        )
    finally:
        con.close()


@pytest.mark.migration
def test_a0b_p1_forward_migration_is_exact_checksummed_and_idempotent(tmp_path: Path) -> None:
    root, catalog, workspace_id = _source(tmp_path)
    original_marker = _marker(catalog)
    backup = _backup(catalog, root / "backups" / "before-2026082401.db")
    plan = migration.build_plan(catalog, root)
    assert plan["state"] == "migration_required"
    assert (plan["source_version"], plan["target_version"]) == (
        2026081317,
        2026082401,
    )
    assert plan["operations"] == [
        "install_canonical_document_block_comment_database_schema",
        "install_operation_revision_event_audit_outbox_authority",
        "install_import_and_dev_cutover_receipts",
        "replace_migration_marker",
    ]
    applied = migration.apply_plan(
        catalog,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
    )
    assert applied["applied"] is True
    assert _marker(backup) == original_marker
    assert _marker(catalog) == (
        migration.TARGET_VERSION,
        migration.TARGET_NAME,
        migration.TARGET_CHECKSUM,
        migration.TARGET_ACTUAL_SCHEMA_SHA256,
    )
    assert migration.build_plan(catalog, root)["state"] == "already_current"
    authority = CanonicalAuthority.open(catalog, root)
    assert authority.integrity() == {
        "contract": "code_assist.notion.canonical_collaboration_authority.v1",
        "quick_check": "ok",
        "foreign_key_error_count": 0,
        "schema_version": 2026082401,
        "schema_marker_count": 1,
        "request_time_directory_scan_count": 0,
        "legacy_source_database_open_count": 0,
        "dual_write_count": 0,
    }
    compatible = AdditiveForwardCompatibleWorkspaceStore(catalog)
    assert compatible.capabilities()["schema_version"] == 2026082401
    assert [item["id"] for item in compatible.list_workspaces("owner")] == [workspace_id]


@pytest.mark.migration
def test_a0b_p1_wrong_checksum_fault_and_unknown_lineage_leave_source_unchanged(
    tmp_path: Path,
) -> None:
    root, catalog, _ = _source(tmp_path)
    before = catalog.read_bytes()
    backup = _backup(catalog, root / "backups" / "before.db")
    plan = migration.build_plan(catalog, root)
    with pytest.raises(migration.MigrationRefused, match="checksum"):
        migration.apply_plan(
            catalog,
            root,
            exact_plan_checksum="sha256:" + "0" * 64,
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup,
        )
    assert catalog.read_bytes() == before

    def fail(point: str) -> None:
        if point == "after_schema":
            raise RuntimeError("injected-before-publish")

    with pytest.raises(RuntimeError, match="injected-before-publish"):
        migration.apply_plan(
            catalog,
            root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup,
            fault_hook=fail,
        )
    assert catalog.read_bytes() == before
    assert _marker(catalog)[0] == 2026081317

    con = sqlite3.connect(catalog)
    try:
        con.execute(
            "UPDATE workspace_schema_migrations SET version=?",
            (migration.PARALLEL_RESERVED_VERSION,),
        )
        con.commit()
    finally:
        con.close()
    with pytest.raises(migration.MigrationRefused, match="exact 2026081317"):
        migration.build_plan(catalog, root)


def test_a0b_p1_2026081318_remains_reserved_and_historical_1317_is_unchanged() -> None:
    assert migration.PARALLEL_RESERVED_VERSION == 2026081318
    assert migration.TARGET_VERSION == 2026082401
    assert migration.TARGET_VERSION != migration.PARALLEL_RESERVED_VERSION
    assert workspace_store.SCHEMA_VERSION == 2026081317
    assert migration.SOURCE_SQL == workspace_store._SCHEMA_SQL + workspace_store._HEALING_SCHEMA_SQL
    assert migration.COLLABORATION_SCHEMA_SQL not in workspace_store._SCHEMA_SQL

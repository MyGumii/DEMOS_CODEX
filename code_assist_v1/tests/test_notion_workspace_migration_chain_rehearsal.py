from __future__ import annotations

from pathlib import Path
import sqlite3

from code_assist_v1 import notion_workspace_healing_apply_migration as migration_1307
from code_assist_v1 import notion_workspace_healing_migration as migration_1306
from code_assist_v1 import notion_workspace_incarnation_migration as migration_1309
from code_assist_v1 import notion_workspace_navigation_migration as migration_1310
from code_assist_v1 import notion_workspace_page_tree_migration as migration_1311
from code_assist_v1 import notion_workspace_lifecycle_kernel_migration as migration_1312
from code_assist_v1 import notion_workspace_lifecycle_finalize_migration as migration_1313
from code_assist_v1 import notion_workspace_structural_effect_migration as migration_1314
from code_assist_v1 import notion_workspace_structural_finalize_migration as migration_1315
from code_assist_v1 import notion_workspace_lifecycle_subtree_migration as migration_1316
from code_assist_v1 import (
    notion_workspace_lifecycle_subtree_finalize_migration as migration_1317,
)
from code_assist_v1 import notion_workspace_outbox_migration as migration_1305
from code_assist_v1 import notion_workspace_quarantine_migration as migration_1308
from code_assist_v1 import notion_workspace_schema_migration as migration_1304
from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1 import notion_legacy_incarnation_contract as legacy_incarnation


MIGRATION_CHAIN = (
    migration_1304,
    migration_1305,
    migration_1306,
    migration_1307,
    migration_1308,
    migration_1309,
    migration_1310,
    migration_1311,
    migration_1312,
    migration_1313,
    migration_1314,
    migration_1315,
    migration_1316,
    migration_1317,
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _source_1303(root: Path) -> Path:
    catalog = root / ".notion-workspace-v2" / "workspace.db"
    catalog.parent.mkdir(parents=True)
    con = sqlite3.connect(catalog)
    try:
        con.executescript(migration_1304.SOURCE_SQL)
        con.execute(
            "INSERT INTO workspace_schema_migrations VALUES(?,?,?,?,?)",
            (
                migration_1304.SOURCE_VERSION,
                migration_1304.SOURCE_NAME,
                migration_1304.SOURCE_CHECKSUM,
                migration_1304.SOURCE_ACTUAL_SCHEMA_SHA256,
                100,
            ),
        )
        con.commit()
    finally:
        con.close()
    return catalog


def _sqlite_backup(source: Path, destination: Path | str) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_con = sqlite3.connect(source)
    destination_con = sqlite3.connect(destination)
    try:
        source_con.backup(destination_con)
        destination_con.commit()
    finally:
        destination_con.close()
        source_con.close()
    return destination


def _marker(catalog: Path) -> tuple[int, str, str, str]:
    con = sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)
    try:
        return tuple(
            con.execute(
                """
                SELECT version,name,checksum,actual_schema_sha256
                FROM workspace_schema_migrations
                """
            ).fetchone()
        )
    finally:
        con.close()


def _schema_objects(catalog: Path) -> list[tuple[str, str, str, str]]:
    con = sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)
    try:
        return con.execute(
            """
            SELECT type,name,tbl_name,sql FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
            ORDER BY type,name
            """
        ).fetchall()
    finally:
        con.close()


def test_full_frozen_chain_retains_restore_points_and_matches_fresh_1317(
    tmp_path: Path,
) -> None:
    root = tmp_path / "migrated-user-data"
    catalog = _source_1303(root)
    backups: list[tuple[Path, tuple[int, str, str, str]]] = []

    for migration in MIGRATION_CHAIN:
        source_marker = _marker(catalog)
        assert source_marker[0] == migration.SOURCE_VERSION
        backup = _sqlite_backup(
            catalog,
            root / "backups" / f"workspace-before-{migration.TARGET_VERSION}.db",
        )
        backups.append((backup, source_marker))
        plan = migration.build_plan(catalog, root)
        assert plan["state"] == "migration_required"
        result = migration.apply_plan(
            catalog,
            root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup,
        )
        assert result["applied"] is True
        assert _marker(catalog)[0] == migration.TARGET_VERSION
        assert _marker(backup) == source_marker

    assert _marker(catalog) == (
        store_module.SCHEMA_VERSION,
        store_module.SCHEMA_MIGRATION_NAME,
        store_module._SCHEMA_CHECKSUM,
        migration_1317.TARGET_ACTUAL_SCHEMA_SHA256,
    )
    migrated_store = store_module.WorkspaceStore(catalog)
    assert migrated_store.verify_deep_health()["healthy"] is True
    assert migrated_store.deep_health()["incarnation_attestation_readiness"] == {
        "binding_count": 0,
        "active_binding_count": 0,
        "pending_binding_count": 0,
        "quarantined_binding_count": 0,
        "attestation_count": 0,
        "current_attestation_count": 0,
        "historical_attestation_count": 0,
        "attested_active_binding_count": 0,
        "unattested_active_binding_count": 0,
        "attested_pending_binding_count": 0,
        "unattested_pending_binding_count": 0,
        "ready_for_resolver_cutover": True,
    }

    fresh_root = tmp_path / "fresh-user-data"
    fresh = store_module.WorkspaceStore(
        store_module.catalog_path_for_user_data_root(fresh_root)
    )
    fresh.bootstrap()
    assert _schema_objects(catalog) == _schema_objects(Path(fresh.db_path))

    # Restore the retained pre-1317 snapshot into a new exact root, then apply
    # the frozen final migration again.  This proves recovery does not depend
    # on the original file identity or mutate the retained restore point.
    restored_root = tmp_path / "restored-user-data"
    restored_catalog = _sqlite_backup(
        backups[-1][0],
        store_module.catalog_path_for_user_data_root(restored_root),
    )
    assert _marker(restored_catalog) == backups[-1][1]
    restored_backup = _sqlite_backup(
        restored_catalog,
        restored_root / "backups" / "workspace-before-restored-1317.db",
    )
    restored_plan = migration_1317.build_plan(restored_catalog, restored_root)
    restored_result = migration_1317.apply_plan(
        restored_catalog,
        restored_root,
        exact_plan_checksum=restored_plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=restored_backup,
    )
    assert restored_result["applied"] is True
    assert _marker(restored_backup) == backups[-1][1]
    restored = store_module.WorkspaceStore(restored_catalog)
    assert restored.verify_deep_health()["healthy"] is True
    assert _marker(restored_catalog) == _marker(catalog)
    assert _schema_objects(restored_catalog) == _schema_objects(catalog)
    assert len(backups) == len(MIGRATION_CHAIN)


def test_current_notional_plans_match_frozen_workspace_versions() -> None:
    documents = (
        REPOSITORY_ROOT / "code_assist_v1" / "docs" / "NOTION_PARITY_MASTER_PLAN.md",
        REPOSITORY_ROOT / "code_assist_v1" / "docs" / "NOTION_PARITY_ROADMAP.md",
        REPOSITORY_ROOT
        / "code_assist_v1"
        / "docs"
        / "notion_parity"
        / "NP2A_WORKSPACE_SUBSTRATE.md",
    )
    chain = "1303→1304→1305→1306→1307→1308→1309→1310→1311→1312→1313→1314→1315→1316→1317"
    for document in documents:
        text = document.read_text(encoding="utf-8")
        assert chain in text
        assert "source incarnation nonce는 아직" not in text
        assert "다음 단계는 source incarnation nonce" not in text

    assert store_module.SCHEMA_VERSION == 2026081317
    assert migration_1309.TARGET_CHECKSUM == store_module._SCHEMA_1309_CHECKSUM
    assert migration_1310.TARGET_CHECKSUM == store_module._SCHEMA_1310_CHECKSUM
    assert migration_1311.TARGET_CHECKSUM == store_module._SCHEMA_1311_CHECKSUM
    assert migration_1312.TARGET_CHECKSUM == store_module._SCHEMA_1312_CHECKSUM
    assert migration_1313.TARGET_CHECKSUM == store_module._SCHEMA_1313_CHECKSUM
    assert migration_1314.TARGET_CHECKSUM == store_module._SCHEMA_1314_CHECKSUM
    assert migration_1315.TARGET_CHECKSUM == store_module._SCHEMA_1315_CHECKSUM
    assert migration_1316.TARGET_CHECKSUM == store_module._SCHEMA_1316_CHECKSUM
    assert migration_1317.TARGET_CHECKSUM == store_module._SCHEMA_CHECKSUM
    assert legacy_incarnation.MIGRATION_VERSION == 2026081302
    assert legacy_incarnation.MIGRATION_CHECKSUM.startswith("sha256:")
    assert legacy_incarnation.EXPECTED_OBJECT_HASH.startswith("sha256:")

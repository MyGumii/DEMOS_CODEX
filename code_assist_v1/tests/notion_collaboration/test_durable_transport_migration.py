from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_collaboration_migration as authority_migration
from code_assist_v1 import notion_workspace_realtime_transport_migration as migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.notion_collaboration.canonical_authority import CanonicalAuthority
from code_assist_v1.notion_collaboration.durable_transport import (
    DurableTransportAuthority,
    ForwardCompatibleCanonicalAuthority,
)
from code_assist_v1.tools.verify_internal_collaborative_transport_authority import (
    DEFAULT_CONTRACT,
    verify,
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
    root = tmp_path / "t0b-migration"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(root))
    store = workspace_store.WorkspaceStore(catalog)
    store.bootstrap()
    created = store.create_workspace(
        "owner", name="T0B", operation_id="op-t0b-migration-workspace-0001"
    )
    workspace_id = str(created["workspace"]["id"])
    first_backup = _backup(catalog, root / "backups" / "before-2401.db")
    first_plan = authority_migration.build_plan(catalog, root)
    authority_migration.apply_plan(
        catalog,
        root,
        exact_plan_checksum=first_plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=first_backup,
    )
    CanonicalAuthority.open(catalog, root).create_document(
        "owner",
        workspace_id,
        "page-t0b",
        title="T0B",
        blocks=[{"id": "block-t0b", "type": "text", "content": "base"}],
        expected_workspace_epoch=1,
        operation_id="op-t0b-migration-document-0001",
    )
    return root, catalog, workspace_id


def _marker(path: Path) -> tuple[int, str, str, str]:
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return tuple(con.execute(
            "SELECT version,name,checksum,actual_schema_sha256 FROM workspace_schema_migrations"
        ).fetchone())
    finally:
        con.close()


@pytest.mark.migration
def test_t0b_p1_forward_migration_is_exact_guarded_and_additive(tmp_path: Path) -> None:
    root, catalog, _workspace_id = _source(tmp_path)
    original_marker = _marker(catalog)
    backup = _backup(catalog, root / "backups" / "before-2402.db")
    plan = migration.build_plan(catalog, root)
    assert plan["state"] == "migration_required"
    assert (plan["source_version"], plan["target_version"]) == (2026082401, 2026082402)
    assert plan["operations"] == [
        "install_digest_only_one_time_ticket_ledger",
        "install_monotonic_client_ack_state",
        "install_opaque_yjs_update_and_snapshot_ledgers",
        "install_immutable_and_monotonic_guards",
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
    assert ForwardCompatibleCanonicalAuthority.open(catalog, root).read_document(
        "owner", "page-t0b"
    )["title"] == "T0B"
    assert DurableTransportAuthority.open(catalog, root).counts()[
        "workspace_collaboration_crdt_updates"
    ] == 0


@pytest.mark.migration
def test_t0b_p1_wrong_checksum_and_fault_leave_2401_source_unchanged(tmp_path: Path) -> None:
    root, catalog, _workspace_id = _source(tmp_path)
    before = catalog.read_bytes()
    backup = _backup(catalog, root / "backups" / "before-2402.db")
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
            raise RuntimeError("t0b-injected")

    with pytest.raises(RuntimeError, match="t0b-injected"):
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
    assert _marker(catalog)[0] == authority_migration.TARGET_VERSION


def test_t0b_p1_reserved_1318_and_frozen_2401_source_are_unchanged() -> None:
    assert migration.PARALLEL_RESERVED_VERSION == 2026081318
    assert migration.SOURCE_SQL == authority_migration.TARGET_SQL
    assert migration.SOURCE_CHECKSUM == authority_migration.TARGET_CHECKSUM
    assert migration.TARGET_VERSION not in {migration.SOURCE_VERSION, migration.PARALLEL_RESERVED_VERSION}
    assert len(migration.SOURCE_OBJECTS) == 213
    assert len(migration.TARGET_OBJECTS) == 228


def test_t0b_p1_frozen_verifier_rejects_current_drift_and_security_false_green(
    tmp_path: Path,
) -> None:
    report, exit_code = verify()
    assert exit_code == 1
    assert report["status"] == "fail"
    assert report["error_count"] == 2
    assert {
        (item["code"], item["subject"])
        for item in report["errors"]
    } == {
        (
            "AUTHORITY_INPUT_HASH",
            "code_assist_v1/notion_collaboration/canonical_authority.py",
        ),
        (
            "AUTHORITY_INPUT_HASH",
            "code_assist_v1/notion_collaboration/durable_transport.py",
        ),
    }
    assert report["summary"] == {
        "packet_id": "T0B-P1",
        "source_version": 2026082401,
        "target_version": 2026082402,
        "target_object_count": 228,
        "runtime_binding_sha256": "sha256:99b8e01015bbf92c385493bebbc08b6600add4b43afd2ab6e1746a6bb8e74057",
        "new_test_count": 15,
        "actual_gateway_transport_implemented": False,
        "product_complete": False,
    }
    contract = json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))
    contract["ticket_authority"]["plaintext_ticket_persisted"] = True
    contract["claims"]["product_complete"] = True
    path = tmp_path / "false-green.json"
    path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
    report, exit_code = verify(path)
    assert exit_code == 1
    assert {item["code"] for item in report["errors"]} >= {
        "TICKET_AUTHORITY_DRIFT",
        "CLAIM_DRIFT",
    }

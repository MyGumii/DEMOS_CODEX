from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_store
from code_assist_v1.notion_legacy_change_migration import (
    apply_plan as apply_change_tracking_plan,
    build_plan as build_change_tracking_plan,
)
from code_assist_v1.notion_workspace_healing import heal_one_locator_batch
from code_assist_v1.notion_workspace_rehearsal import (
    REHEARSAL_SENTINEL_CONTENT,
    REHEARSAL_SENTINEL_NAME,
    WorkspaceRehearsalRefused,
    run_isolated_np2a_1309_rehearsal,
)
from code_assist_v1.notion_workspace_store import (
    WorkspaceStore,
    catalog_path_for_user_data_root,
)


PREPARE_TOKEN = "hlease_" + "a" * 32
REHEARSAL_TOKEN = "hlease_" + "b" * 32


def test_rehearsal_refuses_before_path_or_source_access_without_attestations(
    tmp_path: Path,
) -> None:
    root = tmp_path / "unattested-root"
    root.mkdir()
    catalog = root / "catalog.db"
    store = WorkspaceStore(catalog)
    source_calls = 0

    def unexpected_source(_owner: str) -> Path:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError("source resolver must not run")

    with pytest.raises(WorkspaceRehearsalRefused, match="quiescence"):
        run_isolated_np2a_1309_rehearsal(
            store,
            actor_subject_id="owner",
            workspace_id="ws_" + "a" * 32,
            rehearsal_root=root,
            user_data_root=root,
            expected_catalog_db_path=catalog,
            source_db_for_owner=unexpected_source,
            source_quiesced_confirmed=False,
            lease_token=REHEARSAL_TOKEN,
        )
    with pytest.raises(WorkspaceRehearsalRefused, match="sentinel"):
        run_isolated_np2a_1309_rehearsal(
            store,
            actor_subject_id="owner",
            workspace_id="ws_" + "a" * 32,
            rehearsal_root=root,
            user_data_root=root,
            expected_catalog_db_path=catalog,
            source_db_for_owner=unexpected_source,
            source_quiesced_confirmed=True,
            lease_token=REHEARSAL_TOKEN,
        )
    assert source_calls == 0
    assert not catalog.exists()


def _bind_page(
    store: WorkspaceStore,
    *,
    workspace_id: str,
    page_id: str,
    operation_id: str,
) -> None:
    proof = notion_store.capture_legacy_page_source_proof(
        "owner", "owner", page_id
    )
    current = store.get_workspace("owner", workspace_id)
    store.bind_page_source(
        "owner",
        workspace_id,
        proof,
        source_revalidator=notion_store.revalidate_legacy_page_source_proof,
        base_version=current["version"],
        operation_id=operation_id,
    )


def test_isolated_1309_rehearsal_is_identifier_free_and_replay_safe(
    tmp_path: Path, monkeypatch
) -> None:
    rehearsal_root = tmp_path / "isolated-np2a-1309"
    rehearsal_root.mkdir()
    (rehearsal_root / REHEARSAL_SENTINEL_NAME).write_bytes(
        REHEARSAL_SENTINEL_CONTENT
    )
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(rehearsal_root))

    store = WorkspaceStore(catalog_path_for_user_data_root(rehearsal_root))
    workspace = store.create_workspace(
        "owner", name="Offline rehearsal", operation_id="rehearsal-workspace"
    )["workspace"]
    normal_page = notion_store.create_page("owner", title="normal")["page"]
    unsafe_page = notion_store.create_page("owner", title="unsafe")["page"]
    notion_store.upload_file(
        "owner", normal_page["id"], filename="normal.txt", raw=b"normal-v1"
    )
    unsafe_file = notion_store.upload_file(
        "owner", unsafe_page["id"], filename="unsafe.txt", raw=b"unsafe-v1"
    )
    _bind_page(
        store,
        workspace_id=workspace["id"],
        page_id=normal_page["id"],
        operation_id="rehearsal-bind-normal",
    )
    _bind_page(
        store,
        workspace_id=workspace["id"],
        page_id=unsafe_page["id"],
        operation_id="rehearsal-bind-unsafe",
    )

    source = rehearsal_root / "owner" / "notion" / "notion.db"
    backup_dir = rehearsal_root / "legacy-watermark-backup"
    backup_dir.mkdir()
    watermark_plan = build_change_tracking_plan(
        user_data_root=rehearsal_root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    apply_change_tracking_plan(
        watermark_plan,
        confirm_checksum=watermark_plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backup_dir / "before.db",
    )

    prepare_run = store.start_healing_run(
        "owner",
        workspace["id"],
        worker="np2a.rehearsal.prepare",
        lease_token=PREPARE_TOKEN,
        lease_seconds=300,
        operation_id="rehearsal-prepare-run",
        healing_mode="locator_apply",
    )["run"]
    prepared = heal_one_locator_batch(
        store,
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        run_id=prepare_run["id"],
        worker="np2a.rehearsal.prepare",
        lease_token=PREPARE_TOKEN,
        fencing_generation=prepare_run["lease"]["fencing_generation"],
        expected_version=prepare_run["version"],
        user_data_root=rehearsal_root,
        expected_catalog_db_path=store.db_path,
        source_db_for_owner=lambda owner: source if owner == "owner" else None,
        source_quiesced_confirmed=True,
        limit=2,
    )
    assert prepared["run"]["status"] == "completed"
    assert prepared["healing_report"]["safe_miss"] == 2

    notion_store.upload_file(
        "owner", normal_page["id"], filename="normal-v2.txt", raw=b"normal-v2"
    )
    con = sqlite3.connect(source)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(
            "UPDATE files SET rel_path=? WHERE id=?",
            ("../escape.txt", unsafe_file["id"]),
        )
        con.commit()
    finally:
        con.close()

    evidence = run_isolated_np2a_1309_rehearsal(
        store,
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        rehearsal_root=rehearsal_root,
        user_data_root=rehearsal_root,
        expected_catalog_db_path=store.db_path,
        source_db_for_owner=lambda owner: source if owner == "owner" else None,
        source_quiesced_confirmed=True,
        lease_token=REHEARSAL_TOKEN,
        limit=2,
    )

    assert evidence == {
        "contract": "np2a_1317_offline_rehearsal_v10",
        "offline_only": True,
        "quiescence_confirmed": True,
        "catalog_schema_version": 2026081317,
        "legacy_watermark_source_count": 1,
        "legacy_watermarks_ready": True,
        "healing_report": {
            "scanned": 2,
            "visited": 2,
            "match": 0,
            "safe_miss": 0,
            "stale": 1,
            "superseded": 0,
            "ambiguous": 0,
            "permission_mismatch": 0,
            "unsafe_path": 1,
            "catalog_unavailable": 0,
            "oversize": 0,
            "source_missing": 0,
        },
        "checkpoint_replay_identical": True,
        "checkpoint_replay_bypassed_source_io": True,
        "catalog": {
            "binding_count": 2,
            "active_binding_count": 1,
            "quarantined_binding_count": 1,
            "permanent_quarantine_count": 1,
            "active_projection_count": 1,
            "quarantined_projection_count": 0,
            "active_file_locator_count": 2,
            "quarantined_file_locator_count": 0,
            "active_public_locator_count": 0,
            "quarantined_public_locator_count": 0,
        },
        "health": {
            "healthy": True,
            "integrity_ok": True,
            "foreign_key_violation_count": 0,
            "issue_count": 0,
        },
    }
    serialized = json.dumps(
        evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    for forbidden in (
        "owner",
        workspace["id"],
        normal_page["id"],
        unsafe_page["id"],
        unsafe_file["id"],
        prepare_run["id"],
        str(rehearsal_root),
        str(source),
        PREPARE_TOKEN,
        REHEARSAL_TOKEN,
    ):
        assert forbidden not in serialized

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest

from code_assist_v1.notion_legacy_change_contract import CHANGE_PAGE
from code_assist_v1.notion_legacy_changes import legacy_incarnation_receipt_sha256
from code_assist_v1.notion_workspace_store import (
    LegacyFileSourceLocator,
    LegacyPageIncarnationEvidence,
    LegacyPageLocatorProjection,
    LegacyPageSourceProof,
    LegacyPublicSourceLocator,
    WorkspacePageLifecycleRenameCandidate,
    WorkspacePageLifecycleLeaseConflict,
    WorkspaceRevisionConflict,
)
from code_assist_v1.tests.test_notion_workspace_page_lifecycle_kernel import (
    LEASE_TOKEN,
    RECEIPT,
    _materialized,
    _source_applied,
)


def _candidate(store, request, *, changed: bool):
    con = store._read_connection()
    try:
        binding = con.execute(
            """
            SELECT * FROM workspace_page_sources
            WHERE workspace_id=? AND source_owner_subject_id=? AND source_page_id=?
            """,
            (
                request.workspace_id,
                request.source_owner_subject_id,
                request.source_page_id,
            ),
        ).fetchone()
        attestation = con.execute(
            """
            SELECT * FROM workspace_page_source_incarnation_attestations
            WHERE workspace_id=? AND source_owner_subject_id=? AND source_page_id=?
              AND binding_version=?
            """,
            (
                request.workspace_id,
                request.source_owner_subject_id,
                request.source_page_id,
                request.expected_binding_version,
            ),
        ).fetchone()
        files = tuple(
            LegacyFileSourceLocator(str(row[0]))
            for row in con.execute(
                """
                SELECT source_file_id FROM workspace_file_sources
                WHERE workspace_id=? AND source_owner_subject_id=?
                  AND source_page_id=? AND binding_version=?
                ORDER BY source_file_id
                """,
                (
                    request.workspace_id,
                    request.source_owner_subject_id,
                    request.source_page_id,
                    request.expected_binding_version,
                ),
            ).fetchall()
        )
        public = tuple(
            LegacyPublicSourceLocator(str(row[0]), bool(row[1]))
            for row in con.execute(
                """
                SELECT token_sha256,allow_duplicate FROM workspace_public_sources
                WHERE workspace_id=? AND source_owner_subject_id=?
                  AND source_page_id=? AND binding_version=?
                ORDER BY token_sha256
                """,
                (
                    request.workspace_id,
                    request.source_owner_subject_id,
                    request.source_page_id,
                    request.expected_binding_version,
                ),
            ).fetchall()
        )
    finally:
        con.close()
    delta = 1 if changed else 0
    revision = int(binding["source_revision"]) + delta
    updated_at = int(binding["source_updated_at"]) + delta
    content_sha256 = (
        "sha256:" + hashlib.sha256(b"renamed-page-proof").hexdigest()
        if changed
        else str(binding["source_sha256"])
    )
    source_epoch = int(attestation["source_epoch"]) + delta
    full_sweep = int(attestation["full_sweep_generation"]) + delta
    change_generation = int(attestation["source_change_generation"]) + delta
    change_mask = int(attestation["source_change_mask"]) | (
        CHANGE_PAGE if changed else 0
    )
    incarnation_receipt = legacy_incarnation_receipt_sha256(
        source_owner_id=request.source_owner_subject_id,
        page_id=request.source_page_id,
        incarnation_id=str(attestation["source_incarnation_id"]),
        incarnation_generation=int(attestation["source_incarnation_generation"]),
        incarnation_retired=False,
        change_generation=change_generation,
        change_mask=change_mask,
        source_exists=True,
        source_active=True,
        source_epoch=source_epoch,
        full_sweep_generation=full_sweep,
        source_device=int(attestation["source_device"]),
        source_inode=int(attestation["source_inode"]),
        source_link_count=int(attestation["source_link_count"]),
    )
    evidence = LegacyPageIncarnationEvidence(
        owner_subject_id=request.source_owner_subject_id,
        page_id=request.source_page_id,
        incarnation_id=str(attestation["source_incarnation_id"]),
        incarnation_generation=int(attestation["source_incarnation_generation"]),
        incarnation_retired=False,
        source_epoch=source_epoch,
        full_sweep_generation=full_sweep,
        source_device=int(attestation["source_device"]),
        source_inode=int(attestation["source_inode"]),
        source_link_count=int(attestation["source_link_count"]),
        change_generation=change_generation,
        change_mask=change_mask,
        source_exists=True,
        source_active=True,
        receipt_sha256=incarnation_receipt,
    )
    proof = LegacyPageSourceProof(
        actor_subject_id=request.source_owner_subject_id,
        owner_subject_id=request.source_owner_subject_id,
        page_id=request.source_page_id,
        revision=revision,
        updated_at=updated_at,
        content_sha256=content_sha256,
    )
    projection = LegacyPageLocatorProjection(
        actor_subject_id=request.source_owner_subject_id,
        owner_subject_id=request.source_owner_subject_id,
        page_id=request.source_page_id,
        binding_version=request.expected_binding_version + delta,
        file_sources=files,
        public_sources=public,
    )
    return WorkspacePageLifecycleRenameCandidate(
        changed=changed,
        source_operation_receipt_sha256=RECEIPT.source_receipt_sha256,
        source_proof=proof,
        source_incarnation=evidence,
        locator_projection=projection,
    )


def _finalize(store, request, created, candidate, *, operation_id, revalidator):
    return store.finalize_page_lifecycle_rename_catalog(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        candidate,
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        fencing_generation=1,
        expected_command_version=3,
        source_revalidator=revalidator,
    )


def test_changed_rename_finalizes_binding_projection_attestation_and_partial_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    operation_id = "rename-catalog-changed"
    created, _ = _source_applied(prepared, request, operation_id=operation_id)
    candidate = _candidate(store, request, changed=True)

    result = _finalize(
        store,
        request,
        created,
        candidate,
        operation_id=operation_id,
        revalidator=lambda value: value == candidate,
    )
    assert result["contract"] == "np2c_page_lifecycle_rename_finalize_v2"
    assert result["changed"] is True
    assert result["full_sweep_required"] is True
    assert result["binding_version"] == request.expected_binding_version + 1
    assert result["materialization_state"] == "partial"
    assert result["tree_version"] == request.expected_tree_version
    assert result["workspace_version"] == request.base_version + 1

    con = store._read_connection()
    try:
        binding = con.execute(
            "SELECT state,row_version,source_revision,source_sha256 "
            "FROM workspace_page_sources WHERE workspace_id=? "
            "AND source_owner_subject_id=? AND source_page_id=?",
            (request.workspace_id, request.source_owner_subject_id, request.source_page_id),
        ).fetchone()
        assert tuple(binding) == (
            "active",
            result["binding_version"],
            candidate.source_proof.revision,
            candidate.source_proof.content_sha256,
        )
        node = con.execute(
            "SELECT binding_version,source_change_generation,source_receipt_sha256,"
            "structure_sha256 "
            "FROM workspace_page_tree_nodes WHERE workspace_id=? "
            "AND source_owner_subject_id=? AND source_page_id=?",
            (request.workspace_id, request.source_owner_subject_id, request.source_page_id),
        ).fetchone()
        assert tuple(node) == (
            result["binding_version"],
            candidate.source_incarnation.change_generation,
            candidate.source_incarnation.receipt_sha256,
            result["tree_node_structure_sha256"],
        )
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_source_incarnation_attestations "
            "WHERE workspace_id=? AND source_owner_subject_id=? AND source_page_id=?",
            (request.workspace_id, request.source_owner_subject_id, request.source_page_id),
        ).fetchone()[0] == 2
        operation = con.execute(
            "SELECT result_json FROM workspace_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0]
        encoded = operation
        assert str(prepared["child"]) not in encoded
        assert "owner" not in encoded
        assert "reviewer" not in encoded
        assert json.loads(encoded) == result
    finally:
        con.close()
    health = store.deep_health()
    assert health["healthy"] is True, health["issues"]

    replay = _finalize(
        store,
        request,
        created,
        candidate,
        operation_id=operation_id,
        revalidator=lambda _value: (_ for _ in ()).throw(
            AssertionError("source revalidation ran during replay")
        ),
    )
    assert replay == result
    store.set_member(
        "reviewer",
        request.workspace_id,
        "later-member",
        role="member",
        base_version=result["workspace_version"],
        operation_id="post-rename-unrelated-workspace-change",
    )
    assert _finalize(
        store,
        request,
        created,
        candidate,
        operation_id=operation_id,
        revalidator=lambda _value: (_ for _ in ()).throw(
            AssertionError("source revalidation ran after unrelated mutation")
        ),
    ) == result
    assert store.deep_health()["healthy"] is True


def test_noop_rename_finalizes_without_catalog_generation_bumps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    operation_id = "rename-catalog-noop"
    created, _ = _source_applied(prepared, request, operation_id=operation_id)
    candidate = _candidate(store, request, changed=False)
    result = _finalize(
        store,
        request,
        created,
        candidate,
        operation_id=operation_id,
        revalidator=lambda value: value == candidate,
    )
    assert result["changed"] is False
    assert result["full_sweep_required"] is False
    assert result["binding_version"] == request.expected_binding_version
    assert result["workspace_version"] == request.base_version
    assert result["materialization_state"] == "complete"
    assert result["tree_state_version"] == result["previous_tree_state_version"]
    health = store.deep_health()
    assert health["healthy"] is True, health["issues"]


def test_changed_rename_outbox_fault_rolls_back_every_catalog_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    operation_id = "rename-catalog-rollback"
    created, _ = _source_applied(prepared, request, operation_id=operation_id)
    candidate = _candidate(store, request, changed=True)
    before = Path(store.db_path).read_bytes()
    real_write = store._write_connection

    def faulting_write():
        con = real_write()
        con.execute(
            """
            CREATE TEMP TRIGGER test_rename_finalize_outbox_fault
            BEFORE INSERT ON workspace_outbox
            WHEN NEW.event_type='workspace.page_lifecycle_finalized'
            BEGIN SELECT RAISE(ABORT,'injected rename finalize fault'); END
            """
        )
        return con

    monkeypatch.setattr(store, "_write_connection", faulting_write)
    with pytest.raises(sqlite3.IntegrityError, match="injected rename finalize fault"):
        _finalize(
            store,
            request,
            created,
            candidate,
            operation_id=operation_id,
            revalidator=lambda value: value == candidate,
        )
    monkeypatch.setattr(store, "_write_connection", real_write)
    assert Path(store.db_path).read_bytes() == before


def test_unconfigured_raw_writer_cannot_refresh_active_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    con = store._write_connection()
    try:
        before = con.execute(
            "SELECT row_version,source_revision,source_sha256 FROM workspace_page_sources "
            "WHERE workspace_id=? AND source_owner_subject_id=? AND source_page_id=?",
            (request.workspace_id, request.source_owner_subject_id, request.source_page_id),
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="invalid page source lifecycle"):
            con.execute(
                """
                UPDATE workspace_page_sources
                SET source_revision=source_revision+1,source_sha256=?,
                    row_version=row_version+1,updated_at=updated_at+1
                WHERE workspace_id=? AND source_owner_subject_id=? AND source_page_id=?
                """,
                (
                    "sha256:" + "f" * 64,
                    request.workspace_id,
                    request.source_owner_subject_id,
                    request.source_page_id,
                ),
            )
        con.rollback()
        after = con.execute(
            "SELECT row_version,source_revision,source_sha256 FROM workspace_page_sources "
            "WHERE workspace_id=? AND source_owner_subject_id=? AND source_page_id=?",
            (request.workspace_id, request.source_owner_subject_id, request.source_page_id),
        ).fetchone()
        assert tuple(after) == tuple(before)
    finally:
        con.close()


def test_callback_lease_expiry_leaves_changed_candidate_write_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    clock = [int(time.time())]
    store._trusted_clock = lambda: clock[0]
    operation_id = "rename-catalog-expiry"
    created = store.prepare_page_lifecycle_command(
        "reviewer", request, operation_id=operation_id
    )
    store.claim_page_lifecycle_command(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        lease_seconds=5,
        expected_command_version=1,
    )
    store.record_page_lifecycle_source_applied(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        RECEIPT,
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        fencing_generation=1,
        expected_command_version=2,
        source_revalidator=lambda value: value == RECEIPT,
    )
    candidate = _candidate(store, request, changed=True)
    before = Path(store.db_path).read_bytes()

    def expire(value):
        assert value == candidate
        clock[0] += 5
        return True

    with pytest.raises(WorkspacePageLifecycleLeaseConflict):
        _finalize(
            store,
            request,
            created,
            candidate,
            operation_id=operation_id,
            revalidator=expire,
        )
    assert Path(store.db_path).read_bytes() == before


def test_workspace_cas_drift_blocks_source_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    operation_id = "rename-catalog-workspace-drift"
    created, _ = _source_applied(prepared, request, operation_id=operation_id)
    candidate = _candidate(store, request, changed=True)
    store.set_member(
        "reviewer",
        request.workspace_id,
        "later-member",
        role="member",
        base_version=request.base_version,
        operation_id="concurrent-workspace-change",
    )
    source_calls = 0

    def must_not_run(_value):
        nonlocal source_calls
        source_calls += 1
        return True

    with pytest.raises(WorkspaceRevisionConflict):
        _finalize(
            store,
            request,
            created,
            candidate,
            operation_id=operation_id,
            revalidator=must_not_run,
        )
    assert source_calls == 0

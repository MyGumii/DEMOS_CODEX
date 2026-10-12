from __future__ import annotations

import json
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1.notion_workspace_store import (
    LegacyFileSourceLocator,
    LegacyPageIncarnationEvidence,
    LegacyPageLocatorProjection,
    LegacyPageSourceProof,
    LegacyPublicSourceLocator,
    WorkspaceStore,
    catalog_path_for_user_data_root,
)


def _proof(*, actor: str, revision: int, digest: str) -> LegacyPageSourceProof:
    return LegacyPageSourceProof(
        actor_subject_id=actor,
        owner_subject_id="owner",
        page_id="page-1",
        revision=revision,
        updated_at=100 + revision,
        content_sha256="sha256:" + digest * 64,
    )


def _evidence(**changes) -> LegacyPageIncarnationEvidence:
    values = {
        "owner_subject_id": "owner",
        "page_id": "page-1",
        "incarnation_id": "inc_" + "a" * 32,
        "incarnation_generation": 1,
        "incarnation_retired": False,
        "source_epoch": 2,
        "full_sweep_generation": 3,
        "source_device": 4,
        "source_inode": 5,
        "source_link_count": 1,
        "change_generation": 6,
        "change_mask": 31,
        "source_exists": True,
        "source_active": True,
    }
    values.update(changes)
    values["receipt_sha256"] = store_module.legacy_incarnation_receipt_sha256(
        source_owner_id=values["owner_subject_id"],
        page_id=values["page_id"],
        incarnation_id=values["incarnation_id"],
        incarnation_generation=values["incarnation_generation"],
        incarnation_retired=values["incarnation_retired"],
        change_generation=values["change_generation"],
        change_mask=values["change_mask"],
        source_exists=values["source_exists"],
        source_active=values["source_active"],
        source_epoch=values["source_epoch"],
        full_sweep_generation=values["full_sweep_generation"],
        source_device=values["source_device"],
        source_inode=values["source_inode"],
        source_link_count=values["source_link_count"],
    )
    return LegacyPageIncarnationEvidence(**values)


def _projection(version: int, *, actor: str = "owner") -> LegacyPageLocatorProjection:
    return LegacyPageLocatorProjection(
        actor_subject_id=actor,
        owner_subject_id="owner",
        page_id="page-1",
        binding_version=version,
        file_sources=(LegacyFileSourceLocator("f_" + "b" * 32),),
        public_sources=(
            LegacyPublicSourceLocator("sha256:" + "c" * 64, False),
        ),
    )


def _quarantined(tmp_path) -> tuple[WorkspaceStore, str, dict]:
    store = WorkspaceStore(
        catalog_path_for_user_data_root(tmp_path / "user-data"),
        trusted_clock=lambda: 1_000,
    )
    workspace = store.create_workspace(
        "reviewer", name="Full rebind", operation_id="create-workspace"
    )["workspace"]
    added = store.set_member(
        "reviewer",
        workspace["id"],
        "owner",
        role="member",
        base_version=workspace["version"],
        operation_id="add-owner",
    )
    old_proof = _proof(actor="reviewer", revision=1, digest="1")
    staged = store.stage_page_source_binding(
        "reviewer",
        workspace["id"],
        old_proof,
        base_version=added["workspace"]["version"],
        operation_id="stage-old",
    )
    active = store.activate_page_source_binding(
        "reviewer",
        workspace["id"],
        old_proof,
        source_revalidator=lambda _candidate: True,
        expected_binding_version=staged["binding"]["version"],
        base_version=staged["workspace"]["version"],
        operation_id="activate-old",
    )
    quarantined = store.quarantine_page_source_if_stale(
        "reviewer",
        workspace["id"],
        "owner",
        "page-1",
        source_revalidator=lambda _candidate: False,
        expected_binding_version=active["binding"]["version"],
        base_version=active["workspace"]["version"],
        operation_id="quarantine-old",
    )
    return store, workspace["id"], quarantined


def _rebind(store: WorkspaceStore, workspace_id: str, quarantined: dict, callback):
    previous_version = int(quarantined["binding"]["version"])
    return store.rebind_quarantined_page_source_incarnation(
        "reviewer",
        workspace_id,
        _proof(actor="owner", revision=2, digest="2"),
        _evidence(),
        _projection(previous_version + 1),
        source_revalidator=callback,
        expected_quarantined_binding_version=previous_version,
        base_version=int(quarantined["workspace"]["version"]),
        operation_id="rebind-page",
        source_quiesced_confirmed=True,
    )


def test_full_rebind_is_atomic_owner_canonical_ready_and_replays_before_io(
    tmp_path,
) -> None:
    store, workspace_id, quarantined = _quarantined(tmp_path)
    before_workspace = quarantined["workspace"]
    calls = 0

    def revalidate(proof, evidence, projection):
        nonlocal calls
        calls += 1
        assert proof.actor_subject_id == proof.owner_subject_id == "owner"
        assert evidence.owner_subject_id == "owner"
        assert projection.actor_subject_id == "owner"
        return True

    first = _rebind(store, workspace_id, quarantined, revalidate)
    replay = _rebind(store, workspace_id, quarantined, revalidate)
    assert replay == first
    assert calls == 1
    assert first["binding"] == {
        "workspace_id": workspace_id,
        "source_owner_subject_id": "owner",
        "source_page_id": "page-1",
        "state": "active",
        "version": quarantined["binding"]["version"] + 1,
    }
    assert first["previous_binding"]["state"] == "quarantined"
    assert first["previous_binding"]["version"] == quarantined["binding"]["version"]
    assert first["source_proof"]["actor_subject_id"] == "owner"
    assert first["workspace"]["version"] == before_workspace["version"] + 1
    assert (
        first["workspace"]["security_epoch"]
        == before_workspace["security_epoch"] + 1
    )

    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row
    try:
        binding = con.execute("SELECT * FROM workspace_page_sources").fetchone()
        assert binding["state"] == "active"
        assert binding["bound_by_subject_id"] == "reviewer"
        assert binding["source_sha256"] == first["source_proof"]["content_sha256"]
        operation = con.execute(
            "SELECT action,result_json FROM workspace_operations "
            "WHERE operation_id='rebind-page'"
        ).fetchone()
        assert operation["action"] == store_module.INCARNATION_REBIND_ACTION
        assert json.loads(operation["result_json"]) == first
        event_payloads = con.execute(
            """
            SELECT payload_json FROM workspace_audit_events
            WHERE operation_id='rebind-page'
            UNION ALL
            SELECT payload_json FROM workspace_outbox
            WHERE operation_id='rebind-page'
            """
        ).fetchall()
        expected_event = {
            "previous_binding": first["previous_binding"],
            "source_proof": first["source_proof"],
            "attestation": first["attestation"],
            "projection": first["projection"],
        }
        assert len(event_payloads) == 2
        assert all(json.loads(row[0]) == expected_event for row in event_payloads)
    finally:
        con.close()

    readiness = store.incarnation_attestation_readiness("reviewer", workspace_id)
    assert readiness["ready_for_resolver_cutover"] is True
    assert store.deep_health()["healthy"] is True
    batch = store.scan_active_page_source_bindings("reviewer", workspace_id)
    assert batch.items[0].projection_actor_subject_id == "owner"
    assert store.get_page_source_incarnation_rebind_replay(
        "reviewer",
        workspace_id,
        "owner",
        "page-1",
        expected_quarantined_binding_version=quarantined["binding"]["version"],
        operation_id="rebind-page",
    ) == first


def test_rebind_replay_and_readiness_reject_corrupt_immutable_commitment(
    tmp_path,
) -> None:
    store, workspace_id, quarantined = _quarantined(tmp_path)
    _rebind(store, workspace_id, quarantined, lambda *_args: True)

    con = sqlite3.connect(store.db_path)
    try:
        trigger_sql = con.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type='trigger'
              AND name='trg_workspace_operations_immutable_update'
            """
        ).fetchone()[0]
        con.execute("DROP TRIGGER trg_workspace_operations_immutable_update")
        con.execute(
            """
            UPDATE workspace_operations SET result_json='{}'
            WHERE actor_subject_id='reviewer' AND operation_id='rebind-page'
            """
        )
        con.execute(trigger_sql)
        con.commit()
    finally:
        con.close()

    assert store.incarnation_attestation_readiness("reviewer", workspace_id)[
        "ready_for_resolver_cutover"
    ] is False
    callback_calls = 0

    def must_not_revalidate(*_args):
        nonlocal callback_calls
        callback_calls += 1
        return True

    with pytest.raises(store_module.WorkspaceSubstrateUnavailable):
        _rebind(store, workspace_id, quarantined, must_not_revalidate)
    with pytest.raises(store_module.WorkspaceSubstrateUnavailable):
        store.get_page_source_incarnation_rebind_replay(
            "reviewer",
            workspace_id,
            "owner",
            "page-1",
            expected_quarantined_binding_version=quarantined["binding"]["version"],
            operation_id="rebind-page",
        )
    assert callback_calls == 0


def test_rebind_refusals_and_late_ledger_failure_leave_quarantine_unchanged(
    tmp_path, monkeypatch
) -> None:
    store, workspace_id, quarantined = _quarantined(tmp_path)
    calls = 0

    def must_not_run(*_args):
        nonlocal calls
        calls += 1
        return True

    previous_version = quarantined["binding"]["version"]
    with pytest.raises(PermissionError, match="owner-canonical"):
        store.rebind_quarantined_page_source_incarnation(
            "reviewer",
            workspace_id,
            _proof(actor="reviewer", revision=2, digest="2"),
            _evidence(),
            _projection(previous_version + 1),
            source_revalidator=must_not_run,
            expected_quarantined_binding_version=previous_version,
            base_version=quarantined["workspace"]["version"],
            operation_id="bad-proof-actor",
            source_quiesced_confirmed=True,
        )
    with pytest.raises(ValueError, match="quiescence"):
        store.rebind_quarantined_page_source_incarnation(
            "reviewer",
            workspace_id,
            _proof(actor="owner", revision=2, digest="2"),
            _evidence(),
            _projection(previous_version + 1),
            source_revalidator=must_not_run,
            expected_quarantined_binding_version=previous_version,
            base_version=quarantined["workspace"]["version"],
            operation_id="not-quiesced",
            source_quiesced_confirmed=False,
        )
    assert calls == 0

    con = sqlite3.connect(store.db_path)
    try:
        duplicate_audit_id = con.execute(
            "SELECT id FROM workspace_audit_events ORDER BY created_at,id LIMIT 1"
        ).fetchone()[0]
        before = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "workspace_page_source_incarnation_attestations",
                "workspace_page_source_projections",
                "workspace_operations",
                "workspace_audit_events",
                "workspace_outbox",
            )
        }
    finally:
        con.close()
    real_new_id = store_module._new_id

    def collide(prefix: str) -> str:
        return duplicate_audit_id if prefix == "waud" else real_new_id(prefix)

    monkeypatch.setattr(store_module, "_new_id", collide)
    with pytest.raises(sqlite3.IntegrityError):
        _rebind(store, workspace_id, quarantined, lambda *_args: True)

    con = sqlite3.connect(store.db_path)
    try:
        assert con.execute(
            "SELECT state,row_version FROM workspace_page_sources"
        ).fetchone() == ("quarantined", previous_version)
        after = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        assert after == before
    finally:
        con.close()

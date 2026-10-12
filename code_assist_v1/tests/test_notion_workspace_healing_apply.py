from __future__ import annotations

from dataclasses import replace
import json
import sqlite3

import pytest

from code_assist_v1 import notion_store
from code_assist_v1 import notion_legacy_changes
from code_assist_v1 import notion_workspace_healing as healing
from code_assist_v1.notion_workspace_projection import (
    capture_exact_owner_healing_candidate,
    revalidate_exact_owner_healing_candidate,
)
from code_assist_v1.notion_legacy_change_migration import (
    apply_plan as apply_change_tracking_plan,
    build_plan as build_change_tracking_plan,
)
from code_assist_v1.notion_workspace_store import (
    LegacyPageQuarantineCandidate,
    WorkspaceHealingLeaseConflict,
    WorkspacePageProjectionAmbiguous,
    WorkspacePageSourceSuperseded,
    WorkspaceHealingSourceFenceConflict,
    WorkspaceOperationConflict,
    WorkspaceStore,
    WorkspaceSubstrateUnavailable,
    catalog_path_for_user_data_root,
)


TOKEN = "hlease_" + "d" * 32


def _fixture(tmp_path, monkeypatch, *, trusted_clock=None):
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    store = WorkspaceStore(
        catalog_path_for_user_data_root(tmp_path), trusted_clock=trusted_clock
    )
    workspace = store.create_workspace(
        "owner", name="Healing apply", operation_id="create-healing-apply"
    )["workspace"]
    page = notion_store.create_page("owner", title="before")["page"]
    proof = notion_store.capture_legacy_page_source_proof(
        "owner", "owner", page["id"]
    )
    bound = store.bind_page_source(
        "owner",
        workspace["id"],
        proof,
        source_revalidator=notion_store.revalidate_legacy_page_source_proof,
        base_version=workspace["version"],
        operation_id="bind-healing-apply",
    )
    source = tmp_path / "owner" / "notion" / "notion.db"
    backup_dir = tmp_path / "watermark-backup"
    backup_dir.mkdir()
    plan = build_change_tracking_plan(
        user_data_root=tmp_path,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    apply_change_tracking_plan(
        plan,
        confirm_checksum=plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backup_dir / "before.db",
    )
    run = store.start_healing_run(
        "owner",
        workspace["id"],
        worker="worker.apply",
        lease_token=TOKEN,
        lease_seconds=120,
        operation_id="start-locator-apply",
        healing_mode="locator_apply",
    )["run"]
    return store, workspace, page, bound, source, run


def _candidate(store, workspace, page, source, binding_version):
    return capture_exact_owner_healing_candidate(
        user_data_root=source.parents[2],
        source_owner_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=store.db_path,
        expected_binding_version=binding_version,
        source_quiesced_confirmed=True,
    )


def _fence(store, workspace, run, candidate, operation_id="fence-owner"):
    return store.fence_healing_source_generation(
        "owner",
        workspace["id"],
        run["id"],
        worker="worker.apply",
        lease_token=TOKEN,
        fencing_generation=run["lease"]["fencing_generation"],
        expected_version=run["version"],
        source_owner_subject_id="owner",
        source_fence=candidate.source_fence(),
        source_fence_revalidator=lambda value: value == candidate.source_fence(),
        operation_id=operation_id,
    )


def _apply(store, workspace, run, candidate, operation_id, revalidator=None):
    callback = revalidator or (lambda value: value == candidate)
    return store.apply_page_source_healing(
        "owner",
        workspace["id"],
        run["id"],
        worker="worker.apply",
        lease_token=TOKEN,
        fencing_generation=run["lease"]["fencing_generation"],
        expected_run_version=run["version"],
        candidate=candidate,
        candidate_revalidator=callback,
        operation_id=operation_id,
    )


def _quarantine_candidate(candidate, reason):
    return LegacyPageQuarantineCandidate(
        workspace_id=candidate.workspace_id,
        workspace_version=candidate.workspace_version,
        owner_subject_id=candidate.owner_subject_id,
        page_id=candidate.page_id,
        binding_version=candidate.binding_version,
        binding_state=candidate.binding_state,
        bound_by_subject_id=candidate.bound_by_subject_id,
        proof_actor_subject_id=candidate.proof_actor_subject_id,
        source_epoch=candidate.source_epoch,
        full_sweep_generation=candidate.full_sweep_generation,
        source_device=candidate.source_device,
        source_inode=candidate.source_inode,
        source_link_count=candidate.source_link_count,
        change_generation=candidate.change_generation,
        change_mask=candidate.change_mask,
        source_exists=candidate.source_exists,
        source_active=candidate.source_active,
        quarantine_reason=reason,
    )


def test_changed_source_proof_and_locators_replace_atomically_and_replay(tmp_path, monkeypatch):
    store, workspace, page, bound, source, run = _fixture(tmp_path, monkeypatch)
    uploaded = notion_store.upload_file(
        "owner", page["id"], filename="hidden.txt", raw=b"classified"
    )
    notion_store.update_page("owner", page["id"], {"title": "after"})
    candidate = _candidate(
        store, workspace, page, source, bound["binding"]["version"]
    )
    _fence(store, workspace, run, candidate)
    before_counts = store.audit_counts()
    first = _apply(store, workspace, run, candidate, "heal-page")
    assert first["healing"]["changed"] is True
    assert first["healing"]["disposition"] == "stale"
    assert first["healing"]["binding_version"] == candidate.binding_version + 1
    assert first["healing"]["file_source_count"] == 1
    assert first["healing"]["source_receipt"] == {
        "source_epoch": candidate.source_epoch,
        "full_sweep_generation": candidate.full_sweep_generation,
        "change_generation": candidate.change_generation,
        "change_mask": candidate.change_mask,
        "source_exists": candidate.source_exists,
        "source_active": candidate.source_active,
    }

    def must_not_repeat(_candidate):
        raise AssertionError("committed healing replay must not recapture source")

    replay = _apply(
        store, workspace, run, candidate, "heal-page", revalidator=must_not_repeat
    )
    assert replay == first
    assert store.audit_counts() == {
        key: value + 1 for key, value in before_counts.items()
    }
    con = sqlite3.connect(store.db_path)
    try:
        binding = con.execute(
            "SELECT state,row_version,source_sha256 FROM workspace_page_sources "
            "WHERE source_page_id=?",
            (page["id"],),
        ).fetchone()
        files = con.execute(
            "SELECT source_file_id,binding_version FROM workspace_file_sources"
        ).fetchall()
    finally:
        con.close()
    assert binding == (
        "active",
        candidate.binding_version + 1,
        candidate.source_proof.content_sha256,
    )
    assert files == [(uploaded["id"], candidate.binding_version + 1)]


def test_missing_projection_is_repaired_without_rewriting_binding_proof(
    tmp_path, monkeypatch
):
    store, workspace, page, bound, source, run = _fixture(tmp_path, monkeypatch)
    candidate = _candidate(
        store, workspace, page, source, bound["binding"]["version"]
    )
    _fence(store, workspace, run, candidate)
    before_workspace = store.get_workspace("owner", workspace["id"])

    repaired = _apply(store, workspace, run, candidate, "heal-missing-projection")

    assert repaired["healing"]["disposition"] == "safe_miss"
    assert repaired["healing"]["changed"] is True
    assert repaired["healing"]["binding_version"] == candidate.binding_version
    assert repaired["workspace"]["version"] == before_workspace["version"] + 1
    con = sqlite3.connect(store.db_path)
    try:
        binding_version = con.execute(
            "SELECT row_version FROM workspace_page_sources WHERE source_page_id=?",
            (page["id"],),
        ).fetchone()[0]
        projection_version = con.execute(
            "SELECT binding_version FROM workspace_page_source_projections "
            "WHERE source_page_id=?",
            (page["id"],),
        ).fetchone()[0]
    finally:
        con.close()
    assert binding_version == candidate.binding_version
    assert projection_version == candidate.binding_version
    assert store.verify_deep_health()["healthy"] is True


@pytest.mark.parametrize(
    ("reason", "disposition"),
    [
        ("legacy_permission_revoked", "permission_mismatch"),
        ("unsafe_path", "unsafe_path"),
        ("ambiguous", "ambiguous"),
        ("oversize", "oversize"),
    ],
)
def test_source_fenced_permanent_refusal_atomically_quarantines_projection(
    tmp_path, monkeypatch, reason, disposition
):
    store, workspace, page, bound, source, run = _fixture(tmp_path, monkeypatch)
    candidate = _candidate(
        store, workspace, page, source, bound["binding"]["version"]
    )
    _fence(store, workspace, run, candidate)
    prepared = _apply(store, workspace, run, candidate, "prepare-quarantine")
    assert prepared["healing"]["disposition"] == "safe_miss"

    current = _candidate(
        store, workspace, page, source, bound["binding"]["version"]
    )
    quarantine = _quarantine_candidate(current, reason)
    before_counts = store.audit_counts()
    first = _apply(store, workspace, run, quarantine, f"quarantine-{reason}")
    assert first["healing"]["disposition"] == disposition
    assert first["healing"]["changed"] is True

    def no_second_source_read(_candidate):
        raise AssertionError("effect replay must precede source revalidation")

    replay = _apply(
        store,
        workspace,
        run,
        quarantine,
        f"quarantine-{reason}",
        revalidator=no_second_source_read,
    )
    assert replay == first
    assert store.audit_counts() == {
        key: value + 1 for key, value in before_counts.items()
    }
    con = sqlite3.connect(store.db_path)
    try:
        row = con.execute(
            "SELECT state,row_version,quarantine_reason,"
            "quarantine_source_generation FROM workspace_page_sources "
            "WHERE workspace_id=? AND source_page_id=?",
            (workspace["id"], page["id"]),
        ).fetchone()
        projection_count = con.execute(
            "SELECT COUNT(*) FROM workspace_page_source_projections "
            "WHERE workspace_id=? AND source_page_id=?",
            (workspace["id"], page["id"]),
        ).fetchone()[0]
    finally:
        con.close()
    assert row == (
        "quarantined",
        quarantine.binding_version + 1,
        reason,
        quarantine.change_generation,
    )
    assert projection_count == 0
    assert store.verify_deep_health()["healthy"] is True


def test_public_digest_collision_atomically_quarantines_only_the_target(
    tmp_path, monkeypatch
):
    store, workspace, page, bound, source, run = _fixture(tmp_path, monkeypatch)
    second = notion_store.create_page("owner", title="second")["page"]
    second_proof = notion_store.capture_legacy_page_source_proof(
        "owner", "owner", second["id"]
    )
    current_workspace = store.get_workspace("owner", workspace["id"])
    second_bound = store.bind_page_source(
        "owner",
        workspace["id"],
        second_proof,
        source_revalidator=notion_store.revalidate_legacy_page_source_proof,
        base_version=current_workspace["version"],
        operation_id="bind-second-collision-page",
    )
    notion_store.enable_public_link("owner", page["id"])

    first_candidate = _candidate(
        store, workspace, page, source, bound["binding"]["version"]
    )
    assert len(first_candidate.public_sources) == 1
    _fence(store, workspace, run, first_candidate)
    _apply(store, workspace, run, first_candidate, "heal-public-owner")

    second_candidate = _candidate(
        store,
        workspace,
        second,
        source,
        second_bound["binding"]["version"],
    )
    forged_collision = replace(
        second_candidate, public_sources=first_candidate.public_sources
    )
    before_workspace = store.get_workspace("owner", workspace["id"])
    before_counts = store.audit_counts()
    result = _apply(
        store,
        workspace,
        run,
        forged_collision,
        "heal-public-collision",
        revalidator=lambda _value: True,
    )

    assert result["healing"]["disposition"] == "ambiguous"
    assert result["healing"]["changed"] is True
    assert store.get_workspace("owner", workspace["id"])["version"] == (
        before_workspace["version"] + 1
    )
    assert store.audit_counts() == {
        key: value + 1 for key, value in before_counts.items()
    }
    con = sqlite3.connect(store.db_path)
    try:
        second_state = con.execute(
            "SELECT state,row_version,source_sha256,quarantine_reason,"
            "quarantine_source_generation FROM workspace_page_sources "
            "WHERE source_page_id=?",
            (second["id"],),
        ).fetchone()
        second_projection = con.execute(
            "SELECT COUNT(*) FROM workspace_page_source_projections "
            "WHERE source_page_id=?",
            (second["id"],),
        ).fetchone()[0]
        first_public = con.execute(
            "SELECT COUNT(*) FROM workspace_public_sources WHERE source_page_id=?",
            (page["id"],),
        ).fetchone()[0]
    finally:
        con.close()
    assert second_state == (
        "quarantined",
        second_bound["binding"]["version"] + 1,
        second_bound["binding"]["source_sha256"],
        "ambiguous",
        forged_collision.change_generation,
    )
    assert second_projection == 0
    assert first_public == 1
    assert store.verify_deep_health()["healthy"] is True

    # The bounded batch classifies this permanent collision as ambiguous and
    # can therefore advance its durable cursor without exposing the digest.


def test_candidate_drift_is_zero_write_and_source_fence_mismatch_fails_closed(
    tmp_path, monkeypatch
):
    store, workspace, page, bound, source, run = _fixture(tmp_path, monkeypatch)
    notion_store.update_page("owner", page["id"], {"title": "candidate"})
    candidate = _candidate(
        store, workspace, page, source, bound["binding"]["version"]
    )
    _fence(store, workspace, run, candidate)
    baseline = store.audit_counts()
    with pytest.raises(WorkspaceHealingSourceFenceConflict):
        _apply(
            store,
            workspace,
            run,
            candidate,
            "reject-drift",
            revalidator=lambda _value: False,
        )
    assert store.audit_counts() == baseline
    assert revalidate_exact_owner_healing_candidate(
        candidate,
        user_data_root=tmp_path,
        expected_source_db_path=source,
        expected_catalog_db_path=store.db_path,
        source_quiesced_confirmed=True,
    )


def test_archive_quarantines_and_restore_requires_explicit_rebind(
    tmp_path, monkeypatch
):
    store, workspace, page, bound, source, run = _fixture(tmp_path, monkeypatch)
    notion_store.archive_page("owner", page["id"])
    archived = _candidate(
        store, workspace, page, source, bound["binding"]["version"]
    )
    assert archived.source_exists is True
    assert archived.source_active is False
    _fence(store, workspace, run, archived)
    quarantined = _apply(store, workspace, run, archived, "heal-archive")
    assert quarantined["healing"]["disposition"] == "source_missing"
    assert quarantined["healing"]["changed"] is True

    notion_store.restore_page("owner", page["id"])
    restored = _candidate(
        store,
        workspace,
        page,
        source,
        quarantined["healing"]["binding_version"],
    )
    assert restored.binding_state == "quarantined"
    assert restored.source_active is True
    with pytest.raises(WorkspaceHealingSourceFenceConflict):
        _apply(store, workspace, run, restored, "heal-restore-old-fence")
    store.fail_healing_run(
        "owner",
        workspace["id"],
        run["id"],
        worker="worker.apply",
        lease_token=TOKEN,
        fencing_generation=run["lease"]["fencing_generation"],
        expected_version=run["version"],
        failure_code="source_generation_changed",
        operation_id="fail-old-source-generation",
    )
    next_run = store.start_healing_run(
        "owner",
        workspace["id"],
        worker="worker.apply",
        lease_token=TOKEN,
        lease_seconds=120,
        operation_id="start-restore-pass",
        healing_mode="locator_apply",
    )["run"]
    _fence(store, workspace, next_run, restored, operation_id="fence-restored-owner")
    with pytest.raises(WorkspacePageSourceSuperseded, match="explicit rebind"):
        _apply(store, workspace, next_run, restored, "heal-restore")
    con = sqlite3.connect(store.db_path)
    try:
        state = con.execute(
            "SELECT state,row_version,quarantine_reason FROM workspace_page_sources "
            "WHERE source_page_id=?",
            (page["id"],),
        ).fetchone()
    finally:
        con.close()
    assert state == (
        "quarantined",
        quarantined["healing"]["binding_version"],
        "source_archived",
    )


def test_second_proof_refresh_and_projected_archive_replace_prior_generation(
    tmp_path, monkeypatch
):
    store, workspace, page, bound, source, first_run = _fixture(tmp_path, monkeypatch)
    notion_store.update_page("owner", page["id"], {"title": "generation two"})
    first_candidate = _candidate(
        store, workspace, page, source, bound["binding"]["version"]
    )
    _fence(store, workspace, first_run, first_candidate)
    first = _apply(store, workspace, first_run, first_candidate, "heal-generation-two")
    assert first["healing"]["binding_version"] == 2
    assert first["healing"]["disposition"] == "stale"
    store.fail_healing_run(
        "owner",
        workspace["id"],
        first_run["id"],
        worker="worker.apply",
        lease_token=TOKEN,
        fencing_generation=first_run["lease"]["fencing_generation"],
        expected_version=first_run["version"],
        failure_code="test_next_generation",
        operation_id="finish-first-generation-run",
    )

    notion_store.update_page("owner", page["id"], {"title": "generation three"})
    second_candidate = _candidate(store, workspace, page, source, 2)
    second_run = store.start_healing_run(
        "owner",
        workspace["id"],
        worker="worker.apply",
        lease_token=TOKEN,
        lease_seconds=120,
        operation_id="start-second-generation-run",
        healing_mode="locator_apply",
    )["run"]
    _fence(store, workspace, second_run, second_candidate, "fence-second-generation")
    second = _apply(
        store, workspace, second_run, second_candidate, "heal-generation-three"
    )
    assert second["healing"]["binding_version"] == 3
    assert second["healing"]["disposition"] == "stale"

    store.fail_healing_run(
        "owner",
        workspace["id"],
        second_run["id"],
        worker="worker.apply",
        lease_token=TOKEN,
        fencing_generation=second_run["lease"]["fencing_generation"],
        expected_version=second_run["version"],
        failure_code="test_archive_generation",
        operation_id="finish-second-generation-run",
    )
    notion_store.archive_page("owner", page["id"])
    archived_candidate = _candidate(store, workspace, page, source, 3)
    archive_run = store.start_healing_run(
        "owner",
        workspace["id"],
        worker="worker.apply",
        lease_token=TOKEN,
        lease_seconds=120,
        operation_id="start-projected-archive-run",
        healing_mode="locator_apply",
    )["run"]
    _fence(store, workspace, archive_run, archived_candidate, "fence-projected-archive")
    archived = _apply(
        store, workspace, archive_run, archived_candidate, "heal-projected-archive"
    )
    assert archived["healing"]["binding_version"] == 4
    assert archived["healing"]["disposition"] == "source_missing"
    con = sqlite3.connect(store.db_path)
    try:
        state = con.execute(
            "SELECT state,row_version FROM workspace_page_sources WHERE source_page_id=?",
            (page["id"],),
        ).fetchone()
        projections = con.execute(
            "SELECT COUNT(*) FROM workspace_page_source_projections "
            "WHERE source_page_id=?",
            (page["id"],),
        ).fetchone()[0]
    finally:
        con.close()
    assert state == ("quarantined", 4)
    assert projections == 0
    assert store.verify_deep_health()["healthy"] is True


@pytest.mark.parametrize("existing_projection", [False, True])
def test_lease_expiry_during_candidate_callback_blocks_safe_miss_and_match(
    tmp_path, monkeypatch, existing_projection
):
    clock = {"now": 100}
    store, workspace, page, bound, source, run = _fixture(
        tmp_path, monkeypatch, trusted_clock=lambda: clock["now"]
    )
    candidate = _candidate(
        store, workspace, page, source, bound["binding"]["version"]
    )
    _fence(store, workspace, run, candidate)
    if existing_projection:
        _apply(store, workspace, run, candidate, "prepare-exact-projection")
        candidate = _candidate(
            store, workspace, page, source, bound["binding"]["version"]
        )
    before = store.audit_counts()

    def expire_then_accept(_candidate):
        clock["now"] = 221
        return True

    with pytest.raises(WorkspaceHealingLeaseConflict):
        _apply(
            store,
            workspace,
            run,
            candidate,
            f"expired-{'match' if existing_projection else 'safe-miss'}",
            revalidator=expire_then_accept,
        )
    assert store.audit_counts() == before


def test_bounded_core_applies_checkpoints_and_response_loss_replays_without_source_io(
    tmp_path, monkeypatch
):
    store, workspace, page, _bound, source, run = _fixture(tmp_path, monkeypatch)
    notion_store.update_page("owner", page["id"], {"title": "core changed"})
    kwargs = {
        "actor_subject_id": "owner",
        "workspace_id": workspace["id"],
        "run_id": run["id"],
        "worker": "worker.apply",
        "lease_token": TOKEN,
        "fencing_generation": run["lease"]["fencing_generation"],
        "expected_version": run["version"],
        "user_data_root": tmp_path,
        "expected_catalog_db_path": store.db_path,
        "source_db_for_owner": lambda owner: source if owner == "owner" else None,
        "source_quiesced_confirmed": True,
        "limit": 1,
    }
    first = healing.heal_one_locator_batch(store, **kwargs)
    assert first["run"]["status"] == "completed"
    assert first["run"]["version"] == 2
    assert first["healing_report"]["scanned"] == 1
    assert first["healing_report"]["visited"] == 1
    assert first["healing_report"]["stale"] == 1

    def no_source_after_commit(_owner):
        raise AssertionError("checkpoint replay must precede source resolution")

    replay = healing.heal_one_locator_batch(
        store,
        **{**kwargs, "source_db_for_owner": no_source_after_commit},
    )
    assert replay == first


def test_page_effect_operation_id_is_fenced_by_source_and_binding_generation():
    base = {
        "run_id": "wheal_effect-id",
        "fencing_generation": 3,
        "expected_version": 7,
        "workspace_id": "ws_effect-id",
        "owner": "owner",
        "page_id": "p_effect-id",
        "source_epoch": 11,
        "full_sweep_generation": 13,
        "binding_generation": 17,
    }
    expected = healing.healing_page_effect_operation_id(**base)
    assert expected.startswith("np2a-healing-page-effect-v2:")
    assert expected == healing.healing_page_effect_operation_id(**base)
    for field in (
        "source_epoch",
        "full_sweep_generation",
        "binding_generation",
    ):
        changed = {**base, field: base[field] + 1}
        assert healing.healing_page_effect_operation_id(**changed) != expected


def test_healing_effect_receipt_replay_is_exact_and_lease_scoped(
    tmp_path, monkeypatch
):
    store, workspace, page, bound, source, run = _fixture(tmp_path, monkeypatch)
    notion_store.update_page("owner", page["id"], {"title": "receipt ledger"})
    candidate = _candidate(
        store, workspace, page, source, bound["binding"]["version"]
    )
    _fence(store, workspace, run, candidate)
    with pytest.raises(WorkspaceHealingLeaseConflict):
        store.get_healing_source_fence(
            "owner",
            workspace["id"],
            run["id"],
            worker="worker.apply",
            lease_token="hlease_" + "e" * 32,
            fencing_generation=run["lease"]["fencing_generation"],
            expected_version=run["version"],
            source_owner_subject_id="owner",
        )

    operation_id = healing.healing_page_effect_operation_id(
        run_id=run["id"],
        fencing_generation=run["lease"]["fencing_generation"],
        expected_version=run["version"],
        workspace_id=workspace["id"],
        owner="owner",
        page_id=page["id"],
        source_epoch=candidate.source_epoch,
        full_sweep_generation=candidate.full_sweep_generation,
        binding_generation=candidate.binding_version,
    )
    _apply(store, workspace, run, candidate, operation_id)
    con = sqlite3.connect(store.db_path)
    try:
        immutable_trigger_sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='trg_workspace_operations_immutable_update'"
        ).fetchone()[0]
        con.execute("DROP TRIGGER trg_workspace_operations_immutable_update")
        raw = con.execute(
            "SELECT result_json FROM workspace_operations "
            "WHERE actor_subject_id='owner' AND operation_id=?",
            (operation_id,),
        ).fetchone()[0]
        corrupted = json.loads(raw)
        corrupted["healing"].pop("source_receipt")
        con.execute(
            "UPDATE workspace_operations SET result_json=? "
            "WHERE actor_subject_id='owner' AND operation_id=?",
            (json.dumps(corrupted, sort_keys=True, separators=(",", ":")), operation_id),
        )
        con.execute(immutable_trigger_sql)
        con.commit()
    finally:
        con.close()
    with pytest.raises(
        WorkspaceSubstrateUnavailable,
        match="workspace healing effect ledger is invalid",
    ):
        store.get_healing_effect_replay(
            "owner",
            workspace["id"],
            run["id"],
            worker="worker.apply",
            lease_token=TOKEN,
            fencing_generation=run["lease"]["fencing_generation"],
            expected_version=run["version"],
            expected_binding_version=candidate.binding_version,
            source_owner_subject_id="owner",
            source_page_id=page["id"],
            operation_id=operation_id,
        )


def test_effect_commit_before_checkpoint_replays_without_reopening_source(
    tmp_path, monkeypatch
):
    store, workspace, page, _bound, source, run = _fixture(tmp_path, monkeypatch)
    notion_store.update_page("owner", page["id"], {"title": "checkpoint crash"})
    kwargs = {
        "actor_subject_id": "owner",
        "workspace_id": workspace["id"],
        "run_id": run["id"],
        "worker": "worker.apply",
        "lease_token": TOKEN,
        "fencing_generation": run["lease"]["fencing_generation"],
        "expected_version": run["version"],
        "user_data_root": tmp_path,
        "expected_catalog_db_path": store.db_path,
        "source_db_for_owner": lambda owner: source if owner == "owner" else None,
        "source_quiesced_confirmed": True,
        "limit": 1,
    }
    committed_checkpoint = store.checkpoint_healing_run

    def lose_checkpoint(*_args, **_kwargs):
        raise RuntimeError("simulated checkpoint crash")

    monkeypatch.setattr(store, "checkpoint_healing_run", lose_checkpoint)
    with pytest.raises(RuntimeError, match="simulated checkpoint crash"):
        healing.heal_one_locator_batch(store, **kwargs)

    monkeypatch.setattr(store, "checkpoint_healing_run", committed_checkpoint)

    def no_source_after_effect(_owner):
        raise AssertionError("effect replay must precede source resolution")

    replay = healing.heal_one_locator_batch(
        store,
        **{**kwargs, "source_db_for_owner": no_source_after_effect},
    )
    assert replay["run"]["status"] == "completed"
    assert replay["healing_report"]["stale"] == 1


def test_core_checkpoints_only_after_committed_permanent_quarantine(
    tmp_path, monkeypatch
):
    store, workspace, page, bound, source, first_run = _fixture(tmp_path, monkeypatch)
    current = _candidate(
        store, workspace, page, source, bound["binding"]["version"]
    )
    _fence(store, workspace, first_run, current, "fence-before-permanent")
    prepared = _apply(
        store, workspace, first_run, current, "prepare-permanent-projection"
    )
    assert prepared["healing"]["disposition"] == "safe_miss"
    store.fail_healing_run(
        "owner",
        workspace["id"],
        first_run["id"],
        worker="worker.apply",
        lease_token=TOKEN,
        fencing_generation=first_run["lease"]["fencing_generation"],
        expected_version=first_run["version"],
        failure_code="test_next_pass",
        operation_id="fail-before-permanent-pass",
    )
    run = store.start_healing_run(
        "owner",
        workspace["id"],
        worker="worker.apply",
        lease_token=TOKEN,
        lease_seconds=120,
        operation_id="start-permanent-pass",
        healing_mode="locator_apply",
    )["run"]
    current = _candidate(
        store, workspace, page, source, bound["binding"]["version"]
    )
    quarantine = _quarantine_candidate(current, "unsafe_path")
    monkeypatch.setattr(
        healing,
        "capture_exact_owner_healing_outcome",
        lambda **_kwargs: quarantine,
    )
    monkeypatch.setattr(
        healing,
        "revalidate_exact_owner_healing_outcome",
        lambda _value, **_kwargs: True,
    )

    result = healing.heal_one_locator_batch(
        store,
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        run_id=run["id"],
        worker="worker.apply",
        lease_token=TOKEN,
        fencing_generation=run["lease"]["fencing_generation"],
        expected_version=run["version"],
        user_data_root=tmp_path,
        expected_catalog_db_path=store.db_path,
        source_db_for_owner=lambda owner: source if owner == "owner" else None,
        source_quiesced_confirmed=True,
        limit=1,
    )

    assert result["run"]["status"] == "completed"
    assert result["healing_report"]["unsafe_path"] == 1
    assert result["run"]["counters"]["unsafe_path"] == 1
    con = sqlite3.connect(store.db_path)
    try:
        binding = con.execute(
            "SELECT state,quarantine_reason FROM workspace_page_sources "
            "WHERE source_page_id=?",
            (page["id"],),
        ).fetchone()
        locators = con.execute(
            "SELECT COUNT(*) FROM workspace_page_source_projections "
            "WHERE source_page_id=?",
            (page["id"],),
        ).fetchone()[0]
    finally:
        con.close()
    assert binding == ("quarantined", "unsafe_path")
    assert locators == 0


def test_locator_batch_runs_full_owner_integrity_check_once_for_multiple_pages(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    store = WorkspaceStore(catalog_path_for_user_data_root(tmp_path))
    workspace = store.create_workspace(
        "owner", name="Attested healing", operation_id="create-attested-healing"
    )["workspace"]
    pages = [
        notion_store.create_page("owner", title=f"page-{index}")["page"]
        for index in range(2)
    ]
    for index, page in enumerate(pages):
        proof = notion_store.capture_legacy_page_source_proof(
            "owner", "owner", page["id"]
        )
        current = store.get_workspace("owner", workspace["id"])
        store.bind_page_source(
            "owner",
            workspace["id"],
            proof,
            source_revalidator=notion_store.revalidate_legacy_page_source_proof,
            base_version=current["version"],
            operation_id=f"bind-attested-{index}",
        )
    source = tmp_path / "owner" / "notion" / "notion.db"
    backup_dir = tmp_path / "watermark-backup"
    backup_dir.mkdir()
    plan = build_change_tracking_plan(
        user_data_root=tmp_path,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    apply_change_tracking_plan(
        plan,
        confirm_checksum=plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backup_dir / "before.db",
    )
    for index, page in enumerate(pages):
        notion_store.update_page(
            "owner", page["id"], {"title": f"changed-{index}"}
        )
    run = store.start_healing_run(
        "owner",
        workspace["id"],
        worker="worker.attested",
        lease_token=TOKEN,
        lease_seconds=120,
        operation_id="start-attested-healing",
        healing_mode="locator_apply",
    )["run"]

    inspect_calls: list[bool] = []
    real_inspect = notion_legacy_changes._inspect

    def counted_inspect(con, *, verify_integrity):
        inspect_calls.append(bool(verify_integrity))
        return real_inspect(con, verify_integrity=verify_integrity)

    monkeypatch.setattr(notion_legacy_changes, "_inspect", counted_inspect)
    result = healing.heal_one_locator_batch(
        store,
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        run_id=run["id"],
        worker="worker.attested",
        lease_token=TOKEN,
        fencing_generation=run["lease"]["fencing_generation"],
        expected_version=run["version"],
        user_data_root=tmp_path,
        expected_catalog_db_path=store.db_path,
        source_db_for_owner=lambda owner: source if owner == "owner" else None,
        source_quiesced_confirmed=True,
        limit=2,
    )

    assert result["run"]["status"] == "completed"
    assert result["healing_report"]["visited"] == 2
    assert result["healing_report"]["stale"] == 2
    assert inspect_calls.count(True) == 1
    assert inspect_calls.count(False) >= 4


def test_core_turns_fence_operation_hash_drift_into_failed_source_pass(
    tmp_path, monkeypatch
):
    store, workspace, page, _bound, source, run = _fixture(tmp_path, monkeypatch)

    def changed_fence(*_args, **_kwargs):
        raise WorkspaceOperationConflict("same-owner-fence")

    monkeypatch.setattr(store, "fence_healing_source_generation", changed_fence)
    result = healing.heal_one_locator_batch(
        store,
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        run_id=run["id"],
        worker="worker.apply",
        lease_token=TOKEN,
        fencing_generation=run["lease"]["fencing_generation"],
        expected_version=run["version"],
        user_data_root=tmp_path,
        expected_catalog_db_path=store.db_path,
        source_db_for_owner=lambda owner: source if owner == "owner" else None,
        source_quiesced_confirmed=True,
        limit=1,
    )
    assert result["run"]["status"] == "failed"
    assert result["run"]["failure_code"] == "source_generation_changed"
    assert result["run"]["counters"] == {
        key: 0 for key in result["run"]["counters"]
    }
    assert result["healing_report"] == result["run"]["counters"]


def test_batch_does_not_advance_on_late_typed_public_collision(tmp_path, monkeypatch):
    store, workspace, page, _bound, source, run = _fixture(tmp_path, monkeypatch)

    def collide(*_args, **_kwargs):
        raise WorkspacePageProjectionAmbiguous("public token digest is already projected")

    monkeypatch.setattr(store, "apply_page_source_healing", collide)
    with pytest.raises(healing.WorkspaceProjectionError, match="revalidation"):
        healing.heal_one_locator_batch(
            store,
            actor_subject_id="owner",
            workspace_id=workspace["id"],
            run_id=run["id"],
            worker="worker.apply",
            lease_token=TOKEN,
            fencing_generation=run["lease"]["fencing_generation"],
            expected_version=run["version"],
            user_data_root=tmp_path,
            expected_catalog_db_path=store.db_path,
            source_db_for_owner=lambda owner: source if owner == "owner" else None,
            source_quiesced_confirmed=True,
            limit=1,
        )
    current = store.get_healing_run("owner", workspace["id"], run["id"])
    assert current["status"] == "running"
    assert current["cursor"] is None
    assert current["counters"] == {key: 0 for key in current["counters"]}


def test_quarantined_permission_loss_does_not_wedge_later_bindings(
    tmp_path, monkeypatch
):
    store, workspace, page, bound, source, run = _fixture(tmp_path, monkeypatch)
    quarantined = store.quarantine_page_source_if_stale(
        "owner",
        workspace["id"],
        "owner",
        page["id"],
        source_revalidator=lambda _proof: False,
        expected_binding_version=bound["binding"]["version"],
        base_version=bound["workspace"]["version"],
        operation_id="quarantine-first-binding",
    )
    second = notion_store.create_page("owner", title="later binding")["page"]
    second_proof = notion_store.capture_legacy_page_source_proof(
        "owner", "owner", second["id"]
    )
    second_bound = store.bind_page_source(
        "owner",
        workspace["id"],
        second_proof,
        source_revalidator=notion_store.revalidate_legacy_page_source_proof,
        base_version=quarantined["workspace"]["version"],
        operation_id="bind-later-healing-page",
    )
    assert second_bound["binding"]["version"] >= 1

    authorized_states = []
    original_authorize = store.assert_active_page_source_binding_authorized

    def guarded_authorize(actor, binding):
        authorized_states.append(binding.binding_state)
        if binding.binding_state == "quarantined":
            raise AssertionError("quarantined binding must not be reauthorized")
        return original_authorize(actor, binding)

    monkeypatch.setattr(
        store, "assert_active_page_source_binding_authorized", guarded_authorize
    )
    result = healing.heal_one_locator_batch(
        store,
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        run_id=run["id"],
        worker="worker.apply",
        lease_token=TOKEN,
        fencing_generation=run["lease"]["fencing_generation"],
        expected_version=run["version"],
        user_data_root=tmp_path,
        expected_catalog_db_path=store.db_path,
        source_db_for_owner=lambda owner: source,
        source_quiesced_confirmed=True,
    )
    assert result["run"]["status"] == "completed"
    assert result["healing_report"]["superseded"] == 1
    assert authorized_states == ["active"]

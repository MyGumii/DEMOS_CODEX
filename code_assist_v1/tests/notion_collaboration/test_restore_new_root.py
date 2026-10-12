from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1.notion_collaboration.coordinated_backup_prototype import (
    SyntheticBackupBoundaryError,
    create_coordinated_generation,
    initialize_synthetic_collaborative_realm,
    restore_coordinated_generation,
    verify_restored_root,
)
from code_assist_v1.notion_collaboration.coordinated_workspace_backup import (
    RPO_MINUTES,
    RTO_MINUTES,
    create_local_workspace_generation,
    restore_local_workspace_generation,
    verify_local_workspace_restored_root,
)
from code_assist_v1.tests.notion_collaboration.o1a_p1_backup_fixture import (
    build_o1a_backup_environment,
)


@pytest.mark.integration
@pytest.mark.restore
@pytest.mark.scenario("OPS-002")
@pytest.mark.scenario("OPS-003")
def test_o0a_p2_new_root_restore_discards_credentials_and_rebuilds_projections(
    tmp_path: Path,
) -> None:
    realm = initialize_synthetic_collaborative_realm(tmp_path / "realm")
    assert realm.source_session_count() == 2
    generation = create_coordinated_generation(realm.root, "generation-restore")
    generation_root = generation.generation_root
    assert not any(path.name.endswith(".key") for path in generation_root.rglob("*"))
    identity_snapshot = generation_root / "authorities" / "identity.sqlite3"
    assert b"synthetic-source-secret-never-backed-up" not in identity_snapshot.read_bytes()

    first = restore_coordinated_generation(
        realm.root, "generation-restore", tmp_path / "restored-a"
    )
    second = restore_coordinated_generation(
        realm.root, "generation-restore", tmp_path / "restored-b"
    )
    assert first.session_count == second.session_count == 0
    assert first.account_count == first.forced_reauthentication_count == 2
    assert second.account_count == second.forced_reauthentication_count == 2
    assert first.signing_secret_fingerprint != second.signing_secret_fingerprint
    assert first.source_manifest_sha256 == second.source_manifest_sha256 == generation.manifest_sha256

    receipt = verify_restored_root(first.target_root)
    assert receipt["new_root"] is True
    assert receipt["outbound_integrations_enabled"] is False
    assert receipt["mutation_traffic_enabled"] is False
    assert receipt["integrity_mismatch_count"] == 0
    assert receipt["projection_manifest_sha256"] == first.projection_manifest_sha256
    marker = json.loads(
        (first.target_root / ".o0a-p2-synthetic-restore.json").read_text(encoding="utf-8")
    )
    assert marker["new_root"] is True
    assert marker["outbound_integrations_enabled"] is False
    assert marker["mutation_traffic_enabled"] is False

    identity = sqlite3.connect(first.target_root / "identity.sqlite3")
    try:
        assert identity.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2
        assert identity.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert identity.execute("SELECT COUNT(*) FROM secret_material").fetchone()[0] == 0
        assert identity.execute(
            "SELECT COUNT(*) FROM auth_state WHERE force_reauthentication=1 AND reauthentication_epoch=3"
        ).fetchone()[0] == 2
        assert all(
            str(row[0]).startswith("sha256:")
            for row in identity.execute("SELECT password_sha256 FROM accounts").fetchall()
        )
    finally:
        identity.close()
    assert sorted(path.name for path in (first.target_root / "projections").glob("*.json")) == [
        "collaboration-events.json",
        "notion-pages.json",
        "projection-manifest.json",
        "second-brain-items.json",
    ]


@pytest.mark.unit
def test_o0a_p2_restore_refuses_existing_or_non_temporary_target(tmp_path: Path) -> None:
    realm = initialize_synthetic_collaborative_realm(tmp_path / "realm")
    create_coordinated_generation(realm.root, "generation-boundary")
    existing = tmp_path / "existing-target"
    existing.mkdir()
    with pytest.raises(SyntheticBackupBoundaryError):
        restore_coordinated_generation(realm.root, "generation-boundary", existing)
    with pytest.raises(SyntheticBackupBoundaryError):
        restore_coordinated_generation(
            realm.root, "generation-boundary", Path.cwd() / "outside-temp-target"
        )


@pytest.mark.integration
@pytest.mark.restore
def test_o0a_p2_same_verified_generation_restores_repeatably_without_secret_reuse(
    tmp_path: Path,
) -> None:
    realm = initialize_synthetic_collaborative_realm(tmp_path / "realm")
    create_coordinated_generation(realm.root, "generation-repeat")
    fingerprints = {
        restore_coordinated_generation(
            realm.root,
            "generation-repeat",
            tmp_path / f"restore-{index}",
        ).signing_secret_fingerprint
        for index in range(3)
    }
    assert len(fingerprints) == 3


@pytest.mark.integration
@pytest.mark.restore
@pytest.mark.scenario("OPS-002")
@pytest.mark.scenario("OPS-003")
@pytest.mark.scenario("OPS-005")
def test_o1a_p1_new_root_restore_rebuilds_both_authorities_and_runs_full_smoke(
    tmp_path: Path,
    isolated_knowledge_layout,
    request,
) -> None:
    environment = build_o1a_backup_environment(
        tmp_path, isolated_knowledge_layout, request=request
    )
    generation = create_local_workspace_generation(
        environment.source_root,
        "o1a-new-root",
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
        sbe_configuration=environment.sbe_configuration,
        smoke_probe=environment.probe,
    )
    restored = restore_local_workspace_generation(
        environment.source_root,
        generation.generation_id,
        "restored-o1a",
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
        target_parent=isolated_knowledge_layout.root.parent / "o1a-restore-targets",
    )
    receipt = verify_local_workspace_restored_root(restored.target_root)

    assert restored.target_root != environment.source_root
    assert restored.target_root != environment.notion_root
    assert restored.smoke_passed is True
    assert restored.integrity_mismatch_count == 0
    assert restored.rpo_minutes <= RPO_MINUTES
    assert restored.rto_seconds <= RTO_MINUTES * 60
    assert receipt["notion"]["canonical_match"] is True
    assert receipt["notion"]["quick_check"] == "ok"
    assert receipt["notion"]["foreign_key_violation_count"] == 0
    assert receipt["notion"]["projection_workspace_count"] == 1
    assert receipt["second_brain"]["delegated_service"].endswith(
        ".RestoreDrillService"
    )
    assert receipt["second_brain"]["integrity_mismatch_count"] == 0
    assert receipt["smoke"]["failed_check_count"] == 0
    assert receipt["smoke"]["permission"] == {
        "owner_read": True,
        "member_read": True,
        "denied_subject_fenced": True,
    }
    assert receipt["smoke"]["comments"]["count"] >= 1
    assert receipt["smoke"]["files"]["size_bytes"] > 0
    assert receipt["smoke"]["history"]["revision_count"] >= 1
    assert receipt["smoke"]["search"] == {
        "matched_document": True,
        "converged": True,
    }
    assert receipt["smoke"]["second_brain"]["restored_item_count"] == len(
        environment.probe.second_brain_item_ids
    )
    assert receipt["identity_sessions_restored"] is False
    assert receipt["fresh_identity_session_required"] is True
    assert receipt["claims"]["target_restore_lane_closed"] is False

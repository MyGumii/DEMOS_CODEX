from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_assist_v1.notion_collaboration.realm_promotion import (
    OPERATOR_ACTION_RECEIPT_CONTRACT,
    OPERATOR_APPROVAL_RECEIPT_CONTRACT,
    PROMOTION_MANIFEST_CONTRACT,
    PROMOTION_STAGES,
    SBE_HANDOFF_RECEIPT_CONTRACT,
    canonical_sha256,
    sha256_file,
    verify_realm_promotion_manifest,
)
from code_assist_v1.notion_collaboration import realm_promotion as promotion_module
from code_assist_v1.notion_collaboration.release_candidate import (
    build_release_candidate_manifest,
    write_release_candidate_manifest_create_only,
)
from code_assist_v1.tools.internal_collaborative_validation import (
    DEFAULT_CATALOG,
    DEFAULT_LADDER,
    DEFAULT_MATRIX,
    DEFAULT_RECEIPT_SCHEMA,
    JsonSchemaValidator,
    load_json,
    validate_execution_receipts,
)
from code_assist_v1.tools import (
    internal_collaborative_validation as central,
    run_internal_collaborative_validation as runner,
)


def _hash(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _write(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _binding(path: Path, receipt_id: str, root: Path) -> dict:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "receipt_id": receipt_id,
    }


def _scope(stage: str, release: str, generation: str, realm_fingerprint: str) -> dict:
    is_canary = stage == "production_canary"
    return {
        "root_identity_sha256": _hash(f"root:{stage}"),
        "ports": [10012] if stage == "default_off_install" else [],
        "subject_identity_sha256": (
            [_hash("collaborator:a"), _hash("collaborator:b"), _hash("outsider:c")]
            if is_canary
            else []
        ),
        "subject_roles": (
            ["collaborator", "collaborator", "outsider_negative_control"]
            if is_canary
            else []
        ),
        "workspace_identity_sha256": [_hash("workspace:canary")] if is_canary else [],
        "argv_sha256": _hash(f"argv:{stage}"),
        "forbidden_scope_sha256": _hash(f"forbidden:{stage}"),
        "precondition_sha256": _hash(f"precondition:{stage}"),
        "rollback_sha256": _hash(f"rollback:{stage}"),
        "release_candidate_id": release,
        "realm_config_fingerprint": realm_fingerprint,
        "deployment_generation_id": generation,
    }


def _evidence(stage: str) -> dict:
    if stage == "offline_rehearsal":
        return {
            "approved_offline_copy_only": True,
            "production_mutation_performed": False,
            "migration_passed": True,
            "new_root_restore_passed": True,
            "integrity_audit_passed": True,
            "redacted_application_smoke_passed": True,
            "compatibility_audit_passed": True,
            "compatible_code_rollback_passed": True,
            "disaster_recovery_rehearsal_passed": True,
            "raw_private_content_recorded": False,
        }
    if stage == "default_off_install":
        return {
            "install_port": 10012,
            "frozen_artifact_installed": True,
            "default_off": True,
            "user_enablement_count": 0,
            "gateway_package_installed": True,
            "gateway_service_restarted": True,
            "gateway_health_passed": True,
            "configuration_derivation_passed": True,
            "before_after_hashes_present": True,
            "rollback_ready": True,
            "artifact_rebuilt": False,
        }
    return {
        "collaborator_count": 2,
        "outsider_negative_control_count": 1,
        "outsider_denied": True,
        "telemetry_soak_passed": True,
        "abort_thresholds_configured": True,
        "abort_threshold_breach_count": 0,
        "aggregate_kill_switch_exercised": True,
        "capability_kill_switch_exercised": True,
        "compatible_rollback_passed": True,
        "recovery_passed": True,
        "acknowledged_operation_loss_count": 0,
        "post_revoke_delivery_count": 0,
        "convergence_mismatch_count": 0,
        "artifact_rebuilt": False,
        "broader_rollout_enabled": False,
    }


def _build_bundle(tmp_path: Path) -> dict:
    root = tmp_path
    evidence_root = root / "promotion"
    generation = "deployment-generation-0001"
    source = _hash("source")
    validation = _hash("validation")
    schema = _hash("schema")
    release_source_path = _write(root / "release-inputs" / "product.py", {"source": "product"})
    release_lock_path = _write(root / "release-inputs" / "runtime.lock", {"dependency": "1"})
    release_migration_path = _write(root / "release-inputs" / "migration.py", {"version": 1})
    release_protocol_path = _write(root / "release-inputs" / "protocol.json", {"version": 1})
    release_frontend_path = _write(root / "release-inputs" / "frontend.js", {"build": 1})
    release_validation_path = _write(root / "release-inputs" / "test_product.py", {"test": 1})
    release_schema_path = _write(root / "release-inputs" / "matrix.json", {"rows": []})
    release_patterns = {
        "product_source": (release_source_path.relative_to(root).as_posix(), release_frontend_path.relative_to(root).as_posix()),
        "dependency_locks": (release_lock_path.relative_to(root).as_posix(),),
        "database_schema_and_migrations": (release_migration_path.relative_to(root).as_posix(),),
        "collaboration_protocol": (release_protocol_path.relative_to(root).as_posix(),),
        "frontend_build": (release_frontend_path.relative_to(root).as_posix(),),
        "validation_source": (release_validation_path.relative_to(root).as_posix(),),
    }
    release_manifest = build_release_candidate_manifest(
        repository_root=root,
        deployment_generation_id=generation,
        recorded_at="2026-09-01T00:00:00Z",
        patterns=release_patterns,
        schema_catalog_path=release_schema_path,
    )
    release_path = evidence_root / "release-candidate.json"
    write_release_candidate_manifest_create_only(release_path, release_manifest)
    release = release_manifest["release_candidate_id"]
    artifact = release_manifest["artifact_manifest_sha256"]
    derivation = release_manifest["derivation_proof_sha256"]
    realm_fingerprints = {
        "offline_rehearsal": _hash("realm:offline"),
        "default_off_install": _hash("realm:production-default-off"),
        "production_canary": _hash("realm:production-canary"),
    }
    handoff = {
        "schema_version": 1,
        "contract": SBE_HANDOFF_RECEIPT_CONTRACT,
        "receipt_id": "sbe-handoff-1",
        "external_gate_id": "XG-SBE-TARGET-OPERATIONS-HANDOFF",
        "status": "accepted",
        "release_candidate_id": release,
        "deployment_generation_id": generation,
        "accepted_at": "2026-09-01T00:00:00Z",
        "expires_at": "2026-09-02T00:00:00Z",
        "bindings": {
            "agents_md_sha256": _hash("agents"),
            "ordered_18_document_manifest_sha256": _hash("18-docs"),
            "current_goal_observation_sha256": _hash("goal"),
            "active_sbe_packet_sha256": _hash("sbe-packet"),
            "sbe_contract_bundle_sha256": _hash("sbe-contracts"),
            "current_schema_lineage_sha256": _hash("lineage"),
            "target_authority_sha256": _hash("target-authority"),
        },
    }
    handoff_path = _write(evidence_root / "sbe-handoff.json", handoff)
    handoff_binding = _binding(handoff_path, handoff["receipt_id"], root)
    policy = {
        "offline_rehearsal": ("R0A-P1", "AP-PROD-REHEARSAL", "approved_offline_production_copy_operator"),
        "default_off_install": ("R0B-P1", "AP-PROD-DEPLOY", "production_canary"),
        "production_canary": ("R1-P1", "AP-PROD-CANARY", "production_canary"),
    }
    times = {
        "offline_rehearsal": ("2026-09-01T01:00:00Z", "2026-09-01T01:10:00Z"),
        "default_off_install": ("2026-09-01T02:00:00Z", "2026-09-01T02:10:00Z"),
        "production_canary": ("2026-09-01T03:00:00Z", "2026-09-01T04:00:00Z"),
    }
    actions: dict[str, dict] = {}
    approvals: dict[str, dict] = {}
    action_paths: dict[str, Path] = {}
    approval_paths: dict[str, Path] = {}
    for stage in PROMOTION_STAGES:
        packet_id, approval_id, realm_id = policy[stage]
        scope = _scope(stage, release, generation, realm_fingerprints[stage])
        approval = {
            "schema_version": 1,
            "contract": OPERATOR_APPROVAL_RECEIPT_CONTRACT,
            "receipt_id": f"approval-{stage}",
            "approval_id": approval_id,
            "packet_id": packet_id,
            "authorized_stage": stage,
            "status": "approved",
            "scope_sha256": canonical_sha256(scope),
            "approved_at": "2026-09-01T00:00:00Z",
            "expires_at": "2026-09-02T00:00:00Z",
            "approval_is_execution_authority_not_evidence": True,
        }
        approval_path = _write(evidence_root / f"approval-{stage}.json", approval)
        action = {
            "schema_version": 1,
            "contract": OPERATOR_ACTION_RECEIPT_CONTRACT,
            "receipt_id": f"action-{stage}",
            "action_id": f"operator-{stage}-1",
            "stage": stage,
            "packet_id": packet_id,
            "approval_id": approval_id,
            "release_candidate_id": release,
            "realm_id": realm_id,
            "realm_config_fingerprint": realm_fingerprints[stage],
            "deployment_generation_id": generation,
            "source_manifest_sha256": source,
            "validation_manifest_sha256": validation,
            "schema_catalog_sha256": schema,
            "artifact_manifest_sha256": artifact,
            "derivation_proof_sha256": derivation,
            "approval_receipt": _binding(approval_path, approval["receipt_id"], root),
            "sbe_handoff_receipt": deepcopy(handoff_binding),
            "scope": scope,
            "execution": {
                "started_at": times[stage][0],
                "ended_at": times[stage][1],
                "operator_action": True,
                "automated_test_runner": False,
                "pytest_invoked": False,
                "action_exit_code": 0,
                "cleanup_complete": True,
                "unrelated_processes_targeted": False,
            },
            "result": {
                "status": "passed",
                "passed": 1,
                "failed": 0,
                "errors": [],
                "skipped": 0,
                "xfailed": 0,
            },
            "evidence": _evidence(stage),
        }
        action_path = _write(evidence_root / f"action-{stage}.json", action)
        actions[stage] = action
        approvals[stage] = approval
        action_paths[stage] = action_path
        approval_paths[stage] = approval_path
    manifest = {
        "schema_version": 1,
        "contract": PROMOTION_MANIFEST_CONTRACT,
        "manifest_id": "promotion-manifest-1",
        "release_candidate_id": release,
        "deployment_generation_id": generation,
        "source_manifest_sha256": source,
        "validation_manifest_sha256": validation,
        "schema_catalog_sha256": schema,
        "artifact_manifest_sha256": artifact,
        "derivation_proof_sha256": derivation,
        "release_candidate_manifest": _binding(
            release_path, release_manifest["manifest_id"], root
        ),
        "receipt_bindings": {
            stage: _binding(action_paths[stage], actions[stage]["receipt_id"], root)
            for stage in PROMOTION_STAGES
        },
        "claims": {
            "same_release_candidate": True,
            "same_deployment_generation": True,
            "no_rebuild_between_stages": True,
            "operator_actions_only": True,
            "verifier_accessed_live_root": False,
            "verifier_invoked_pytest": False,
            "full_rollout_enabled": False,
            "product_complete": False,
        },
    }
    manifest_path = _write(evidence_root / "promotion-manifest.json", manifest)
    return {
        "root": root,
        "evidence_root": evidence_root,
        "release": release,
        "generation": generation,
        "release_manifest": release_manifest,
        "release_path": release_path,
        "release_source_path": release_source_path,
        "release_patterns": release_patterns,
        "release_schema_path": release_schema_path,
        "realm_fingerprints": realm_fingerprints,
        "handoff": handoff,
        "handoff_path": handoff_path,
        "actions": actions,
        "action_paths": action_paths,
        "approvals": approvals,
        "approval_paths": approval_paths,
        "manifest": manifest,
        "manifest_path": manifest_path,
    }


def _rewrite_action(bundle: dict, stage: str) -> None:
    _write(bundle["action_paths"][stage], bundle["actions"][stage])
    bundle["manifest"]["receipt_bindings"][stage] = _binding(
        bundle["action_paths"][stage], bundle["actions"][stage]["receipt_id"], bundle["root"]
    )
    _write(bundle["manifest_path"], bundle["manifest"])


def _rewrite_approval(bundle: dict, stage: str) -> None:
    _write(bundle["approval_paths"][stage], bundle["approvals"][stage])
    bundle["actions"][stage]["approval_receipt"] = _binding(
        bundle["approval_paths"][stage], bundle["approvals"][stage]["receipt_id"], bundle["root"]
    )
    _rewrite_action(bundle, stage)


def _verify(bundle: dict, **overrides):
    return verify_realm_promotion_manifest(
        bundle["manifest_path"],
        expected_release_candidate_id=overrides.get("release", bundle["release"]),
        expected_realm_id=overrides.get("realm", "production_canary"),
        expected_realm_config_fingerprint=overrides.get(
            "fingerprint", bundle["realm_fingerprints"]["production_canary"]
        ),
        expected_source_manifest_sha256=overrides.get(
            "source", bundle["manifest"]["source_manifest_sha256"]
        ),
        expected_validation_manifest_sha256=overrides.get(
            "validation", bundle["manifest"]["validation_manifest_sha256"]
        ),
        expected_schema_catalog_sha256=overrides.get(
            "schema", bundle["manifest"]["schema_catalog_sha256"]
        ),
        expected_sbe_handoff_bindings=overrides.get("handoff_bindings", {}),
        release_patterns=bundle["release_patterns"],
        release_schema_catalog_path=bundle["release_schema_path"],
        repository_root=bundle["root"],
        allowed_root=bundle["evidence_root"],
    )


@pytest.mark.unit
def test_realm_promotion_accepts_three_separate_exact_operator_receipts(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    report, exit_code = _verify(bundle)
    assert exit_code == 0, report
    assert report["gate_eligible"] is True
    assert report["verified_stages"] == list(PROMOTION_STAGES)
    assert report["operator_actions_executed_by_verifier"] is False
    assert report["pytest_invoked_by_verifier"] is False
    assert report["full_rollout_enabled_by_verifier"] is False
    assert report["product_complete"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda bundle: bundle["actions"]["production_canary"]["evidence"].__setitem__("collaborator_count", 1), "COUNT_MISMATCH"),
        (lambda bundle: bundle["actions"]["production_canary"]["evidence"].__setitem__("artifact_rebuilt", True), "ATTESTATION_MISMATCH"),
        (lambda bundle: bundle["actions"]["offline_rehearsal"]["execution"].__setitem__("pytest_invoked", True), "ATTESTATION_MISMATCH"),
    ],
)
def test_realm_promotion_rejects_false_operator_attestations(tmp_path: Path, mutation, expected_code: str) -> None:
    bundle = _build_bundle(tmp_path)
    mutation(bundle)
    stage = "offline_rehearsal" if bundle["actions"]["offline_rehearsal"]["execution"]["pytest_invoked"] else "production_canary"
    _rewrite_action(bundle, stage)
    report, exit_code = _verify(bundle)
    assert exit_code == 1
    assert report["errors"][0]["code"] == expected_code


@pytest.mark.unit
def test_realm_promotion_rejects_approval_that_does_not_bind_exact_scope(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    bundle["actions"]["default_off_install"]["scope"]["precondition_sha256"] = _hash("changed")
    _rewrite_action(bundle, "default_off_install")
    report, exit_code = _verify(bundle)
    assert exit_code == 1
    assert report["errors"][0]["code"] == "APPROVAL_SCOPE_HASH_MISMATCH"


@pytest.mark.unit
def test_realm_promotion_rejects_expired_action_approval(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    bundle["approvals"]["production_canary"]["expires_at"] = "2026-09-01T03:30:00Z"
    _rewrite_approval(bundle, "production_canary")
    report, exit_code = _verify(bundle)
    assert exit_code == 1
    assert report["errors"][0]["code"] == "APPROVAL_TIME_SCOPE_INVALID"


@pytest.mark.unit
def test_realm_promotion_rejects_stage_time_inversion(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    bundle["actions"]["default_off_install"]["execution"]["started_at"] = "2026-09-01T01:05:00Z"
    bundle["actions"]["default_off_install"]["execution"]["ended_at"] = "2026-09-01T01:09:00Z"
    _rewrite_action(bundle, "default_off_install")
    report, exit_code = _verify(bundle)
    assert exit_code == 1
    assert report["errors"][0]["code"] == "PROMOTION_STAGE_ORDER_INVALID"


@pytest.mark.unit
def test_realm_promotion_rejects_receipt_outside_evidence_root(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    outside = _write(tmp_path / "outside.json", bundle["actions"]["offline_rehearsal"])
    bundle["manifest"]["receipt_bindings"]["offline_rehearsal"] = _binding(
        outside, bundle["actions"]["offline_rehearsal"]["receipt_id"], bundle["root"]
    )
    _write(bundle["manifest_path"], bundle["manifest"])
    report, exit_code = _verify(bundle)
    assert exit_code == 1
    assert report["errors"][0]["code"] == "BOUND_PATH_INVALID"


@pytest.mark.unit
def test_realm_promotion_rejects_reused_operator_identity(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    bundle["actions"]["production_canary"]["action_id"] = bundle["actions"]["default_off_install"]["action_id"]
    _rewrite_action(bundle, "production_canary")
    report, exit_code = _verify(bundle)
    assert exit_code == 1
    assert report["errors"][0]["code"] == "PROMOTION_IDENTITY_REUSED"


@pytest.mark.unit
def test_realm_promotion_requires_exact_cli_release_and_canary_realm(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    report, exit_code = _verify(bundle, release=_hash("other-release"))
    assert exit_code == 1
    assert report["errors"][0]["code"] == "PROMOTION_RELEASE_BINDING_MISMATCH"

    report, exit_code = _verify(bundle, realm="synthetic_isolated")
    assert exit_code == 1
    assert report["errors"][0]["code"] == "PROMOTION_REALM_INVALID"


@pytest.mark.unit
def test_realm_promotion_requires_current_authority_and_handoff_bindings(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    report, exit_code = _verify(bundle, source=_hash("other-source"))
    assert exit_code == 1
    assert report["errors"][0]["code"] == "PROMOTION_AUTHORITY_BINDING_DRIFT"

    report, exit_code = _verify(
        bundle,
        handoff_bindings={"agents_md_sha256": _hash("other-agents")},
    )
    assert exit_code == 1
    assert report["errors"][0]["code"] == "SBE_HANDOFF_BINDING_DRIFT"


@pytest.mark.unit
def test_realm_promotion_freshly_reverifies_release_derivation(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    _write(bundle["release_source_path"], {"source": "changed-after-freeze"})
    report, exit_code = _verify(bundle)
    assert exit_code == 1
    assert report["errors"][0]["code"] == "RELEASE_CANDIDATE_INVALID"


@pytest.mark.unit
def test_gate_mode_without_lane_never_falls_back_to_audit(monkeypatch, capsys) -> None:
    monkeypatch.setattr(runner, "verify_bundle", lambda **_kwargs: ({"status": "pass", "errors": []}, 0))
    exit_code = runner.main(["--mode", "gate", "--compact"])
    report = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert report["errors"][0]["code"] == "GATE_LANE_REQUIRED"


@pytest.mark.unit
def test_exact_l9_gate_command_requires_promotion_manifest(monkeypatch, capsys) -> None:
    monkeypatch.setattr(runner, "verify_bundle", lambda **_kwargs: ({"status": "pass", "errors": []}, 0))
    exit_code = runner.main(
        ["--mode", "gate", "--rung", "L9-RELEASE-AND-CANARY", "--compact"]
    )
    report = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert report["errors"][0]["code"] == "PROMOTION_MANIFEST_REQUIRED"


@pytest.mark.unit
def test_l9_runner_rejects_private_or_escaping_evidence_paths(tmp_path: Path) -> None:
    rung = {"id": "L9-RELEASE-AND-CANARY"}
    lane = {"id": "L9-REALM-PROMOTION"}
    base = {
        "release_candidate_id": _hash("release"),
        "realm_id": "production_canary",
        "realm_config_fingerprint": _hash("realm"),
        "receipt_output": None,
    }
    report, exit_code = runner._execute_realm_promotion(
        rung,
        lane,
        SimpleNamespace(promotion_manifest=tmp_path / "manifest.json", **base),
        ["--mode", "gate"],
    )
    assert exit_code == 2
    assert report["errors"][0]["code"] == "PROMOTION_MANIFEST_PATH_INVALID"

    report, exit_code = runner._execute_realm_promotion(
        rung,
        lane,
        SimpleNamespace(
            promotion_manifest=Path("promotion/manifest.json"),
            **{**base, "receipt_output": Path("outside-receipts.json")},
        ),
        ["--mode", "gate"],
    )
    assert exit_code == 2
    assert report["errors"][0]["code"] == "PROMOTION_RECEIPT_OUTPUT_INVALID"


@pytest.mark.unit
def test_audit_mode_without_lane_remains_read_only_audit(monkeypatch, capsys) -> None:
    expected = {"status": "pass", "errors": [], "error_count": 0}
    monkeypatch.setattr(runner, "verify_bundle", lambda **_kwargs: (expected, 0))
    assert runner.main(["--mode", "audit", "--compact"]) == 0
    assert json.loads(capsys.readouterr().out) == expected


@pytest.mark.unit
def test_l9_aggregate_receipt_matches_generic_receipt_schema(monkeypatch) -> None:
    promotion = {
        "gate_eligible": True,
        "error_count": 0,
        "errors": [],
        "manifest_id": "promotion-manifest-1",
        "artifact_hashes": {"promotion.json": _hash("manifest")},
        "verified_action_receipt_count": 3,
        "deployment_generation_id": _hash("generation"),
    }
    monkeypatch.setattr(
        runner,
        "verify_realm_promotion_manifest",
        lambda *_args, **_kwargs: (promotion, 0),
    )
    rung = {"id": "L9-RELEASE-AND-CANARY"}
    lane = {"id": "L9-REALM-PROMOTION"}
    args = SimpleNamespace(
        promotion_manifest=Path("promotion.json"),
        release_candidate_id=_hash("release"),
        realm_id="production_canary",
        realm_config_fingerprint=_hash("realm"),
    )
    report, exit_code = runner._execute_realm_promotion(rung, lane, args, ["--mode", "gate"])
    assert exit_code == 0, report
    assert JsonSchemaValidator(load_json(DEFAULT_RECEIPT_SCHEMA)).validate(report["receipt"]) == []
    assert report["receipt"]["operator_action_receipt_count"] == 3
    assert report["receipt"]["isolation_attestation"]["operator_actions_executed_by_verifier"] is False
    assert report["receipt"]["realm_promotion_evidence"] == {
        "path": "promotion.json",
        "sha256": _hash("manifest"),
        "manifest_id": "promotion-manifest-1",
    }

    receipt = report["receipt"]
    post_hashes = {
        "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_packets.v2.json": receipt["source_manifest_sha256"],
        "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_validation_ladder.v1.json": receipt["validation_manifest_sha256"],
        "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_completion_matrix.v1.json": receipt["schema_catalog_sha256"],
    }
    checkpoints = [
        {
            "packet_id": packet_id,
            "post_edit_sha256": post_hashes,
            "current_schema_lineage_sha256": receipt["current_schema_lineage_sha256"],
            "agents_md_sha256": receipt["agents_md_sha256"],
        }
        for packet_id in receipt["packet_ids"]
    ]
    forged = deepcopy(receipt)
    forged.pop("realm_promotion_evidence")
    valid, errors = validate_execution_receipts(
        [forged],
        load_json(DEFAULT_CATALOG),
        load_json(DEFAULT_LADDER),
        load_json(DEFAULT_MATRIX),
        checkpoints,
    )
    assert valid == []
    assert "PROMOTION_EVIDENCE_REQUIRED" in {
        item["code"] for item in errors
    }

    monkeypatch.setattr(
        central,
        "_validate_realm_promotion_evidence",
        lambda *_args, **_kwargs: [],
    )
    valid, errors = validate_execution_receipts(
        [receipt],
        load_json(DEFAULT_CATALOG),
        load_json(DEFAULT_LADDER),
        load_json(DEFAULT_MATRIX),
        checkpoints,
    )
    assert errors == []
    assert [item["receipt_id"] for item in valid] == [receipt["receipt_id"]]

    drifted = deepcopy(checkpoints)
    drifted[-1]["agents_md_sha256"] = _hash("different-agents")
    valid, errors = validate_execution_receipts(
        [receipt],
        load_json(DEFAULT_CATALOG),
        load_json(DEFAULT_LADDER),
        load_json(DEFAULT_MATRIX),
        drifted,
    )
    assert valid == []
    assert "RECEIPT_CHECKPOINT_BINDING_AMBIGUOUS" in {item["code"] for item in errors}


@pytest.mark.unit
def test_central_validator_freshly_reconstructs_bound_l9_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_root = tmp_path / "promotion"
    manifest = {
        "contract": PROMOTION_MANIFEST_CONTRACT,
        "manifest_id": "promotion-manifest-1",
    }
    manifest_path = _write(evidence_root / "promotion-manifest.json", manifest)
    manifest_relative = manifest_path.relative_to(tmp_path).as_posix()
    manifest_sha256 = sha256_file(manifest_path)
    release = _hash("release")
    realm = _hash("realm")
    source = _hash("source")
    validation = _hash("validation")
    schema = _hash("schema")
    generation = "deployment-generation-0001"
    artifact_hashes = {manifest_relative: manifest_sha256}
    calls: list[tuple[Path, dict]] = []

    def verify(path: Path, **kwargs):
        calls.append((path, kwargs))
        return {
            "status": "pass",
            "gate_eligible": True,
            "error_count": 0,
            "errors": [],
            "manifest_id": manifest["manifest_id"],
            "release_candidate_id": release,
            "deployment_generation_id": generation,
            "realm_id": "production_canary",
            "realm_config_fingerprint": realm,
            "verified_stages": list(PROMOTION_STAGES),
            "verified_action_receipt_count": 3,
            "artifact_hashes": artifact_hashes,
            "operator_actions_executed_by_verifier": False,
            "live_roots_accessed_by_verifier": False,
            "pytest_invoked_by_verifier": False,
            "full_rollout_enabled_by_verifier": False,
            "product_complete": False,
        }, 0

    monkeypatch.setattr(central, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(central, "PROMOTION_EVIDENCE_ROOT", evidence_root)
    monkeypatch.setattr(
        promotion_module,
        "verify_realm_promotion_manifest",
        verify,
    )
    receipt = {
        "packet_ids": ["R0A-P1", "R0B-P1", "R1-P1"],
        "lane_id": "L9-REALM-PROMOTION",
        "realm_id": "production_canary",
        "environment_class": "operator_action_receipt_verification",
        "matrix_row_ids": [
            "ICN-OPS-002",
            "ROLL-001",
            "OBS-001",
            "PILOT-001",
        ],
        "scenario_ids": [],
        "operator_action_receipt_count": 3,
        "deployment_generation_id": generation,
        "release_candidate_id": release,
        "realm_config_fingerprint": realm,
        "source_manifest_sha256": source,
        "validation_manifest_sha256": validation,
        "schema_catalog_sha256": schema,
        "agents_md_sha256": _hash("agents"),
        "goal_observation_sha256": _hash("goal"),
        "current_schema_lineage_sha256": _hash("lineage"),
        "full_rollout_enabled_by_verifier": False,
        "product_complete": False,
        "isolation_attestation": {
            "operator_actions_executed_by_verifier": False,
            "pytest_invoked_by_verifier": False,
        },
        "realm_promotion_evidence": {
            "path": manifest_relative,
            "sha256": manifest_sha256,
            "manifest_id": manifest["manifest_id"],
        },
        "artifact_hashes": artifact_hashes,
    }

    assert central._validate_realm_promotion_evidence(receipt, "$") == []
    assert calls == [
        (
            manifest_path,
            {
                "expected_release_candidate_id": release,
                "expected_realm_id": "production_canary",
                "expected_realm_config_fingerprint": realm,
                "expected_source_manifest_sha256": source,
                "expected_validation_manifest_sha256": validation,
                "expected_schema_catalog_sha256": schema,
                "expected_sbe_handoff_bindings": {
                    "agents_md_sha256": _hash("agents"),
                    "current_goal_observation_sha256": _hash("goal"),
                    "current_schema_lineage_sha256": _hash("lineage"),
                },
            },
        )
    ]

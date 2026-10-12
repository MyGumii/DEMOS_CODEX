from __future__ import annotations

import copy
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from code_assist_v1.tools import (
    internal_collaborative_validation as central,
    run_internal_collaborative_validation as runner,
    verify_internal_collaborative_target_restore as target_verifier,
)
from code_assist_v1.tools.internal_collaborative_validation import (
    DEFAULT_CATALOG,
    DEFAULT_CHECKPOINT,
    DEFAULT_LADDER,
    DEFAULT_MATRIX,
    DEFAULT_RECEIPT_SCHEMA,
    JsonSchemaValidator,
    TARGET_POLICY_PATH as CENTRAL_TARGET_POLICY_PATH,
    load_json,
    sha256_file,
    validate_execution_receipts,
)


SHA_A = "sha256:" + "a" * 64


def _args(*, realm_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        release_candidate_id=SHA_A,
        realm_id=realm_id,
        realm_config_fingerprint=SHA_A,
        target_policy=None,
        target_approval=None,
        target_handoff=None,
        target_evidence_index=None,
        target_evidence_root=None,
        target_receipt=None,
        expected_target_policy_sha256=SHA_A,
        q0_report=None,
        q0_attestation=None,
        receipt_output=None,
    )


def _lane(lane_id: str) -> tuple[dict, dict]:
    ladder = load_json(DEFAULT_LADDER)
    for rung in ladder["rungs"]:
        for lane in rung["lanes"]:
            if lane["id"] == lane_id:
                return rung, lane
    raise AssertionError(lane_id)


@pytest.mark.unit
def test_central_receipt_validator_uses_current_runner_target_policy() -> None:
    assert runner.TARGET_POLICY_PATH.name == (
        "internal_collaborative_o1b_p1_target_operations.v7.json"
    )
    assert CENTRAL_TARGET_POLICY_PATH == runner.TARGET_POLICY_PATH


@pytest.mark.unit
def test_central_receipt_validator_reconstructs_v4_bound_target_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_path = central.TARGET_POLICY_PATH
    policy_relative = policy_path.relative_to(central.REPOSITORY_ROOT).as_posix()
    policy_sha256 = sha256_file(policy_path)
    evidence_path = next(central.DEFAULT_RECEIPTS_ROOT.glob("*.json"))
    evidence_relative = evidence_path.relative_to(central.REPOSITORY_ROOT).as_posix()
    evidence_sha256 = sha256_file(evidence_path)
    evidence_root_relative = central.TARGET_EVIDENCE_ROOT.relative_to(
        central.REPOSITORY_ROOT
    ).as_posix()
    contract = central.TARGET_GATE_CONTRACTS["O1B-P1"]

    original_load_json = central.load_json
    bound_policy = copy.deepcopy(original_load_json(policy_path))
    bound_policy["trust_anchor_policy"]["status"] = "bound"
    bound_policy["trust_anchor_policy"][
        "authorized_release_candidate_sha256"
    ] = SHA_A

    def load_bound_policy(path: Path) -> dict:
        if Path(path).resolve() == policy_path.resolve():
            return bound_policy
        return original_load_json(path)

    monkeypatch.setattr(central, "load_json", load_bound_policy)
    monkeypatch.setattr(
        target_verifier,
        "verify",
        lambda *_args, **_kwargs: {
            "status": "pass",
            "error_count": 0,
            "errors": [],
            "gate_passed": True,
            "execution_required_mode": True,
            "product_complete": False,
        },
    )

    receipt = {
        "packet_ids": ["O1B-P1"],
        "lane_id": contract["lane_id"],
        "realm_id": contract["realm_id"],
        "environment_class": contract["environment_class"],
        "matrix_row_ids": contract["matrix_row_ids"],
        "scenario_ids": contract["scenario_ids"],
        "release_candidate_id": SHA_A,
        "realm_config_fingerprint": policy_sha256,
        "target_operations_executed_by_verifier": False,
        "product_complete": False,
        "isolation_attestation": {
            "operator_actions_executed_by_verifier": False,
            "pytest_invoked_by_verifier": False,
        },
        "o1b_target_evidence": {
            "policy": {"path": policy_relative, "sha256": policy_sha256},
            "approval": {"path": evidence_relative, "sha256": evidence_sha256},
            "handoff": {"path": evidence_relative, "sha256": evidence_sha256},
            "evidence_index": {
                "path": evidence_relative,
                "sha256": evidence_sha256,
            },
            "evidence_root": evidence_root_relative,
            "target_receipt": {
                "path": evidence_relative,
                "sha256": evidence_sha256,
            },
        },
        "artifact_hashes": {
            policy_relative: policy_sha256,
            evidence_relative: evidence_sha256,
        },
    }

    assert central._validate_target_gate_evidence(receipt, "$") == []


@pytest.mark.unit
def test_generic_gate_runner_cannot_label_local_pytest_as_target_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("target gate must not invoke pytest"),
    )
    rung, lane = _lane("L7-COLLAB-WORKSPACE-RESTORE")
    report, exit_code = runner._execute_lane(
        "gate", rung, lane, _args(realm_id="target_restore")
    )
    assert exit_code == 2
    assert report["errors"][0]["code"] == "TARGET_GATE_DEDICATED_VERIFIER_REQUIRED"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lane_id", "realm_id", "expected_code"),
    [
        (
            "L7-COLLAB-WORKSPACE-RESTORE",
            "synthetic_isolated",
            "TARGET_RESTORE_REALM_REQUIRED",
        ),
        (
            "L8-COLLAB-LOAD-FAULT",
            "synthetic_isolated",
            "TARGET_WINDOWS_LOAD_REALM_REQUIRED",
        ),
    ],
)
def test_target_gate_rejects_wrong_realm_before_reading_evidence(
    lane_id: str,
    realm_id: str,
    expected_code: str,
) -> None:
    rung, lane = _lane(lane_id)
    report, exit_code = runner._execute_target_gate(
        rung, lane, _args(realm_id=realm_id), []
    )
    assert exit_code == 2
    assert report["errors"][0]["code"] == expected_code


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lane_id", "realm_id"),
    [
        ("L7-COLLAB-WORKSPACE-RESTORE", "target_restore"),
        ("L8-COLLAB-LOAD-FAULT", "target_windows_load"),
    ],
)
def test_target_gate_requires_complete_external_evidence_set(
    lane_id: str,
    realm_id: str,
) -> None:
    rung, lane = _lane(lane_id)
    report, exit_code = runner._execute_target_gate(
        rung, lane, _args(realm_id=realm_id), []
    )
    assert exit_code == 2
    assert report["errors"][0]["code"] == "TARGET_EVIDENCE_SET_REQUIRED"


@pytest.mark.unit
def test_current_unbound_o1b_policy_cannot_start_external_verification() -> None:
    rung, lane = _lane("L7-COLLAB-WORKSPACE-RESTORE")
    policy_relative = runner.TARGET_POLICY_PATH.relative_to(runner.REPOSITORY_ROOT)
    evidence_relative = runner.DEFAULT_RECEIPTS_ROOT.relative_to(runner.REPOSITORY_ROOT)
    existing_receipt = next(runner.DEFAULT_RECEIPTS_ROOT.glob("*.json"))
    existing_relative = existing_receipt.relative_to(runner.REPOSITORY_ROOT)
    policy_sha256 = sha256_file(runner.TARGET_POLICY_PATH)
    args = _args(realm_id="target_restore")
    args.release_candidate_id = SHA_A
    args.realm_config_fingerprint = policy_sha256
    args.expected_target_policy_sha256 = policy_sha256
    args.target_policy = policy_relative
    args.target_approval = existing_relative
    args.target_handoff = existing_relative
    args.target_evidence_index = existing_relative
    args.target_evidence_root = evidence_relative
    args.target_receipt = existing_relative

    report, exit_code = runner._execute_target_gate(rung, lane, args, [])
    assert exit_code == 2
    assert report["errors"][0]["code"] == "TARGET_POLICY_NOT_BOUND"


@pytest.mark.unit
def test_dedicated_o1b_runner_builds_single_packet_receipt_without_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rung, lane = _lane("L7-COLLAB-WORKSPACE-RESTORE")
    files = {
        "policy": runner.TARGET_POLICY_PATH,
        "approval": DEFAULT_CATALOG,
        "handoff": DEFAULT_LADDER,
        "evidence_index": DEFAULT_MATRIX,
        "receipt": DEFAULT_CHECKPOINT,
    }
    inputs = {
        "evidence_root": runner.TARGET_EVIDENCE_ROOT,
        "evidence_root_relative": str(
            runner.TARGET_EVIDENCE_ROOT.relative_to(runner.REPOSITORY_ROOT)
        ).replace("\\", "/"),
        "release_candidate_id": SHA_A,
        "realm_config_fingerprint": sha256_file(runner.TARGET_POLICY_PATH),
        "expected_policy_sha256": sha256_file(runner.TARGET_POLICY_PATH),
    }
    for name, path in files.items():
        inputs[name] = path
        inputs[f"{name}_relative"] = str(
            path.relative_to(runner.REPOSITORY_ROOT)
        ).replace("\\", "/")

    monkeypatch.setattr(runner, "_resolve_target_gate_inputs", lambda *_: inputs)
    monkeypatch.setattr(
        runner,
        "verify_target_restore",
        lambda *_args, **_kwargs: {
            "status": "pass",
            "error_count": 0,
            "errors": [],
            "gate_passed": True,
            "execution_required_mode": True,
            "product_complete": False,
        },
    )
    report, exit_code = runner._execute_target_gate(
        rung, lane, _args(realm_id="target_restore"), ["--mode", "gate"]
    )
    assert exit_code == 0, report
    receipt = report["receipt"]
    assert receipt["packet_ids"] == ["O1B-P1"]
    assert receipt["realm_id"] == "target_restore"
    assert receipt["environment_class"] == (
        "external_target_restore_receipt_verification"
    )
    assert receipt["isolation_attestation"]["pytest_invoked_by_verifier"] is False
    assert receipt["target_operations_executed_by_verifier"] is False
    schema = load_json(DEFAULT_RECEIPT_SCHEMA)
    assert JsonSchemaValidator(schema).validate(receipt) == []


@pytest.mark.unit
def test_dedicated_q0_runner_requires_validated_report_and_o1b_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rung, lane = _lane("L8-COLLAB-LOAD-FAULT")
    files = {
        "policy": runner.TARGET_POLICY_PATH,
        "approval": DEFAULT_CATALOG,
        "handoff": DEFAULT_LADDER,
        "evidence_index": DEFAULT_MATRIX,
        "receipt": DEFAULT_CHECKPOINT,
        "q0_report": DEFAULT_CATALOG,
        "q0_attestation": DEFAULT_LADDER,
    }
    inputs = {
        "evidence_root": runner.TARGET_EVIDENCE_ROOT,
        "evidence_root_relative": str(
            runner.TARGET_EVIDENCE_ROOT.relative_to(runner.REPOSITORY_ROOT)
        ).replace("\\", "/"),
        "release_candidate_id": SHA_A,
        "realm_config_fingerprint": sha256_file(runner.TARGET_POLICY_PATH),
        "expected_policy_sha256": sha256_file(runner.TARGET_POLICY_PATH),
    }
    for name, path in files.items():
        inputs[name] = path
        inputs[f"{name}_relative"] = str(
            path.relative_to(runner.REPOSITORY_ROOT)
        ).replace("\\", "/")

    monkeypatch.setattr(runner, "_resolve_target_gate_inputs", lambda *_: inputs)
    monkeypatch.setattr(
        runner,
        "verify_target_restore",
        lambda *_args, **_kwargs: {
            "status": "pass",
            "error_count": 0,
            "errors": [],
            "gate_passed": True,
            "execution_required_mode": True,
            "product_complete": False,
        },
    )
    monkeypatch.setattr(runner, "load_json", lambda _path: {"o1b": "evidence"})
    monkeypatch.setattr(
        runner,
        "load_q0_json_document",
        lambda path: (
            {"q0": "attestation"}
            if path == inputs["q0_attestation"]
            else {"q0": "report"}
        ),
    )
    monkeypatch.setattr(
        runner,
        "validate_q0_report",
        lambda *_args, **_kwargs: {
            "status": "pass",
            "target_gate_eligible": True,
        },
    )
    monkeypatch.setattr(
        runner,
        "validate_q0_target_attestation",
        lambda *_args, **_kwargs: {
            "status": "pass",
            "target_gate_eligible": True,
        },
    )
    report, exit_code = runner._execute_target_gate(
        rung,
        lane,
        _args(realm_id="target_windows_load"),
        ["--mode", "gate"],
    )
    assert exit_code == 0, report
    receipt = report["receipt"]
    assert receipt["packet_ids"] == ["Q0-P1"]
    assert receipt["realm_id"] == "target_windows_load"
    assert receipt["q0_report"]["sha256"] == sha256_file(DEFAULT_CATALOG)
    assert receipt["q0_attestation"]["sha256"] == sha256_file(DEFAULT_LADDER)
    assert report["q0_validation"]["target_gate_eligible"] is True


@pytest.mark.unit
def test_central_receipt_validator_rejects_handcrafted_o1b_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="ok\n", stderr=""
        ),
    )
    seed_rung = {"id": "L0-CONTRACT-BOOT", "packets": ["U0A-P2"]}
    seed_lane = {
        "id": "L0-TEST-TARGET-RECEIPT-SEED",
        "state": "existing_baseline",
        "runner": "process",
        "working_directory": ".",
        "argv": ["unused"],
        "evidence_types": ["contract"],
    }
    seed_args = SimpleNamespace(
        release_candidate_id="",
        realm_id="synthetic_isolated",
        realm_config_fingerprint="",
    )
    report, exit_code = runner._execute_lane(
        "audit", seed_rung, seed_lane, seed_args
    )
    assert exit_code == 0
    forged = copy.deepcopy(report["receipt"])
    forged.update(
        {
            "rung_id": "L7-BACKUP-RESTORE",
            "lane_id": "L7-COLLAB-WORKSPACE-RESTORE",
            "packet_ids": ["O1B-P1"],
            "matrix_row_ids": ["ICN-OPS-001", "ICN-OPS-002"],
            "scenario_ids": [
                "OPS-001",
                "OPS-002",
                "OPS-003",
                "OPS-004",
                "OPS-005",
                "OPS-006",
            ],
            "mode": "gate",
            "release_candidate_id": SHA_A,
            "realm_id": "target_restore",
            "realm_config_fingerprint": SHA_A,
            "environment_class": (
                "external_target_restore_receipt_verification"
            ),
            "isolation_attestation": {
                "shell_used": False,
                "live_roots_accessed": False,
                "operator_actions_executed_by_verifier": False,
                "pytest_invoked_by_verifier": False,
            },
            "target_operations_executed_by_verifier": False,
            "product_complete": False,
        }
    )
    valid, errors = validate_execution_receipts(
        [forged],
        load_json(DEFAULT_CATALOG),
        load_json(DEFAULT_LADDER),
        load_json(DEFAULT_MATRIX),
    )
    assert valid == []
    assert "TARGET_EVIDENCE_SET_REQUIRED" in {item["code"] for item in errors}


@pytest.mark.unit
def test_central_receipt_validator_rejects_handcrafted_q0_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="ok\n", stderr=""
        ),
    )
    report, exit_code = runner._execute_lane(
        "audit",
        {"id": "L0-CONTRACT-BOOT", "packets": ["U0A-P2"]},
        {
            "id": "L0-TEST-Q0-RECEIPT-SEED",
            "state": "existing_baseline",
            "runner": "process",
            "working_directory": ".",
            "argv": ["unused"],
            "evidence_types": ["contract"],
        },
        SimpleNamespace(
            release_candidate_id="",
            realm_id="synthetic_isolated",
            realm_config_fingerprint="",
        ),
    )
    assert exit_code == 0
    forged = copy.deepcopy(report["receipt"])
    forged.update(
        {
            "rung_id": "L8-LOAD-FAULT-SECURITY",
            "lane_id": "L8-COLLAB-LOAD-FAULT",
            "packet_ids": ["Q0-P1"],
            "matrix_row_ids": ["ICN-PERF-001", "SEC-001", "OBS-001"],
            "scenario_ids": ["PERF-001", "OPS-005", "AUTH-004"],
            "mode": "gate",
            "release_candidate_id": SHA_A,
            "realm_id": "target_windows_load",
            "realm_config_fingerprint": SHA_A,
            "environment_class": (
                "external_target_windows_load_receipt_verification"
            ),
            "isolation_attestation": {
                "shell_used": False,
                "live_roots_accessed": False,
                "operator_actions_executed_by_verifier": False,
                "pytest_invoked_by_verifier": False,
            },
            "target_operations_executed_by_verifier": False,
            "product_complete": False,
        }
    )
    valid, errors = validate_execution_receipts(
        [forged],
        load_json(DEFAULT_CATALOG),
        load_json(DEFAULT_LADDER),
        load_json(DEFAULT_MATRIX),
    )
    assert valid == []
    assert "TARGET_EVIDENCE_SET_REQUIRED" in {item["code"] for item in errors}

from __future__ import annotations

import base64
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from code_assist_v1.notion_collaboration.q0_validation import (
    FROZEN_PRODUCT_DECISION_SHA256,
    Q0EvidenceError,
    Q0_REPORT_CONTRACT,
    REQUIRED_FAULT_POINT_IDS,
    REQUIRED_OBSERVABILITY_SIGNAL_IDS,
    REQUIRED_SECURITY_CONTROL_IDS,
    load_frozen_scale_contract,
    target_realm_attestation,
)
from code_assist_v1.tools.q0_target_attestation import (
    Q0_TARGET_ATTESTATION_CONTRACT,
    load_q0_json_document,
    q0_target_attestation_payload,
    validate_q0_target_attestation,
)


def _sha(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _bundle() -> dict[str, object]:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    release = _sha("release")
    policy_sha = _sha("policy")
    report_sha = _sha("q0-report-file")
    receipt_sha = _sha("o1b-receipt-file")
    target_machine = _sha("target-machine")
    key_id = "receipt-review-key-01"
    policy = {
        "trust_anchor_policy": {
            "status": "bound",
            "authorized_release_candidate_sha256": release,
            "receipt_reviewer": {
                "key_id": key_id,
                "public_key_base64": base64.b64encode(public).decode("ascii"),
                "public_key_sha256": "sha256:" + hashlib.sha256(public).hexdigest(),
            },
        }
    }
    approval = {
        "resource_scope": {
            "target_host_id": "target-windows-01",
            "target_machine_identity_sha256": target_machine,
        }
    }
    target_receipt = {
        "reviewer_attestation": {
            "reviewer_id": "external-reviewer-01",
            "attestation_system_id": "target-attestation-system-01",
            "trust_policy_id": "target-trust-policy-01",
            "signature_key_id": key_id,
            "signed_at": "2026-09-07T00:00:00Z",
        }
    }
    scale = load_frozen_scale_contract()
    report = {
        "contract": Q0_REPORT_CONTRACT,
        "product_decision_sha256": FROZEN_PRODUCT_DECISION_SHA256,
        "scale_contract": asdict(scale),
        "realm": target_realm_attestation(
            {
                "CODE_ASSIST_Q0_TARGET_LOAD": "1",
                "CODE_ASSIST_Q0_RUN_ID": "q0-target-run-01",
                "CODE_ASSIST_Q0_SYNTHETIC_ROOT_SHA256": _sha("synthetic-root"),
            }
        ),
        "observed_denominators": {
            "registered_users": scale.registered_users,
            "simultaneous_connections": scale.simultaneous_connections,
            "simultaneous_editors_per_page": scale.simultaneous_editors_per_page,
            "sustained_operations_per_second": scale.sustained_operations_per_second,
            "burst_operations_per_second": scale.burst_operations_per_second,
            "attachment_concurrency": scale.attachment_concurrency,
        },
        "performance": {
            "ack_latency_ms": {
                "count": 4,
                "minimum": 1.0,
                "p50": 2.0,
                "p95": 3.0,
                "p99": 4.0,
                "maximum": 4.0,
            },
            "sqlite_busy_count": 0,
            "queue_lag_ms": 0.0,
            "projection_lag_ms": 0.0,
            "memory_peak_bytes": 1,
            "compaction_age_seconds": 0.0,
        },
        "reliability": {
            "acknowledged_operation_loss_count": 0,
            "convergence_mismatch_count": 0,
            "post_revoke_protected_delivery_count": 0,
            "unauthorized_upload_read_count": 0,
        },
        "fault_point_ids": sorted(REQUIRED_FAULT_POINT_IDS),
        "security_control_ids": sorted(REQUIRED_SECURITY_CONTROL_IDS),
        "observability_signal_ids": sorted(REQUIRED_OBSERVABILITY_SIGNAL_IDS),
    }
    attestation = {
        "schema_version": 1,
        "contract": Q0_TARGET_ATTESTATION_CONTRACT,
        "packet_id": "Q0-P1",
        "lane_id": "L8-COLLAB-LOAD-FAULT",
        "realm_id": "target_windows_load",
        "release_candidate_id": release,
        "realm_config_fingerprint": policy_sha,
        "target_host_id": "target-windows-01",
        "target_machine_identity_sha256": target_machine,
        "q0_report_sha256": report_sha,
        "q0_run_id": "q0-target-run-01",
        "synthetic_root_identity_sha256": _sha("synthetic-root"),
        "o1b_target_receipt_sha256": receipt_sha,
        "issued_at": "2026-09-07T00:01:00Z",
        "reviewer_id": "external-reviewer-01",
        "attestation_system_id": "target-attestation-system-01",
        "trust_policy_id": "target-trust-policy-01",
        "signature_key_id": key_id,
        "signature_algorithm": "ed25519",
        "private_key_material_in_attestation": False,
        "claims": {
            "Q0_P1_target_load_executed": True,
            "runner_owned_synthetic_root": True,
            "contains_real_user_content": False,
            "production_root_accessed": False,
            "product_complete": False,
            "active_shared_goal_complete": False,
        },
        "signature_base64": "",
    }
    attestation["signature_base64"] = base64.b64encode(
        key.sign(_canonical_bytes(q0_target_attestation_payload(attestation)))
    ).decode("ascii")
    return {
        "attestation": attestation,
        "report": report,
        "policy": policy,
        "approval": approval,
        "target_receipt": target_receipt,
        "report_sha": report_sha,
        "receipt_sha": receipt_sha,
        "release": release,
        "policy_sha": policy_sha,
    }


def _verify(bundle: dict[str, object]) -> dict[str, object]:
    return validate_q0_target_attestation(
        bundle["attestation"],  # type: ignore[arg-type]
        report=bundle["report"],  # type: ignore[arg-type]
        q0_report_sha256=str(bundle["report_sha"]),
        policy=bundle["policy"],  # type: ignore[arg-type]
        approval=bundle["approval"],  # type: ignore[arg-type]
        target_receipt=bundle["target_receipt"],  # type: ignore[arg-type]
        target_receipt_sha256=str(bundle["receipt_sha"]),
        expected_release_candidate_id=str(bundle["release"]),
        expected_realm_config_fingerprint=str(bundle["policy_sha"]),
    )


@pytest.mark.unit
def test_q0_target_attestation_binds_verified_release_machine_and_report() -> None:
    result = _verify(_bundle())
    assert result == {
        "contract": Q0_TARGET_ATTESTATION_CONTRACT,
        "status": "pass",
        "target_gate_eligible": True,
        "release_candidate_bound": True,
        "target_machine_bound": True,
        "q0_report_bound": True,
        "external_reviewer_signature_verified": True,
        "product_complete": False,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("release_candidate_id", _sha("other-release")),
        ("target_machine_identity_sha256", _sha("other-machine")),
        ("q0_report_sha256", _sha("other-report")),
        ("o1b_target_receipt_sha256", _sha("other-receipt")),
    ],
)
def test_q0_target_attestation_rejects_cross_binding_substitution(
    field: str,
    replacement: str,
) -> None:
    bundle = _bundle()
    attestation = copy.deepcopy(bundle["attestation"])
    assert isinstance(attestation, dict)
    attestation[field] = replacement
    bundle["attestation"] = attestation
    with pytest.raises(Q0EvidenceError, match="does not match"):
        _verify(bundle)


@pytest.mark.unit
def test_q0_target_attestation_rejects_unsigned_post_signature_change() -> None:
    bundle = _bundle()
    attestation = copy.deepcopy(bundle["attestation"])
    assert isinstance(attestation, dict)
    attestation["issued_at"] = "2026-09-07T00:02:00Z"
    bundle["attestation"] = attestation
    with pytest.raises(Q0EvidenceError, match="signature invalid"):
        _verify(bundle)


@pytest.mark.unit
def test_q0_json_loader_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"contract":"a","contract":"b"}', encoding="utf-8")
    with pytest.raises(Q0EvidenceError, match="duplicate JSON key"):
        load_q0_json_document(path)

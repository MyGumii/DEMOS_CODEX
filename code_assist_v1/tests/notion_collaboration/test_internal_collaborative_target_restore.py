from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Callable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from code_assist_v1.tools.assemble_internal_collaborative_target_authority import (
    AUTHORITY_MANIFEST_CONTRACT,
    SIGNATURE_RESPONSE_CONTRACT,
    AuthorityAssemblyError,
    assemble_authority,
)
import code_assist_v1.tools.compile_internal_collaborative_target_execution_plan as target_execution_plan_compiler
import code_assist_v1.tools.verify_internal_collaborative_target_restore as target_restore_verifier
from code_assist_v1.tools.compile_internal_collaborative_target_execution_plan import (
    ExecutionPlanError,
    PLAN_CONTRACT,
    REQUIRED_TRANSITION_ORDER,
    compile_execution_plan,
)
from code_assist_v1.tools.compile_internal_collaborative_target_signing_requests import (
    REVIEWER_METADATA_CONTRACT,
    SIGNING_REQUEST_CONTRACT,
    SigningRequestError,
    compile_signing_requests,
)
from code_assist_v1.tools.verify_internal_collaborative_target_restore import (
    ACTION_CONFIRMATION,
    APPROVAL_CONTRACT,
    APPROVAL_ID,
    CANARY_IDS,
    EVIDENCE_ARTIFACT_CONTRACT,
    EVIDENCE_INDEX_CONTRACT,
    HANDOFF_CONTRACT,
    HANDOFF_ID,
    HANDOFF_PACKET_IDS,
    POLICY_CONTRACT,
    POLICY_PATH,
    RECEIPT_CONTRACT,
    REQUIRED_EVIDENCE_ARTIFACT_IDS,
    RETENTION,
    SCENARIO_IDS,
    TARGET_SCOPE,
    main,
    verify,
    verify_pre_execution,
)


_EXECUTION_ID = "00000000-0000-4000-8000-000000000001"
_GENERATION_ID = "00000000-0000-4000-8000-000000000002"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _sha_tag(tag: str) -> str:
    return "sha256:" + hashlib.sha256(tag.encode("utf-8")).hexdigest()


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _sign(key: Ed25519PrivateKey, value: object) -> str:
    return base64.b64encode(key.sign(_canonical_bytes(value))).decode("ascii")


def _anchor(key: Ed25519PrivateKey, key_id: str) -> dict[str, object]:
    public = key.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    return {
        "key_id": key_id,
        "public_key_base64": base64.b64encode(public).decode("ascii"),
        "public_key_sha256": "sha256:" + hashlib.sha256(public).hexdigest(),
    }


def _keys() -> dict[str, Ed25519PrivateKey]:
    return {role: Ed25519PrivateKey.generate() for role in ("approval", "handoff", "evidence_index", "receipt_reviewer")}


def _release_candidate() -> dict[str, object]:
    return {
        "build_sha256": _sha_tag("build"),
        "configuration_sha256": _sha_tag("configuration"),
        "catalog_sha256": _sha_tag("catalog"),
        "schema_sha256": _sha_tag("schema"),
        "sqlite_version": "3.50.4",
        "sqlite_source_id_sha256": _sha_tag("sqlite-source"),
        "successor_flags_default_off": True,
        "one_durable_worker": True,
        "one_projector": True,
    }


def _bound_policy(tmp_path: Path, keys: dict[str, Ed25519PrivateKey]) -> tuple[Path, dict[str, object]]:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    trust = policy["trust_anchor_policy"]
    assert isinstance(trust, dict)
    trust.update({
        "status": "bound",
        "authorized_release_candidate_sha256": _sha256_value(_release_candidate()),
        "approval": _anchor(keys["approval"], "approval-signing-key-01"),
        "handoff": _anchor(keys["handoff"], "handoff-signing-key-01"),
        "evidence_index": _anchor(keys["evidence_index"], "evidence-index-key-01"),
        "receipt_reviewer": _anchor(keys["receipt_reviewer"], "receipt-review-key-01"),
    })
    path = tmp_path / "bound-policy.json"
    _write(path, policy)
    return path, policy


def _resource_scope() -> dict[str, object]:
    return {
        "source_host_id": "source-host-01",
        "backup_host_id": "backup-host-01",
        "target_host_id": "target-windows-01",
        "source_machine_identity_sha256": _sha_tag("source-machine"),
        "backup_machine_identity_sha256": _sha_tag("backup-machine"),
        "target_machine_identity_sha256": _sha_tag("target-machine"),
        "source_root_locator_hmac": _sha_tag("source-root"),
        "backup_root_locator_hmac": _sha_tag("backup-root"),
        "restore_root_locator_hmac": _sha_tag("restore-root"),
        "port_set_sha256": _sha_tag("port-set"),
        "subject_set_sha256": _sha_tag("subject-set"),
        "workspace_set_sha256": _sha_tag("workspace-set"),
        "sanitized_argv_sha256": _sha_tag("argv"),
        "precondition_sha256": _sha_tag("precondition"),
        "cleanup_rollback_sha256": _sha_tag("cleanup-rollback"),
        "locator_key_id": "locator-hmac-key-01",
        "locator_domain_separator": "o1b-target-locator-v1",
        "authorized_reviewer_id": "company-reviewer-01",
        "review_attestation_system_id": "company-attestation-01",
        "review_trust_policy_id": "target-restore-trust-v1",
        "review_signature_key_id": "receipt-review-key-01",
        "target_evidence_store_id": "target-evidence-store-01",
        "approval_system_id": "approval-system-01",
        "handoff_system_id": "handoff-system-01",
        "nonce_ledger_id": "durable-nonce-ledger-01",
        "sbe_audit_system_id": "repository-sbe-contract-audit-01",
        "encryption_control_id": "bitlocker-volume-01",
        "encryption_key_owner_id": "backup-key-owner-01",
        "encryption_key_id": "backup-key-2026-09",
    }


def _scope_sha256(scope: dict[str, object]) -> str:
    return _sha256_value({"resource_scope": scope, "target_scope": TARGET_SCOPE})


def _reseal_approval(value: dict[str, object], key: Ed25519PrivateKey) -> None:
    value.pop("receipt_sha256", None)
    value.pop("signature", None)
    value["signature"] = {"key_id": "approval-signing-key-01", "algorithm": "ed25519", "signature_base64": _sign(key, value)}
    value["receipt_sha256"] = _sha256_value(value)


def _approval(policy: dict[str, object], key: Ed25519PrivateKey) -> dict[str, object]:
    scope = _resource_scope()
    trust = policy["trust_anchor_policy"]
    assert isinstance(trust, dict)
    value: dict[str, object] = {
        "schema_version": 1,
        "contract": APPROVAL_CONTRACT,
        "approval_id": APPROVAL_ID,
        "status": "approved",
        "approval_record_id": "approval-record-01",
        "approval_source_evidence_sha256": _sha_tag("approval-source-record"),
        "approver_id": "approved-operator-01",
        "issued_at": "2026-09-07T00:00:00.000Z",
        "expires_at": "2026-09-07T02:00:00.000Z",
        "authorized_execution_id": _EXECUTION_ID,
        "single_use_nonce": "approval-nonce-01",
        "action_time_confirmation": ACTION_CONFIRMATION,
        "target_scope": TARGET_SCOPE,
        "resource_scope": scope,
        "scope_sha256": _scope_sha256(scope),
        "release_candidate_sha256": trust["authorized_release_candidate_sha256"],
        "production_restore_authorized": False,
    }
    _reseal_approval(value, key)
    return value


def _attestation(*, key: Ed25519PrivateKey, key_id: str, reviewer_id: str, attestation_system_id: str, trust_policy_id: str, signature_record_id: str, decision: str, evidence_document_sha256: str, signed_at: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "reviewer_id": reviewer_id,
        "attestation_system_id": attestation_system_id,
        "trust_policy_id": trust_policy_id,
        "signature_key_id": key_id,
        "signature_algorithm": "ed25519",
        "signature_record_id": signature_record_id,
        "decision": decision,
        "evidence_document_sha256": evidence_document_sha256,
        "signed_at": signed_at,
    }
    value = dict(payload)
    value["signature_base64"] = _sign(key, payload)
    value["private_key_material_in_receipt"] = False
    return value


def _reseal_handoff(value: dict[str, object], key: Ed25519PrivateKey) -> None:
    value.pop("receipt_sha256", None)
    value.pop("reviewer_attestation", None)
    value["reviewer_attestation"] = _attestation(
        key=key, key_id="handoff-signing-key-01", reviewer_id="handoff-reviewer-01",
        attestation_system_id="company-attestation-01", trust_policy_id="target-operations-trust-v1",
        signature_record_id="handoff-signature-01", decision="accepted",
        evidence_document_sha256=_sha256_value(value), signed_at=str(value["accepted_at"]),
    )
    value["receipt_sha256"] = _sha256_value(value)


def _handoff(policy: dict[str, object], approval: dict[str, object], key: Ed25519PrivateKey) -> dict[str, object]:
    baseline = policy["sbe_dynamic_baseline"]
    assert isinstance(baseline, dict)
    value: dict[str, object] = {
        "schema_version": 1, "contract": HANDOFF_CONTRACT, "handoff_id": HANDOFF_ID,
        "packet_ids": HANDOFF_PACKET_IDS, "status": "accepted", "owner_role": "coordinated_target_authority",
        "owner_id": "ops-owner-01", "issuer_id": "sbe-operations-owner-01", "acceptor_id": "notion-operations-owner-01",
        "issued_at": "2026-09-07T00:01:00.000Z", "accepted_at": "2026-09-07T00:02:00.000Z",
        "expires_at": "2026-09-07T02:00:00.000Z", "authorized_execution_id": _EXECUTION_ID,
        "scope_sha256": approval["scope_sha256"], "release_candidate_sha256": _sha256_value(_release_candidate()),
        "sbe_dynamic_baseline": {name: baseline[name] for name in ("status", "error_count", "input_sha256", "goal_count", "scenario_count", "report_path", "report_sha256")},
        "release_candidate": _release_candidate(),
        "authority": {
            "target_operation_scope": TARGET_SCOPE, "notion_and_sbe_authorities_separate": True,
            "cross_database_transaction_claimed": False, "sbe_target_gate_closed_by_notion_receipt": False,
            "notion_target_gate_closed_by_sbe_receipt": False, "production_restore_authorized": False,
        },
    }
    _reseal_handoff(value, key)
    return value


def _artifact_captured_at(artifact_id: str) -> str:
    return {
        "approval_source": "2026-09-07T00:00:00.000Z",
        "handoff_acceptance": "2026-09-07T00:02:00.000Z",
        "nonce_consumption": "2026-09-07T00:02:30.000Z",
    }.get(artifact_id, "2026-09-07T00:31:00.000Z")


def _nonce_observation(approval: dict[str, object]) -> dict[str, object]:
    scope = approval["resource_scope"]
    assert isinstance(scope, dict)
    return {
        "ledger_id": scope["nonce_ledger_id"],
        "approval_record_id": approval["approval_record_id"],
        "single_use_nonce": approval["single_use_nonce"],
        "authorized_execution_id": _EXECUTION_ID,
        "prior_consumption_count": 0,
        "final_consumption_count": 1,
        "atomic_compare_exchange": True,
        "commit_result": "committed",
        "transaction_record_id": "nonce-transaction-record-01",
        "committed_at": _artifact_captured_at("nonce_consumption"),
    }


def _artifact_source_system(
    artifact_id: str, approval: dict[str, object]
) -> str:
    scope = approval["resource_scope"]
    assert isinstance(scope, dict)
    if artifact_id == "approval_source":
        return str(scope["approval_system_id"])
    if artifact_id == "handoff_acceptance":
        return str(scope["handoff_system_id"])
    if artifact_id == "nonce_consumption":
        return str(scope["nonce_ledger_id"])
    if artifact_id == "source_machine_identity":
        return str(scope["source_host_id"])
    if artifact_id in {
        "backup_machine_identity",
        "encryption",
        "acl",
        "retention",
        "generation_manifest",
        "blob_set",
        "notion_snapshot",
        "notion_blob_manifest",
        "sbe_snapshot",
        "sbe_backup_manifest",
    }:
        return str(scope["backup_host_id"])
    if artifact_id == "sbe_contract_audit":
        return str(scope["sbe_audit_system_id"])
    return str(scope["target_host_id"])


def _artifact_observations(
    policy: dict[str, object],
    approval: dict[str, object],
    handoff_path: Path,
) -> dict[str, str]:
    baseline = policy["sbe_dynamic_baseline"]
    scope = approval["resource_scope"]
    assert isinstance(baseline, dict) and isinstance(scope, dict)
    observations = {
        name: _sha_tag("observation:" + name)
        for name in REQUIRED_EVIDENCE_ARTIFACT_IDS
    }
    observations.update(
        {
            "handoff_acceptance": _sha256_file(handoff_path),
            "nonce_consumption": _sha256_value(_nonce_observation(approval)),
            "source_machine_identity": str(scope["source_machine_identity_sha256"]),
            "backup_machine_identity": str(scope["backup_machine_identity_sha256"]),
            "target_machine_identity": str(scope["target_machine_identity_sha256"]),
            "sbe_contract_audit": str(baseline["report_sha256"]),
        }
    )
    return observations


def _write_evidence_artifacts(
    evidence_root: Path,
    *,
    policy: dict[str, object],
    approval: dict[str, object],
    handoff_path: Path,
) -> dict[str, dict[str, object]]:
    evidence_root.mkdir()
    observations = _artifact_observations(policy, approval, handoff_path)
    exports: dict[str, dict[str, object]] = {}
    for artifact_id in REQUIRED_EVIDENCE_ARTIFACT_IDS:
        export: dict[str, object] = {
            "schema_version": 1,
            "contract": EVIDENCE_ARTIFACT_CONTRACT,
            "artifact_id": artifact_id,
            "execution_id": _EXECUTION_ID,
            "generation_id": _GENERATION_ID,
            "evidence_record_id": "record-" + artifact_id.replace("_", "-"),
            "captured_at": _artifact_captured_at(artifact_id),
            "source_system_id": _artifact_source_system(artifact_id, approval),
            "observation_sha256": observations[artifact_id],
            "raw_evidence_retained": True,
            "redacted_export": True,
            "private_data_in_export": False,
        }
        if artifact_id == "nonce_consumption":
            export["nonce_observation"] = _nonce_observation(approval)
        _write(evidence_root / f"{artifact_id}.json", export)
        exports[artifact_id] = export
    return exports


def _reseal_index(value: dict[str, object], key: Ed25519PrivateKey) -> None:
    value.pop("receipt_sha256", None)
    value.pop("signature", None)
    value["signature"] = {"key_id": "evidence-index-key-01", "algorithm": "ed25519", "signature_base64": _sign(key, value)}
    value["receipt_sha256"] = _sha256_value(value)


def _evidence_index(approval: dict[str, object], evidence_root: Path, key: Ed25519PrivateKey) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1, "contract": EVIDENCE_INDEX_CONTRACT, "execution_id": _EXECUTION_ID,
        "generation_id": _GENERATION_ID, "issued_at": "2026-09-07T00:31:30.000Z",
        "target_evidence_store_id": approval["resource_scope"]["target_evidence_store_id"], "observed_resource_scope": approval["resource_scope"],  # type: ignore[index]
        "artifacts": [
            {
                "artifact_id": name,
                "evidence_record_id": "record-" + name.replace("_", "-"),
                "sha256": _sha256_file(evidence_root / f"{name}.json"),
                "captured_at": _artifact_captured_at(name),
                "sanitized_export_path": f"{name}.json",
            }
            for name in REQUIRED_EVIDENCE_ARTIFACT_IDS
        ],
    }
    _reseal_index(value, key)
    return value


def _artifact_map(index: dict[str, object]) -> dict[str, dict[str, object]]:
    artifacts = index["artifacts"]
    assert isinstance(artifacts, list)
    return {str(item["artifact_id"]): item for item in artifacts if isinstance(item, dict)}


def _reseal_receipt(value: dict[str, object], key: Ed25519PrivateKey) -> None:
    value.pop("receipt_sha256", None)
    value.pop("reviewer_attestation", None)
    scope = value["observed_resource_scope"]
    assert isinstance(scope, dict)
    value["reviewer_attestation"] = _attestation(
        key=key, key_id=str(scope["review_signature_key_id"]), reviewer_id=str(scope["authorized_reviewer_id"]),
        attestation_system_id=str(scope["review_attestation_system_id"]), trust_policy_id=str(scope["review_trust_policy_id"]),
        signature_record_id="review-record-01", decision="approved", evidence_document_sha256=_sha256_value(value),
        signed_at="2026-09-07T00:32:00.000Z",
    )
    value["receipt_sha256"] = _sha256_value(value)


def _receipt(
    policy_path: Path,
    policy: dict[str, object],
    approval: dict[str, object],
    approval_path: Path,
    handoff: dict[str, object],
    handoff_path: Path,
    index: dict[str, object],
    index_path: Path,
    evidence_exports: dict[str, dict[str, object]],
    key: Ed25519PrivateKey,
) -> dict[str, object]:
    baseline = policy["sbe_dynamic_baseline"]
    scope = approval["resource_scope"]
    assert isinstance(baseline, dict) and isinstance(scope, dict)
    artifacts = _artifact_map(index)
    observations = {
        artifact_id: str(export["observation_sha256"])
        for artifact_id, export in evidence_exports.items()
    }
    value: dict[str, object] = {
        "schema_version": 1, "contract": RECEIPT_CONTRACT, "packet_id": "O1B-P1", "scenario_ids": SCENARIO_IDS,
        "realm_id": "target_restore", "result": "passed", "exit_code": 0,
        "receipt_id": "O1B-P1-TARGET-" + _sha256_file(index_path)[7:23].upper(), "execution_id": _EXECUTION_ID,
        "policy_binding": {"contract": POLICY_CONTRACT, "sha256": _sha256_file(policy_path)},
        "approval_binding": {
            "id": APPROVAL_ID, "contract": APPROVAL_CONTRACT, "approval_record_id": approval["approval_record_id"],
            "sha256": _sha256_file(approval_path), "scope_sha256": approval["scope_sha256"], "single_use_nonce": approval["single_use_nonce"],
            "authorized_execution_id": _EXECUTION_ID, "consumed_before_execution": True, "consumed_at": "2026-09-07T00:02:30.000Z",
            "nonce_prior_consumption_count": 0, "nonce_final_consumption_count": 1, "nonce_atomic_compare_exchange": True,
            "nonce_consumption_evidence_sha256": artifacts["nonce_consumption"]["sha256"],
        },
        "target_operations_handoff": {
            "id": HANDOFF_ID, "contract": HANDOFF_CONTRACT, "sha256": _sha256_file(handoff_path), "owner_id": handoff["owner_id"],
            "scope_sha256": handoff["scope_sha256"], "release_candidate_sha256": handoff["release_candidate_sha256"],
            "authorized_execution_id": _EXECUTION_ID, "accepted_before_execution": True,
        },
        "evidence_index_binding": {
            "contract": EVIDENCE_INDEX_CONTRACT, "sha256": _sha256_file(index_path), "execution_id": _EXECUTION_ID,
            "generation_id": _GENERATION_ID, "target_evidence_store_id": index["target_evidence_store_id"],
        },
        "observed_resource_scope": scope,
        "sbe_dynamic_baseline": {name: baseline[name] for name in ("status", "error_count", "input_sha256", "goal_count", "scenario_count", "report_path", "report_sha256")},
        "external_backup": {
            "generation_id": _GENERATION_ID, "source_host_id": scope["source_host_id"], "destination_host_id": scope["backup_host_id"],
            "destination_locator_hmac": scope["backup_root_locator_hmac"], "destination_off_host": True,
            "same_volume_exception_used": False, "test_mode_used": False, "failpoint_used": False,
            "encryption_at_rest_verified": True, "encryption_evidence_sha256": artifacts["encryption"]["sha256"],
            "acl_reviewed": True, "acl_evidence_sha256": artifacts["acl"]["sha256"], "encryption_control_id": scope["encryption_control_id"],
            "key_owner_id": scope["encryption_key_owner_id"], "key_id": scope["encryption_key_id"], "key_material_in_receipt": False,
            "retention": RETENTION, "retention_evidence_sha256": artifacts["retention"]["sha256"],
            "generation_manifest_sha256": observations["generation_manifest"], "blob_set_sha256": observations["blob_set"],
            "blob_count": 10, "blob_bytes": 600, "completed_generation_verified": True, "gc_hold_closed": True,
        },
        "generation_boundary": {
            "authority_order": ["notion", "second_brain"], "separate_canonical_authorities": True, "cross_database_transaction_claimed": False,
            "notion": {"snapshot_db_sha256": observations["notion_snapshot"], "blob_manifest_sha256": observations["notion_blob_manifest"], "audit_high_water": 101, "event_high_water": 202, "crdt_checkpoint_high_water": 303, "blob_count": 4, "missing_blob_count": 0, "event_order_mismatch_count": 0, "publication_link_mismatch_count": 0, "transient_upload_count": 0},
            "second_brain": {"snapshot_db_sha256": observations["sbe_snapshot"], "backup_manifest_sha256": observations["sbe_backup_manifest"], "audit_high_water": 404, "publication_high_water": 505, "blob_count": 6, "missing_blob_count": 0, "publication_link_mismatch_count": 0, "active_backup_hold_count": 0},
            "identity": {"database_included": False, "fresh_session_required": True, "signing_secret_rotated": True},
        },
        "target_restore": {
            "source_host_id": scope["source_host_id"], "target_host_id": scope["target_host_id"],
            "generation_id": _GENERATION_ID, "generation_manifest_sha256": observations["generation_manifest"],
            "notion_snapshot_db_sha256": observations["notion_snapshot"], "sbe_snapshot_db_sha256": observations["sbe_snapshot"],
            "restored_blob_set_sha256": observations["blob_set"], "restored_blob_count": 10,
            "source_root_locator_hmac": scope["source_root_locator_hmac"],
            "target_root_locator_hmac": scope["restore_root_locator_hmac"], "port_set_sha256": scope["port_set_sha256"], "windows_target": True,
            "new_root": True, "empty_before_restore": True, "isolated_services_used": True, "source_and_restore_roots_disjoint": True,
            "test_mode_used": False, "failpoint_used": False, "release_candidate": handoff["release_candidate"],
            "integrity": {"quick_check": "ok", "foreign_key_violation_count": 0, "checksum_mismatch_count": 0, "canonical_mismatch_count": 0, "projection_mismatch_count": 0, "lost_event_count": 0, "duplicate_effect_count": 0},
            "integrity_evidence_sha256": artifacts["target_integrity"]["sha256"], "canaries": {name: True for name in CANARY_IDS},
            "canary_evidence_sha256": artifacts["canary_results"]["sha256"],
        },
        "timing": {
            "first_external_action_at": "2026-09-07T00:03:00.000Z", "notion_audit_high_water_at": "2026-09-07T00:04:00.000Z",
            "sbe_audit_high_water_at": "2026-09-07T00:04:30.000Z", "oldest_canonical_high_water_at": "2026-09-07T00:04:00.000Z",
            "backup_manifest_created_at": "2026-09-07T00:05:00.000Z", "external_publish_completed_at": "2026-09-07T00:06:00.000Z",
            "assumed_incident_at": "2026-09-07T00:09:00.000Z", "drill_started_at": "2026-09-07T00:10:00.000Z",
            "smoke_ready_at": "2026-09-07T00:30:00.000Z", "drill_completed_at": "2026-09-07T00:31:00.000Z",
            "measured_rpo_milliseconds": 300000, "measured_rto_milliseconds": 1260000, "operation_elapsed_milliseconds": 1680000,
            "rpo_source": "oldest_canonical_authority_high_water_timestamp", "rto_source": "assumed_incident_to_all_required_canaries_ready", "rto_end_condition": "all_required_canaries_ready",
            "o1b_rpo_seconds_max": 900, "o1b_rto_seconds_max": 3600, "sbe_rpo_seconds_max": 3600, "sbe_rto_seconds_max": 14400,
            "o1b_passed": True, "sbe_passed": True, "timing_evidence_sha256": artifacts["timing"]["sha256"],
        },
        "cleanup": {"isolated_services_stopped": True, "isolated_ports_closed": True, "production_user_traffic_changed": False, "production_restore_executed": False, "same_volume_workaround_used": False, "unrelated_processes_targeted": False, "external_completed_generation_retained": True, "approval_single_use_nonce_consumed": True, "target_evidence_store_sealed": True, "cleanup_evidence_sha256": artifacts["cleanup"]["sha256"]},
        "redaction": {"raw_path_count": 0, "credential_count": 0, "secret_material_count": 0, "private_content_body_count": 0, "raw_identity_count": 0, "raw_endpoint_count": 0, "authorization_header_count": 0, "cookie_count": 0, "scan_evidence_sha256": artifacts["redaction_scan"]["sha256"]},
        "claims": {"O1B_P1_gate_passed": True, "external_encrypted_backup_verified": True, "target_host_new_root_restore_verified": True, "target_operations_handoff_consumed": True, "production_restore_executed": False, "production_user_traffic_changed": False, "product_complete": False, "active_shared_goal_complete": False},
    }
    _reseal_receipt(value, key)
    return value


def _evidence_set(tmp_path: Path) -> dict[str, object]:
    keys = _keys()
    policy_path, policy = _bound_policy(tmp_path, keys)
    approval = _approval(policy, keys["approval"])
    approval_path = tmp_path / "approval.json"
    _write(approval_path, approval)
    handoff = _handoff(policy, approval, keys["handoff"])
    handoff_path = tmp_path / "handoff.json"
    _write(handoff_path, handoff)
    evidence_root = tmp_path / "evidence"
    evidence_exports = _write_evidence_artifacts(
        evidence_root,
        policy=policy,
        approval=approval,
        handoff_path=handoff_path,
    )
    approval["approval_source_evidence_sha256"] = _sha256_file(
        evidence_root / "approval_source.json"
    )
    _reseal_approval(approval, keys["approval"])
    _write(approval_path, approval)
    index = _evidence_index(approval, evidence_root, keys["evidence_index"])
    index_path = tmp_path / "evidence-index.json"
    _write(index_path, index)
    receipt = _receipt(policy_path, policy, approval, approval_path, handoff, handoff_path, index, index_path, evidence_exports, keys["receipt_reviewer"])
    receipt_path = tmp_path / "receipt.json"
    _write(receipt_path, receipt)
    return {"keys": keys, "policy": policy, "policy_path": policy_path, "approval": approval, "approval_path": approval_path, "handoff": handoff, "handoff_path": handoff_path, "evidence_exports": evidence_exports, "evidence_root": evidence_root, "index": index, "index_path": index_path, "receipt": receipt, "receipt_path": receipt_path}


def _verify_set(bundle: dict[str, object], *, require_execution: bool = False) -> dict[str, object]:
    return verify(bundle["policy_path"], approval_path=bundle["approval_path"], handoff_path=bundle["handoff_path"], evidence_index_path=bundle["index_path"], evidence_root=bundle["evidence_root"], receipt_path=bundle["receipt_path"], expected_policy_sha256=_sha256_file(bundle["policy_path"]), require_execution=require_execution)  # type: ignore[arg-type]


@pytest.mark.unit
def test_o1b_pre_execution_verifies_live_signed_grant_without_starting_target_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _evidence_set(tmp_path)
    policy_path = bundle["policy_path"]
    assert isinstance(policy_path, Path)
    policy_sha256 = _sha256_file(policy_path)
    monkeypatch.setattr(
        target_restore_verifier,
        "AUTHORIZED_EXECUTION_POLICY_SHA256",
        policy_sha256,
    )

    report = verify_pre_execution(
        policy_path,
        approval_path=bundle["approval_path"],  # type: ignore[arg-type]
        handoff_path=bundle["handoff_path"],  # type: ignore[arg-type]
        expected_policy_sha256=policy_sha256,
        now=datetime(2026, 9, 7, 0, 2, 15, tzinfo=timezone.utc),
    )

    assert report["status"] == "pass"
    assert report["error_count"] == 0
    assert report["errors"] == []
    assert report["pre_execution_authorized"] is True
    assert report["authorized_execution_id"] == _EXECUTION_ID
    assert report["nonce_consumed"] is False
    assert report["external_action_started"] is False
    assert report["target_action_executed"] is False
    assert report["gate_passed"] is False


@pytest.mark.unit
def test_o1b_pre_execution_fails_without_reviewed_policy_code_pin(
    tmp_path: Path,
) -> None:
    bundle = _evidence_set(tmp_path)
    policy_path = bundle["policy_path"]
    assert isinstance(policy_path, Path)

    report = verify_pre_execution(
        policy_path,
        approval_path=bundle["approval_path"],  # type: ignore[arg-type]
        handoff_path=bundle["handoff_path"],  # type: ignore[arg-type]
        expected_policy_sha256=_sha256_file(policy_path),
        now=datetime(2026, 9, 7, 0, 2, 15, tzinfo=timezone.utc),
    )

    assert report["status"] == "fail"
    assert report["pre_execution_authorized"] is False
    assert any(
        item["code"] == "EXECUTION_POLICY_NOT_ACTIVATED"
        for item in report["errors"]
    )
    assert report["external_action_started"] is False


@pytest.mark.unit
def test_o1b_pre_execution_rejects_expired_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _evidence_set(tmp_path)
    policy_path = bundle["policy_path"]
    assert isinstance(policy_path, Path)
    policy_sha256 = _sha256_file(policy_path)
    monkeypatch.setattr(
        target_restore_verifier,
        "AUTHORIZED_EXECUTION_POLICY_SHA256",
        policy_sha256,
    )

    report = verify_pre_execution(
        policy_path,
        approval_path=bundle["approval_path"],  # type: ignore[arg-type]
        handoff_path=bundle["handoff_path"],  # type: ignore[arg-type]
        expected_policy_sha256=policy_sha256,
        now=datetime(2026, 9, 7, 2, 0, 0, tzinfo=timezone.utc),
    )

    assert report["status"] == "fail"
    assert any(
        item["code"] == "PRE_EXECUTION_WINDOW_CLOSED"
        for item in report["errors"]
    )
    assert report["target_action_executed"] is False


@pytest.mark.unit
def test_o1b_pre_execution_rejects_tampered_approval_before_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _evidence_set(tmp_path)
    policy_path = bundle["policy_path"]
    approval_path = bundle["approval_path"]
    assert isinstance(policy_path, Path) and isinstance(approval_path, Path)
    policy_sha256 = _sha256_file(policy_path)
    monkeypatch.setattr(
        target_restore_verifier,
        "AUTHORIZED_EXECUTION_POLICY_SHA256",
        policy_sha256,
    )
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["approver_id"] = "tampered-operator"
    _write(approval_path, approval)

    report = verify_pre_execution(
        policy_path,
        approval_path=approval_path,
        handoff_path=bundle["handoff_path"],  # type: ignore[arg-type]
        expected_policy_sha256=policy_sha256,
        now=datetime(2026, 9, 7, 0, 2, 15, tzinfo=timezone.utc),
    )

    assert report["status"] == "fail"
    assert any(
        item["code"] in {"APPROVAL_SIGNATURE_INVALID", "APPROVAL_SELF_HASH"}
        for item in report["errors"]
    )
    assert report["external_action_started"] is False


@pytest.mark.unit
def test_o1b_execution_plan_is_immutable_sanitized_and_non_authorizing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _evidence_set(tmp_path)
    policy_path = bundle["policy_path"]
    approval_path = bundle["approval_path"]
    handoff_path = bundle["handoff_path"]
    assert all(
        isinstance(path, Path)
        for path in (policy_path, approval_path, handoff_path)
    )
    policy_sha256 = _sha256_file(policy_path)  # type: ignore[arg-type]
    monkeypatch.setattr(
        target_restore_verifier,
        "AUTHORIZED_EXECUTION_POLICY_SHA256",
        policy_sha256,
    )
    output_path = tmp_path / "execution-plan.json"

    result = compile_execution_plan(
        policy_path=policy_path,  # type: ignore[arg-type]
        approval_path=approval_path,  # type: ignore[arg-type]
        handoff_path=handoff_path,  # type: ignore[arg-type]
        expected_policy_sha256=policy_sha256,
        output_path=output_path,
        now=datetime(2026, 9, 7, 0, 2, 15, tzinfo=timezone.utc),
    )

    plan = json.loads(output_path.read_text(encoding="utf-8"))
    record_sha256 = plan.pop("plan_sha256")
    approval = bundle["approval"]
    assert isinstance(approval, dict)
    raw_text = output_path.read_text(encoding="utf-8")
    assert result["status"] == "pass"
    assert result["plan_sha256"] == _sha256_file(output_path)
    assert record_sha256 == _sha256_value(plan)
    assert plan["contract"] == PLAN_CONTRACT
    assert plan["required_transition_order"] == REQUIRED_TRANSITION_ORDER
    assert plan["execution_invariants"]["plan_is_execution_authority"] is False
    assert plan["execution_invariants"]["fresh_pre_execution_recheck_before_nonce_cas"] is True
    assert plan["claims"]["nonce_consumed"] is False
    assert plan["claims"]["external_action_started"] is False
    assert plan["claims"]["target_backup_restore_executed"] is False
    assert plan["claims"]["gate_passed"] is False
    assert approval["single_use_nonce"] not in raw_text
    assert str(tmp_path) not in raw_text


@pytest.mark.unit
def test_o1b_execution_plan_fails_closed_without_activated_policy(
    tmp_path: Path,
) -> None:
    bundle = _evidence_set(tmp_path)
    policy_path = bundle["policy_path"]
    assert isinstance(policy_path, Path)
    output_path = tmp_path / "execution-plan.json"

    with pytest.raises(ExecutionPlanError, match="pre_execution_not_authorized"):
        compile_execution_plan(
            policy_path=policy_path,
            approval_path=bundle["approval_path"],  # type: ignore[arg-type]
            handoff_path=bundle["handoff_path"],  # type: ignore[arg-type]
            expected_policy_sha256=_sha256_file(policy_path),
            output_path=output_path,
            now=datetime(2026, 9, 7, 0, 2, 15, tzinfo=timezone.utc),
        )

    assert not output_path.exists()


@pytest.mark.unit
def test_o1b_execution_plan_never_overwrites_existing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _evidence_set(tmp_path)
    policy_path = bundle["policy_path"]
    assert isinstance(policy_path, Path)
    policy_sha256 = _sha256_file(policy_path)
    monkeypatch.setattr(
        target_restore_verifier,
        "AUTHORIZED_EXECUTION_POLICY_SHA256",
        policy_sha256,
    )
    output_path = tmp_path / "execution-plan.json"
    output_path.write_bytes(b"preserve-me")

    with pytest.raises(FileExistsError):
        compile_execution_plan(
            policy_path=policy_path,
            approval_path=bundle["approval_path"],  # type: ignore[arg-type]
            handoff_path=bundle["handoff_path"],  # type: ignore[arg-type]
            expected_policy_sha256=policy_sha256,
            output_path=output_path,
            now=datetime(2026, 9, 7, 0, 2, 15, tzinfo=timezone.utc),
        )

    assert output_path.read_bytes() == b"preserve-me"


@pytest.mark.unit
def test_o1b_execution_plan_rejects_artifact_changed_after_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _evidence_set(tmp_path)
    policy_path = bundle["policy_path"]
    approval_path = bundle["approval_path"]
    assert isinstance(policy_path, Path) and isinstance(approval_path, Path)
    policy_sha256 = _sha256_file(policy_path)
    monkeypatch.setattr(
        target_restore_verifier,
        "AUTHORIZED_EXECUTION_POLICY_SHA256",
        policy_sha256,
    )
    real_preflight = target_restore_verifier.verify_pre_execution

    def mutate_after_preflight(*args: object, **kwargs: object) -> dict[str, object]:
        report = real_preflight(*args, **kwargs)  # type: ignore[arg-type]
        approval_path.write_bytes(approval_path.read_bytes() + b" ")
        return report

    monkeypatch.setattr(
        target_execution_plan_compiler.target_restore_verifier,
        "verify_pre_execution",
        mutate_after_preflight,
    )
    output_path = tmp_path / "execution-plan.json"

    with pytest.raises(
        ExecutionPlanError,
        match="artifact_changed_after_pre_execution",
    ):
        compile_execution_plan(
            policy_path=policy_path,
            approval_path=approval_path,
            handoff_path=bundle["handoff_path"],  # type: ignore[arg-type]
            expected_policy_sha256=policy_sha256,
            output_path=output_path,
            now=datetime(2026, 9, 7, 0, 2, 15, tzinfo=timezone.utc),
        )

    assert not output_path.exists()


def _unsigned_authority_sources(
    bundle: dict[str, object],
    tmp_path: Path,
) -> tuple[Path, Path, Path]:
    approval = bundle["approval"]
    handoff = bundle["handoff"]
    assert isinstance(approval, dict) and isinstance(handoff, dict)
    approval_document = {
        key: value
        for key, value in approval.items()
        if key not in {"signature", "receipt_sha256"}
    }
    handoff_document = {
        key: value
        for key, value in handoff.items()
        if key not in {"reviewer_attestation", "receipt_sha256"}
    }
    reviewer = handoff["reviewer_attestation"]
    assert isinstance(reviewer, dict)
    reviewer_metadata = {
        "schema_version": 1,
        "contract": REVIEWER_METADATA_CONTRACT,
        "reviewer_id": reviewer["reviewer_id"],
        "attestation_system_id": reviewer["attestation_system_id"],
        "trust_policy_id": reviewer["trust_policy_id"],
        "signature_record_id": reviewer["signature_record_id"],
    }
    approval_path = tmp_path / "unsigned-approval.json"
    handoff_path = tmp_path / "unsigned-handoff.json"
    metadata_path = tmp_path / "handoff-reviewer-metadata.json"
    _write(approval_path, approval_document)
    _write(handoff_path, handoff_document)
    _write(metadata_path, reviewer_metadata)
    return approval_path, handoff_path, metadata_path


@pytest.mark.unit
def test_o1b_signing_request_compiler_emits_exact_unsigned_messages(
    tmp_path: Path,
) -> None:
    bundle = _evidence_set(tmp_path)
    policy_path = bundle["policy_path"]
    assert isinstance(policy_path, Path)
    approval_path, handoff_path, metadata_path = _unsigned_authority_sources(
        bundle,
        tmp_path,
    )
    output_path = tmp_path / "signing-requests.json"

    result = compile_signing_requests(
        policy_path=policy_path,
        expected_policy_sha256=_sha256_file(policy_path),
        approval_document_path=approval_path,
        handoff_document_path=handoff_path,
        reviewer_metadata_path=metadata_path,
        output_path=output_path,
    )

    request = json.loads(output_path.read_text(encoding="utf-8"))
    record_sha256 = request.pop("request_sha256")
    approval_message = base64.b64decode(
        request["approval_request"]["signing_message"]["message_base64"],
        validate=True,
    )
    handoff_message = base64.b64decode(
        request["handoff_request"]["signing_message"]["message_base64"],
        validate=True,
    )
    assert result["status"] == "pass"
    assert result["request_file_sha256"] == _sha256_file(output_path)
    assert record_sha256 == _sha256_value(request)
    assert request["contract"] == SIGNING_REQUEST_CONTRACT
    assert json.loads(approval_message) == request["approval_request"]["document"]
    assert json.loads(handoff_message) == request["handoff_request"]["reviewer_attestation_payload"]
    assert request["claims"]["approval_granted"] is False
    assert request["claims"]["private_key_loaded"] is False
    assert request["claims"]["signature_created"] is False
    assert request["claims"]["external_action_started"] is False
    assert request["claims"]["gate_passed"] is False


@pytest.mark.unit
def test_o1b_signing_request_compiler_rejects_invalid_unsigned_scope(
    tmp_path: Path,
) -> None:
    bundle = _evidence_set(tmp_path)
    policy_path = bundle["policy_path"]
    assert isinstance(policy_path, Path)
    approval_path, handoff_path, metadata_path = _unsigned_authority_sources(
        bundle,
        tmp_path,
    )
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["resource_scope"]["target_host_id"] = approval["resource_scope"]["source_host_id"]
    _write(approval_path, approval)
    output_path = tmp_path / "signing-requests.json"

    with pytest.raises(
        SigningRequestError,
        match="unsigned_approval_contract_invalid",
    ):
        compile_signing_requests(
            policy_path=policy_path,
            expected_policy_sha256=_sha256_file(policy_path),
            approval_document_path=approval_path,
            handoff_document_path=handoff_path,
            reviewer_metadata_path=metadata_path,
            output_path=output_path,
        )

    assert not output_path.exists()


@pytest.mark.unit
def test_o1b_signing_request_compiler_never_overwrites_output(
    tmp_path: Path,
) -> None:
    bundle = _evidence_set(tmp_path)
    policy_path = bundle["policy_path"]
    assert isinstance(policy_path, Path)
    approval_path, handoff_path, metadata_path = _unsigned_authority_sources(
        bundle,
        tmp_path,
    )
    output_path = tmp_path / "signing-requests.json"
    output_path.write_bytes(b"preserve-me")

    with pytest.raises(FileExistsError):
        compile_signing_requests(
            policy_path=policy_path,
            expected_policy_sha256=_sha256_file(policy_path),
            approval_document_path=approval_path,
            handoff_document_path=handoff_path,
            reviewer_metadata_path=metadata_path,
            output_path=output_path,
        )

    assert output_path.read_bytes() == b"preserve-me"


def _compiled_signing_request(
    bundle: dict[str, object],
    tmp_path: Path,
) -> Path:
    policy_path = bundle["policy_path"]
    assert isinstance(policy_path, Path)
    approval_path, handoff_path, metadata_path = _unsigned_authority_sources(
        bundle,
        tmp_path,
    )
    request_path = tmp_path / "signing-requests.json"
    compile_signing_requests(
        policy_path=policy_path,
        expected_policy_sha256=_sha256_file(policy_path),
        approval_document_path=approval_path,
        handoff_document_path=handoff_path,
        reviewer_metadata_path=metadata_path,
        output_path=request_path,
    )
    return request_path


def _signature_responses(
    bundle: dict[str, object],
    request_path: Path,
    tmp_path: Path,
) -> tuple[Path, Path]:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    keys = bundle["keys"]
    assert isinstance(keys, dict)
    paths: list[Path] = []
    for role in ("approval", "handoff"):
        request_value = request[f"{role}_request"]
        signing_message = base64.b64decode(
            request_value["signing_message"]["message_base64"],
            validate=True,
        )
        key = keys[role]
        assert isinstance(key, Ed25519PrivateKey)
        value = {
            "schema_version": 1,
            "contract": SIGNATURE_RESPONSE_CONTRACT,
            "request_id": request["request_id"],
            "role": role,
            "key_id": request_value["signature_key_id"],
            "algorithm": "ed25519",
            "message_sha256": request_value["signing_message"]["message_sha256"],
            "signature_base64": base64.b64encode(key.sign(signing_message)).decode("ascii"),
        }
        path = tmp_path / f"{role}-signature.json"
        _write(path, value)
        paths.append(path)
    return paths[0], paths[1]


@pytest.mark.unit
def test_o1b_authority_assembler_produces_preflight_verifiable_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _evidence_set(tmp_path)
    policy_path = bundle["policy_path"]
    assert isinstance(policy_path, Path)
    request_path = _compiled_signing_request(bundle, tmp_path)
    approval_signature_path, handoff_signature_path = _signature_responses(
        bundle,
        request_path,
        tmp_path,
    )
    output_dir = tmp_path / "assembled-authority"

    result = assemble_authority(
        policy_path=policy_path,
        expected_policy_sha256=_sha256_file(policy_path),
        signing_request_path=request_path,
        expected_signing_request_sha256=_sha256_file(request_path),
        approval_signature_path=approval_signature_path,
        handoff_signature_path=handoff_signature_path,
        output_dir=output_dir,
    )

    manifest = json.loads(
        (output_dir / "authority-manifest.json").read_text(encoding="utf-8")
    )
    manifest_record_sha256 = manifest.pop("manifest_sha256")
    assert result["status"] == "pass"
    assert manifest["contract"] == AUTHORITY_MANIFEST_CONTRACT
    assert manifest_record_sha256 == _sha256_value(manifest)
    assert manifest["assembly_invariants"]["private_key_loaded"] is False
    assert manifest["assembly_invariants"]["detached_signatures_verified"] is True
    assert manifest["claims"]["nonce_consumed"] is False
    assert manifest["claims"]["external_action_started"] is False
    policy_sha256 = _sha256_file(policy_path)
    monkeypatch.setattr(
        target_restore_verifier,
        "AUTHORIZED_EXECUTION_POLICY_SHA256",
        policy_sha256,
    )
    preflight = verify_pre_execution(
        policy_path,
        approval_path=output_dir / "approval.json",
        handoff_path=output_dir / "handoff.json",
        expected_policy_sha256=policy_sha256,
        now=datetime(2026, 9, 7, 0, 2, 15, tzinfo=timezone.utc),
    )
    assert preflight["status"] == "pass"
    assert preflight["pre_execution_authorized"] is True
    assert preflight["nonce_consumed"] is False
    assert preflight["external_action_started"] is False


@pytest.mark.unit
def test_o1b_authority_assembler_rejects_wrong_detached_signature(
    tmp_path: Path,
) -> None:
    bundle = _evidence_set(tmp_path)
    policy_path = bundle["policy_path"]
    assert isinstance(policy_path, Path)
    request_path = _compiled_signing_request(bundle, tmp_path)
    approval_signature_path, handoff_signature_path = _signature_responses(
        bundle,
        request_path,
        tmp_path,
    )
    response = json.loads(approval_signature_path.read_text(encoding="utf-8"))
    keys = bundle["keys"]
    assert isinstance(keys, dict)
    wrong_key = keys["handoff"]
    assert isinstance(wrong_key, Ed25519PrivateKey)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    message = base64.b64decode(
        request["approval_request"]["signing_message"]["message_base64"],
        validate=True,
    )
    response["signature_base64"] = base64.b64encode(wrong_key.sign(message)).decode("ascii")
    _write(approval_signature_path, response)
    output_dir = tmp_path / "assembled-authority"

    with pytest.raises(AuthorityAssemblyError, match="approval_signature_invalid"):
        assemble_authority(
            policy_path=policy_path,
            expected_policy_sha256=_sha256_file(policy_path),
            signing_request_path=request_path,
            expected_signing_request_sha256=_sha256_file(request_path),
            approval_signature_path=approval_signature_path,
            handoff_signature_path=handoff_signature_path,
            output_dir=output_dir,
        )

    assert not output_dir.exists()


@pytest.mark.unit
def test_o1b_authority_assembler_never_reuses_existing_output_directory(
    tmp_path: Path,
) -> None:
    bundle = _evidence_set(tmp_path)
    policy_path = bundle["policy_path"]
    assert isinstance(policy_path, Path)
    request_path = _compiled_signing_request(bundle, tmp_path)
    approval_signature_path, handoff_signature_path = _signature_responses(
        bundle,
        request_path,
        tmp_path,
    )
    output_dir = tmp_path / "assembled-authority"
    output_dir.mkdir()
    sentinel = output_dir / "preserve.txt"
    sentinel.write_text("preserve-me", encoding="utf-8")

    with pytest.raises(FileExistsError):
        assemble_authority(
            policy_path=policy_path,
            expected_policy_sha256=_sha256_file(policy_path),
            signing_request_path=request_path,
            expected_signing_request_sha256=_sha256_file(request_path),
            approval_signature_path=approval_signature_path,
            handoff_signature_path=handoff_signature_path,
            output_dir=output_dir,
        )

    assert sentinel.read_text(encoding="utf-8") == "preserve-me"


@pytest.mark.unit
def test_o1b_readiness_contract_passes_without_claiming_target_execution() -> None:
    report = verify()
    assert report["status"] == "pass", report
    assert report["readiness_contract_passed"] is True
    assert report["gate_passed"] is False
    assert report["approval_required"] is True


@pytest.mark.unit
def test_o1b_gate_mode_is_machine_readable_failure_without_external_evidence() -> None:
    report = verify(require_execution=True)
    assert report["status"] == "fail"
    assert report["gate_passed"] is False
    assert report["execution_required_mode"] is True
    assert any(error["code"] == "TARGET_EXECUTION_EVIDENCE_REQUIRED" for error in report["errors"])
    assert main(["--compact", "--require-execution"]) == 1


@pytest.mark.unit
def test_o1b_readiness_contract_rejects_false_green(tmp_path: Path) -> None:
    value = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    value["claims"]["O1B_P1_gate_passed"] = True
    path = tmp_path / "false-green-policy.json"
    _write(path, value)
    report = verify(path)
    assert report["status"] == "fail"
    assert any(error["code"] == "POLICY_FALSE_GREEN" for error in report["errors"])


@pytest.mark.unit
def test_o1b_complete_cryptographically_bound_external_set_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _evidence_set(tmp_path)
    monkeypatch.setattr(
        target_restore_verifier,
        "AUTHORIZED_EXECUTION_POLICY_SHA256",
        _sha256_file(bundle["policy_path"]),  # type: ignore[arg-type]
    )
    report = _verify_set(bundle, require_execution=True)
    assert report["status"] == "pass", report
    assert report["gate_passed"] is True
    assert report["approval_required"] is False
    assert report["product_complete"] is False


@pytest.mark.unit
def test_o1b_valid_external_set_is_rejected_until_policy_hash_is_activated(
    tmp_path: Path,
) -> None:
    report = _verify_set(_evidence_set(tmp_path), require_execution=True)
    assert report["status"] == "fail"
    assert report["gate_passed"] is False
    assert any(
        error["code"] == "EXECUTION_POLICY_NOT_ACTIVATED"
        for error in report["errors"]
    )


@pytest.mark.unit
@pytest.mark.parametrize(("mutate", "expected_code"), [
    (lambda receipt: receipt["external_backup"].update({"encryption_at_rest_verified": False}), "EXTERNAL_BACKUP_INVALID"),
    (lambda receipt: receipt["external_backup"].update({"destination_host_id": "source-host-01"}), "EXTERNAL_BACKUP_INVALID"),
    (lambda receipt: receipt["external_backup"].update({"test_mode_used": True}), "EXTERNAL_BACKUP_INVALID"),
    (lambda receipt: receipt["timing"].update({"measured_rpo_milliseconds": 900001}), "RECOVERY_TIMING_INVALID"),
    (lambda receipt: receipt["timing"].update({"oldest_canonical_high_water_at": "2026-09-07T00:04:30.000Z", "measured_rpo_milliseconds": 270000}), "RECOVERY_TIMING_INVALID"),
    (lambda receipt: receipt["approval_binding"].update({"nonce_prior_consumption_count": 1}), "APPROVAL_INVALID"),
    (lambda receipt: receipt["target_operations_handoff"].update({"sha256": _sha_tag("wrong-handoff")}), "HANDOFF_BINDING_INVALID"),
    (lambda receipt: receipt["observed_resource_scope"].update({"subject_set_sha256": _sha_tag("wrong-subjects")}), "EVIDENCE_INDEX_BINDING_INVALID"),
    (lambda receipt: receipt["target_restore"]["canaries"].update({"search": False}), "TARGET_RESTORE_INVALID"),
    (lambda receipt: receipt["target_restore"].update({"generation_id": "00000000-0000-4000-8000-000000000099"}), "TARGET_RESTORE_INVALID"),
    (lambda receipt: receipt["target_restore"].update({"restored_blob_count": 9}), "TARGET_RESTORE_INVALID"),
    (lambda receipt: receipt["claims"].update({"product_complete": True}), "RECEIPT_FALSE_GREEN"),
])
def test_o1b_receipt_rejects_false_green_or_safety_drift(tmp_path: Path, mutate: Callable[[dict[str, object]], object], expected_code: str) -> None:
    bundle = _evidence_set(tmp_path)
    receipt, keys = bundle["receipt"], bundle["keys"]
    assert isinstance(receipt, dict) and isinstance(keys, dict)
    mutate(receipt)
    _reseal_receipt(receipt, keys["receipt_reviewer"])
    _write(bundle["receipt_path"], receipt)  # type: ignore[arg-type]
    report = _verify_set(bundle)
    assert report["status"] == "fail"
    assert any(error["code"] == expected_code for error in report["errors"]), report


@pytest.mark.unit
def test_o1b_rejects_forged_approval_signature(tmp_path: Path) -> None:
    bundle = _evidence_set(tmp_path)
    approval, receipt, keys = bundle["approval"], bundle["receipt"], bundle["keys"]
    assert isinstance(approval, dict) and isinstance(receipt, dict) and isinstance(keys, dict)
    signature = approval["signature"]
    assert isinstance(signature, dict)
    signature["signature_base64"] = base64.b64encode(b"x" * 64).decode("ascii")
    approval["receipt_sha256"] = _sha256_value({k: v for k, v in approval.items() if k != "receipt_sha256"})
    _write(bundle["approval_path"], approval)  # type: ignore[arg-type]
    receipt["approval_binding"]["sha256"] = _sha256_file(bundle["approval_path"])  # type: ignore[index,arg-type]
    _reseal_receipt(receipt, keys["receipt_reviewer"])
    _write(bundle["receipt_path"], receipt)  # type: ignore[arg-type]
    report = _verify_set(bundle)
    assert any(error["code"] == "APPROVAL_SIGNATURE_INVALID" for error in report["errors"])


@pytest.mark.unit
def test_o1b_rejects_expired_approval_even_when_resigned(tmp_path: Path) -> None:
    bundle = _evidence_set(tmp_path)
    approval, receipt, keys = bundle["approval"], bundle["receipt"], bundle["keys"]
    assert isinstance(approval, dict) and isinstance(receipt, dict) and isinstance(keys, dict)
    approval["expires_at"] = "2026-09-07T00:02:59.000Z"
    _reseal_approval(approval, keys["approval"])
    _write(bundle["approval_path"], approval)  # type: ignore[arg-type]
    receipt["approval_binding"]["sha256"] = _sha256_file(bundle["approval_path"])  # type: ignore[index,arg-type]
    _reseal_receipt(receipt, keys["receipt_reviewer"])
    _write(bundle["receipt_path"], receipt)  # type: ignore[arg-type]
    report = _verify_set(bundle)
    assert any(error["code"] == "APPROVAL_INVALID" for error in report["errors"])


@pytest.mark.unit
@pytest.mark.parametrize("raw_value", [r"C:/Target/Restore", "/mnt/target/restore", "smb://target/share"])
def test_o1b_rejects_raw_target_locator_forms(tmp_path: Path, raw_value: str) -> None:
    bundle = _evidence_set(tmp_path)
    receipt, keys = bundle["receipt"], bundle["keys"]
    assert isinstance(receipt, dict) and isinstance(keys, dict)
    receipt["external_backup"]["encryption_control_id"] = raw_value  # type: ignore[index]
    _reseal_receipt(receipt, keys["receipt_reviewer"])
    _write(bundle["receipt_path"], receipt)  # type: ignore[arg-type]
    report = _verify_set(bundle)
    assert any(error["code"] in {"RAW_PATH_FORBIDDEN", "RAW_ENDPOINT_FORBIDDEN"} for error in report["errors"])


@pytest.mark.unit
def test_o1b_malformed_external_backup_fails_without_crashing(tmp_path: Path) -> None:
    bundle = _evidence_set(tmp_path)
    receipt, keys = bundle["receipt"], bundle["keys"]
    assert isinstance(receipt, dict) and isinstance(keys, dict)
    receipt["external_backup"] = []
    _reseal_receipt(receipt, keys["receipt_reviewer"])
    _write(bundle["receipt_path"], receipt)  # type: ignore[arg-type]
    report = _verify_set(bundle)
    assert report["status"] == "fail"
    assert any(error["code"] == "EXTERNAL_BACKUP_INVALID" for error in report["errors"])


@pytest.mark.unit
def test_o1b_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    bundle = _evidence_set(tmp_path)
    receipt = bundle["receipt"]
    assert isinstance(receipt, dict)
    encoded = json.dumps(receipt, ensure_ascii=False)
    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text(encoded[:-1] + ',"packet_id":"O1B-P1"}', encoding="utf-8")
    bundle["receipt_path"] = duplicate_path
    report = _verify_set(bundle)
    assert report["status"] == "fail"
    assert any(error["code"] == "RECEIPT_READ_FAILED" for error in report["errors"])


@pytest.mark.unit
def test_o1b_rejects_forged_evidence_index_signature(tmp_path: Path) -> None:
    bundle = _evidence_set(tmp_path)
    index = bundle["index"]
    assert isinstance(index, dict)
    signature = index["signature"]
    assert isinstance(signature, dict)
    signature["signature_base64"] = base64.b64encode(b"y" * 64).decode("ascii")
    index["receipt_sha256"] = _sha256_value({k: v for k, v in index.items() if k != "receipt_sha256"})
    _write(bundle["index_path"], index)  # type: ignore[arg-type]
    report = _verify_set(bundle)
    assert any(error["code"] == "EVIDENCE_INDEX_SIGNATURE_INVALID" for error in report["errors"])


@pytest.mark.unit
def test_o1b_rejects_missing_sanitized_evidence_artifact(tmp_path: Path) -> None:
    bundle = _evidence_set(tmp_path)
    artifact_path = bundle["evidence_root"] / "canary_results.json"  # type: ignore[operator]
    artifact_path.unlink()
    report = _verify_set(bundle)
    assert any(error["code"] == "EVIDENCE_INDEX_ARTIFACTS" for error in report["errors"])


@pytest.mark.unit
def test_o1b_rejects_unbound_machine_evidence_provenance(tmp_path: Path) -> None:
    bundle = _evidence_set(tmp_path)
    export = bundle["evidence_exports"]["source_machine_identity"]  # type: ignore[index]
    assert isinstance(export, dict)
    export["source_system_id"] = "unrelated-observer-01"
    export["observation_sha256"] = _sha_tag("unrelated-machine")
    export_path = bundle["evidence_root"] / "source_machine_identity.json"  # type: ignore[operator]
    _write(export_path, export)
    index, receipt, keys = bundle["index"], bundle["receipt"], bundle["keys"]
    assert isinstance(index, dict) and isinstance(receipt, dict) and isinstance(keys, dict)
    _artifact_map(index)["source_machine_identity"]["sha256"] = _sha256_file(export_path)
    _reseal_index(index, keys["evidence_index"])
    _write(bundle["index_path"], index)  # type: ignore[arg-type]
    receipt["evidence_index_binding"]["sha256"] = _sha256_file(bundle["index_path"])  # type: ignore[index,arg-type]
    receipt["receipt_id"] = "O1B-P1-TARGET-" + _sha256_file(bundle["index_path"])[7:23].upper()  # type: ignore[arg-type]
    _reseal_receipt(receipt, keys["receipt_reviewer"])
    _write(bundle["receipt_path"], receipt)  # type: ignore[arg-type]
    report = _verify_set(bundle)
    assert any(error["code"] == "EVIDENCE_INDEX_ARTIFACTS" for error in report["errors"])


@pytest.mark.unit
def test_o1b_rejects_nonce_claim_not_bound_to_durable_cas(tmp_path: Path) -> None:
    bundle = _evidence_set(tmp_path)
    export = bundle["evidence_exports"]["nonce_consumption"]  # type: ignore[index]
    assert isinstance(export, dict)
    nonce_observation = export["nonce_observation"]
    assert isinstance(nonce_observation, dict)
    nonce_observation["prior_consumption_count"] = 1
    export["observation_sha256"] = _sha256_value(nonce_observation)
    export_path = bundle["evidence_root"] / "nonce_consumption.json"  # type: ignore[operator]
    _write(export_path, export)
    index, receipt, keys = bundle["index"], bundle["receipt"], bundle["keys"]
    assert isinstance(index, dict) and isinstance(receipt, dict) and isinstance(keys, dict)
    _artifact_map(index)["nonce_consumption"]["sha256"] = _sha256_file(export_path)
    _reseal_index(index, keys["evidence_index"])
    _write(bundle["index_path"], index)  # type: ignore[arg-type]
    receipt["approval_binding"]["nonce_consumption_evidence_sha256"] = _sha256_file(export_path)  # type: ignore[index]
    receipt["evidence_index_binding"]["sha256"] = _sha256_file(bundle["index_path"])  # type: ignore[index,arg-type]
    receipt["receipt_id"] = "O1B-P1-TARGET-" + _sha256_file(bundle["index_path"])[7:23].upper()  # type: ignore[arg-type]
    _reseal_receipt(receipt, keys["receipt_reviewer"])
    _write(bundle["receipt_path"], receipt)  # type: ignore[arg-type]
    report = _verify_set(bundle)
    assert any(error["code"] == "EVIDENCE_INDEX_ARTIFACTS" for error in report["errors"])


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path_key", "expected_code"),
    [
        ("approval_path", "APPROVAL_READ_FAILED"),
        ("handoff_path", "HANDOFF_READ_FAILED"),
        ("index_path", "EVIDENCE_INDEX_READ_FAILED"),
        ("receipt_path", "RECEIPT_READ_FAILED"),
    ],
)
def test_o1b_missing_signed_document_returns_structured_failure(
    tmp_path: Path, path_key: str, expected_code: str
) -> None:
    bundle = _evidence_set(tmp_path)
    bundle[path_key].unlink()  # type: ignore[union-attr]
    report = _verify_set(bundle)
    assert report["status"] == "fail"
    assert any(error["code"] == expected_code for error in report["errors"])


@pytest.mark.unit
def test_o1b_rto_cannot_be_hidden_by_a_late_drill_start(tmp_path: Path) -> None:
    bundle = _evidence_set(tmp_path)
    receipt, keys = bundle["receipt"], bundle["keys"]
    assert isinstance(receipt, dict) and isinstance(keys, dict)
    receipt["timing"].update(  # type: ignore[union-attr]
        {
            "drill_started_at": "2026-09-07T01:08:00.000Z",
            "smoke_ready_at": "2026-09-07T01:10:00.000Z",
            "drill_completed_at": "2026-09-07T01:11:00.000Z",
            "measured_rto_milliseconds": 120000,
            "operation_elapsed_milliseconds": 4080000,
        }
    )
    _reseal_receipt(receipt, keys["receipt_reviewer"])
    _write(bundle["receipt_path"], receipt)  # type: ignore[arg-type]
    report = _verify_set(bundle)
    assert any(error["code"] == "RECOVERY_TIMING_INVALID" for error in report["errors"])


@pytest.mark.unit
def test_o1b_rejects_forged_final_reviewer_signature(tmp_path: Path) -> None:
    bundle = _evidence_set(tmp_path)
    receipt = bundle["receipt"]
    assert isinstance(receipt, dict)
    reviewer = receipt["reviewer_attestation"]
    assert isinstance(reviewer, dict)
    reviewer["signature_base64"] = base64.b64encode(b"z" * 64).decode("ascii")
    receipt["receipt_sha256"] = _sha256_value({k: v for k, v in receipt.items() if k != "receipt_sha256"})
    _write(bundle["receipt_path"], receipt)  # type: ignore[arg-type]
    report = _verify_set(bundle)
    assert any(error["code"] == "REVIEWER_ATTESTATION_INVALID" for error in report["errors"])


@pytest.mark.unit
def test_o1b_rejects_handoff_accepted_after_first_external_action(tmp_path: Path) -> None:
    bundle = _evidence_set(tmp_path)
    handoff, keys = bundle["handoff"], bundle["keys"]
    assert isinstance(handoff, dict) and isinstance(keys, dict)
    handoff["accepted_at"] = "2026-09-07T00:04:00.000Z"
    _reseal_handoff(handoff, keys["handoff"])
    _write(bundle["handoff_path"], handoff)  # type: ignore[arg-type]
    report = _verify_set(bundle)
    assert any(error["code"] == "HANDOFF_BINDING_INVALID" for error in report["errors"])


@pytest.mark.unit
def test_o1b_rejects_external_set_when_policy_trust_anchors_are_pending(tmp_path: Path) -> None:
    bundle = _evidence_set(tmp_path)
    report = verify(
        POLICY_PATH,
        approval_path=bundle["approval_path"],  # type: ignore[arg-type]
        handoff_path=bundle["handoff_path"],  # type: ignore[arg-type]
        evidence_index_path=bundle["index_path"],  # type: ignore[arg-type]
        evidence_root=bundle["evidence_root"],  # type: ignore[arg-type]
        receipt_path=bundle["receipt_path"],  # type: ignore[arg-type]
        expected_policy_sha256=_sha256_file(POLICY_PATH),
    )
    assert report["status"] == "fail"
    assert any(error["code"] == "TRUST_ANCHORS_NOT_BOUND" for error in report["errors"])


@pytest.mark.unit
def test_o1b_rejects_external_set_without_caller_pinned_policy_hash(tmp_path: Path) -> None:
    bundle = _evidence_set(tmp_path)
    report = verify(
        bundle["policy_path"],  # type: ignore[arg-type]
        approval_path=bundle["approval_path"],  # type: ignore[arg-type]
        handoff_path=bundle["handoff_path"],  # type: ignore[arg-type]
        evidence_index_path=bundle["index_path"],  # type: ignore[arg-type]
        evidence_root=bundle["evidence_root"],  # type: ignore[arg-type]
        receipt_path=bundle["receipt_path"],  # type: ignore[arg-type]
    )
    assert report["status"] == "fail"
    assert any(error["code"] == "EXPECTED_POLICY_HASH_REQUIRED" for error in report["errors"])


@pytest.mark.unit
def test_o1b_rejects_signed_index_hash_not_reflected_in_receipt(tmp_path: Path) -> None:
    bundle = _evidence_set(tmp_path)
    index, receipt, keys = bundle["index"], bundle["receipt"], bundle["keys"]
    assert isinstance(index, dict) and isinstance(receipt, dict) and isinstance(keys, dict)
    artifacts = _artifact_map(index)
    artifacts["encryption"]["sha256"] = _sha_tag("changed-encryption-evidence")
    _reseal_index(index, keys["evidence_index"])
    _write(bundle["index_path"], index)  # type: ignore[arg-type]
    receipt["evidence_index_binding"]["sha256"] = _sha256_file(bundle["index_path"])  # type: ignore[index,arg-type]
    receipt["receipt_id"] = "O1B-P1-TARGET-" + _sha256_file(bundle["index_path"])[7:23].upper()  # type: ignore[arg-type]
    _reseal_receipt(receipt, keys["receipt_reviewer"])
    _write(bundle["receipt_path"], receipt)  # type: ignore[arg-type]
    report = _verify_set(bundle)
    assert any(error["code"] == "EXTERNAL_BACKUP_INVALID" for error in report["errors"])


@pytest.mark.unit
@pytest.mark.parametrize(
    "field",
    [
        "approval_binding",
        "target_operations_handoff",
        "evidence_index_binding",
        "observed_resource_scope",
        "sbe_dynamic_baseline",
        "external_backup",
        "generation_boundary",
        "target_restore",
        "timing",
        "cleanup",
        "redaction",
        "reviewer_attestation",
    ],
)
def test_o1b_malformed_receipt_sections_return_structured_failure(
    tmp_path: Path, field: str
) -> None:
    bundle = _evidence_set(tmp_path)
    receipt = bundle["receipt"]
    assert isinstance(receipt, dict)
    receipt[field] = None
    _write(bundle["receipt_path"], receipt)  # type: ignore[arg-type]
    report = _verify_set(bundle)
    assert report["status"] == "fail"
    assert report["error_count"] > 0
    assert isinstance(report["errors"], list)

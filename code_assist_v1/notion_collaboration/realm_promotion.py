"""Fail-closed validation for L9 operator-action promotion evidence.

This module is intentionally a verifier, not an operator.  It reads only the
immutable, hash-bound JSON receipts named by a promotion manifest.  It never
starts pytest, touches an offline production copy, changes feature flags, or
performs a deployment/canary action.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from code_assist_v1.notion_collaboration.release_candidate import (
    DEFAULT_RELEASE_PATTERNS,
    DEFAULT_SCHEMA_CATALOG as DEFAULT_RELEASE_SCHEMA_CATALOG,
    verify_release_candidate_manifest,
)


PROMOTION_MANIFEST_CONTRACT = (
    "code_assist.notion.internal_collaborative_realm_promotion_manifest.v1"
)
OPERATOR_ACTION_RECEIPT_CONTRACT = (
    "code_assist.notion.internal_collaborative_operator_action_receipt.v1"
)
OPERATOR_APPROVAL_RECEIPT_CONTRACT = (
    "code_assist.notion.internal_collaborative_operator_approval_receipt.v1"
)
SBE_HANDOFF_RECEIPT_CONTRACT = (
    "code_assist.notion.internal_collaborative_sbe_target_handoff_receipt.v1"
)
PROMOTION_VERIFIER_CONTRACT = (
    "code_assist.notion.internal_collaborative_realm_promotion_verifier.v1"
)

PROMOTION_STAGES = (
    "offline_rehearsal",
    "default_off_install",
    "production_canary",
)
STAGE_POLICY = {
    "offline_rehearsal": {
        "packet_id": "R0A-P1",
        "approval_id": "AP-PROD-REHEARSAL",
        "realm_id": "approved_offline_production_copy_operator",
    },
    "default_off_install": {
        "packet_id": "R0B-P1",
        "approval_id": "AP-PROD-DEPLOY",
        "realm_id": "production_canary",
    },
    "production_canary": {
        "packet_id": "R1-P1",
        "approval_id": "AP-PROD-CANARY",
        "realm_id": "production_canary",
    },
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
DEFAULT_PROMOTION_ROOT = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_promotion"
)

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_FORBIDDEN_KEYS = frozenset(
    {
        "password",
        "cookie",
        "token",
        "secret",
        "raw_crdt_update",
        "snapshot_bytes",
        "page_title",
        "member_name",
        "comment_body",
        "unpublished_content",
        "private_path",
    }
)

_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "contract",
        "manifest_id",
        "release_candidate_id",
        "deployment_generation_id",
        "source_manifest_sha256",
        "validation_manifest_sha256",
        "schema_catalog_sha256",
        "artifact_manifest_sha256",
        "derivation_proof_sha256",
        "release_candidate_manifest",
        "receipt_bindings",
        "claims",
    }
)
_BINDING_KEYS = frozenset({"path", "sha256", "receipt_id"})
_MANIFEST_CLAIM_KEYS = frozenset(
    {
        "same_release_candidate",
        "same_deployment_generation",
        "no_rebuild_between_stages",
        "operator_actions_only",
        "verifier_accessed_live_root",
        "verifier_invoked_pytest",
        "full_rollout_enabled",
        "product_complete",
    }
)
_ACTION_KEYS = frozenset(
    {
        "schema_version",
        "contract",
        "receipt_id",
        "action_id",
        "stage",
        "packet_id",
        "approval_id",
        "release_candidate_id",
        "realm_id",
        "realm_config_fingerprint",
        "deployment_generation_id",
        "source_manifest_sha256",
        "validation_manifest_sha256",
        "schema_catalog_sha256",
        "artifact_manifest_sha256",
        "derivation_proof_sha256",
        "approval_receipt",
        "sbe_handoff_receipt",
        "scope",
        "execution",
        "result",
        "evidence",
    }
)
_SCOPE_KEYS = frozenset(
    {
        "root_identity_sha256",
        "ports",
        "subject_identity_sha256",
        "subject_roles",
        "workspace_identity_sha256",
        "argv_sha256",
        "forbidden_scope_sha256",
        "precondition_sha256",
        "rollback_sha256",
        "release_candidate_id",
        "realm_config_fingerprint",
        "deployment_generation_id",
    }
)
_EXECUTION_KEYS = frozenset(
    {
        "started_at",
        "ended_at",
        "operator_action",
        "automated_test_runner",
        "pytest_invoked",
        "action_exit_code",
        "cleanup_complete",
        "unrelated_processes_targeted",
    }
)
_RESULT_KEYS = frozenset(
    {"status", "passed", "failed", "errors", "skipped", "xfailed"}
)
_APPROVAL_KEYS = frozenset(
    {
        "schema_version",
        "contract",
        "receipt_id",
        "approval_id",
        "packet_id",
        "authorized_stage",
        "status",
        "scope_sha256",
        "approved_at",
        "expires_at",
        "approval_is_execution_authority_not_evidence",
    }
)
_HANDOFF_KEYS = frozenset(
    {
        "schema_version",
        "contract",
        "receipt_id",
        "external_gate_id",
        "status",
        "release_candidate_id",
        "deployment_generation_id",
        "accepted_at",
        "expires_at",
        "bindings",
    }
)
_HANDOFF_BINDING_KEYS = frozenset(
    {
        "agents_md_sha256",
        "ordered_18_document_manifest_sha256",
        "current_goal_observation_sha256",
        "active_sbe_packet_sha256",
        "sbe_contract_bundle_sha256",
        "current_schema_lineage_sha256",
        "target_authority_sha256",
    }
)

_OFFLINE_EVIDENCE_KEYS = frozenset(
    {
        "approved_offline_copy_only",
        "production_mutation_performed",
        "migration_passed",
        "new_root_restore_passed",
        "integrity_audit_passed",
        "redacted_application_smoke_passed",
        "compatibility_audit_passed",
        "compatible_code_rollback_passed",
        "disaster_recovery_rehearsal_passed",
        "raw_private_content_recorded",
    }
)
_INSTALL_EVIDENCE_KEYS = frozenset(
    {
        "install_port",
        "frozen_artifact_installed",
        "default_off",
        "user_enablement_count",
        "gateway_package_installed",
        "gateway_service_restarted",
        "gateway_health_passed",
        "configuration_derivation_passed",
        "before_after_hashes_present",
        "rollback_ready",
        "artifact_rebuilt",
    }
)
_CANARY_EVIDENCE_KEYS = frozenset(
    {
        "collaborator_count",
        "outsider_negative_control_count",
        "outsider_denied",
        "telemetry_soak_passed",
        "abort_thresholds_configured",
        "abort_threshold_breach_count",
        "aggregate_kill_switch_exercised",
        "capability_kill_switch_exercised",
        "compatible_rollback_passed",
        "recovery_passed",
        "acknowledged_operation_loss_count",
        "post_revoke_delivery_count",
        "convergence_mismatch_count",
        "artifact_rebuilt",
        "broader_rollout_enabled",
    }
)


class RealmPromotionError(ValueError):
    """The supplied promotion evidence is not gate eligible."""

    def __init__(self, code: str, subject: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.subject = subject
        self.message = message

    def as_report_error(self) -> dict[str, str]:
        return {"code": self.code, "subject": self.subject, "message": self.message}


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RealmPromotionError("NON_CANONICAL_JSON", "$", "canonical JSON value required") from exc


def canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RealmPromotionError("DUPLICATE_JSON_KEY", "$", "duplicate JSON key")
        result[key] = value
    return result


def _load_json(path: Path, subject: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except RealmPromotionError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RealmPromotionError("JSON_READ_FAILED", subject, "valid UTF-8 JSON file required") from exc
    if not isinstance(value, dict):
        raise RealmPromotionError("JSON_OBJECT_REQUIRED", subject, "JSON object required")
    _reject_forbidden_keys(value, subject)
    return value


def _reject_forbidden_keys(value: Any, subject: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_KEYS:
                raise RealmPromotionError(
                    "FORBIDDEN_PRIVATE_FIELD",
                    f"{subject}.{key}",
                    "operator evidence must remain hash-only and redacted",
                )
            _reject_forbidden_keys(child, f"{subject}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, f"{subject}[{index}]")


def _exact_keys(value: Any, expected: frozenset[str], subject: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise RealmPromotionError(
            "FIELD_SET_MISMATCH",
            subject,
            f"exact fields required: {sorted(expected)}",
        )
    return value


def _hash(value: Any, subject: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise RealmPromotionError("SHA256_REQUIRED", subject, "canonical lowercase sha256 required")
    return value


def _text(value: Any, subject: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RealmPromotionError("NONEMPTY_TEXT_REQUIRED", subject, "non-empty string required")
    return value


def _time(value: Any, subject: str) -> datetime:
    text = _text(value, subject)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RealmPromotionError("UTC_TIMESTAMP_REQUIRED", subject, "ISO-8601 timestamp required") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RealmPromotionError("UTC_TIMESTAMP_REQUIRED", subject, "UTC timestamp required")
    return parsed


def _exact_bool(value: Any, expected: bool, subject: str) -> None:
    if value is not expected:
        raise RealmPromotionError("ATTESTATION_MISMATCH", subject, f"must be {expected}")


def _exact_int(value: Any, expected: int, subject: str) -> None:
    if type(value) is not int or value != expected:
        raise RealmPromotionError("COUNT_MISMATCH", subject, f"must equal {expected}")


def _hash_list(value: Any, expected_count: int, subject: str) -> list[str]:
    if not isinstance(value, list) or len(value) != expected_count:
        raise RealmPromotionError("HASH_DENOMINATOR_MISMATCH", subject, f"requires {expected_count} hashes")
    result = [_hash(item, f"{subject}[{index}]") for index, item in enumerate(value)]
    if len(set(result)) != len(result):
        raise RealmPromotionError("HASH_DENOMINATOR_MISMATCH", subject, "hash identities must be unique")
    return result


def _bound_path(
    binding: Any,
    *,
    subject: str,
    repository_root: Path,
    allowed_root: Path,
) -> tuple[Path, Mapping[str, Any]]:
    item = _exact_keys(binding, _BINDING_KEYS, subject)
    relative = _text(item["path"], f"{subject}.path")
    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise RealmPromotionError("BOUND_PATH_INVALID", f"{subject}.path", "repository-relative path required")
    root = repository_root.resolve(strict=True)
    allowed = allowed_root.resolve(strict=True)
    target = (root / relative_path).resolve(strict=True)
    if not target.is_file() or not target.is_relative_to(allowed):
        raise RealmPromotionError("BOUND_PATH_INVALID", f"{subject}.path", "file must stay in promotion evidence root")
    expected_hash = _hash(item["sha256"], f"{subject}.sha256")
    if sha256_file(target) != expected_hash:
        raise RealmPromotionError("BOUND_HASH_MISMATCH", subject, "bound file hash differs")
    _text(item["receipt_id"], f"{subject}.receipt_id")
    return target, item


def _validate_approval(
    approval: Mapping[str, Any],
    *,
    stage: str,
    scope: Mapping[str, Any],
    execution_start: datetime,
    execution_end: datetime,
    subject: str,
) -> None:
    _exact_keys(approval, _APPROVAL_KEYS, subject)
    policy = STAGE_POLICY[stage]
    if approval.get("schema_version") != 1 or approval.get("contract") != OPERATOR_APPROVAL_RECEIPT_CONTRACT:
        raise RealmPromotionError("APPROVAL_CONTRACT_MISMATCH", subject, "operator approval contract mismatch")
    for key, expected in (
        ("approval_id", policy["approval_id"]),
        ("packet_id", policy["packet_id"]),
        ("authorized_stage", stage),
        ("status", "approved"),
    ):
        if approval.get(key) != expected:
            raise RealmPromotionError("APPROVAL_SCOPE_MISMATCH", f"{subject}.{key}", f"must equal {expected}")
    _text(approval.get("receipt_id"), f"{subject}.receipt_id")
    if approval.get("scope_sha256") != canonical_sha256(scope):
        raise RealmPromotionError("APPROVAL_SCOPE_HASH_MISMATCH", f"{subject}.scope_sha256", "approval does not bind action scope")
    _exact_bool(
        approval.get("approval_is_execution_authority_not_evidence"),
        True,
        f"{subject}.approval_is_execution_authority_not_evidence",
    )
    approved = _time(approval.get("approved_at"), f"{subject}.approved_at")
    expires = _time(approval.get("expires_at"), f"{subject}.expires_at")
    if not approved <= execution_start <= execution_end <= expires:
        raise RealmPromotionError("APPROVAL_TIME_SCOPE_INVALID", subject, "approval must cover the complete operator action")


def _validate_handoff(
    handoff: Mapping[str, Any],
    *,
    release_candidate_id: str,
    deployment_generation_id: str,
    execution_start: datetime,
    execution_end: datetime,
    expected_bindings: Mapping[str, str],
    subject: str,
) -> None:
    _exact_keys(handoff, _HANDOFF_KEYS, subject)
    if handoff.get("schema_version") != 1 or handoff.get("contract") != SBE_HANDOFF_RECEIPT_CONTRACT:
        raise RealmPromotionError("SBE_HANDOFF_CONTRACT_MISMATCH", subject, "SBE target handoff contract mismatch")
    if handoff.get("external_gate_id") != "XG-SBE-TARGET-OPERATIONS-HANDOFF" or handoff.get("status") != "accepted":
        raise RealmPromotionError("SBE_HANDOFF_NOT_ACCEPTED", subject, "accepted target-operations handoff required")
    if handoff.get("release_candidate_id") != release_candidate_id or handoff.get("deployment_generation_id") != deployment_generation_id:
        raise RealmPromotionError("SBE_HANDOFF_RELEASE_DRIFT", subject, "handoff release/generation binding differs")
    _text(handoff.get("receipt_id"), f"{subject}.receipt_id")
    bindings = _exact_keys(handoff.get("bindings"), _HANDOFF_BINDING_KEYS, f"{subject}.bindings")
    for key, value in bindings.items():
        _hash(value, f"{subject}.bindings.{key}")
    for key, expected in expected_bindings.items():
        if key not in _HANDOFF_BINDING_KEYS:
            raise RealmPromotionError("SBE_HANDOFF_EXPECTATION_INVALID", key, "unknown handoff binding")
        if bindings.get(key) != expected:
            raise RealmPromotionError("SBE_HANDOFF_BINDING_DRIFT", f"{subject}.bindings.{key}", "handoff differs from current authority")
    accepted = _time(handoff.get("accepted_at"), f"{subject}.accepted_at")
    expires = _time(handoff.get("expires_at"), f"{subject}.expires_at")
    if not accepted <= execution_start <= execution_end <= expires:
        raise RealmPromotionError("SBE_HANDOFF_TIME_SCOPE_INVALID", subject, "handoff must cover the complete operator action")


def _validate_scope(scope: Any, *, stage: str, action: Mapping[str, Any], subject: str) -> Mapping[str, Any]:
    value = _exact_keys(scope, _SCOPE_KEYS, subject)
    for key in (
        "root_identity_sha256",
        "argv_sha256",
        "forbidden_scope_sha256",
        "precondition_sha256",
        "rollback_sha256",
    ):
        _hash(value.get(key), f"{subject}.{key}")
    if value.get("release_candidate_id") != action.get("release_candidate_id"):
        raise RealmPromotionError("SCOPE_RELEASE_DRIFT", subject, "scope release candidate differs")
    if value.get("realm_config_fingerprint") != action.get("realm_config_fingerprint"):
        raise RealmPromotionError("SCOPE_REALM_DRIFT", subject, "scope realm fingerprint differs")
    if value.get("deployment_generation_id") != action.get("deployment_generation_id"):
        raise RealmPromotionError("SCOPE_GENERATION_DRIFT", subject, "scope deployment generation differs")
    ports = value.get("ports")
    if not isinstance(ports, list) or any(type(port) is not int or not 1 <= port <= 65535 for port in ports):
        raise RealmPromotionError("PORT_SCOPE_INVALID", f"{subject}.ports", "explicit valid port list required")
    if len(ports) != len(set(ports)):
        raise RealmPromotionError("PORT_SCOPE_INVALID", f"{subject}.ports", "ports must be unique")
    if stage == "default_off_install" and ports != [10012]:
        raise RealmPromotionError("INSTALL_PORT_SCOPE_INVALID", f"{subject}.ports", "default-off install is limited to 10012")
    if stage != "default_off_install" and ports:
        raise RealmPromotionError("PORT_SCOPE_INVALID", f"{subject}.ports", "this stage may not claim a listener port")
    expected_subjects = 3 if stage == "production_canary" else 0
    expected_workspaces = 1 if stage == "production_canary" else 0
    _hash_list(value.get("subject_identity_sha256"), expected_subjects, f"{subject}.subject_identity_sha256")
    _hash_list(value.get("workspace_identity_sha256"), expected_workspaces, f"{subject}.workspace_identity_sha256")
    roles = value.get("subject_roles")
    expected_roles = ["collaborator", "collaborator", "outsider_negative_control"] if stage == "production_canary" else []
    if roles != expected_roles:
        raise RealmPromotionError("SUBJECT_ROLE_SCOPE_INVALID", f"{subject}.subject_roles", f"must equal {expected_roles}")
    return value


def _validate_execution(value: Any, subject: str) -> tuple[datetime, datetime]:
    execution = _exact_keys(value, _EXECUTION_KEYS, subject)
    started = _time(execution.get("started_at"), f"{subject}.started_at")
    ended = _time(execution.get("ended_at"), f"{subject}.ended_at")
    if started > ended:
        raise RealmPromotionError("ACTION_TIME_INVALID", subject, "action end precedes start")
    for key, expected in (
        ("operator_action", True),
        ("automated_test_runner", False),
        ("pytest_invoked", False),
        ("cleanup_complete", True),
        ("unrelated_processes_targeted", False),
    ):
        _exact_bool(execution.get(key), expected, f"{subject}.{key}")
    _exact_int(execution.get("action_exit_code"), 0, f"{subject}.action_exit_code")
    return started, ended


def _validate_result(value: Any, subject: str) -> None:
    result = _exact_keys(value, _RESULT_KEYS, subject)
    if result.get("status") != "passed" or result.get("errors") != []:
        raise RealmPromotionError("ACTION_RESULT_FAILED", subject, "operator action must pass without errors")
    for key, expected in (("passed", 1), ("failed", 0), ("skipped", 0), ("xfailed", 0)):
        _exact_int(result.get(key), expected, f"{subject}.{key}")


def _validate_stage_evidence(stage: str, value: Any, subject: str) -> None:
    if stage == "offline_rehearsal":
        evidence = _exact_keys(value, _OFFLINE_EVIDENCE_KEYS, subject)
        for key in (
            "approved_offline_copy_only",
            "migration_passed",
            "new_root_restore_passed",
            "integrity_audit_passed",
            "redacted_application_smoke_passed",
            "compatibility_audit_passed",
            "compatible_code_rollback_passed",
            "disaster_recovery_rehearsal_passed",
        ):
            _exact_bool(evidence.get(key), True, f"{subject}.{key}")
        for key in ("production_mutation_performed", "raw_private_content_recorded"):
            _exact_bool(evidence.get(key), False, f"{subject}.{key}")
        return
    if stage == "default_off_install":
        evidence = _exact_keys(value, _INSTALL_EVIDENCE_KEYS, subject)
        _exact_int(evidence.get("install_port"), 10012, f"{subject}.install_port")
        _exact_int(evidence.get("user_enablement_count"), 0, f"{subject}.user_enablement_count")
        for key in (
            "frozen_artifact_installed",
            "default_off",
            "gateway_package_installed",
            "gateway_service_restarted",
            "gateway_health_passed",
            "configuration_derivation_passed",
            "before_after_hashes_present",
            "rollback_ready",
        ):
            _exact_bool(evidence.get(key), True, f"{subject}.{key}")
        _exact_bool(evidence.get("artifact_rebuilt"), False, f"{subject}.artifact_rebuilt")
        return
    evidence = _exact_keys(value, _CANARY_EVIDENCE_KEYS, subject)
    _exact_int(evidence.get("collaborator_count"), 2, f"{subject}.collaborator_count")
    _exact_int(evidence.get("outsider_negative_control_count"), 1, f"{subject}.outsider_negative_control_count")
    for key in (
        "outsider_denied",
        "telemetry_soak_passed",
        "abort_thresholds_configured",
        "aggregate_kill_switch_exercised",
        "capability_kill_switch_exercised",
        "compatible_rollback_passed",
        "recovery_passed",
    ):
        _exact_bool(evidence.get(key), True, f"{subject}.{key}")
    for key in (
        "abort_threshold_breach_count",
        "acknowledged_operation_loss_count",
        "post_revoke_delivery_count",
        "convergence_mismatch_count",
    ):
        _exact_int(evidence.get(key), 0, f"{subject}.{key}")
    for key in ("artifact_rebuilt", "broader_rollout_enabled"):
        _exact_bool(evidence.get(key), False, f"{subject}.{key}")


def _validate_action_receipt(
    action: Mapping[str, Any],
    *,
    stage: str,
    manifest: Mapping[str, Any],
    repository_root: Path,
    allowed_root: Path,
    expected_handoff_bindings: Mapping[str, str],
    subject: str,
) -> dict[str, Any]:
    _exact_keys(action, _ACTION_KEYS, subject)
    policy = STAGE_POLICY[stage]
    if action.get("schema_version") != 1 or action.get("contract") != OPERATOR_ACTION_RECEIPT_CONTRACT:
        raise RealmPromotionError("ACTION_CONTRACT_MISMATCH", subject, "operator action receipt contract mismatch")
    for key, expected in (
        ("stage", stage),
        ("packet_id", policy["packet_id"]),
        ("approval_id", policy["approval_id"]),
        ("realm_id", policy["realm_id"]),
    ):
        if action.get(key) != expected:
            raise RealmPromotionError("ACTION_STAGE_SCOPE_MISMATCH", f"{subject}.{key}", f"must equal {expected}")
    _text(action.get("receipt_id"), f"{subject}.receipt_id")
    _text(action.get("action_id"), f"{subject}.action_id")
    for key in (
        "release_candidate_id",
        "realm_config_fingerprint",
        "deployment_generation_id",
        "source_manifest_sha256",
        "validation_manifest_sha256",
        "schema_catalog_sha256",
        "artifact_manifest_sha256",
        "derivation_proof_sha256",
    ):
        if key == "deployment_generation_id":
            _text(action.get(key), f"{subject}.{key}")
        else:
            _hash(action.get(key), f"{subject}.{key}")
    for key in (
        "release_candidate_id",
        "deployment_generation_id",
        "source_manifest_sha256",
        "validation_manifest_sha256",
        "schema_catalog_sha256",
        "artifact_manifest_sha256",
        "derivation_proof_sha256",
    ):
        if action.get(key) != manifest.get(key):
            raise RealmPromotionError("ACTION_MANIFEST_BINDING_DRIFT", f"{subject}.{key}", "action differs from promotion manifest")

    scope = _validate_scope(action.get("scope"), stage=stage, action=action, subject=f"{subject}.scope")
    started, ended = _validate_execution(action.get("execution"), f"{subject}.execution")
    _validate_result(action.get("result"), f"{subject}.result")
    _validate_stage_evidence(stage, action.get("evidence"), f"{subject}.evidence")

    approval_path, approval_binding = _bound_path(
        action.get("approval_receipt"),
        subject=f"{subject}.approval_receipt",
        repository_root=repository_root,
        allowed_root=allowed_root,
    )
    approval = _load_json(approval_path, f"{subject}.approval_document")
    if approval.get("receipt_id") != approval_binding["receipt_id"]:
        raise RealmPromotionError("APPROVAL_RECEIPT_ID_MISMATCH", f"{subject}.approval_receipt", "approval receipt identity differs")
    _validate_approval(
        approval,
        stage=stage,
        scope=scope,
        execution_start=started,
        execution_end=ended,
        subject=f"{subject}.approval_document",
    )

    handoff_path, handoff_binding = _bound_path(
        action.get("sbe_handoff_receipt"),
        subject=f"{subject}.sbe_handoff_receipt",
        repository_root=repository_root,
        allowed_root=allowed_root,
    )
    handoff = _load_json(handoff_path, f"{subject}.sbe_handoff_document")
    if handoff.get("receipt_id") != handoff_binding["receipt_id"]:
        raise RealmPromotionError("SBE_HANDOFF_RECEIPT_ID_MISMATCH", f"{subject}.sbe_handoff_receipt", "handoff receipt identity differs")
    _validate_handoff(
        handoff,
        release_candidate_id=manifest["release_candidate_id"],
        deployment_generation_id=manifest["deployment_generation_id"],
        execution_start=started,
        execution_end=ended,
        expected_bindings=expected_handoff_bindings,
        subject=f"{subject}.sbe_handoff_document",
    )
    return {
        "receipt_id": action["receipt_id"],
        "action_id": action["action_id"],
        "started": started,
        "ended": ended,
        "approval_receipt_id": approval_binding["receipt_id"],
        "approval_receipt_sha256": approval_binding["sha256"],
        "handoff_receipt_sha256": handoff_binding["sha256"],
    }


def _validate_manifest_claims(value: Any, subject: str) -> None:
    claims = _exact_keys(value, _MANIFEST_CLAIM_KEYS, subject)
    for key in (
        "same_release_candidate",
        "same_deployment_generation",
        "no_rebuild_between_stages",
        "operator_actions_only",
    ):
        _exact_bool(claims.get(key), True, f"{subject}.{key}")
    for key in (
        "verifier_accessed_live_root",
        "verifier_invoked_pytest",
        "full_rollout_enabled",
        "product_complete",
    ):
        _exact_bool(claims.get(key), False, f"{subject}.{key}")


def verify_realm_promotion_manifest(
    manifest_path: Path,
    *,
    expected_release_candidate_id: str,
    expected_realm_id: str,
    expected_realm_config_fingerprint: str,
    expected_source_manifest_sha256: str,
    expected_validation_manifest_sha256: str,
    expected_schema_catalog_sha256: str,
    expected_sbe_handoff_bindings: Mapping[str, str] | None = None,
    release_patterns: Mapping[str, Sequence[str]] = DEFAULT_RELEASE_PATTERNS,
    release_schema_catalog_path: Path = DEFAULT_RELEASE_SCHEMA_CATALOG,
    repository_root: Path = REPOSITORY_ROOT,
    allowed_root: Path = DEFAULT_PROMOTION_ROOT,
) -> tuple[dict[str, Any], int]:
    """Verify immutable L9 promotion receipts without executing their actions."""

    manifest_path = Path(manifest_path)
    try:
        root = Path(repository_root).resolve(strict=True)
        evidence_root = Path(allowed_root).resolve(strict=True)
        resolved_manifest = manifest_path.resolve(strict=True)
        if not resolved_manifest.is_file() or not resolved_manifest.is_relative_to(evidence_root):
            raise RealmPromotionError(
                "PROMOTION_MANIFEST_PATH_INVALID",
                "manifest",
                "manifest must be a file inside the promotion evidence root",
            )
        manifest = _load_json(resolved_manifest, "manifest")
        _exact_keys(manifest, _MANIFEST_KEYS, "manifest")
        if manifest.get("schema_version") != 1 or manifest.get("contract") != PROMOTION_MANIFEST_CONTRACT:
            raise RealmPromotionError("PROMOTION_MANIFEST_CONTRACT_MISMATCH", "manifest", "promotion manifest contract mismatch")
        _text(manifest.get("manifest_id"), "manifest.manifest_id")
        for key in (
            "release_candidate_id",
            "deployment_generation_id",
            "source_manifest_sha256",
            "validation_manifest_sha256",
            "schema_catalog_sha256",
            "artifact_manifest_sha256",
            "derivation_proof_sha256",
        ):
            if key == "deployment_generation_id":
                _text(manifest.get(key), f"manifest.{key}")
            else:
                _hash(manifest.get(key), f"manifest.{key}")
        if manifest.get("release_candidate_id") != expected_release_candidate_id:
            raise RealmPromotionError("PROMOTION_RELEASE_BINDING_MISMATCH", "manifest.release_candidate_id", "CLI release candidate differs")
        for key, expected in (
            ("source_manifest_sha256", expected_source_manifest_sha256),
            ("validation_manifest_sha256", expected_validation_manifest_sha256),
            ("schema_catalog_sha256", expected_schema_catalog_sha256),
        ):
            _hash(expected, key)
            if manifest.get(key) != expected:
                raise RealmPromotionError("PROMOTION_AUTHORITY_BINDING_DRIFT", f"manifest.{key}", "promotion manifest differs from current authority")
        if expected_realm_id != "production_canary":
            raise RealmPromotionError("PROMOTION_REALM_INVALID", "realm_id", "L9 promotion receipt must close in production_canary realm")
        _hash(expected_realm_config_fingerprint, "realm_config_fingerprint")
        _validate_manifest_claims(manifest.get("claims"), "manifest.claims")

        release_path, release_binding = _bound_path(
            manifest.get("release_candidate_manifest"),
            subject="manifest.release_candidate_manifest",
            repository_root=root,
            allowed_root=evidence_root,
        )
        release_report, release_exit = verify_release_candidate_manifest(
            release_path,
            repository_root=root,
            patterns=release_patterns,
            schema_catalog_path=release_schema_catalog_path,
        )
        if release_exit != 0:
            raise RealmPromotionError(
                "RELEASE_CANDIDATE_INVALID",
                "manifest.release_candidate_manifest",
                "frozen release candidate did not pass fresh derivation verification",
            )
        if release_report.get("manifest_id") != release_binding["receipt_id"]:
            raise RealmPromotionError(
                "RELEASE_CANDIDATE_IDENTITY_MISMATCH",
                "manifest.release_candidate_manifest",
                "release manifest identity differs",
            )
        for key in (
            "release_candidate_id",
            "deployment_generation_id",
            "artifact_manifest_sha256",
            "derivation_proof_sha256",
        ):
            if manifest.get(key) != release_report.get(key):
                raise RealmPromotionError(
                    "RELEASE_CANDIDATE_BINDING_DRIFT",
                    f"manifest.{key}",
                    "promotion manifest differs from freshly verified release candidate",
                )

        bindings = manifest.get("receipt_bindings")
        if not isinstance(bindings, dict) or set(bindings) != set(PROMOTION_STAGES):
            raise RealmPromotionError(
                "PROMOTION_STAGE_SET_MISMATCH",
                "manifest.receipt_bindings",
                f"exact stages required: {list(PROMOTION_STAGES)}",
            )
        verified: dict[str, dict[str, Any]] = {}
        action_paths: set[Path] = set()
        for stage in PROMOTION_STAGES:
            action_path, binding = _bound_path(
                bindings[stage],
                subject=f"manifest.receipt_bindings.{stage}",
                repository_root=root,
                allowed_root=evidence_root,
            )
            if action_path in action_paths:
                raise RealmPromotionError("PROMOTION_ACTION_RECEIPT_REUSED", f"manifest.receipt_bindings.{stage}", "each stage requires a separate receipt")
            action_paths.add(action_path)
            action = _load_json(action_path, f"action.{stage}")
            if action.get("receipt_id") != binding["receipt_id"]:
                raise RealmPromotionError("ACTION_RECEIPT_ID_MISMATCH", f"manifest.receipt_bindings.{stage}", "action receipt identity differs")
            verified[stage] = _validate_action_receipt(
                action,
                stage=stage,
                manifest=manifest,
                repository_root=root,
                allowed_root=evidence_root,
                expected_handoff_bindings=expected_sbe_handoff_bindings or {},
                subject=f"action.{stage}",
            )

        for identity_key in ("receipt_id", "action_id", "approval_receipt_id"):
            identities = [verified[stage][identity_key] for stage in PROMOTION_STAGES]
            if len(set(identities)) != len(identities):
                raise RealmPromotionError(
                    "PROMOTION_IDENTITY_REUSED",
                    f"actions.{identity_key}",
                    "each operator stage requires a distinct identity",
                )

        if verified["default_off_install"]["ended"] > verified["production_canary"]["started"]:
            raise RealmPromotionError("PROMOTION_STAGE_ORDER_INVALID", "actions", "canary began before default-off install completed")
        if verified["offline_rehearsal"]["ended"] > verified["default_off_install"]["started"]:
            raise RealmPromotionError("PROMOTION_STAGE_ORDER_INVALID", "actions", "install began before rehearsal completed")

        canary_path = (root / Path(bindings["production_canary"]["path"])).resolve(strict=True)
        canary_action = _load_json(canary_path, "action.production_canary")
        if canary_action.get("realm_config_fingerprint") != expected_realm_config_fingerprint:
            raise RealmPromotionError("PROMOTION_REALM_BINDING_MISMATCH", "action.production_canary.realm_config_fingerprint", "CLI realm fingerprint differs")

        artifact_hashes = {
            str(resolved_manifest.relative_to(root)).replace("\\", "/"): sha256_file(resolved_manifest)
        }
        artifact_hashes[release_binding["path"]] = release_binding["sha256"]
        for stage in PROMOTION_STAGES:
            item = bindings[stage]
            artifact_hashes[item["path"]] = item["sha256"]
        report = {
            "report_schema_version": 1,
            "contract": PROMOTION_VERIFIER_CONTRACT,
            "status": "pass",
            "gate_eligible": True,
            "error_count": 0,
            "errors": [],
            "manifest_id": manifest["manifest_id"],
            "release_candidate_id": manifest["release_candidate_id"],
            "deployment_generation_id": manifest["deployment_generation_id"],
            "realm_id": expected_realm_id,
            "realm_config_fingerprint": expected_realm_config_fingerprint,
            "verified_stages": list(PROMOTION_STAGES),
            "verified_action_receipt_count": len(PROMOTION_STAGES),
            "artifact_hashes": artifact_hashes,
            "operator_actions_executed_by_verifier": False,
            "live_roots_accessed_by_verifier": False,
            "pytest_invoked_by_verifier": False,
            "full_rollout_enabled_by_verifier": False,
            "product_complete": False,
        }
        return report, 0
    except RealmPromotionError as exc:
        return {
            "report_schema_version": 1,
            "contract": PROMOTION_VERIFIER_CONTRACT,
            "status": "fail",
            "gate_eligible": False,
            "error_count": 1,
            "errors": [exc.as_report_error()],
            "verified_stages": [],
            "verified_action_receipt_count": 0,
            "operator_actions_executed_by_verifier": False,
            "live_roots_accessed_by_verifier": False,
            "pytest_invoked_by_verifier": False,
            "full_rollout_enabled_by_verifier": False,
            "product_complete": False,
        }, 1
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {
            "report_schema_version": 1,
            "contract": PROMOTION_VERIFIER_CONTRACT,
            "status": "fail",
            "gate_eligible": False,
            "error_count": 1,
            "errors": [
                {
                    "code": "PROMOTION_VERIFICATION_FAILED",
                    "subject": "manifest",
                    "message": type(exc).__name__,
                }
            ],
            "verified_stages": [],
            "verified_action_receipt_count": 0,
            "operator_actions_executed_by_verifier": False,
            "live_roots_accessed_by_verifier": False,
            "pytest_invoked_by_verifier": False,
            "full_rollout_enabled_by_verifier": False,
            "product_complete": False,
        }, 1


__all__ = [
    "DEFAULT_PROMOTION_ROOT",
    "OPERATOR_ACTION_RECEIPT_CONTRACT",
    "OPERATOR_APPROVAL_RECEIPT_CONTRACT",
    "PROMOTION_MANIFEST_CONTRACT",
    "PROMOTION_STAGES",
    "PROMOTION_VERIFIER_CONTRACT",
    "SBE_HANDOFF_RECEIPT_CONTRACT",
    "RealmPromotionError",
    "canonical_sha256",
    "sha256_file",
    "verify_realm_promotion_manifest",
]

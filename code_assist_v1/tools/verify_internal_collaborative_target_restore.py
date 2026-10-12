from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
SBE_DOCS_ROOT = PROJECT_ROOT / "docs" / "knowledge_spaces"
# Fail closed until a user-approved, target-specific successor policy is reviewed
# and its exact hash is deliberately pinned here for one O1B execution.
AUTHORIZED_EXECUTION_POLICY_SHA256: str | None = None
POLICY_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_o1b_p1_target_operations.v7.json"
)

POLICY_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_operations.o1b_p1.v1"
)
APPROVAL_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_operations_approval."
    "o1b_p1.v1"
)
HANDOFF_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_operations_handoff.o1b_p1.v1"
)
RECEIPT_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_backup_restore_receipt."
    "o1b_p1.v1"
)
EVIDENCE_INDEX_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_evidence_index.o1b_p1.v1"
)
EVIDENCE_ARTIFACT_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_evidence_artifact.o1b_p1.v1"
)
AUDIT_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_backup_restore.audit.v1"
)
PRE_EXECUTION_AUDIT_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_pre_execution.audit.v1"
)
APPROVAL_ID = "AP-OPS-TARGET"
ACTION_CONFIRMATION = "O1B-P1_TARGET_BACKUP_RESTORE"
HANDOFF_ID = "XG-SBE-TARGET-OPERATIONS-HANDOFF"
REALM_ID = "target_restore"
RPO_SECONDS = 15 * 60
RTO_SECONDS = 60 * 60
RETENTION = {
    "half_hour_count": 96,
    "daily_count": 30,
    "monthly_count": 12,
}
SCENARIO_IDS = [
    "OPS-001",
    "OPS-002",
    "OPS-003",
    "OPS-004",
    "OPS-005",
    "OPS-006",
]
CANARY_IDS = [
    "authentication",
    "personal_read",
    "part_read",
    "edit",
    "dual_publication_response_loss_retry",
    "search",
    "graph",
    "projection",
    "notion_permissions",
    "notion_realtime",
    "notion_comments",
    "notion_files",
    "notion_history",
]
TARGET_SCOPE = [
    "encrypted_external_generation_publish",
    "retention_key_and_destination_validation",
    "target_host_new_root_restore_and_measured_rpo_rto",
]
REQUIRED_EVIDENCE_ARTIFACT_IDS = [
    "approval_source",
    "handoff_acceptance",
    "nonce_consumption",
    "source_machine_identity",
    "backup_machine_identity",
    "target_machine_identity",
    "encryption",
    "acl",
    "retention",
    "generation_manifest",
    "blob_set",
    "notion_snapshot",
    "notion_blob_manifest",
    "sbe_snapshot",
    "sbe_backup_manifest",
    "target_integrity",
    "canary_results",
    "timing",
    "cleanup",
    "redaction_scan",
    "sbe_contract_audit",
]
REQUIRED_PRE_EXECUTION_ARTIFACTS = [
    "fresh_self_hashed_XG_SBE_TARGET_OPERATIONS_HANDOFF",
    "fresh_self_hashed_AP_OPS_TARGET_approval",
    "policy_bound_ed25519_trust_anchors",
    "cryptographically_verified_approval_and_handoff",
    "reviewed_release_candidate_and_target_identity",
    "opaque_source_backup_and_restore_root_locators",
    "reviewed_encryption_ACL_key_owner_and_key_ID",
    "authorized_external_reviewer_and_signing_key_ID",
]
REQUIRED_POST_EXECUTION_EVIDENCE = [
    "signed_target_evidence_index",
    "durable_single_use_nonce_consumption_record",
    "completed_external_generation_manifest",
    "separate_Notion_and_Second_Brain_high_water_boundary",
    "new_root_target_restore_integrity_and_canary_results",
    "measured_RPO_and_RTO",
    "post_drill_external_reviewer_attestation",
    "closed_isolated_services_and_ports",
]
FORBIDDEN_SHORTCUTS = [
    "same_source_volume_backup",
    "unencrypted_destination",
    "unreviewed_destination_ACL",
    "repository_stored_key_or_credential_material",
    "workstation_evidence_labeled_as_target_server_evidence",
    "Notion_receipt_closing_an_SBE_target_gate",
    "SBE_receipt_closing_a_Notion_target_gate",
    "production_restore_without_AP_PROD_RESTORE",
    "O1B_or_product_completion_without_fresh_external_receipts",
]
HANDOFF_PACKET_IDS = ["O1B-P1", "Q0-P1", "R0A-P1", "R0B-P1", "R1-P1"]
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_OPAQUE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
_WINDOWS_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\s\"']*")
_POSIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9._-])/(?:[^/\s\"']+/)+[^\s\"']*")
_EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_URL_RE = re.compile(r"\b(?:https?|file|smb|ftp)://", re.IGNORECASE)
_SECRET_VALUE_RE = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+|"
    r"\b(?:password|passwd|client_secret|access_token|refresh_token|api_key|"
    r"signing_secret|session_cookie)\s*[:=]|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.)",
    re.IGNORECASE,
)
_FORBIDDEN_SECRET_KEYS = {
    "password",
    "passwd",
    "secret",
    "private_key",
    "private_key_material",
    "access_token",
    "refresh_token",
    "client_secret",
    "credential",
    "credentials",
    "authorization",
    "cookie",
    "cookies",
    "csrf_token",
    "session_token",
    "signing_token",
    "api_key",
    "secret_key",
    "key_material",
    "signing_secret",
    "session_cookie",
    "authorization_header",
    "set_cookie",
    "token",
}


def _parse_json_bytes(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )


def _load_with_sha256(path: Path) -> tuple[Any, str]:
    raw = path.read_bytes()
    return _parse_json_bytes(raw), "sha256:" + hashlib.sha256(raw).hexdigest()


def _load(path: Path) -> Any:
    return _load_with_sha256(path)[0]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _without(value: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    omitted = set(keys)
    return {key: item for key, item in value.items() if key not in omitted}


def _repository_file(relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    target = (REPOSITORY_ROOT / relative).resolve(strict=False)
    try:
        target.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.tzinfo != timezone.utc:
        return None
    canonical = parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return parsed if canonical == value else None


def _aware_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _opaque_id(value: Any) -> bool:
    return isinstance(value, str) and _OPAQUE_ID_RE.fullmatch(value) is not None


def _sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _raw_public_key(value: Any) -> bytes | None:
    if not isinstance(value, str):
        return None
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return None
    return decoded if len(decoded) == 32 else None


def _signature_bytes(value: Any) -> bytes | None:
    if not isinstance(value, str):
        return None
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return None
    return decoded if len(decoded) == 64 else None


def _trust_anchor(
    policy: Mapping[str, Any], role: str
) -> Mapping[str, Any] | None:
    trust = policy.get("trust_anchor_policy")
    if not isinstance(trust, dict) or trust.get("status") != "bound":
        return None
    anchor = trust.get(role)
    return anchor if isinstance(anchor, dict) else None


def _verify_ed25519(
    *,
    signature: Any,
    message: bytes,
    anchor: Mapping[str, Any] | None,
    key_id: Any,
) -> bool:
    if anchor is None or set(anchor) != {
        "key_id",
        "public_key_base64",
        "public_key_sha256",
    }:
        return False
    public_key = _raw_public_key(anchor.get("public_key_base64"))
    signature_bytes = _signature_bytes(signature)
    if (
        public_key is None
        or signature_bytes is None
        or key_id != anchor.get("key_id")
        or not _opaque_id(key_id)
        or anchor.get("public_key_sha256")
        != "sha256:" + hashlib.sha256(public_key).hexdigest()
    ):
        return False
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature_bytes, message)
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def _attestation_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    return _without(value, "signature_base64", "private_key_material_in_receipt")


def _scan_redaction(value: Any, subject: str, errors: list[dict[str, str]]) -> None:
    def walk(item: Any, pointer: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                lowered = str(key).casefold()
                if lowered in _FORBIDDEN_SECRET_KEYS:
                    errors.append(
                        {
                            "code": "SECRET_FIELD_FORBIDDEN",
                            "subject": f"{pointer}.{key}",
                            "message": "secret-bearing fields are forbidden in repository evidence",
                        }
                    )
                walk(child, f"{pointer}.{key}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                walk(child, f"{pointer}[{index}]")
        elif isinstance(item, str):
            encoded_field = pointer.endswith((".public_key_base64", ".signature_base64"))
            if not encoded_field and (
                _WINDOWS_PATH_RE.search(item) or _POSIX_PATH_RE.search(item)
            ):
                errors.append(
                    {
                        "code": "RAW_PATH_FORBIDDEN",
                        "subject": pointer,
                        "message": "use an opaque locator hash instead of a raw target path",
                    }
                )
            if not encoded_field and _SECRET_VALUE_RE.search(item):
                errors.append(
                    {
                        "code": "SECRET_VALUE_FORBIDDEN",
                        "subject": pointer,
                        "message": "credential or private-key material is forbidden",
                        }
                    )
            if _EMAIL_RE.search(item):
                errors.append(
                    {
                        "code": "RAW_IDENTITY_FORBIDDEN",
                        "subject": pointer,
                        "message": "raw email or user identity is forbidden",
                    }
                )
            if not encoded_field and _URL_RE.search(item):
                errors.append(
                    {
                        "code": "RAW_ENDPOINT_FORBIDDEN",
                        "subject": pointer,
                        "message": "use an opaque endpoint locator instead of a URL",
                    }
                )

    walk(value, subject)


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _exact_keys(
    value: Any,
    expected: set[str],
    *,
    code: str,
    subject: str,
    errors: list[dict[str, str]],
) -> bool:
    if not isinstance(value, dict) or set(value) != expected:
        errors.append(
            _error(code, subject, "exact key denominator required")
        )
        return False
    return True


def _scope_sha256(resource_scope: Any) -> str | None:
    if not isinstance(resource_scope, dict):
        return None
    return _sha256_value(
        {
            "resource_scope": resource_scope,
            "target_scope": TARGET_SCOPE,
        }
    )


def _verify_binding(
    item: Any,
    subject: str,
    errors: list[dict[str, str]],
) -> None:
    target = _repository_file(item.get("path")) if isinstance(item, dict) else None
    if target is None or item.get("sha256") != _sha256_file(target):
        errors.append(
            _error(
                "BINDING_DRIFT",
                subject,
                "exact current repository path and SHA-256 are required",
            )
        )


def _verify_policy(value: Any, errors: list[dict[str, str]]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(_error("POLICY_SHAPE", "$", "object required"))
        return {}
    _exact_keys(
        value,
        {
            "schema_version",
            "contract",
            "packet_id",
            "recorded_at",
            "status",
            "current_bindings",
            "sbe_dynamic_baseline",
            "execution_contract",
            "required_pre_execution_artifacts",
            "required_post_execution_evidence",
            "forbidden_shortcuts",
            "trust_anchor_policy",
            "claims",
        },
        code="POLICY_KEY_DENOMINATOR",
        subject="$",
        errors=errors,
    )
    if not (
        value.get("schema_version") == 1
        and value.get("contract") == POLICY_CONTRACT
        and value.get("packet_id") == "O1B-P1"
        and value.get("status") == "ready_for_approval_and_target_handoff"
        and _aware_timestamp(value.get("recorded_at")) is not None
    ):
        errors.append(
            _error("POLICY_IDENTITY", "$", "exact O1B-P1 readiness identity required")
        )

    bindings = value.get("current_bindings")
    if not isinstance(bindings, dict) or set(bindings) != {
        "dynamic_instructions",
        "packet_catalog",
        "validation_ladder",
        "completion_matrix",
        "current_checkpoint",
        "v0b_checkpoint",
        "o1a_contract",
        "o1a_receipt",
        "company_target_handoff",
        "sbe_contract_audit_report",
        "sbe_contract_auditor",
    }:
        errors.append(
            _error(
                "POLICY_BINDING_DENOMINATOR",
                "$.current_bindings",
                "exact O1B prerequisite binding set required",
            )
        )
    elif isinstance(bindings, dict):
        for name, item in bindings.items():
            _verify_binding(item, f"$.current_bindings.{name}", errors)

    baseline = value.get("sbe_dynamic_baseline")
    if not (
        isinstance(baseline, dict)
        and set(baseline)
        == {
            "status",
            "error_count",
            "report_schema_version",
            "input_sha256",
            "goal_count",
            "scenario_count",
            "report_path",
            "report_sha256",
            "protected_sbe_changed",
        }
        and baseline.get("status") == "pass"
        and baseline.get("error_count") == 0
        and baseline.get("report_schema_version") == 2
        and baseline.get("goal_count") == 20
        and baseline.get("scenario_count") == 284
        and _sha(baseline.get("input_sha256"))
        and isinstance(baseline.get("report_path"), str)
        and _sha(baseline.get("report_sha256"))
        and baseline.get("protected_sbe_changed") is False
    ):
        errors.append(
            _error(
                "SBE_DYNAMIC_BASELINE",
                "$.sbe_dynamic_baseline",
                "fresh zero-error 20-goal/284-scenario SBE audit required",
            )
        )

    baseline_path = (
        _repository_file(baseline.get("report_path"))
        if isinstance(baseline, dict)
        else None
    )
    baseline_report_sha256: str | None = None
    try:
        if baseline_path is not None:
            baseline_report, baseline_report_sha256 = _load_with_sha256(baseline_path)
        else:
            baseline_report = None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        baseline_report = None
    report_summary = (
        baseline_report.get("summary") if isinstance(baseline_report, dict) else None
    )
    report_summary = report_summary if isinstance(report_summary, dict) else {}
    report_goal = report_summary.get("goal")
    report_goal = report_goal if isinstance(report_goal, dict) else {}
    report_scenarios = report_summary.get("scenarios")
    report_scenarios = report_scenarios if isinstance(report_scenarios, dict) else {}
    source_files = (
        baseline_report.get("source_files") if isinstance(baseline_report, dict) else None
    )
    source_files_valid = isinstance(source_files, list) and bool(source_files)
    seen_source_paths: set[str] = set()
    if source_files_valid:
        for item in source_files:
            relative = item.get("path") if isinstance(item, dict) else None
            if relative == "repository:AGENTS.md":
                target = (PROJECT_ROOT / "AGENTS.md").resolve(strict=False)
                allowed_root = PROJECT_ROOT.resolve()
            else:
                target = (
                    (SBE_DOCS_ROOT / relative).resolve(strict=False)
                    if isinstance(relative, str) and relative and "\\" not in relative
                    else None
                )
                allowed_root = SBE_DOCS_ROOT.resolve()
            try:
                inside = target is not None and target.is_relative_to(allowed_root)
            except (OSError, ValueError):
                inside = False
            try:
                source_bytes = target.read_bytes() if target is not None else None
            except OSError:
                source_bytes = None
            item_valid = (
                isinstance(item, dict)
                and set(item) == {"path", "sha256", "size_bytes"}
                and isinstance(relative, str)
                and relative not in seen_source_paths
                and not Path(relative).is_absolute()
                and inside
                and target is not None
                and source_bytes is not None
                and _nonnegative_int(item.get("size_bytes"))
                and item.get("size_bytes") == len(source_bytes)
                and item.get("sha256") == hashlib.sha256(source_bytes).hexdigest()
            )
            if not item_valid:
                source_files_valid = False
                break
            seen_source_paths.add(relative)
    if not (
        baseline_path is not None
        and baseline.get("report_sha256") == baseline_report_sha256
        and isinstance(bindings, dict)
        and bindings.get("sbe_contract_audit_report")
        == {
            "path": baseline.get("report_path"),
            "sha256": baseline.get("report_sha256"),
        }
        and isinstance(baseline_report, dict)
        and baseline_report.get("report_schema_version") == 2
        and baseline_report.get("status") == "pass"
        and baseline_report.get("error_count") == 0
        and baseline_report.get("errors") == []
        and source_files_valid
        and baseline_report.get("source_file_count") == len(source_files)
        and baseline_report.get("input_sha256")
        == str(baseline.get("input_sha256", "")).removeprefix("sha256:")
        and report_goal.get("count") == 20
        and report_scenarios.get("count") == 284
    ):
        errors.append(
            _error(
                "SBE_AUDIT_REPORT_INVALID",
                "$.sbe_dynamic_baseline.report_path",
                "fresh immutable report/hash with zero-error SBE audit is required",
            )
        )

    execution = value.get("execution_contract")
    if not (
        isinstance(execution, dict)
        and set(execution)
        == {
            "approval_id",
            "approval_contract",
            "action_time_confirmation",
            "handoff_id",
            "handoff_contract",
            "receipt_contract",
            "evidence_index_contract",
            "evidence_artifact_contract",
            "evidence_artifact_count",
            "sanitized_evidence_root_required",
            "nonce_ledger_compare_exchange_required",
            "restore_generation_binding_required",
            "rto_measurement_origin",
            "cross_receipt_nonce_reuse_rejected_at_registration",
            "realm_id",
            "scenario_ids",
            "target_scope",
            "locator_scheme",
            "rpo_seconds_max",
            "rto_seconds_max",
            "sbe_rpo_seconds_max",
            "sbe_rto_seconds_max",
            "retention",
            "same_volume_exception_allowed",
            "destination_off_host_required",
            "production_restore_authorized",
            "raw_paths_or_secrets_in_receipt_allowed",
        }
        and execution.get("approval_id") == APPROVAL_ID
        and execution.get("approval_contract") == APPROVAL_CONTRACT
        and execution.get("action_time_confirmation") == ACTION_CONFIRMATION
        and execution.get("handoff_id") == HANDOFF_ID
        and execution.get("handoff_contract") == HANDOFF_CONTRACT
        and execution.get("receipt_contract") == RECEIPT_CONTRACT
        and execution.get("evidence_index_contract") == EVIDENCE_INDEX_CONTRACT
        and execution.get("evidence_artifact_contract") == EVIDENCE_ARTIFACT_CONTRACT
        and execution.get("evidence_artifact_count")
        == len(REQUIRED_EVIDENCE_ARTIFACT_IDS)
        and execution.get("sanitized_evidence_root_required") is True
        and execution.get("nonce_ledger_compare_exchange_required") is True
        and execution.get("restore_generation_binding_required") is True
        and execution.get("rto_measurement_origin") == "assumed_incident_at"
        and execution.get("cross_receipt_nonce_reuse_rejected_at_registration") is True
        and execution.get("realm_id") == REALM_ID
        and execution.get("scenario_ids") == SCENARIO_IDS
        and execution.get("target_scope") == TARGET_SCOPE
        and execution.get("locator_scheme") == "hmac-sha256-v1"
        and execution.get("rpo_seconds_max") == RPO_SECONDS
        and execution.get("rto_seconds_max") == RTO_SECONDS
        and execution.get("sbe_rpo_seconds_max") == 3600
        and execution.get("sbe_rto_seconds_max") == 14400
        and execution.get("retention") == RETENTION
        and execution.get("same_volume_exception_allowed") is False
        and execution.get("destination_off_host_required") is True
        and execution.get("production_restore_authorized") is False
        and execution.get("raw_paths_or_secrets_in_receipt_allowed") is False
    ):
        errors.append(
            _error(
                "EXECUTION_CONTRACT_DRIFT",
                "$.execution_contract",
                "approval, handoff, recovery, and safety contract drift",
            )
        )

    trust = value.get("trust_anchor_policy")
    trust_keys = {
        "status",
        "signature_algorithm",
        "public_key_encoding",
        "minimum_distinct_key_count",
        "authorized_release_candidate_sha256",
        "approval",
        "handoff",
        "evidence_index",
        "receipt_reviewer",
    }
    trust_shape_valid = isinstance(trust, dict) and set(trust) == trust_keys
    trust_common_valid = (
        trust_shape_valid
        and trust.get("status") in {"pending", "bound"}
        and trust.get("signature_algorithm") == "ed25519"
        and trust.get("public_key_encoding") == "raw-base64"
        and trust.get("minimum_distinct_key_count") == 4
    )
    trust_roles = ("approval", "handoff", "evidence_index", "receipt_reviewer")
    if trust_common_valid and trust.get("status") == "pending":
        trust_valid = (
            trust.get("authorized_release_candidate_sha256") is None
            and all(trust.get(role) is None for role in trust_roles)
        )
    elif trust_common_valid:
        anchors = [trust.get(role) for role in trust_roles]
        trust_valid = _sha(trust.get("authorized_release_candidate_sha256")) and all(
            isinstance(anchor, dict)
            and set(anchor) == {"key_id", "public_key_base64", "public_key_sha256"}
            and _opaque_id(anchor.get("key_id"))
            and (raw := _raw_public_key(anchor.get("public_key_base64"))) is not None
            and anchor.get("public_key_sha256")
            == "sha256:" + hashlib.sha256(raw).hexdigest()
            for anchor in anchors
        )
        if trust_valid:
            trust_valid = (
                len({anchor["key_id"] for anchor in anchors}) == 4
                and len({anchor["public_key_sha256"] for anchor in anchors}) == 4
            )
    else:
        trust_valid = False
    if not trust_valid:
        errors.append(
            _error(
                "TRUST_ANCHOR_POLICY_INVALID",
                "$.trust_anchor_policy",
                "pending-empty or four distinct bound Ed25519 trust anchors required",
            )
        )

    if value.get("required_pre_execution_artifacts") != REQUIRED_PRE_EXECUTION_ARTIFACTS:
        errors.append(
            _error(
                "PRE_EXECUTION_ARTIFACTS_DRIFT",
                "$.required_pre_execution_artifacts",
                "exact pre-execution artifact denominator required",
            )
        )
    if value.get("required_post_execution_evidence") != REQUIRED_POST_EXECUTION_EVIDENCE:
        errors.append(
            _error(
                "POST_EXECUTION_EVIDENCE_DRIFT",
                "$.required_post_execution_evidence",
                "exact post-execution evidence denominator required",
            )
        )
    if value.get("forbidden_shortcuts") != FORBIDDEN_SHORTCUTS:
        errors.append(
            _error(
                "FORBIDDEN_SHORTCUTS_DRIFT",
                "$.forbidden_shortcuts",
                "exact forbidden-shortcut denominator required",
            )
        )

    claims = value.get("claims")
    if claims != {
        "O1B_P1_readiness_contract_passed": True,
        "AP_OPS_TARGET_granted": False,
        "target_operations_handoff_accepted": False,
        "target_backup_restore_executed": False,
        "O1B_P1_gate_passed": False,
        "production_restore_executed": False,
        "product_complete": False,
        "active_shared_goal_complete": False,
    }:
        errors.append(
            _error(
                "POLICY_FALSE_GREEN",
                "$.claims",
                "readiness must not claim approval, execution, or completion",
            )
        )
    _scan_redaction(value, "$", errors)
    return value


def _verify_approval(
    value: Any,
    *,
    policy: Mapping[str, Any],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(_error("APPROVAL_SHAPE", "$approval", "object required"))
        return {}
    _exact_keys(
        value,
        {
            "schema_version",
            "contract",
            "approval_id",
            "status",
            "approval_record_id",
            "approval_source_evidence_sha256",
            "approver_id",
            "issued_at",
            "expires_at",
            "authorized_execution_id",
            "single_use_nonce",
            "action_time_confirmation",
            "target_scope",
            "resource_scope",
            "scope_sha256",
            "release_candidate_sha256",
            "production_restore_authorized",
            "signature",
            "receipt_sha256",
        },
        code="APPROVAL_KEY_DENOMINATOR",
        subject="$approval",
        errors=errors,
    )
    issued = _timestamp(value.get("issued_at"))
    expires = _timestamp(value.get("expires_at"))
    if not (
        value.get("schema_version") == 1
        and value.get("contract") == APPROVAL_CONTRACT
        and value.get("approval_id") == APPROVAL_ID
        and value.get("status") == "approved"
        and _opaque_id(value.get("approval_record_id"))
        and _sha(value.get("approval_source_evidence_sha256"))
        and _opaque_id(value.get("approver_id"))
        and issued is not None
        and expires is not None
        and issued < expires
        and _uuid(value.get("authorized_execution_id"))
        and _opaque_id(value.get("single_use_nonce"))
        and value.get("action_time_confirmation") == ACTION_CONFIRMATION
        and value.get("target_scope") == TARGET_SCOPE
        and _sha(value.get("release_candidate_sha256"))
        and value.get("release_candidate_sha256")
        == (
            policy.get("trust_anchor_policy", {})
            if isinstance(policy.get("trust_anchor_policy"), dict)
            else {}
        ).get("authorized_release_candidate_sha256")
        and value.get("production_restore_authorized") is False
    ):
        errors.append(
            _error(
                "APPROVAL_IDENTITY",
                "$approval",
                "bounded, expiring, single-execution AP-OPS-TARGET artifact required",
            )
        )
    scope = value.get("resource_scope")
    if not (
        isinstance(scope, dict)
        and set(scope)
        == {
            "source_host_id",
            "backup_host_id",
            "target_host_id",
            "source_machine_identity_sha256",
            "backup_machine_identity_sha256",
            "target_machine_identity_sha256",
            "source_root_locator_hmac",
            "backup_root_locator_hmac",
            "restore_root_locator_hmac",
            "port_set_sha256",
            "subject_set_sha256",
            "workspace_set_sha256",
            "sanitized_argv_sha256",
            "precondition_sha256",
            "cleanup_rollback_sha256",
            "locator_key_id",
            "locator_domain_separator",
            "authorized_reviewer_id",
            "review_attestation_system_id",
            "review_trust_policy_id",
            "review_signature_key_id",
            "target_evidence_store_id",
            "approval_system_id",
            "handoff_system_id",
            "nonce_ledger_id",
            "sbe_audit_system_id",
            "encryption_control_id",
            "encryption_key_owner_id",
            "encryption_key_id",
        }
        and all(
            _opaque_id(scope.get(name))
            for name in ("source_host_id", "backup_host_id", "target_host_id")
        )
        and len(
            {
                scope.get("source_host_id"),
                scope.get("backup_host_id"),
                scope.get("target_host_id"),
            }
        )
        == 3
        and len(
            {
                scope.get("source_machine_identity_sha256"),
                scope.get("backup_machine_identity_sha256"),
                scope.get("target_machine_identity_sha256"),
            }
        )
        == 3
        and all(
            _sha(scope.get(name))
            for name in (
                "source_machine_identity_sha256",
                "backup_machine_identity_sha256",
                "target_machine_identity_sha256",
                "source_root_locator_hmac",
                "backup_root_locator_hmac",
                "restore_root_locator_hmac",
                "port_set_sha256",
                "subject_set_sha256",
                "workspace_set_sha256",
                "sanitized_argv_sha256",
                "precondition_sha256",
                "cleanup_rollback_sha256",
            )
        )
        and all(
            _opaque_id(scope.get(name))
            for name in (
                "locator_key_id",
                "locator_domain_separator",
                "authorized_reviewer_id",
                "review_attestation_system_id",
                "review_trust_policy_id",
                "review_signature_key_id",
                "target_evidence_store_id",
                "approval_system_id",
                "handoff_system_id",
                "nonce_ledger_id",
                "sbe_audit_system_id",
                "encryption_control_id",
                "encryption_key_owner_id",
                "encryption_key_id",
            )
        )
        and scope.get("review_signature_key_id")
        == (_trust_anchor(policy, "receipt_reviewer") or {}).get("key_id")
        and len(
            {
                scope.get("source_root_locator_hmac"),
                scope.get("backup_root_locator_hmac"),
                scope.get("restore_root_locator_hmac"),
            }
        )
        == 3
        and value.get("scope_sha256") == _scope_sha256(scope)
    ):
        errors.append(
            _error(
                "APPROVAL_SCOPE",
                "$approval.resource_scope",
                "exact host/root/port/subject/workspace/argv/precondition/cleanup scope required",
            )
        )
    signature = value.get("signature")
    approval_document = _without(value, "signature", "receipt_sha256")
    if not (
        isinstance(signature, dict)
        and set(signature) == {"key_id", "algorithm", "signature_base64"}
        and signature.get("algorithm") == "ed25519"
        and _verify_ed25519(
            signature=signature.get("signature_base64"),
            message=_canonical_bytes(approval_document),
            anchor=_trust_anchor(policy, "approval"),
            key_id=signature.get("key_id"),
        )
    ):
        errors.append(
            _error(
                "APPROVAL_SIGNATURE_INVALID",
                "$approval.signature",
                "policy-bound Ed25519 approval signature required",
            )
        )
    if value.get("receipt_sha256") != _sha256_value(
        _without(value, "receipt_sha256")
    ):
        errors.append(
            _error(
                "APPROVAL_SELF_HASH",
                "$approval.receipt_sha256",
                "canonical self-hash mismatch",
            )
        )
    _scan_redaction(value, "$approval", errors)
    return value


def _verify_handoff(
    value: Any,
    *,
    policy: Mapping[str, Any],
    approval: Mapping[str, Any],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(_error("HANDOFF_SHAPE", "$handoff", "object required"))
        return {}
    _exact_keys(
        value,
        {
            "schema_version",
            "contract",
            "handoff_id",
            "packet_ids",
            "status",
            "owner_role",
            "owner_id",
            "issuer_id",
            "acceptor_id",
            "issued_at",
            "accepted_at",
            "expires_at",
            "authorized_execution_id",
            "scope_sha256",
            "release_candidate_sha256",
            "sbe_dynamic_baseline",
            "release_candidate",
            "authority",
            "reviewer_attestation",
            "receipt_sha256",
        },
        code="HANDOFF_KEY_DENOMINATOR",
        subject="$handoff",
        errors=errors,
    )
    issued = _timestamp(value.get("issued_at"))
    accepted = _timestamp(value.get("accepted_at"))
    expires = _timestamp(value.get("expires_at"))
    approval_issued = _timestamp(approval.get("issued_at"))
    if not (
        value.get("schema_version") == 1
        and value.get("contract") == HANDOFF_CONTRACT
        and value.get("handoff_id") == HANDOFF_ID
        and value.get("packet_ids") == HANDOFF_PACKET_IDS
        and value.get("status") == "accepted"
        and value.get("owner_role") == "coordinated_target_authority"
        and _opaque_id(value.get("owner_id"))
        and _opaque_id(value.get("issuer_id"))
        and _opaque_id(value.get("acceptor_id"))
        and issued is not None
        and accepted is not None
        and expires is not None
        and issued <= accepted < expires
        and approval_issued is not None
        and approval_issued <= accepted
        and _uuid(value.get("authorized_execution_id"))
        and value.get("authorized_execution_id")
        == approval.get("authorized_execution_id")
        and value.get("scope_sha256") == approval.get("scope_sha256")
    ):
        errors.append(
            _error(
                "HANDOFF_IDENTITY",
                "$handoff",
                "fresh accepted coordinated target-authority handoff required",
            )
        )

    baseline = value.get("sbe_dynamic_baseline")
    expected_baseline = (
        policy.get("sbe_dynamic_baseline", {})
        if isinstance(policy.get("sbe_dynamic_baseline"), dict)
        else {}
    )
    if not (
        isinstance(baseline, dict)
        and set(baseline)
        == {
            "status",
            "error_count",
            "input_sha256",
            "goal_count",
            "scenario_count",
            "report_path",
            "report_sha256",
        }
        and baseline.get("status") == "pass"
        and baseline.get("error_count") == 0
        and baseline.get("input_sha256") == expected_baseline.get("input_sha256")
        and baseline.get("goal_count") == 20
        and baseline.get("scenario_count") == 284
        and baseline.get("report_path") == expected_baseline.get("report_path")
        and baseline.get("report_sha256") == expected_baseline.get("report_sha256")
    ):
        errors.append(
            _error(
                "HANDOFF_SBE_BASELINE",
                "$handoff.sbe_dynamic_baseline",
                "handoff must bind the policy's fresh SBE baseline",
            )
        )

    release = value.get("release_candidate")
    if not (
        isinstance(release, dict)
        and set(release)
        == {
            "build_sha256",
            "configuration_sha256",
            "catalog_sha256",
            "schema_sha256",
            "sqlite_version",
            "sqlite_source_id_sha256",
            "successor_flags_default_off",
            "one_durable_worker",
            "one_projector",
        }
        and _sha(release.get("build_sha256"))
        and _sha(release.get("configuration_sha256"))
        and _sha(release.get("catalog_sha256"))
        and _sha(release.get("schema_sha256"))
        and _opaque_id(release.get("sqlite_version"))
        and _sha(release.get("sqlite_source_id_sha256"))
        and release.get("successor_flags_default_off") is True
        and release.get("one_durable_worker") is True
        and release.get("one_projector") is True
        and value.get("release_candidate_sha256") == _sha256_value(release)
        and value.get("release_candidate_sha256")
        == approval.get("release_candidate_sha256")
    ):
        errors.append(
            _error(
                "HANDOFF_RELEASE_BINDING",
                "$handoff.release_candidate",
                "exact target release and supported worker topology required",
            )
        )

    authority = value.get("authority")
    if authority != {
        "target_operation_scope": TARGET_SCOPE,
        "notion_and_sbe_authorities_separate": True,
        "cross_database_transaction_claimed": False,
        "sbe_target_gate_closed_by_notion_receipt": False,
        "notion_target_gate_closed_by_sbe_receipt": False,
        "production_restore_authorized": False,
    }:
        errors.append(
            _error(
                "HANDOFF_AUTHORITY",
                "$handoff.authority",
                "cross-stream ownership and production-restore fence required",
            )
        )
    evidence_document = _without(value, "reviewer_attestation", "receipt_sha256")
    reviewer = value.get("reviewer_attestation")
    signed_at = _timestamp(reviewer.get("signed_at")) if isinstance(reviewer, dict) else None
    if not (
        isinstance(reviewer, dict)
        and set(reviewer)
        == {
            "reviewer_id",
            "attestation_system_id",
            "trust_policy_id",
            "signature_key_id",
            "signature_algorithm",
            "signature_record_id",
            "decision",
            "evidence_document_sha256",
            "signed_at",
            "signature_base64",
            "private_key_material_in_receipt",
        }
        and _opaque_id(reviewer.get("reviewer_id"))
        and _opaque_id(reviewer.get("attestation_system_id"))
        and _opaque_id(reviewer.get("trust_policy_id"))
        and _opaque_id(reviewer.get("signature_key_id"))
        and reviewer.get("signature_algorithm") == "ed25519"
        and reviewer.get("signature_key_id")
        == (_trust_anchor(policy, "handoff") or {}).get("key_id")
        and _opaque_id(reviewer.get("signature_record_id"))
        and reviewer.get("decision") == "accepted"
        and reviewer.get("evidence_document_sha256")
        == _sha256_value(evidence_document)
        and signed_at is not None
        and issued is not None
        and accepted is not None
        and expires is not None
        and issued <= signed_at == accepted <= expires
        and _verify_ed25519(
            signature=reviewer.get("signature_base64"),
            message=_canonical_bytes(_attestation_payload(reviewer)),
            anchor=_trust_anchor(policy, "handoff"),
            key_id=reviewer.get("signature_key_id"),
        )
        and reviewer.get("private_key_material_in_receipt") is False
    ):
        errors.append(
            _error(
                "HANDOFF_ATTESTATION",
                "$handoff.reviewer_attestation",
                "externally verified handoff attestation over the exact document required",
            )
        )
    if value.get("receipt_sha256") != _sha256_value(
        _without(value, "receipt_sha256")
    ):
        errors.append(
            _error(
                "HANDOFF_SELF_HASH",
                "$handoff.receipt_sha256",
                "canonical self-hash mismatch",
            )
        )
    _scan_redaction(value, "$handoff", errors)
    return value


def _expected_evidence_source_system(
    artifact_id: str,
    approval: Mapping[str, Any],
) -> Any:
    scope = approval.get("resource_scope")
    scope = scope if isinstance(scope, dict) else {}
    if artifact_id == "approval_source":
        return scope.get("approval_system_id")
    if artifact_id == "handoff_acceptance":
        return scope.get("handoff_system_id")
    if artifact_id == "nonce_consumption":
        return scope.get("nonce_ledger_id")
    if artifact_id == "source_machine_identity":
        return scope.get("source_host_id")
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
        return scope.get("backup_host_id")
    if artifact_id == "sbe_contract_audit":
        return scope.get("sbe_audit_system_id")
    return scope.get("target_host_id")


def _verify_evidence_index(
    value: Any,
    *,
    policy: Mapping[str, Any],
    approval: Mapping[str, Any],
    handoff: Mapping[str, Any],
    evidence_root: Path,
    verified_artifacts: dict[str, dict[str, Any]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(_error("EVIDENCE_INDEX_SHAPE", "$evidence_index", "object required"))
        return {}
    _exact_keys(
        value,
        {
            "schema_version",
            "contract",
            "execution_id",
            "generation_id",
            "issued_at",
            "target_evidence_store_id",
            "observed_resource_scope",
            "artifacts",
            "signature",
            "receipt_sha256",
        },
        code="EVIDENCE_INDEX_KEY_DENOMINATOR",
        subject="$evidence_index",
        errors=errors,
    )
    issued_at = _timestamp(value.get("issued_at"))
    if not (
        value.get("schema_version") == 1
        and value.get("contract") == EVIDENCE_INDEX_CONTRACT
        and _uuid(value.get("execution_id"))
        and value.get("execution_id") == approval.get("authorized_execution_id")
        and value.get("execution_id") == handoff.get("authorized_execution_id")
        and _uuid(value.get("generation_id"))
        and issued_at is not None
        and _opaque_id(value.get("target_evidence_store_id"))
        and value.get("target_evidence_store_id")
        == (
            approval.get("resource_scope", {}).get("target_evidence_store_id")
            if isinstance(approval.get("resource_scope"), dict)
            else None
        )
        and value.get("observed_resource_scope") == approval.get("resource_scope")
    ):
        errors.append(
            _error(
                "EVIDENCE_INDEX_IDENTITY",
                "$evidence_index",
                "signed execution/generation identity and exact observed scope required",
            )
        )
    artifacts = value.get("artifacts")
    artifacts_valid = isinstance(artifacts, list) and len(artifacts) == len(
        REQUIRED_EVIDENCE_ARTIFACT_IDS
    )
    evidence_root_resolved = evidence_root.resolve(strict=False)
    artifact_paths: set[str] = set()
    if artifacts_valid:
        observed_ids: list[str] = []
        for item in artifacts:
            captured_at = _timestamp(item.get("captured_at")) if isinstance(item, dict) else None
            relative_export = (
                item.get("sanitized_export_path") if isinstance(item, dict) else None
            )
            export_path = (
                (evidence_root_resolved / relative_export).resolve(strict=False)
                if isinstance(relative_export, str)
                and relative_export
                and "\\" not in relative_export
                and not Path(relative_export).is_absolute()
                else None
            )
            try:
                export_inside_root = (
                    export_path is not None
                    and export_path.is_relative_to(evidence_root_resolved)
                )
            except (OSError, ValueError):
                export_inside_root = False
            item_valid = (
                isinstance(item, dict)
                and set(item) == {
                    "artifact_id",
                    "evidence_record_id",
                    "sha256",
                    "captured_at",
                    "sanitized_export_path",
                }
                and isinstance(item.get("artifact_id"), str)
                and _opaque_id(item.get("evidence_record_id"))
                and _sha(item.get("sha256"))
                and captured_at is not None
                and issued_at is not None
                and captured_at <= issued_at
                and isinstance(relative_export, str)
                and relative_export not in artifact_paths
                and export_inside_root
                and export_path is not None
                and export_path.is_file()
            )
            if not item_valid:
                artifacts_valid = False
                break
            try:
                export_value, export_sha256 = _load_with_sha256(export_path)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                artifacts_valid = False
                break
            artifact_id = item.get("artifact_id")
            expected_export_keys = {
                "schema_version",
                "contract",
                "artifact_id",
                "execution_id",
                "generation_id",
                "evidence_record_id",
                "captured_at",
                "source_system_id",
                "observation_sha256",
                "raw_evidence_retained",
                "redacted_export",
                "private_data_in_export",
            }
            if artifact_id == "nonce_consumption":
                expected_export_keys.add("nonce_observation")
            export_valid = (
                isinstance(export_value, dict)
                and set(export_value) == expected_export_keys
                and item.get("sha256") == export_sha256
                and export_value.get("schema_version") == 1
                and export_value.get("contract") == EVIDENCE_ARTIFACT_CONTRACT
                and export_value.get("artifact_id") == artifact_id
                and export_value.get("execution_id") == value.get("execution_id")
                and export_value.get("generation_id") == value.get("generation_id")
                and export_value.get("evidence_record_id")
                == item.get("evidence_record_id")
                and export_value.get("captured_at") == item.get("captured_at")
                and export_value.get("source_system_id")
                == _expected_evidence_source_system(str(artifact_id), approval)
                and _sha(export_value.get("observation_sha256"))
                and export_value.get("raw_evidence_retained") is True
                and export_value.get("redacted_export") is True
                and export_value.get("private_data_in_export") is False
            )
            if artifact_id == "nonce_consumption":
                nonce_observation = (
                    export_value.get("nonce_observation")
                    if isinstance(export_value, dict)
                    else None
                )
                committed_at = (
                    _timestamp(nonce_observation.get("committed_at"))
                    if isinstance(nonce_observation, dict)
                    else None
                )
                export_valid = export_valid and (
                    isinstance(nonce_observation, dict)
                    and set(nonce_observation)
                    == {
                        "ledger_id",
                        "approval_record_id",
                        "single_use_nonce",
                        "authorized_execution_id",
                        "prior_consumption_count",
                        "final_consumption_count",
                        "atomic_compare_exchange",
                        "commit_result",
                        "transaction_record_id",
                        "committed_at",
                    }
                    and nonce_observation.get("ledger_id")
                    == export_value.get("source_system_id")
                    and nonce_observation.get("approval_record_id")
                    == approval.get("approval_record_id")
                    and nonce_observation.get("single_use_nonce")
                    == approval.get("single_use_nonce")
                    and nonce_observation.get("authorized_execution_id")
                    == approval.get("authorized_execution_id")
                    and nonce_observation.get("prior_consumption_count") == 0
                    and nonce_observation.get("final_consumption_count") == 1
                    and nonce_observation.get("atomic_compare_exchange") is True
                    and nonce_observation.get("commit_result") == "committed"
                    and _opaque_id(nonce_observation.get("transaction_record_id"))
                    and committed_at is not None
                    and committed_at == captured_at
                    and export_value.get("observation_sha256")
                    == _sha256_value(nonce_observation)
                )
            if not export_valid:
                artifacts_valid = False
                break
            verified_artifacts[str(item["artifact_id"])] = export_value
            observed_ids.append(item["artifact_id"])
            artifact_paths.add(relative_export)
        artifacts_valid = artifacts_valid and observed_ids == REQUIRED_EVIDENCE_ARTIFACT_IDS
    scope = approval.get("resource_scope")
    scope = scope if isinstance(scope, dict) else {}
    expected_machine_observations = {
        "source_machine_identity": scope.get("source_machine_identity_sha256"),
        "backup_machine_identity": scope.get("backup_machine_identity_sha256"),
        "target_machine_identity": scope.get("target_machine_identity_sha256"),
    }
    artifacts_valid = artifacts_valid and all(
        verified_artifacts.get(artifact_id, {}).get("observation_sha256")
        == expected_observation
        for artifact_id, expected_observation in expected_machine_observations.items()
    )
    if not artifacts_valid:
        errors.append(
            _error(
                "EVIDENCE_INDEX_ARTIFACTS",
                "$evidence_index.artifacts",
                "exact ordered signed target-artifact denominator required",
            )
        )
    signature = value.get("signature")
    evidence_document = _without(value, "signature", "receipt_sha256")
    if not (
        isinstance(signature, dict)
        and set(signature) == {"key_id", "algorithm", "signature_base64"}
        and signature.get("algorithm") == "ed25519"
        and _verify_ed25519(
            signature=signature.get("signature_base64"),
            message=_canonical_bytes(evidence_document),
            anchor=_trust_anchor(policy, "evidence_index"),
            key_id=signature.get("key_id"),
        )
    ):
        errors.append(
            _error(
                "EVIDENCE_INDEX_SIGNATURE_INVALID",
                "$evidence_index.signature",
                "policy-bound Ed25519 evidence-index signature required",
            )
        )
    if value.get("receipt_sha256") != _sha256_value(
        _without(value, "receipt_sha256")
    ):
        errors.append(
            _error(
                "EVIDENCE_INDEX_SELF_HASH",
                "$evidence_index.receipt_sha256",
                "canonical self-hash mismatch",
            )
        )
    _scan_redaction(value, "$evidence_index", errors)
    for artifact_id, artifact in verified_artifacts.items():
        _scan_redaction(
            artifact,
            f"$evidence_artifacts.{artifact_id}",
            errors,
        )
    return value


def _verify_receipt(
    value: Any,
    *,
    policy: Mapping[str, Any],
    policy_sha256: str | None,
    approval: Mapping[str, Any],
    approval_sha256: str | None,
    handoff: Mapping[str, Any],
    handoff_sha256: str | None,
    evidence_index: Mapping[str, Any],
    evidence_index_sha256: str | None,
    evidence_artifacts: Mapping[str, Mapping[str, Any]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(_error("RECEIPT_SHAPE", "$receipt", "object required"))
        return {}
    _exact_keys(
        value,
        {
            "schema_version",
            "contract",
            "packet_id",
            "scenario_ids",
            "realm_id",
            "result",
            "exit_code",
            "receipt_id",
            "execution_id",
            "policy_binding",
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
            "claims",
            "receipt_sha256",
        },
        code="RECEIPT_KEY_DENOMINATOR",
        subject="$receipt",
        errors=errors,
    )
    if not (
        value.get("schema_version") == 1
        and value.get("contract") == RECEIPT_CONTRACT
        and value.get("packet_id") == "O1B-P1"
        and value.get("scenario_ids") == SCENARIO_IDS
        and value.get("realm_id") == REALM_ID
        and value.get("result") == "passed"
        and value.get("exit_code") == 0
        and _sha(evidence_index_sha256)
        and value.get("receipt_id")
        == "O1B-P1-TARGET-" + str(evidence_index_sha256)[7:23].upper()
        and _uuid(value.get("execution_id"))
        and value.get("execution_id") == approval.get("authorized_execution_id")
        and value.get("execution_id") == handoff.get("authorized_execution_id")
    ):
        errors.append(
            _error(
                "RECEIPT_IDENTITY",
                "$receipt",
                "exact passed O1B-P1 target-restore receipt required",
            )
        )

    policy_binding = value.get("policy_binding")
    if not (
        isinstance(policy_binding, dict)
        and set(policy_binding) == {"contract", "sha256"}
        and policy_binding.get("contract") == POLICY_CONTRACT
        and policy_binding.get("sha256") == policy_sha256
    ):
        errors.append(
            _error(
                "POLICY_BINDING_INVALID",
                "$receipt.policy_binding",
                "exact current O1B policy hash required",
            )
        )

    timing_value = value.get("timing")
    execution_started = (
        _timestamp(timing_value.get("first_external_action_at"))
        if isinstance(timing_value, dict)
        else None
    )
    approval_issued = _timestamp(approval.get("issued_at"))
    approval_expires = _timestamp(approval.get("expires_at"))
    approval_binding = value.get("approval_binding")
    approval_binding_map = approval_binding if isinstance(approval_binding, dict) else {}
    if not (
        isinstance(approval_binding, dict)
        and set(approval_binding)
        == {
            "id",
            "contract",
            "approval_record_id",
            "sha256",
            "scope_sha256",
            "single_use_nonce",
            "authorized_execution_id",
            "consumed_before_execution",
            "consumed_at",
            "nonce_prior_consumption_count",
            "nonce_final_consumption_count",
            "nonce_atomic_compare_exchange",
            "nonce_consumption_evidence_sha256",
        }
        and approval_binding.get("id") == APPROVAL_ID
        and approval_binding.get("contract") == APPROVAL_CONTRACT
        and approval_binding.get("approval_record_id")
        == approval.get("approval_record_id")
        and approval_binding.get("sha256") == approval_sha256
        and approval_binding.get("scope_sha256") == approval.get("scope_sha256")
        and approval_binding.get("single_use_nonce")
        == approval.get("single_use_nonce")
        and approval_binding.get("authorized_execution_id")
        == approval.get("authorized_execution_id")
        and approval_binding.get("consumed_before_execution") is True
        and _timestamp(approval_binding.get("consumed_at")) is not None
        and approval_binding.get("nonce_prior_consumption_count") == 0
        and approval_binding.get("nonce_final_consumption_count") == 1
        and approval_binding.get("nonce_atomic_compare_exchange") is True
        and _sha(approval_binding.get("nonce_consumption_evidence_sha256"))
        and approval_issued is not None
        and approval_expires is not None
        and execution_started is not None
        and approval_issued <= execution_started <= approval_expires
        and _timestamp(approval_binding.get("consumed_at")) <= execution_started
    ):
        errors.append(
            _error(
                "APPROVAL_INVALID",
                "$receipt.approval_binding",
                "named action-time approval must precede target execution",
            )
        )

    handoff_binding = value.get("target_operations_handoff")
    issued_at = _timestamp(handoff.get("issued_at"))
    accepted_at = _timestamp(handoff.get("accepted_at"))
    handoff_expires = _timestamp(handoff.get("expires_at"))
    if not (
        isinstance(handoff_binding, dict)
        and set(handoff_binding)
        == {
            "id",
            "contract",
            "sha256",
            "owner_id",
            "scope_sha256",
            "release_candidate_sha256",
            "authorized_execution_id",
            "accepted_before_execution",
        }
        and handoff_binding.get("id") == HANDOFF_ID
        and handoff_binding.get("contract") == HANDOFF_CONTRACT
        and handoff_binding.get("sha256") == handoff_sha256
        and handoff_binding.get("owner_id") == handoff.get("owner_id")
        and handoff_binding.get("scope_sha256") == handoff.get("scope_sha256")
        and handoff_binding.get("release_candidate_sha256")
        == handoff.get("release_candidate_sha256")
        and handoff_binding.get("authorized_execution_id")
        == handoff.get("authorized_execution_id")
        and handoff_binding.get("accepted_before_execution") is True
        and issued_at is not None
        and accepted_at is not None
        and handoff_expires is not None
        and execution_started is not None
        and issued_at <= accepted_at <= execution_started <= handoff_expires
    ):
        errors.append(
            _error(
                "HANDOFF_BINDING_INVALID",
                "$receipt.target_operations_handoff",
                "exact pre-execution handoff path hash and owner required",
            )
        )

    evidence_index_binding = value.get("evidence_index_binding")
    if not (
        isinstance(evidence_index_binding, dict)
        and set(evidence_index_binding)
        == {
            "contract",
            "sha256",
            "execution_id",
            "generation_id",
            "target_evidence_store_id",
        }
        and evidence_index_binding.get("contract") == EVIDENCE_INDEX_CONTRACT
        and evidence_index_binding.get("sha256") == evidence_index_sha256
        and evidence_index_binding.get("execution_id") == evidence_index.get("execution_id")
        and evidence_index_binding.get("generation_id") == evidence_index.get("generation_id")
        and evidence_index_binding.get("target_evidence_store_id")
        == evidence_index.get("target_evidence_store_id")
        and value.get("observed_resource_scope")
        == evidence_index.get("observed_resource_scope")
        == approval.get("resource_scope")
    ):
        errors.append(
            _error(
                "EVIDENCE_INDEX_BINDING_INVALID",
                "$receipt.evidence_index_binding",
                "exact signed target evidence index and observed scope binding required",
            )
        )

    artifacts = evidence_index.get("artifacts")
    artifact_by_id = {
        item.get("artifact_id"): item
        for item in artifacts
        if isinstance(item, dict) and isinstance(item.get("artifact_id"), str)
    } if isinstance(artifacts, list) else {}
    artifact_observation_by_id = {
        artifact_id: artifact.get("observation_sha256")
        for artifact_id, artifact in evidence_artifacts.items()
    }
    nonce_artifact = artifact_by_id.get("nonce_consumption", {})
    nonce_export = evidence_artifacts.get("nonce_consumption", {})
    nonce_observation = (
        nonce_export.get("nonce_observation")
        if isinstance(nonce_export, dict)
        else None
    )
    if (
        not isinstance(nonce_artifact, dict)
        or approval_binding_map.get("nonce_consumption_evidence_sha256")
        != nonce_artifact.get("sha256")
        or not isinstance(nonce_observation, dict)
        or approval_binding_map.get("consumed_at")
        != nonce_observation.get("committed_at")
        or approval_binding_map.get("nonce_prior_consumption_count")
        != nonce_observation.get("prior_consumption_count")
        or approval_binding_map.get("nonce_final_consumption_count")
        != nonce_observation.get("final_consumption_count")
        or approval_binding_map.get("nonce_atomic_compare_exchange")
        != nonce_observation.get("atomic_compare_exchange")
    ):
        errors.append(
            _error(
                "NONCE_CONSUMPTION_EVIDENCE_INVALID",
                "$receipt.approval_binding.nonce_consumption_evidence_sha256",
                "signed durable nonce-consumption evidence is required",
            )
        )
    approval_source_artifact = artifact_by_id.get("approval_source")
    if (
        not isinstance(approval_source_artifact, dict)
        or approval_source_artifact.get("sha256")
        != approval.get("approval_source_evidence_sha256")
    ):
        errors.append(
            _error(
                "EVIDENCE_INDEX_ARTIFACT_BINDING_INVALID",
                "$evidence_index.artifacts.approval_source",
                "signed approval-source export hash differs from its approval binding",
            )
        )
    expected_observations = {
        "handoff_acceptance": handoff_sha256,
        "sbe_contract_audit": (
            policy.get("sbe_dynamic_baseline", {}).get("report_sha256")
            if isinstance(policy.get("sbe_dynamic_baseline"), dict)
            else None
        ),
    }
    for artifact_id, expected_hash in expected_observations.items():
        if artifact_observation_by_id.get(artifact_id) != expected_hash:
            errors.append(
                _error(
                    "EVIDENCE_INDEX_ARTIFACT_BINDING_INVALID",
                    f"$evidence_artifacts.{artifact_id}.observation_sha256",
                    "signed evidence observation differs from its bound source",
                )
            )

    baseline = value.get("sbe_dynamic_baseline")
    expected_baseline = (
        policy.get("sbe_dynamic_baseline", {})
        if isinstance(policy.get("sbe_dynamic_baseline"), dict)
        else {}
    )
    if not (
        isinstance(baseline, dict)
        and set(baseline)
        == {
            "status",
            "error_count",
            "input_sha256",
            "goal_count",
            "scenario_count",
            "report_path",
            "report_sha256",
        }
        and baseline.get("input_sha256") == expected_baseline.get("input_sha256")
        and baseline.get("status") == "pass"
        and baseline.get("error_count") == 0
        and baseline.get("goal_count") == 20
        and baseline.get("scenario_count") == 284
        and baseline.get("report_path") == expected_baseline.get("report_path")
        and baseline.get("report_sha256") == expected_baseline.get("report_sha256")
    ):
        errors.append(
            _error(
                "RECEIPT_SBE_BASELINE",
                "$receipt.sbe_dynamic_baseline",
                "receipt must bind the fresh handoff SBE baseline",
            )
        )

    backup_value = value.get("external_backup")
    backup = backup_value if isinstance(backup_value, dict) else {}
    approval_scope = approval.get("resource_scope")
    approval_scope = approval_scope if isinstance(approval_scope, dict) else {}
    if not (
        isinstance(backup_value, dict)
        and set(backup)
        == {
            "generation_id",
            "source_host_id",
            "destination_host_id",
            "destination_locator_hmac",
            "destination_off_host",
            "same_volume_exception_used",
            "test_mode_used",
            "failpoint_used",
            "encryption_at_rest_verified",
            "encryption_evidence_sha256",
            "acl_reviewed",
            "acl_evidence_sha256",
            "encryption_control_id",
            "key_owner_id",
            "key_id",
            "key_material_in_receipt",
            "retention",
            "retention_evidence_sha256",
            "generation_manifest_sha256",
            "blob_set_sha256",
            "blob_count",
            "blob_bytes",
            "completed_generation_verified",
            "gc_hold_closed",
        }
        and _uuid(backup.get("generation_id"))
        and backup.get("generation_id") == evidence_index.get("generation_id")
        and _opaque_id(backup.get("source_host_id"))
        and _opaque_id(backup.get("destination_host_id"))
        and backup.get("source_host_id") != backup.get("destination_host_id")
        and backup.get("source_host_id") == approval_scope.get("source_host_id")
        and backup.get("destination_host_id") == approval_scope.get("backup_host_id")
        and _sha(backup.get("destination_locator_hmac"))
        and backup.get("destination_locator_hmac")
        == approval_scope.get("backup_root_locator_hmac")
        and backup.get("destination_off_host") is True
        and backup.get("same_volume_exception_used") is False
        and backup.get("test_mode_used") is False
        and backup.get("failpoint_used") is False
        and backup.get("encryption_at_rest_verified") is True
        and _sha(backup.get("encryption_evidence_sha256"))
        and backup.get("acl_reviewed") is True
        and _sha(backup.get("acl_evidence_sha256"))
        and _opaque_id(backup.get("encryption_control_id"))
        and backup.get("encryption_control_id")
        == approval_scope.get("encryption_control_id")
        and _opaque_id(backup.get("key_owner_id"))
        and backup.get("key_owner_id") == approval_scope.get("encryption_key_owner_id")
        and _opaque_id(backup.get("key_id"))
        and backup.get("key_id") == approval_scope.get("encryption_key_id")
        and backup.get("key_material_in_receipt") is False
        and backup.get("retention") == RETENTION
        and _sha(backup.get("retention_evidence_sha256"))
        and _sha(backup.get("generation_manifest_sha256"))
        and _sha(backup.get("blob_set_sha256"))
        and _nonnegative_int(backup.get("blob_count"))
        and _nonnegative_int(backup.get("blob_bytes"))
        and backup.get("completed_generation_verified") is True
        and backup.get("gc_hold_closed") is True
        and backup.get("encryption_evidence_sha256")
        == artifact_by_id.get("encryption", {}).get("sha256")
        and backup.get("acl_evidence_sha256")
        == artifact_by_id.get("acl", {}).get("sha256")
        and backup.get("retention_evidence_sha256")
        == artifact_by_id.get("retention", {}).get("sha256")
        and backup.get("generation_manifest_sha256")
        == artifact_observation_by_id.get("generation_manifest")
        and backup.get("blob_set_sha256")
        == artifact_observation_by_id.get("blob_set")
    ):
        errors.append(
            _error(
                "EXTERNAL_BACKUP_INVALID",
                "$receipt.external_backup",
                "distinct-host encrypted ACL-reviewed generation with evidence and exact retention required",
            )
        )

    boundary = value.get("generation_boundary")
    notion = boundary.get("notion") if isinstance(boundary, dict) else None
    sbe = boundary.get("second_brain") if isinstance(boundary, dict) else None
    identity = boundary.get("identity") if isinstance(boundary, dict) else None
    if not (
        isinstance(boundary, dict)
        and set(boundary)
        == {
            "authority_order",
            "separate_canonical_authorities",
            "cross_database_transaction_claimed",
            "notion",
            "second_brain",
            "identity",
        }
        and boundary.get("authority_order") == ["notion", "second_brain"]
        and boundary.get("separate_canonical_authorities") is True
        and boundary.get("cross_database_transaction_claimed") is False
        and isinstance(notion, dict)
        and set(notion)
        == {
            "snapshot_db_sha256",
            "blob_manifest_sha256",
            "audit_high_water",
            "event_high_water",
            "crdt_checkpoint_high_water",
            "blob_count",
            "missing_blob_count",
            "event_order_mismatch_count",
            "publication_link_mismatch_count",
            "transient_upload_count",
        }
        and _sha(notion.get("snapshot_db_sha256"))
        and _sha(notion.get("blob_manifest_sha256"))
        and _positive_int(notion.get("audit_high_water"))
        and _positive_int(notion.get("event_high_water"))
        and _positive_int(notion.get("crdt_checkpoint_high_water"))
        and _nonnegative_int(notion.get("blob_count"))
        and notion.get("missing_blob_count") == 0
        and notion.get("event_order_mismatch_count") == 0
        and notion.get("publication_link_mismatch_count") == 0
        and notion.get("transient_upload_count") == 0
        and notion.get("snapshot_db_sha256")
        == artifact_observation_by_id.get("notion_snapshot")
        and notion.get("blob_manifest_sha256")
        == artifact_observation_by_id.get("notion_blob_manifest")
        and isinstance(sbe, dict)
        and set(sbe)
        == {
            "snapshot_db_sha256",
            "backup_manifest_sha256",
            "audit_high_water",
            "publication_high_water",
            "blob_count",
            "missing_blob_count",
            "publication_link_mismatch_count",
            "active_backup_hold_count",
        }
        and _sha(sbe.get("snapshot_db_sha256"))
        and _sha(sbe.get("backup_manifest_sha256"))
        and _positive_int(sbe.get("audit_high_water"))
        and _positive_int(sbe.get("publication_high_water"))
        and _nonnegative_int(sbe.get("blob_count"))
        and sbe.get("missing_blob_count") == 0
        and sbe.get("publication_link_mismatch_count") == 0
        and sbe.get("active_backup_hold_count") == 0
        and sbe.get("snapshot_db_sha256")
        == artifact_observation_by_id.get("sbe_snapshot")
        and sbe.get("backup_manifest_sha256")
        == artifact_observation_by_id.get("sbe_backup_manifest")
        and isinstance(identity, dict)
        and set(identity)
        == {"database_included", "fresh_session_required", "signing_secret_rotated"}
        and identity.get("database_included") is False
        and identity.get("fresh_session_required") is True
        and identity.get("signing_secret_rotated") is True
    ):
        errors.append(
            _error(
                "GENERATION_BOUNDARY_INVALID",
                "$receipt.generation_boundary",
                "separate Notion/SBE high-water generation boundary required",
            )
        )

    restore = value.get("target_restore")
    release = restore.get("release_candidate") if isinstance(restore, dict) else None
    integrity = restore.get("integrity") if isinstance(restore, dict) else None
    canaries = restore.get("canaries") if isinstance(restore, dict) else None
    if not (
        isinstance(restore, dict)
        and set(restore)
        == {
            "source_host_id",
            "target_host_id",
            "generation_id",
            "generation_manifest_sha256",
            "notion_snapshot_db_sha256",
            "sbe_snapshot_db_sha256",
            "restored_blob_set_sha256",
            "restored_blob_count",
            "source_root_locator_hmac",
            "target_root_locator_hmac",
            "port_set_sha256",
            "windows_target",
            "new_root",
            "empty_before_restore",
            "isolated_services_used",
            "source_and_restore_roots_disjoint",
            "test_mode_used",
            "failpoint_used",
            "release_candidate",
            "integrity",
            "integrity_evidence_sha256",
            "canaries",
            "canary_evidence_sha256",
        }
        and _opaque_id(restore.get("source_host_id"))
        and _opaque_id(restore.get("target_host_id"))
        and _uuid(restore.get("generation_id"))
        and _sha(restore.get("generation_manifest_sha256"))
        and _sha(restore.get("notion_snapshot_db_sha256"))
        and _sha(restore.get("sbe_snapshot_db_sha256"))
        and _sha(restore.get("restored_blob_set_sha256"))
        and _nonnegative_int(restore.get("restored_blob_count"))
        and _sha(restore.get("source_root_locator_hmac"))
        and _sha(restore.get("target_root_locator_hmac"))
        and _sha(restore.get("port_set_sha256"))
        and restore.get("windows_target") is True
        and restore.get("new_root") is True
        and restore.get("empty_before_restore") is True
        and restore.get("isolated_services_used") is True
        and restore.get("source_and_restore_roots_disjoint") is True
        and restore.get("test_mode_used") is False
        and restore.get("failpoint_used") is False
        and restore.get("source_host_id")
        == approval_scope.get("source_host_id")
        and restore.get("target_host_id")
        == approval_scope.get("target_host_id")
        and backup.get("source_host_id") == restore.get("source_host_id")
        and backup.get("destination_host_id")
        == approval_scope.get("backup_host_id")
        and restore.get("generation_id") == backup.get("generation_id")
        and restore.get("generation_id") == evidence_index.get("generation_id")
        and restore.get("generation_manifest_sha256")
        == backup.get("generation_manifest_sha256")
        and restore.get("notion_snapshot_db_sha256")
        == (notion.get("snapshot_db_sha256") if isinstance(notion, dict) else None)
        and restore.get("sbe_snapshot_db_sha256")
        == (sbe.get("snapshot_db_sha256") if isinstance(sbe, dict) else None)
        and restore.get("restored_blob_set_sha256") == backup.get("blob_set_sha256")
        and restore.get("restored_blob_count") == backup.get("blob_count")
        and isinstance(notion, dict)
        and isinstance(sbe, dict)
        and _nonnegative_int(notion.get("blob_count"))
        and _nonnegative_int(sbe.get("blob_count"))
        and backup.get("blob_count")
        == notion.get("blob_count", -1) + sbe.get("blob_count", -1)
        and restore.get("source_root_locator_hmac")
        == approval_scope.get("source_root_locator_hmac")
        and backup.get("destination_locator_hmac")
        == approval_scope.get("backup_root_locator_hmac")
        and restore.get("target_root_locator_hmac")
        == approval_scope.get("restore_root_locator_hmac")
        and restore.get("port_set_sha256")
        == approval_scope.get("port_set_sha256")
        and isinstance(release, dict)
        and release == handoff.get("release_candidate")
        and isinstance(integrity, dict)
        and restore.get("integrity_evidence_sha256")
        == artifact_by_id.get("target_integrity", {}).get("sha256")
        and set(integrity)
        == {
            "quick_check",
            "foreign_key_violation_count",
            "checksum_mismatch_count",
            "canonical_mismatch_count",
            "projection_mismatch_count",
            "lost_event_count",
            "duplicate_effect_count",
        }
        and integrity.get("quick_check") == "ok"
        and integrity.get("foreign_key_violation_count") == 0
        and integrity.get("checksum_mismatch_count") == 0
        and integrity.get("canonical_mismatch_count") == 0
        and integrity.get("projection_mismatch_count") == 0
        and integrity.get("lost_event_count") == 0
        and integrity.get("duplicate_effect_count") == 0
        and isinstance(canaries, dict)
        and canaries == {name: True for name in CANARY_IDS}
        and restore.get("canary_evidence_sha256")
        == artifact_by_id.get("canary_results", {}).get("sha256")
    ):
        errors.append(
            _error(
                "TARGET_RESTORE_INVALID",
                "$receipt.target_restore",
                "new-root target restore, exact release, integrity, and canaries required",
            )
        )

    timing = value.get("timing")
    timing_map = timing if isinstance(timing, dict) else {}
    incident = _timestamp(timing_map.get("assumed_incident_at"))
    first_action = _timestamp(timing_map.get("first_external_action_at"))
    notion_high_water = _timestamp(timing_map.get("notion_audit_high_water_at"))
    sbe_high_water = _timestamp(timing_map.get("sbe_audit_high_water_at"))
    oldest_high_water = _timestamp(timing_map.get("oldest_canonical_high_water_at"))
    manifest_created = _timestamp(timing_map.get("backup_manifest_created_at"))
    publish_completed = _timestamp(timing_map.get("external_publish_completed_at"))
    started = _timestamp(timing_map.get("drill_started_at"))
    smoke_ready = _timestamp(timing_map.get("smoke_ready_at"))
    completed = _timestamp(timing_map.get("drill_completed_at"))
    rpo_ms = timing_map.get("measured_rpo_milliseconds")
    rto_ms = timing_map.get("measured_rto_milliseconds")
    elapsed_ms = timing_map.get("operation_elapsed_milliseconds")
    timing_valid = (
        isinstance(timing, dict)
        and set(timing)
        == {
            "first_external_action_at",
            "notion_audit_high_water_at",
            "sbe_audit_high_water_at",
            "oldest_canonical_high_water_at",
            "backup_manifest_created_at",
            "external_publish_completed_at",
            "assumed_incident_at",
            "drill_started_at",
            "smoke_ready_at",
            "drill_completed_at",
            "measured_rpo_milliseconds",
            "measured_rto_milliseconds",
            "operation_elapsed_milliseconds",
            "rpo_source",
            "rto_source",
            "rto_end_condition",
            "o1b_rpo_seconds_max",
            "o1b_rto_seconds_max",
            "sbe_rpo_seconds_max",
            "sbe_rto_seconds_max",
            "o1b_passed",
            "sbe_passed",
            "timing_evidence_sha256",
        }
        and incident is not None
        and first_action is not None
        and notion_high_water is not None
        and sbe_high_water is not None
        and oldest_high_water is not None
        and oldest_high_water == min(notion_high_water, sbe_high_water)
        and manifest_created is not None
        and publish_completed is not None
        and started is not None
        and smoke_ready is not None
        and completed is not None
        and first_action <= notion_high_water <= manifest_created
        and first_action <= sbe_high_water <= manifest_created
        and manifest_created <= publish_completed <= incident <= started <= smoke_ready <= completed
        and _nonnegative_int(rpo_ms)
        and _nonnegative_int(rto_ms)
        and _nonnegative_int(elapsed_ms)
        and rpo_ms == int((incident - oldest_high_water).total_seconds() * 1000)
        and rto_ms == int((smoke_ready - incident).total_seconds() * 1000)
        and elapsed_ms == int((completed - first_action).total_seconds() * 1000)
        and rpo_ms <= RPO_SECONDS * 1000
        and rto_ms <= RTO_SECONDS * 1000
        and timing_map.get("rpo_source") == "oldest_canonical_authority_high_water_timestamp"
        and timing_map.get("rto_source")
        == "assumed_incident_to_all_required_canaries_ready"
        and timing_map.get("rto_end_condition") == "all_required_canaries_ready"
        and timing_map.get("o1b_rpo_seconds_max") == RPO_SECONDS
        and timing_map.get("o1b_rto_seconds_max") == RTO_SECONDS
        and timing_map.get("sbe_rpo_seconds_max") == 3600
        and timing_map.get("sbe_rto_seconds_max") == 14400
        and timing_map.get("o1b_passed") is True
        and timing_map.get("sbe_passed") is True
        and timing_map.get("timing_evidence_sha256")
        == artifact_by_id.get("timing", {}).get("sha256")
    )
    if not timing_valid:
        errors.append(
            _error(
                "RECOVERY_TIMING_INVALID",
                "$receipt.timing",
                "audit-high-water RPO and smoke-ready RTO must be consistent and pass both strict O1B and SBE limits",
            )
        )

    cleanup = value.get("cleanup")
    if cleanup != {
        "isolated_services_stopped": True,
        "isolated_ports_closed": True,
        "production_user_traffic_changed": False,
        "production_restore_executed": False,
        "same_volume_workaround_used": False,
        "unrelated_processes_targeted": False,
        "external_completed_generation_retained": True,
        "approval_single_use_nonce_consumed": True,
        "target_evidence_store_sealed": True,
        "cleanup_evidence_sha256": artifact_by_id.get("cleanup", {}).get("sha256"),
    }:
        errors.append(
            _error(
                "CLEANUP_INVALID",
                "$receipt.cleanup",
                "target drill cleanup and production-restore fence required",
            )
        )

    redaction = value.get("redaction")
    if redaction != {
        "raw_path_count": 0,
        "credential_count": 0,
        "secret_material_count": 0,
        "private_content_body_count": 0,
        "raw_identity_count": 0,
        "raw_endpoint_count": 0,
        "authorization_header_count": 0,
        "cookie_count": 0,
        "scan_evidence_sha256": artifact_by_id.get("redaction_scan", {}).get("sha256"),
    }:
        errors.append(
            _error(
                "REDACTION_INVALID",
                "$receipt.redaction",
                "zero raw path, credential, secret, and content-body disclosure required",
            )
        )

    evidence_document = _without(value, "reviewer_attestation", "receipt_sha256")
    evidence_sha = _sha256_value(evidence_document)
    reviewer = value.get("reviewer_attestation")
    completed_at = completed
    evidence_index_issued_at = _timestamp(evidence_index.get("issued_at"))
    signed_at = _timestamp(reviewer.get("signed_at")) if isinstance(reviewer, dict) else None
    if not (
        isinstance(reviewer, dict)
        and set(reviewer)
        == {
            "reviewer_id",
            "attestation_system_id",
            "trust_policy_id",
            "signature_key_id",
            "signature_algorithm",
            "signature_record_id",
            "decision",
            "evidence_document_sha256",
            "signed_at",
            "signature_base64",
            "private_key_material_in_receipt",
        }
        and _opaque_id(reviewer.get("reviewer_id"))
        and _opaque_id(reviewer.get("attestation_system_id"))
        and _opaque_id(reviewer.get("trust_policy_id"))
        and _opaque_id(reviewer.get("signature_key_id"))
        and reviewer.get("signature_algorithm") == "ed25519"
        and reviewer.get("reviewer_id") == approval_scope.get("authorized_reviewer_id")
        and reviewer.get("attestation_system_id")
        == approval_scope.get("review_attestation_system_id")
        and reviewer.get("trust_policy_id") == approval_scope.get("review_trust_policy_id")
        and reviewer.get("signature_key_id") == approval_scope.get("review_signature_key_id")
        and _opaque_id(reviewer.get("signature_record_id"))
        and reviewer.get("decision") == "approved"
        and reviewer.get("evidence_document_sha256") == evidence_sha
        and signed_at is not None
        and completed_at is not None
        and approval_expires is not None
        and handoff_expires is not None
        and completed_at <= approval_expires
        and completed_at <= handoff_expires
        and evidence_index_issued_at is not None
        and completed_at <= evidence_index_issued_at <= signed_at
        and _verify_ed25519(
            signature=reviewer.get("signature_base64"),
            message=_canonical_bytes(_attestation_payload(reviewer)),
            anchor=_trust_anchor(policy, "receipt_reviewer"),
            key_id=reviewer.get("signature_key_id"),
        )
        and reviewer.get("private_key_material_in_receipt") is False
    ):
        errors.append(
            _error(
                "REVIEWER_ATTESTATION_INVALID",
                "$receipt.reviewer_attestation",
                "post-drill external reviewer attestation over the exact evidence document required",
            )
        )

    if value.get("claims") != {
        "O1B_P1_gate_passed": True,
        "external_encrypted_backup_verified": True,
        "target_host_new_root_restore_verified": True,
        "target_operations_handoff_consumed": True,
        "production_restore_executed": False,
        "production_user_traffic_changed": False,
        "product_complete": False,
        "active_shared_goal_complete": False,
    }:
        errors.append(
            _error(
                "RECEIPT_FALSE_GREEN",
                "$receipt.claims",
                "O1B receipt claims must remain bounded and product-incomplete",
            )
        )
    if value.get("receipt_sha256") != _sha256_value(
        _without(value, "receipt_sha256")
    ):
        errors.append(
            _error(
                "RECEIPT_SELF_HASH",
                "$receipt.receipt_sha256",
                "canonical self-hash mismatch",
            )
        )
    _scan_redaction(value, "$receipt", errors)
    return value


def verify(
    policy_path: Path = POLICY_PATH,
    *,
    approval_path: Path | None = None,
    handoff_path: Path | None = None,
    evidence_index_path: Path | None = None,
    evidence_root: Path | None = None,
    receipt_path: Path | None = None,
    expected_policy_sha256: str | None = None,
    require_execution: bool = False,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    policy_file_sha256: str | None = None
    try:
        policy_value, policy_file_sha256 = _load_with_sha256(policy_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error("POLICY_READ_FAILED", str(policy_path), str(exc)))
        policy_value = {}
    policy = _verify_policy(policy_value, errors)
    policy_error_count = len(errors)

    approval: dict[str, Any] = {}
    handoff: dict[str, Any] = {}
    evidence_index: dict[str, Any] = {}
    evidence_artifacts: dict[str, dict[str, Any]] = {}
    receipt: dict[str, Any] = {}
    evidence_paths = (
        approval_path,
        handoff_path,
        evidence_index_path,
        evidence_root,
        receipt_path,
    )
    external_requested = any(path is not None for path in evidence_paths)
    if external_requested and (
        not _sha(expected_policy_sha256)
        or expected_policy_sha256 != policy_file_sha256
    ):
        errors.append(
            _error(
                "EXPECTED_POLICY_HASH_REQUIRED",
                "$",
                "external evidence requires the caller-pinned exact policy SHA-256",
            )
        )
    if external_requested and (
        AUTHORIZED_EXECUTION_POLICY_SHA256 is None
        or policy_file_sha256 != AUTHORIZED_EXECUTION_POLICY_SHA256
    ):
        errors.append(
            _error(
                "EXECUTION_POLICY_NOT_ACTIVATED",
                "$",
                "the reviewed target-specific policy hash is not activated in this verifier",
            )
        )
    if external_requested and not all(path is not None for path in evidence_paths):
        errors.append(
            _error(
                "EXTERNAL_EVIDENCE_SET_REQUIRED",
                "$",
                "approval, handoff, signed evidence index, sanitized artifact root, and target receipt must be supplied together",
            )
        )
    elif (
        approval_path is not None
        and handoff_path is not None
        and evidence_index_path is not None
        and evidence_root is not None
        and receipt_path is not None
    ):
        approval_file_sha256: str | None = None
        handoff_file_sha256: str | None = None
        evidence_index_file_sha256: str | None = None
        trust = policy.get("trust_anchor_policy")
        if not isinstance(trust, dict) or trust.get("status") != "bound":
            errors.append(
                _error(
                    "TRUST_ANCHORS_NOT_BOUND",
                    "$.trust_anchor_policy",
                    "external evidence cannot be accepted until policy trust anchors are bound",
                )
            )
        try:
            approval_value, approval_file_sha256 = _load_with_sha256(approval_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(_error("APPROVAL_READ_FAILED", str(approval_path), str(exc)))
            approval_value = {}
        approval = _verify_approval(approval_value, policy=policy, errors=errors)
        try:
            handoff_value, handoff_file_sha256 = _load_with_sha256(handoff_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(_error("HANDOFF_READ_FAILED", str(handoff_path), str(exc)))
            handoff_value = {}
        handoff = _verify_handoff(
            handoff_value,
            policy=policy,
            approval=approval,
            errors=errors,
        )
        try:
            evidence_index_value, evidence_index_file_sha256 = _load_with_sha256(
                evidence_index_path
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(
                _error("EVIDENCE_INDEX_READ_FAILED", str(evidence_index_path), str(exc))
            )
            evidence_index_value = {}
        evidence_index = _verify_evidence_index(
            evidence_index_value,
            policy=policy,
            approval=approval,
            handoff=handoff,
            evidence_root=evidence_root,
            verified_artifacts=evidence_artifacts,
            errors=errors,
        )
        try:
            receipt_value, _ = _load_with_sha256(receipt_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(_error("RECEIPT_READ_FAILED", str(receipt_path), str(exc)))
            receipt_value = {}
        receipt = _verify_receipt(
            receipt_value,
            policy=policy,
            policy_sha256=policy_file_sha256,
            approval=approval,
            approval_sha256=approval_file_sha256,
            handoff=handoff,
            handoff_sha256=handoff_file_sha256,
            evidence_index=evidence_index,
            evidence_index_sha256=evidence_index_file_sha256,
            evidence_artifacts=evidence_artifacts,
            errors=errors,
        )

    policy_valid = policy_error_count == 0
    gate_candidate = (
        external_requested
        and not errors
        and bool(approval)
        and bool(handoff)
        and bool(evidence_index)
        and bool(receipt)
    )
    if require_execution and not gate_candidate:
        errors.append(
            _error(
                "TARGET_EXECUTION_EVIDENCE_REQUIRED",
                "$",
                "execution-required mode needs a verified target evidence set",
            )
        )
    errors.sort(key=lambda item: (item["code"], item["subject"], item["message"]))
    gate_passed = gate_candidate and not errors
    return {
        "contract": AUDIT_CONTRACT,
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "errors": errors,
        "policy_sha256": policy_file_sha256,
        "readiness_contract_passed": policy_valid,
        "approval_required": not gate_passed,
        "target_operations_handoff_required": not gate_passed,
        "target_execution_required": not gate_passed,
        "gate_passed": gate_passed,
        "scenario_count": len(SCENARIO_IDS),
        "rpo_seconds_max": RPO_SECONDS,
        "rto_seconds_max": RTO_SECONDS,
        "production_restore_executed": False,
        "product_complete": False,
        "execution_required_mode": require_execution,
        "caller_pinned_policy": expected_policy_sha256 == policy_file_sha256,
    }


def verify_pre_execution(
    policy_path: Path,
    *,
    approval_path: Path,
    handoff_path: Path,
    expected_policy_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify one still-live signed execution grant without starting target work."""

    errors: list[dict[str, str]] = []
    policy_file_sha256: str | None = None
    approval_file_sha256: str | None = None
    handoff_file_sha256: str | None = None
    try:
        policy_value, policy_file_sha256 = _load_with_sha256(policy_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error("POLICY_READ_FAILED", str(policy_path), str(exc)))
        policy_value = {}
    policy = _verify_policy(policy_value, errors)
    if not _sha(expected_policy_sha256) or expected_policy_sha256 != policy_file_sha256:
        errors.append(
            _error(
                "EXPECTED_POLICY_HASH_REQUIRED",
                "$",
                "pre-execution verification requires the caller-pinned exact policy SHA-256",
            )
        )
    if (
        AUTHORIZED_EXECUTION_POLICY_SHA256 is None
        or policy_file_sha256 != AUTHORIZED_EXECUTION_POLICY_SHA256
    ):
        errors.append(
            _error(
                "EXECUTION_POLICY_NOT_ACTIVATED",
                "$",
                "the reviewed target-specific policy hash is not activated in this verifier",
            )
        )
    try:
        approval_value, approval_file_sha256 = _load_with_sha256(approval_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error("APPROVAL_READ_FAILED", str(approval_path), str(exc)))
        approval_value = {}
    approval = _verify_approval(approval_value, policy=policy, errors=errors)
    try:
        handoff_value, handoff_file_sha256 = _load_with_sha256(handoff_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error("HANDOFF_READ_FAILED", str(handoff_path), str(exc)))
        handoff_value = {}
    handoff = _verify_handoff(
        handoff_value,
        policy=policy,
        approval=approval,
        errors=errors,
    )

    observed_at = now if now is not None else datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        errors.append(
            _error(
                "PRE_EXECUTION_TIME_INVALID",
                "$",
                "pre-execution time must be timezone-aware",
            )
        )
    else:
        observed_at = observed_at.astimezone(timezone.utc)
        approval_issued = _timestamp(approval.get("issued_at"))
        approval_expires = _timestamp(approval.get("expires_at"))
        handoff_accepted = _timestamp(handoff.get("accepted_at"))
        handoff_expires = _timestamp(handoff.get("expires_at"))
        if not (
            approval_issued is not None
            and approval_expires is not None
            and handoff_accepted is not None
            and handoff_expires is not None
            and approval_issued <= handoff_accepted <= observed_at
            and observed_at < approval_expires
            and observed_at < handoff_expires
        ):
            errors.append(
                _error(
                    "PRE_EXECUTION_WINDOW_CLOSED",
                    "$",
                    "approval and handoff must both be accepted and unexpired at action time",
                )
            )

    errors.sort(key=lambda item: (item["code"], item["subject"], item["message"]))
    authorized = not errors and bool(policy) and bool(approval) and bool(handoff)
    return {
        "contract": PRE_EXECUTION_AUDIT_CONTRACT,
        "status": "pass" if authorized else "fail",
        "error_count": len(errors),
        "errors": errors,
        "policy_sha256": policy_file_sha256,
        "approval_sha256": approval_file_sha256,
        "handoff_sha256": handoff_file_sha256,
        "authorized_execution_id": (
            approval.get("authorized_execution_id") if authorized else None
        ),
        "scope_sha256": approval.get("scope_sha256") if authorized else None,
        "release_candidate_sha256": (
            approval.get("release_candidate_sha256") if authorized else None
        ),
        "pre_execution_authorized": authorized,
        "nonce_consumption_required": True,
        "nonce_consumed": False,
        "external_action_started": False,
        "target_action_executed": False,
        "gate_passed": False,
        "production_restore_executed": False,
        "product_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--handoff", type=Path)
    parser.add_argument("--evidence-index", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--expected-policy-sha256")
    parser.add_argument("--require-execution", action="store_true")
    parser.add_argument("--pre-execution", action="store_true")
    parser.add_argument("--compact", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.pre_execution:
        if (
            arguments.approval is None
            or arguments.handoff is None
            or arguments.expected_policy_sha256 is None
            or arguments.evidence_index is not None
            or arguments.evidence_root is not None
            or arguments.receipt is not None
            or arguments.require_execution
        ):
            report = {
                "contract": PRE_EXECUTION_AUDIT_CONTRACT,
                "status": "fail",
                "error_count": 1,
                "errors": [
                    _error(
                        "PRE_EXECUTION_ARGUMENTS",
                        "$",
                        "pre-execution requires only policy, approval, handoff, and exact policy hash",
                    )
                ],
                "pre_execution_authorized": False,
                "nonce_consumed": False,
                "external_action_started": False,
                "target_action_executed": False,
                "gate_passed": False,
                "production_restore_executed": False,
                "product_complete": False,
            }
        else:
            report = verify_pre_execution(
                arguments.policy,
                approval_path=arguments.approval,
                handoff_path=arguments.handoff,
                expected_policy_sha256=arguments.expected_policy_sha256,
            )
    else:
        report = verify(
            arguments.policy,
            approval_path=arguments.approval,
            handoff_path=arguments.handoff,
            evidence_index_path=arguments.evidence_index,
            evidence_root=arguments.evidence_root,
            receipt_path=arguments.receipt,
            expected_policy_sha256=arguments.expected_policy_sha256,
            require_execution=arguments.require_execution,
        )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if arguments.compact else 2,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ACTION_CONFIRMATION",
    "APPROVAL_CONTRACT",
    "APPROVAL_ID",
    "AUDIT_CONTRACT",
    "AUTHORIZED_EXECUTION_POLICY_SHA256",
    "CANARY_IDS",
    "EVIDENCE_ARTIFACT_CONTRACT",
    "EVIDENCE_INDEX_CONTRACT",
    "HANDOFF_CONTRACT",
    "HANDOFF_ID",
    "HANDOFF_PACKET_IDS",
    "POLICY_CONTRACT",
    "POLICY_PATH",
    "PRE_EXECUTION_AUDIT_CONTRACT",
    "REALM_ID",
    "RECEIPT_CONTRACT",
    "REQUIRED_EVIDENCE_ARTIFACT_IDS",
    "RETENTION",
    "RPO_SECONDS",
    "RTO_SECONDS",
    "SCENARIO_IDS",
    "TARGET_SCOPE",
    "main",
    "verify",
    "verify_pre_execution",
]

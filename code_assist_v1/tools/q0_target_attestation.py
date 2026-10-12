"""Verify signed, release-bound Q0 target-load attestations.

This validation-only module does not start services or execute target actions.
It binds a Q0 report to an already verified O1B release, target machine, and
external reviewer without changing the product release candidate source set.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from code_assist_v1.notion_collaboration.q0_validation import (
    Q0EvidenceError,
    TARGET_REALM_ID,
    validate_q0_report,
)


Q0_TARGET_ATTESTATION_CONTRACT = (
    "code_assist.notion.q0.target_load_attestation.v1"
)


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
        raise Q0EvidenceError("Q0 attestation must be canonical JSON") from exc


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Q0EvidenceError("duplicate JSON key")
        result[key] = value
    return result


def load_q0_json_document(path: Path) -> dict[str, Any]:
    """Load a Q0 JSON object while rejecting duplicate keys and invalid UTF-8."""

    try:
        value = json.loads(
            Path(path).read_bytes().decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Q0EvidenceError("invalid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise Q0EvidenceError("JSON object required")
    return value


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical_sha256_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if (
        len(normalized) != 71
        or not normalized.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in normalized[7:])
    ):
        return None
    return normalized


def _opaque_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 160
        and value == value.strip()
        and not any(character in value for character in ("/", "\\", "\r", "\n", "\t"))
    )


def _utc_timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise Q0EvidenceError(f"UTC timestamp required for {field}")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise Q0EvidenceError(f"UTC timestamp required for {field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise Q0EvidenceError(f"UTC timestamp required for {field}")
    return parsed


def _strict_base64(value: Any, *, field: str, length: int) -> bytes:
    if not isinstance(value, str) or not value:
        raise Q0EvidenceError(f"base64 value required for {field}")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise Q0EvidenceError(f"invalid base64 for {field}") from exc
    if len(decoded) != length:
        raise Q0EvidenceError(f"invalid byte length for {field}")
    return decoded


def q0_target_attestation_payload(attestation: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact canonical document covered by the reviewer signature."""

    return {
        key: value
        for key, value in attestation.items()
        if key != "signature_base64"
    }


def validate_q0_target_attestation(
    attestation: Mapping[str, Any],
    *,
    report: Mapping[str, Any],
    q0_report_sha256: str,
    policy: Mapping[str, Any],
    approval: Mapping[str, Any],
    target_receipt: Mapping[str, Any],
    target_receipt_sha256: str,
    expected_release_candidate_id: str,
    expected_realm_config_fingerprint: str,
) -> dict[str, Any]:
    """Verify external-reviewer proof over one exact Q0 target-load report."""

    required_fields = {
        "schema_version",
        "contract",
        "packet_id",
        "lane_id",
        "realm_id",
        "release_candidate_id",
        "realm_config_fingerprint",
        "target_host_id",
        "target_machine_identity_sha256",
        "q0_report_sha256",
        "q0_run_id",
        "synthetic_root_identity_sha256",
        "o1b_target_receipt_sha256",
        "issued_at",
        "reviewer_id",
        "attestation_system_id",
        "trust_policy_id",
        "signature_key_id",
        "signature_algorithm",
        "private_key_material_in_attestation",
        "claims",
        "signature_base64",
    }
    if set(attestation) != required_fields:
        raise Q0EvidenceError("Q0 target attestation field set mismatch")
    if (
        attestation.get("schema_version") != 1
        or attestation.get("contract") != Q0_TARGET_ATTESTATION_CONTRACT
        or attestation.get("packet_id") != "Q0-P1"
        or attestation.get("lane_id") != "L8-COLLAB-LOAD-FAULT"
        or attestation.get("realm_id") != TARGET_REALM_ID
    ):
        raise Q0EvidenceError("Q0 target attestation identity mismatch")

    report_result = validate_q0_report(report, require_target_realm=True)
    if report_result.get("target_gate_eligible") is not True:
        raise Q0EvidenceError("Q0 report is not target-gate eligible")
    realm = report.get("realm")
    if not isinstance(realm, Mapping):
        raise Q0EvidenceError("Q0 report realm missing")
    q0_run_id = realm.get("run_id")
    if not _opaque_id(q0_run_id):
        raise Q0EvidenceError("opaque Q0 run ID required")

    expected_release = _canonical_sha256_value(expected_release_candidate_id)
    expected_fingerprint = _canonical_sha256_value(
        expected_realm_config_fingerprint
    )
    report_hash = _canonical_sha256_value(q0_report_sha256)
    target_receipt_hash = _canonical_sha256_value(target_receipt_sha256)
    trust = policy.get("trust_anchor_policy")
    scope = approval.get("resource_scope")
    reviewer = target_receipt.get("reviewer_attestation")
    if not isinstance(trust, Mapping) or trust.get("status") != "bound":
        raise Q0EvidenceError("bound O1B trust policy required")
    anchor = trust.get("receipt_reviewer")
    if not isinstance(anchor, Mapping):
        raise Q0EvidenceError("O1B receipt reviewer trust anchor required")
    if not isinstance(scope, Mapping) or not isinstance(reviewer, Mapping):
        raise Q0EvidenceError("verified O1B approval and reviewer receipt required")
    if None in (expected_release, expected_fingerprint, report_hash, target_receipt_hash):
        raise Q0EvidenceError("canonical SHA-256 bindings required")
    if trust.get("authorized_release_candidate_sha256") != expected_release:
        raise Q0EvidenceError("O1B authorized release candidate mismatch")

    issued_at = _utc_timestamp(attestation.get("issued_at"), field="issued_at")
    reviewer_signed_at = _utc_timestamp(
        reviewer.get("signed_at"), field="O1B reviewer signed_at"
    )
    if issued_at < reviewer_signed_at:
        raise Q0EvidenceError("Q0 attestation predates the verified O1B receipt")

    expected_claims = {
        "Q0_P1_target_load_executed": True,
        "runner_owned_synthetic_root": True,
        "contains_real_user_content": False,
        "production_root_accessed": False,
        "product_complete": False,
        "active_shared_goal_complete": False,
    }
    exact_bindings = (
        attestation.get("release_candidate_id") == expected_release
        and attestation.get("realm_config_fingerprint") == expected_fingerprint
        and attestation.get("target_host_id") == scope.get("target_host_id")
        and attestation.get("target_machine_identity_sha256")
        == scope.get("target_machine_identity_sha256")
        and attestation.get("q0_report_sha256") == report_hash
        and attestation.get("q0_run_id") == q0_run_id
        and attestation.get("synthetic_root_identity_sha256")
        == realm.get("synthetic_root_identity_sha256")
        and attestation.get("o1b_target_receipt_sha256") == target_receipt_hash
        and attestation.get("reviewer_id") == reviewer.get("reviewer_id")
        and attestation.get("attestation_system_id")
        == reviewer.get("attestation_system_id")
        and attestation.get("trust_policy_id") == reviewer.get("trust_policy_id")
        and attestation.get("signature_key_id") == anchor.get("key_id")
        and attestation.get("signature_key_id") == reviewer.get("signature_key_id")
        and attestation.get("signature_algorithm") == "ed25519"
        and attestation.get("private_key_material_in_attestation") is False
        and attestation.get("claims") == expected_claims
    )
    if not exact_bindings:
        raise Q0EvidenceError("Q0 attestation does not match the verified target evidence")
    if not _opaque_id(attestation.get("target_host_id")):
        raise Q0EvidenceError("opaque target host ID required")
    for field in (
        "target_machine_identity_sha256",
        "synthetic_root_identity_sha256",
    ):
        if _canonical_sha256_value(attestation.get(field)) is None:
            raise Q0EvidenceError(f"canonical SHA-256 required for {field}")

    public_key = _strict_base64(
        anchor.get("public_key_base64"),
        field="receipt reviewer public key",
        length=32,
    )
    if _sha256(public_key) != anchor.get("public_key_sha256"):
        raise Q0EvidenceError("receipt reviewer public key hash mismatch")
    signature = _strict_base64(
        attestation.get("signature_base64"),
        field="Q0 reviewer signature",
        length=64,
    )
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            _canonical_bytes(q0_target_attestation_payload(attestation)),
        )
    except (InvalidSignature, ValueError) as exc:
        raise Q0EvidenceError("Q0 reviewer signature invalid") from exc

    return {
        "contract": Q0_TARGET_ATTESTATION_CONTRACT,
        "status": "pass",
        "target_gate_eligible": True,
        "release_candidate_bound": True,
        "target_machine_bound": True,
        "q0_report_bound": True,
        "external_reviewer_signature_verified": True,
        "product_complete": False,
    }


__all__ = [
    "Q0_TARGET_ATTESTATION_CONTRACT",
    "load_q0_json_document",
    "q0_target_attestation_payload",
    "validate_q0_target_attestation",
]

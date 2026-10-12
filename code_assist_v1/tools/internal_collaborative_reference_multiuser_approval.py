"""Compile and verify the exact AP-REFERENCE-MULTIUSER owner receipt."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_execution import (
    CONTRACT_PATH as EXECUTION_MANIFEST_PATH,
    EXACT_APPROVAL_TEXT,
    verify as verify_execution_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT = "code_assist.notion.internal_collaborative_reference_multiuser_approval.v0r_p1.v1"
MANIFEST_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/core_free_web_ui/"
    "internal_collaborative_reference_multiuser_execution.v1.json"
)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {value}")
        ),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_time(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def compile_approval_receipt(
    *,
    approval_text: str,
    recorded_at: str,
    manifest_path: Path = EXECUTION_MANIFEST_PATH,
) -> dict[str, Any]:
    """Compile a non-evidentiary execution authorization from exact owner text."""

    if approval_text != EXACT_APPROVAL_TEXT:
        raise ValueError("exact AP-REFERENCE-MULTIUSER approval text required")
    if not _valid_time(recorded_at):
        raise ValueError("recorded_at must be an RFC 3339 timestamp with an offset")
    manifest_report = verify_execution_manifest(manifest_path)
    if manifest_report.get("status") != "pass":
        raise ValueError(
            "V0R-P1 execution manifest is invalid: "
            + json.dumps(manifest_report, ensure_ascii=False, sort_keys=True)
        )
    manifest = _load(manifest_path)
    actions = [item["id"] for item in manifest["operator_actions"]]
    states = [item["id"] for item in manifest["capture_states"]]
    return {
        "schema_version": 1,
        "contract": CONTRACT,
        "packet_id": "V0R-P1",
        "approval_id": "AP-REFERENCE-MULTIUSER",
        "recorded_at": recorded_at,
        "approval": {
            "exact_user_text": approval_text,
            "approval_sha256": _sha256_text(approval_text),
            "status": "owner_approved",
            "approval_is_test_evidence": False,
        },
        "execution_manifest": {
            "path": MANIFEST_REPO_PATH,
            "sha256": _sha256_file(manifest_path),
            "audit_status": "pass",
        },
        "authorization": {
            "workspace_aliases": ["REF-FREE-A", "REF-FREE-B"],
            "identity_aliases": ["REF-OWNER", "REF-COLLABORATOR"],
            "operator_action_ids": actions,
            "capture_state_ids": states,
            "expected_screenshot_count": 36,
            "external_reference_mutation_authorized": True,
            "test_identity_invite_role_change_and_revoke_authorized": True,
            "synthetic_page_edit_comment_reaction_mention_and_database_mutation_authorized": True,
            "capture_authorized": True,
            "cleanup_authorized": True,
            "cleanup_mode": "trash_only_recoverable",
            "cleanup_required_after_success_or_failure": True,
            "personal_workspace_access_allowed": False,
            "personal_page_access_allowed": False,
            "non_test_identity_invite_allowed": False,
            "public_publish_or_public_link_allowed": False,
            "message_outside_test_identities_allowed": False,
            "permanent_delete_allowed": False,
            "production_or_second_brain_mutation_allowed": False,
        },
        "validity": {
            "starts_at": recorded_at,
            "expires_on_manifest_hash_drift": True,
            "expires_on_workspace_or_identity_alias_mismatch": True,
            "expires_on_browser_control_or_identity_certainty_loss": True,
            "cannot_be_reused_for_other_packets": True,
        },
        "claims": {
            "owner_approval_received": True,
            "execution_authorized": True,
            "external_reference_mutation_executed": False,
            "reference_screenshots_captured": False,
            "cleanup_executed": False,
            "V0R_P1_gate_passed": False,
            "PIX_001_closed": False,
            "A11Y_001_closed": False,
            "UI_CORE_25_closed": False,
            "production_changed": False,
            "second_brain_changed": False,
            "product_complete": False,
            "active_shared_goal_complete": False,
        },
    }


def verify_approval_receipt(
    path: Path,
    *,
    manifest_path: Path = EXECUTION_MANIFEST_PATH,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        receipt = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "contract": "code_assist.notion.internal_collaborative_reference_multiuser_approval.audit.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [
                {"code": "RECEIPT_READ_FAILED", "subject": str(path), "message": str(exc)}
            ],
        }
        return report, 1
    check(isinstance(receipt, dict), "RECEIPT_SHAPE", "$", "receipt must be an object")
    if not isinstance(receipt, dict):
        return {"status": "fail", "error_count": len(errors), "errors": errors}, 1

    approval = receipt.get("approval", {})
    check(
        receipt.get("schema_version") == 1
        and receipt.get("contract") == CONTRACT
        and receipt.get("packet_id") == "V0R-P1"
        and receipt.get("approval_id") == "AP-REFERENCE-MULTIUSER"
        and _valid_time(receipt.get("recorded_at")),
        "RECEIPT_IDENTITY",
        "$",
        "V0R-P1 approval receipt identity or time drift",
    )
    check(
        isinstance(approval, dict)
        and approval.get("exact_user_text") == EXACT_APPROVAL_TEXT
        and approval.get("approval_sha256") == _sha256_text(EXACT_APPROVAL_TEXT)
        and approval.get("status") == "owner_approved"
        and approval.get("approval_is_test_evidence") is False,
        "APPROVAL_TEXT",
        "$.approval",
        "exact owner approval binding required",
    )

    manifest_report = verify_execution_manifest(manifest_path)
    manifest_binding = receipt.get("execution_manifest", {})
    check(
        manifest_report.get("status") == "pass"
        and isinstance(manifest_binding, dict)
        and manifest_binding.get("path") == MANIFEST_REPO_PATH
        and manifest_binding.get("sha256") == _sha256_file(manifest_path)
        and manifest_binding.get("audit_status") == "pass",
        "MANIFEST_BINDING",
        "$.execution_manifest",
        "current audited execution manifest hash required",
    )
    try:
        manifest = _load(manifest_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        manifest = {}
    expected_actions = [item["id"] for item in manifest.get("operator_actions", [])]
    expected_states = [item["id"] for item in manifest.get("capture_states", [])]
    authorization = receipt.get("authorization", {})
    check(
        isinstance(authorization, dict)
        and authorization.get("workspace_aliases") == ["REF-FREE-A", "REF-FREE-B"]
        and authorization.get("identity_aliases") == ["REF-OWNER", "REF-COLLABORATOR"]
        and authorization.get("operator_action_ids") == expected_actions
        and authorization.get("capture_state_ids") == expected_states
        and authorization.get("expected_screenshot_count") == 36
        and all(
            authorization.get(field) is True
            for field in (
                "external_reference_mutation_authorized",
                "test_identity_invite_role_change_and_revoke_authorized",
                "synthetic_page_edit_comment_reaction_mention_and_database_mutation_authorized",
                "capture_authorized",
                "cleanup_authorized",
                "cleanup_required_after_success_or_failure",
            )
        )
        and authorization.get("cleanup_mode") == "trash_only_recoverable",
        "AUTHORIZED_SCOPE",
        "$.authorization",
        "exact action, state, capture, and cleanup authority required",
    )
    forbidden = (
        "personal_workspace_access_allowed",
        "personal_page_access_allowed",
        "non_test_identity_invite_allowed",
        "public_publish_or_public_link_allowed",
        "message_outside_test_identities_allowed",
        "permanent_delete_allowed",
        "production_or_second_brain_mutation_allowed",
    )
    check(
        isinstance(authorization, dict)
        and all(authorization.get(field) is False for field in forbidden),
        "FORBIDDEN_SCOPE",
        "$.authorization",
        "personal, non-test, public, permanent-delete, production, and Second Brain scope forbidden",
    )

    validity = receipt.get("validity", {})
    check(
        isinstance(validity, dict)
        and validity.get("starts_at") == receipt.get("recorded_at")
        and all(
            validity.get(field) is True
            for field in (
                "expires_on_manifest_hash_drift",
                "expires_on_workspace_or_identity_alias_mismatch",
                "expires_on_browser_control_or_identity_certainty_loss",
                "cannot_be_reused_for_other_packets",
            )
        ),
        "VALIDITY_BOUNDARY",
        "$.validity",
        "fail-closed approval validity required",
    )

    claims = receipt.get("claims", {})
    false_claims = (
        "external_reference_mutation_executed",
        "reference_screenshots_captured",
        "cleanup_executed",
        "V0R_P1_gate_passed",
        "PIX_001_closed",
        "A11Y_001_closed",
        "UI_CORE_25_closed",
        "production_changed",
        "second_brain_changed",
        "product_complete",
        "active_shared_goal_complete",
    )
    check(
        isinstance(claims, dict)
        and claims.get("owner_approval_received") is True
        and claims.get("execution_authorized") is True
        and all(claims.get(field) is False for field in false_claims),
        "CLAIMS_FALSE_GREEN",
        "$.claims",
        "approval authorizes execution but proves no execution or completion",
    )

    normalized = sorted(
        {
            (item["code"], item["subject"], item["message"]): item
            for item in errors
        }.values(),
        key=lambda item: (item["code"], item["subject"], item["message"]),
    )
    report = {
        "contract": "code_assist.notion.internal_collaborative_reference_multiuser_approval.audit.v1",
        "status": "pass" if not normalized else "fail",
        "error_count": len(normalized),
        "errors": normalized,
        "receipt_sha256": _sha256_file(path),
        "approval_received": not normalized,
        "execution_authorized": not normalized,
        "approval_is_test_evidence": False,
        "external_mutation_executed": False,
        "product_complete": False,
    }
    return report, 0 if not normalized else 1


__all__ = [
    "CONTRACT",
    "EXACT_APPROVAL_TEXT",
    "compile_approval_receipt",
    "verify_approval_receipt",
]

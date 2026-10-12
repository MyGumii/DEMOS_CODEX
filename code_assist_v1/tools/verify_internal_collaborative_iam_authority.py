"""Fail-closed verifier for the I0-P1 collaborative IAM authority."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from code_assist_v1 import notion_workspace_iam_migration as migration
from code_assist_v1 import notion_workspace_realtime_transport_migration as source_migration
from code_assist_v1.notion_collaboration import iam_authority


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parent
DEFAULT_CONTRACT = (
    PACKAGE_ROOT
    / "docs/notion_parity/core_free_web_ui/internal_collaborative_iam_authority.v1.json"
)
REPORT_SCHEMA_VERSION = 1
CONTRACT_ID = "code_assist.notion.internal_collaborative_iam_authority.v1"
EXPECTED_ARTIFACTS = (
    "code_assist_v1/notion_workspace_iam_migration.py",
    "code_assist_v1/notion_collaboration/iam_authority.py",
    "code_assist_v1/tests/notion_collaboration/test_collaborative_iam_authority.py",
    "code_assist_v1/tools/verify_internal_collaborative_iam_authority.py",
)


def _duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_duplicates,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite number: {value}")
        ),
    )


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def verify(contract_path: Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject})

    try:
        contract = _load(contract_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        contract = {}
        errors.append({"code": "CONTRACT_READ_FAILED", "subject": str(contract_path)})
    if not isinstance(contract, dict):
        contract = {}
        errors.append({"code": "CONTRACT_SHAPE_INVALID", "subject": "$"})
    check(contract.get("schema_version") == 1, "SCHEMA_VERSION_INVALID", "$.schema_version")
    check(contract.get("contract") == CONTRACT_ID, "CONTRACT_ID_INVALID", "$.contract")
    check(contract.get("packet_id") == "I0-P1", "PACKET_ID_INVALID", "$.packet_id")
    check(
        contract.get("status") == "implemented_and_locally_gate_verified_default_off",
        "STATUS_INVALID",
        "$.status",
    )

    lineage = contract.get("workspace_schema_lineage", {})
    observed_lineage = {
        "source_version": migration.SOURCE_VERSION,
        "source_name": migration.SOURCE_NAME,
        "source_checksum": migration.SOURCE_CHECKSUM,
        "source_actual_schema_sha256": migration.SOURCE_ACTUAL_SCHEMA_SHA256,
        "target_version": migration.TARGET_VERSION,
        "target_name": migration.TARGET_NAME,
        "target_checksum": migration.TARGET_CHECKSUM,
        "target_actual_schema_sha256": migration.TARGET_ACTUAL_SCHEMA_SHA256,
        "source_object_count": len(migration.SOURCE_OBJECTS),
        "target_object_count": len(migration.TARGET_OBJECTS),
        "historical_schema_or_migration_modified": False,
        "forward_only": True,
    }
    check(lineage == observed_lineage, "SCHEMA_LINEAGE_DRIFT", "$.workspace_schema_lineage")
    check(
        migration.SOURCE_SQL == source_migration.TARGET_SQL
        and migration.SOURCE_VERSION == source_migration.TARGET_VERSION == 2026082402
        and migration.TARGET_VERSION == 2026082403,
        "FORWARD_LINEAGE_INVALID",
        "notion_workspace_iam_migration.py",
    )
    source_names = {item["name"] for item in migration.SOURCE_OBJECTS}
    target_names = {item["name"] for item in migration.TARGET_OBJECTS}
    required_objects = contract.get("required_schema_objects", [])
    check(
        isinstance(required_objects, list)
        and len(required_objects) == len(set(required_objects))
        and set(required_objects).issubset(target_names)
        and set(required_objects).isdisjoint(source_names),
        "REQUIRED_SCHEMA_OBJECT_INVALID",
        "$.required_schema_objects",
    )

    migration_contract = contract.get("migration_contract", {})
    for key in (
        "single_explicit_migrator",
        "exact_dry_run_checksum_required",
        "exact_offline_backup_required",
        "catalog_quiescence_required",
        "idempotent_already_current_plan",
        "wrong_checksum_and_fault_leave_source_unchanged",
    ):
        check(migration_contract.get(key) is True, "MIGRATION_GUARD_MISSING", key)
    check(
        migration_contract.get("application_request_path_may_apply") is False
        and migration_contract.get("schema_downgrade_for_code_rollback") is False,
        "MIGRATION_BOUNDARY_WEAKENED",
        "$.migration_contract",
    )

    identity = contract.get("identity_contract", {})
    check(
        identity
        == {
            "login_authority": "Agent_identity_registry",
            "password_or_login_credential_copied": False,
            "accepted_target": "active_human_internal_subject_only",
            "disabled_service_and_system_subjects_rejected": True,
            "identity_snapshot_replaceable_outside_database_transaction": True,
            "notion_roles_grant_second_brain_authorship": False,
        },
        "IDENTITY_BOUNDARY_DRIFT",
        "$.identity_contract",
    )
    resolver = contract.get("role_resolver", {})
    check(
        resolver.get("roles_low_to_high") == list(iam_authority.PAGE_ROLE_LEVEL)
        and resolver.get("principals") == ["subject", "group", "teamspace"]
        and resolver.get("one_authoritative_snapshot") is True
        and resolver.get("workspace_administration_implies_page_access") is False
        and resolver.get("workspace_owner_implicit_page_role") == "full_access"
        and resolver.get("strongest_authorized_role_wins") is True
        and resolver.get("outsider_absent_and_denied_response")
        == "not found or permission denied",
        "ROLE_RESOLVER_DRIFT",
        "$.role_resolver",
    )
    check(
        iam_authority.PAGE_ROLE_CAPABILITIES
        == {
            "view": ("read",),
            "comment": ("read", "comment"),
            "edit_content": ("read", "comment", "edit_content"),
            "edit": ("read", "comment", "edit_content", "edit_schema"),
            "full_access": (
                "read",
                "comment",
                "edit_content",
                "edit_schema",
                "share",
            ),
        },
        "CAPABILITY_MATRIX_DRIFT",
        "PAGE_ROLE_CAPABILITIES",
    )
    mutation = contract.get("mutation_contract", {})
    for key in (
        "last_workspace_owner_removal_or_demotion_rejected",
        "target_active_human_revalidated",
        "same_operation_same_hash_returns_original_result",
        "same_operation_different_hash_rejected",
        "state_event_audit_outbox_and_operation_commit_together",
        "workspace_and_document_epochs_fence_stale_access",
    ):
        check(mutation.get(key) is True, "MUTATION_CONTRACT_DRIFT", key)
    check(
        mutation.get("post_revoke_protected_delivery_count") == 0,
        "REVOKE_LEAK_CLAIM_INVALID",
        "$.mutation_contract.post_revoke_protected_delivery_count",
    )

    hashes = contract.get("artifact_sha256", {})
    check(
        isinstance(hashes, dict) and tuple(hashes) == EXPECTED_ARTIFACTS,
        "ARTIFACT_SET_DRIFT",
        "$.artifact_sha256",
    )
    if isinstance(hashes, dict):
        for relative in EXPECTED_ARTIFACTS:
            path = REPOSITORY_ROOT / relative
            check(
                path.is_file() and hashes.get(relative) == _sha256(path),
                "ARTIFACT_HASH_DRIFT",
                relative,
            )

    authority_source = inspect.getsource(iam_authority.CollaborativeIAMAuthority)
    for forbidden in (
        "os.environ",
        "os.listdir",
        ".glob(",
        ".rglob(",
        "notion.db",
        "10011",
        "10012",
        "10111",
        "10112",
        "10113",
    ):
        check(
            forbidden not in authority_source,
            "REQUEST_DISCOVERY_OR_PORT_LITERAL",
            forbidden,
        )
    for method in (
        "resolve_access",
        "set_workspace_member",
        "revoke_workspace_member",
        "set_page_grant",
        "revoke_page_grant",
        "authorize_frame_send",
    ):
        check(
            callable(getattr(iam_authority.CollaborativeIAMAuthority, method, None)),
            "SERVICE_METHOD_MISSING",
            method,
        )

    validation = contract.get("validation_contract", {})
    check(
        validation.get("validation_ids") == ["L2-IAM-SHARING-AUTHORITY"]
        and validation.get("focused_migration_authority_and_multiprocess", {}).get("passed") == 7
        and validation.get("focused_migration_authority_and_multiprocess", {}).get("failed") == 0
        and validation.get("adjacent_A0B_T0B_and_I0_regression", {}).get("passed") == 33
        and validation.get("synthetic_temporary_databases_only") is True
        and validation.get("live_or_existing_database_count") == 0
        and validation.get("service_or_port_change_count") == 0,
        "VALIDATION_CLAIM_DRIFT",
        "$.validation_contract",
    )
    claims = contract.get("claims", {})
    check(
        claims
        == {
            "I0_P1_implemented": True,
            "I0_P1_locally_gate_verified": True,
            "development_10111_10112_10113_changed": False,
            "production_10011_10012_changed": False,
            "SBE_database_or_authorization_changed": False,
            "runtime_or_feature_enabled": False,
            "product_complete": False,
        },
        "CLAIM_DRIFT",
        "$.claims",
    )
    deduped = [
        {"code": code, "subject": subject}
        for code, subject in sorted({(item["code"], item["subject"]) for item in errors})
    ]
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "contract": "code_assist.notion.i0_p1.verification_report.v1",
        "status": "pass" if not deduped else "fail",
        "error_count": len(deduped),
        "errors": deduped,
        "contract_sha256": _sha256(contract_path) if contract_path.is_file() else None,
        "source_object_count": len(migration.SOURCE_OBJECTS),
        "target_object_count": len(migration.TARGET_OBJECTS),
        "gate_passed": contract.get("claims", {}).get("I0_P1_locally_gate_verified") is True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = verify(args.contract)
    print(
        json.dumps(
            report,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":") if args.compact else None,
            indent=None if args.compact else 2,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())

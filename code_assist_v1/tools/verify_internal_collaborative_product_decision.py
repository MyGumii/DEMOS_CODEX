"""Verify the approved G0-U product decision and aggregate G0 freeze."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = PACKAGE_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
ADR = CONTRACT_ROOT / "internal_collaborative_product_decision.v1.json"
EXPECTED_CONTRACT = "code_assist.notion.internal_collaborative_product_decision.g0_u.v1"
EXPECTED_DECISIONS = {
    "development_identity_import_scope",
    "target_scale_and_deployment_topology",
    "adopted_feature_denominator",
    "recovery_backup_retention_and_history_policy",
    "offline_editing_policy",
}


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs, parse_constant=_reject)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def verify(path: Path = ADR) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": "code_assist.notion.internal_collaborative_product_decision_verification.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [{"code": "ADR_READ_FAILED", "subject": str(path), "message": str(exc)}],
        }
    check(isinstance(value, dict), "ADR_SHAPE", "$", "ADR must be an object")
    if not isinstance(value, dict):
        return {"status": "fail", "error_count": len(errors), "errors": errors}
    check(value.get("schema_version") == 1, "ADR_VERSION", "$.schema_version", "schema version drift")
    check(value.get("contract") == EXPECTED_CONTRACT, "ADR_CONTRACT", "$.contract", "contract drift")
    check(value.get("decision_id") == "G0-U", "DECISION_ID", "$.decision_id", "decision id drift")
    check(value.get("status") == "frozen_product_owner_subgate", "DECISION_STATUS", "$.status", "decision is not frozen")

    approval = value.get("approval_receipt", {})
    check(isinstance(approval, dict) and approval.get("receipt_id") == "G0-U-USER-PRODUCT-OWNER-APPROVAL-20260824", "APPROVAL_RECEIPT", "$.approval_receipt", "explicit approval receipt missing")
    if isinstance(approval, dict):
        check(
            all(approval.get(field) is False for field in ("authorizes_external_mutation", "authorizes_dev_or_production_service_start", "authorizes_identity_import", "authorizes_production_deployment")),
            "APPROVAL_SCOPE_EXPANDED",
            "$.approval_receipt",
            "product choice must not authorize an external mutation",
        )

    prerequisites = value.get("prerequisite_evidence", {})
    check(isinstance(prerequisites, dict) and set(prerequisites) == {"U0A-GATE-001", "G0-E"}, "PREREQUISITE_DENOMINATOR", "$.prerequisite_evidence", "U0A and G0-E are required")
    if isinstance(prerequisites, dict):
        for key, item in prerequisites.items():
            if not isinstance(item, dict):
                errors.append({"code": "PREREQUISITE_SHAPE", "subject": key, "message": "prerequisite must be an object"})
                continue
            target = (CONTRACT_ROOT / str(item.get("path"))).resolve()
            check(target.is_file(), "PREREQUISITE_MISSING", key, "prerequisite file missing")
            if target.is_file():
                check(_sha256_file(target) == item.get("sha256"), "PREREQUISITE_HASH", key, "prerequisite hash drift")

    decisions = value.get("decisions", {})
    check(isinstance(decisions, dict) and set(decisions) == EXPECTED_DECISIONS, "DECISION_DENOMINATOR", "$.decisions", "exact five product decisions required")
    if isinstance(decisions, dict):
        identity = decisions.get("development_identity_import_scope", {})
        check(identity.get("development_identity_authority") == "10111_Agent_identity_registry" and identity.get("development_notion_target") == "10112_new_marker_bound_development_realm", "IDENTITY_DEV_PAIR", "$.decisions.development_identity_import_scope", "development pair drift")
        check(identity.get("workspace_membership") == "invitation_only_except_creator_owns_new_workspace", "IDENTITY_MEMBERSHIP", "$.decisions.development_identity_import_scope.workspace_membership", "workspace membership drift")
        check(identity.get("password_or_login_authority_copied_to_notion") is False and identity.get("cross_workspace_search_or_notification_leak_allowed") is False, "IDENTITY_BOUNDARY", "$.decisions.development_identity_import_scope", "identity or isolation boundary weakened")
        check(identity.get("actual_10111_import_requires_approval") == "AP-DEV-PAIR", "IDENTITY_APPROVAL", "$.decisions.development_identity_import_scope", "real identity import approval missing")

        scale = decisions.get("target_scale_and_deployment_topology", {})
        check((scale.get("registered_users"), scale.get("simultaneous_connections"), scale.get("simultaneous_editors_per_page")) == (100, 30, 10), "SCALE_CORE", "$.decisions.target_scale_and_deployment_topology", "approved 100/30/10 profile drift")
        check(scale.get("product_quota_mode") == "no_plan_or_user_feature_storage_quota", "SCALE_QUOTA", "$.decisions.target_scale_and_deployment_topology.product_quota_mode", "commercial quota reintroduced")
        check(scale.get("attachment_concurrency") == 10 and scale.get("sustained_operations_per_second") == 30 and scale.get("burst_operations_per_second") == 100, "SCALE_LOAD_FLOOR", "$.decisions.target_scale_and_deployment_topology", "derived load floor drift")
        for field in ("pages_per_workspace", "blocks_per_page", "rows_per_database"):
            item = scale.get(field, {})
            check(isinstance(item, dict) and item.get("product_limit") is None and type(item.get("acceptance_fixture_floor")) is int and item["acceptance_fixture_floor"] > 0, "SCALE_NOT_QUOTA", f"$.decisions.target_scale_and_deployment_topology.{field}", "acceptance fixture must not become a product quota")

        features = decisions.get("adopted_feature_denominator", {})
        adopted = set(features.get("adopted", []))
        deferred = set(features.get("deferred_and_hidden", []))
        excluded = set(features.get("excluded_and_hidden", []))
        check(bool(adopted) and not (adopted & deferred or adopted & excluded or deferred & excluded), "FEATURE_PARTITION", "$.decisions.adopted_feature_denominator", "feature sets must be nonempty and disjoint")
        for required in ("Core_25_desktop_web_scenarios", "live_presence_cursor_selection_and_convergent_multi_user_editing", "exact_revision_Second_Brain_publication_and_attachment_boundary"):
            check(required in adopted, "FEATURE_REQUIRED_MISSING", "$.decisions.adopted_feature_denominator.adopted", required)
        for forbidden in ("public_web_publish_and_public_links", "external_email_guests", "pricing_billing_and_plan_entitlements", "Notion_AI_replica"):
            check(forbidden in excluded, "FEATURE_HIDDEN_MISSING", "$.decisions.adopted_feature_denominator.excluded_and_hidden", forbidden)

        recovery = decisions.get("recovery_backup_retention_and_history_policy", {})
        check((recovery.get("rpo_seconds"), recovery.get("rto_seconds"), recovery.get("write_drain_timeout_seconds")) == (900, 3600, 60), "RECOVERY_SLO", "$.decisions.recovery_backup_retention_and_history_policy", "approved recovery profile drift")
        retention = recovery.get("backup_retention", {})
        check(isinstance(retention, dict) and retention.get("daily_generations") == 30 and retention.get("monthly_generations") == 12 and retention.get("rolling_15_minute_generations") == 96, "RECOVERY_RETENTION", "$.decisions.recovery_backup_retention_and_history_policy.backup_retention", "retention drift")
        check(recovery.get("page_history_retention") == "no_automatic_deletion_of_user_visible_revisions", "HISTORY_RETENTION", "$.decisions.recovery_backup_retention_and_history_policy.page_history_retention", "history policy drift")
        check(recovery.get("restore_secret_policy") == "rotate_session_signing_secret_and_force_reauthentication", "RESTORE_SECRET", "$.decisions.recovery_backup_retention_and_history_policy.restore_secret_policy", "restore secret policy drift")
        check("AP-OPS-TARGET" in str(recovery.get("external_destination")), "RECOVERY_TARGET_APPROVAL", "$.decisions.recovery_backup_retention_and_history_policy.external_destination", "external target must remain separately approved")

        offline = decisions.get("offline_editing_policy", {})
        check(offline.get("selected_level") == 2 and offline.get("selected") == "durable_browser_close_reopen_unsent_edit_recovery", "OFFLINE_LEVEL", "$.decisions.offline_editing_policy", "approved offline level drift")
        check(offline.get("full_offline_workspace_browse_search_or_bulk_sync_adopted") is False, "OFFLINE_SCOPE", "$.decisions.offline_editing_policy", "full offline scope silently adopted")

    boundary = value.get("g0_aggregate_boundary", {})
    check(isinstance(boundary, dict) and boundary.get("G0_U_status") == boundary.get("G0_E_status") == boundary.get("G0_status") == "frozen" and boundary.get("implementation_packets_requiring_G0_may_start") is True, "G0_BOUNDARY", "$.g0_aggregate_boundary", "aggregate G0 is not frozen")
    claims = value.get("claims", {})
    check(isinstance(claims, dict) and claims.get("G0_U_frozen") is True and claims.get("G0_E_frozen") is True and claims.get("G0_aggregate_frozen") is True, "G0_CLAIMS", "$.claims", "G0 freeze claims drift")
    if isinstance(claims, dict):
        check(all(claims.get(field) is False for field in ("identity_import_executed", "dev_or_production_service_started", "schema_migration_implemented", "runtime_or_feature_enabled", "live_or_production_data_accessed", "product_complete", "active_shared_goal_complete")), "CLAIMS_FALSE_GREEN", "$.claims", "decision receipt falsely claims execution or completion")

    return {
        "contract": "code_assist.notion.internal_collaborative_product_decision_verification.v1",
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "errors": errors,
        "adr_sha256": _sha256_file(path),
        "product_decision_sha256": _canonical_sha256(decisions) if isinstance(decisions, Mapping) else None,
        "load_profile": {"registered_users": 100, "simultaneous_connections": 30, "simultaneous_editors_per_page": 10},
        "claims": {
            "G0_U_frozen": not errors,
            "G0_E_frozen": not errors,
            "G0_aggregate_frozen": not errors,
            "product_complete": False,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adr", type=Path, default=ADR)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = verify(args.adr)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

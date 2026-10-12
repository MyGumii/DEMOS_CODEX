from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from code_assist_v1 import notion_workspace_search_authority_migration as migration
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
    / "internal_collaborative_adopted_surface.v1.json"
)
EXPECTED_VIEWS = ["table", "board", "list", "gallery", "calendar", "timeline"]
EXPECTED_ADOPTED = [
    "Core_25_desktop_web_scenarios",
    "workspace_create_switch_invite_roles_and_leave",
    "page_tree_create_move_duplicate_trash_restore_and_favorites",
    "rich_block_editor_slash_drag_keyboard_undo_redo_and_Korean_IME",
    "workspace_and_page_sharing_with_inherited_full_edit_comment_view_permissions",
    "live_presence_cursor_selection_and_convergent_multi_user_editing",
    "comments_replies_reactions_mentions_and_inbox",
    "files_and_images",
    "user_visible_page_history_and_revision_restore",
    "table_board_list_gallery_calendar_and_timeline_database_views",
    "database_filter_sort_group_relation_rollup_formula_and_templates",
    "Markdown_and_CSV_import_export",
    "reminders",
    "permission_aware_search_recent_favorites_and_backlinks",
    "exact_revision_Second_Brain_publication_and_attachment_boundary",
]
EXPECTED_DEFERRED = [
    "charts", "dashboards", "forms", "automations", "feed_view", "map_view",
    "native_mobile_apps", "full_offline_workspace_browse_and_search",
    "external_calendar_and_mail_integrations",
]
EXPECTED_EXCLUDED = [
    "public_web_publish_and_public_links", "external_email_guests",
    "pricing_billing_and_plan_entitlements", "Notion_AI_replica",
]
EXPECTED_SOURCE_PATHS = [
    "code_assist_v1/notion_collaboration/database_features.py",
    "code_assist_v1/notion_collaboration/editing_authority.py",
    "code_assist_v1/notion_collaboration/iam_runtime.py",
    "code_assist_v1/notion_collaboration/f0_p5_browser.py",
    "code_assist_v1/routes_notion_workspace.py",
    "code_assist_v1/app_notion.py",
    "code_assist_v1/notion_static/notion_database_bridge.js",
    "code_assist_v1/notion_static/notion.js",
    "code_assist_v1/notion_static/index.html",
    "code_assist_v1/docs/notion_workspace_route_contract.json",
    "code_assist_v1/docs/notion_workspace_route_semantics.json",
    "code_assist_v1/tools/adopted_surface_successor.py",
    "code_assist_v1/tools/search_authority_successor.py",
    "code_assist_v1/tools/database_feature_successor.py",
    "code_assist_v1/tools/verify_internal_live_collaboration_runtime.py",
    "code_assist_v1/tools/verify_internal_collaborative_sharing_runtime.py",
    "code_assist_v1/tools/verify_internal_collaborative_editing_authority.py",
    "code_assist_v1/tools/verify_internal_collaborative_search_authority.py",
    "code_assist_v1/tools/verify_notion_workspace_route_contract.py",
    "code_assist_v1/tools/verify_internal_collaborative_adopted_surface.py",
    "code_assist_v1/tools/internal_collaborative_validation.py",
]
EXPECTED_TEST_PATHS = [
    "code_assist_v1/tests/notion_collaboration/test_adopted_feature_surface.py",
    "code_assist_v1/tests/notion_collaboration/test_database_features.py",
    "code_assist_v1/tests/notion_collaboration/test_live_collaboration_authority.py",
    "code_assist_v1/tests/notion_collaboration/browser/test_f0_p5_adopted_surface.py",
    "code_assist_v1/tests/test_notion_database_bridge.js",
    "code_assist_v1/tests/test_notion_routes.py",
    "code_assist_v1/tests/test_notion_workspace_routes.py",
    "code_assist_v1/tests/test_notion_http_contract.py",
    "code_assist_v1/tests/test_verify_notion_workspace_route_contract.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_adopted_surface.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
]


def _is_nonadopted_path(path: str) -> bool:
    return bool(
        path == "/api/notion/ai/ask"
        or path.startswith("/api/notion/outlook")
        or path.startswith("/public/notion/")
        or "/public_link" in path
        or "/forms/" in path
    )


def _load(path: Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _repository_file(relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    target = (REPOSITORY_ROOT / relative).resolve(strict=False)
    try:
        target.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None


def _verify_manifest(
    value: Any,
    expected: list[str],
    subject: str,
    errors: list[dict[str, str]],
) -> None:
    if not isinstance(value, list):
        errors.append({"code": "MANIFEST_SHAPE", "subject": subject, "message": "array required"})
        return
    paths = [item.get("path") for item in value if isinstance(item, dict)]
    if paths != expected:
        errors.append({"code": "MANIFEST_DENOMINATOR", "subject": subject, "message": "exact ordered paths required"})
    for index, item in enumerate(value):
        target = _repository_file(item.get("path")) if isinstance(item, dict) else None
        if target is None:
            errors.append({"code": "MANIFEST_FILE_MISSING", "subject": f"{subject}[{index}]", "message": "file missing"})
        elif item.get("sha256") != _sha256(target):
            errors.append({"code": "MANIFEST_HASH_DRIFT", "subject": str(item.get("path")), "message": "bound file hash drift"})


def verify(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": "code_assist.notion.internal_collaborative_adopted_surface.audit.v1",
            "status": "fail", "error_count": 1,
            "errors": [{"code": "CONTRACT_READ_FAILED", "subject": str(path), "message": str(exc)}],
            "gate_passed": False, "product_complete": False,
        }
    if not isinstance(value, dict):
        return {"status": "fail", "error_count": 1, "errors": [{"code": "CONTRACT_SHAPE", "subject": "$", "message": "object required"}], "gate_passed": False, "product_complete": False}

    check(
        value.get("schema_version") == 1
        and value.get("contract") == "code_assist.notion.internal_collaborative_adopted_surface.f0_p5.v1"
        and value.get("packet_id") == "F0-P5"
        and value.get("status") == "implemented_and_validated",
        "CONTRACT_IDENTITY", "$", "F0-P5 identity or status drift",
    )

    instruction = value.get("dynamic_instruction_binding", {})
    agents = _repository_file(instruction.get("path")) if isinstance(instruction, dict) else None
    check(
        agents is not None and instruction.get("sha256") == _sha256(agents)
        and instruction.get("write_policy") == "read_only",
        "DYNAMIC_INSTRUCTION_DRIFT", "AGENTS.md", "current read-only instruction binding required",
    )

    prerequisites = value.get("prerequisite_evidence", {})
    for key, expected_packet in (("F0_P4_checkpoint", "F0-P4"), ("UI0_P1_checkpoint", "UI0-P1")):
        item = prerequisites.get(key, {}) if isinstance(prerequisites, dict) else {}
        target = _repository_file(item.get("path")) if isinstance(item, dict) else None
        check(
            target is not None and item.get("packet_id") == expected_packet
            and item.get("sha256") == _sha256(target),
            "PREREQUISITE_DRIFT", f"$.prerequisite_evidence.{key}", "exact predecessor checkpoint required",
        )

    decision_binding = value.get("product_decision_binding", {})
    decision_path = _repository_file(decision_binding.get("path")) if isinstance(decision_binding, dict) else None
    decision: dict[str, Any] = {}
    if decision_path is not None:
        try:
            loaded = _load(decision_path)
            decision = loaded if isinstance(loaded, dict) else {}
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            decision = {}
    denominator = decision.get("decisions", {}).get("adopted_feature_denominator", {})
    scale = decision.get("decisions", {}).get("target_scale_and_deployment_topology", {})
    check(
        decision_path is not None and decision_binding.get("sha256") == _sha256(decision_path)
        and denominator.get("adopted") == EXPECTED_ADOPTED
        and denominator.get("deferred_and_hidden") == EXPECTED_DEFERRED
        and denominator.get("excluded_and_hidden") == EXPECTED_EXCLUDED,
        "PRODUCT_DECISION_DRIFT", "$.product_decision_binding", "frozen adopted denominator required",
    )

    surface = value.get("adopted_surface", {})
    check(
        surface.get("adopted_feature_count") == len(EXPECTED_ADOPTED)
        and surface.get("adopted_features") == EXPECTED_ADOPTED
        and surface.get("deferred_and_hidden") == EXPECTED_DEFERRED
        and surface.get("excluded_and_hidden") == EXPECTED_EXCLUDED
        and surface.get("adopted_database_views") == EXPECTED_VIEWS
        and surface.get("every_nonadopted_control_hidden_or_route_fenced") is True,
        "ADOPTED_SURFACE_DRIFT", "$.adopted_surface", "exact adopted and hidden surface required",
    )

    capability = value.get("uniform_capability_contract", {})
    check(
        scale.get("product_quota_mode") == "no_plan_or_user_feature_storage_quota"
        and all(scale.get(key, {}).get("product_limit") is None for key in ("pages_per_workspace", "blocks_per_page", "rows_per_database"))
        and capability.get("plan_or_entitlement_tiers") is False
        and capability.get("upgrade_pricing_billing_ui") is False
        and capability.get("feature_or_storage_product_quotas") is False
        and capability.get("authorization_and_role_checks_remain_enforced") is True
        and capability.get("operational_safety_limits_are_not_plan_entitlements") is True,
        "CAPABILITY_DRIFT", "$.uniform_capability_contract", "uniform internal capability contract drift",
    )

    roles = value.get("role_and_sync_contract", {})
    check(
        roles.get("edit_content_crdt_transport_role") == "edit"
        and roles.get("edit_content_may_mutate_cells") is True
        and roles.get("edit_content_may_mutate_schema") is False
        and roles.get("edit_or_full_access_may_mutate_schema") is True
        and roles.get("pending_local_database_blocks_survive_canonical_refresh") is True
        and roles.get("page_switch_fences_stale_database_reload") is True,
        "ROLE_SYNC_DRIFT", "$.role_and_sync_contract", "role separation or canonical synchronization drift",
    )

    lineage = value.get("migration_lineage", {})
    check(
        lineage.get("target_version") == migration.TARGET_VERSION == 2026082410
        and lineage.get("target_name") == migration.TARGET_NAME
        and lineage.get("target_checksum") == migration.TARGET_CHECKSUM
        and lineage.get("target_actual_schema_sha256") == migration.TARGET_ACTUAL_SCHEMA_SHA256,
        "MIGRATION_LINEAGE_DRIFT", "$.migration_lineage", "exact current collaborative schema required",
    )

    for excluded_path in (
        "/api/notion/ai/ask", "/api/notion/outlook/status", "/api/notion/outlook/oauth/start",
        "/public/notion/token-a", "/api/notion/pages/page-a/public_link",
        "/api/notion/pages/page-a/forms/form-a/submit",
    ):
        check(_is_nonadopted_path(excluded_path), "ROUTE_FENCE_DRIFT", excluded_path, "nonadopted path must fail closed")
    for adopted_path in (
        "/", "/api/notion/workspaces/workspace-a/search",
        "/api/notion/workspaces/workspace-a/pages/page-a/databases/database-a",
    ):
        check(not _is_nonadopted_path(adopted_path), "ROUTE_FENCE_OVERREACH", adopted_path, "adopted path must remain reachable")

    ui_markers = value.get("ui_marker_contract", {})
    for relative, markers in ui_markers.items() if isinstance(ui_markers, dict) else []:
        target = _repository_file(relative)
        content = target.read_text(encoding="utf-8") if target is not None else ""
        check(
            target is not None and isinstance(markers, list)
            and all(isinstance(marker, str) and marker in content for marker in markers),
            "UI_MARKER_DRIFT", relative, "required adopted-surface source markers missing",
        )

    receipt_binding = value.get("browser_receipt", {})
    receipt_path = _repository_file(receipt_binding.get("path")) if isinstance(receipt_binding, dict) else None
    receipt: dict[str, Any] = {}
    if receipt_path is not None:
        try:
            loaded_receipt = _load(receipt_path)
            receipt = loaded_receipt if isinstance(loaded_receipt, dict) else {}
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            receipt = {}
    check(
        receipt_path is not None and receipt_binding.get("sha256") == _sha256(receipt_path)
        and receipt.get("receipt_id") == "F0-P5-L4-ADOPTED-SURFACE-20260825"
        and receipt.get("status") == "pass"
        and receipt.get("principal_ids") == ["admin", "user01"]
        and receipt.get("principal_count") == 2
        and receipt.get("adopted_database_views") == EXPECTED_VIEWS
        and receipt.get("multi_principal_cell_round_trip") is True
        and receipt.get("schema_role_controls_fenced") is True
        and receipt.get("canonical_reload_convergence") is True
        and receipt.get("nonadopted_controls_and_deep_links_fenced") is True
        and receipt.get("uniform_plan_or_quota_ui") is True
        and receipt.get("test_page_moved_to_trash") is True
        and receipt.get("browser", {}).get("independent_ephemeral_contexts") == 2
        and receipt.get("browser", {}).get("persistent_or_signed_in_profile_used") is False
        and receipt.get("browser", {}).get("browser_closed") is True
        and receipt.get("credential_cookie_secret_or_session_recorded") is False
        and receipt.get("unapproved_network_target_escape_count") == 0
        and receipt.get("page_error_count") == 0,
        "BROWSER_RECEIPT_DRIFT", "$.browser_receipt", "exact redacted two-principal browser pass required",
    )

    catalogs = value.get("catalog_binding", {})
    loaded_catalogs: dict[str, dict[str, Any]] = {}
    for key in ("packet_catalog", "validation_ladder", "completion_matrix"):
        item = catalogs.get(key, {}) if isinstance(catalogs, dict) else {}
        target = _repository_file(item.get("path")) if isinstance(item, dict) else None
        current: dict[str, Any] = {}
        if target is not None:
            try:
                candidate = _load(target)
                current = candidate if isinstance(candidate, dict) else {}
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                current = {}
        loaded_catalogs[key] = current
        check(target is not None and item.get("sha256") == _sha256(target), "CATALOG_HASH_DRIFT", f"$.catalog_binding.{key}", "exact current catalog hash required")
    packets = loaded_catalogs.get("packet_catalog", {})
    f0_p5 = next((item for item in packets.get("packets", []) if isinstance(item, dict) and item.get("id") == "F0-P5"), {})
    v0r_p1 = next((item for item in packets.get("packets", []) if isinstance(item, dict) and item.get("id") == "V0R-P1"), {})
    v0b_p1 = next((item for item in packets.get("packets", []) if isinstance(item, dict) and item.get("id") == "V0B-P1"), {})
    ladder_next = loaded_catalogs.get("validation_ladder", {}).get("implementation_targets", {}).get("next_packet")
    normal_handoff = packets.get("next_packet") == "O1B-P1" and ladder_next == "O1B-P1"
    corrective_handoff = (
        packets.get("next_packet") == "V0R-P1"
        and ladder_next == "V0R-P1"
        and v0r_p1.get("status") == "implemented_unverified"
        and v0b_p1.get("status") == "implemented_unverified"
    )
    reference_rebound_handoff = (
        packets.get("next_packet") == "V0B-P1"
        and ladder_next == "V0B-P1"
        and v0r_p1.get("status") == "gate_passed"
        and v0b_p1.get("status") == "implemented_unverified"
    )
    check(
        f0_p5.get("status") == "gate_passed"
        and f0_p5.get("matrix_closes") == ["DB-001", "CAP-001"]
        and f0_p5.get("gate_ids") == ["F0-GATE-001"]
        and packets.get("matrix_closure_registry", {}).get("DB-001") == "F0-P5"
        and packets.get("matrix_closure_registry", {}).get("CAP-001") == "F0-P5"
        and (normal_handoff or corrective_handoff or reference_rebound_handoff),
        "CATALOG_STATE_DRIFT", "$.catalog_binding", "F0-P5 gate and a matched normal or fail-closed corrective handoff required",
    )

    validation = value.get("validation", {})
    check(
        validation.get("related_regression", {}).get("passed") == 73
        and validation.get("related_regression", {}).get("failed") == 0
        and validation.get("browser_live", {}).get("passed") == 1
        and validation.get("browser_live", {}).get("failed") == 0
        and validation.get("javascript_syntax") == "pass"
        and validation.get("database_bridge_node") == "pass"
        and validation.get("route_contract_regression", {}).get("passed") == 15
        and validation.get("route_contract_regression", {}).get("failed") == 0
        and validation.get("central_harness_regression", {}).get("passed") == 32
        and validation.get("central_harness_regression", {}).get("failed") == 0
        and validation.get("development_ports") == [10111, 10112, 10113]
        and validation.get("development_database_integrity") == "ok"
        and validation.get("development_foreign_key_errors") == 0
        and validation.get("development_schema_version") == 2026082410
        and validation.get("production_10011_10012_changed") is False,
        "VALIDATION_DRIFT", "$.validation", "exact F0-P5 measured evidence required",
    )

    sbe = value.get("second_brain_fence", {})
    check(
        sbe.get("protected_input_sha256") == "sha256:0beefd78448cdd19767be0e0276fdbf6951ff417c39c135105e7754d755df494"
        and sbe.get("goal_count") == 20 and sbe.get("scenario_count") == 284
        and sbe.get("protected_second_brain_changed") is False,
        "SECOND_BRAIN_FENCE_DRIFT", "$.second_brain_fence", "protected Second Brain boundary drift",
    )

    _verify_manifest(value.get("source_manifest"), EXPECTED_SOURCE_PATHS, "$.source_manifest", errors)
    _verify_manifest(value.get("test_manifest"), EXPECTED_TEST_PATHS, "$.test_manifest", errors)

    claims = value.get("claims", {})
    check(
        claims.get("F0_P5_gate_passed") is True
        and claims.get("F0_GATE_001_closed") is True
        and claims.get("DB_001_closed") is True
        and claims.get("CAP_001_closed") is True
        and claims.get("A11Y_001_closed") is False
        and claims.get("PIX_001_closed") is False
        and claims.get("production_changed") is False
        and claims.get("product_complete") is False
        and claims.get("active_shared_goal_complete") is False,
        "CLAIMS_DRIFT", "$.claims", "F0 closure and remaining product incompleteness required",
    )

    return {
        "contract": "code_assist.notion.internal_collaborative_adopted_surface.audit.v1",
        "contract_sha256": _sha256(path),
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "errors": errors,
        "gate_passed": not errors,
        "source_count": len(EXPECTED_SOURCE_PATHS),
        "test_count": len(EXPECTED_TEST_PATHS),
        "adopted_feature_count": len(EXPECTED_ADOPTED),
        "product_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    args = parser.parse_args(argv)
    report = verify(args.contract)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("status") == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Strict offline verifier for the NP2C-5a Archive characterization matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "np2c_archive_live_characterization_matrix.json"
)

STATUS = "prepared_not_authorized_not_executed"
PREDECESSOR_STATUS = (
    "official_archive_source_contract_designed_source_v5_and_workspace_1318_"
    "blocked_default_off"
)
SCENARIO_IDS = (
    "NP2-ARCHIVE-PARENT-001",
    "NP2-ARCHIVE-NESTED-001",
    "NP2-ARCHIVE-CREATE-MOVE-001",
    "NP2-ARCHIVE-DUPLICATE-001",
    "NP2-ARCHIVE-TRASH-001",
    "NP2-ARCHIVE-RESTORE-001",
    "NP2-ARCHIVE-EDIT-001",
    "NP2-ARCHIVE-NAVIGATION-001",
    "NP2-ARCHIVE-DATABASE-001",
    "NP2-ARCHIVE-REFERENCES-001",
    "NP2-ARCHIVE-PERMISSION-001",
    "NP2-ARCHIVE-PLAN-LOSS-001",
    "NP2-ARCHIVE-PUBLIC-001",
    "NP2-ARCHIVE-PLATFORM-001",
    "NP2-ARCHIVE-CONCURRENCY-001",
    "NP2-ARCHIVE-DIRECT-LINK-001",
)
PROFILE_IDS = (
    "business_owner_web_ko",
    "business_member_web_ko",
    "business_guest_editor_web_ko",
    "business_guest_commenter_web_ko",
    "business_guest_viewer_web_ko",
    "enterprise_admin_web_en",
    "enterprise_member_windows_ko",
    "enterprise_member_macos_en",
    "business_member_ios_ko",
    "enterprise_member_android_en",
    "public_anonymous_web_en",
)
REFERENCE_SOURCES = (
    (
        "SRC-NOTION-ARCHIVE",
        "official_help",
        "https://www.notion.com/help/archive-pages",
    ),
    (
        "SRC-NOTION-DELETE-RESTORE",
        "official_help",
        "https://www.notion.com/en-gb/help/duplicate-delete-and-restore-content",
    ),
    (
        "SRC-NOTION-ARCHIVE-RELEASE",
        "official_release",
        "https://www.notion.com/releases/2026-03-27",
    ),
    (
        "SRC-NOTION-PAGE-OBJECT",
        "official_developer",
        "https://developers.notion.com/reference/page",
    ),
    (
        "SRC-NOTION-PAGE-UPDATE",
        "official_developer",
        "https://developers.notion.com/reference/patch-page",
    ),
)
REFERENCE_ONLY_SCENARIOS = {
    "NP2-ARCHIVE-PARENT-001",
    "NP2-ARCHIVE-NAVIGATION-001",
    "NP2-ARCHIVE-DATABASE-001",
    "NP2-ARCHIVE-PERMISSION-001",
    "NP2-ARCHIVE-DIRECT-LINK-001",
}
SCENARIO_CONTRACT = {
    "NP2-ARCHIVE-PARENT-001": (
        "subtree",
        ("root_lifecycle", "descendant_lifecycle", "archive_provenance", "direct_link_access", "default_visibility"),
    ),
    "NP2-ARCHIVE-NESTED-001": (
        "nested_state",
        ("nested_provenance", "unarchive_cohort", "independent_child_state", "inherited_child_action", "ancestor_state"),
    ),
    "NP2-ARCHIVE-CREATE-MOVE-001": (
        "structural_interaction",
        ("create_permission", "created_page_state", "move_in_state", "move_out_state", "placement_visibility", "link_stability"),
    ),
    "NP2-ARCHIVE-DUPLICATE-001": (
        "duplicate_interaction",
        ("duplicate_root_state", "duplicate_child_state", "new_identity", "subtree_shape", "default_visibility", "link_behavior"),
    ),
    "NP2-ARCHIVE-TRASH-001": (
        "trash_interaction",
        ("archive_to_trash_transition", "editability", "direct_link_access", "archive_view_membership", "trash_view_membership", "subtree_effect"),
    ),
    "NP2-ARCHIVE-RESTORE-001": (
        "restore_interaction",
        ("restored_archive_state", "restored_placement", "descendant_state", "direct_link_access", "default_visibility", "editability"),
    ),
    "NP2-ARCHIVE-EDIT-001": (
        "content_interaction",
        ("open_outcome", "edit_outcome", "comment_outcome", "mention_outcome", "notification_outcome", "archive_banner"),
    ),
    "NP2-ARCHIVE-NAVIGATION-001": (
        "visibility",
        ("sidebar_visibility", "default_search_visibility", "archive_filter_visibility", "library_visibility", "enterprise_content_search_visibility", "breadcrumb_behavior"),
    ),
    "NP2-ARCHIVE-DATABASE-001": (
        "database_visibility",
        ("default_view_visibility", "archived_view_visibility", "filter_behavior", "property_preservation", "relation_rollup_behavior", "direct_link_access"),
    ),
    "NP2-ARCHIVE-REFERENCES-001": (
        "reference_graph",
        ("backlink_behavior", "relation_behavior", "rollup_behavior", "mention_behavior", "synced_block_behavior", "access_leakage"),
    ),
    "NP2-ARCHIVE-PERMISSION-001": (
        "authorization",
        ("archive_authority", "unarchive_authority", "denial_surface", "partial_mutation", "admin_scope", "guest_scope"),
    ),
    "NP2-ARCHIVE-PLAN-LOSS-001": (
        "entitlement",
        ("feature_availability", "existing_archive_state", "direct_link_access", "unarchive_availability", "default_visibility", "data_loss"),
    ),
    "NP2-ARCHIVE-PUBLIC-001": (
        "public_visibility",
        ("public_link_reachability", "published_site_visibility", "child_visibility", "cache_behavior", "unarchive_recovery", "metadata_leakage"),
    ),
    "NP2-ARCHIVE-PLATFORM-001": (
        "platform_variance",
        ("surface_availability", "sidebar_visibility", "search_visibility", "archive_banner", "action_availability", "focus_and_accessibility"),
    ),
    "NP2-ARCHIVE-CONCURRENCY-001": (
        "concurrency_and_recovery",
        ("acknowledged_result", "partial_state", "subtree_consistency", "permission_loss_result", "reload_convergence", "retry_surface"),
    ),
    "NP2-ARCHIVE-DIRECT-LINK-001": (
        "addressability",
        ("authorized_link_access", "unauthorized_link_access", "url_stability", "archive_banner", "default_navigation_absence", "metadata_leakage"),
    ),
}
REQUIRED_MANIFEST_FIELDS = (
    "schema_version",
    "scenario_id",
    "observation_state",
    "promotion_status",
    "reference_date",
    "reference_sources",
    "plan",
    "role",
    "platform",
    "app_or_browser_version",
    "locale",
    "input_sha256",
    "result_sha256",
    "evidence",
    "reference_conflicts",
    "reviewer",
    "reviewed_at",
)


class DuplicateJsonKeyError(ValueError):
    pass


def _object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJsonKeyError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def load_matrix(path: str | Path = DEFAULT_MATRIX) -> dict[str, Any]:
    value = json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=_object,
        parse_constant=lambda item: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {item}")
        ),
    )
    if not isinstance(value, dict):
        raise ValueError("Archive characterization matrix root must be an object")
    return value


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def validate_matrix(value: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(
        set(value) == {
            "contract_version",
            "name",
            "status",
            "reference_state",
            "reference_date",
            "timezone",
            "predecessor",
            "authorization",
            "claims",
            "reference_sources",
            "profiles",
            "fixture_contract",
            "evidence_contract",
            "required_scenario_ids",
            "scenarios",
            "decision_gate",
        },
        "matrix root keys are invalid",
    )
    require(value.get("contract_version") == 1, "contract_version must be 1")
    require(
        value.get("name") == "np2c5a_official_archive_live_characterization_matrix",
        "matrix name is invalid",
    )
    require(value.get("status") == STATUS, "matrix must remain prepared and unexecuted")
    require(
        value.get("reference_state")
        == "official_reference_only_live_characterization_pending",
        "live characterization must remain pending",
    )
    require(value.get("reference_date") == "2026-08-14", "reference date is invalid")
    require(value.get("timezone") == "Asia/Seoul", "timezone is invalid")

    predecessor = value.get("predecessor")
    require(isinstance(predecessor, dict), "predecessor must be an object")
    if isinstance(predecessor, dict):
        require(
            predecessor
            == {
                "page_tree_contract_version": 19,
                "page_tree_contract_status": PREDECESSOR_STATUS,
                "current_workspace_schema_version": 2026081317,
                "h5e_reserved_source_version": 2026081304,
                "candidate_archive_source_version": 2026081305,
                "candidate_archive_workspace_version": 2026081318,
                "source_or_catalog_archive_implementation_exists": False,
            },
            "predecessor lineage is invalid",
        )

    authorization = value.get("authorization")
    require(isinstance(authorization, dict), "authorization must be an object")
    if isinstance(authorization, dict):
        for field in (
            "explicit_live_execution_approval_required",
            "synthetic_fixture_only",
            "normal_public_ui_only",
            "evidence_root_must_be_outside_repository",
        ):
            require(authorization.get(field) is True, f"{field} must be true")
        for field in (
            "explicit_live_execution_approval_granted",
            "approved_business_or_enterprise_test_tenant_available",
            "production_or_personal_workspace_allowed",
            "public_or_private_api_capture_allowed",
            "private_endpoint_or_network_payload_capture_allowed",
            "scraping_or_rate_limit_bypass_allowed",
            "raw_artifacts_may_be_committed",
            "source_catalog_or_runtime_mutation_authorized",
        ):
            require(authorization.get(field) is False, f"{field} must be false")
        require(len(authorization) == 12, "authorization keys are invalid")

    claims = value.get("claims")
    require(isinstance(claims, dict), "claims must be an object")
    if isinstance(claims, dict):
        require(
            set(claims)
            == {
                "live_characterized",
                "differential_passed",
                "source_v5_implemented",
                "workspace_1318_implemented",
                "runtime_archive_enabled",
                "np2c5a_complete",
                "np2c_complete",
                "overall_parity_complete",
            },
            "claim keys are invalid",
        )
        for field, item in claims.items():
            require(item is False, f"claim {field} must be false")

    sources = value.get("reference_sources")
    require(isinstance(sources, list), "reference_sources must be an array")
    if isinstance(sources, list):
        normalized_sources = tuple(
            (item.get("id"), item.get("kind"), item.get("url"))
            for item in sources
            if isinstance(item, dict) and set(item) == {"id", "kind", "url"}
        )
        require(normalized_sources == REFERENCE_SOURCES, "official reference sources are invalid")

    profiles = value.get("profiles")
    require(isinstance(profiles, list), "profiles must be an array")
    profile_ids: list[str] = []
    if isinstance(profiles, list):
        for item in profiles:
            require(
                isinstance(item, dict)
                and set(item) == {"id", "plan", "role", "platform", "locale"},
                "profile shape is invalid",
            )
            if not isinstance(item, dict):
                continue
            profile_ids.append(str(item.get("id")))
            plan = item.get("plan")
            require(plan in {"business", "enterprise", "not_applicable"}, "profile plan is invalid")
            if item.get("role") == "public_anonymous":
                require(plan == "not_applicable", "public profile plan is invalid")
            else:
                require(plan in {"business", "enterprise"}, "signed-in profile requires Business or Enterprise")
            require(
                item.get("platform")
                in {"desktop_web", "desktop_app_windows", "desktop_app_macos", "ios_app", "android_app", "public_web"},
                "profile platform is invalid",
            )
            require(re.fullmatch(r"[a-z]{2}-[A-Z]{2}", str(item.get("locale"))) is not None, "profile locale is invalid")
        require(tuple(profile_ids) == PROFILE_IDS, "profile matrix is incomplete or reordered")
        require(len(profile_ids) == len(set(profile_ids)), "profile ids must be unique")

    fixture = value.get("fixture_contract")
    require(isinstance(fixture, dict), "fixture_contract must be an object")
    if isinstance(fixture, dict):
        for field in (
            "tenant_is_synthetic_and_dedicated",
            "fixture_manifest_required",
            "fixture_manifest_sha256_required",
            "fixture_ids_are_placeholders_only",
            "cleanup_must_be_reviewed_before_execution",
        ):
            require(fixture.get(field) is True, f"fixture {field} must be true")
        for field in (
            "real_user_or_organization_content_allowed",
            "cross_run_real_identifier_linkage_allowed",
            "source_catalog_or_local_user_data_path_allowed",
        ):
            require(fixture.get(field) is False, f"fixture {field} must be false")
        require(len(fixture) == 8, "fixture contract keys are invalid")

    evidence = value.get("evidence_contract")
    require(isinstance(evidence, dict), "evidence_contract must be an object")
    if isinstance(evidence, dict):
        require(evidence.get("policy") == "docs/notion_parity/EVIDENCE_POLICY.md", "evidence policy path is invalid")
        require(tuple(evidence.get("required_manifest_fields", ())) == REQUIRED_MANIFEST_FIELDS, "manifest fields are invalid")
        require(evidence.get("manifest_observation_state_after_review") == "live-characterized", "reviewed observation state is invalid")
        require(evidence.get("manifest_promotion_status_after_review") == "live_characterized", "reviewed promotion status is invalid")
        for field in (
            "one_manifest_per_scenario_profile_run",
            "redaction_validation_required",
            "independent_reviewer_required",
            "input_and_result_sha256_required",
            "unobserved_dimensions_remain_unknown",
        ):
            require(evidence.get(field) is True, f"evidence {field} must be true")
        for field in (
            "raw_dom_network_cookie_token_or_user_content_in_repository_allowed",
            "automatic_phase_or_implementation_promotion_allowed",
        ):
            require(evidence.get(field) is False, f"evidence {field} must be false")
        require(len(evidence) == 11, "evidence contract keys are invalid")

    required_ids = value.get("required_scenario_ids")
    require(tuple(required_ids or ()) == SCENARIO_IDS, "required scenario ids are invalid")
    scenarios = value.get("scenarios")
    require(isinstance(scenarios, list), "scenarios must be an array")
    seen_ids: list[str] = []
    used_profiles: set[str] = set()
    if isinstance(scenarios, list):
        for scenario in scenarios:
            require(
                isinstance(scenario, dict)
                and set(scenario)
                == {
                    "id",
                    "category",
                    "title",
                    "reference_source_ids",
                    "profile_ids",
                    "fixture_id",
                    "actions",
                    "observation_dimensions",
                    "observation_state",
                    "promotion_status",
                    "live_evidence_manifest_paths",
                    "must_not_infer",
                },
                "scenario shape is invalid",
            )
            if not isinstance(scenario, dict):
                continue
            scenario_id = str(scenario.get("id"))
            seen_ids.append(scenario_id)
            contract = SCENARIO_CONTRACT.get(scenario_id)
            require(contract is not None, "scenario id is not allowlisted")
            if contract is not None:
                require(scenario.get("category") == contract[0], f"{scenario_id} category is invalid")
                require(tuple(scenario.get("observation_dimensions", ())) == contract[1], f"{scenario_id} observations are incomplete")
            require(isinstance(scenario.get("title"), str) and bool(scenario.get("title")), f"{scenario_id} title is invalid")
            source_ids = scenario.get("reference_source_ids")
            require(
                isinstance(source_ids, list)
                and bool(source_ids)
                and len(source_ids) == len(set(source_ids))
                and set(source_ids).issubset({item[0] for item in REFERENCE_SOURCES}),
                f"{scenario_id} reference sources are invalid",
            )
            selected_profiles = scenario.get("profile_ids")
            require(
                isinstance(selected_profiles, list)
                and bool(selected_profiles)
                and len(selected_profiles) == len(set(selected_profiles))
                and set(selected_profiles).issubset(set(PROFILE_IDS)),
                f"{scenario_id} profiles are invalid",
            )
            if isinstance(selected_profiles, list):
                used_profiles.update(str(item) for item in selected_profiles)
            require(re.fullmatch(r"FX-[A-Z0-9]+(?:-[A-Z0-9]+)*", str(scenario.get("fixture_id"))) is not None, f"{scenario_id} fixture id is invalid")
            actions = scenario.get("actions")
            require(isinstance(actions, list) and 1 <= len(actions) <= 12, f"{scenario_id} actions are invalid")
            if isinstance(actions, list):
                for index, action in enumerate(actions, start=1):
                    require(
                        isinstance(action, dict)
                        and set(action) == {"step", "actor", "operation", "target"}
                        and action.get("step") == index
                        and re.fullmatch(r"[a-z][a-z0-9_]*", str(action.get("actor"))) is not None
                        and re.fullmatch(r"[a-z][a-z0-9_]*", str(action.get("operation"))) is not None
                        and re.fullmatch(r"[a-z][a-z0-9_]*", str(action.get("target"))) is not None,
                        f"{scenario_id} action sequence is invalid",
                    )
            expected_state = "reference-only" if scenario_id in REFERENCE_ONLY_SCENARIOS else "unknown"
            require(scenario.get("observation_state") == expected_state, f"{scenario_id} state must remain {expected_state}")
            require(scenario.get("promotion_status") is None, f"{scenario_id} must not be promoted")
            require(scenario.get("live_evidence_manifest_paths") == [], f"{scenario_id} must not claim live evidence")
            require(
                isinstance(scenario.get("must_not_infer"), str)
                and len(str(scenario.get("must_not_infer"))) >= 20,
                f"{scenario_id} must preserve a no-inference rule",
            )
            if scenario_id == "NP2-ARCHIVE-PUBLIC-001":
                require(
                    scenario.get("must_not_infer")
                    == "No public or published-link behavior is assumed from private workspace links.",
                    "public or published-link behavior must remain unknown",
                )
        require(tuple(seen_ids) == SCENARIO_IDS, "scenario list is incomplete or reordered")
        require(len(seen_ids) == len(set(seen_ids)), "scenario ids must be unique")
        require(used_profiles == set(PROFILE_IDS), "not every required profile is covered")

    decision = value.get("decision_gate")
    require(isinstance(decision, dict), "decision_gate must be an object")
    if isinstance(decision, dict):
        for field in (
            "all_required_scenarios_must_have_reviewed_live_evidence",
            "all_required_profiles_or_explicit_unknown_exceptions_must_be_accounted_for",
            "reference_conflicts_must_remain_explicit",
            "unobserved_dimensions_must_remain_unknown",
            "reviewed_semantic_decision_record_required",
        ):
            require(decision.get(field) is True, f"decision gate {field} must be true")
        for field in (
            "implementation_or_schema_work_before_gate_allowed",
            "automatic_machine_contract_promotion_allowed",
        ):
            require(decision.get(field) is False, f"decision gate {field} must be false")
        require(
            decision.get("next_gate_after_acceptance")
            == "NP2C-5b-exact-v4-to-v5-archive-source-schema-and-route-unregistered-adapter",
            "next gate after acceptance is invalid",
        )
        require(len(decision) == 8, "decision gate keys are invalid")

    matrix_strings = tuple(_strings(value))
    require(
        not any(
            re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|/Users/|/home/)", item)
            for item in matrix_strings
        ),
        "matrix contains an absolute or UNC path",
    )
    require(
        not any(
            re.search(r"(?i)(?:authorization:\s*bearer|cookie\s*=|token=)", item)
            for item in matrix_strings
        ),
        "matrix contains a secret-like value",
    )

    unique_errors = sorted(set(errors))
    return {
        "valid": not unique_errors,
        "error_count": len(unique_errors),
        "errors": unique_errors,
        "status": value.get("status"),
        "scenario_count": len(scenarios) if isinstance(scenarios, list) else 0,
        "profile_count": len(profiles) if isinstance(profiles, list) else 0,
        "live_execution_authorized": bool(
            authorization.get("explicit_live_execution_approval_granted")
        )
        if isinstance(authorization, dict)
        else False,
        "source_or_catalog_implementation_authorized": bool(
            authorization.get("source_catalog_or_runtime_mutation_authorized")
        )
        if isinstance(authorization, dict)
        else False,
    }


def verify_matrix(path: str | Path = DEFAULT_MATRIX) -> dict[str, Any]:
    try:
        return validate_matrix(load_matrix(path))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "error_count": 1,
            "errors": [f"matrix could not be verified: {type(exc).__name__}"],
            "status": None,
            "scenario_count": 0,
            "profile_count": 0,
            "live_execution_authorized": False,
            "source_or_catalog_implementation_authorized": False,
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX))
    args = parser.parse_args(argv)
    report = verify_matrix(args.matrix)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    sys.exit(main())

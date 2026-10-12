"""Strict read-only verifier for the NP-2B navigation design contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = PROJECT_ROOT / "docs" / "notion_parity" / "np2b_navigation_contract.json"


class DuplicateJsonKeyError(ValueError):
    pass


def _object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_contract(path: str | Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    value = json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=_object,
        parse_constant=lambda item: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {item}")
        ),
    )
    if not isinstance(value, dict):
        raise ValueError("NP-2B contract root must be an object")
    return value


def validate_contract(value: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(value.get("contract_version") == 4, "contract_version must be 4")
    require(value.get("name") == "np2b_server_backed_navigation_state", "invalid name")
    require(
        value.get("status") == "ui_contract_tested_default_off",
        "status must identify the contract-tested default-off UI slice",
    )
    require(
        value.get("reference_state")
        == "official_reference_only_live_characterization_pending",
        "live characterization must remain pending",
    )

    implementation = value.get("implementation")
    require(isinstance(implementation, dict), "implementation must be an object")
    if isinstance(implementation, dict):
        require(
            implementation.get("workspace_schema_version") == 2026081310,
            "workspace schema must be 2026081310",
        )
        for field in (
            "backend_schema_implemented",
            "explicit_migration_implemented",
            "backend_store_api_implemented",
            "backend_foundation_contract_tested",
            "route_contract_tested",
            "routes_enabled",
            "ui_implemented",
            "ui_contract_tested",
        ):
            require(implementation.get(field) is True, f"{field} must be true")
        for field in (
            "migration_applied_to_live_data",
            "ui_enabled",
            "recents_write_enabled",
            "resolver_cutover_enabled",
        ):
            require(implementation.get(field) is False, f"{field} must be false")

    schema = value.get("schema_contract")
    require(isinstance(schema, dict), "schema_contract must be an object")
    if isinstance(schema, dict):
        require(schema.get("source_version") == 2026081309, "source version is invalid")
        require(schema.get("target_version") == 2026081310, "target version is invalid")
        expected_digests = {
            "source_checksum": "sha256:ecc2a8b40e8d114015e05b1772701dd97bb99ac513db9fd041626408f5f127ec",
            "source_actual_schema_sha256": "sha256:d79ecc5b79faacf3ca4db288e7978b8192df91b8c3f9e1d5a99f0d859e2e06b1",
            "target_checksum": "sha256:72058812be6f0b92db756d5648d2cd9a0741d4ad4f4f9c62377d128ca0b581b3",
            "target_actual_schema_sha256": "sha256:d8935af134fa12cdd5f0f803e15cba798dece1831cfeeee7673e0dc0ef7d7b33",
        }
        for field, expected_digest in expected_digests.items():
            item = schema.get(field)
            require(
                isinstance(item, str)
                and len(item) == 71
                and item.startswith("sha256:")
                and all(ch in "0123456789abcdef" for ch in item[7:])
                and item == expected_digest,
                f"{field} must match the frozen schema digest",
            )
        require(
            schema.get("request_or_startup_migration_enabled") is False,
            "request/startup migration must remain disabled",
        )

    first_slice = value.get("first_slice")
    require(isinstance(first_slice, dict), "first_slice must be an object")
    if isinstance(first_slice, dict):
        require(first_slice.get("id") == "NP2B-1", "first slice must be NP2B-1")
        features = set(first_slice.get("features", []))
        for feature in (
            "sidebar_width_and_collapsed_state",
            "sidebar_section_preferences",
            "page_favorites",
        ):
            require(feature in features, f"missing first-slice feature: {feature}")
        excluded = set(first_slice.get("excluded", []))
        for feature in (
            "recents_view_events",
            "tree_lifecycle_mutations",
            "runtime_ui_registration",
            "resolver_cutover",
        ):
            require(feature in excluded, f"missing first-slice exclusion: {feature}")

    authorization = value.get("authorization")
    require(isinstance(authorization, dict), "authorization must be an object")
    if isinstance(authorization, dict):
        require(
            authorization.get("workspace_active_member_required") is True,
            "active membership must be required",
        )
        require(
            authorization.get("guest_navigation_state_allowed") is False,
            "guest navigation state must be denied",
        )
        require(
            authorization.get("current_membership_rechecked_on_replay") is True,
            "replay must recheck current membership",
        )
        require(
            authorization.get("invisible_favorite_add_returns_not_found") is True,
            "invisible favorite add must be not-found",
        )
        require(
            authorization.get("owned_stale_favorite_remove_allowed") is True,
            "stale owned favorite must remain removable",
        )

    identity = value.get("canonical_identity")
    require(isinstance(identity, dict), "canonical_identity must be an object")
    if isinstance(identity, dict):
        require(
            identity.get("favorite_key")
            == [
                "workspace_id",
                "subject_id",
                "source_owner_subject_id",
                "source_page_id",
            ],
            "favorite identity is invalid",
        )
        require(
            identity.get("binding_generation_in_favorite_key") is False,
            "favorite identity must survive safe binding generations",
        )
        require(
            identity.get("visibility_recomputed_on_every_read") is True,
            "favorite reads must recheck visibility",
        )
        require(
            identity.get("hidden_rows_omitted_and_dense_reindexed") is True,
            "hidden rows must not leak ordinal gaps",
        )

    mutation = value.get("mutation_invariants")
    require(isinstance(mutation, dict), "mutation_invariants must be an object")
    if isinstance(mutation, dict):
        for field in (
            "strict_json_object_required",
            "duplicate_json_keys_rejected",
            "nonfinite_json_numbers_rejected",
            "operation_id_required",
            "request_hash_conflict_required",
            "operation_audit_outbox_atomic",
            "preference_and_favorite_versions_independent",
        ):
            require(mutation.get(field) is True, f"{field} must be true")
        require(
            mutation.get("navigation_json_max_bytes") == 65536,
            "navigation JSON must be bounded to 65536 bytes",
        )
        for field in (
            "workspace_row_version_bumped",
            "workspace_security_epoch_bumped",
            "source_io_inside_write_transaction",
            "network_io_inside_write_transaction",
        ):
            require(mutation.get(field) is False, f"{field} must be false")

    read = value.get("read_invariants")
    require(isinstance(read, dict), "read_invariants must be an object")
    if isinstance(read, dict):
        require(read.get("legacy_directory_scan_count") == 0, "directory scans are forbidden")
        require(read.get("legacy_source_database_open_count") == 0, "source DB opens are forbidden")
        require(read.get("current_visibility_filter_required") is True, "visibility filter is required")

    sections = value.get("section_allowlist")
    require(
        sections == ["favorites", "recents", "teamspaces", "shared", "private"],
        "section allowlist or order is invalid",
    )

    route_contract = value.get("route_contract")
    require(isinstance(route_contract, dict), "route_contract must be an object")
    if isinstance(route_contract, dict):
        require(
            route_contract
            == {
                "contract_id": "notion-workspace-route-contract-np2a",
                "expected_rule_count": 12,
                "expected_operation_count": 19,
                "required_hard_operation_count": 19,
            },
            "route contract counts or identity are invalid",
        )

    ui_contract = value.get("ui_contract")
    require(isinstance(ui_contract, dict), "ui_contract must be an object")
    if isinstance(ui_contract, dict):
        require(ui_contract.get("slice_id") == "NP2B-3", "UI slice must be NP2B-3")
        require(
            ui_contract.get("implementation_status")
            == "implemented_contract_tested_default_off",
            "UI must remain contract-tested and default-off",
        )
        require(
            ui_contract.get("server_capability_key") == "navigation_ui",
            "UI capability key is invalid",
        )
        require(
            ui_contract.get("enable_environment_variable")
            == "CODE_ASSIST_NP2B_NAVIGATION_UI",
            "UI enable variable is invalid",
        )
        require(
            ui_contract.get("kill_switch_environment_variable")
            == "CODE_ASSIST_NP2B_NAVIGATION_UI_KILL_SWITCH",
            "UI kill-switch variable is invalid",
        )
        for field in (
            "kill_switch_overrides_enable",
            "kill_switch_nonzero_or_unknown_value_fails_closed",
            "legacy_ui_unchanged_when_disabled",
            "server_snapshot_overrides_local_cache",
            "snapshot_subject_identity_required",
            "duplicate_workspace_identity_rejected",
            "malformed_canonical_payload_fails_closed_without_blocking_legacy_content",
            "member_workspaces_only",
            "guest_workspaces_excluded",
            "draft_flush_before_workspace_switch",
            "stale_response_fencing_required",
            "mutation_failure_requires_canonical_reload_or_local_rollback",
            "hidden_or_revoked_favorites_removed_by_canonical_reload",
            "authoritative_empty_state_suppresses_local_cache",
            "membership_revoke_fails_closed",
            "capability_recheck_failure_fails_closed",
            "section_keyboard_controls_required",
            "section_focus_restore_required",
            "sidebar_resize_keyboard_required",
            "narrow_viewport_resize_disabled",
        ):
            require(ui_contract.get(field) is True, f"{field} must be true")
        for field in (
            "default_enabled",
            "content_resolver_switched",
            "offline_action_queue_enabled",
            "recents_write_enabled",
        ):
            require(ui_contract.get(field) is False, f"{field} must be false")
        require(
            ui_contract.get("maximum_navigation_mutations_in_flight") == 1,
            "navigation mutations must be serialized",
        )
        require(
            ui_contract.get("network_uncertain_replay_attempts") == 1,
            "network uncertainty must use one exact-operation replay attempt",
        )

    routes = value.get("routes")
    require(isinstance(routes, list) and len(routes) == 4, "four routes are required")
    if isinstance(routes, list):
        require(
            routes
            == [
                {
                    "method": "GET",
                    "path": "/api/notion/workspaces/<workspace_id>/navigation",
                },
                {
                    "method": "PATCH",
                    "path": "/api/notion/workspaces/<workspace_id>/navigation/preferences",
                },
                {
                    "method": "PUT",
                    "path": "/api/notion/workspaces/<workspace_id>/navigation/favorites",
                },
                {
                    "method": "DELETE",
                    "path": "/api/notion/workspaces/<workspace_id>/navigation/favorites",
                },
            ],
            "navigation routes are invalid",
        )

    forbidden = set(value.get("public_forbidden_data", []))
    for item in (
        "page_content",
        "source_path",
        "file_name",
        "raw_public_token",
        "lease_token",
        "hidden_favorite_count",
        "hidden_favorite_ordinal_gap",
    ):
        require(item in forbidden, f"public redaction omits {item}")

    recents = set(value.get("recents_decision_gates", []))
    for item in (
        "immutable_audit_and_outbox_retention",
        "view_event_coalescing_window",
        "trusted_clock_and_multi_device_ordering",
        "maximum_rows_and_pruning_authority",
        "archive_delete_revoke_visibility_and_retention",
    ):
        require(item in recents, f"recents gate omits {item}")

    return {
        "valid": not errors,
        "error_count": len(errors),
        "errors": errors,
        "status": value.get("status"),
        "backend_schema_implemented": (
            implementation.get("backend_schema_implemented")
            if isinstance(implementation, dict)
            else None
        ),
        "backend_store_api_implemented": (
            implementation.get("backend_store_api_implemented")
            if isinstance(implementation, dict)
            else None
        ),
        "routes_enabled": implementation.get("routes_enabled") if isinstance(implementation, dict) else None,
        "ui_enabled": implementation.get("ui_enabled") if isinstance(implementation, dict) else None,
        "recents_write_enabled": (
            implementation.get("recents_write_enabled")
            if isinstance(implementation, dict)
            else None
        ),
    }


def verify_contract(path: str | Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    try:
        return validate_contract(load_contract(path))
    except Exception as exc:
        return {
            "valid": False,
            "error_count": 1,
            "errors": [str(exc)],
            "status": None,
            "backend_schema_implemented": None,
            "backend_store_api_implemented": None,
            "routes_enabled": None,
            "ui_enabled": None,
            "recents_write_enabled": None,
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    args = parser.parse_args(argv)
    report = verify_contract(args.contract)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())

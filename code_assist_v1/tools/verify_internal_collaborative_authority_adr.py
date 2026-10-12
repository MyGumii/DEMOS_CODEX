"""Verify the A0A-P1 single-authority and permission-epoch ADR.

The verifier is intentionally read-only.  It checks the checked-in ADR against
its strict schema, current source/SBE hashes, source-level current-state probes,
the packet catalog, and the declared L2 substrate lane.  It never imports a web
application or opens a runtime data root.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from code_assist_v1.tools.internal_collaborative_validation import (
    DEFAULT_CATALOG,
    DEFAULT_LADDER,
    PROJECT_ROOT,
    repository_path,
    sha256_file,
)
from code_assist_v1.tools.verify_notion_parity_catalog import (
    JsonSchemaValidator,
    load_json,
)


REPORT_SCHEMA_VERSION = 1
CONTRACT_ROOT = PROJECT_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
DEFAULT_ADR = CONTRACT_ROOT / "internal_collaborative_authority_adr.v1.json"
DEFAULT_SCHEMA = CONTRACT_ROOT / "internal_collaborative_authority_adr.v1.schema.json"

EXPECTED_PLANNING_PATHS = (
    "code_assist_v1/docs/notion_parity/core_free_web_ui/INTERNAL_COLLABORATIVE_NOTION_SCOPE_V2.md",
    "code_assist_v1/docs/notion_parity/core_free_web_ui/INTERNAL_COLLABORATIVE_NOTION_ULTRA_EXECUTION_PLAN_V3.md",
    "code_assist_v1/docs/notion_parity/core_free_web_ui/INTERNAL_COLLABORATIVE_NOTION_XHIGH_IMPLEMENTATION_HANDOFF_V2.md",
)
EXPECTED_SOURCE_PATHS = (
    "code_assist_v1/app_notion.py",
    "code_assist_v1/routes_notion.py",
    "code_assist_v1/routes_notion_workspace.py",
    "code_assist_v1/notion_store.py",
    "code_assist_v1/notion_workspace_store.py",
    "code_assist_v1/notion_workspace_pages.py",
    "code_assist_v1/notion_publication.py",
    "code_assist_v1/knowledge_core/services/publication.py",
)
EXPECTED_SBE_PATHS = (
    "code_assist_v1/docs/knowledge_spaces/README.md",
    "code_assist_v1/docs/knowledge_spaces/second_brain_evolution/README.md",
    "code_assist_v1/docs/knowledge_spaces/second_brain_evolution/PRODUCT_CONTRACT.md",
    "code_assist_v1/docs/knowledge_spaces/second_brain_evolution/FORWARD_SCHEMA.md",
    "code_assist_v1/docs/knowledge_spaces/second_brain_evolution/EXECUTION_PLAN.md",
    "code_assist_v1/docs/knowledge_spaces/second_brain_evolution/ACCEPTANCE.md",
    "code_assist_v1/docs/knowledge_spaces/DECISIONS.md",
    "code_assist_v1/docs/knowledge_spaces/ARCHITECTURE.md",
    "code_assist_v1/docs/knowledge_spaces/SCHEMA_V1.md",
    "code_assist_v1/docs/knowledge_spaces/API_UI_CONTRACT.md",
    "code_assist_v1/docs/knowledge_spaces/CURRENT_SYSTEM_INVENTORY.md",
    "code_assist_v1/docs/knowledge_spaces/MIGRATION_ROLLOUT.md",
    "code_assist_v1/docs/knowledge_spaces/TEST_OPERATIONS.md",
    "code_assist_v1/docs/knowledge_spaces/DEVELOPMENT_BASELINE.md",
    "code_assist_v1/docs/knowledge_spaces/IMPLEMENTATION_BACKLOG.md",
    "code_assist_v1/docs/knowledge_spaces/TRACEABILITY.md",
    "code_assist_v1/docs/knowledge_spaces/GOAL_CONTRACT.json",
    "code_assist_v1/docs/knowledge_spaces/STATUS.md",
)
EXPECTED_ROUTE_IDS = {
    "legacy_page_api",
    "workspace_api",
    "second_brain_publication_api",
    "public_link_api",
    "shared_identity_session",
    "collaboration_gateway",
}
EXPECTED_ATOMIC_EFFECTS = {
    "commit_time_authorization_revalidation",
    "relevant_workspace_and_page_access_epoch",
    "operation_id_and_canonical_request_hash",
    "stable_replay_result_or_stable_error",
    "canonical_content_or_IAM_mutation",
    "document_revision_and_page_sequence_when_content_changes",
    "durable_collaboration_event_when_observable",
    "audit_event",
    "outbox_event",
}
EXPECTED_EPOCH_USES = {
    "read",
    "write",
    "replay",
    "snapshot",
    "search",
    "inbox",
    "notification",
    "comment delivery",
    "subscription",
    "unpublished preview",
    "new publication",
    "live Notion attachment",
}
EXPECTED_L2_TESTS = (
    "code_assist_v1/tests/test_notion_workspace_store.py",
    "code_assist_v1/tests/test_notion_workspace_store_p1.py",
    "code_assist_v1/tests/test_notion_workspace_routes.py",
    "code_assist_v1/tests/test_notion_workspace_pages.py",
    "code_assist_v1/tests/test_notion_workspace_locator_projection.py",
    "code_assist_v1/tests/test_notion_workspace_projection.py",
    "code_assist_v1/tests/test_notion_workspace_outbox.py",
    "code_assist_v1/tests/test_notion_workspace_outbox_delivery.py",
    "code_assist_v1/tests/test_notion_workspace_navigation.py",
    "code_assist_v1/tests/test_notion_workspace_navigation_routes.py",
    "code_assist_v1/tests/test_notion_workspace_page_tree.py",
)
EXPECTED_DELIVERABLES = {
    "authority_route_and_storage_map",
    "atomic_transaction_and_permission_epoch_ADR",
    "legacy_import_no_dual_write_and_no_directory_scan_contract",
    "second_brain_revision_boundary",
    "revoke_commit_gateway_fence_ack_or_send_time_epoch_check_contract",
}


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _sorted_errors(errors: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    unique = {(item["code"], item["subject"], item["message"]): item for item in errors}
    return [unique[key] for key in sorted(unique)]


def _load(path: Path, label: str, errors: list[dict[str, str]]) -> Any | None:
    try:
        return load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error(f"{label}_READ_FAILED", str(path), str(exc)))
        return None


def _expect(
    condition: bool,
    errors: list[dict[str, str]],
    code: str,
    subject: str,
    message: str,
) -> None:
    if not condition:
        errors.append(_error(code, subject, message))


def _hash_ref_paths(
    refs: Any,
    *,
    subject: str,
    expected_paths: Sequence[str],
    errors: list[dict[str, str]],
) -> None:
    if not isinstance(refs, list):
        errors.append(_error("HASH_REFS_INVALID", subject, "hash references must be an array"))
        return
    paths = [item.get("path") for item in refs if isinstance(item, dict)]
    _expect(
        tuple(paths) == tuple(expected_paths),
        errors,
        "AUTHORITY_PATH_SET_DRIFT",
        subject,
        "authority paths or their required order changed",
    )
    for index, item in enumerate(refs):
        item_subject = f"{subject}[{index}]"
        if not isinstance(item, dict):
            errors.append(_error("HASH_REF_INVALID", item_subject, "hash reference must be an object"))
            continue
        relative = item.get("path")
        expected_hash = item.get("sha256")
        resolved = repository_path(relative) if isinstance(relative, str) else None
        if resolved is None or not resolved.is_file():
            errors.append(_error("AUTHORITY_PATH_INVALID", item_subject + ".path", str(relative)))
            continue
        actual = sha256_file(resolved)
        _expect(
            expected_hash == actual,
            errors,
            "AUTHORITY_HASH_DRIFT",
            item_subject + ".sha256",
            f"expected {expected_hash}, observed {actual}",
        )


def _validate_authority_inputs(adr: dict[str, Any], errors: list[dict[str, str]]) -> None:
    inputs = adr.get("authority_inputs")
    if not isinstance(inputs, dict):
        return
    agents = inputs.get("agents_md")
    if isinstance(agents, dict):
        _hash_ref_paths(
            [agents],
            subject="$.authority_inputs.agents_md",
            expected_paths=("code_assist_v1/AGENTS.md",),
            errors=errors,
        )
    _hash_ref_paths(
        inputs.get("planning_documents"),
        subject="$.authority_inputs.planning_documents",
        expected_paths=EXPECTED_PLANNING_PATHS,
        errors=errors,
    )
    _hash_ref_paths(
        inputs.get("source_documents"),
        subject="$.authority_inputs.source_documents",
        expected_paths=EXPECTED_SOURCE_PATHS,
        errors=errors,
    )

    manifest = inputs.get("sbe_instruction_manifest")
    if isinstance(manifest, list):
        paths = [item[0] for item in manifest if isinstance(item, list) and len(item) == 2]
        _expect(
            tuple(paths) == EXPECTED_SBE_PATHS,
            errors,
            "SBE_MANIFEST_ORDER_DRIFT",
            "$.authority_inputs.sbe_instruction_manifest",
            "the AGENTS.md 18-document order must remain exact",
        )
        for index, item in enumerate(manifest):
            subject = f"$.authority_inputs.sbe_instruction_manifest[{index}]"
            if not isinstance(item, list) or len(item) != 2:
                errors.append(_error("SBE_MANIFEST_ENTRY_INVALID", subject, "expected [path, sha256]"))
                continue
            path, expected_hash = item
            resolved = repository_path(path) if isinstance(path, str) else None
            if resolved is None or not resolved.is_file():
                errors.append(_error("SBE_PATH_INVALID", subject + "[0]", str(path)))
                continue
            actual = sha256_file(resolved)
            _expect(
                expected_hash == actual,
                errors,
                "SBE_HASH_DRIFT",
                subject + "[1]",
                f"expected {expected_hash}, observed {actual}",
            )

    schema_path = repository_path(
        "code_assist_v1/docs/knowledge_spaces/second_brain_evolution/FORWARD_SCHEMA.md"
    )
    if schema_path is not None and schema_path.is_file():
        _expect(
            inputs.get("current_schema_lineage_sha256") == sha256_file(schema_path),
            errors,
            "SCHEMA_LINEAGE_DRIFT",
            "$.authority_inputs.current_schema_lineage_sha256",
            "current schema lineage must bind the exact SBE forward schema",
        )


def _validate_current_source_truth(adr: dict[str, Any], errors: list[dict[str, str]]) -> None:
    current = adr.get("current_state")
    if not isinstance(current, dict):
        return
    _expect(
        current.get("classification") == "observed_split_legacy_authority_not_collaboration_ready",
        errors,
        "CURRENT_STATE_FALSE_GREEN",
        "$.current_state.classification",
        "A0A-P1 must not describe the current split runtime as collaboration-ready",
    )
    _expect(
        current.get("runtime_mutated_by_this_adr") is False,
        errors,
        "CURRENT_RUNTIME_MUTATION_FALSE_GREEN",
        "$.current_state.runtime_mutated_by_this_adr",
        "an ADR cannot claim a runtime mutation",
    )
    workspace = current.get("workspace_authority", {})
    _expect(
        isinstance(workspace, dict) and workspace.get("schema_version") == 2026081317,
        errors,
        "WORKSPACE_SCHEMA_VERSION_DRIFT",
        "$.current_state.workspace_authority.schema_version",
        "current WorkspaceStore schema version must be stated exactly",
    )

    source_markers = {
        "code_assist_v1/notion_store.py": (
            "def _known_workspace_users()",
            "os.listdir(USER_DATA_DIR)",
            "def resolve_page_from_owner(",
            "without enumerating USER_DATA_DIR",
            "def save_page_atomic(",
        ),
        "code_assist_v1/notion_workspace_store.py": (
            "SCHEMA_VERSION = 2026081317",
            'return os.path.join(root, ".notion-workspace-v2", "workspace.db")',
            "CREATE TABLE IF NOT EXISTS workspace_memberships",
            "CREATE TABLE IF NOT EXISTS workspace_audit_events",
            "CREATE TABLE IF NOT EXISTS workspace_outbox",
        ),
        "code_assist_v1/routes_notion.py": (
            '"/api/notion/pages/<path:page_id>/publish_preview"',
            '"/api/notion/pages/<path:page_id>/publish_second_brain"',
            "publish_page_to_legacy_second_brain",
        ),
        "code_assist_v1/notion_publication.py": (
            "def capture_page_source_snapshot(",
            "def publish_page_to_legacy_second_brain(",
            "expected_source_snapshot_sha256",
        ),
    }
    for relative, markers in source_markers.items():
        path = repository_path(relative)
        if path is None or not path.is_file():
            errors.append(_error("CURRENT_SOURCE_MISSING", relative, "source probe path is missing"))
            continue
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            _expect(
                marker in text,
                errors,
                "CURRENT_SOURCE_PROBE_DRIFT",
                relative,
                f"missing source marker: {marker}",
            )


def _validate_target_contract(adr: dict[str, Any], errors: list[dict[str, str]]) -> None:
    target = adr.get("target_state")
    if isinstance(target, dict):
        for field in (
            "request_time_owner_directory_scan",
            "dual_write",
            "second_permission_authority",
            "runtime_enabled",
        ):
            _expect(
                target.get(field) is False,
                errors,
                "TARGET_BOUNDARY_WEAKENED",
                f"$.target_state.{field}",
                f"{field} must remain false",
            )
        _expect(
            "one Notion-owned SQLite authority" in str(target.get("notion_authority")),
            errors,
            "SINGLE_AUTHORITY_MISSING",
            "$.target_state.notion_authority",
            "target must name one Notion-owned SQLite authority",
        )

    routes = adr.get("route_storage_map")
    if isinstance(routes, list):
        ids = [item.get("id") for item in routes if isinstance(item, dict)]
        duplicates = sorted(key for key, count in Counter(ids).items() if count > 1)
        _expect(not duplicates, errors, "ROUTE_MAP_DUPLICATE", "$.route_storage_map", str(duplicates))
        _expect(
            set(ids) == EXPECTED_ROUTE_IDS,
            errors,
            "ROUTE_MAP_INCOMPLETE",
            "$.route_storage_map",
            f"expected route families {sorted(EXPECTED_ROUTE_IDS)}",
        )

    atomic = adr.get("atomic_transaction_contract")
    if isinstance(atomic, dict):
        _expect(
            atomic.get("database_boundary") == "one Notion SQLite writer transaction",
            errors,
            "ATOMIC_BOUNDARY_DRIFT",
            "$.atomic_transaction_contract.database_boundary",
            "all canonical effects must use one Notion writer transaction",
        )
        _expect(
            set(atomic.get("required_effects", [])) == EXPECTED_ATOMIC_EFFECTS,
            errors,
            "ATOMIC_EFFECT_SET_DRIFT",
            "$.atomic_transaction_contract.required_effects",
            "content/IAM, epoch, operation, event, audit, and outbox effects must stay complete",
        )
        for field in (
            "service_layer_only",
            "all_required_effects_commit_or_rollback_together",
            "external_result_inputs_revalidated_immediately_before_commit",
        ):
            _expect(
                atomic.get(field) is True,
                errors,
                "ATOMIC_INVARIANT_WEAKENED",
                f"$.atomic_transaction_contract.{field}",
                f"{field} must remain true",
            )
        _expect(
            atomic.get("route_or_gateway_direct_canonical_write") is False,
            errors,
            "DIRECT_WRITE_ENABLED",
            "$.atomic_transaction_contract.route_or_gateway_direct_canonical_write",
            "routes and gateways must not write canonical state directly",
        )

    epochs = adr.get("permission_epoch_contract")
    if isinstance(epochs, dict):
        _expect(
            set(epochs.get("epochs", [])) == {"workspace_access_epoch", "page_access_epoch"},
            errors,
            "EPOCH_SCOPE_DRIFT",
            "$.permission_epoch_contract.epochs",
            "workspace and page access epochs are both required",
        )
        _expect(
            set(epochs.get("current_epoch_required_for", [])) == EXPECTED_EPOCH_USES,
            errors,
            "EPOCH_USE_SET_DRIFT",
            "$.permission_epoch_contract.current_epoch_required_for",
            "every protected read/write/delivery/publication boundary must be epoch-fenced",
        )
        for field in (
            "monotonic_no_reuse",
            "permission_mutation_increments_affected_epochs_in_same_transaction",
            "rejoin_uses_new_epoch",
        ):
            _expect(
                epochs.get(field) is True,
                errors,
                "EPOCH_INVARIANT_WEAKENED",
                f"$.permission_epoch_contract.{field}",
                f"{field} must remain true",
            )

    revoke = adr.get("revoke_delivery_fence_contract")
    if isinstance(revoke, dict):
        _expect(
            revoke.get("selected_strategy") == "authoritative_send_time_epoch_check",
            errors,
            "REVOKE_STRATEGY_DRIFT",
            "$.revoke_delivery_fence_contract.selected_strategy",
            "A0A-P1 selected authoritative send-time epoch checks",
        )
        _expect(
            revoke.get("post_response_old_epoch_protected_delivery_count") == 0,
            errors,
            "REVOKE_FALSE_GREEN",
            "$.revoke_delivery_fence_contract.post_response_old_epoch_protected_delivery_count",
            "post-response old-epoch delivery must be zero",
        )
        unavailable = revoke.get("gateway_unavailable_response", {})
        _expect(
            isinstance(unavailable, dict)
            and unavailable.get("http_status") == 200
            and unavailable.get("state") == "revoked"
            and unavailable.get("gateway_notification") == "durable_outbox_pending",
            errors,
            "GATEWAY_UNAVAILABLE_RESPONSE_DRIFT",
            "$.revoke_delivery_fence_contract.gateway_unavailable_response",
            "a durable revoke succeeds independently of an unavailable transport",
        )
        authority_unavailable = revoke.get("notion_authority_unavailable_response", {})
        _expect(
            isinstance(authority_unavailable, dict)
            and authority_unavailable.get("http_status") == 503
            and authority_unavailable.get("success_claimed") is False,
            errors,
            "AUTHORITY_UNAVAILABLE_FALSE_SUCCESS",
            "$.revoke_delivery_fence_contract.notion_authority_unavailable_response",
            "an uncommitted revoke must fail closed",
        )

    cutover = adr.get("legacy_cutover_contract")
    if isinstance(cutover, dict):
        for field in (
            "live_source_import",
            "request_time_directory_enumeration",
            "request_time_legacy_source_database_open",
            "compatibility_dual_write",
            "production_cutover_authorized",
        ):
            _expect(
                cutover.get(field) is False,
                errors,
                "LEGACY_CUTOVER_BOUNDARY_WEAKENED",
                f"$.legacy_cutover_contract.{field}",
                f"{field} must remain false",
            )
        _expect(
            cutover.get("import_mode") == "offline_explicit_manifest_only",
            errors,
            "IMPORT_MODE_DRIFT",
            "$.legacy_cutover_contract.import_mode",
            "legacy import must be offline and manifest-bound",
        )

    sbe = adr.get("second_brain_boundary")
    if isinstance(sbe, dict):
        for field in (
            "shared_identity_only",
            "notion_and_sbe_database_authorities_separate",
            "notion_and_sbe_schema_lineages_separate",
        ):
            _expect(
                sbe.get(field) is True,
                errors,
                "SBE_BOUNDARY_WEAKENED",
                f"$.second_brain_boundary.{field}",
                f"{field} must remain true",
            )
        for field in (
            "cross_database_atomic_transaction_claimed",
            "later_notion_edit_auto_sync",
            "sbe_files_modified_by_a0a_p1",
        ):
            _expect(
                sbe.get(field) is False,
                errors,
                "SBE_FALSE_AUTHORITY_CLAIM",
                f"$.second_brain_boundary.{field}",
                f"{field} must remain false",
            )
        _expect(
            len(sbe.get("ordered_publication_contract", [])) == 6,
            errors,
            "SBE_PUBLICATION_ORDER_INCOMPLETE",
            "$.second_brain_boundary.ordered_publication_contract",
            "the six-step exact-revision publication boundary is required",
        )

    prototype = adr.get("prototype_boundary")
    if isinstance(prototype, dict):
        _expect(
            prototype.get("next_packet") == "A0A-P2"
            and prototype.get("real_schema_migration_number_allocated") is False
            and prototype.get("existing_workspace_schema_modified") is False
            and prototype.get("prototype_tables_are_production_contract") is False,
            errors,
            "PROTOTYPE_BOUNDARY_DRIFT",
            "$.prototype_boundary",
            "A0A-P2 must remain disposable and unversioned",
        )


def _validate_catalog_and_lane(
    adr: dict[str, Any],
    *,
    catalog_path: Path,
    ladder_path: Path,
    errors: list[dict[str, str]],
) -> None:
    catalog = _load(catalog_path, "CATALOG", errors)
    ladder = _load(ladder_path, "LADDER", errors)
    if isinstance(catalog, dict):
        packets = catalog.get("packets", [])
        packet = next(
            (item for item in packets if isinstance(item, dict) and item.get("id") == "A0A-P1"),
            None,
        )
        _expect(packet is not None, errors, "PACKET_MISSING", "A0A-P1", "packet is absent")
        if isinstance(packet, dict):
            _expect(
                set(packet.get("deliverables", [])) == EXPECTED_DELIVERABLES,
                errors,
                "PACKET_DELIVERABLE_DRIFT",
                "A0A-P1.deliverables",
                "catalog deliverables no longer match this ADR",
            )
            _expect(
                packet.get("validation_ids") == ["L2-WORKSPACE-SUBSTRATE-BASELINE"],
                errors,
                "PACKET_LANE_DRIFT",
                "A0A-P1.validation_ids",
                "A0A-P1 must retain its declared L2 substrate lane",
            )
    lane_argv: list[str] | None = None
    if isinstance(ladder, dict):
        for rung in ladder.get("rungs", []):
            if not isinstance(rung, dict):
                continue
            for lane in rung.get("lanes", []):
                if isinstance(lane, dict) and lane.get("id") == "L2-WORKSPACE-SUBSTRATE-BASELINE":
                    lane_argv = lane.get("argv")
                    break
    _expect(
        isinstance(lane_argv, list),
        errors,
        "L2_LANE_MISSING",
        "L2-WORKSPACE-SUBSTRATE-BASELINE",
        "declared validation lane is absent",
    )
    if isinstance(lane_argv, list):
        lane_tests = tuple(item for item in lane_argv if isinstance(item, str) and item.endswith(".py"))
        _expect(
            lane_tests == EXPECTED_L2_TESTS,
            errors,
            "L2_LANE_TEST_DRIFT",
            "L2-WORKSPACE-SUBSTRATE-BASELINE.argv",
            "the exact workspace substrate selection changed",
        )
        validation = adr.get("validation_contract", {})
        _expect(
            isinstance(validation, dict)
            and tuple(validation.get("workspace_substrate_tests", [])) == EXPECTED_L2_TESTS,
            errors,
            "ADR_L2_TEST_DRIFT",
            "$.validation_contract.workspace_substrate_tests",
            "ADR test selection must equal the ladder lane",
        )


def verify_authority_adr(
    *,
    adr_path: Path = DEFAULT_ADR,
    schema_path: Path = DEFAULT_SCHEMA,
    catalog_path: Path = DEFAULT_CATALOG,
    ladder_path: Path = DEFAULT_LADDER,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    adr = _load(Path(adr_path), "ADR", errors)
    schema = _load(Path(schema_path), "SCHEMA", errors)
    if isinstance(adr, dict) and isinstance(schema, dict):
        for item in JsonSchemaValidator(schema).validate(adr):
            errors.append(
                _error(
                    "ADR_SCHEMA_" + item["code"],
                    item["subject"],
                    item["message"],
                )
            )
        _validate_authority_inputs(adr, errors)
        _validate_current_source_truth(adr, errors)
        _validate_target_contract(adr, errors)
        _validate_catalog_and_lane(
            adr,
            catalog_path=Path(catalog_path),
            ladder_path=Path(ladder_path),
            errors=errors,
        )
    ordered = _sorted_errors(errors)
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "contract": "code_assist.notion.internal_collaborative_authority_adr.audit.v1",
        "status": "pass" if not ordered else "fail",
        "error_count": len(ordered),
        "errors": ordered,
        "adr_sha256": sha256_file(Path(adr_path)) if Path(adr_path).is_file() else None,
        "schema_sha256": sha256_file(Path(schema_path)) if Path(schema_path).is_file() else None,
        "summary": {
            "packet_id": adr.get("packet_id") if isinstance(adr, dict) else None,
            "route_family_count": len(adr.get("route_storage_map", [])) if isinstance(adr, dict) else 0,
            "sbe_manifest_count": len(adr.get("authority_inputs", {}).get("sbe_instruction_manifest", []))
            if isinstance(adr, dict) and isinstance(adr.get("authority_inputs"), dict)
            else 0,
            "selected_revoke_strategy": adr.get("revoke_delivery_fence_contract", {}).get("selected_strategy")
            if isinstance(adr, dict) and isinstance(adr.get("revoke_delivery_fence_contract"), dict)
            else None,
            "runtime_implemented": adr.get("claims", {}).get("runtime_implemented")
            if isinstance(adr, dict) and isinstance(adr.get("claims"), dict)
            else None,
            "product_complete": adr.get("claims", {}).get("product_complete")
            if isinstance(adr, dict) and isinstance(adr.get("claims"), dict)
            else None,
        },
    }
    return report, 0 if not ordered else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adr", type=Path, default=DEFAULT_ADR)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--ladder", type=Path, default=DEFAULT_LADDER)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report, exit_code = verify_authority_adr(
        adr_path=args.adr,
        schema_path=args.schema,
        catalog_path=args.catalog,
        ladder_path=args.ladder,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

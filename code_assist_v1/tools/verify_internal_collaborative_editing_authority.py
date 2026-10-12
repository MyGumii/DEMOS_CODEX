"""Verify the E1-P1 collaborative editing authority contract offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from code_assist_v1.tools.database_feature_successor import owns_current_artifact as f0_owns_current_artifact

from code_assist_v1 import notion_workspace_editing_authority_migration as migration
from code_assist_v1.tools.verify_notion_workspace_route_contract import (
    verify_contract as verify_route_contract,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_editing_authority.v1.json"
)
SUCCESSOR_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_editor_ui.v1.json"
)
SECOND_BRAIN_SUCCESSOR_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_second_brain_publication.v1.json"
)
EXPECTED_CONTRACT = "code_assist.notion.internal_collaborative_editing_authority.e1_p1.v1"
EXPECTED_PREREQUISITES = {"I0-P1", "T0B-P3", "E0-P4", "K0-P1"}
EXPECTED_COMMANDS = [
    "block.delete",
    "block.insert",
    "block.move",
    "database.cell.set",
    "database.property.rename",
]
EXPECTED_IDENTITIES = [
    "client_operation_id",
    "document_event_sequence",
    "crdt_generation",
    "crdt_snapshot_id",
    "history_checkpoint_id",
    "user_revision",
    "publication_revision_id",
]
EXPECTED_SOURCE_PATHS = (
    "code_assist_v1/notion_workspace_editing_authority_migration.py",
    "code_assist_v1/notion_collaboration/editing_authority.py",
    "code_assist_v1/notion_collaboration/durable_transport.py",
    "code_assist_v1/notion_collaboration/iam_runtime.py",
    "code_assist_v1/notion_collaboration/gateway_service.py",
    "code_assist_v1/notion_collaboration/node_compactor.py",
    "code_assist_v1/collaboration_runtime/node/compactor.mjs",
    "code_assist_v1/notion_collaboration/approved_dev_pair.py",
    "code_assist_v1/routes_notion_workspace.py",
)
EXPECTED_TEST_PATHS = (
    "code_assist_v1/tests/notion_collaboration/test_collaborative_editing_authority.py",
    "code_assist_v1/tests/test_notion_workspace_auth_isolation.py",
    "code_assist_v1/tests/test_verify_notion_workspace_route_contract.py",
    "code_assist_v1/tools/verify_notion_workspace_route_contract.py",
)
SUCCESSOR_OWNED_PATHS = {
    "code_assist_v1/notion_collaboration/editing_authority.py",
    "code_assist_v1/tests/notion_collaboration/test_collaborative_editing_authority.py",
}
SECOND_BRAIN_SUCCESSOR_OWNED_PATHS = {
    "code_assist_v1/notion_collaboration/approved_dev_pair.py",
    "code_assist_v1/notion_collaboration/editing_authority.py",
    "code_assist_v1/notion_collaboration/iam_runtime.py",
}


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


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


def _repository_file(relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    candidate = (REPOSITORY_ROOT / relative).resolve(strict=False)
    try:
        candidate.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _successor_owns_current_artifact(relative: str, target: Path) -> bool:
    if f0_owns_current_artifact(relative, target):
        return True
    contracts = (
        (SUCCESSOR_CONTRACT_PATH, SUCCESSOR_OWNED_PATHS, "E1-P2", "E1_P2_gate_passed"),
        (
            SECOND_BRAIN_SUCCESSOR_CONTRACT_PATH,
            SECOND_BRAIN_SUCCESSOR_OWNED_PATHS,
            "S0A-P2",
            "S0A_P2_gate_passed",
        ),
    )
    for contract_path, owned_paths, packet_id, gate_claim in contracts:
        if relative not in owned_paths:
            continue
        try:
            successor = _load(contract_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(successor, dict):
            continue
        rows = [
            *successor.get("source_manifest", []),
            *successor.get("test_manifest", []),
        ]
        binding = next(
            (
                item
                for item in rows
                if isinstance(item, dict) and item.get("path") == relative
            ),
            None,
        )
        if (
            successor.get("packet_id") == packet_id
            and successor.get("status") == "implemented_and_validated"
            and successor.get("claims", {}).get(gate_claim) is True
            and isinstance(binding, dict)
            and binding.get("sha256") == _sha256_file(target)
        ):
            return True
    return False


def verify(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    allow_successor = path.resolve(strict=False) == CONTRACT_PATH.resolve(strict=False)

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": "code_assist.notion.internal_collaborative_editing_authority.audit.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [
                {"code": "CONTRACT_READ_FAILED", "subject": str(path), "message": str(exc)}
            ],
        }
    check(isinstance(value, dict), "CONTRACT_SHAPE", "$", "contract must be an object")
    if not isinstance(value, dict):
        return {"status": "fail", "error_count": len(errors), "errors": errors}
    check(
        value.get("schema_version") == 1
        and value.get("contract") == EXPECTED_CONTRACT
        and value.get("packet_id") == "E1-P1"
        and value.get("status") == "implemented_and_validated",
        "CONTRACT_IDENTITY",
        "$",
        "E1-P1 contract identity or status drift",
    )

    prerequisites = value.get("prerequisite_evidence", {})
    check(
        isinstance(prerequisites, dict) and set(prerequisites) == EXPECTED_PREREQUISITES,
        "PREREQUISITE_DENOMINATOR",
        "$.prerequisite_evidence",
        "exact E1-P1 prerequisite set required",
    )
    if isinstance(prerequisites, dict):
        for identifier, item in prerequisites.items():
            relative = item.get("path") if isinstance(item, dict) else None
            target = (CONTRACT_PATH.parent / str(relative or "")).resolve(strict=False)
            try:
                target.relative_to(REPOSITORY_ROOT.resolve())
            except ValueError:
                target = None
            else:
                if not target.is_file():
                    target = None
            check(target is not None, "PREREQUISITE_MISSING", identifier, "checkpoint missing")
            if target is not None:
                check(
                    _sha256_file(target) == item.get("sha256"),
                    "PREREQUISITE_HASH_DRIFT",
                    identifier,
                    "checkpoint hash drift",
                )
            check(
                isinstance(item, dict) and item.get("status") == "gate_passed",
                "PREREQUISITE_STATUS",
                identifier,
                "gate-passed prerequisite required",
            )

    runtime = value.get("runtime_contract", {})
    check(
        isinstance(runtime, dict)
        and [runtime.get("source_schema_version"), runtime.get("target_schema_version")]
        == [migration.SOURCE_VERSION, migration.TARGET_VERSION]
        and runtime.get("target_schema_name") == migration.TARGET_NAME
        and runtime.get("target_schema_checksum") == migration.TARGET_CHECKSUM
        and runtime.get("actual_schema_sha256") == migration.TARGET_ACTUAL_SCHEMA_SHA256
        and runtime.get("selected_runtime_candidate") == "PY_OPAQUE_NODE_COMPACTOR"
        and runtime.get("yjs_wire_version") == 1
        and runtime.get("development_ports") == [10111, 10112, 10113]
        and runtime.get("approval_id") == "AP-DEV-PAIR"
        and runtime.get("approved_principals") == ["admin", "user01"]
        and runtime.get("production_ports_changed") is False
        and runtime.get("production_mutation_allowed") is False,
        "RUNTIME_BOUNDARY",
        "$.runtime_contract",
        "schema, runtime, or development/production boundary drift",
    )

    rich_text = value.get("rich_text_crdt_boundary", {})
    check(
        isinstance(rich_text, dict)
        and rich_text.get("scope") == "document_rich_text_leaves"
        and rich_text.get("python_decodes_or_transforms_yjs_payload") is False
        and rich_text.get("every_keystroke_advances_user_revision") is False
        and all(
            rich_text.get(field) is True
            for field in (
                "python_persists_opaque_update_bytes_and_hashes",
                "node_yjs_child_owns_snapshot_and_state_vector_materialization",
                "every_update_bound_to_exactly_one_crdt_generation",
                "every_snapshot_bound_to_exactly_one_crdt_generation",
                "durable_ack_precedes_peer_broadcast",
                "edit_content_role_can_submit_rich_text_updates",
            )
        ),
        "CRDT_BOUNDARY",
        "$.rich_text_crdt_boundary",
        "opaque rich-text CRDT boundary drift",
    )

    typed = value.get("typed_command_boundary", {})
    check(
        isinstance(typed, dict)
        and typed.get("supported_command_kinds") == EXPECTED_COMMANDS
        and typed.get("content_only_role_can_change_database_schema") is False
        and all(
            typed.get(field) is True
            for field in (
                "server_sequenced",
                "document_sequence_cas_required",
                "target_row_version_cas_supported",
                "domain_event_audit_outbox_and_idempotency_atomic",
                "block_delete_writes_recovery_tombstone",
                "content_only_role_can_change_blocks_and_cells",
                "edit_or_full_access_role_required_for_database_schema",
            )
        ),
        "TYPED_COMMAND_BOUNDARY",
        "$.typed_command_boundary",
        "server-sequenced command or role boundary drift",
    )

    history = value.get("history_identity_contract", {})
    check(
        isinstance(history, dict)
        and history.get("distinct_identity_order") == EXPECTED_IDENTITIES
        and all(
            history.get(field) is True
            for field in (
                "checkpoint_requires_all_current_generation_updates_compacted",
                "checkpoint_captures_structure_command_high_water",
                "checkpoint_captures_canonical_blocks_and_database_snapshot",
                "checkpoint_may_advance_user_revision",
                "publication_revision_is_nullable_and_never_aliased_to_user_revision",
                "existing_revisions_backfilled_as_initial_checkpoints",
            )
        ),
        "HISTORY_IDENTITY",
        "$.history_identity_contract",
        "revision/checkpoint identity drift",
    )

    validation = value.get("validation_contract", {})
    check(
        isinstance(validation, dict)
        and validation.get("isolated_authority_tests") == 6
        and validation.get("focused_route_and_authority_bundle_tests") == 22
        and validation.get("approved_pair_schema_version") == 2026082405
        and validation.get("approved_pair_health_ports") == [10111, 10112, 10113]
        and validation.get("approved_pair_pre_migration_backup") == "before-2405.db"
        and [
            validation.get("approved_pair_document_count"),
            validation.get("approved_pair_generation_count"),
            validation.get("approved_pair_checkpoint_count"),
        ]
        == [19, 19, 19]
        and all(
            validation.get(field) is True
            for field in (
                "real_gateway_process",
                "real_node_yjs_compactor_process",
                "real_websocket_client_process",
                "valid_korean_yjs_update_committed",
                "gateway_restart_verified",
                "fault_rollback_verified",
                "uncompacted_update_checkpoint_rejected",
                "idempotent_replay_verified",
            )
        ),
        "VALIDATION_DENOMINATOR",
        "$.validation_contract",
        "E1-P1 validation denominator drift",
    )

    editing_source = (PROJECT_ROOT / "notion_collaboration" / "editing_authority.py").read_text(
        encoding="utf-8"
    )
    check(
        "import yjs" not in editing_source.lower()
        and "from yjs" not in editing_source.lower()
        and "y.apply" not in editing_source.lower(),
        "PYTHON_YJS_INTERPRETATION",
        "editing_authority.py",
        "Python editing authority must not interpret Yjs payloads",
    )

    for key, expected_paths in (
        ("source_manifest", EXPECTED_SOURCE_PATHS),
        ("test_manifest", EXPECTED_TEST_PATHS),
    ):
        manifest = value.get(key)
        paths = (
            tuple(
                item.get("path")
                for item in manifest
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            )
            if isinstance(manifest, list)
            else ()
        )
        check(paths == expected_paths, "MANIFEST_DENOMINATOR", f"$.{key}", "path set or order drift")
        if isinstance(manifest, list):
            for index, item in enumerate(manifest):
                target = _repository_file(item.get("path")) if isinstance(item, dict) else None
                check(target is not None, "MANIFEST_FILE_MISSING", f"$.{key}[{index}]", "file missing")
                if target is not None:
                    check(
                        _sha256_file(target) == item.get("sha256")
                        or _successor_owns_current_artifact(str(item.get("path")), target),
                        "MANIFEST_HASH_DRIFT",
                        str(item.get("path")),
                        "bound file hash drift",
                    )

    route = value.get("route_contract", {})
    route_report, route_exit = verify_route_contract()
    check(
        isinstance(route, dict)
        and route_exit == 0
        and route_report.get("status") == "pass"
        and (
            route_report.get("summary", {}).get("actual_route_count") == 28
            or (
                allow_successor
                and route_report.get("summary", {}).get("actual_route_count") == 30
                and _successor_owns_current_artifact(
                    "code_assist_v1/routes_notion_workspace.py",
                    PROJECT_ROOT / "routes_notion_workspace.py",
                )
            )
        )
        and route_report.get("summary", {}).get("operation_count") == 42
        and route.get("rule_count") == 28
        and route.get("operation_count") == 40
        and route.get("editing_command_method") == "POST"
        and route.get("revision_checkpoint_methods") == ["GET", "POST"],
        "ROUTE_CONTRACT",
        "$.route_contract",
        "editing route or semantic overlay drift",
    )
    if isinstance(route, dict):
        for path_key, hash_key in (
            ("manifest_path", "manifest_sha256"),
            ("semantics_path", "semantics_sha256"),
        ):
            target = _repository_file(route.get(path_key))
            check(target is not None, "ROUTE_FILE_MISSING", f"$.route_contract.{path_key}", "file missing")
            if target is not None:
                check(
                    _sha256_file(target) == route.get(hash_key)
                    or (
                        allow_successor
                        and _successor_owns_current_artifact(
                            str(route.get(path_key)), target
                        )
                    ),
                    "ROUTE_HASH_DRIFT",
                    str(route.get(path_key)),
                    "route artifact hash drift",
                )

    claims = value.get("claims", {})
    check(
        isinstance(claims, dict)
        and all(
            claims.get(field) is True
            for field in (
                "E1_P1_gate_passed",
                "EDIT_001_contributed",
                "EDIT_002_contributed",
                "HIST_001_contributed",
            )
        )
        and all(
            claims.get(field) is False
            for field in (
                "EDIT_001_closed",
                "EDIT_002_closed",
                "HIST_001_closed",
                "production_changed",
                "product_complete",
                "active_shared_goal_complete",
            )
        ),
        "CLAIMS_FALSE_GREEN",
        "$.claims",
        "E1 contribution or non-completion claims drift",
    )

    normalized = sorted(
        {(item["code"], item["subject"], item["message"]): item for item in errors}.values(),
        key=lambda item: (item["code"], item["subject"], item["message"]),
    )
    return {
        "contract": "code_assist.notion.internal_collaborative_editing_authority.audit.v1",
        "status": "pass" if not normalized else "fail",
        "error_count": len(normalized),
        "errors": normalized,
        "contract_sha256": _sha256_file(path),
        "source_count": len(EXPECTED_SOURCE_PATHS),
        "test_count": len(EXPECTED_TEST_PATHS),
        "route_count": route_report.get("summary", {}).get("actual_route_count"),
        "operation_count": route_report.get("summary", {}).get("operation_count"),
        "gate_passed": not normalized,
        "product_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report = verify(args.contract)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

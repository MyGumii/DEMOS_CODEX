from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from code_assist_v1 import notion_workspace_database_features_migration as migration
from code_assist_v1.tools.database_feature_successor import owns_current_artifact


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
    / "internal_collaborative_database_features.v1.json"
)
EXPECTED_SOURCE_PATHS = [
    "code_assist_v1/notion_workspace_database_features_migration.py",
    "code_assist_v1/notion_collaboration/database_features.py",
    "code_assist_v1/notion_collaboration/editing_authority.py",
    "code_assist_v1/notion_collaboration/iam_runtime.py",
    "code_assist_v1/notion_collaboration/approved_dev_pair.py",
    "code_assist_v1/tools/database_feature_successor.py",
    "code_assist_v1/tools/verify_internal_collaborative_editing_authority.py",
    "code_assist_v1/tools/verify_internal_collaborative_editor_ui.py",
    "code_assist_v1/tools/verify_internal_collaborative_sharing_runtime.py",
    "code_assist_v1/tools/verify_internal_live_collaboration_runtime.py",
    "code_assist_v1/tools/verify_internal_collaborative_convergence_corpus.py",
    "code_assist_v1/tools/verify_internal_collaborative_second_brain_boundary.py",
    "code_assist_v1/tools/verify_internal_collaborative_second_brain_publication.py",
    "code_assist_v1/tools/verify_internal_collaborative_database_features.py",
    "code_assist_v1/tools/internal_collaborative_validation.py",
]
EXPECTED_TEST_PATHS = [
    "code_assist_v1/tests/notion_collaboration/test_database_features.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_database_features.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
]


def _load(path: Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

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
    *,
    allow_successor: bool,
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
        elif item.get("sha256") != _sha256(target) and not (
            allow_successor and owns_current_artifact(str(item.get("path")), target)
        ):
            errors.append({"code": "MANIFEST_HASH_DRIFT", "subject": str(item.get("path")), "message": "bound file hash drift"})


def verify(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    allow_successor = path.resolve(strict=False) == CONTRACT_PATH.resolve(strict=False)

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {"contract": "code_assist.notion.internal_collaborative_database_features.audit.v1", "status": "fail", "error_count": 1, "errors": [{"code": "CONTRACT_READ_FAILED", "subject": str(path), "message": str(exc)}], "gate_passed": False, "product_complete": False}
    if not isinstance(value, dict):
        return {"status": "fail", "error_count": 1, "errors": [{"code": "CONTRACT_SHAPE", "subject": "$", "message": "object required"}], "gate_passed": False, "product_complete": False}
    check(
        value.get("schema_version") == 1
        and value.get("contract") == "code_assist.notion.internal_collaborative_database_features.f0_p1.v1"
        and value.get("packet_id") == "F0-P1"
        and value.get("status") == "implemented_and_validated",
        "CONTRACT_IDENTITY", "$", "F0-P1 identity or status drift",
    )
    prerequisite = value.get("prerequisite_evidence", {})
    checkpoint = prerequisite.get("S0A-P2_checkpoint", {}) if isinstance(prerequisite, dict) else {}
    checkpoint_path = _repository_file(checkpoint.get("path")) if isinstance(checkpoint, dict) else None
    check(
        prerequisite.get("G0") == "frozen"
        and prerequisite.get("E1-P3") == "gate_passed"
        and prerequisite.get("K0-P1") == "gate_passed"
        and checkpoint_path is not None
        and checkpoint.get("sha256") == _sha256(checkpoint_path),
        "PREREQUISITE_DRIFT", "$.prerequisite_evidence", "exact frozen prerequisites required",
    )
    instruction = value.get("dynamic_instruction_binding", {})
    agents = _repository_file(instruction.get("path")) if isinstance(instruction, dict) else None
    check(
        agents is not None
        and instruction.get("sha256") == _sha256(agents)
        and instruction.get("write_policy") == "read_only",
        "DYNAMIC_INSTRUCTION_DRIFT", "AGENTS.md", "current read-only instruction binding required",
    )
    lineage = value.get("migration_lineage", {})
    check(
        lineage.get("source_version") == migration.SOURCE_VERSION == 2026082406
        and lineage.get("target_version") == migration.TARGET_VERSION == 2026082407
        and lineage.get("target_name") == migration.TARGET_NAME
        and lineage.get("target_checksum") == migration.TARGET_CHECKSUM
        and lineage.get("target_actual_schema_sha256") == migration.TARGET_ACTUAL_SCHEMA_SHA256
        and lineage.get("forward_only") is True,
        "MIGRATION_LINEAGE_DRIFT", "$.migration_lineage", "exact frozen 2407 lineage required",
    )
    database = value.get("database_contract", {})
    check(
        database.get("adopted_view_types") == ["table", "board", "list", "gallery", "calendar", "timeline"]
        and database.get("relation_rollup_formula_configs_are_typed") is True
        and database.get("formula_language_is_deterministic_and_non_executable") is True
        and database.get("schema_mutation_minimum_role") == "edit"
        and database.get("cell_mutation_minimum_role") == "edit_content"
        and database.get("computed_properties_are_read_only") is True
        and database.get("relation_targets_require_current_read_authority") is True
        and database.get("schema_command_audit_event_outbox_atomic") is True
        and database.get("target_version_conflicts_fail_closed") is True
        and database.get("views_and_templates_restart_round_trip") is True,
        "DATABASE_CONTRACT_DRIFT", "$.database_contract", "adopted database contract drift",
    )
    validation = value.get("validation", {})
    check(
        validation.get("database_feature_tests", {}).get("passed") == 3
        and validation.get("database_feature_tests", {}).get("failed") == 0
        and validation.get("editing_and_convergence_regression", {}).get("passed") == 11
        and validation.get("editing_and_convergence_regression", {}).get("failed") == 0
        and validation.get("production_10011_10012_changed") is False,
        "VALIDATION_DRIFT", "$.validation", "exact measured regression evidence required",
    )
    _verify_manifest(
        value.get("source_manifest"), EXPECTED_SOURCE_PATHS, "$.source_manifest", errors,
        allow_successor=allow_successor,
    )
    _verify_manifest(
        value.get("test_manifest"), EXPECTED_TEST_PATHS, "$.test_manifest", errors,
        allow_successor=allow_successor,
    )
    claims = value.get("claims", {})
    check(
        claims.get("F0_P1_gate_passed") is True
        and claims.get("DB_001_closed") is False
        and claims.get("EDIT_002_closed") is True
        and claims.get("CAP_001_closed") is False
        and claims.get("production_changed") is False
        and claims.get("product_complete") is False
        and claims.get("active_shared_goal_complete") is False,
        "CLAIMS_DRIFT", "$.claims", "partial completion truth drift",
    )
    return {
        "contract": "code_assist.notion.internal_collaborative_database_features.audit.v1",
        "contract_sha256": _sha256(path),
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "errors": errors,
        "gate_passed": not errors,
        "source_count": len(EXPECTED_SOURCE_PATHS),
        "test_count": len(EXPECTED_TEST_PATHS),
        "target_version": migration.TARGET_VERSION,
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

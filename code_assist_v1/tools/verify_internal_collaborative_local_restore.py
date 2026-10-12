from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from code_assist_v1.notion_collaboration import coordinated_workspace_backup as backup


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_local_backup_restore.v1.json"
)
EXPECTED_PREREQUISITES = {
    "O0A-P3",
    "A0B-P1",
    "T0B-P3",
    "I0-P2",
    "C0-P2",
    "E1-P3",
    "S0A-P2",
    "F0-P5",
}
EXPECTED_SCENARIOS = [
    "OPS-001",
    "OPS-002",
    "OPS-003",
    "OPS-004",
    "OPS-005",
    "OPS-006",
]
EXPECTED_SOURCE_PATHS = [
    "code_assist_v1/notion_collaboration/coordinated_workspace_backup.py",
    "code_assist_v1/knowledge_core/services/backup.py",
    "code_assist_v1/knowledge_core/services/restore.py",
    "code_assist_v1/knowledge_core/backup_storage.py",
    "code_assist_v1/tools/verify_internal_collaborative_local_restore.py",
    "code_assist_v1/tools/internal_collaborative_validation.py",
]
EXPECTED_TEST_PATHS = [
    "code_assist_v1/tests/notion_collaboration/o1a_p1_backup_fixture.py",
    "code_assist_v1/tests/notion_collaboration/second_brain_publication_fixture.py",
    "code_assist_v1/tests/notion_collaboration/test_backup_high_water.py",
    "code_assist_v1/tests/notion_collaboration/test_restore_new_root.py",
    "code_assist_v1/tests/notion_collaboration/test_restore_replay_integrity.py",
    "code_assist_v1/tests/notion_collaboration/test_compaction_boundary.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_local_restore.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
]


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
    expected_paths: list[str],
    subject: str,
    errors: list[dict[str, str]],
) -> None:
    if not isinstance(value, list):
        errors.append(
            {"code": "MANIFEST_SHAPE", "subject": subject, "message": "array required"}
        )
        return
    paths = [item.get("path") for item in value if isinstance(item, dict)]
    if paths != expected_paths:
        errors.append(
            {
                "code": "MANIFEST_DENOMINATOR",
                "subject": subject,
                "message": "exact ordered paths required",
            }
        )
    for index, item in enumerate(value):
        target = _repository_file(item.get("path")) if isinstance(item, dict) else None
        if target is None:
            errors.append(
                {
                    "code": "MANIFEST_FILE_MISSING",
                    "subject": f"{subject}[{index}]",
                    "message": "file missing",
                }
            )
        elif item.get("sha256") != _sha256(target):
            errors.append(
                {
                    "code": "MANIFEST_HASH_DRIFT",
                    "subject": str(item.get("path")),
                    "message": "bound file hash drift",
                }
            )


def verify(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": "code_assist.notion.internal_collaborative_local_backup_restore.audit.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [
                {
                    "code": "CONTRACT_READ_FAILED",
                    "subject": str(path),
                    "message": str(exc),
                }
            ],
            "gate_passed": False,
            "full_target_restore_lane_closed": False,
            "product_complete": False,
        }
    if not isinstance(value, dict):
        return {
            "status": "fail",
            "error_count": 1,
            "errors": [
                {
                    "code": "CONTRACT_SHAPE",
                    "subject": "$",
                    "message": "object required",
                }
            ],
            "gate_passed": False,
            "full_target_restore_lane_closed": False,
            "product_complete": False,
        }

    check(
        value.get("schema_version") == 1
        and value.get("contract")
        == "code_assist.notion.internal_collaborative_local_backup_restore.o1a_p1.v1"
        and value.get("packet_id") == "O1A-P1"
        and value.get("status") == "implemented_and_validated",
        "CONTRACT_IDENTITY",
        "$",
        "O1A-P1 contract identity or status drift",
    )

    instruction = value.get("dynamic_instruction_binding", {})
    agents = _repository_file(instruction.get("path")) if isinstance(instruction, dict) else None
    check(
        agents is not None
        and instruction.get("sha256") == _sha256(agents)
        and instruction.get("write_policy") == "read_only",
        "DYNAMIC_INSTRUCTION_DRIFT",
        "$.dynamic_instruction_binding",
        "current read-only AGENTS.md binding required",
    )

    prerequisites = value.get("prerequisite_evidence", {})
    check(
        isinstance(prerequisites, dict)
        and set(prerequisites) == EXPECTED_PREREQUISITES,
        "PREREQUISITE_DENOMINATOR",
        "$.prerequisite_evidence",
        "exact O1A prerequisite checkpoint set required",
    )
    if isinstance(prerequisites, dict):
        for packet_id, item in prerequisites.items():
            target = _repository_file(item.get("path")) if isinstance(item, dict) else None
            check(
                target is not None
                and item.get("packet_id") == packet_id
                and item.get("sha256") == _sha256(target),
                "PREREQUISITE_DRIFT",
                f"$.prerequisite_evidence.{packet_id}",
                "exact passed prerequisite checkpoint required",
            )

    objectives = value.get("recovery_objectives", {})
    check(
        objectives
        == {
            "rpo_minutes": backup.RPO_MINUTES,
            "rto_minutes": backup.RTO_MINUTES,
            "retention": backup.RETENTION_POLICY,
        }
        == {
            "rpo_minutes": 15,
            "rto_minutes": 60,
            "retention": {
                "half_hour_count": 96,
                "daily_count": 30,
                "monthly_count": 12,
            },
        },
        "RECOVERY_OBJECTIVE_DRIFT",
        "$.recovery_objectives",
        "approved RPO, RTO, and retention are required",
    )

    authority = value.get("authority_boundary", {})
    check(
        authority.get("authority_order") == ["notion", "second_brain"]
        and authority.get("separate_canonical_authorities") is True
        and authority.get("cross_database_transaction_claimed") is False
        and authority.get("identity_database_included") is False
        and authority.get("fresh_agent_identity_session_required") is True
        and authority.get("notion_transient_uploads_must_be_zero") is True
        and authority.get("second_brain_backup_service")
        == "knowledge_core.services.backup.BackupService"
        and authority.get("second_brain_restore_service")
        == "knowledge_core.services.restore.RestoreDrillService",
        "AUTHORITY_BOUNDARY_DRIFT",
        "$.authority_boundary",
        "separate authority, identity, drain, or delegation contract drift",
    )

    implementation = value.get("implementation_contract", {})
    check(
        implementation.get("generation_contract") == backup.GENERATION_CONTRACT
        and implementation.get("restore_contract") == backup.RESTORE_CONTRACT
        and implementation.get("source_contract") == backup.SOURCE_CONTRACT
        and implementation.get("fault_points")
        == sorted(point for point in backup.FAULT_POINTS if point is not None)
        and implementation.get("allowed_realm_ids")
        == ["synthetic_isolated", "target_restore"]
        and implementation.get("executed_realm_id") == "synthetic_isolated",
        "IMPLEMENTATION_CONTRACT_DRIFT",
        "$.implementation_contract",
        "runtime contract constants or realm boundary drift",
    )

    _verify_manifest(
        value.get("source_manifest"), EXPECTED_SOURCE_PATHS, "$.source_manifest", errors
    )
    _verify_manifest(
        value.get("test_manifest"), EXPECTED_TEST_PATHS, "$.test_manifest", errors
    )

    receipt_binding = value.get("execution_receipt", {})
    receipt_path = (
        _repository_file(receipt_binding.get("path"))
        if isinstance(receipt_binding, dict)
        else None
    )
    receipt: dict[str, Any] = {}
    if receipt_path is not None:
        try:
            loaded = _load(receipt_path)
            receipt = loaded if isinstance(loaded, dict) else {}
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            receipt = {}
    check(
        receipt_path is not None
        and receipt_binding.get("sha256") == _sha256(receipt_path)
        and receipt.get("receipt_id") == "O1A-P1-L7-LOCAL-BACKUP-RESTORE-20260825"
        and receipt.get("packet_ids") == ["O1A-P1"]
        and receipt.get("scenario_ids") == EXPECTED_SCENARIOS
        and receipt.get("matrix_row_ids") == ["ICN-OPS-001", "ICN-OPS-002"]
        and receipt.get("realm_id") == "synthetic_isolated"
        and receipt.get("mode") == "gate"
        and receipt.get("result") == "passed"
        and receipt.get("exit_code") == 0
        and receipt.get("passed") == 30
        and receipt.get("failed") == 0
        and receipt.get("skipped") == 0
        and receipt.get("xfailed") == 0
        and receipt.get("source_pre_sha256") == receipt.get("source_post_sha256"),
        "EXECUTION_RECEIPT_DRIFT",
        "$.execution_receipt",
        "exact zero-error O1A L7 receipt required",
    )

    for label, item in value.get("catalog_binding", {}).items():
        target = _repository_file(item.get("path")) if isinstance(item, dict) else None
        check(
            target is not None and item.get("sha256") == _sha256(target),
            "CATALOG_BINDING_DRIFT",
            f"$.catalog_binding.{label}",
            "bound catalog hash drift",
        )

    sbe = value.get("second_brain_fence", {})
    check(
        sbe.get("contract_audit_status") == "pass"
        and sbe.get("contract_audit_error_count") == 0
        and sbe.get("contract_audit_input_sha256")
        == "sha256:0beefd78448cdd19767be0e0276fdbf6951ff417c39c135105e7754d755df494"
        and sbe.get("goal_count") == 20
        and sbe.get("scenario_count") == 284
        and sbe.get("protected_second_brain_changed") is False,
        "SECOND_BRAIN_FENCE_DRIFT",
        "$.second_brain_fence",
        "current SBE audit or protected-change fence drift",
    )

    claims = value.get("claims", {})
    check(
        claims
        == {
            "O1A_P1_gate_passed": True,
            "local_generation_and_new_root_restore_passed": True,
            "full_target_restore_lane_closed": False,
            "target_restore_executed": False,
            "production_changed": False,
            "product_complete": False,
            "active_shared_goal_complete": False,
        },
        "CLAIMS_DRIFT",
        "$.claims",
        "partial local completion truth must remain fail closed",
    )

    errors.sort(key=lambda item: (item["code"], item["subject"], item["message"]))
    return {
        "contract": "code_assist.notion.internal_collaborative_local_backup_restore.audit.v1",
        "contract_sha256": _sha256(path),
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "errors": errors,
        "gate_passed": not errors,
        "scenario_count": len(EXPECTED_SCENARIOS),
        "source_count": len(EXPECTED_SOURCE_PATHS),
        "test_count": len(EXPECTED_TEST_PATHS),
        "full_target_restore_lane_closed": False,
        "product_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--compact", action="store_true")
    arguments = parser.parse_args(argv)
    report = verify(arguments.contract)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if arguments.compact else 2,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CONTRACT_PATH", "verify"]

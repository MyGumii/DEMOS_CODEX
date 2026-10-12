"""Verify the S0A-P2 collaborative Notion-to-SBE publication fence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from code_assist_v1 import notion_workspace_second_brain_publication_migration as migration
from code_assist_v1.tools.contract_audit import audit_contracts
from code_assist_v1.tools.database_feature_successor import owns_current_artifact as f0_owns_current_artifact


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_second_brain_publication.v1.json"
)
SBE_ROOT = PROJECT_ROOT / "docs" / "knowledge_spaces"
EXPECTED_CONTRACT = (
    "code_assist.notion.internal_collaborative_second_brain_publication.s0a_p2.v1"
)
EXPECTED_SCENARIOS = [
    "PUB-001",
    "PUB-002",
    "PUB-003",
    "PUB-004",
    "AUTH-001",
    "AUTH-002",
    "AUTH-004",
    "REG-002",
    "REG-003",
]
EXPECTED_SBE_DOCUMENTS = [
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
]
EXPECTED_SOURCE_PATHS = [
    "code_assist_v1/notion_workspace_second_brain_publication_migration.py",
    "code_assist_v1/notion_collaboration/second_brain_publication.py",
    "code_assist_v1/notion_collaboration/second_brain_boundary.py",
    "code_assist_v1/notion_collaboration/editing_authority.py",
    "code_assist_v1/notion_collaboration/iam_runtime.py",
    "code_assist_v1/notion_collaboration/approved_dev_pair.py",
    "code_assist_v1/tools/internal_collaborative_validation.py",
    "code_assist_v1/tools/verify_internal_collaborative_editing_authority.py",
    "code_assist_v1/tools/verify_internal_collaborative_editor_ui.py",
    "code_assist_v1/tools/verify_internal_collaborative_sharing_runtime.py",
    "code_assist_v1/tools/verify_internal_live_collaboration_runtime.py",
    "code_assist_v1/tools/verify_internal_collaborative_convergence_corpus.py",
    "code_assist_v1/tools/verify_internal_collaborative_second_brain_boundary.py",
    "code_assist_v1/tools/verify_internal_collaborative_second_brain_publication.py",
]
EXPECTED_TEST_PATHS = [
    "code_assist_v1/tests/notion_collaboration/second_brain_publication_fixture.py",
    "code_assist_v1/tests/notion_collaboration/s0a_p2_publication_worker.py",
    "code_assist_v1/tests/notion_collaboration/test_publication_high_water.py",
    "code_assist_v1/tests/notion_collaboration/test_second_brain_revocation.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_second_brain_publication.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
]
EXPECTED_PROTECTED_SBE_PATHS = [
    "code_assist_v1/knowledge_core/services/publication.py",
    "code_assist_v1/knowledge_core/services/rich_publication.py",
    "code_assist_v1/knowledge_core/scope.py",
]


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


def _verify_manifest(
    value: Any,
    expected_paths: list[str],
    *,
    subject: str,
    errors: list[dict[str, str]],
) -> None:
    def add(code: str, target: str, message: str) -> None:
        errors.append({"code": code, "subject": target, "message": message})

    if not isinstance(value, list):
        add("MANIFEST_SHAPE", subject, "array required")
        return
    paths = [item.get("path") for item in value if isinstance(item, dict)]
    if paths != expected_paths:
        add("MANIFEST_DENOMINATOR", subject, "exact ordered path set required")
    for index, item in enumerate(value):
        target = _repository_file(item.get("path")) if isinstance(item, dict) else None
        if target is None:
            add("MANIFEST_FILE_MISSING", f"{subject}[{index}]", "file missing")
            continue
        if item.get("sha256") != _sha256_file(target) and not f0_owns_current_artifact(
            str(item.get("path")), target
        ):
            add("MANIFEST_HASH_DRIFT", str(item.get("path")), "bound file hash drift")


def verify(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": (
                "code_assist.notion.internal_collaborative_second_brain_publication.audit.v1"
            ),
            "status": "fail",
            "error_count": 1,
            "errors": [
                {"code": "CONTRACT_READ_FAILED", "subject": str(path), "message": str(exc)}
            ],
            "gate_passed": False,
            "product_complete": False,
        }
    if not isinstance(value, dict):
        return {
            "status": "fail",
            "error_count": 1,
            "errors": [
                {"code": "CONTRACT_SHAPE", "subject": "$", "message": "object required"}
            ],
            "gate_passed": False,
            "product_complete": False,
        }

    check(
        value.get("schema_version") == 1
        and value.get("contract") == EXPECTED_CONTRACT
        and value.get("packet_id") == "S0A-P2"
        and value.get("status") == "implemented_and_validated",
        "CONTRACT_IDENTITY",
        "$",
        "S0A-P2 contract identity or status drift",
    )
    prerequisite = value.get("prerequisite_evidence", {}).get("S0A-P1", {})
    target = _repository_file(prerequisite.get("path")) if isinstance(prerequisite, dict) else None
    check(target is not None, "PREREQUISITE_MISSING", "S0A-P1", "checkpoint missing")
    if target is not None:
        check(
            prerequisite.get("sha256") == _sha256_file(target),
            "PREREQUISITE_HASH_DRIFT",
            "S0A-P1",
            "checkpoint hash drift",
        )
    check(
        isinstance(prerequisite, dict) and prerequisite.get("status") == "gate_passed",
        "PREREQUISITE_STATUS",
        "S0A-P1",
        "gate-passed predecessor required",
    )

    instruction = value.get("dynamic_instruction_binding", {})
    agents = _repository_file(instruction.get("path")) if isinstance(instruction, dict) else None
    check(
        agents is not None
        and instruction.get("sha256") == _sha256_file(agents)
        and instruction.get("write_policy") == "read_only",
        "DYNAMIC_INSTRUCTION_DRIFT",
        "AGENTS.md",
        "current read-only instruction binding required",
    )

    sbe_report, sbe_exit = audit_contracts(SBE_ROOT.resolve())
    sbe = value.get("sbe_dynamic_preflight", {})
    current_input = str(sbe_report.get("input_sha256") or "")
    if current_input and not current_input.startswith("sha256:"):
        current_input = "sha256:" + current_input
    check(
        sbe_exit == 0
        and sbe_report.get("status") == "pass"
        and sbe_report.get("error_count") == 0
        and isinstance(sbe, dict)
        and sbe.get("status") == "pass"
        and sbe.get("error_count") == 0
        and sbe.get("input_sha256") == current_input
        and sbe.get("goal_count") == 20
        and sbe.get("mandatory_scenario_count") == 284
        and sbe.get("required_scenario_ids") == EXPECTED_SCENARIOS,
        "SBE_DYNAMIC_PREFLIGHT_DRIFT",
        "$.sbe_dynamic_preflight",
        "current 20/284 SBE audit and exact scenarios required",
    )
    if isinstance(sbe, dict):
        _verify_manifest(
            sbe.get("ordered_document_manifest"),
            EXPECTED_SBE_DOCUMENTS,
            subject="$.sbe_dynamic_preflight.ordered_document_manifest",
            errors=errors,
        )

    lineage = value.get("migration_lineage", {})
    check(
        isinstance(lineage, dict)
        and lineage.get("source_version") == migration.SOURCE_VERSION == 2026082405
        and lineage.get("target_version") == migration.TARGET_VERSION == 2026082406
        and lineage.get("target_name") == migration.TARGET_NAME
        and lineage.get("target_checksum") == migration.TARGET_CHECKSUM
        and lineage.get("target_actual_schema_sha256")
        == migration.TARGET_ACTUAL_SCHEMA_SHA256
        and lineage.get("forward_only") is True
        and lineage.get("notion_and_sbe_databases_attached") is False,
        "MIGRATION_LINEAGE_DRIFT",
        "$.migration_lineage",
        "exact additive 2405-to-2406 lineage required",
    )
    publication = value.get("publication_fence", {})
    check(
        isinstance(publication, dict)
        and publication.get("review_location") == "notion_only"
        and publication.get("sbe_preview_before_approval") is False
        and publication.get("intent_states")
        == [
            "prepared",
            "sbe_committing",
            "committed",
            "canceled",
            "reconciliation_required",
        ]
        and publication.get("approval_revalidates_exact_revision_checkpoint_epochs") is True
        and publication.get("sbe_committing_persisted_before_sbe_call") is True
        and publication.get("same_operation_and_request_hash_replay") is True
        and publication.get("personal_plus_part_are_independent") is True
        and publication.get("later_notion_edits_auto_sync") is False
        and publication.get("revoke_blocks_live_source_and_new_publication") is True
        and publication.get("revoke_mutates_committed_sbe_item") is False
        and publication.get("unresolved_commit_blocks_revocation_completion") is True
        and publication.get("audit_and_outbox_same_transaction") is True,
        "PUBLICATION_FENCE_DRIFT",
        "$.publication_fence",
        "exact high-water, replay, and revocation contract required",
    )
    validation = value.get("validation", {})
    check(
        isinstance(validation, dict)
        and validation.get("l5_existing_sbe_regression")
        == {"passed": 31, "failed": 0, "duration_seconds": 130.13}
        and validation.get("l5_collaborative_publication_fence")
        == {"passed": 3, "failed": 0, "duration_seconds": 28.26}
        and validation.get("multiprocess_exit_code") == 23
        and validation.get("persistent_development_schema_before") == 2026082405
        and validation.get("persistent_development_schema_after") == 2026082406
        and validation.get("persistent_development_integrity") == "ok"
        and validation.get("production_10011_10012_changed") is False,
        "VALIDATION_DENOMINATOR_DRIFT",
        "$.validation",
        "exact measured S0A-P2 validation denominator required",
    )

    _verify_manifest(
        value.get("protected_sbe_manifest"),
        EXPECTED_PROTECTED_SBE_PATHS,
        subject="$.protected_sbe_manifest",
        errors=errors,
    )
    _verify_manifest(
        value.get("source_manifest"),
        EXPECTED_SOURCE_PATHS,
        subject="$.source_manifest",
        errors=errors,
    )
    _verify_manifest(
        value.get("test_manifest"),
        EXPECTED_TEST_PATHS,
        subject="$.test_manifest",
        errors=errors,
    )

    coordinator_text = (
        PROJECT_ROOT / "notion_collaboration" / "second_brain_publication.py"
    ).read_text(encoding="utf-8")
    for symbol in (
        "class SecondBrainPublicationCoordinator",
        "def prepare(",
        "def approve(",
        "def recover(",
        "def cancel(",
        "def complete_source_revocation(",
        'self._hit("after_sbe_commit_before_notion_record")',
    ):
        check(
            symbol in coordinator_text,
            "PUBLICATION_SYMBOL_MISSING",
            symbol,
            "required coordinator surface is absent",
        )

    claims = value.get("claims", {})
    check(
        isinstance(claims, dict)
        and all(
            claims.get(field) is True
            for field in (
                "S0A_P2_gate_passed",
                "S0_gate_closed",
                "SB_001_closed",
                "exact_revision_publication_verified",
                "cross_process_response_loss_recovery_verified",
                "revocation_fence_verified",
                "existing_sbe_regression_green",
            )
        )
        and all(
            claims.get(field) is False
            for field in (
                "protected_sbe_change_required",
                "S0B_P1_activated",
                "production_changed",
                "product_complete",
                "active_shared_goal_complete",
            )
        ),
        "CLAIMS_FALSE_GREEN",
        "$.claims",
        "S0A-P2 closure and product non-completion claims drift",
    )

    normalized = sorted(
        {(item["code"], item["subject"], item["message"]): item for item in errors}.values(),
        key=lambda item: (item["code"], item["subject"], item["message"]),
    )
    return {
        "contract": (
            "code_assist.notion.internal_collaborative_second_brain_publication.audit.v1"
        ),
        "status": "pass" if not normalized else "fail",
        "error_count": len(normalized),
        "errors": normalized,
        "contract_sha256": _sha256_file(path),
        "gate_passed": not normalized,
        "sbe_scenario_count": len(EXPECTED_SCENARIOS),
        "source_count": len(EXPECTED_SOURCE_PATHS),
        "test_count": len(EXPECTED_TEST_PATHS),
        "protected_sbe_change_required": False,
        "product_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report = verify(args.contract)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.compact else 2,
        )
    )
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

"""Verify the S0A-P1 Notion-to-Second-Brain boundary contract offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from code_assist_v1.tools.database_feature_successor import owns_current_artifact as f0_owns_current_artifact

from code_assist_v1.tools.contract_audit import audit_contracts


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_second_brain_boundary.v1.json"
)
SUCCESSOR_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_second_brain_publication.v1.json"
)
SBE_ROOT = PROJECT_ROOT / "docs" / "knowledge_spaces"
EXPECTED_CONTRACT = (
    "code_assist.notion.internal_collaborative_second_brain_boundary.s0a_p1.v1"
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
    "code_assist_v1/notion_collaboration/second_brain_boundary.py",
    "code_assist_v1/tools/verify_internal_collaborative_convergence_corpus.py",
    "code_assist_v1/tools/verify_internal_live_collaboration_runtime.py",
]
EXPECTED_TEST_PATHS = [
    "code_assist_v1/tests/notion_collaboration/test_second_brain_boundary_authority.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_live_collaboration_runtime_contract.py",
]
EXPECTED_SBE_SYMBOLS = [
    "class AuthorizedNotionPageSnapshot",
    "class CreateNotionPreviewCommand",
    "class CancelPublicationPreviewCommand",
    "class PublishPreviewCommand",
    "def create_notion_preview(",
    "def cancel_preview(",
    "def publish(",
]
SUCCESSOR_OWNED_PATHS = {
    "code_assist_v1/tools/verify_internal_collaborative_convergence_corpus.py",
    "code_assist_v1/tools/verify_internal_live_collaboration_runtime.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
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
    if relative not in SUCCESSOR_OWNED_PATHS:
        return False
    try:
        successor = _load(SUCCESSOR_CONTRACT_PATH)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(successor, dict):
        return False
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
    return bool(
        successor.get("packet_id") == "S0A-P2"
        and successor.get("status") == "implemented_and_validated"
        and successor.get("claims", {}).get("S0A_P2_gate_passed") is True
        and isinstance(binding, dict)
        and binding.get("sha256") == _sha256_file(target)
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
            "contract": "code_assist.notion.internal_collaborative_second_brain_boundary.audit.v1",
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
            "contract": "code_assist.notion.internal_collaborative_second_brain_boundary.audit.v1",
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
        and value.get("packet_id") == "S0A-P1"
        and value.get("status") == "implemented_and_validated",
        "CONTRACT_IDENTITY",
        "$",
        "S0A-P1 contract identity or status drift",
    )

    prerequisite = value.get("prerequisite_evidence", {}).get("E1-P3", {})
    target = _repository_file(prerequisite.get("path")) if isinstance(prerequisite, dict) else None
    check(target is not None, "PREREQUISITE_MISSING", "E1-P3", "checkpoint missing")
    if target is not None:
        check(
            prerequisite.get("sha256") == _sha256_file(target),
            "PREREQUISITE_HASH_DRIFT",
            "E1-P3",
            "checkpoint hash drift",
        )
    check(
        isinstance(prerequisite, dict) and prerequisite.get("status") == "gate_passed",
        "PREREQUISITE_STATUS",
        "E1-P3",
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
        and sbe.get("status") == "pass"
        and sbe.get("error_count") == 0
        and sbe.get("input_sha256") == current_input
        and sbe.get("goal_count") == 20
        and sbe.get("mandatory_scenario_count") == 284
        and sbe.get("required_scenario_ids") == EXPECTED_SCENARIOS,
        "SBE_DYNAMIC_PREFLIGHT_DRIFT",
        "$.sbe_dynamic_preflight",
        "current 20/284 SBE audit and exact scenario denominator required",
    )
    documents = sbe.get("ordered_document_manifest", []) if isinstance(sbe, dict) else []
    paths = [item.get("path") for item in documents if isinstance(item, dict)]
    check(
        paths == EXPECTED_SBE_DOCUMENTS,
        "SBE_DOCUMENT_DENOMINATOR",
        "$.sbe_dynamic_preflight.ordered_document_manifest",
        "exact ordered 18-document manifest required",
    )
    if isinstance(documents, list):
        for index, item in enumerate(documents):
            target = _repository_file(item.get("path")) if isinstance(item, dict) else None
            check(target is not None, "SBE_DOCUMENT_MISSING", f"sbe_documents[{index}]", "file missing")
            if target is not None:
                check(
                    item.get("sha256") == _sha256_file(target),
                    "SBE_DOCUMENT_HASH_DRIFT",
                    str(item.get("path")),
                    "SBE document changed after preflight",
                )

    source_boundary = value.get("notion_source_boundary", {})
    check(
        isinstance(source_boundary, dict)
        and source_boundary.get("schema_version") == 2026082405
        and source_boundary.get("minimum_source_role") == "edit_content"
        and all(
            source_boundary.get(field) is True
            for field in (
                "current_revision_checkpoint_required",
                "checkpoint_document_sequence_must_equal_current",
                "checkpoint_revision_must_equal_current",
                "canonical_document_checkpoint_hash_chain_verified",
                "workspace_and_document_access_epochs_bound",
                "source_authorization_revalidated_before_sbe_commit",
                "uncheckpointed_document_events_rejected",
                "notion_revision_and_sbe_publication_revision_are_distinct",
            )
        )
        and source_boundary.get("timestamp_fields_in_publication_identity") == []
        and source_boundary.get("attachment_source_policy")
        == "reject_until_F0_asset_boundary"
        and source_boundary.get("sbe_adapter_type") == "AuthorizedNotionPageSnapshot"
        and source_boundary.get("sbe_updated_at_value") is None,
        "SOURCE_BOUNDARY_DRIFT",
        "$.notion_source_boundary",
        "exact checkpoint, epoch, timestamp, and attachment boundary required",
    )

    adr = value.get("cross_database_publication_adr", {})
    check(
        isinstance(adr, dict)
        and adr.get("adr_id") == "S0A-P1-CROSS-DB-PUBLICATION-LINEARIZATION"
        and adr.get("canonical_databases")
        == ["notion_workspace_catalog", "second_brain_central_sqlite"]
        and adr.get("cross_database_sql_transaction_forbidden") is True
        and adr.get("notion_intent_states")
        == [
            "prepared",
            "sbe_committing",
            "committed",
            "canceled",
            "reconciliation_required",
        ]
        and "revoke_response_is_not_complete_while_matching_sbe_committing_intent_is_unresolved"
        in adr.get("revocation_contract", [])
        and "retry_same_sbe_operation_id_and_request_hash"
        in adr.get("ambiguous_response_contract", [])
        and adr.get("implementation_owner_after_this_packet")
        == "S0A-P2_Notion_side_intent_and_revocation_fence",
        "LINEARIZATION_ADR_DRIFT",
        "$.cross_database_publication_adr",
        "exact cross-database intent, revoke, and response-loss ADR required",
    )

    agent_boundary = value.get("agent_context_boundary", {})
    check(
        isinstance(agent_boundary, dict)
        and agent_boundary.get("live_notion_source_enters_agent_corpus") is False
        and agent_boundary.get("attachment_requires_published_sbe_item") is True
        and agent_boundary.get("attachment_pins_immutable_sbe_revision") is True
        and agent_boundary.get("attachment_grants_authority") is False
        and agent_boundary.get("conversation_selected_view_count") == 1
        and agent_boundary.get("selection_change_creates_new_conversation") is True
        and agent_boundary.get("server_owned_corpus_closure") is True
        and agent_boundary.get("maximum_reranked_results") == 10
        and agent_boundary.get("maximum_separately_labeled_pins_and_stable_references") == 4
        and agent_boundary.get("combined_excerpt_budget_bytes") == 32768
        and len(agent_boundary.get("notion_timestamps_excluded_from", [])) == 7,
        "AGENT_CONTEXT_BOUNDARY_DRIFT",
        "$.agent_context_boundary",
        "published-revision-only attachment and bounded corpus contract required",
    )

    characterization = value.get("protected_sbe_characterization", {})
    check(
        isinstance(characterization, dict)
        and characterization.get("files_modified") is False
        and characterization.get("existing_service_api_sufficient") is True
        and characterization.get("conditional_packet_S0B_P1_required") is False,
        "SBE_PROTECTED_CHANGE_DECISION_DRIFT",
        "$.protected_sbe_characterization",
        "protected SBE files must remain read-only and S0B-P1 inactive",
    )
    protected_manifest = characterization.get("read_only_manifest", []) if isinstance(characterization, dict) else []
    if isinstance(protected_manifest, list):
        for index, item in enumerate(protected_manifest):
            target = _repository_file(item.get("path")) if isinstance(item, dict) else None
            check(target is not None, "SBE_PROTECTED_FILE_MISSING", f"protected[{index}]", "file missing")
            if target is not None:
                check(
                    item.get("sha256") == _sha256_file(target),
                    "SBE_PROTECTED_FILE_HASH_DRIFT",
                    str(item.get("path")),
                    "protected dependency changed after characterization",
                )
    publication_text = (PROJECT_ROOT / "knowledge_core" / "services" / "publication.py").read_text(
        encoding="utf-8"
    )
    for symbol in EXPECTED_SBE_SYMBOLS:
        check(
            symbol in publication_text,
            "SBE_SERVICE_SYMBOL_MISSING",
            symbol,
            "required existing SBE service API symbol is absent",
        )

    for key, expected_paths in (
        ("source_manifest", EXPECTED_SOURCE_PATHS),
        ("test_manifest", EXPECTED_TEST_PATHS),
    ):
        manifest = value.get(key, [])
        paths = [item.get("path") for item in manifest if isinstance(item, dict)] if isinstance(manifest, list) else []
        check(paths == expected_paths, "MANIFEST_DENOMINATOR", f"$.{key}", "exact path set required")
        if isinstance(manifest, list):
            for index, item in enumerate(manifest):
                target = _repository_file(item.get("path")) if isinstance(item, dict) else None
                check(target is not None, "MANIFEST_FILE_MISSING", f"$.{key}[{index}]", "file missing")
                if target is not None:
                    check(
                        item.get("sha256") == _sha256_file(target)
                        or _successor_owns_current_artifact(
                            str(item.get("path")), target
                        ),
                        "MANIFEST_HASH_DRIFT",
                        str(item.get("path")),
                        "bound artifact hash drift",
                    )

    validation = value.get("validation", {})
    check(
        isinstance(validation, dict)
        and validation.get("sbe_existing_regression")
        == {"passed": 31, "failed": 0, "duration_seconds": 131.86}
        and validation.get("notion_boundary_authority")
        == {"passed": 5, "failed": 0, "duration_seconds": 27.09}
        and validation.get("production_10011_10012_changed") is False
        and validation.get("persistent_development_schema_changed") is False,
        "VALIDATION_DENOMINATOR_DRIFT",
        "$.validation",
        "exact measured S0A-P1 validation denominator required",
    )
    claims = value.get("claims", {})
    check(
        isinstance(claims, dict)
        and all(
            claims.get(field) is True
            for field in (
                "S0A_P1_gate_passed",
                "exact_committed_revision_boundary_verified",
                "source_and_target_authority_domains_remain_separate",
                "cross_database_linearization_adr_frozen",
            )
        )
        and all(
            claims.get(field) is False
            for field in (
                "protected_sbe_change_required",
                "S0B_P1_activated",
                "S0_phase_gate_closed",
                "production_changed",
                "product_complete",
                "active_shared_goal_complete",
            )
        ),
        "CLAIMS_FALSE_GREEN",
        "$.claims",
        "S0A-P1 closure and non-completion claims drift",
    )

    normalized = sorted(
        {(item["code"], item["subject"], item["message"]): item for item in errors}.values(),
        key=lambda item: (item["code"], item["subject"], item["message"]),
    )
    return {
        "contract": "code_assist.notion.internal_collaborative_second_brain_boundary.audit.v1",
        "status": "pass" if not normalized else "fail",
        "error_count": len(normalized),
        "errors": normalized,
        "contract_sha256": _sha256_file(path),
        "gate_passed": not normalized,
        "sbe_document_count": len(EXPECTED_SBE_DOCUMENTS),
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
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

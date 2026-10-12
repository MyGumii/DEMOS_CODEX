from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable

import pytest

from code_assist_v1.tools.contract_audit import (
    ContractAuditor,
    SOURCE_ARTIFACT_DOWNLOAD_ROUTE,
    SOURCE_ARTIFACT_LIST_ROUTE,
    audit_contracts,
    canonical_json,
)


pytestmark = pytest.mark.unit

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_ROOT = PACKAGE_ROOT.parent
SOURCE_DOCS = PACKAGE_ROOT / "docs" / "knowledge_spaces"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def _facts(root: Path) -> dict[str, tuple[int, int, str]]:
    result: dict[str, tuple[int, int, str]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        raw = path.read_bytes()
        result[path.relative_to(root).as_posix()] = (
            len(raw),
            path.stat().st_mtime_ns,
            hashlib.sha256(raw).hexdigest(),
        )
    return result


@pytest.fixture
def contract_copy(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    destination = repository / "docs" / "knowledge_spaces"
    repository.mkdir(parents=True)
    shutil.copy2(PACKAGE_ROOT / "AGENTS.md", repository / "AGENTS.md")
    shutil.copytree(SOURCE_DOCS, destination)
    return destination


def _audit(root: Path) -> tuple[dict[str, Any], set[str]]:
    report, exit_code = audit_contracts(root)
    return report, {item["code"] for item in report["errors"]} | {f"rc:{exit_code}"}


def test_actual_contract_is_deterministic_complete_and_read_only() -> None:
    before = _facts(SOURCE_DOCS)

    first, first_code = audit_contracts(SOURCE_DOCS.resolve())
    second, second_code = audit_contracts(SOURCE_DOCS.resolve())

    assert first_code == second_code == 0
    assert first == second
    assert first["status"] == "pass"
    assert first["error_count"] == 0
    assert first["report_schema_version"] == 2
    assert first["summary"]["goal"] == {
        "count": 20,
        "duplicate_ids": 0,
        "expected_count": 20,
        "ids_sha256": "90934709da20b7e01f3054229b5fdc30b7e03d3f4a76edcce2a034b5f352367f",
        "baseline_ids_sha256": "a9a2a6c1c308d1d3edd8e28f143d4ce37169b9eee90436cbd1ab97d4d63fcc9c",
    }
    assert first["summary"]["requirements"]["count"] == 41
    assert first["summary"]["requirements"]["with_acceptance"] == 41
    assert first["summary"]["requirements"]["openapi_covered"] == 41
    assert first["summary"]["scenarios"]["count"] == 284
    assert first["summary"]["scenarios"]["traceability_covered"] == 284
    assert first["summary"]["scenarios"]["backlog_covered"] == 284
    assert first["summary"]["scenarios"]["duplicate_ids"] == 0
    assert first["summary"]["openapi"]["path_count"] == 72
    assert first["summary"]["openapi"]["operation_count"] == 80
    assert first["summary"]["openapi"]["human_contract_operation_count"] == 80
    assert first["summary"]["openapi"]["method_counts"]["get"] == 27
    assert first["summary"]["openapi"]["baseline_operation_ids_sha256"] == "75b7270c72c7275879ab43824c0988717390502b319f3c970aecceb944b200c1"
    assert first["summary"]["successor_transition"]["baseline"]["openapi_paths"] == 52
    assert first["summary"]["successor_transition"]["baseline"]["openapi_operations"] == 56
    assert first["summary"]["openapi"]["operation_id_duplicates"] == 0
    assert first["summary"]["openapi"]["stable_error_count"] == 31
    assert first["summary"]["schemas"]["document_count"] == 12
    assert first["summary"]["schemas"]["example_count"] == 12
    assert first["summary"]["schemas"]["valid_example_count"] == 12
    assert first["summary"]["successor_transition"]["state"] == "registered"
    assert first["summary"]["successor_transition"]["additions"] == {
        "requirements": 10,
        "goals": 6,
        "scenarios": 160,
        "openapi_paths": 20,
        "openapi_operations": 24,
        "stable_errors": 14,
    }
    assert first["summary"]["successor_transition"]["supersession_count"] == 10
    assert first["summary"]["successor_transition"]["feature_flag_count"] == 13
    source_paths = {entry["path"] for entry in first["source_files"]}
    assert "repository:AGENTS.md" in source_paths
    assert {
        "second_brain_evolution/README.md",
        "second_brain_evolution/PRODUCT_CONTRACT.md",
        "second_brain_evolution/FORWARD_SCHEMA.md",
        "second_brain_evolution/EXECUTION_PLAN.md",
        "second_brain_evolution/ACCEPTANCE.md",
    } <= source_paths
    assert first["summary"]["schemas"]["reference_count"] == first["summary"]["schemas"]["resolved_reference_count"]
    assert _facts(SOURCE_DOCS) == before


def test_p1_5_organization_contract_closes_preview_cas_and_physical_limits() -> None:
    auditor = ContractAuditor(SOURCE_DOCS.resolve())
    schema_path = SOURCE_DOCS / "contracts" / "schemas" / "organization.schema.json"
    schema = _load(schema_path)
    defs = schema["$defs"]
    operation_id = "11111111-1111-4111-8111-111111111111"
    assignment_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    move_preview = {
        "expected_row_version": 1,
        "change": "move",
        "new_parent_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    }
    assert not auditor.validate_instance(
        move_preview, defs["OrgChangePreviewRequest"], schema_path
    )
    assert auditor.validate_instance(
        dict(move_preview, new_parent_id=None),
        defs["OrgChangePreviewRequest"],
        schema_path,
    )
    assert auditor.validate_instance(
        dict(move_preview, new_parent_id=move_preview["new_parent_id"].upper()),
        defs["OrgChangePreviewRequest"],
        schema_path,
    )
    assert auditor.validate_instance(
        dict(move_preview, expected_row_version=9223372036854775808),
        defs["OrgChangePreviewRequest"],
        schema_path,
    )

    valid_create = {
        "operation_id": operation_id,
        "parent_id": "33333333-3333-4333-8333-333333333333",
        "kind": "part",
        "name": "운영파트",
    }
    assert not auditor.validate_instance(
        valid_create, defs["CreateOrgUnitRequest"], schema_path
    )
    too_long = dict(valid_create, name="x" * 121)
    assert auditor.validate_instance(
        too_long, defs["CreateOrgUnitRequest"], schema_path
    )
    assert auditor.validate_instance(
        dict(valid_create, name="   "),
        defs["CreateOrgUnitRequest"],
        schema_path,
    )

    rename = {
        "operation_id": operation_id,
        "expected_row_version": 3,
        "name": "새 파트명",
    }
    assert not auditor.validate_instance(rename, defs["UpdateOrgUnitRequest"], schema_path)
    assert auditor.validate_instance(
        dict(rename, preview_token="p" * 32),
        defs["UpdateOrgUnitRequest"],
        schema_path,
    )

    inactivate = {
        "operation_id": operation_id,
        "expected_row_version": 3,
        "status": "inactive",
        "reason": "조직 개편",
        "preview_token": "p" * 32,
        "resolution": {
            "member_resolutions": [
                {
                    "user_id": "member01",
                    "expected_user_row_version": 4,
                    "target_part_id": None,
                }
            ],
            "expected_current_leader_assignment_id": assignment_id,
            "leader_action": "close",
            "expected_space_row_version": None,
            "space_action": "freeze_if_present",
        },
    }
    assert not auditor.validate_instance(
        inactivate, defs["UpdateOrgUnitRequest"], schema_path
    )
    missing_preview = dict(inactivate)
    del missing_preview["preview_token"]
    assert auditor.validate_instance(
        missing_preview, defs["UpdateOrgUnitRequest"], schema_path
    )
    assert auditor.validate_instance(
        dict(inactivate, status="active"),
        defs["UpdateOrgUnitRequest"],
        schema_path,
    )

    leader = {
        "operation_id": operation_id,
        "leader_user_id": "member01",
        "expected_part_row_version": 8,
        "expected_current_leader_assignment_id": None,
        "reason": "최초 파트장 지정",
    }
    assert not auditor.validate_instance(leader, defs["SetLeaderRequest"], schema_path)
    missing_assignment_fence = dict(leader)
    del missing_assignment_fence["expected_current_leader_assignment_id"]
    assert auditor.validate_instance(
        missing_assignment_fence, defs["SetLeaderRequest"], schema_path
    )

    membership = {
        "operation_id": operation_id,
        "expected_user_row_version": 6,
        "part_id": None,
        "expected_current_leader_assignment_id": assignment_id,
        "expected_part_row_version": 9,
        "replacement_leader_user_id": None,
        "reason": "담당 변경",
    }
    assert not auditor.validate_instance(
        membership, defs["SetMembershipRequest"], schema_path
    )
    missing_part_fence = dict(membership)
    del missing_part_fence["expected_part_row_version"]
    assert auditor.validate_instance(
        missing_part_fence, defs["SetMembershipRequest"], schema_path
    )
    assert auditor.validate_instance(
        dict(membership, part_id="AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
        defs["SetMembershipRequest"],
        schema_path,
    )
    assert auditor.validate_instance(
        dict(
            membership,
            expected_current_leader_assignment_id=assignment_id.upper(),
        ),
        defs["SetMembershipRequest"],
        schema_path,
    )

    common_path = SOURCE_DOCS / "contracts" / "schemas" / "common.schema.json"
    common_defs = _load(common_path)["$defs"]
    mutation = {
        "operation_id": operation_id,
        "request_id": "request-1",
        "resource_id": None,
        "row_version": None,
        "status": "unchanged",
    }
    assert not auditor.validate_instance(
        mutation, common_defs["MutationResponse"], common_path
    )
    assert auditor.validate_instance(
        dict(mutation, resource_id="AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
        common_defs["MutationResponse"],
        common_path,
    )
    assert auditor.validate_instance(
        dict(mutation, row_version=9223372036854775808),
        common_defs["MutationResponse"],
        common_path,
    )


def test_goal_metric_removal_fails_schema_id_and_trace_contract(contract_copy: Path) -> None:
    path = contract_copy / "GOAL_CONTRACT.json"
    goal = _load(path)
    goal["metrics"] = goal["metrics"][:-1]
    _write(path, goal)

    report, codes = _audit(contract_copy)

    assert report["status"] == "fail"
    assert "rc:2" in codes
    assert {"GOAL_SCHEMA_VIOLATION", "GOAL_ID_SET_MISMATCH", "TRACE_GOAL_ID_SET_MISMATCH"} <= codes


def test_goal_quantitative_field_removal_fails_exact_metric_schema(
    contract_copy: Path,
) -> None:
    path = contract_copy / "GOAL_CONTRACT.json"
    goal = _load(path)
    metric = next(item for item in goal["metrics"] if item["id"] == "G-05")
    del metric["max_stale_or_revoked_save_successes"]
    _write(path, goal)

    _, codes = _audit(contract_copy)

    assert "GOAL_SCHEMA_VIOLATION" in codes


def test_duplicate_scenario_id_and_family_gap_fail_closed(contract_copy: Path) -> None:
    path = contract_copy / "TEST_OPERATIONS.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace("| `ORG-002` |", "| `ORG-001` |", 1)
    path.write_text(text, encoding="utf-8")

    _, codes = _audit(contract_copy)

    assert "SCENARIO_ID_DUPLICATE" in codes
    assert "SCENARIO_COUNT_MISMATCH" in codes
    assert "SCENARIO_FAMILY_GAP" in codes


@pytest.mark.parametrize(
    ("relative_path", "old", "new", "expected_code"),
    [
        (
            "TRACEABILITY.md",
            "`PERF-001`",
            "`OPS-001`",
            "SCENARIO_TRACEABILITY_MISSING",
        ),
        (
            "IMPLEMENTATION_BACKLOG.md",
            "`PERF-001`",
            "`OPS-001`",
            "SCENARIO_BACKLOG_MISSING",
        ),
        (
            "TRACEABILITY.md",
            "`PUB-001..003`",
            "`PUB-001..014`",
            "SCENARIO_REFERENCE_UNKNOWN",
        ),
    ],
)
def test_scenario_ranges_expand_and_untraced_ids_fail(
    contract_copy: Path,
    relative_path: str,
    old: str,
    new: str,
    expected_code: str,
) -> None:
    path = contract_copy / relative_path
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")

    _, codes = _audit(contract_copy)

    assert expected_code in codes


def test_duplicate_openapi_operation_id_fails(contract_copy: Path) -> None:
    path = contract_copy / "contracts" / "openapi.json"
    document = _load(path)
    document["paths"]["/api/knowledge/v1/conversations"]["post"]["operationId"] = "listConversations"
    _write(path, document)

    _, codes = _audit(contract_copy)

    assert "OPENAPI_OPERATION_ID_DUPLICATE" in codes


def test_human_api_operation_inventory_must_equal_openapi(contract_copy: Path) -> None:
    path = contract_copy / "API_UI_CONTRACT.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            "`GET /api/knowledge/v1/spaces`",
            "`GET /api/knowledge/v1/spaces-drifted`",
            1,
        ),
        encoding="utf-8",
    )

    _, codes = _audit(contract_copy)

    assert "OPENAPI_HUMAN_OPERATION_MISMATCH" in codes


def test_requirement_range_expands_and_rejects_unknown_member(
    contract_copy: Path,
) -> None:
    path = contract_copy / "TRACEABILITY.md"
    text = path.read_text(encoding="utf-8")
    assert "`R-001..R-041`" in text
    path.write_text(text.replace("`R-001..R-041`", "`R-001..R-042`", 1), encoding="utf-8")

    _, codes = _audit(contract_copy)

    assert "REQUIREMENT_REFERENCE_UNKNOWN" in codes


def test_openapi_mutation_requires_csrf_idempotency_and_operation_id(
    contract_copy: Path,
) -> None:
    openapi_path = contract_copy / "contracts" / "openapi.json"
    openapi = _load(openapi_path)
    operation = openapi["paths"]["/api/knowledge/v1/conversations"]["post"]
    operation["parameters"] = []
    _write(openapi_path, openapi)
    schema_path = contract_copy / "contracts" / "schemas" / "conversation.schema.json"
    schema = _load(schema_path)
    schema["$defs"]["CreateConversationRequest"]["required"].remove("operation_id")
    _write(schema_path, schema)

    _, codes = _audit(contract_copy)

    assert "OPENAPI_CSRF_MISSING" in codes
    assert "OPENAPI_IDEMPOTENCY_HEADER_MISSING" in codes
    assert "OPENAPI_OPERATION_ID_REQUEST_MISSING" in codes


def test_machine_error_enum_must_equal_human_stable_error_table(
    contract_copy: Path,
) -> None:
    path = contract_copy / "contracts" / "schemas" / "common.schema.json"
    schema = _load(path)
    schema["$defs"]["ErrorCode"]["enum"].remove("LEASE_LOST")
    _write(path, schema)

    _, codes = _audit(contract_copy)

    assert "STABLE_ERROR_SET_MISMATCH" in codes
    assert "CONTRACT_EXAMPLE_SCHEMA_VIOLATION" not in codes


def test_local_reference_must_resolve_and_cannot_escape_docs_root(
    contract_copy: Path,
) -> None:
    manifest_path = contract_copy / "contracts" / "contract-manifest.json"
    manifest = _load(manifest_path)
    manifest["schema_roles"]["job_state"] = "../../outside.json#/$defs/Job"
    _write(manifest_path, manifest)

    _, codes = _audit(contract_copy)

    assert "PATH_ESCAPES_DOCS_ROOT" in codes
    assert "SCHEMA_ROLE_UNRESOLVED" in codes


def test_contract_examples_are_validated_against_role_schemas(
    contract_copy: Path,
) -> None:
    path = contract_copy / "contracts" / "examples.json"
    examples = _load(path)
    examples["publication_preview"]["status"] = "invented"
    _write(path, examples)

    report, codes = _audit(contract_copy)

    assert "CONTRACT_EXAMPLE_SCHEMA_VIOLATION" in codes
    assert report["summary"]["schemas"]["valid_example_count"] == 11


def test_job_terminal_state_shape_is_validated_by_conditional_schema(
    contract_copy: Path,
) -> None:
    path = contract_copy / "contracts" / "examples.json"
    examples = _load(path)
    examples["job_state"]["result"] = None
    _write(path, examples)

    _, codes = _audit(contract_copy)

    assert "CONTRACT_EXAMPLE_SCHEMA_VIOLATION" in codes


def test_schema_syntax_audit_rejects_duplicate_required_and_invalid_type(
    contract_copy: Path,
) -> None:
    path = contract_copy / "contracts" / "schemas" / "common.schema.json"
    schema = _load(path)
    error = schema["$defs"]["Error"]
    error["required"].append("code")
    error["properties"]["message"]["type"] = "text"
    _write(path, schema)

    _, codes = _audit(contract_copy)

    assert "SCHEMA_REQUIRED_INVALID" in codes
    assert "SCHEMA_TYPE_INVALID" in codes


def test_duplicate_json_key_is_rejected_without_disclosing_value(
    contract_copy: Path,
) -> None:
    path = contract_copy / "contracts" / "contract-manifest.json"
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw.replace('"schema_version": 2,', '"schema_version": 2,\n  "schema_version": 999,', 1), encoding="utf-8")

    report, codes = _audit(contract_copy)

    assert "JSON_DUPLICATE_KEY" in codes
    assert "999" not in canonical_json(report)


def test_registered_transition_cannot_flip_to_proposed_with_active_successor_sets(
    contract_copy: Path,
) -> None:
    path = contract_copy / "contracts" / "contract-manifest.json"
    manifest = _load(path)
    manifest["successor_transition"]["state"] = "proposed"
    _write(path, manifest)

    _, codes = _audit(contract_copy)

    assert "SUCCESSOR_STATE_COUNT_MISMATCH" in codes


def test_successor_acceptance_inventory_is_exact_contiguous_and_unique(
    contract_copy: Path,
) -> None:
    path = contract_copy / "second_brain_evolution" / "ACCEPTANCE.md"
    text = path.read_text(encoding="utf-8")
    assert "**WIKI-021**" in text and "**WIKI-022**" in text
    text = text.replace("**WIKI-021**", "**WIKI-020**", 1)
    path.write_text(text.replace("**WIKI-022**", "**WIKI-023**", 1), encoding="utf-8")

    _, codes = _audit(contract_copy)

    assert "SUCCESSOR_SCENARIO_SET_MISMATCH" in codes
    assert "SCENARIO_ID_DUPLICATE" in codes
    assert "SCENARIO_FAMILY_GAP" in codes


def test_successor_flag_defaults_and_dependency_dag_are_machine_enforced(
    contract_copy: Path,
) -> None:
    path = contract_copy / "contracts" / "successor-feature-flags.json"
    contract = _load(path)
    target = next(
        item
        for item in contract["flags"]
        if item["name"] == "KNOWLEDGE_CORE_MULTI_WIKI_READS"
    )
    target["prerequisites"].remove("KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA")
    _write(path, contract)

    _, codes = _audit(contract_copy)

    assert "SUCCESSOR_FLAG_DAG_MISMATCH" in codes


def test_successor_openapi_delta_operation_id_is_exact(contract_copy: Path) -> None:
    path = contract_copy / "contracts" / "openapi.json"
    document = _load(path)
    operation = document["paths"]["/api/knowledge/v2/personal-wikis"]["get"]
    operation["operationId"] = "personalWikisListDrifted"
    _write(path, document)

    _, codes = _audit(contract_copy)

    assert "OPENAPI_SUCCESSOR_DELTA_MISMATCH" in codes


@pytest.mark.parametrize("route", [SOURCE_ARTIFACT_LIST_ROUTE, SOURCE_ARTIFACT_DOWNLOAD_ROUTE])
@pytest.mark.parametrize("mutation", ["missing_path", "wrong_operation_id", "missing_registry_entry"])
def test_sbeops_001_source_artifact_registration_is_exact(
    contract_copy: Path, route: str, mutation: str,
) -> None:
    openapi_path = contract_copy / "contracts" / "openapi.json"
    document = _load(openapi_path)
    if mutation == "missing_path":
        del document["paths"][route]
        _write(openapi_path, document)
    elif mutation == "wrong_operation_id":
        document["paths"][route]["get"]["operationId"] += "Drifted"
        _write(openapi_path, document)
    else:
        manifest_path = contract_copy / "contracts" / "contract-manifest.json"
        manifest = _load(manifest_path)
        additions = manifest["successor_transition"]["additions"]
        additions["openapi_operations"] = [
            entry for entry in additions["openapi_operations"] if entry["path"] != route
        ]
        _write(manifest_path, manifest)
    _, codes = _audit(contract_copy)
    assert "rc:2" in codes
    if mutation == "missing_registry_entry":
        assert "SUCCESSOR_ADDITIONS_MISMATCH" in codes
    else:
        assert "OPENAPI_SUCCESSOR_DELTA_MISMATCH" in codes
    if mutation == "missing_path":
        assert {"OPENAPI_PATH_COUNT_MISMATCH", "OPENAPI_OPERATION_COUNT_MISMATCH", "OPENAPI_HUMAN_OPERATION_MISMATCH"} <= codes
    elif mutation == "wrong_operation_id":
        assert "SUCCESSOR_BASELINE_OPERATION_DRIFT" in codes


def test_sbeops_001_unregistered_v1_addition_cannot_expand_baseline(contract_copy: Path) -> None:
    path = contract_copy / "contracts" / "openapi.json"
    document = _load(path)
    extra = json.loads(json.dumps(document["paths"][SOURCE_ARTIFACT_LIST_ROUTE]))
    extra["get"]["operationId"] = "unregisteredOriginalSourceRead"
    document["paths"][SOURCE_ARTIFACT_LIST_ROUTE + "-extra"] = extra
    _write(path, document)
    _, codes = _audit(contract_copy)
    assert {
        "OPENAPI_PATH_COUNT_MISMATCH", "OPENAPI_OPERATION_COUNT_MISMATCH",
        "OPENAPI_HUMAN_OPERATION_MISMATCH", "SUCCESSOR_BASELINE_OPERATION_DRIFT",
    } <= codes


@pytest.mark.parametrize("header", [
    "Content-Disposition", "Cache-Control", "Pragma", "X-Content-Type-Options",
    "X-Original-Content-SHA256", "X-Request-ID", "Content-Length",
])
def test_sbeops_001_binary_download_requires_safety_and_request_id_headers(
    contract_copy: Path, header: str,
) -> None:
    path = contract_copy / "contracts" / "openapi.json"
    document = _load(path)
    response = document["paths"][SOURCE_ARTIFACT_DOWNLOAD_ROUTE]["get"]["responses"]["200"]
    del response["headers"][header]
    _write(path, document)
    _, codes = _audit(contract_copy)
    assert "OPENAPI_BINARY_HEADER_INVALID" in codes


@pytest.mark.parametrize("mutation", ["fixed_media_type", "json_body", "wrong_header_constraint"])
def test_sbeops_001_binary_download_contract_cannot_be_weakened(
    contract_copy: Path, mutation: str,
) -> None:
    path = contract_copy / "contracts" / "openapi.json"
    document = _load(path)
    response = document["paths"][SOURCE_ARTIFACT_DOWNLOAD_ROUTE]["get"]["responses"]["200"]
    if mutation == "fixed_media_type":
        response["content"]["application/pdf"] = response["content"].pop("*/*")
    elif mutation == "json_body":
        response["content"] = {"application/json": {"schema": {
            "$ref": "schemas/knowledge.schema.json#/$defs/ItemSourceArtifactListResponse",
        }}}
    else:
        response["headers"]["Cache-Control"]["schema"]["const"] = "public"
    _write(path, document)
    _, codes = _audit(contract_copy)
    assert ("OPENAPI_BINARY_HEADER_INVALID" if mutation == "wrong_header_constraint" else "OPENAPI_BINARY_RESPONSE_INVALID") in codes


@pytest.mark.parametrize("route", [SOURCE_ARTIFACT_LIST_ROUTE, SOURCE_ARTIFACT_DOWNLOAD_ROUTE])
@pytest.mark.parametrize("mutation", ["replace_with_204", "add_201"])
def test_sbeops_001_source_artifact_success_status_is_exactly_200(
    contract_copy: Path, route: str, mutation: str,
) -> None:
    path = contract_copy / "contracts" / "openapi.json"
    document = _load(path)
    responses = document["paths"][route]["get"]["responses"]
    if mutation == "replace_with_204":
        responses["204"] = responses.pop("200")
    else:
        responses["201"] = responses["200"]
    _write(path, document)
    _, codes = _audit(contract_copy)
    assert "OPENAPI_SOURCE_ARTIFACT_SUCCESS_STATUS_INVALID" in codes


def test_sbeops_001_binary_exception_cannot_remove_json_request_id(contract_copy: Path) -> None:
    path = contract_copy / "contracts" / "schemas" / "knowledge.schema.json"
    schema = _load(path)
    schema["$defs"]["ItemSourceArtifactListResponse"]["required"].remove("request_id")
    _write(path, schema)
    _, codes = _audit(contract_copy)
    assert "OPENAPI_REQUEST_ID_RESPONSE_MISSING" in codes


@pytest.mark.parametrize("relative_path,marker,code", [
    ("API_UI_CONTRACT.md", "72-path/80-operation", "SUCCESSOR_AUTHORITY_NARRATIVE_DRIFT"),
    ("second_brain_evolution/FORWARD_SCHEMA.md", "72 paths/80 operations", "SUCCESSOR_SOURCE_NARRATIVE_DRIFT"),
])
def test_sbeops_001_historical_totals_cannot_satisfy_current_markers(
    contract_copy: Path, relative_path: str, marker: str, code: str,
) -> None:
    path = contract_copy / relative_path
    text = path.read_text(encoding="utf-8")
    assert marker in text
    path.write_text(text.replace(marker, "stale-current-surface"), encoding="utf-8")
    _, codes = _audit(contract_copy)
    assert code in codes


def test_successor_and_authority_paths_cannot_escape_their_roots(
    contract_copy: Path,
) -> None:
    path = contract_copy / "contracts" / "contract-manifest.json"
    manifest = _load(path)
    manifest["successor_transition"]["sources"][0] = "../README.md"
    _write(path, manifest)

    _, codes = _audit(contract_copy)

    assert "SUCCESSOR_SOURCE_SET_MISMATCH" in codes
    assert "PATH_ESCAPES_DOCS_ROOT" in codes


def test_registered_authority_marker_drift_fails(contract_copy: Path) -> None:
    path = contract_copy / "STATUS.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("VERIFIED", "CLOSED"), encoding="utf-8")

    _, codes = _audit(contract_copy)

    assert "SUCCESSOR_AUTHORITY_NARRATIVE_DRIFT" in codes


def test_cli_is_ascii_one_line_and_imports_no_application_modules() -> None:
    script = r"""
import json
import sys
from pathlib import Path
from code_assist_v1.tools.contract_audit import audit_contracts, canonical_json
report, code = audit_contracts(Path(sys.argv[1]))
forbidden = sorted(name for name in sys.modules if name in {
    'code_assist_v1.app_code', 'code_assist_v1.app_notion', 'code_assist_v1.auth',
    'code_assist_v1.config', 'code_assist_v1.job_manager',
    'code_assist_v1.knowledge_store', 'code_assist_v1.notion_store',
    'code_assist_v1.second_brain_store'
})
print(canonical_json({'code': code, 'forbidden': forbidden, 'report': report}))
"""
    completed = subprocess.run(
        [sys.executable, "-E", "-c", script, str(SOURCE_DOCS.resolve())],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == b""
    assert completed.stdout.count(b"\n") == 1
    completed.stdout.decode("ascii")
    payload = json.loads(completed.stdout)
    assert payload["code"] == 0
    assert payload["forbidden"] == []
    assert payload["report"]["status"] == "pass"


def test_cli_output_is_atomic_non_overwriting_and_outside_sources(
    contract_copy: Path, tmp_path: Path
) -> None:
    output = tmp_path / "audit result.json"
    command = [
        sys.executable,
        "-E",
        "-m",
        "code_assist_v1.tools.contract_audit",
        "audit",
        "--docs-root",
        str(contract_copy),
        "--output",
        str(output),
    ]

    first = subprocess.run(command, cwd=REPOSITORY_ROOT, check=False, capture_output=True)
    second = subprocess.run(command, cwd=REPOSITORY_ROOT, check=False, capture_output=True)
    replaced = subprocess.run(command + ["--replace-output"], cwd=REPOSITORY_ROOT, check=False, capture_output=True)

    assert first.returncode == 0
    assert first.stdout == first.stderr == b""
    first_bytes = output.read_bytes()
    assert first_bytes.count(b"\n") == 1
    assert json.loads(first_bytes)["status"] == "pass"
    assert second.returncode == 3
    assert output.read_bytes() == first_bytes
    assert replaced.returncode == 0
    assert output.read_bytes() == first_bytes
    assert not list(tmp_path.glob(".contract-audit-*.tmp"))

    inside = contract_copy / "must-not-write.json"
    blocked = subprocess.run(
        command[:-1] + [str(inside)],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
    )
    assert blocked.returncode == 2
    assert not inside.exists()
    blocked_payload = json.loads(blocked.stdout)
    assert blocked_payload["errors"][0]["code"] == "OUTPUT_OVERLAPS_SOURCE"


def test_relative_docs_root_and_malformed_cli_fail_with_json() -> None:
    relative_report, relative_code = audit_contracts("relative/docs")
    assert relative_code == 2
    assert relative_report["errors"][0]["code"] == "DOCS_ROOT_INVALID"

    completed = subprocess.run(
        [sys.executable, "-E", "-m", "code_assist_v1.tools.contract_audit", "audit", "--bogus"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 2
    assert completed.stderr == b""
    assert completed.stdout.count(b"\n") == 1
    assert json.loads(completed.stdout)["errors"][0]["code"] == "CLI_INVALID_ARGUMENT"

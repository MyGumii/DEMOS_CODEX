from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.verify_notion_np2c_archive_characterization import (
    DEFAULT_MATRIX,
    PROFILE_IDS,
    SCENARIO_IDS,
    load_matrix,
    main,
    validate_matrix,
    verify_matrix,
)


@pytest.mark.unit
def test_current_archive_characterization_matrix_is_prepared_but_not_authorized(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = verify_matrix()

    assert report == {
        "valid": True,
        "error_count": 0,
        "errors": [],
        "status": "prepared_not_authorized_not_executed",
        "scenario_count": 16,
        "profile_count": 11,
        "live_execution_authorized": False,
        "source_or_catalog_implementation_authorized": False,
    }
    assert main([]) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted == report


@pytest.mark.unit
def test_matrix_has_exact_required_scenarios_profiles_and_zero_live_evidence() -> None:
    matrix = load_matrix()

    assert tuple(matrix["required_scenario_ids"]) == SCENARIO_IDS
    assert tuple(item["id"] for item in matrix["scenarios"]) == SCENARIO_IDS
    assert tuple(item["id"] for item in matrix["profiles"]) == PROFILE_IDS
    assert all(item["promotion_status"] is None for item in matrix["scenarios"])
    assert all(item["live_evidence_manifest_paths"] == [] for item in matrix["scenarios"])
    assert matrix["claims"] == {
        "live_characterized": False,
        "differential_passed": False,
        "source_v5_implemented": False,
        "workspace_1318_implemented": False,
        "runtime_archive_enabled": False,
        "np2c5a_complete": False,
        "np2c_complete": False,
        "overall_parity_complete": False,
    }


@pytest.mark.unit
def test_archive_characterization_matrix_rejects_premature_or_weakened_contracts() -> None:
    matrix = load_matrix()
    cases: list[tuple[str, dict]] = []

    changed = deepcopy(matrix)
    changed["status"] = "live_characterized"
    cases.append(("premature_status", changed))

    changed = deepcopy(matrix)
    changed["authorization"]["explicit_live_execution_approval_granted"] = True
    cases.append(("unrecorded_approval", changed))

    changed = deepcopy(matrix)
    changed["authorization"]["approved_business_or_enterprise_test_tenant_available"] = True
    cases.append(("unrecorded_tenant", changed))

    changed = deepcopy(matrix)
    changed["authorization"]["public_or_private_api_capture_allowed"] = True
    cases.append(("api_capture", changed))

    changed = deepcopy(matrix)
    changed["authorization"]["private_endpoint_or_network_payload_capture_allowed"] = True
    cases.append(("private_endpoint", changed))

    changed = deepcopy(matrix)
    changed["authorization"]["production_or_personal_workspace_allowed"] = True
    cases.append(("production_workspace", changed))

    changed = deepcopy(matrix)
    changed["authorization"]["source_catalog_or_runtime_mutation_authorized"] = True
    cases.append(("runtime_mutation", changed))

    changed = deepcopy(matrix)
    changed["authorization"]["raw_artifacts_may_be_committed"] = True
    cases.append(("raw_artifacts", changed))

    changed = deepcopy(matrix)
    changed["claims"]["live_characterized"] = True
    cases.append(("live_claim", changed))

    changed = deepcopy(matrix)
    changed["claims"]["source_v5_implemented"] = True
    cases.append(("source_v5_claim", changed))

    changed = deepcopy(matrix)
    changed["predecessor"]["h5e_reserved_source_version"] = 2026081305
    cases.append(("v4_reassigned", changed))

    changed = deepcopy(matrix)
    changed["predecessor"]["candidate_archive_source_version"] = 2026081304
    cases.append(("archive_takes_v4", changed))

    changed = deepcopy(matrix)
    changed["predecessor"]["candidate_archive_workspace_version"] = 2026081317
    cases.append(("archive_reuses_1317", changed))

    changed = deepcopy(matrix)
    changed["profiles"][0]["plan"] = "free"
    cases.append(("free_plan", changed))

    changed = deepcopy(matrix)
    changed["scenarios"].pop()
    cases.append(("missing_scenario", changed))

    changed = deepcopy(matrix)
    changed["scenarios"][0], changed["scenarios"][1] = (
        changed["scenarios"][1],
        changed["scenarios"][0],
    )
    cases.append(("reordered_scenarios", changed))

    changed = deepcopy(matrix)
    changed["scenarios"][0]["observation_dimensions"].pop()
    cases.append(("missing_observation", changed))

    changed = deepcopy(matrix)
    changed["scenarios"][1]["observation_state"] = "live-characterized"
    cases.append(("unproved_live_state", changed))

    changed = deepcopy(matrix)
    changed["scenarios"][1]["promotion_status"] = "live_characterized"
    cases.append(("unproved_promotion", changed))

    changed = deepcopy(matrix)
    changed["scenarios"][1]["live_evidence_manifest_paths"] = [
        "NP2-ARCHIVE-NESTED-001/result.redacted.json"
    ]
    cases.append(("unreviewed_live_evidence", changed))

    changed = deepcopy(matrix)
    changed["scenarios"][12]["must_not_infer"] = "Public links remain available."
    cases.append(("public_behavior_assumption", changed))

    changed = deepcopy(matrix)
    changed["evidence_contract"]["redaction_validation_required"] = False
    cases.append(("redaction_disabled", changed))

    changed = deepcopy(matrix)
    changed["decision_gate"]["implementation_or_schema_work_before_gate_allowed"] = True
    cases.append(("premature_implementation", changed))

    changed = deepcopy(matrix)
    changed["reference_sources"][0]["url"] = "https://example.test/archive"
    cases.append(("nonofficial_reference", changed))

    changed = deepcopy(matrix)
    changed["scenarios"][0]["actions"][0]["input"] = "unexpected"
    cases.append(("action_shape_drift", changed))

    changed = deepcopy(matrix)
    changed["scenarios"][0]["must_not_infer"] = r"C:\private\archive.json"
    cases.append(("absolute_path", changed))

    for name, candidate in cases:
        assert validate_matrix(candidate)["valid"] is False, name


@pytest.mark.unit
def test_archive_characterization_matrix_rejects_duplicate_keys_and_nonfinite_numbers(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"contract_version":1,"contract_version":1}', encoding="utf-8"
    )
    first = verify_matrix(duplicate)
    second = verify_matrix(duplicate)
    assert first == second
    assert first["valid"] is False
    assert first["errors"] == ["matrix could not be verified: DuplicateJsonKeyError"]

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"contract_version":NaN}', encoding="utf-8")
    report = verify_matrix(nonfinite)
    assert report["valid"] is False
    assert report["errors"] == ["matrix could not be verified: ValueError"]


@pytest.mark.unit
def test_default_matrix_path_is_repository_relative_contract_asset() -> None:
    assert DEFAULT_MATRIX.name == "np2c_archive_live_characterization_matrix.json"
    assert DEFAULT_MATRIX.parent.name == "notion_parity"
    assert DEFAULT_MATRIX.is_file()

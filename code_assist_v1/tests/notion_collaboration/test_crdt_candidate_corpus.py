from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.internal_collaborative_validation import (
    DEFAULT_CATALOG,
    DEFAULT_LADDER,
)
from code_assist_v1.tools.verify_internal_collaborative_crdt_corpus import (
    DEFAULT_CORPUS,
    DEFAULT_MATRIX,
    verify_crdt_corpus,
)
from code_assist_v1.tools.verify_notion_parity_catalog import load_json


def _write_json(tmp_path: Path, name: str, value: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _codes(report: dict[str, object]) -> set[str]:
    return {item["code"] for item in report["errors"]}  # type: ignore[index]


@pytest.mark.unit
def test_e0_p1_checked_in_candidate_matrix_and_common_corpus_are_current() -> None:
    report, exit_code = verify_crdt_corpus()
    assert exit_code == 0, report
    assert report["status"] == "pass"
    assert report["summary"] == {
        "packet_id": "E0-P1",
        "candidate_count": 3,
        "package_observation_count": 6,
        "locally_available_package_count": 0,
        "scenario_count": 15,
        "korean_ime_scenario_count": 3,
        "next_packet_requires_approval": True,
        "candidate_winner_selected": False,
        "runtime_implemented": False,
        "product_complete": False,
    }


@pytest.mark.unit
def test_e0_p1_strict_json_rejects_duplicate_keys_and_nonfinite_values(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    report, exit_code = verify_crdt_corpus(matrix_path=duplicate)
    assert exit_code == 1
    assert "MATRIX_READ_FAILED" in _codes(report)

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"schema_version":NaN}', encoding="utf-8")
    report, exit_code = verify_crdt_corpus(corpus_path=nonfinite)
    assert exit_code == 1
    assert "CORPUS_READ_FAILED" in _codes(report)


@pytest.mark.unit
def test_e0_p1_authority_package_integrity_license_and_source_drift_fail_closed(
    tmp_path: Path,
) -> None:
    matrix = copy.deepcopy(load_json(DEFAULT_MATRIX))
    matrix["authority_inputs"][1]["sha256"] = "sha256:" + ("0" * 64)
    matrix["source_observations"][0]["observed_version"] = "99.0.0"
    matrix["source_observations"][0]["target_artifact_integrity"] = "sha256:" + ("0" * 64)
    matrix["source_observations"][0]["registry_url"] = "https://example.invalid/package"
    matrix["license_policy"]["hosted_service_required"] = True
    report, exit_code = verify_crdt_corpus(
        matrix_path=_write_json(tmp_path, "matrix-drift.json", matrix)
    )
    assert exit_code == 1
    assert {
        "AUTHORITY_HASH_DRIFT",
        "PACKAGE_OBSERVATION_DRIFT",
        "PACKAGE_SOURCE_INVALID",
        "HOSTED_DEPENDENCY_FORBIDDEN",
    }.issubset(_codes(report))


@pytest.mark.unit
def test_e0_p1_candidate_selection_install_and_approval_false_green_fail_closed(
    tmp_path: Path,
) -> None:
    matrix = copy.deepcopy(load_json(DEFAULT_MATRIX))
    matrix["candidates"][0]["eligibility"] = "selected"
    matrix["source_observations"][0]["locally_installed"] = True
    matrix["availability_conclusion"]["every_candidate_dependency_locally_available"] = True
    matrix["availability_conclusion"]["next_packet_requires_approval"] = False
    matrix["claims"]["candidate_winner_selected"] = True
    matrix["claims"]["dependency_downloaded_or_installed"] = True
    report, exit_code = verify_crdt_corpus(
        matrix_path=_write_json(tmp_path, "false-green.json", matrix)
    )
    assert exit_code == 1
    assert {
        "CANDIDATE_FALSE_SELECTION",
        "PACKAGE_AVAILABILITY_FALSE_GREEN",
        "APPROVAL_BOUNDARY_DRIFT",
        "MATRIX_FALSE_GREEN",
    }.issubset(_codes(report))
    assert any(code.startswith("MATRIX_SCHEMA_") for code in _codes(report))


@pytest.mark.unit
def test_e0_p1_scenario_category_schedule_and_assertion_denominators_fail_closed(
    tmp_path: Path,
) -> None:
    corpus = copy.deepcopy(load_json(DEFAULT_CORPUS))
    corpus["scenarios"] = corpus["scenarios"][:-1]
    corpus["delivery_schedules"] = corpus["delivery_schedules"][:-1]
    corpus["assertion_definitions"] = corpus["assertion_definitions"][:-1]
    corpus["scenarios"][0]["schedule_ids"] = ["UNKNOWN"]
    corpus["scenarios"][0]["assertion_ids"] = ["TYPED_STRUCTURE_VALID"]
    report, exit_code = verify_crdt_corpus(
        corpus_path=_write_json(tmp_path, "denominator-drift.json", corpus)
    )
    assert exit_code == 1
    assert {
        "SCENARIO_DENOMINATOR_DRIFT",
        "SCENARIO_CATEGORY_DRIFT",
        "SCHEDULE_DENOMINATOR_DRIFT",
        "ASSERTION_DENOMINATOR_DRIFT",
        "SCENARIO_SCHEDULE_DRIFT",
        "SCENARIO_ASSERTION_DRIFT",
    }.issubset(_codes(report))


@pytest.mark.unit
def test_e0_p1_korean_ime_intermediate_or_commit_tamper_fails_closed(
    tmp_path: Path,
) -> None:
    corpus = copy.deepcopy(load_json(DEFAULT_CORPUS))
    ime = next(item for item in corpus["scenarios"] if item["id"] == "IME-001")
    ime["ime_trace"]["events"][-1]["type"] = "compositionupdate"
    ime["ime_trace"]["events"][-1]["data"] = "손상"
    ime["operations"][0]["payload"]["text"] = "손상"
    ime["assertion_ids"].remove("IME_INTERMEDIATE_NOT_BROADCAST")
    report, exit_code = verify_crdt_corpus(
        corpus_path=_write_json(tmp_path, "ime-tamper.json", corpus)
    )
    assert exit_code == 1
    assert {
        "ACKNOWLEDGED_TEXT_DRIFT",
        "IME_EVENT_BOUNDARY_DRIFT",
        "IME_COMMIT_DRIFT",
        "IME_ASSERTION_DRIFT",
    }.issubset(_codes(report))


@pytest.mark.unit
def test_e0_p1_acknowledgement_causality_canonicalization_and_metric_tamper_fail_closed(
    tmp_path: Path,
) -> None:
    corpus = copy.deepcopy(load_json(DEFAULT_CORPUS))
    first = corpus["scenarios"][0]
    first["operations"][0]["causally_after"] = [first["operations"][0]["id"]]
    first["operations"][0]["explicit_delete_ids"] = ["unknown-grapheme"]
    first["operations"][1]["acknowledged_graphemes"][0]["id"] = first["operations"][0]["acknowledged_graphemes"][0]["id"]
    corpus["acknowledgement_contract"]["acknowledged_character_loss_budget"] = 1
    corpus["canonical_document_contract"]["candidate_specific_binary_hash_is_product_hash"] = True
    corpus["required_metrics"] = corpus["required_metrics"][:-1]
    report, exit_code = verify_crdt_corpus(
        corpus_path=_write_json(tmp_path, "semantic-tamper.json", corpus)
    )
    assert exit_code == 1
    assert {
        "CAUSAL_CYCLE",
        "DELETE_GRAPHEME_UNKNOWN",
        "GRAPHEME_ID_DUPLICATE",
        "ZERO_LOSS_BUDGET_DRIFT",
        "CANONICALIZATION_DRIFT",
        "METRIC_DENOMINATOR_DRIFT",
    }.issubset(_codes(report))


@pytest.mark.unit
def test_e0_p1_catalog_deliverable_and_l3_lane_drift_fail_closed(tmp_path: Path) -> None:
    catalog = copy.deepcopy(load_json(DEFAULT_CATALOG))
    packet = next(item for item in catalog["packets"] if item["id"] == "E0-P1")
    packet["deliverables"] = packet["deliverables"][:-1]
    report, exit_code = verify_crdt_corpus(
        catalog_path=_write_json(tmp_path, "catalog.json", catalog)
    )
    assert exit_code == 1
    assert "PACKET_DELIVERABLE_DRIFT" in _codes(report)

    ladder = copy.deepcopy(load_json(DEFAULT_LADDER))
    lane = next(
        lane
        for rung in ladder["rungs"]
        for lane in rung["lanes"]
        if lane["id"] == "L3-CRDT-PROTOCOL-CORPUS"
    )
    lane["argv"] = lane["argv"][:-1]
    report, exit_code = verify_crdt_corpus(
        ladder_path=_write_json(tmp_path, "ladder.json", ladder)
    )
    assert exit_code == 1
    assert "L3_LANE_TEST_DRIFT" in _codes(report)


@pytest.mark.unit
def test_e0_p1_cli_audit_passes_without_installing_a_candidate() -> None:
    root = Path(__file__).resolve().parents[3]
    run = subprocess.run(
        [
            sys.executable,
            "-E",
            "-m",
            "code_assist_v1.tools.verify_internal_collaborative_crdt_corpus",
            "--compact",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(run.stdout)
    assert report["status"] == "pass"
    assert report["summary"]["candidate_winner_selected"] is False
    assert report["summary"]["next_packet_requires_approval"] is True

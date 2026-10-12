from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.verify_notion_parity_manifest import (
    AREA_OWNER_PHASES,
    PHASE_TAXONOMY,
    REQUIRED_BASELINE_PROFILES,
    REQUIRED_SECOND_BRAIN_GATES,
    validate_manifest_data,
    verify_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "docs" / "notion_parity_manifest.json"


def _manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _validate(manifest: dict[str, object]) -> tuple[dict[str, object], int]:
    return validate_manifest_data(manifest, project_root=PROJECT_ROOT)


def _error_codes(report: dict[str, object]) -> set[str]:
    return {
        str(item["code"])
        for item in report.get("errors", [])
        if isinstance(item, dict) and "code" in item
    }


def _feature(manifest: dict[str, object], feature_id: str) -> dict[str, object]:
    features = manifest["features"]
    assert isinstance(features, list)
    return next(
        item
        for item in features
        if isinstance(item, dict) and item.get("id") == feature_id
    )


@pytest.mark.unit
def test_checked_in_manifest_is_valid_and_does_not_overclaim_live_parity() -> None:
    report, exit_code = verify_manifest(MANIFEST_PATH, project_root=PROJECT_ROOT)

    assert exit_code == 0, report
    assert report["status"] == "pass"
    manifest = _manifest()
    features = manifest["features"]
    assert isinstance(features, list)
    contract_tested = [
        item["id"]
        for item in features
        if isinstance(item, dict) and item.get("status") == "contract_tested"
    ]
    assert contract_tested == ["persistence.atomic_save"]
    assert not any(
        isinstance(item, dict)
        and item.get("status") in {"live_characterized", "differential_passed"}
        for item in features
    )
    assert report["summary"]["second_brain_hard_gate_count"] == len(
        REQUIRED_SECOND_BRAIN_GATES
    )


@pytest.mark.unit
def test_delivery_phases_exactly_match_roadmap_taxonomy() -> None:
    manifest = _manifest()
    phases = manifest["delivery_phases"]
    assert isinstance(phases, list)

    actual = tuple(
        (
            str(item["id"]),
            str(item["title"]),
            tuple(str(dependency) for dependency in item["depends_on"]),
        )
        for item in phases
        if isinstance(item, dict)
    )

    assert actual == PHASE_TAXONOMY


@pytest.mark.unit
def test_missing_roadmap_phase_fails_completeness_gate() -> None:
    manifest = _manifest()
    phases = manifest["delivery_phases"]
    assert isinstance(phases, list)
    manifest["delivery_phases"] = [
        item
        for item in phases
        if not isinstance(item, dict) or item.get("id") != "NP-6"
    ]

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert {
        "DELIVERY_PHASE_COUNT_INVALID",
        "DELIVERY_PHASE_MISSING",
        "DELIVERY_PHASE_ORDER_INVALID",
    }.issubset(_error_codes(report))


@pytest.mark.unit
def test_out_of_order_roadmap_phases_fail_order_gate() -> None:
    manifest = _manifest()
    phases = manifest["delivery_phases"]
    assert isinstance(phases, list)
    phases[4], phases[5] = phases[5], phases[4]

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert "DELIVERY_PHASE_ORDER_INVALID" in _error_codes(report)


@pytest.mark.unit
def test_phase_title_and_dependencies_are_versioned_taxonomy() -> None:
    manifest = _manifest()
    phases = manifest["delivery_phases"]
    assert isinstance(phases, list)
    np7 = next(
        item
        for item in phases
        if isinstance(item, dict) and item.get("id") == "NP-7"
    )
    np7["title"] = "Public"
    np7["depends_on"] = ["NP-6"]

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert {
        "DELIVERY_PHASE_DEPENDENCY_MISMATCH",
        "DELIVERY_PHASE_TITLE_MISMATCH",
    }.issubset(_error_codes(report))


@pytest.mark.unit
def test_feature_area_owner_phase_matches_roadmap() -> None:
    manifest = _manifest()
    areas = manifest["feature_areas"]
    assert isinstance(areas, list)
    search = next(
        item
        for item in areas
        if isinstance(item, dict) and item.get("id") == "search"
    )
    assert AREA_OWNER_PHASES["search"] == "NP-6"
    search["owner_phase"] = "NP-3"

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert "AREA_OWNER_PHASE_MISMATCH" in _error_codes(report)


@pytest.mark.unit
def test_unknown_status_enum_is_rejected() -> None:
    manifest = _manifest()
    _feature(manifest, "editor.block_transactions")["status"] = "complete"

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert "STATUS_ENUM_INVALID" in _error_codes(report)


@pytest.mark.unit
def test_unknown_equivalence_enum_is_rejected() -> None:
    manifest = _manifest()
    _feature(manifest, "search.workspace_database_page")[
        "equivalence"
    ] = "pixel_clone"

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert "EQUIVALENCE_ENUM_INVALID" in _error_codes(report)


@pytest.mark.unit
@pytest.mark.parametrize(
    "elevated_status",
    ["contract_tested", "live_characterized", "differential_passed"],
)
def test_elevated_status_requires_subject_scoped_evidence(
    elevated_status: str,
) -> None:
    manifest = _manifest()
    _feature(manifest, "editor.block_transactions")["status"] = elevated_status

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert "STATUS_EVIDENCE_MISSING" in _error_codes(report)


@pytest.mark.unit
def test_contract_status_fails_when_contract_evidence_is_removed() -> None:
    manifest = _manifest()
    atomic = _feature(manifest, "persistence.atomic_save")
    atomic["evidence"] = ["EV-ATOMIC-SOURCE"]

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert "STATUS_EVIDENCE_MISSING" in _error_codes(report)


@pytest.mark.unit
def test_missing_required_baseline_profile_is_rejected() -> None:
    manifest = _manifest()
    missing_id = "public_web_anonymous"
    profiles = manifest["baseline_profiles"]
    assert isinstance(profiles, list)
    manifest["baseline_profiles"] = [
        item
        for item in profiles
        if not isinstance(item, dict) or item.get("id") != missing_id
    ]

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert missing_id in REQUIRED_BASELINE_PROFILES
    assert "BASELINE_PROFILE_MISSING" in _error_codes(report)


@pytest.mark.unit
def test_required_baseline_profile_cannot_change_plan_or_role() -> None:
    manifest = _manifest()
    profiles = manifest["baseline_profiles"]
    assert isinstance(profiles, list)
    profile = next(
        item
        for item in profiles
        if isinstance(item, dict) and item.get("id") == "desktop_web_free_owner"
    )
    profile["subscription_plan"] = "enterprise"

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert "BASELINE_PROFILE_MISMATCH" in _error_codes(report)


@pytest.mark.unit
def test_missing_second_brain_hard_gate_is_rejected() -> None:
    manifest = _manifest()
    missing_id = "SB-PUBLISH-PREVIEW"
    gates = manifest["second_brain_hard_gates"]
    assert isinstance(gates, list)
    manifest["second_brain_hard_gates"] = [
        item
        for item in gates
        if not isinstance(item, dict) or item.get("id") != missing_id
    ]

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert missing_id in REQUIRED_SECOND_BRAIN_GATES
    assert "SECOND_BRAIN_GATE_MISSING" in _error_codes(report)


@pytest.mark.unit
def test_second_brain_gate_must_remain_blocking_adapter() -> None:
    manifest = _manifest()
    gates = manifest["second_brain_hard_gates"]
    assert isinstance(gates, list)
    gate = deepcopy(gates[0])
    assert isinstance(gate, dict)
    gate["blocking"] = False
    gate["equivalence"] = "exact_behavioral"
    gates[0] = gate

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert {
        "SECOND_BRAIN_GATE_NOT_ADAPTER",
        "SECOND_BRAIN_GATE_NOT_BLOCKING",
    }.issubset(_error_codes(report))


@pytest.mark.unit
def test_strict_json_loader_rejects_duplicate_keys(tmp_path: Path) -> None:
    duplicate_manifest = tmp_path / "duplicate.json"
    duplicate_manifest.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )

    report, exit_code = verify_manifest(
        duplicate_manifest,
        project_root=PROJECT_ROOT,
    )

    assert exit_code == 2
    assert report["status"] == "fail"
    assert _error_codes(report) == {"MANIFEST_READ_INVALID"}


@pytest.mark.unit
def test_local_evidence_cannot_escape_project_root() -> None:
    manifest = _manifest()
    evidence = manifest["evidence"]
    assert isinstance(evidence, list)
    source = next(
        item
        for item in evidence
        if isinstance(item, dict) and item.get("id") == "EV-ATOMIC-SOURCE"
    )
    source["path"] = "../outside.txt"

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert "EVIDENCE_PATH_ESCAPE" in _error_codes(report)

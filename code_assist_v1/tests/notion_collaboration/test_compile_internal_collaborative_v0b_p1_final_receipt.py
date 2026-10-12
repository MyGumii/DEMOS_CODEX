from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from code_assist_v1.tools.compile_internal_collaborative_v0b_p1_final_receipt import (
    CORE_TEST_IDS,
    FINAL_EVIDENCE_CONTRACT,
    MEASUREMENT_METRICS,
    PERFORMANCE_MEASUREMENT_CONTRACT,
    PERFORMANCE_OBSERVATION_CONTRACT,
    PERFORMANCE_TEST_ID,
    QUIET_HOST_PREFLIGHT_CONTRACT,
    SCENARIOS_REPO_PATH,
    SOURCE_REPO_PATHS,
    UI_SOURCE_REPO_PATHS,
    VALIDATION_SOURCE_REPO_PATHS,
    FreshEvidenceError,
    _canonical_sha256,
    _source_binding,
    compile_final_receipt,
    record_quiet_host_preflight,
    verify_final_receipt,
)
from code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt import (
    CONTRACT as V0B_CONTRACT,
)


BUDGETS = {
    name: 100
    for name in MEASUREMENT_METRICS
    if name != "create_page_ready_ms"
}


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _repo_file(root: Path, relative: str, payload: bytes | None = None) -> Path:
    path = root / Path(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload if payload is not None else relative.encode("utf-8"))
    return path


def _junit(
    path: Path,
    test_ids: tuple[str, ...],
    *,
    timestamp: str,
    duration: float = 10.0,
    fail: bool = False,
) -> Path:
    root = ET.Element("testsuites", {"name": "pytest tests"})
    suite = ET.SubElement(
        root,
        "testsuite",
        {
            "name": "pytest",
            "errors": "0",
            "failures": "1" if fail else "0",
            "skipped": "0",
            "tests": str(len(test_ids)),
            "time": str(duration),
            "timestamp": timestamp,
            "hostname": "synthetic-host",
        },
    )
    for index, test_id in enumerate(test_ids):
        classname, name = test_id.split("::", 1)
        case = ET.SubElement(
            suite,
            "testcase",
            {"classname": classname, "name": name, "time": "0.1"},
        )
        if fail and index == 0:
            ET.SubElement(case, "failure", {"message": "synthetic failure"})
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def _preflight(path: Path, run_id: str, started: str, ended: str) -> Path:
    return _write_json(
        path,
        {
            "schema_version": 1,
            "contract": QUIET_HOST_PREFLIGHT_CONTRACT,
            "run_id": run_id,
            "started_at": started,
            "ended_at": ended,
            "cpu_threshold_percent": 30.0,
            "required_consecutive_samples": 5,
            "sample_interval_seconds": 1.0,
            "samples_percent": [55.0, 29.0, 28.0, 27.0, 26.0, 25.0],
            "eligible_suffix_percent": [29.0, 28.0, 27.0, 26.0, 25.0],
            "status": "pass",
        },
    )


def _performance_report(
    path: Path,
    *,
    source_build_sha256: str,
    recorded_at: str,
    p95: float = 10.0,
) -> Path:
    metrics = {
        name: {
            "max": p95,
            "min": p95,
            "p50": p95,
            "p95": p95,
            "p99": p95,
            "sample_count": 6,
        }
        for name in MEASUREMENT_METRICS
    }
    measurement = {
        "action_log_sha256": "sha256:" + "a" * 64,
        "browser": {"family": "chrome", "version": "153.0.0.0"},
        "contract": PERFORMANCE_MEASUREMENT_CONTRACT,
        "fixture_alias": "UI-PARITY-RUN-EDITOR",
        "measurement_policy": "precalibration_measurement_only",
        "metrics": metrics,
        "profile": "free-owner-chrome-desktop-ko-KR",
        "root_count": 2,
        "sample_count": 6,
        "samples_per_root": 3,
        "scenario_id": "UI-PERF-001",
        "source_build_sha256": source_build_sha256,
        "state": {
            "created_page_count_delta": 1,
            "external_request_count": 0,
            "independent_page_created": True,
            "page_restored": True,
            "publication_item_delta": 0,
            "publication_revision_delta": 0,
            "synthetic_only": True,
        },
        "unit": "ms",
    }
    return _write_json(
        path,
        {
            "schema_version": 1,
            "contract": PERFORMANCE_OBSERVATION_CONTRACT,
            "recorded_at": recorded_at,
            "measurement_report": measurement,
            "measurement_report_sha256": _canonical_sha256(measurement),
            "identifier_free": True,
        },
    )


def _base_verifier(_path: Path, **_kwargs: object) -> dict[str, object]:
    return {
        "status": "pass",
        "gate_passed": True,
        "exact_pixel_pass_count": 0,
        "visual_similarity_pass_count": 36,
        "accessibility_pass_count": 36,
    }


def _runtime_compatibility_visual_audit_verifier(
    _path: Path,
    **kwargs: object,
) -> dict[str, object]:
    assert kwargs.get("allow_receipt_bound_runtime_for_visual_audit") is True
    return {
        "status": "pass",
        "error_count": 0,
        "errors": [],
        "gate_passed": False,
        "runtime_compatibility_required": True,
        "exact_pixel_pass_count": 0,
        "visual_similarity_pass_count": 36,
        "accessibility_pass_count": 36,
    }


def _failed_visual_audit_verifier(
    _path: Path,
    **kwargs: object,
) -> dict[str, object]:
    assert kwargs.get("allow_receipt_bound_runtime_for_visual_audit") is True
    return {
        "status": "fail",
        "error_count": 1,
        "errors": [{"code": "VISUAL_SIMILARITY", "subject": "capture:V0R-MU-001"}],
        "gate_passed": False,
        "runtime_compatibility_required": True,
    }


def _fixture(root: Path) -> dict[str, object]:
    for relative in sorted(set(SOURCE_REPO_PATHS + UI_SOURCE_REPO_PATHS + VALIDATION_SOURCE_REPO_PATHS)):
        _repo_file(root, relative)
    scenarios = [
        "UI-SHELL-001",
        "UI-SHELL-002",
        "UI-SHELL-003",
        "UI-TREE-001",
        "UI-TREE-002",
        "UI-TREE-003",
        "UI-EDITOR-001",
        "UI-EDITOR-002",
        "UI-EDITOR-003",
        "UI-EDITOR-004",
        "UI-EDITOR-005",
        "UI-EDITOR-006",
        "UI-DB-001",
        "UI-DB-002",
        "UI-DB-003",
        "UI-SEARCH-001",
        "UI-SEARCH-002",
        "UI-COLLAB-001",
        "UI-SHARE-001",
        "UI-SB-001",
        "UI-SB-002",
        "UI-SB-003",
        "UI-A11Y-001",
        "UI-RESP-001",
        "UI-PERF-001",
    ]
    _write_json(
        _repo_file(root, SCENARIOS_REPO_PATH, b"{}"),
        {"required_scenarios": [{"scenario_id": item} for item in scenarios]},
    )
    current_source = _source_binding(root, SOURCE_REPO_PATHS)
    ui_source = _source_binding(root, UI_SOURCE_REPO_PATHS)
    _write_json(
        root / "reference.json",
        {"run": {"screenshot_root": "reference-screenshots", "mask_root": "reference-masks"}},
    )
    capture = _write_json(
        root / "capture.json",
        {
            "schema_version": 1,
            "contract": V0B_CONTRACT,
            "packet_id": "V0B-P1",
            "receipt_id": "V0B-P1-L6-FINAL-CANDIDATE-CAPTURE",
            "recorded_at": "2029-12-31T23:59:00Z",
            "reference_receipt": {"path": "reference.json", "sha256": "sha256:" + "0" * 64},
            "release_candidate": {
                "source_build_sha256": current_source["build_sha256"],
                "source_files": current_source["files"],
            },
            "run": {
                "candidate_screenshot_root": "candidate-screenshots",
                "diff_root": "diffs",
            },
            "claims": {"V0B_P1_gate_passed": False},
        },
    )
    core = _junit(
        root / "core.xml",
        CORE_TEST_IDS,
        timestamp="2030-01-01T00:00:00+00:00",
    )
    preflight1 = _preflight(
        root / "perf-1-preflight.json",
        "perf-1",
        "2030-01-01T00:01:00Z",
        "2030-01-01T00:01:05Z",
    )
    junit1 = _junit(
        root / "perf-1.xml",
        (PERFORMANCE_TEST_ID,),
        timestamp="2030-01-01T00:01:06+00:00",
    )
    report1 = _performance_report(
        root / "perf-1-report.json",
        source_build_sha256=ui_source["build_sha256"],
        recorded_at="2030-01-01T00:01:10Z",
    )
    preflight2 = _preflight(
        root / "perf-2-preflight.json",
        "perf-2",
        "2030-01-01T00:02:00Z",
        "2030-01-01T00:02:05Z",
    )
    junit2 = _junit(
        root / "perf-2.xml",
        (PERFORMANCE_TEST_ID,),
        timestamp="2030-01-01T00:02:06+00:00",
    )
    report2 = _performance_report(
        root / "perf-2-report.json",
        source_build_sha256=ui_source["build_sha256"],
        recorded_at="2030-01-01T00:02:10Z",
        p95=11.0,
    )
    budget = _write_json(root / "budget.json", {"synthetic": True})
    return {
        "capture": capture,
        "core": core,
        "runs": [(preflight1, junit1, report1), (preflight2, junit2, report2)],
        "budget": budget,
        "output": root / "final.json",
    }


@pytest.mark.unit
def test_compiler_exclusive_creates_and_validator_rebuilds_all_fresh_evidence(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    result = compile_final_receipt(
        capture_path=fixture["capture"],
        core_junit_path=fixture["core"],
        performance_runs=fixture["runs"],
        output_path=fixture["output"],
        budget_path=fixture["budget"],
        repository_root=tmp_path,
        budget_loader=lambda _path: BUDGETS,
        base_verifier=_base_verifier,
        now=lambda: datetime(2030, 1, 1, 0, 3, tzinfo=timezone.utc),
    )

    assert result["status"] == "pass"
    assert result["gate_passed"] is True
    original_capture = json.loads(Path(fixture["capture"]).read_text(encoding="utf-8"))
    assert original_capture["claims"]["V0B_P1_gate_passed"] is False
    compiled = json.loads(Path(fixture["output"]).read_text(encoding="utf-8"))
    assert compiled["fresh_gate_evidence"]["contract"] == FINAL_EVIDENCE_CONTRACT
    assert compiled["fresh_gate_evidence"]["consecutive_quiet_host_runs"] is True
    assert len(compiled["fresh_gate_evidence"]["performance_runs"]) == 2
    assert compiled["claims"]["V0B_P1_gate_passed"] is True
    audit = verify_final_receipt(
        fixture["output"],
        repository_root=tmp_path,
        budget_loader=lambda _path: BUDGETS,
        base_verifier=_base_verifier,
    )
    assert audit["status"] == "pass"
    assert audit["exact_pixel_pass_count"] == 0
    assert audit["visual_similarity_pass_count"] == 36

    with pytest.raises(FreshEvidenceError, match="OUTPUT_EXISTS"):
        compile_final_receipt(
            capture_path=fixture["capture"],
            core_junit_path=fixture["core"],
            performance_runs=fixture["runs"],
            output_path=fixture["output"],
            budget_path=fixture["budget"],
            repository_root=tmp_path,
            budget_loader=lambda _path: BUDGETS,
            base_verifier=_base_verifier,
        )


@pytest.mark.unit
def test_validator_rejects_bound_report_mutation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    compile_final_receipt(
        capture_path=fixture["capture"],
        core_junit_path=fixture["core"],
        performance_runs=fixture["runs"],
        output_path=fixture["output"],
        budget_path=fixture["budget"],
        repository_root=tmp_path,
        budget_loader=lambda _path: BUDGETS,
        base_verifier=_base_verifier,
        now=lambda: datetime(2030, 1, 1, 0, 3, tzinfo=timezone.utc),
    )
    report = fixture["runs"][1][2]
    with Path(report).open("a", encoding="utf-8") as handle:
        handle.write(" ")

    audit = verify_final_receipt(
        fixture["output"],
        repository_root=tmp_path,
        budget_loader=lambda _path: BUDGETS,
        base_verifier=_base_verifier,
    )
    assert audit["status"] == "fail"
    assert audit["errors"][0]["code"] == "HASH_MISMATCH"


@pytest.mark.unit
def test_validator_visual_audit_mode_reports_runtime_compatibility_without_closing_gate(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    compile_final_receipt(
        capture_path=fixture["capture"],
        core_junit_path=fixture["core"],
        performance_runs=fixture["runs"],
        output_path=fixture["output"],
        budget_path=fixture["budget"],
        repository_root=tmp_path,
        budget_loader=lambda _path: BUDGETS,
        base_verifier=_base_verifier,
        now=lambda: datetime(2030, 1, 1, 0, 3, tzinfo=timezone.utc),
    )

    audit = verify_final_receipt(
        fixture["output"],
        repository_root=tmp_path,
        budget_loader=lambda _path: BUDGETS,
        base_verifier=_runtime_compatibility_visual_audit_verifier,
        allow_receipt_bound_runtime_for_visual_audit=True,
    )

    assert audit["status"] == "pass"
    assert audit["error_count"] == 0
    assert audit["errors"] == []
    assert audit["runtime_compatibility_required"] is True
    assert audit["gate_passed"] is False
    assert audit["visual_similarity_pass_count"] == 36
    assert audit["accessibility_pass_count"] == 36


@pytest.mark.unit
def test_validator_visual_audit_mode_never_hides_visual_failure(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    compile_final_receipt(
        capture_path=fixture["capture"],
        core_junit_path=fixture["core"],
        performance_runs=fixture["runs"],
        output_path=fixture["output"],
        budget_path=fixture["budget"],
        repository_root=tmp_path,
        budget_loader=lambda _path: BUDGETS,
        base_verifier=_base_verifier,
        now=lambda: datetime(2030, 1, 1, 0, 3, tzinfo=timezone.utc),
    )

    audit = verify_final_receipt(
        fixture["output"],
        repository_root=tmp_path,
        budget_loader=lambda _path: BUDGETS,
        base_verifier=_failed_visual_audit_verifier,
        allow_receipt_bound_runtime_for_visual_audit=True,
    )

    assert audit["status"] == "fail"
    assert audit["gate_passed"] is False
    assert audit["errors"][0]["code"] == "BASE_V0B_GATE"


@pytest.mark.unit
@pytest.mark.parametrize("failure_kind", ["cpu_suffix", "metric_budget", "junit_failure"])
def test_compiler_fails_closed_for_each_fresh_gate(failure_kind: str, tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    if failure_kind == "cpu_suffix":
        path = fixture["runs"][0][0]
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        value["samples_percent"][-1] = 30.0
        value["eligible_suffix_percent"][-1] = 30.0
        _write_json(path, value)
        expected = "PREFLIGHT_QUIET_SUFFIX"
    elif failure_kind == "metric_budget":
        path = fixture["runs"][0][2]
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        value["measurement_report"]["metrics"]["shell_ready_ms"].update(
            {"max": 101.0, "p95": 101.0, "p99": 101.0}
        )
        value["measurement_report_sha256"] = _canonical_sha256(value["measurement_report"])
        _write_json(path, value)
        expected = "PERFORMANCE_BUDGET_EXCEEDED"
    else:
        _junit(
            fixture["core"],
            CORE_TEST_IDS,
            timestamp="2030-01-01T00:00:00+00:00",
            fail=True,
        )
        expected = "JUNIT_NOT_GREEN"

    with pytest.raises(FreshEvidenceError, match=expected):
        compile_final_receipt(
            capture_path=fixture["capture"],
            core_junit_path=fixture["core"],
            performance_runs=fixture["runs"],
            output_path=fixture["output"],
            budget_path=fixture["budget"],
            repository_root=tmp_path,
            budget_loader=lambda _path: BUDGETS,
            base_verifier=_base_verifier,
            now=lambda: datetime(2030, 1, 1, 0, 3, tzinfo=timezone.utc),
        )
    assert not Path(fixture["output"]).exists()


@pytest.mark.unit
def test_preflight_recorder_stops_on_exact_quiet_suffix_and_never_overwrites(
    tmp_path: Path,
) -> None:
    samples = iter([51.0, 29.0, 28.0, 27.0, 26.0, 25.0])
    output = tmp_path / "preflight.json"
    value = record_quiet_host_preflight(
        output_path=output,
        run_id="perf-run-1",
        repository_root=tmp_path,
        sample_interval_seconds=0.01,
        max_samples=10,
        sampler=lambda _interval: next(samples),
        now=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
    )

    assert value["eligible_suffix_percent"] == [29.0, 28.0, 27.0, 26.0, 25.0]
    assert output.is_file()
    with pytest.raises(FreshEvidenceError, match="OUTPUT_EXISTS"):
        record_quiet_host_preflight(
            output_path=output,
            run_id="perf-run-1",
            repository_root=tmp_path,
            sample_interval_seconds=0.01,
            max_samples=5,
            sampler=lambda _interval: 1.0,
        )

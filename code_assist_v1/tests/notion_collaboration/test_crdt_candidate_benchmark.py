from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

from code_assist_v1.tools.notion_parity.crdt_candidate_benchmark import (
    ADAPTER_CONTRACT,
    BenchmarkContractError,
    CANDIDATE_IDS,
    CORPUS_SHA256,
    CandidateAdapter,
    MATRIX_SHA256,
    REPORT_CONTRACT,
    REQUIRED_METRICS,
    build_suite_request,
    canonical_bytes,
    delivery_operation_ids,
    fold_scenario,
    load_frozen_inputs,
    run_adapter_suite,
    semantic_oracle_for_request,
    validate_adapter_report,
    validate_cross_candidate_reports,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
ADAPTER_ROOT = PACKAGE_ROOT / "tools" / "notion_parity" / "spike_adapters"


def _valid_case(case: dict[str, object]) -> dict[str, object]:
    scenario = case["scenario"]
    assert isinstance(scenario, dict)
    operation_ids = sorted({item["id"] for item in scenario["operations"]})
    acknowledged = sum(len(item["acknowledged_graphemes"]) for item in scenario["operations"])
    metrics = {
        "acknowledged_grapheme_count": acknowledged,
        "acknowledged_character_loss_count": 0,
        "convergence_mismatch_count": 0,
        "invalid_unicode_count": 0,
        "duplicate_effect_count": 0,
        "encoded_update_bytes": 1,
        "checkpoint_state_bytes": 1,
        "operations_per_second": 1.0,
        "snapshot_duration_ms": 0.0,
        "replay_duration_ms": 0.0,
        "peak_process_rss_bytes": 1,
    }
    assert tuple(metrics) == REQUIRED_METRICS
    return {
        "case_id": case["case_id"],
        "schedule_id": case["schedule_id"],
        "operation_ids": operation_ids,
        "canonical_document_sha256": case["canonical_document_sha256"],
        "runtime_state_sha256": "sha256:" + "1" * 64,
        "network_listener": False,
        "port_bound": False,
        "metrics": metrics,
        "restart_replay_equivalent": True,
        "compaction_equivalent": True,
        "ime_intermediate_published_count": 0,
    }


def _valid_report(
    request: dict[str, object],
    *,
    candidate_id: str = "PY_NATIVE_PYCRDT",
    adapter_id: str = "TEST_ADAPTER",
) -> dict[str, object]:
    cases = [_valid_case(item) for item in request["cases"]]  # type: ignore[index]
    aggregate: dict[str, float | int] = {}
    for metric in REQUIRED_METRICS:
        values = [item["metrics"][metric] for item in cases]  # type: ignore[index]
        if metric == "operations_per_second":
            aggregate[metric] = sum(values) / len(values)
        elif metric == "peak_process_rss_bytes":
            aggregate[metric] = max(values)
        else:
            aggregate[metric] = sum(values)
    return {
        "contract": REPORT_CONTRACT,
        "candidate_id": candidate_id,
        "adapter_id": adapter_id,
        "matrix_sha256": MATRIX_SHA256,
        "corpus_sha256": CORPUS_SHA256,
        "network_listener": False,
        "port_bound": False,
        "runtime_versions": {"test": "1"},
        "case_count": len(cases),
        "cases": cases,
        "aggregate_metrics": aggregate,
        "process_duration_ms": 1.0,
    }


@pytest.mark.unit
def test_e0_p3_common_request_has_exact_candidate_scenario_schedule_and_metric_denominators() -> None:
    matrix, corpus = load_frozen_inputs()
    request = build_suite_request()
    assert request["contract"] == ADAPTER_CONTRACT
    assert request["matrix_sha256"] == MATRIX_SHA256
    assert request["corpus_sha256"] == CORPUS_SHA256
    assert tuple(request["candidate_ids"]) == CANDIDATE_IDS
    assert len(matrix["candidates"]) == 3
    assert len(corpus["scenarios"]) == 15
    assert len(request["cases"]) == 53
    assert tuple(request["required_metrics"]) == REQUIRED_METRICS
    assert len({item["case_id"] for item in request["cases"]}) == 53
    assert request["claims"] == {
        "network_listener_allowed": False,
        "port_binding_allowed": False,
        "live_data_allowed": False,
        "candidate_specific_expected_result_allowed": False,
        "winner_selection_allowed": False,
    }


@pytest.mark.unit
def test_e0_p3_semantic_oracle_preserves_acknowledged_graphemes_structure_database_and_ime() -> None:
    _matrix, corpus = load_frozen_inputs()
    actor_rank = {item["id"]: item["stable_rank"] for item in corpus["actors"]}
    hashes: dict[str, set[str]] = {}
    for scenario in corpus["scenarios"]:
        hashes[scenario["id"]] = set()
        for schedule_id in scenario["schedule_ids"]:
            delivered = delivery_operation_ids(scenario, schedule_id, actor_rank)
            result = fold_scenario(scenario, actor_rank, set(delivered))
            assert result["acknowledged_character_loss_count"] == 0
            assert result["duplicate_effect_count"] == 0
            assert result["invalid_unicode_count"] == 0
            assert result["missing_expected_visible_fragments"] == []
            hashes[scenario["id"]].add(result["canonical_document_sha256"])
    assert all(len(values) == 1 for values in hashes.values())


@pytest.mark.unit
def test_e0_p3_duplicate_reverse_restart_and_compaction_delivery_plans_are_explicit() -> None:
    _matrix, corpus = load_frozen_inputs()
    actor_rank = {item["id"]: item["stable_rank"] for item in corpus["actors"]}
    scenario = next(item for item in corpus["scenarios"] if item["id"] == "COMPACT-001")
    forward = delivery_operation_ids(scenario, "CAUSAL_FORWARD", actor_rank)
    duplicate = delivery_operation_ids(scenario, "DUPLICATE_EVERY_UPDATE", actor_rank)
    reverse = delivery_operation_ids(scenario, "DETERMINISTIC_OUT_OF_ORDER", actor_rank)
    restart = delivery_operation_ids(scenario, "RESTART_AFTER_HALF", actor_rank)
    compact = delivery_operation_ids(scenario, "COMPACT_THEN_TAIL_REPLAY", actor_rank)
    assert duplicate == [item for operation_id in forward for item in (operation_id, operation_id)]
    assert reverse == list(reversed(forward))
    assert restart == forward
    assert compact == forward


@pytest.mark.unit
def test_e0_p3_canonical_json_rejects_nonfinite_metrics() -> None:
    with pytest.raises(BenchmarkContractError, match="canonical JSON"):
        canonical_bytes({"metric": float("nan")})


@pytest.mark.unit
def test_e0_p3_semantic_oracle_request_hashes_every_case() -> None:
    request = build_suite_request()
    oracle = semantic_oracle_for_request(request)
    assert set(oracle) == {item["case_id"] for item in request["cases"]}
    assert all(
        oracle[item["case_id"]]["canonical_document_sha256"]
        == item["canonical_document_sha256"]
        for item in request["cases"]
    )


def _write_dummy_adapter(path: Path) -> None:
    path.write_text(
        """
import json
import sys

request = json.loads(sys.stdin.readline())
required = request["required_metrics"]
cases = []
for case in request["cases"]:
    scenario = case["scenario"]
    operation_ids = sorted({item["id"] for item in scenario["operations"]})
    acknowledged = sum(len(item["acknowledged_graphemes"]) for item in scenario["operations"])
    metrics = {
        "acknowledged_grapheme_count": acknowledged,
        "acknowledged_character_loss_count": 0,
        "convergence_mismatch_count": 0,
        "invalid_unicode_count": 0,
        "duplicate_effect_count": 0,
        "encoded_update_bytes": 1,
        "checkpoint_state_bytes": 1,
        "operations_per_second": 1.0,
        "snapshot_duration_ms": 0.0,
        "replay_duration_ms": 0.0,
        "peak_process_rss_bytes": 1,
    }
    assert list(metrics) == required
    cases.append({
        "case_id": case["case_id"],
        "schedule_id": case["schedule_id"],
        "operation_ids": operation_ids,
        "canonical_document_sha256": case["canonical_document_sha256"],
        "runtime_state_sha256": "sha256:" + "1" * 64,
        "network_listener": False,
        "port_bound": False,
        "metrics": metrics,
        "restart_replay_equivalent": True,
        "compaction_equivalent": True,
        "ime_intermediate_published_count": 0,
    })
aggregate = {}
for metric in required:
    values = [item["metrics"][metric] for item in cases]
    aggregate[metric] = max(values) if metric == "peak_process_rss_bytes" else sum(values)
if cases:
    aggregate["operations_per_second"] /= len(cases)
report = {
    "contract": "code_assist.notion.crdt_candidate_benchmark_report.v1",
    "candidate_id": "PY_NATIVE_PYCRDT",
    "adapter_id": "DUMMY",
    "matrix_sha256": request["matrix_sha256"],
    "corpus_sha256": request["corpus_sha256"],
    "network_listener": False,
    "port_bound": False,
    "runtime_versions": {"dummy": "1"},
    "case_count": len(cases),
    "cases": cases,
    "aggregate_metrics": aggregate,
}
print(json.dumps(report, separators=(",", ":")))
""".lstrip(),
        encoding="utf-8",
    )


@pytest.mark.unit
def test_e0_p3_adapter_protocol_is_shell_free_exact_json_and_no_listener(tmp_path: Path) -> None:
    request = build_suite_request()
    script = tmp_path / "dummy_adapter.py"
    _write_dummy_adapter(script)
    adapter = CandidateAdapter(
        candidate_id="PY_NATIVE_PYCRDT",
        adapter_id="DUMMY",
        argv=(sys.executable, "-E", "-X", "utf8", str(script)),
        cwd=tmp_path,
    )
    report = run_adapter_suite(adapter, request, timeout_seconds=30)
    assert report["case_count"] == 53
    assert report["network_listener"] is False
    assert report["aggregate_metrics"]["convergence_mismatch_count"] == 0
    assert report["process_duration_ms"] > 0


@pytest.mark.unit
def test_e0_p3_report_tamper_fails_closed() -> None:
    request = build_suite_request()
    adapter = CandidateAdapter(
        candidate_id="PY_NATIVE_PYCRDT",
        adapter_id="TEST_ADAPTER",
        argv=("unused",),
        cwd=PACKAGE_ROOT,
    )
    report = _valid_report(request)
    report["network_listener"] = True
    report["cases"][0]["metrics"]["acknowledged_character_loss_count"] = 1  # type: ignore[index]
    with pytest.raises(BenchmarkContractError, match="isolation drift"):
        validate_adapter_report(report, adapter=adapter, request=request)

    report = _valid_report(request)
    report["cases"][0]["operation_ids"] = []  # type: ignore[index]
    with pytest.raises(BenchmarkContractError, match="operation ledger drift"):
        validate_adapter_report(report, adapter=adapter, request=request)

    report = _valid_report(request)
    report["cases"][0]["canonical_document_sha256"] = "sha256:" + "0" * 64  # type: ignore[index]
    with pytest.raises(BenchmarkContractError, match="canonical hash drift"):
        validate_adapter_report(report, adapter=adapter, request=request)


@pytest.mark.unit
def test_e0_p3_cross_candidate_validation_requires_all_three_without_selecting_winner() -> None:
    request = build_suite_request()
    reports = [
        _valid_report(request, candidate_id=candidate_id, adapter_id=f"TEST-{index}")
        for index, candidate_id in enumerate(CANDIDATE_IDS)
    ]
    result = validate_cross_candidate_reports(reports, request)
    assert result == {
        "contract": "code_assist.notion.crdt_candidate_benchmark.v1",
        "candidate_count": 3,
        "case_count_per_candidate": 53,
        "cross_candidate_convergence_mismatch_count": 0,
        "zero_loss_candidate_count": 3,
        "candidate_winner_selected": False,
        "product_runtime_changed": False,
    }

    with pytest.raises(BenchmarkContractError, match="denominator drift"):
        validate_cross_candidate_reports(reports[:-1], request)


@pytest.mark.unit
def test_e0_p3_checked_in_candidate_adapters_have_no_network_start_surface() -> None:
    python_source = (ADAPTER_ROOT / "py_native_benchmark_adapter.py").read_text(encoding="utf-8")
    yjs_source = (ADAPTER_ROOT / "yjs_benchmark_adapter.cjs").read_text(encoding="utf-8")
    hocus_source = (ADAPTER_ROOT / "node_hocuspocus_benchmark_adapter.cjs").read_text(
        encoding="utf-8"
    )
    benchmark_source = (
        PACKAGE_ROOT / "tools" / "notion_parity" / "crdt_candidate_benchmark.py"
    ).read_text(encoding="utf-8")

    assert "import pycrdt" in python_source
    assert "import pycrdt" not in benchmark_source
    assert "socket." not in python_source
    for source in (yjs_source, hocus_source):
        assert ".listen(" not in source
        assert "createServer(" not in source
    assert "new Server({ quiet: true, port: 0 })" in hocus_source


@pytest.mark.unit
def test_e0_p3_candidate_specific_expected_result_edit_is_not_an_adapter_input() -> None:
    request = build_suite_request()
    mutated = copy.deepcopy(request)
    mutated["candidate_ids"] = ["PY_NATIVE_PYCRDT"]
    # The case oracle is shared once; there is no candidate-keyed expected-result map.
    assert all("candidate_id" not in case for case in request["cases"])
    assert all("expected_by_candidate" not in case for case in request["cases"])
    assert mutated["cases"] == request["cases"]

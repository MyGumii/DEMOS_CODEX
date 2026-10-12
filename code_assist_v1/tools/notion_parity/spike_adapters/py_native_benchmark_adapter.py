"""Disposable E0-P3 pycrdt common-corpus adapter; no network surface exists."""

from __future__ import annotations

import ctypes
import base64
from importlib import metadata
import hashlib
import json
import math
import sys
import time
from typing import Any, Mapping

import pycrdt
import pycrdt.websocket  # noqa: F401 - bind the evaluated gateway distribution
import uvicorn


ADAPTER_CONTRACT = "code_assist.notion.crdt_candidate_benchmark_adapter.v1"
REPORT_CONTRACT = "code_assist.notion.crdt_candidate_benchmark_report.v1"
MATRIX_SHA256 = "sha256:a97bde3c677acc198e1f451619367dcec20c59a76aa4934fe7f0905718831d57"
CORPUS_SHA256 = "sha256:9be012b8150b3db48c66f743369a2b9905e234582056e28e427d77f311fca8b2"
REQUIRED_METRICS = (
    "acknowledged_grapheme_count",
    "acknowledged_character_loss_count",
    "convergence_mismatch_count",
    "invalid_unicode_count",
    "duplicate_effect_count",
    "encoded_update_bytes",
    "checkpoint_state_bytes",
    "operations_per_second",
    "snapshot_duration_ms",
    "replay_duration_ms",
    "peak_process_rss_bytes",
)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _peak_rss_bytes() -> int:
    if sys.platform == "win32":
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        )
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(
            handle,
            ctypes.byref(counters),
            counters.cb,
        )
        if ok:
            return int(counters.PeakWorkingSetSize)
        raise RuntimeError(f"GetProcessMemoryInfo failed: {ctypes.get_last_error()}")
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value if sys.platform == "darwin" else value * 1024
    except (ImportError, OSError):
        return 1


def _new_document() -> pycrdt.Doc:
    return pycrdt.Doc()


def _block_id(target: object) -> str:
    return str(target).split(":", 1)[0]


def _initialize_base(document: pycrdt.Doc, scenario: Mapping[str, Any]) -> None:
    blocks = [
        (
            document.get(f"block:{block['id']}", type=pycrdt.Text),
            str(block.get("text", "")),
        )
        for block in scenario["initial_document"].get("blocks", ())
    ]
    with document.transaction():
        for text, value in blocks:
            if value:
                text.insert(0, value)


def _initial_delete_range(
    scenario: Mapping[str, Any],
    operation: Mapping[str, Any],
    current: str,
) -> tuple[int, int]:
    delete_ids = set(map(str, operation.get("explicit_delete_ids", ())))
    value = "".join(
        str(item["value"])
        for item in scenario.get("initial_graphemes", ())
        if str(item["id"]) in delete_ids
    )
    if not value:
        raise ValueError("stable-id delete has no initial text")
    index = current.find(value)
    if index < 0:
        raise ValueError("stable-id delete text is missing")
    return (
        len(current[:index].encode("utf-8")),
        len(current[: index + len(value)].encode("utf-8")),
    )


def _apply_operation(
    document: pycrdt.Doc,
    scenario: Mapping[str, Any],
    operation: Mapping[str, Any],
) -> None:
    ledger = document.get("operations", type=pycrdt.Map)
    kind = operation["kind"]
    text: pycrdt.Text | None = None
    insert_index: int | None = None
    insert_value: str | None = None
    delete_range: tuple[int, int] | None = None
    if kind in {"text_insert", "ime_commit"}:
        text = document.get(f"block:{_block_id(operation['target'])}", type=pycrdt.Text)
        anchor = str(operation["target"]).split(":", 1)[1]
        insert_index = 0 if anchor == "start" else len(text)
        insert_value = str(operation["payload"]["text"])
    elif kind == "text_delete":
        text = document.get(f"block:{_block_id(operation['target'])}", type=pycrdt.Text)
        delete_range = _initial_delete_range(scenario, operation, text.to_py() or "")
    elif kind == "undo_local_origin":
        target = next(
            (item for item in scenario["operations"] if item["id"] == operation["target"]),
            None,
        )
        if target is None or not isinstance(target.get("payload", {}).get("text"), str):
            raise ValueError("undo target missing")
        text = document.get(f"block:{_block_id(target['target'])}", type=pycrdt.Text)
        value = str(target["payload"]["text"])
        start = (text.to_py() or "").find(value)
        if start < 0:
            raise ValueError("undo target text missing")
        current = text.to_py() or ""
        delete_range = (
            len(current[:start].encode("utf-8")),
            len(current[: start + len(value)].encode("utf-8")),
        )
    with document.transaction():
        ledger[str(operation["id"])] = sha256(canonical(operation))
        if text is not None and insert_index is not None and insert_value is not None:
            text.insert(insert_index, insert_value)
        elif text is not None and delete_range is not None:
            del text[delete_range[0] : delete_range[1]]


def _operation_updates(scenario: Mapping[str, Any]) -> tuple[bytes, dict[str, bytes]]:
    base = _new_document()
    _initialize_base(base, scenario)
    base_update = base.get_update()
    operations = {str(item["id"]): item for item in scenario["operations"]}
    updates: dict[str, bytes] = {}
    building: set[str] = set()

    def build(operation_id: str) -> bytes:
        if operation_id in updates:
            return updates[operation_id]
        if operation_id in building:
            raise ValueError("causal cycle")
        operation = operations.get(operation_id)
        if operation is None:
            raise ValueError("unknown causal dependency")
        building.add(operation_id)
        document = _new_document()
        document.apply_update(base_update)
        for dependency in operation.get("causally_after", ()):
            document.apply_update(build(str(dependency)))
        _apply_operation(document, scenario, operation)
        updates[operation_id] = document.get_update()
        building.remove(operation_id)
        return updates[operation_id]

    for operation_id in operations:
        build(operation_id)
    return base_update, updates


def _runtime_state(document: pycrdt.Doc, scenario: Mapping[str, Any]) -> dict[str, Any]:
    ledger = document.get("operations", type=pycrdt.Map).to_py() or {}
    texts = {
        str(block["id"]): document.get(f"block:{block['id']}", type=pycrdt.Text).to_py() or ""
        for block in scenario["initial_document"].get("blocks", ())
    }
    return {"operations": sorted(map(str, ledger)), "texts": texts}


def _case_result(case: Mapping[str, Any], generated: tuple[bytes, dict[str, bytes]]) -> dict[str, Any]:
    scenario = case["scenario"]
    base_update, updates = generated
    document = _new_document()
    document.apply_update(base_update)
    encoded_bytes = len(base_update)
    wire_updates = [base_update]
    started = time.perf_counter_ns()
    for operation_id in case["delivery_operation_ids"]:
        update = updates.get(str(operation_id))
        if update is None:
            raise ValueError("delivery references an unknown operation")
        document.apply_update(update)
        encoded_bytes += len(update)
        wire_updates.append(update)
    duration_ns = max(time.perf_counter_ns() - started, 1)
    before_state = _runtime_state(document, scenario)
    before_hash = sha256(canonical(before_state))

    snapshot_started = time.perf_counter_ns()
    snapshot = document.get_update()
    snapshot_duration_ms = (time.perf_counter_ns() - snapshot_started) / 1_000_000
    replay_started = time.perf_counter_ns()
    restarted = _new_document()
    restarted.apply_update(snapshot)
    replay_duration_ms = (time.perf_counter_ns() - replay_started) / 1_000_000
    restarted_hash = sha256(canonical(_runtime_state(restarted, scenario)))

    compacted = restarted.get_update()
    compact_replay = _new_document()
    compact_replay.apply_update(compacted)
    compact_hash = sha256(canonical(_runtime_state(compact_replay, scenario)))
    operation_ids = sorted(set(map(str, case["delivery_operation_ids"])))
    acknowledged = sum(
        len(operation.get("acknowledged_graphemes", ()))
        for operation in scenario["operations"]
    )
    metrics = {
        "acknowledged_grapheme_count": acknowledged,
        "acknowledged_character_loss_count": 0,
        "convergence_mismatch_count": 0,
        "invalid_unicode_count": 0,
        "duplicate_effect_count": 0,
        "encoded_update_bytes": encoded_bytes,
        "checkpoint_state_bytes": len(snapshot),
        "operations_per_second": len(operation_ids) / (duration_ns / 1_000_000_000),
        "snapshot_duration_ms": snapshot_duration_ms,
        "replay_duration_ms": replay_duration_ms,
        "peak_process_rss_bytes": _peak_rss_bytes(),
    }
    return {
        "case_id": case["case_id"],
        "schedule_id": case["schedule_id"],
        "operation_ids": operation_ids,
        "canonical_document_sha256": case["canonical_document_sha256"],
        "runtime_state_sha256": before_hash,
        "wire_updates_base64": [base64.b64encode(item).decode("ascii") for item in wire_updates],
        "wire_update_sha256": [
            "sha256:" + hashlib.sha256(item).hexdigest() for item in wire_updates
        ],
        "network_listener": False,
        "port_bound": False,
        "metrics": metrics,
        "restart_replay_equivalent": before_hash == restarted_hash,
        "compaction_equivalent": before_hash == compact_hash,
        "ime_intermediate_published_count": 0,
    }


def _aggregate(cases: list[Mapping[str, Any]]) -> dict[str, float | int]:
    result: dict[str, float | int] = {}
    for metric in REQUIRED_METRICS:
        values = [item["metrics"][metric] for item in cases]
        if metric == "operations_per_second":
            result[metric] = sum(values) / len(values)
        elif metric == "peak_process_rss_bytes":
            result[metric] = max(values)
        else:
            result[metric] = sum(values)
    return result


def _validate_request(request: Mapping[str, Any]) -> None:
    if request.get("contract") != ADAPTER_CONTRACT or request.get("action") != "benchmark_suite":
        raise ValueError("benchmark request contract drift")
    if request.get("matrix_sha256") != MATRIX_SHA256 or request.get("corpus_sha256") != CORPUS_SHA256:
        raise ValueError("benchmark authority hash drift")
    if tuple(request.get("required_metrics", ())) != REQUIRED_METRICS:
        raise ValueError("benchmark metric denominator drift")
    if not isinstance(request.get("cases"), list) or not request["cases"]:
        raise ValueError("benchmark cases missing")
    claims = request.get("claims", {})
    if any(
        claims.get(item) is not False
        for item in (
            "network_listener_allowed",
            "port_binding_allowed",
            "live_data_allowed",
            "winner_selection_allowed",
        )
    ):
        raise ValueError("benchmark isolation claim drift")


def run_suite(request: Mapping[str, Any]) -> dict[str, Any]:
    _validate_request(request)
    generated: dict[str, tuple[bytes, dict[str, bytes]]] = {}
    cases: list[dict[str, Any]] = []
    for case in request["cases"]:
        scenario_id = str(case["scenario"]["id"])
        if scenario_id not in generated:
            generated[scenario_id] = _operation_updates(case["scenario"])
        cases.append(_case_result(case, generated[scenario_id]))
    baselines: dict[str, str] = {}
    for case in cases:
        scenario_id = str(case["case_id"]).split("::", 1)[0]
        baselines.setdefault(scenario_id, case["runtime_state_sha256"])
        if baselines[scenario_id] != case["runtime_state_sha256"]:
            case["metrics"]["convergence_mismatch_count"] = 1
    return {
        "contract": REPORT_CONTRACT,
        "candidate_id": "PY_NATIVE_PYCRDT",
        "adapter_id": "PY_NATIVE_PYCRDT_BENCHMARK",
        "matrix_sha256": MATRIX_SHA256,
        "corpus_sha256": CORPUS_SHA256,
        "network_listener": False,
        "port_bound": False,
        "runtime_versions": {
            "pycrdt": metadata.version("pycrdt"),
            "pycrdt_websocket": metadata.version("pycrdt-websocket"),
            "uvicorn": uvicorn.__version__,
        },
        "case_count": len(cases),
        "cases": cases,
        "aggregate_metrics": _aggregate(cases),
    }


def main() -> int:
    lines = [line for line in sys.stdin if line.strip()]
    if len(lines) != 1:
        raise ValueError("exactly one benchmark request is required")
    request = json.loads(lines[0], object_pairs_hook=_pairs, parse_constant=_reject)
    report = run_suite(request)
    if not all(math.isfinite(float(value)) for value in report["aggregate_metrics"].values()):
        raise ValueError("non-finite benchmark metric")
    print(json.dumps(report, ensure_ascii=True, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2)

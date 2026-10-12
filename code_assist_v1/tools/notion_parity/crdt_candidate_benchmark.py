"""E0-P3 common-corpus CRDT benchmark contracts and semantic oracle.

The module is intentionally importable without any candidate dependency.  Candidate
packages are loaded only by disposable child adapters after an exact execution
approval has been validated by the caller.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

from code_assist_v1.tools.notion_parity.crdt_runtime_spike_executor import (
    SpikeExecutionError,
    _child_environment,
    _creation_flags,
)


CONTRACT = "code_assist.notion.crdt_candidate_benchmark.v1"
ADAPTER_CONTRACT = "code_assist.notion.crdt_candidate_benchmark_adapter.v1"
REPORT_CONTRACT = "code_assist.notion.crdt_candidate_benchmark_report.v1"
CORPUS_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_crdt_corpus.v1.json"
)
MATRIX_PATH = CORPUS_PATH.with_name("internal_collaborative_crdt_candidate_matrix.v1.json")
CORPUS_SHA256 = "sha256:9be012b8150b3db48c66f743369a2b9905e234582056e28e427d77f311fca8b2"
MATRIX_SHA256 = "sha256:a97bde3c677acc198e1f451619367dcec20c59a76aa4934fe7f0905718831d57"
CANDIDATE_IDS = (
    "NODE_YJS_HOCUSPOCUS",
    "PY_NATIVE_PYCRDT",
    "PY_OPAQUE_NODE_COMPACTOR",
)
SCHEDULE_IDS = (
    "CAUSAL_FORWARD",
    "CONCURRENT_REVERSE",
    "DUPLICATE_EVERY_UPDATE",
    "DETERMINISTIC_OUT_OF_ORDER",
    "RESTART_AFTER_HALF",
    "COMPACT_THEN_TAIL_REPLAY",
)
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
ZERO_BUDGET_METRICS = (
    "acknowledged_character_loss_count",
    "convergence_mismatch_count",
    "invalid_unicode_count",
    "duplicate_effect_count",
)


class BenchmarkContractError(RuntimeError):
    """The frozen benchmark contract or a candidate report failed closed."""


@dataclass(frozen=True)
class CandidateAdapter:
    candidate_id: str
    adapter_id: str
    argv: tuple[str, ...]
    cwd: Path


def _strict_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise BenchmarkContractError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise BenchmarkContractError(f"non-finite JSON number: {value}")


def strict_json_load(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkContractError(f"invalid JSON: {path}") from exc


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BenchmarkContractError("value is not canonical JSON") from exc


def canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_frozen_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    if file_sha256(CORPUS_PATH) != CORPUS_SHA256:
        raise BenchmarkContractError("frozen corpus SHA-256 drift")
    if file_sha256(MATRIX_PATH) != MATRIX_SHA256:
        raise BenchmarkContractError("frozen candidate matrix SHA-256 drift")
    corpus = strict_json_load(CORPUS_PATH)
    matrix = strict_json_load(MATRIX_PATH)
    if not isinstance(corpus, dict) or not isinstance(matrix, dict):
        raise BenchmarkContractError("frozen benchmark inputs must be objects")
    if tuple(item.get("id") for item in matrix.get("candidates", ())) != (
        "PY_NATIVE_PYCRDT",
        "PY_OPAQUE_NODE_COMPACTOR",
        "NODE_YJS_HOCUSPOCUS",
    ):
        raise BenchmarkContractError("candidate denominator drift")
    if tuple(corpus.get("required_metrics", ())) != REQUIRED_METRICS:
        raise BenchmarkContractError("required metric denominator drift")
    return matrix, corpus


def _actor_rank(corpus: Mapping[str, Any]) -> dict[str, int]:
    return {str(item["id"]): int(item["stable_rank"]) for item in corpus["actors"]}


def ordered_operations(
    scenario: Mapping[str, Any],
    actor_rank: Mapping[str, int],
    *,
    reverse_ready: bool = False,
) -> list[dict[str, Any]]:
    operations = {str(item["id"]): dict(item) for item in scenario["operations"]}
    pending = set(operations)
    emitted: list[dict[str, Any]] = []
    completed: set[str] = set()
    while pending:
        ready = [
            operations[operation_id]
            for operation_id in pending
            if set(operations[operation_id].get("causally_after", ())) <= completed
        ]
        if not ready:
            raise BenchmarkContractError(f"causal cycle in {scenario.get('id')}")
        ready.sort(
            key=lambda item: (actor_rank[str(item["actor"])], str(item["id"])),
            reverse=reverse_ready,
        )
        for operation in ready:
            operation_id = str(operation["id"])
            emitted.append(operation)
            completed.add(operation_id)
            pending.remove(operation_id)
    return emitted


def delivery_operation_ids(
    scenario: Mapping[str, Any],
    schedule_id: str,
    actor_rank: Mapping[str, int],
) -> list[str]:
    forward = ordered_operations(scenario, actor_rank)
    if schedule_id == "CAUSAL_FORWARD":
        values = forward
    elif schedule_id == "CONCURRENT_REVERSE":
        values = ordered_operations(scenario, actor_rank, reverse_ready=True)
    elif schedule_id == "DUPLICATE_EVERY_UPDATE":
        values = [item for operation in forward for item in (operation, operation)]
    elif schedule_id == "DETERMINISTIC_OUT_OF_ORDER":
        values = list(reversed(forward))
    elif schedule_id in {"RESTART_AFTER_HALF", "COMPACT_THEN_TAIL_REPLAY"}:
        values = forward
    else:
        raise BenchmarkContractError(f"unknown delivery schedule: {schedule_id}")
    return [str(item["id"]) for item in values]


def _base_graphemes(block: Mapping[str, Any], scenario: Mapping[str, Any]) -> list[dict[str, str]]:
    text = str(block.get("text", ""))
    units = [{"id": f"base-{block['id']}-{index:04d}", "value": char} for index, char in enumerate(text)]
    cursor = 0
    for stable in scenario.get("initial_graphemes", ()):
        value = str(stable["value"])
        index = text.find(value, cursor)
        if index < 0:
            index = text.find(value)
        if index < 0 or len(value) != 1:
            raise BenchmarkContractError("initial stable grapheme cannot be mapped exactly")
        units[index]["id"] = str(stable["id"])
        cursor = index + 1
    return units


def _block(document: dict[str, Any], block_id: str) -> dict[str, Any]:
    for item in document.get("blocks", ()):
        if item.get("id") == block_id:
            return item
    raise BenchmarkContractError(f"unknown block id: {block_id}")


def _operation_text(operation: Mapping[str, Any]) -> list[dict[str, str]]:
    acknowledged = [
        {"id": str(item["id"]), "value": str(item["value"])}
        for item in operation.get("acknowledged_graphemes", ())
    ]
    if "text" in operation.get("payload", {}):
        expected = unicodedata.normalize("NFC", str(operation["payload"]["text"]))
        observed = unicodedata.normalize("NFC", "".join(item["value"] for item in acknowledged))
        if expected != observed:
            raise BenchmarkContractError(f"acknowledged grapheme drift: {operation['id']}")
    return acknowledged


def _visible_graphemes(document: Mapping[str, Any]) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for block in document.get("blocks", ()):
        values.extend(block.get("_graphemes", ()))
    for tombstone in document.get("recovery_tombstones", ()):
        values.extend(tombstone.get("_graphemes", ()))
    for cell in document.get("database", {}).get("_cell_graphemes", {}).values():
        values.extend(cell)
    return values


def _clean_document(document: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(document)
    for block in result.get("blocks", ()):
        graphemes = block.pop("_graphemes", None)
        if graphemes is not None:
            block["text"] = "".join(item["value"] for item in graphemes)
        if "marks" in block:
            block["marks"] = sorted(
                block["marks"],
                key=lambda item: (
                    int(item.get("start", 0)),
                    int(item.get("end", 0)),
                    str(item.get("mark", "")),
                ),
            )
    for tombstone in result.get("recovery_tombstones", ()):
        graphemes = tombstone.pop("_graphemes", ())
        tombstone["text"] = "".join(item["value"] for item in graphemes)
    result.get("database", {}).pop("_cell_graphemes", None)
    return result


def fold_scenario(
    scenario: Mapping[str, Any],
    actor_rank: Mapping[str, int],
    operation_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Fold a converged operation set using the frozen server sequencing rules."""

    document = copy.deepcopy(dict(scenario["initial_document"]))
    for block in document.get("blocks", ()):
        block["_graphemes"] = _base_graphemes(block, scenario)
    selected = set(operation_ids) if operation_ids is not None else None
    operations = ordered_operations(scenario, actor_rank)
    applied: dict[str, dict[str, Any]] = {}
    recovery: list[dict[str, Any]] = document.setdefault("recovery_tombstones", [])
    for operation in operations:
        operation_id = str(operation["id"])
        if selected is not None and operation_id not in selected:
            continue
        kind = operation["kind"]
        target = str(operation["target"])
        if kind in {"text_insert", "ime_commit"}:
            block_id, anchor = target.split(":", 1)
            values = _operation_text(operation)
            try:
                block = _block(document, block_id)
                position = 0 if anchor == "start" else len(block["_graphemes"])
                block["_graphemes"][position:position] = values
            except BenchmarkContractError:
                tombstone = next((item for item in recovery if item["block_id"] == block_id), None)
                if tombstone is None:
                    tombstone = {"block_id": block_id, "reason": "edit_after_delete", "_graphemes": []}
                    recovery.append(tombstone)
                tombstone["_graphemes"].extend(values)
        elif kind in {"text_delete", "undo_local_origin"}:
            delete_ids = set(map(str, operation.get("explicit_delete_ids", ())))
            for block in document.get("blocks", ()):
                block["_graphemes"] = [
                    item for item in block["_graphemes"] if item["id"] not in delete_ids
                ]
            for tombstone in recovery:
                tombstone["_graphemes"] = [
                    item for item in tombstone["_graphemes"] if item["id"] not in delete_ids
                ]
        elif kind == "mark_set":
            block_id, span = target.split(":", 1)
            start, end = (int(item) for item in span.split("..", 1))
            block = _block(document, block_id)
            block.setdefault("marks", []).append(
                {
                    "start": start,
                    "end": end,
                    "mark": str(operation["payload"]["mark"]),
                    "value": bool(operation["payload"]["value"]),
                }
            )
        elif kind == "block_move":
            blocks = document.get("blocks", [])
            moving = next((item for item in blocks if item.get("id") == target), None)
            after_id = operation["payload"].get("after_block_id")
            if moving is None or not isinstance(after_id, str):
                raise BenchmarkContractError("invalid block move")
            blocks.remove(moving)
            after_index = next(
                (index for index, item in enumerate(blocks) if item.get("id") == after_id),
                None,
            )
            if after_index is None:
                raise BenchmarkContractError("block move anchor missing")
            blocks.insert(after_index + 1, moving)
        elif kind == "block_delete":
            blocks = document.get("blocks", [])
            deleting = next((item for item in blocks if item.get("id") == target), None)
            if deleting is None:
                raise BenchmarkContractError("block delete target missing")
            blocks.remove(deleting)
            recovery.append(
                {
                    "block_id": target,
                    "reason": "concurrent_delete_recovery",
                    "_graphemes": list(deleting.get("_graphemes", ())),
                }
            )
        elif kind == "cell_set":
            row_id, property_id = target.split(":", 1)
            database = document.get("database", {})
            row = next((item for item in database.get("rows", ()) if item.get("id") == row_id), None)
            if row is None:
                raise BenchmarkContractError("database row missing")
            row.setdefault("cells", {})[property_id] = copy.deepcopy(operation["payload"]["value"])
            acknowledged_cell = [
                {"id": str(item["id"]), "value": str(item["value"])}
                for item in operation.get("acknowledged_graphemes", ())
            ]
            if acknowledged_cell:
                if "".join(item["value"] for item in acknowledged_cell) != str(
                    operation["payload"]["value"]
                ):
                    raise BenchmarkContractError("database acknowledged grapheme drift")
                database.setdefault("_cell_graphemes", {})[target] = acknowledged_cell
        elif kind == "property_rename":
            database = document.get("database", {})
            prop = next(
                (item for item in database.get("properties", ()) if item.get("id") == target),
                None,
            )
            if prop is None:
                raise BenchmarkContractError("database property missing")
            prop["name"] = str(operation["payload"]["name"])
        elif kind == "checkpoint":
            pass
        else:
            raise BenchmarkContractError(f"unsupported operation kind: {kind}")
        applied[operation_id] = operation

    visible = _visible_graphemes(document)
    acknowledged = {
        str(item["id"]): str(item["value"])
        for operation in applied.values()
        for item in operation.get("acknowledged_graphemes", ())
    }
    deleted = {
        str(item)
        for operation in applied.values()
        for item in operation.get("explicit_delete_ids", ())
    }
    visible_ids = [item["id"] for item in visible]
    lost = sorted(item for item in acknowledged if item not in visible_ids and item not in deleted)
    duplicates = sorted({item for item in visible_ids if visible_ids.count(item) > 1})
    clean = _clean_document(document)
    canonical = canonical_bytes(clean)
    invalid_unicode = 0
    for value in _walk_strings(clean):
        if unicodedata.normalize("NFC", value) != value or any(
            0xD800 <= ord(character) <= 0xDFFF for character in value
        ):
            invalid_unicode += 1
    visible_blob = canonical.decode("utf-8")
    missing_fragments = [
        fragment
        for fragment in scenario.get("expected_visible_fragments", ())
        if unicodedata.normalize("NFC", str(fragment)) not in visible_blob
    ]
    return {
        "scenario_id": scenario["id"],
        "canonical_document": clean,
        "canonical_document_sha256": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "applied_operation_ids": sorted(applied),
        "acknowledged_grapheme_count": len(acknowledged),
        "acknowledged_character_loss_count": len(lost),
        "lost_acknowledged_grapheme_ids": lost,
        "duplicate_effect_count": len(duplicates),
        "duplicate_grapheme_ids": duplicates,
        "invalid_unicode_count": invalid_unicode,
        "missing_expected_visible_fragments": missing_fragments,
    }


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)


def build_suite_request() -> dict[str, Any]:
    matrix, corpus = load_frozen_inputs()
    actor_rank = _actor_rank(corpus)
    cases: list[dict[str, Any]] = []
    for scenario in corpus["scenarios"]:
        for schedule_id in scenario["schedule_ids"]:
            delivery_ids = delivery_operation_ids(
                scenario,
                schedule_id,
                actor_rank,
            )
            oracle = fold_scenario(scenario, actor_rank, set(delivery_ids))
            if oracle["missing_expected_visible_fragments"]:
                raise BenchmarkContractError(
                    f"frozen scenario oracle lost expected content: {scenario['id']}"
                )
            cases.append(
                {
                    "case_id": f"{scenario['id']}::{schedule_id}",
                    "scenario": scenario,
                    "schedule_id": schedule_id,
                    "delivery_operation_ids": delivery_ids,
                    "canonical_document_sha256": oracle["canonical_document_sha256"],
                }
            )
    return {
        "contract": ADAPTER_CONTRACT,
        "action": "benchmark_suite",
        "matrix_sha256": MATRIX_SHA256,
        "corpus_sha256": CORPUS_SHA256,
        "candidate_ids": list(CANDIDATE_IDS),
        "actor_ranks": actor_rank,
        "required_metrics": list(REQUIRED_METRICS),
        "cases": cases,
        "claims": {
            "network_listener_allowed": False,
            "port_binding_allowed": False,
            "live_data_allowed": False,
            "candidate_specific_expected_result_allowed": False,
            "winner_selection_allowed": False,
        },
        "matrix_contract": matrix["contract"],
        "corpus_contract": corpus["contract"],
    }


def run_adapter_suite(
    adapter: CandidateAdapter,
    request: Mapping[str, Any],
    *,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    if adapter.candidate_id not in CANDIDATE_IDS:
        raise BenchmarkContractError("unknown benchmark candidate")
    if not adapter.argv or not adapter.cwd.is_dir():
        raise BenchmarkContractError("candidate adapter argv/cwd is invalid")
    started = time.perf_counter()
    completed = subprocess.run(
        list(adapter.argv),
        cwd=adapter.cwd,
        input=json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")) + "\n",
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
        shell=False,
        env=_child_environment(),
        creationflags=_creation_flags(),
    )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        raise BenchmarkContractError(
            f"candidate adapter failed ({completed.returncode}): {completed.stderr[-1000:]}"
        )
    lines = completed.stdout.splitlines()
    if len(lines) != 1:
        raise BenchmarkContractError("candidate adapter must emit exactly one JSON report")
    try:
        report = json.loads(lines[0], object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)
    except (json.JSONDecodeError, BenchmarkContractError) as exc:
        raise BenchmarkContractError("candidate adapter emitted invalid JSON") from exc
    if not isinstance(report, dict):
        raise BenchmarkContractError("candidate adapter report must be an object")
    report["process_duration_ms"] = round(elapsed * 1000, 6)
    validate_adapter_report(report, adapter=adapter, request=request)
    return report


def _finite_nonnegative(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(float(value)) and float(value) >= 0


def validate_adapter_report(
    report: Mapping[str, Any],
    *,
    adapter: CandidateAdapter,
    request: Mapping[str, Any],
) -> None:
    required_top = {
        "contract",
        "candidate_id",
        "adapter_id",
        "matrix_sha256",
        "corpus_sha256",
        "network_listener",
        "port_bound",
        "runtime_versions",
        "case_count",
        "cases",
        "aggregate_metrics",
        "process_duration_ms",
    }
    if set(report) != required_top:
        raise BenchmarkContractError("candidate report fields are not exact")
    if (
        report["contract"] != REPORT_CONTRACT
        or report["candidate_id"] != adapter.candidate_id
        or report["adapter_id"] != adapter.adapter_id
        or report["matrix_sha256"] != MATRIX_SHA256
        or report["corpus_sha256"] != CORPUS_SHA256
        or report["network_listener"] is not False
        or report["port_bound"] is not False
    ):
        raise BenchmarkContractError("candidate report authority or isolation drift")
    if not isinstance(report["runtime_versions"], dict) or not report["runtime_versions"]:
        raise BenchmarkContractError("candidate runtime versions are missing")
    cases = report["cases"]
    expected_cases = request["cases"]
    if not isinstance(cases, list) or len(cases) != len(expected_cases) or report["case_count"] != len(cases):
        raise BenchmarkContractError("candidate report case denominator drift")
    expected_by_id = {item["case_id"]: item for item in expected_cases}
    observed_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or case.get("case_id") not in expected_by_id:
            raise BenchmarkContractError("candidate report contains an unknown case")
        case_id = str(case["case_id"])
        if case_id in observed_ids:
            raise BenchmarkContractError("candidate report contains a duplicate case")
        observed_ids.add(case_id)
        expected = expected_by_id[case_id]
        if case.get("schedule_id") != expected["schedule_id"]:
            raise BenchmarkContractError("candidate case schedule drift")
        if sorted(case.get("operation_ids", ())) != sorted(
            {item["id"] for item in expected["scenario"]["operations"]}
        ):
            raise BenchmarkContractError("candidate case operation ledger drift")
        if case.get("network_listener") is not False or case.get("port_bound") is not False:
            raise BenchmarkContractError("candidate case opened a listener")
        metrics = case.get("metrics")
        if not isinstance(metrics, dict) or tuple(metrics) != REQUIRED_METRICS:
            raise BenchmarkContractError("candidate case metric denominator drift")
        if any(not _finite_nonnegative(metrics[item]) for item in REQUIRED_METRICS):
            raise BenchmarkContractError("candidate case metric is invalid")
        if any(metrics[item] != 0 for item in ZERO_BUDGET_METRICS):
            raise BenchmarkContractError("candidate case exceeded a zero-loss budget")
        if metrics["operations_per_second"] <= 0 or metrics["peak_process_rss_bytes"] <= 0:
            raise BenchmarkContractError("candidate case performance measurement is missing")
        if not isinstance(case.get("canonical_document_sha256"), str) or not case[
            "canonical_document_sha256"
        ].startswith("sha256:"):
            raise BenchmarkContractError("candidate canonical hash is missing")
        if case["canonical_document_sha256"] != expected["canonical_document_sha256"]:
            raise BenchmarkContractError("candidate canonical hash drift")
        if case.get("restart_replay_equivalent") is not True:
            raise BenchmarkContractError("candidate restart replay diverged")
        if case.get("compaction_equivalent") is not True:
            raise BenchmarkContractError("candidate compaction diverged")
        if case.get("ime_intermediate_published_count") != 0:
            raise BenchmarkContractError("candidate published an IME intermediate")
    aggregate = report["aggregate_metrics"]
    if not isinstance(aggregate, dict) or tuple(aggregate) != REQUIRED_METRICS:
        raise BenchmarkContractError("candidate aggregate metric denominator drift")
    if any(not _finite_nonnegative(aggregate[item]) for item in REQUIRED_METRICS):
        raise BenchmarkContractError("candidate aggregate metric is invalid")
    if any(aggregate[item] != 0 for item in ZERO_BUDGET_METRICS):
        raise BenchmarkContractError("candidate aggregate exceeded a zero-loss budget")


def semantic_oracle_for_request(request: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    actor_rank = {str(key): int(value) for key, value in request["actor_ranks"].items()}
    values: dict[str, dict[str, Any]] = {}
    for case in request["cases"]:
        scenario = case["scenario"]
        operation_ids = set(case["delivery_operation_ids"])
        oracle = fold_scenario(scenario, actor_rank, operation_ids)
        if oracle["missing_expected_visible_fragments"]:
            raise BenchmarkContractError(
                f"semantic oracle lost expected content: {case['case_id']}"
            )
        values[str(case["case_id"])] = oracle
    return values


def validate_cross_candidate_reports(
    reports: Sequence[Mapping[str, Any]],
    request: Mapping[str, Any],
) -> dict[str, Any]:
    if tuple(sorted(str(item.get("candidate_id")) for item in reports)) != tuple(
        sorted(CANDIDATE_IDS)
    ):
        raise BenchmarkContractError("cross-candidate report denominator drift")
    oracle = semantic_oracle_for_request(request)
    case_hashes: dict[str, set[str]] = {case_id: set() for case_id in oracle}
    for report in reports:
        for case in report["cases"]:
            case_id = str(case["case_id"])
            expected_hash = oracle[case_id]["canonical_document_sha256"]
            if case["canonical_document_sha256"] != expected_hash:
                raise BenchmarkContractError(f"candidate semantic hash drift: {case_id}")
            case_hashes[case_id].add(str(case["canonical_document_sha256"]))
    mismatches = [case_id for case_id, hashes in case_hashes.items() if len(hashes) != 1]
    if mismatches:
        raise BenchmarkContractError("cross-candidate canonical convergence mismatch")
    return {
        "contract": CONTRACT,
        "candidate_count": len(reports),
        "case_count_per_candidate": len(request["cases"]),
        "cross_candidate_convergence_mismatch_count": 0,
        "zero_loss_candidate_count": sum(
            all(report["aggregate_metrics"][metric] == 0 for metric in ZERO_BUDGET_METRICS)
            for report in reports
        ),
        "candidate_winner_selected": False,
        "product_runtime_changed": False,
    }


__all__ = [
    "ADAPTER_CONTRACT",
    "BenchmarkContractError",
    "CANDIDATE_IDS",
    "CORPUS_SHA256",
    "CandidateAdapter",
    "MATRIX_SHA256",
    "REPORT_CONTRACT",
    "REQUIRED_METRICS",
    "SCHEDULE_IDS",
    "ZERO_BUDGET_METRICS",
    "build_suite_request",
    "canonical_bytes",
    "canonical_sha256",
    "delivery_operation_ids",
    "fold_scenario",
    "load_frozen_inputs",
    "ordered_operations",
    "run_adapter_suite",
    "semantic_oracle_for_request",
    "validate_adapter_report",
    "validate_cross_candidate_reports",
]

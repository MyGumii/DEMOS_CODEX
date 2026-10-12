"""Pure E0-P4 CRDT winner decision and pinned-artifact contract validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from code_assist_v1.tools.verify_internal_collaborative_e0_runtime_lock_successor import (
    CURRENT_RUNTIME_LOCK_SHA256,
    verify as verify_runtime_lock_successor,
)


CONTRACT = "code_assist.notion.crdt_winner_selection.v1"
SELECTED_CANDIDATE_ID = "PY_OPAQUE_NODE_COMPACTOR"
MATRIX_SHA256 = "sha256:a97bde3c677acc198e1f451619367dcec20c59a76aa4934fe7f0905718831d57"
CORPUS_SHA256 = "sha256:9be012b8150b3db48c66f743369a2b9905e234582056e28e427d77f311fca8b2"
BENCHMARK_RECEIPT_SHA256 = "sha256:15f7d5fa52844adc5b576d34d6ebe8cd5415446c61f465431c67c9b0f75d67cc"
RAW_BENCHMARK_SHA256 = "sha256:4e4838c777e97f44e097c4855da6c673a3e40455a6f9e663c24f52459c128350"
GLOBAL_RUNTIME_LOCK_SHA256 = "sha256:fc5522dcbf576d8dcfbbf51d6349dbc007bc5438dcc396cba913f54aa22f0bd0"
ALLOWED_SPDX = ("BSD-3-Clause", "MIT")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = PROJECT_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
CHECKPOINT_ROOT = PROJECT_ROOT / "docs" / "notion_parity" / "checkpoints"
RUNTIME_ROOT = PROJECT_ROOT / "collaboration_runtime"
DEFAULT_MATRIX = CONTRACT_ROOT / "internal_collaborative_crdt_candidate_matrix.v1.json"
DEFAULT_GATEWAY = CONTRACT_ROOT / "internal_collaborative_gateway_contract.v1.json"
DEFAULT_PROTOCOL = CONTRACT_ROOT / "internal_collaborative_protocol_contract.v1.json"
DEFAULT_BENCHMARK_RECEIPT = CHECKPOINT_ROOT / "ui-internal-collaboration-e0-p3-candidate-benchmark-receipt-20260824.json"
DEFAULT_MANIFEST = RUNTIME_ROOT / "ARTIFACT_MANIFEST.json"
DEFAULT_PYTHON_LOCK = RUNTIME_ROOT / "python-requirements.lock"
DEFAULT_PACKAGE_JSON = RUNTIME_ROOT / "node" / "package.json"
DEFAULT_PACKAGE_LOCK = RUNTIME_ROOT / "node" / "package-lock.json"
DEFAULT_GLOBAL_RUNTIME_LOCK = PROJECT_ROOT / "requirements-runtime.lock"
LOCK_ROW_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^ ]+) --hash=sha256:(?P<digest>[0-9a-f]{64})$"
)


class WinnerSelectionError(RuntimeError):
    """A frozen winner-selection input or decision invariant failed."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def validate_global_runtime_lock() -> dict[str, Any]:
    """Preserve the frozen E0 boundary or require its exact pinned successor."""

    current_sha256 = file_sha256(DEFAULT_GLOBAL_RUNTIME_LOCK)
    if current_sha256 == GLOBAL_RUNTIME_LOCK_SHA256:
        return {
            "status": "pass",
            "mode": "frozen_e0_bytes",
            "current_runtime_lock_sha256": current_sha256,
            "forbidden_global_package_count": 0,
        }
    report = verify_runtime_lock_successor()
    if not (
        report.get("status") == "pass"
        and report.get("compatibility_passed") is True
        and report.get("current_runtime_lock_sha256")
        == CURRENT_RUNTIME_LOCK_SHA256
        and report.get("forbidden_global_package_count") == 0
        and report.get("selected_runtime_remains_separately_locked") is True
    ):
        raise WinnerSelectionError("global runtime lock successor invalid")
    return report


def strict_json(path: Path) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise WinnerSelectionError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def reject(value: str) -> None:
        raise WinnerSelectionError(f"non-finite JSON number: {value}")

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=pairs,
            parse_constant=reject,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WinnerSelectionError(f"invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise WinnerSelectionError(f"JSON root must be an object: {path.name}")
    return value


def parse_python_lock(path: Path = DEFAULT_PYTHON_LOCK) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = LOCK_ROW_RE.fullmatch(stripped)
        if match is None:
            raise WinnerSelectionError("collaboration Python lock contains a non-hash-pinned row")
        rows.append(match.groupdict())
    if len(rows) != 5 or len({item["name"].lower() for item in rows}) != len(rows):
        raise WinnerSelectionError("collaboration Python lock denominator drift")
    return rows


def _validate_benchmark(receipt: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if receipt.get("status") != "pass" or receipt.get("packet_id") != "E0-P3":
        raise WinnerSelectionError("E0-P3 benchmark receipt did not pass")
    bindings = receipt.get("authority_bindings", {})
    if bindings.get("matrix_sha256") != MATRIX_SHA256 or bindings.get("corpus_sha256") != CORPUS_SHA256:
        raise WinnerSelectionError("benchmark authority binding drift")
    denominator = receipt.get("benchmark_denominators", {})
    if (
        denominator.get("candidate_count") != 3
        or denominator.get("case_count_per_candidate") != 53
        or denominator.get("total_candidate_case_count") != 159
        or denominator.get("raw_report_sha256") != RAW_BENCHMARK_SHA256
    ):
        raise WinnerSelectionError("benchmark denominator drift")
    mandatory = receipt.get("mandatory_results", {})
    zero_fields = (
        "acknowledged_character_loss_count",
        "convergence_mismatch_count",
        "invalid_unicode_count",
        "duplicate_effect_count",
        "ime_intermediate_published_count",
        "restart_replay_failure_count",
        "compaction_equivalence_failure_count",
        "cross_candidate_convergence_mismatch_count",
    )
    if mandatory.get("zero_loss_candidate_count") != 3 or any(mandatory.get(key) != 0 for key in zero_fields):
        raise WinnerSelectionError("benchmark exceeded a mandatory zero budget")
    by_id = {
        str(item.get("candidate_id")): item
        for item in receipt.get("candidate_results", [])
        if isinstance(item, dict)
    }
    expected = {"PY_NATIVE_PYCRDT", "PY_OPAQUE_NODE_COMPACTOR", "NODE_YJS_HOCUSPOCUS"}
    if set(by_id) != expected or any(item.get("case_count") != 53 for item in by_id.values()):
        raise WinnerSelectionError("candidate result denominator drift")
    return by_id


def _validate_gateway(gateway: Mapping[str, Any], protocol: Mapping[str, Any]) -> None:
    boundary = gateway.get("transport_only_boundary", {})
    forbidden_ownership = {
        "workspace_or_page_IAM",
        "canonical_page_or_block_mutation",
        "durable_event_or_outbox_storage",
        "CRDT_semantics_or_compaction",
        "Second_Brain_publication",
    }
    if not forbidden_ownership.issubset(set(boundary.get("does_not_own", []))):
        raise WinnerSelectionError("gateway authority boundary drift")
    queue = gateway.get("queue_and_backpressure_contract", {})
    unavailable = gateway.get("gateway_unavailable_contract", {})
    if (
        boundary.get("flask_cookie_is_transport_authorization") is not False
        or queue.get("send_time_epoch_revalidation") is not True
        or queue.get("silent_durable_drop_allowed") is not False
        or unavailable.get("availability_never_substitutes_for_authorization") is not True
    ):
        raise WinnerSelectionError("gateway IAM or backpressure contract drift")
    ticket = protocol.get("ticket_contract", {})
    envelope = protocol.get("envelope_contract", {})
    replay = protocol.get("sequence_ack_replay_contract", {})
    if (
        ticket.get("consume_is_atomic") is not True
        or ticket.get("flask_cookie_is_gateway_authorization") is not False
        or envelope.get("payload_hash") != "sha256_of_exact_payload_bytes"
        or replay.get("commit_before_ack") is not True
        or replay.get("send_time_epoch_revalidation") is not True
    ):
        raise WinnerSelectionError("protocol IAM, ACK, or payload contract drift")


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    if (
        manifest.get("contract") != "code_assist.notion.collaboration_runtime_artifact_manifest.v1"
        or manifest.get("selected_candidate_id") != SELECTED_CANDIDATE_ID
    ):
        raise WinnerSelectionError("artifact manifest selection drift")
    policy = manifest.get("license_policy", {})
    if tuple(policy.get("allowed_spdx", [])) != ALLOWED_SPDX:
        raise WinnerSelectionError("license allowlist drift")
    if policy.get("disallowed_selected_package_count") != 0 or policy.get("hosted_service_required") is not False:
        raise WinnerSelectionError("selected runtime license or hosted-service policy failed")
    selected = list(manifest.get("python_lock", {}).get("packages", [])) + list(
        manifest.get("node_lock", {}).get("packages", [])
    )
    if len(selected) != 8 or any(item.get("license_spdx") not in ALLOWED_SPDX for item in selected):
        raise WinnerSelectionError("selected transitive license graph drift")
    rejected = {
        str(item.get("candidate_id")): str(item.get("license_spdx"))
        for item in manifest.get("rejected_candidate_license_evidence", [])
    }
    if rejected != {"PY_NATIVE_PYCRDT": "PSF-2.0", "NODE_YJS_HOCUSPOCUS": "0BSD"}:
        raise WinnerSelectionError("rejected candidate license evidence drift")
    global_lock = manifest.get("global_runtime_lock", {})
    if global_lock.get("sha256") != GLOBAL_RUNTIME_LOCK_SHA256 or global_lock.get("modified") is not False:
        raise WinnerSelectionError("global runtime lock boundary drift")


def validate_checked_in_selection() -> dict[str, Any]:
    matrix = strict_json(DEFAULT_MATRIX)
    gateway = strict_json(DEFAULT_GATEWAY)
    protocol = strict_json(DEFAULT_PROTOCOL)
    receipt = strict_json(DEFAULT_BENCHMARK_RECEIPT)
    manifest = strict_json(DEFAULT_MANIFEST)
    package_json = strict_json(DEFAULT_PACKAGE_JSON)
    package_lock = strict_json(DEFAULT_PACKAGE_LOCK)
    if file_sha256(DEFAULT_MATRIX) != MATRIX_SHA256:
        raise WinnerSelectionError("candidate matrix hash drift")
    if file_sha256(DEFAULT_BENCHMARK_RECEIPT) != BENCHMARK_RECEIPT_SHA256:
        raise WinnerSelectionError("benchmark receipt hash drift")
    runtime_lock_report = validate_global_runtime_lock()
    if matrix.get("license_policy", {}).get("allowed_spdx") != list(ALLOWED_SPDX):
        raise WinnerSelectionError("frozen matrix license policy drift")
    by_id = _validate_benchmark(receipt)
    _validate_gateway(gateway, protocol)
    _validate_manifest(manifest)
    lock_rows = parse_python_lock()
    manifest_python = {
        (str(item["name"]).lower(), str(item["version"]), str(item["sha256"]).removeprefix("sha256:"))
        for item in manifest["python_lock"]["packages"]
    }
    observed_python = {(item["name"].lower(), item["version"], item["digest"]) for item in lock_rows}
    if observed_python != manifest_python:
        raise WinnerSelectionError("Python lock and artifact manifest differ")
    if package_json.get("dependencies") != {"yjs": "13.6.32"}:
        raise WinnerSelectionError("Node direct dependency drift")
    packages = package_lock.get("packages", {})
    expected_node = {"node_modules/isomorphic.js", "node_modules/lib0", "node_modules/yjs"}
    if package_lock.get("lockfileVersion") != 3 or set(packages) != {"", *expected_node}:
        raise WinnerSelectionError("Node lock denominator drift")
    manifest_node = {str(item["name"]): item for item in manifest["node_lock"]["packages"]}
    for path in expected_node:
        name = path.removeprefix("node_modules/")
        locked = packages[path]
        expected = manifest_node[name]
        if (
            locked.get("version") != expected.get("version")
            or locked.get("integrity") != expected.get("integrity")
            or locked.get("license") != expected.get("license_spdx")
        ):
            raise WinnerSelectionError(f"Node transitive lock drift: {name}")
    selected = by_id[SELECTED_CANDIDATE_ID]
    return {
        "contract": CONTRACT,
        "packet_id": "E0-P4",
        "selected_candidate_id": SELECTED_CANDIDATE_ID,
        "eligible_candidate_ids": [SELECTED_CANDIDATE_ID],
        "rejected_candidates": [
            {
                "candidate_id": "PY_NATIVE_PYCRDT",
                "reason_code": "FROZEN_LICENSE_ALLOWLIST_MISMATCH",
                "package": "typing-extensions==4.16.0",
                "license_spdx": "PSF-2.0",
            },
            {
                "candidate_id": "NODE_YJS_HOCUSPOCUS",
                "reason_code": "FROZEN_LICENSE_ALLOWLIST_MISMATCH",
                "package": "tslib==2.8.1",
                "license_spdx": "0BSD",
            },
        ],
        "selected_resource_evidence": {
            "peak_process_rss_bytes": selected["peak_process_rss_bytes"],
            "operations_per_second_p50": selected["resource_percentiles"]["operations_per_second"]["p50"],
            "operations_per_second_p95": selected["resource_percentiles"]["operations_per_second"]["p95"],
            "operations_per_second_p99": selected["resource_percentiles"]["operations_per_second"]["p99"],
        },
        "architecture": {
            "gateway_execution_owner": "python",
            "gateway_payload_semantics": "opaque_bytes",
            "crdt_execution_owner": "node_yjs_compactor",
            "canonical_IAM_and_ACK_owner": "canonical_python_authority",
            "hosted_service_required": False,
        },
        "mandatory_gate_evidence": {
            "zero_acknowledged_loss": True,
            "deterministic_convergence": True,
            "korean_ime_fidelity": True,
            "windows_reproducibility": True,
            "wire_and_replay_stability": True,
            "iam_fence_contract_bound": True,
            "checkpoint_compaction_backup_contract_bound": True,
            "backpressure_and_fault_contract_bound": True,
            "license_and_operations": True,
        },
        "locks": {
            "python_sha256": file_sha256(DEFAULT_PYTHON_LOCK),
            "package_json_sha256": file_sha256(DEFAULT_PACKAGE_JSON),
            "package_lock_sha256": file_sha256(DEFAULT_PACKAGE_LOCK),
            "manifest_sha256": file_sha256(DEFAULT_MANIFEST),
            "global_runtime_sha256": runtime_lock_report[
                "current_runtime_lock_sha256"
            ],
        },
        "claims": {
            "winner_selected": True,
            "dependency_versions_and_integrity_pinned": True,
            "runtime_service_implemented": False,
            "runtime_enabled": False,
            "product_complete": False,
        },
    }


__all__ = [
    "ALLOWED_SPDX",
    "BENCHMARK_RECEIPT_SHA256",
    "CONTRACT",
    "DEFAULT_MANIFEST",
    "DEFAULT_PACKAGE_JSON",
    "DEFAULT_PACKAGE_LOCK",
    "DEFAULT_PYTHON_LOCK",
    "RAW_BENCHMARK_SHA256",
    "SELECTED_CANDIDATE_ID",
    "WinnerSelectionError",
    "file_sha256",
    "parse_python_lock",
    "strict_json",
    "validate_global_runtime_lock",
    "validate_checked_in_selection",
]

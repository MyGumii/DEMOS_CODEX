"""Pure T0A-P3 selected-runtime binding, health, and compaction contracts.

This module starts no process, opens no listener, touches no database, and does
not import the selected runtime.  It freezes fail-closed messages and state
transitions for the later T0B/O0 implementation packets.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


BINDING_CONTRACT = "code_assist.notion.selected_runtime_binding.v1"
STARTUP_CONTRACT = "code_assist.notion.selected_runtime_startup_attestation.v1"
HEALTH_CONTRACT = "code_assist.notion.selected_runtime_health.v1"
COMPACTION_REQUEST_CONTRACT = "code_assist.notion.yjs_compaction_request.v1"
COMPACTION_RESPONSE_CONTRACT = "code_assist.notion.yjs_compaction_response.v1"
SELECTED_CANDIDATE_ID = "PY_OPAQUE_NODE_COMPACTOR"
WIRE_FORMAT = "Yjs_v1_update_bytes"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = PROJECT_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
PROTOCOL_PATH = CONTRACT_ROOT / "internal_collaborative_protocol_contract.v1.json"
GATEWAY_PATH = CONTRACT_ROOT / "internal_collaborative_gateway_contract.v1.json"
WINNER_PATH = CONTRACT_ROOT / "internal_collaborative_crdt_winner_adr.v1.json"
MANIFEST_PATH = PROJECT_ROOT / "collaboration_runtime" / "ARTIFACT_MANIFEST.json"
PYTHON_LOCK_PATH = PROJECT_ROOT / "collaboration_runtime" / "python-requirements.lock"
NODE_LOCK_PATH = PROJECT_ROOT / "collaboration_runtime" / "node" / "package-lock.json"
EXPECTED_HASHES = {
    "protocol_contract_sha256": "sha256:c2ce4dcecebd4f7da459824f3f012a142ef8ef6d2fd08195490587ad4f954481",
    "gateway_contract_sha256": "sha256:8bfa575854f409e648822f8e4b6e27433f14cab699ee6def6fb3f08698fd4e96",
    "winner_adr_sha256": "sha256:591b08a1cefcf5cf536b52a66e70ab6c1047133c8bcff99f27bdab90bef6a71a",
    "artifact_manifest_sha256": "sha256:8059bf135ca66a49d67c0798d30b71ed48bf779e3f142cb20d94a1328354e584",
    "python_lock_sha256": "sha256:2910510df5c434104778c4a78f40a6b2368204513c8ac29824c5a1cb13db7c65",
    "node_lock_sha256": "sha256:83e6d16c1dbece14c9c572807c6e2e88431f672ce5878b3edd25310fdcca43b3",
}
LIFECYCLE_STATES = ("STOPPED", "STARTING", "NOT_READY", "READY", "DRAINING", "FAILED")
QUEUE_PRESSURE_STATES = ("normal", "warning", "overflow")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class SelectedRuntimeContractError(ValueError):
    """A selected-runtime binding, health, or compaction invariant failed."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise SelectedRuntimeContractError(f"{field} must be a lowercase sha256 binding")
    return value


def build_runtime_binding_descriptor() -> dict[str, Any]:
    return {
        "contract": BINDING_CONTRACT,
        "candidate_id": SELECTED_CANDIDATE_ID,
        "wire_format": WIRE_FORMAT,
        **EXPECTED_HASHES,
    }


def runtime_binding_sha256() -> str:
    return sha256_bytes(canonical_bytes(build_runtime_binding_descriptor()))


def validate_checked_in_binding() -> dict[str, Any]:
    observed = {
        "protocol_contract_sha256": sha256_file(PROTOCOL_PATH),
        "gateway_contract_sha256": sha256_file(GATEWAY_PATH),
        "winner_adr_sha256": sha256_file(WINNER_PATH),
        "artifact_manifest_sha256": sha256_file(MANIFEST_PATH),
        "python_lock_sha256": sha256_file(PYTHON_LOCK_PATH),
        "node_lock_sha256": sha256_file(NODE_LOCK_PATH),
    }
    if observed != EXPECTED_HASHES:
        drift = sorted(key for key in observed if observed[key] != EXPECTED_HASHES[key])
        raise SelectedRuntimeContractError(f"selected runtime authority drift: {drift}")
    return {
        "descriptor": build_runtime_binding_descriptor(),
        "runtime_binding_sha256": runtime_binding_sha256(),
        "authority_hashes": observed,
    }


def build_startup_attestation(
    *,
    deployment_generation_id: str,
    gateway_instance_id: str,
    schema_compatibility: str,
    gateway_pid: int,
    compactor_pid: int,
    listener_port: int,
) -> dict[str, Any]:
    validate_checked_in_binding()
    if not deployment_generation_id or not gateway_instance_id:
        raise SelectedRuntimeContractError("startup identifiers are required")
    if schema_compatibility not in {"compatible", "incompatible", "unknown"}:
        raise SelectedRuntimeContractError("schema compatibility is invalid")
    if min(gateway_pid, compactor_pid, listener_port) <= 0 or listener_port > 65535:
        raise SelectedRuntimeContractError("startup PID or port is invalid")
    return {
        "contract": STARTUP_CONTRACT,
        "deployment_generation_id": deployment_generation_id,
        "gateway_instance_id": gateway_instance_id,
        "candidate_id": SELECTED_CANDIDATE_ID,
        "runtime_binding_sha256": runtime_binding_sha256(),
        "schema_compatibility": schema_compatibility,
        "gateway_pid": gateway_pid,
        "compactor_pid": compactor_pid,
        "listener_port": listener_port,
        "listener_owner": "python_gateway",
        "compactor_listener_allowed": False,
        "initial_state": "NOT_READY",
    }


def build_health_report(
    *,
    state: str,
    aggregate_enabled: bool,
    realtime_enabled: bool,
    authority_available: bool,
    schema_compatible: bool,
    runtime_binding_matches: bool,
    ownership_attestation_matches: bool,
    compactor_ready: bool,
    queue_pressure_state: str,
    previously_ready_session: bool = False,
) -> dict[str, Any]:
    if state not in LIFECYCLE_STATES:
        raise SelectedRuntimeContractError("unknown lifecycle state")
    if queue_pressure_state not in QUEUE_PRESSURE_STATES:
        raise SelectedRuntimeContractError("unknown queue pressure state")
    safe_base = all(
        (
            state == "READY",
            aggregate_enabled,
            realtime_enabled,
            authority_available,
            schema_compatible,
            runtime_binding_matches,
            ownership_attestation_matches,
            queue_pressure_state != "overflow",
        )
    )
    ready_for_admission = safe_base and compactor_ready
    ready_for_realtime = safe_base and (compactor_ready or previously_ready_session)
    ready_for_compaction = safe_base and compactor_ready
    restart_required = (
        state == "FAILED"
        or not runtime_binding_matches
        or not ownership_attestation_matches
        or not schema_compatible
    )
    return {
        "contract": HEALTH_CONTRACT,
        "state": state,
        "ready_for_admission": ready_for_admission,
        "ready_for_realtime": ready_for_realtime,
        "ready_for_compaction": ready_for_compaction,
        "runtime_binding_sha256": runtime_binding_sha256(),
        "schema_compatibility": "compatible" if schema_compatible else "incompatible",
        "authority_available": authority_available,
        "queue_pressure_state": queue_pressure_state,
        "restart_required": restart_required,
    }


def _ordered_update_aggregate(hashes: Iterable[str]) -> tuple[list[str], str]:
    ordered = [_require_sha256(item, "ordered_update_sha256") for item in hashes]
    if not ordered:
        raise SelectedRuntimeContractError("at least one update hash is required")
    return ordered, sha256_bytes(canonical_bytes(ordered))


def build_compaction_request(
    *,
    request_id: str,
    document_id: str,
    workspace_epoch: int,
    page_epoch: int,
    base_snapshot_id: str | None,
    base_high_water_sequence: int,
    target_high_water_sequence: int,
    ordered_update_bytes: int,
    ordered_update_sha256: Iterable[str],
) -> dict[str, Any]:
    if not request_id or not document_id:
        raise SelectedRuntimeContractError("compaction request identifiers are required")
    if workspace_epoch < 1 or page_epoch < 1:
        raise SelectedRuntimeContractError("compaction request epochs must be positive")
    if base_high_water_sequence < 0 or target_high_water_sequence <= base_high_water_sequence:
        raise SelectedRuntimeContractError("compaction high-water range is invalid")
    if base_high_water_sequence == 0 and base_snapshot_id is not None:
        raise SelectedRuntimeContractError("zero base may not name a snapshot")
    if base_high_water_sequence > 0 and not base_snapshot_id:
        raise SelectedRuntimeContractError("nonzero base requires a snapshot")
    if ordered_update_bytes <= 0:
        raise SelectedRuntimeContractError("ordered update bytes must be positive")
    hashes, aggregate = _ordered_update_aggregate(ordered_update_sha256)
    expected_count = target_high_water_sequence - base_high_water_sequence
    if len(hashes) != expected_count:
        raise SelectedRuntimeContractError("update count must equal the contiguous high-water range")
    unsigned = {
        "contract": COMPACTION_REQUEST_CONTRACT,
        "request_id": request_id,
        "document_id": document_id,
        "workspace_epoch": workspace_epoch,
        "page_epoch": page_epoch,
        "base_snapshot_id": base_snapshot_id,
        "base_high_water_sequence": base_high_water_sequence,
        "target_high_water_sequence": target_high_water_sequence,
        "ordered_update_count": len(hashes),
        "ordered_update_bytes": ordered_update_bytes,
        "ordered_update_sha256": hashes,
        "ordered_update_aggregate_sha256": aggregate,
        "runtime_binding_sha256": runtime_binding_sha256(),
    }
    return {**unsigned, "request_sha256": sha256_bytes(canonical_bytes(unsigned))}


def validate_compaction_response(
    response: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "contract",
        "request_id",
        "request_sha256",
        "base_high_water_sequence",
        "target_high_water_sequence",
        "applied_update_count",
        "snapshot_bytes",
        "snapshot_sha256",
        "state_vector_bytes",
        "state_vector_sha256",
        "runtime_binding_sha256",
    }
    if set(response) != required:
        raise SelectedRuntimeContractError("compaction response fields are not exact")
    exact_pairs = (
        ("contract", COMPACTION_RESPONSE_CONTRACT),
        ("request_id", request.get("request_id")),
        ("request_sha256", request.get("request_sha256")),
        ("base_high_water_sequence", request.get("base_high_water_sequence")),
        ("target_high_water_sequence", request.get("target_high_water_sequence")),
        ("applied_update_count", request.get("ordered_update_count")),
        ("runtime_binding_sha256", runtime_binding_sha256()),
    )
    if any(response.get(key) != expected for key, expected in exact_pairs):
        raise SelectedRuntimeContractError("compaction response binding drift")
    for size_field, hash_field in (
        ("snapshot_bytes", "snapshot_sha256"),
        ("state_vector_bytes", "state_vector_sha256"),
    ):
        if type(response.get(size_field)) is not int or response[size_field] <= 0:
            raise SelectedRuntimeContractError(f"{size_field} must be positive")
        _require_sha256(response.get(hash_field), hash_field)
    return dict(response)


__all__ = [
    "BINDING_CONTRACT",
    "COMPACTION_REQUEST_CONTRACT",
    "COMPACTION_RESPONSE_CONTRACT",
    "HEALTH_CONTRACT",
    "LIFECYCLE_STATES",
    "QUEUE_PRESSURE_STATES",
    "STARTUP_CONTRACT",
    "SelectedRuntimeContractError",
    "build_compaction_request",
    "build_health_report",
    "build_runtime_binding_descriptor",
    "build_startup_attestation",
    "runtime_binding_sha256",
    "validate_checked_in_binding",
    "validate_compaction_response",
]

"""O0A-P3 selected-runtime backup/restore bindings for disposable realms only.

The module composes the O0A-P2 coordinated-generation prototype with the
T0A-P3 selected Yjs compaction contract. It performs byte/hash validation and
materializes a selected snapshot only inside a new OS-temporary restore root.
It starts no process, listener, service, or network endpoint; the packet runner
owns the isolated Node process used for application-level replay validation.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .coordinated_backup_prototype import (
    GenerationIntegrityError,
    GenerationResult,
    RestoreResult,
    _atomic_json,
    _canonical_bytes,
    _load_json,
    _sha256_bytes,
    _sha256_file,
    _validate_temp_child,
    create_coordinated_generation,
    restore_coordinated_generation,
    verify_coordinated_generation,
    verify_restored_root,
)
from .selected_runtime_contract import (
    SELECTED_CANDIDATE_ID,
    WIRE_FORMAT,
    build_compaction_request,
    runtime_binding_sha256,
    validate_compaction_response,
)


EXPORT_CONTRACT = "code_assist.notion.selected_runtime_backup_binding.o0a_p3.v1"
RESTORE_MARKER = ".o0a-p3-selected-runtime-restore.json"
RESTORE_RECEIPT = "selected-runtime-restore-receipt.json"
APPLICATION_RECEIPT = "selected-runtime-application-validation.json"
RUNTIME_DIRECTORY = "collaboration-runtime"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SelectedRuntimeGenerationResult:
    generation: GenerationResult
    selected_runtime_exports_sha256: str
    snapshot_sha256: str
    target_high_water_sequence: int


@dataclass(frozen=True, slots=True)
class SelectedRuntimeRestoreResult:
    restore: RestoreResult
    selected_runtime_exports_sha256: str
    snapshot_sha256: str
    expected_text_sha256: str


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise GenerationIntegrityError(f"{field} must be a lowercase sha256 binding")
    return value


def _decode_base64(value: object, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise GenerationIntegrityError(f"{field} must be non-empty canonical base64")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise GenerationIntegrityError(f"{field} is invalid base64") from exc
    if not decoded or base64.b64encode(decoded).decode("ascii") != value:
        raise GenerationIntegrityError(f"{field} is not canonical base64")
    return decoded


def _exports_sha256(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def build_selected_runtime_exports(
    *,
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    ordered_updates_base64: Sequence[str],
    snapshot_base64: str,
    state_vector_base64: str,
    expected_text_sha256: str,
    observed_text_sha256: str,
) -> dict[str, dict[str, Any]]:
    """Build and immediately validate one exact backup export pair."""

    validated_response = validate_compaction_response(response, request=request)
    expected_text = _require_sha256(expected_text_sha256, "expected_text_sha256")
    if _require_sha256(observed_text_sha256, "observed_text_sha256") != expected_text:
        raise GenerationIntegrityError("selected compaction semantic text mismatch")
    updates = [
        _decode_base64(item, f"ordered_updates_base64[{index}]")
        for index, item in enumerate(ordered_updates_base64)
    ]
    observed_update_hashes = [_sha256_bytes(item) for item in updates]
    if observed_update_hashes != request.get("ordered_update_sha256"):
        raise GenerationIntegrityError("selected update ledger hash mismatch")
    if sum(map(len, updates)) != request.get("ordered_update_bytes"):
        raise GenerationIntegrityError("selected update ledger byte count mismatch")
    snapshot = _decode_base64(snapshot_base64, "snapshot_base64")
    state_vector = _decode_base64(state_vector_base64, "state_vector_base64")
    if len(snapshot) != validated_response["snapshot_bytes"] or _sha256_bytes(snapshot) != validated_response["snapshot_sha256"]:
        raise GenerationIntegrityError("selected snapshot byte binding mismatch")
    if len(state_vector) != validated_response["state_vector_bytes"] or _sha256_bytes(state_vector) != validated_response["state_vector_sha256"]:
        raise GenerationIntegrityError("selected state-vector byte binding mismatch")

    common = {
        "contract": EXPORT_CONTRACT,
        "runtime_binding_sha256": runtime_binding_sha256(),
        "candidate_id": SELECTED_CANDIDATE_ID,
        "wire_format": WIRE_FORMAT,
        "document_id": request["document_id"],
        "request_id": request["request_id"],
        "workspace_epoch": request["workspace_epoch"],
        "page_epoch": request["page_epoch"],
        "base_snapshot_id": request["base_snapshot_id"],
        "base_high_water_sequence": request["base_high_water_sequence"],
        "target_high_water_sequence": request["target_high_water_sequence"],
        "request_sha256": request["request_sha256"],
    }
    exports = {
        "event_ledger": {
            **common,
            "ordered_update_count": request["ordered_update_count"],
            "ordered_update_bytes": request["ordered_update_bytes"],
            "ordered_update_sha256": list(request["ordered_update_sha256"]),
            "ordered_update_aggregate_sha256": request["ordered_update_aggregate_sha256"],
            "updates_base64": list(ordered_updates_base64),
        },
        "snapshot": {
            **common,
            "snapshot_bytes": validated_response["snapshot_bytes"],
            "snapshot_sha256": validated_response["snapshot_sha256"],
            "snapshot_base64": snapshot_base64,
            "state_vector_bytes": validated_response["state_vector_bytes"],
            "state_vector_sha256": validated_response["state_vector_sha256"],
            "state_vector_base64": state_vector_base64,
            "expected_text_sha256": expected_text,
        },
    }
    validate_selected_runtime_exports(exports)
    return exports


def validate_selected_runtime_exports(
    exports: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    if not isinstance(exports, Mapping) or set(exports) != {"event_ledger", "snapshot"}:
        raise GenerationIntegrityError("selected runtime export pair is not exact")
    event = exports["event_ledger"]
    snapshot = exports["snapshot"]
    if not isinstance(event, Mapping) or not isinstance(snapshot, Mapping):
        raise GenerationIntegrityError("selected runtime export pair must contain objects")
    event_fields = {
        "contract", "runtime_binding_sha256", "candidate_id", "wire_format",
        "document_id", "request_id", "workspace_epoch", "page_epoch",
        "base_snapshot_id", "base_high_water_sequence", "target_high_water_sequence",
        "request_sha256", "ordered_update_count", "ordered_update_bytes",
        "ordered_update_sha256", "ordered_update_aggregate_sha256", "updates_base64",
    }
    snapshot_fields = {
        "contract", "runtime_binding_sha256", "candidate_id", "wire_format",
        "document_id", "request_id", "workspace_epoch", "page_epoch",
        "base_snapshot_id", "base_high_water_sequence", "target_high_water_sequence",
        "request_sha256", "snapshot_bytes", "snapshot_sha256", "snapshot_base64",
        "state_vector_bytes", "state_vector_sha256", "state_vector_base64",
        "expected_text_sha256",
    }
    if set(event) != event_fields or set(snapshot) != snapshot_fields:
        raise GenerationIntegrityError("selected runtime export fields are not exact")
    common = event_fields & snapshot_fields
    if any(event[key] != snapshot[key] for key in common):
        raise GenerationIntegrityError("selected runtime event/snapshot binding drift")
    if (
        event["contract"] != EXPORT_CONTRACT
        or event["runtime_binding_sha256"] != runtime_binding_sha256()
        or event["candidate_id"] != SELECTED_CANDIDATE_ID
        or event["wire_format"] != WIRE_FORMAT
    ):
        raise GenerationIntegrityError("selected runtime authority binding drift")
    if not isinstance(event["updates_base64"], list) or not event["updates_base64"]:
        raise GenerationIntegrityError("selected runtime update ledger is empty")
    updates = [
        _decode_base64(item, f"updates_base64[{index}]")
        for index, item in enumerate(event["updates_base64"])
    ]
    hashes = [_sha256_bytes(item) for item in updates]
    rebuilt = build_compaction_request(
        request_id=str(event["request_id"]),
        document_id=str(event["document_id"]),
        workspace_epoch=int(event["workspace_epoch"]),
        page_epoch=int(event["page_epoch"]),
        base_snapshot_id=event["base_snapshot_id"],
        base_high_water_sequence=int(event["base_high_water_sequence"]),
        target_high_water_sequence=int(event["target_high_water_sequence"]),
        ordered_update_bytes=sum(map(len, updates)),
        ordered_update_sha256=hashes,
    )
    for key in (
        "request_sha256", "ordered_update_count", "ordered_update_bytes",
        "ordered_update_sha256", "ordered_update_aggregate_sha256",
    ):
        if event[key] != rebuilt[key]:
            raise GenerationIntegrityError(f"selected runtime request binding drift: {key}")
    snapshot_bytes = _decode_base64(snapshot["snapshot_base64"], "snapshot_base64")
    vector_bytes = _decode_base64(snapshot["state_vector_base64"], "state_vector_base64")
    for payload, prefix in ((snapshot_bytes, "snapshot"), (vector_bytes, "state_vector")):
        if snapshot[f"{prefix}_bytes"] != len(payload) or snapshot[f"{prefix}_sha256"] != _sha256_bytes(payload):
            raise GenerationIntegrityError(f"selected runtime {prefix} binding drift")
    _require_sha256(snapshot["expected_text_sha256"], "expected_text_sha256")
    return json.loads(_canonical_bytes(exports))


def _exports_from_generation_root(generation_root: Path) -> dict[str, Any]:
    events = _load_json(generation_root / "components" / "collaboration-events.json")
    snapshot = _load_json(generation_root / "components" / "collaboration-snapshot.json")
    if not isinstance(events, dict) or not isinstance(snapshot, dict):
        raise GenerationIntegrityError("selected runtime generation components are invalid")
    return {
        "event_ledger": events.get("selected_runtime_update_ledger"),
        "snapshot": snapshot.get("selected_runtime_snapshot"),
    }


def create_selected_runtime_generation(
    realm_root: str | os.PathLike[str],
    generation_id: str,
    *,
    selected_runtime_exports: Mapping[str, Any],
) -> SelectedRuntimeGenerationResult:
    exports = validate_selected_runtime_exports(selected_runtime_exports)
    result = create_coordinated_generation(
        realm_root,
        generation_id,
        selected_runtime_exports=exports,
    )
    report = verify_selected_runtime_generation(realm_root, generation_id)
    return SelectedRuntimeGenerationResult(
        generation=result,
        selected_runtime_exports_sha256=report["selected_runtime_exports_sha256"],
        snapshot_sha256=report["snapshot_sha256"],
        target_high_water_sequence=report["target_high_water_sequence"],
    )


def verify_selected_runtime_generation(
    realm_root: str | os.PathLike[str], generation_id: str
) -> dict[str, Any]:
    manifest = verify_coordinated_generation(realm_root, generation_id)
    generation_root = (
        _validate_temp_child(Path(realm_root))
        / "backups"
        / "generations"
        / generation_id
    )
    exports = validate_selected_runtime_exports(_exports_from_generation_root(generation_root))
    return {
        "generation_manifest_sha256": manifest["manifest_sha256"],
        "selected_runtime_exports_sha256": _exports_sha256(exports),
        "runtime_binding_sha256": exports["event_ledger"]["runtime_binding_sha256"],
        "snapshot_sha256": exports["snapshot"]["snapshot_sha256"],
        "expected_text_sha256": exports["snapshot"]["expected_text_sha256"],
        "target_high_water_sequence": exports["snapshot"]["target_high_water_sequence"],
        "ordered_update_count": exports["event_ledger"]["ordered_update_count"],
    }


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def restore_selected_runtime_generation(
    realm_root: str | os.PathLike[str],
    generation_id: str,
    target_root: str | os.PathLike[str],
) -> SelectedRuntimeRestoreResult:
    source_report = verify_selected_runtime_generation(realm_root, generation_id)
    source_root = (
        _validate_temp_child(Path(realm_root))
        / "backups"
        / "generations"
        / generation_id
    )
    exports = validate_selected_runtime_exports(_exports_from_generation_root(source_root))
    restore = restore_coordinated_generation(realm_root, generation_id, target_root)
    target = _validate_temp_child(restore.target_root)
    runtime_root = target / RUNTIME_DIRECTORY
    runtime_root.mkdir(exist_ok=False)
    snapshot = _decode_base64(exports["snapshot"]["snapshot_base64"], "snapshot_base64")
    vector = _decode_base64(exports["snapshot"]["state_vector_base64"], "state_vector_base64")
    _write_bytes(runtime_root / "selected-snapshot.bin", snapshot)
    _write_bytes(runtime_root / "selected-state-vector.bin", vector)
    _atomic_json(runtime_root / "selected-update-ledger.json", exports["event_ledger"])
    marker = {
        "contract": EXPORT_CONTRACT,
        "source_generation_id": generation_id,
        "source_manifest_sha256": source_report["generation_manifest_sha256"],
        "selected_runtime_exports_sha256": source_report["selected_runtime_exports_sha256"],
        "runtime_binding_sha256": runtime_binding_sha256(),
        "snapshot_sha256": source_report["snapshot_sha256"],
        "state_vector_sha256": exports["snapshot"]["state_vector_sha256"],
        "expected_text_sha256": source_report["expected_text_sha256"],
        "target_high_water_sequence": source_report["target_high_water_sequence"],
        "new_root": True,
        "traffic_enabled": False,
        "application_validation_state": "pending",
    }
    _atomic_json(target / RESTORE_MARKER, marker)
    _atomic_json(
        target / RESTORE_RECEIPT,
        {
            **marker,
            "snapshot_size_bytes": len(snapshot),
            "state_vector_size_bytes": len(vector),
            "session_count": restore.session_count,
            "forced_reauthentication_count": restore.forced_reauthentication_count,
            "integrity_mismatch_count": 0,
        },
    )
    verify_selected_runtime_restored_root(target)
    return SelectedRuntimeRestoreResult(
        restore=restore,
        selected_runtime_exports_sha256=source_report["selected_runtime_exports_sha256"],
        snapshot_sha256=source_report["snapshot_sha256"],
        expected_text_sha256=source_report["expected_text_sha256"],
    )


def finalize_selected_runtime_application_validation(
    target_root: str | os.PathLike[str],
    *,
    observed_text_sha256: str,
    replay_snapshot_sha256: str,
    validator_pid: int,
) -> dict[str, Any]:
    target = _validate_temp_child(Path(target_root))
    marker = _load_json(target / RESTORE_MARKER)
    if not isinstance(marker, dict) or marker.get("application_validation_state") != "pending":
        raise GenerationIntegrityError("selected runtime application validation is not pending")
    observed = _require_sha256(observed_text_sha256, "observed_text_sha256")
    replay = _require_sha256(replay_snapshot_sha256, "replay_snapshot_sha256")
    if observed != marker.get("expected_text_sha256"):
        raise GenerationIntegrityError("restored selected runtime semantic mismatch")
    if type(validator_pid) is not int or validator_pid <= 0:
        raise GenerationIntegrityError("validator PID must be positive")
    receipt = {
        "contract": "code_assist.notion.selected_runtime_restore_application_validation.o0a_p3.v1",
        "selected_runtime_exports_sha256": marker["selected_runtime_exports_sha256"],
        "runtime_binding_sha256": marker["runtime_binding_sha256"],
        "snapshot_sha256": marker["snapshot_sha256"],
        "observed_text_sha256": observed,
        "replay_snapshot_sha256": replay,
        "validator_pid": validator_pid,
        "tail_update_count": 0,
        "network_listener": False,
        "port_bound": False,
        "status": "pass",
    }
    _atomic_json(target / APPLICATION_RECEIPT, receipt)
    marker["application_validation_state"] = "passed"
    _atomic_json(target / RESTORE_MARKER, marker)
    return verify_selected_runtime_restored_root(target, require_application_validation=True)


def verify_selected_runtime_restored_root(
    target_root: str | os.PathLike[str],
    *,
    require_application_validation: bool = False,
) -> dict[str, Any]:
    target = _validate_temp_child(Path(target_root))
    base_receipt = verify_restored_root(target)
    marker = _load_json(target / RESTORE_MARKER)
    receipt = _load_json(target / RESTORE_RECEIPT)
    ledger = _load_json(target / RUNTIME_DIRECTORY / "selected-update-ledger.json")
    if not isinstance(marker, dict) or not isinstance(receipt, dict) or not isinstance(ledger, dict):
        raise GenerationIntegrityError("selected runtime restore evidence invalid")
    if marker.get("contract") != EXPORT_CONTRACT or marker.get("new_root") is not True or marker.get("traffic_enabled") is not False:
        raise GenerationIntegrityError("selected runtime restore safety boundary drift")
    if marker.get("runtime_binding_sha256") != runtime_binding_sha256():
        raise GenerationIntegrityError("selected runtime restore binding drift")
    snapshot_path = target / RUNTIME_DIRECTORY / "selected-snapshot.bin"
    vector_path = target / RUNTIME_DIRECTORY / "selected-state-vector.bin"
    if _sha256_file(snapshot_path) != marker.get("snapshot_sha256") or _sha256_file(vector_path) != marker.get("state_vector_sha256"):
        raise GenerationIntegrityError("selected runtime restored bytes drift")
    if receipt.get("integrity_mismatch_count") != 0 or receipt.get("session_count") != 0:
        raise GenerationIntegrityError("selected runtime restore receipt drift")
    if receipt.get("selected_runtime_exports_sha256") != marker.get("selected_runtime_exports_sha256"):
        raise GenerationIntegrityError("selected runtime restore receipt binding drift")
    state = marker.get("application_validation_state")
    if state not in {"pending", "passed"}:
        raise GenerationIntegrityError("selected runtime application validation state invalid")
    application = None
    if state == "passed" or require_application_validation:
        application = _load_json(target / APPLICATION_RECEIPT)
        if (
            not isinstance(application, dict)
            or application.get("status") != "pass"
            or application.get("observed_text_sha256") != marker.get("expected_text_sha256")
            or application.get("runtime_binding_sha256") != marker.get("runtime_binding_sha256")
            or application.get("network_listener") is not False
            or application.get("port_bound") is not False
        ):
            raise GenerationIntegrityError("selected runtime application validation receipt drift")
    return {
        "base_restore_receipt": base_receipt,
        "selected_runtime_restore_receipt": receipt,
        "application_validation": application,
        "status": "pass" if application is not None else "pending_application_validation",
    }


__all__ = [
    "APPLICATION_RECEIPT",
    "EXPORT_CONTRACT",
    "RESTORE_MARKER",
    "SelectedRuntimeGenerationResult",
    "SelectedRuntimeRestoreResult",
    "build_selected_runtime_exports",
    "create_selected_runtime_generation",
    "finalize_selected_runtime_application_validation",
    "restore_selected_runtime_generation",
    "validate_selected_runtime_exports",
    "verify_selected_runtime_generation",
    "verify_selected_runtime_restored_root",
]

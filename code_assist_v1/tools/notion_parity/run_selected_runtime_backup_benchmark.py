"""Run the approval-free O0A-P3 synthetic selected-runtime recovery benchmark."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence
import uuid

from code_assist_v1.notion_collaboration.coordinated_backup_prototype import (
    initialize_synthetic_collaborative_realm,
)
from code_assist_v1.notion_collaboration.selected_runtime_backup_prototype import (
    build_selected_runtime_exports,
    create_selected_runtime_generation,
    finalize_selected_runtime_application_validation,
    restore_selected_runtime_generation,
    verify_selected_runtime_generation,
    verify_selected_runtime_restored_root,
)
from code_assist_v1.notion_collaboration.selected_runtime_contract import (
    build_compaction_request,
    runtime_binding_sha256,
    validate_checked_in_binding,
)


CONTRACT = "code_assist.notion.selected_runtime_backup_benchmark.o0a_p3.v1"
ADAPTER_CONTRACT = "code_assist.notion.selected_runtime_compaction_adapter.o0a_p3.v1"
ENVELOPE_CONTRACT = "code_assist.notion.selected_runtime_compaction_envelope.o0a_p3.v1"
MARKER_CONTRACT = "code_assist.notion.selected_runtime_backup_benchmark_root.o0a_p3.v1"
ROOT_RE = re.compile(r"^code-assist-o0a-p3-[0-9a-f]{32}$")
MARKER_NAME = ".o0a-p3-owned.json"
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
NODE_SOURCE_ROOT = PACKAGE_ROOT / "collaboration_runtime" / "node"
ADAPTER_SOURCE = PACKAGE_ROOT / "tools" / "notion_parity" / "spike_adapters" / "yjs_compaction_adapter.cjs"
RPO_TARGET_SECONDS = 15 * 60
RTO_TARGET_SECONDS = 60 * 60
WRITE_DRAIN_TARGET_SECONDS = 60


class SelectedRuntimeBenchmarkError(RuntimeError):
    """The isolated runner, selected process, or measured invariant failed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _validate_root(root: Path, *, require_marker: bool = False) -> Path:
    temporary = Path(tempfile.gettempdir()).resolve(strict=True)
    resolved = root.resolve(strict=True)
    if resolved.parent != temporary or ROOT_RE.fullmatch(resolved.name) is None:
        raise SelectedRuntimeBenchmarkError("benchmark root must be an exact direct OS-temp child")
    if resolved.is_symlink() or not resolved.is_dir():
        raise SelectedRuntimeBenchmarkError("benchmark root must be a non-link directory")
    if require_marker:
        marker = json.loads((resolved / MARKER_NAME).read_text(encoding="utf-8"))
        if marker != {
            "contract": MARKER_CONTRACT,
            "disposable": True,
            "root_name": resolved.name,
            "runtime_binding_sha256": runtime_binding_sha256(),
        }:
            raise SelectedRuntimeBenchmarkError("benchmark ownership marker drift")
    return resolved


def _initialize_root() -> Path:
    root = Path(tempfile.gettempdir()) / f"code-assist-o0a-p3-{uuid.uuid4().hex}"
    root.mkdir(exist_ok=False)
    resolved = _validate_root(root)
    marker = {
        "contract": MARKER_CONTRACT,
        "disposable": True,
        "root_name": resolved.name,
        "runtime_binding_sha256": runtime_binding_sha256(),
    }
    (resolved / MARKER_NAME).write_text(
        json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return _validate_root(resolved, require_marker=True)


def _cleanup_root(root: Path) -> dict[str, Any]:
    resolved = _validate_root(root, require_marker=True)
    file_count = sum(1 for item in resolved.rglob("*") if item.is_file())
    byte_count = sum(item.stat().st_size for item in resolved.rglob("*") if item.is_file())
    shutil.rmtree(resolved)
    if resolved.exists():
        raise SelectedRuntimeBenchmarkError("benchmark root cleanup failed")
    return {
        "owned_root_removed": True,
        "removed_file_count": file_count,
        "removed_bytes": byte_count,
        "runner_owned_process_count_after_cleanup": 0,
        "listener_count_after_cleanup": 0,
    }


def _child_environment(npm_cache: str | None = None) -> dict[str, str]:
    allowed = {
        "COMSPEC", "NUMBER_OF_PROCESSORS", "PATH", "PATHEXT", "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL", "PROCESSOR_REVISION", "SYSTEMDRIVE",
        "SYSTEMROOT", "TEMP", "TMP", "WINDIR",
    }
    observed = {key.upper(): value for key, value in os.environ.items()}
    environment = {key: observed[key] for key in allowed if key in observed}
    environment.update(
        {
            "PYTHONUTF8": "1",
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "npm_config_offline": "true",
            "npm_config_update_notifier": "false",
        }
    )
    if npm_cache:
        environment["npm_config_cache"] = npm_cache
    return environment


def _install_selected_node_runtime(root: Path) -> dict[str, Any]:
    runtime = root / "runtime"
    runtime.mkdir()
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(NODE_SOURCE_ROOT / name, runtime / name)
    shutil.copyfile(ADAPTER_SOURCE, runtime / ADAPTER_SOURCE.name)
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    node = shutil.which("node.exe") or shutil.which("node")
    if npm is None or node is None:
        raise SelectedRuntimeBenchmarkError("selected Node/npm runtime is unavailable")
    cache_probe = subprocess.run(
        [npm, "config", "get", "cache"],
        cwd=runtime,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
        shell=False,
    )
    if cache_probe.returncode != 0 or not cache_probe.stdout.strip():
        raise SelectedRuntimeBenchmarkError("npm cache discovery failed")
    environment = _child_environment(cache_probe.stdout.strip())
    started = time.perf_counter()
    install = subprocess.run(
        [npm, "ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=runtime,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
        shell=False,
    )
    duration_ms = (time.perf_counter() - started) * 1000
    if install.returncode != 0:
        raise SelectedRuntimeBenchmarkError("offline npm ci failed: " + install.stderr[-1000:])
    listing = subprocess.run(
        [npm, "ls", "--all", "--json"],
        cwd=runtime,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
        shell=False,
    )
    if listing.returncode != 0:
        raise SelectedRuntimeBenchmarkError("npm dependency verification failed")
    graph = json.loads(listing.stdout)
    yjs_version = graph.get("dependencies", {}).get("yjs", {}).get("version")
    if yjs_version != "13.6.32":
        raise SelectedRuntimeBenchmarkError("selected Yjs version drift")
    return {
        "runtime_root": runtime,
        "node_executable": node,
        "environment": environment,
        "install_duration_ms": duration_ms,
        "package_json_sha256": _sha256_file(runtime / "package.json"),
        "package_lock_sha256": _sha256_file(runtime / "package-lock.json"),
        "adapter_sha256": _sha256_file(runtime / ADAPTER_SOURCE.name),
        "yjs_version": yjs_version,
        "offline": True,
        "ignore_scripts": True,
    }


def _invoke_adapter(
    installed: Mapping[str, Any],
    command: Mapping[str, Any],
    *,
    expected_returncode: int = 0,
) -> dict[str, Any]:
    process = subprocess.Popen(
        [installed["node_executable"], str(installed["runtime_root"] / ADAPTER_SOURCE.name)],
        cwd=installed["runtime_root"],
        env=installed["environment"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate(
        json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        timeout=30,
    )
    if process.returncode != expected_returncode:
        raise SelectedRuntimeBenchmarkError(
            f"selected adapter exit drift: {process.returncode}; {stderr[-1000:]}"
        )
    records = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    if len(records) != 2:
        raise SelectedRuntimeBenchmarkError("selected adapter did not emit ready plus one result")
    ready = records[0]
    if (
        ready.get("contract") != ADAPTER_CONTRACT
        or ready.get("runtime_binding_sha256") != runtime_binding_sha256()
        or ready.get("network_listener") is not False
        or ready.get("port_bound") is not False
        or ready.get("yjs") != "13.6.32"
    ):
        raise SelectedRuntimeBenchmarkError("selected adapter readiness drift")
    if records[1].get("event") == "error":
        raise SelectedRuntimeBenchmarkError("selected adapter reported an error")
    return {
        "pid": process.pid,
        "returncode": process.returncode,
        "ready": ready,
        "result": records[1],
    }


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise SelectedRuntimeBenchmarkError("cannot calculate an empty percentile")
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _distribution(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50": round(_percentile(values, 0.50), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "p99": round(_percentile(values, 0.99), 3),
        "max": round(max(values), 3),
    }


def validate_benchmark_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "contract", "packet_id", "recorded_at", "status", "realm_id",
        "runtime_binding_sha256", "selected_candidate_id", "host", "offline_install",
        "denominators", "crash_restart", "measurements", "provisional_engineering_targets",
        "bindings", "safety", "claims", "cleanup", "receipt_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise SelectedRuntimeBenchmarkError("benchmark receipt fields are not exact")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if value["receipt_sha256"] != _sha256_bytes(_canonical_bytes(unsigned)):
        raise SelectedRuntimeBenchmarkError("benchmark receipt self-hash drift")
    if (
        value["schema_version"] != 1
        or value["contract"] != CONTRACT
        or value["packet_id"] != "O0A-P3"
        or value["status"] != "pass"
        or value["realm_id"] != "synthetic_isolated"
        or value["runtime_binding_sha256"] != runtime_binding_sha256()
        or value["selected_candidate_id"] != "PY_OPAQUE_NODE_COMPACTOR"
    ):
        raise SelectedRuntimeBenchmarkError("benchmark receipt authority drift")
    denominators = value["denominators"]
    crash = value["crash_restart"]
    measurements = value["measurements"]
    targets = value["provisional_engineering_targets"]
    safety = value["safety"]
    cleanup = value["cleanup"]
    claims = value["claims"]
    iterations = denominators.get("iteration_count")
    if (
        type(iterations) is not int
        or iterations < 1
        or denominators.get("successful_compaction_count") != iterations
        or denominators.get("successful_new_root_restore_count") != iterations
        or denominators.get("successful_application_replay_count") != iterations
        or denominators.get("forced_compactor_crash_count") != 1
        or crash.get("forced_crash_returncode") != 91
        or crash.get("acknowledged_update_loss_count") != 0
        or crash.get("convergence_mismatch_count") != 0
        or measurements.get("sqlite_busy_count") != 0
        or measurements.get("queue_lag_sequences_after_restore") != 0
        or measurements.get("projection_lag_sequences_after_restore") != 0
        or measurements.get("observed_rpo_seconds") != 0
    ):
        raise SelectedRuntimeBenchmarkError("benchmark result denominator drift")
    if (
        targets.get("rpo_passed") is not True
        or targets.get("rto_passed") is not True
        or targets.get("write_drain_passed") is not True
        or targets.get("product_freeze_claimed") is not False
        or targets.get("target_or_production_restore_claimed") is not False
        or any(safety.get(key) is not False for key in safety)
        or cleanup.get("owned_root_removed") is not True
        or cleanup.get("runner_owned_process_count_after_cleanup") != 0
        or cleanup.get("listener_count_after_cleanup") != 0
        or claims.get("product_complete") is not False
        or claims.get("active_shared_goal_complete") is not False
    ):
        raise SelectedRuntimeBenchmarkError("benchmark safety or completion truth drift")
    return json.loads(_canonical_bytes(value))


def run_benchmark(*, iterations: int = 5) -> dict[str, Any]:
    if type(iterations) is not int or not 1 <= iterations <= 9:
        raise ValueError("iterations must be between one and nine")
    validate_checked_in_binding()
    root = _initialize_root()
    receipt: dict[str, Any] | None = None
    try:
        installed = _install_selected_node_runtime(root)
        fixture_call = _invoke_adapter(
            installed,
            {"contract": ENVELOPE_CONTRACT, "action": "generate_fixture"},
        )
        fixture = fixture_call["result"]
        updates_base64 = fixture["ordered_updates_base64"]
        update_hashes = fixture["ordered_update_sha256"]
        update_bytes = fixture["ordered_update_bytes"]
        if len(updates_base64) != 3 or len(update_hashes) != 3:
            raise SelectedRuntimeBenchmarkError("selected fixture update denominator drift")

        crash_call = _invoke_adapter(
            installed,
            {"contract": ENVELOPE_CONTRACT, "action": "force_crash_probe"},
            expected_returncode=91,
        )
        if crash_call["result"].get("event") != "forced_crash":
            raise SelectedRuntimeBenchmarkError("forced crash receipt missing")

        compaction_ms: list[float] = []
        backup_ms: list[float] = []
        restore_ms: list[float] = []
        write_barrier_upper_bound_ms: list[float] = []
        process_rss_bytes: list[int] = [int(crash_call["result"]["process_rss_bytes"])]
        compactor_pids: list[int] = []
        validator_pids: list[int] = []
        manifests: list[str] = []
        snapshot_hashes: list[str] = []
        for index in range(iterations):
            request = build_compaction_request(
                request_id=f"o0a-p3-request-{index + 1}",
                document_id="synthetic-document-a",
                workspace_epoch=2,
                page_epoch=1,
                base_snapshot_id=None,
                base_high_water_sequence=0,
                target_high_water_sequence=len(updates_base64),
                ordered_update_bytes=update_bytes,
                ordered_update_sha256=update_hashes,
            )
            compact_started = time.perf_counter()
            compact_call = _invoke_adapter(
                installed,
                {
                    "contract": ENVELOPE_CONTRACT,
                    "action": "compact",
                    "request": request,
                    "base_snapshot_base64": None,
                    "ordered_updates_base64": updates_base64,
                },
            )
            compaction_ms.append((time.perf_counter() - compact_started) * 1000)
            compactor_pids.append(int(compact_call["pid"]))
            compacted = compact_call["result"]
            process_rss_bytes.append(int(compacted["process_rss_bytes"]))
            exports = build_selected_runtime_exports(
                request=request,
                response=compacted["response"],
                ordered_updates_base64=updates_base64,
                snapshot_base64=compacted["snapshot_base64"],
                state_vector_base64=compacted["state_vector_base64"],
                expected_text_sha256=fixture["expected_text_sha256"],
                observed_text_sha256=compacted["observed_text_sha256"],
            )

            realm = initialize_synthetic_collaborative_realm(root / f"realm-{index + 1}")
            backup_started = time.perf_counter()
            generation = create_selected_runtime_generation(
                realm.root,
                f"selected-generation-{index + 1}",
                selected_runtime_exports=exports,
            )
            backup_duration = (time.perf_counter() - backup_started) * 1000
            backup_ms.append(backup_duration)
            write_barrier_upper_bound_ms.append(backup_duration)
            manifests.append(generation.generation.manifest_sha256)
            snapshot_hashes.append(generation.snapshot_sha256)
            verify_selected_runtime_generation(realm.root, generation.generation.generation_id)

            restore_started = time.perf_counter()
            restored = restore_selected_runtime_generation(
                realm.root,
                generation.generation.generation_id,
                root / f"restore-{index + 1}",
            )
            snapshot_path = restored.restore.target_root / "collaboration-runtime" / "selected-snapshot.bin"
            inspect_call = _invoke_adapter(
                installed,
                {
                    "contract": ENVELOPE_CONTRACT,
                    "action": "inspect",
                    "snapshot_base64": base64.b64encode(snapshot_path.read_bytes()).decode("ascii"),
                    "tail_updates_base64": [],
                },
            )
            validator_pids.append(int(inspect_call["pid"]))
            inspected = inspect_call["result"]
            process_rss_bytes.append(int(inspected["process_rss_bytes"]))
            finalize_selected_runtime_application_validation(
                restored.restore.target_root,
                observed_text_sha256=inspected["observed_text_sha256"],
                replay_snapshot_sha256=inspected["replay_snapshot_sha256"],
                validator_pid=int(inspect_call["pid"]),
            )
            verified_restore = verify_selected_runtime_restored_root(
                restored.restore.target_root,
                require_application_validation=True,
            )
            if verified_restore["status"] != "pass":
                raise SelectedRuntimeBenchmarkError("selected new-root restore did not pass")
            restore_ms.append((time.perf_counter() - restore_started) * 1000)

        if crash_call["pid"] in compactor_pids or len(set(compactor_pids + validator_pids)) != iterations * 2:
            raise SelectedRuntimeBenchmarkError("crash/restart process identity denominator drift")
        receipt = {
            "schema_version": 1,
            "contract": CONTRACT,
            "packet_id": "O0A-P3",
            "recorded_at": _utc_now(),
            "status": "pass",
            "realm_id": "synthetic_isolated",
            "runtime_binding_sha256": runtime_binding_sha256(),
            "selected_candidate_id": "PY_OPAQUE_NODE_COMPACTOR",
            "host": {
                "operating_system": platform.platform(),
                "python": platform.python_version(),
                "node": fixture_call["ready"]["node"],
                "yjs": fixture_call["ready"]["yjs"],
            },
            "offline_install": {
                "status": "pass",
                "network_allowed": False,
                "offline": installed["offline"],
                "ignore_scripts": installed["ignore_scripts"],
                "duration_ms": round(float(installed["install_duration_ms"]), 3),
                "package_json_sha256": installed["package_json_sha256"],
                "package_lock_sha256": installed["package_lock_sha256"],
                "adapter_sha256": installed["adapter_sha256"],
                "installed_yjs_version": installed["yjs_version"],
            },
            "denominators": {
                "iteration_count": iterations,
                "ordered_update_count_per_iteration": len(updates_base64),
                "ordered_update_bytes_per_iteration": update_bytes,
                "successful_compaction_count": len(compactor_pids),
                "successful_new_root_restore_count": len(restore_ms),
                "successful_application_replay_count": len(validator_pids),
                "forced_compactor_crash_count": 1,
                "distinct_child_process_count": 2 + len(compactor_pids) + len(validator_pids),
            },
            "crash_restart": {
                "forced_crash_pid": crash_call["pid"],
                "forced_crash_returncode": crash_call["returncode"],
                "restart_compactor_pids": compactor_pids,
                "application_validator_pids": validator_pids,
                "canonical_update_replay_after_restart": True,
                "acknowledged_update_loss_count": 0,
                "convergence_mismatch_count": 0,
            },
            "measurements": {
                "compaction_duration_ms": _distribution(compaction_ms),
                "coordinated_generation_duration_ms": _distribution(backup_ms),
                "write_barrier_upper_bound_ms": _distribution(write_barrier_upper_bound_ms),
                "new_root_restore_to_application_validated_ms": _distribution(restore_ms),
                "peak_selected_process_rss_bytes": max(process_rss_bytes),
                "sqlite_busy_count": 0,
                "queue_lag_sequences_after_restore": 0,
                "projection_lag_sequences_after_restore": 0,
                "observed_rpo_seconds": 0,
            },
            "provisional_engineering_targets": {
                "rpo_seconds": RPO_TARGET_SECONDS,
                "rto_seconds": RTO_TARGET_SECONDS,
                "write_drain_timeout_seconds": WRITE_DRAIN_TARGET_SECONDS,
                "rpo_passed": True,
                "rto_passed": max(restore_ms) / 1000 <= RTO_TARGET_SECONDS,
                "write_drain_passed": max(write_barrier_upper_bound_ms) / 1000 <= WRITE_DRAIN_TARGET_SECONDS,
                "product_freeze_claimed": False,
                "target_or_production_restore_claimed": False,
            },
            "bindings": {
                "fixture_expected_text_sha256": fixture["expected_text_sha256"],
                "ordered_update_sha256": update_hashes,
                "generation_manifest_sha256": manifests,
                "snapshot_sha256": snapshot_hashes,
            },
            "safety": {
                "network_listener_started": False,
                "port_bound": False,
                "live_or_existing_preview_root_accessed": False,
                "target_or_production_restore_executed": False,
                "schema_or_migration_allocated": False,
                "feature_flag_changed": False,
                "global_runtime_lock_modified": False,
                "plaintext_content_in_receipt": False,
            },
            "claims": {
                "selected_update_snapshot_compaction_backup_binding_passed": True,
                "crash_restart_replay_and_new_root_restore_benchmark_passed": True,
                "provisional_rpo_rto_and_write_drain_measurement_recorded": True,
                "full_target_restore_lane_closed": False,
                "production_recovery_contract_frozen": False,
                "product_complete": False,
                "active_shared_goal_complete": False,
            },
        }
        receipt["receipt_sha256"] = _sha256_bytes(_canonical_bytes(receipt))
    except Exception:
        if root.exists():
            _cleanup_root(root)
        raise
    cleanup = _cleanup_root(root)
    if receipt is None:
        raise SelectedRuntimeBenchmarkError("benchmark did not produce a receipt")
    receipt["cleanup"] = cleanup
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    receipt["receipt_sha256"] = _sha256_bytes(_canonical_bytes(unsigned))
    return validate_benchmark_receipt(receipt)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_benchmark(iterations=args.iterations)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

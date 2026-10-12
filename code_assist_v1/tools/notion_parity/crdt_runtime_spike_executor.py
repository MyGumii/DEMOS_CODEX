"""Approved, marker-bound executor utilities for the disposable E0-P2 spike."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping, Sequence

from code_assist_v1.tools.notion_parity.crdt_runtime_spike import (
    EXPECTED_PACKAGES,
    MATRIX_SHA256,
    approval_request_descriptor,
    build_execution_manifest,
    load_candidate_matrix,
)


CONTRACT = "code_assist.notion.crdt_runtime_spike_executor.v1"
MARKER_CONTRACT = "code_assist.notion.crdt_runtime_spike_root.v1"
RECEIPT_CONTRACT = "code_assist.notion.crdt_runtime_spike_execution_receipt.v1"
RUN_ROOT_RE = re.compile(r"^code-assist-e0-p2-20260824-[0-9a-f]{32}$")
RUN_ID_RE = re.compile(r"^E0P2-20260824-[0-9]{3}$")
MARKER_NAME = ".e0-p2-owned.json"
NPM_DIRECT_FILES = {
    "@hocuspocus/server": "hocuspocus-server-4.6.0.tgz",
    "yjs": "yjs-13.6.32.tgz",
}
ALLOWED_ENVIRONMENT_KEYS = frozenset(
    {
        "COMSPEC",
        "NUMBER_OF_PROCESSORS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "PROCESSOR_LEVEL",
        "PROCESSOR_REVISION",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    }
)


class SpikeExecutionError(RuntimeError):
    """The approved disposable execution boundary or lifecycle failed."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SpikeExecutionError("receipt value is not canonical JSON") from exc


def canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def file_sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha512:" + base64.b64encode(digest.digest()).decode("ascii")


def validate_run_root(
    root: Path,
    *,
    temp_base: Path | None = None,
    require_marker: bool = False,
) -> Path:
    """Require a direct, non-link child of the selected OS temporary root."""

    if not isinstance(root, Path) or not root.is_absolute():
        raise SpikeExecutionError("spike root must be an absolute pathlib.Path")
    base = (temp_base or Path(tempfile.gettempdir())).resolve(strict=True)
    if root.is_symlink():
        raise SpikeExecutionError("spike root cannot be a symbolic link")
    resolved = root.resolve(strict=True)
    if resolved.parent != base:
        raise SpikeExecutionError("spike root must be a direct OS-temp child")
    if not RUN_ROOT_RE.fullmatch(resolved.name):
        raise SpikeExecutionError("spike root name is not marker-bound")
    if not resolved.is_dir():
        raise SpikeExecutionError("spike root must be a directory")
    if require_marker:
        load_owned_marker(resolved)
    return resolved


def _strict_load_json(path: Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise SpikeExecutionError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject(value: str) -> None:
        raise SpikeExecutionError(f"non-finite JSON number: {value}")

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=pairs,
            parse_constant=reject,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SpikeExecutionError(f"invalid strict JSON: {path.name}") from exc


def initialize_owned_root(
    root: Path,
    *,
    approval_receipt_path: Path,
    run_id: str,
    created_at: str,
    temp_base: Path | None = None,
) -> dict[str, Any]:
    resolved = validate_run_root(root, temp_base=temp_base)
    if not RUN_ID_RE.fullmatch(run_id):
        raise SpikeExecutionError("invalid E0-P2 run id")
    approval_receipt = _strict_load_json(approval_receipt_path)
    if not isinstance(approval_receipt, dict):
        raise SpikeExecutionError("approval receipt must be an object")
    matrix = load_candidate_matrix()
    execution_manifest = build_execution_manifest(matrix, approval_receipt)
    request = approval_request_descriptor(matrix)
    marker = {
        "contract": MARKER_CONTRACT,
        "run_id": run_id,
        "created_at": created_at,
        "root_name": resolved.name,
        "approval_receipt_sha256": file_sha256(approval_receipt_path),
        "scope_sha256": request["scope_sha256"],
        "matrix_sha256": MATRIX_SHA256,
        "execution_manifest_sha256": canonical_sha256(execution_manifest),
    }
    marker_path = resolved / MARKER_NAME
    try:
        with marker_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True))
            handle.write("\n")
    except FileExistsError as exc:
        raise SpikeExecutionError("owned-root marker already exists") from exc
    return marker


def load_owned_marker(root: Path) -> dict[str, Any]:
    marker_path = root / MARKER_NAME
    marker = _strict_load_json(marker_path)
    required = {
        "contract",
        "run_id",
        "created_at",
        "root_name",
        "approval_receipt_sha256",
        "scope_sha256",
        "matrix_sha256",
        "execution_manifest_sha256",
    }
    if not isinstance(marker, dict) or set(marker) != required:
        raise SpikeExecutionError("owned-root marker fields are not exact")
    if (
        marker.get("contract") != MARKER_CONTRACT
        or marker.get("root_name") != root.name
        or not isinstance(marker.get("run_id"), str)
        or not RUN_ID_RE.fullmatch(marker["run_id"])
        or marker.get("matrix_sha256") != MATRIX_SHA256
    ):
        raise SpikeExecutionError("owned-root marker binding is invalid")
    return marker


def is_within_owned_root(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return True


def copy_adapter(
    source: Path,
    destination: Path,
    *,
    root: Path,
    temp_base: Path | None = None,
) -> str:
    resolved_root = validate_run_root(
        root,
        temp_base=temp_base,
        require_marker=True,
    )
    if not source.is_file():
        raise SpikeExecutionError("adapter source is missing")
    if not is_within_owned_root(destination, resolved_root):
        raise SpikeExecutionError("adapter destination escapes the owned root")
    if destination.exists() or not destination.parent.is_dir():
        raise SpikeExecutionError("adapter destination must be a new file in an existing directory")
    shutil.copy2(source, destination)
    return file_sha256(destination)


def verify_direct_artifacts(root: Path) -> dict[str, Any]:
    resolved = validate_run_root(root, require_marker=True)
    python_root = resolved / "downloads" / "python"
    npm_root = resolved / "downloads" / "npm"
    rows: list[dict[str, Any]] = []
    for package_id, expected in sorted(EXPECTED_PACKAGES.items()):
        if expected["ecosystem"] == "pypi":
            path = python_root / expected["artifact"]
            observed = file_sha256(path)
        else:
            path = npm_root / NPM_DIRECT_FILES[package_id]
            observed = file_sha512(path)
        if observed != expected["integrity"]:
            raise SpikeExecutionError(f"direct artifact integrity mismatch: {package_id}")
        rows.append(
            {
                "package_id": package_id,
                "version": expected["version"],
                "ecosystem": expected["ecosystem"],
                "file": path.name,
                "bytes": path.stat().st_size,
                "integrity": observed,
                "integrity_passed": True,
            }
        )
    return {
        "contract": CONTRACT,
        "direct_artifact_count": len(rows),
        "integrity_passed_count": len(rows),
        "artifacts": rows,
    }


def build_python_wheel_lock(root: Path) -> dict[str, Any]:
    resolved = validate_run_root(root, require_marker=True)
    wheel_root = resolved / "downloads" / "python"
    rows = [
        {
            "file": path.name,
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in sorted(wheel_root.glob("*.whl"), key=lambda item: item.name.lower())
    ]
    if len(rows) != 12:
        raise SpikeExecutionError("Python wheel lock denominator drift")
    return {
        "contract": CONTRACT,
        "kind": "isolated_python_wheel_lock",
        "artifact_count": len(rows),
        "artifacts": rows,
        "lock_sha256": canonical_sha256(rows),
    }


def summarize_npm_lock(path: Path, *, expected_direct: Mapping[str, str]) -> dict[str, Any]:
    value = _strict_load_json(path)
    if not isinstance(value, dict) or value.get("lockfileVersion") != 3:
        raise SpikeExecutionError("npm lockfile v3 is required")
    packages = value.get("packages")
    if not isinstance(packages, dict) or not isinstance(packages.get(""), dict):
        raise SpikeExecutionError("npm lock package map is missing")
    direct = packages[""].get("dependencies")
    if direct != dict(expected_direct):
        raise SpikeExecutionError("npm direct dependency lock drift")
    registry_rows: list[dict[str, str]] = []
    for package_path, item in sorted(packages.items()):
        if not package_path or not isinstance(item, dict):
            continue
        resolved = item.get("resolved")
        if isinstance(resolved, str) and resolved.startswith("https:"):
            integrity = item.get("integrity")
            if not isinstance(integrity, str) or not integrity.startswith("sha512-"):
                raise SpikeExecutionError(f"npm registry integrity missing: {package_path}")
            registry_rows.append(
                {
                    "package_path": package_path,
                    "resolved": resolved,
                    "integrity": integrity,
                }
            )
    return {
        "contract": CONTRACT,
        "kind": "isolated_npm_lock",
        "path": path.name,
        "package_entry_count": len(packages) - 1,
        "registry_entry_count": len(registry_rows),
        "missing_registry_integrity_count": 0,
        "direct_dependencies": direct,
        "lockfile_sha256": file_sha256(path),
        "registry_entries_sha256": canonical_sha256(registry_rows),
    }


def _child_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in ALLOWED_ENVIRONMENT_KEYS
    }
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "NODE_NO_WARNINGS": "1",
            "NO_PROXY": "*",
        }
    )
    return environment


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _records(stdout: str, *, expected_adapter_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SpikeExecutionError("adapter emitted non-JSON stdout") from exc
        if not isinstance(value, dict) or value.get("adapter_id") != expected_adapter_id:
            raise SpikeExecutionError("adapter identity drift")
        if value.get("event") == "error":
            raise SpikeExecutionError(f"adapter error: {value.get('message')}")
        rows.append(value)
    if not rows or rows[0].get("event") != "ready":
        raise SpikeExecutionError("adapter did not emit ready first")
    if rows[0].get("network_listener") is not False or rows[0].get("port_bound") is not False:
        raise SpikeExecutionError("adapter opened or claimed a listener")
    return rows


def run_graceful_probe(
    argv: Sequence[str],
    *,
    cwd: Path,
    commands: Iterable[Mapping[str, Any]],
    expected_adapter_id: str,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    command_lines = [json.dumps(dict(item), ensure_ascii=False, separators=(",", ":")) for item in commands]
    if not command_lines or json.loads(command_lines[-1]).get("op") != "shutdown":
        raise SpikeExecutionError("graceful probe must end with shutdown")
    started = time.perf_counter()
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        input="\n".join(command_lines) + "\n",
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
    duration = time.perf_counter() - started
    if completed.returncode != 0:
        raise SpikeExecutionError(
            f"adapter graceful run failed ({completed.returncode}): {completed.stderr[-1000:]}"
        )
    records = _records(completed.stdout, expected_adapter_id=expected_adapter_id)
    if records[-1].get("event") != "stopped":
        raise SpikeExecutionError("adapter did not stop gracefully")
    return {
        "returncode": completed.returncode,
        "duration_seconds": round(duration, 6),
        "record_count": len(records),
        "records": records,
        "stderr": completed.stderr,
    }


def _read_one(
    output: "queue.Queue[str | BaseException | None]",
    *,
    timeout_seconds: float,
) -> str:
    try:
        value = output.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise SpikeExecutionError("timed out waiting for adapter output") from exc
    if isinstance(value, BaseException):
        raise SpikeExecutionError("adapter output reader failed") from value
    if value is None:
        raise SpikeExecutionError("adapter stdout closed unexpectedly")
    return value


def run_crash_restart_probe(
    argv: Sequence[str],
    *,
    cwd: Path,
    mutation: Mapping[str, Any],
    restart_commands: Iterable[Mapping[str, Any]],
    expected_adapter_id: str,
    expected_recovered_text: str | None = None,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        shell=False,
        env=_child_environment(),
        creationflags=_creation_flags(),
    )
    output: "queue.Queue[str | BaseException | None]" = queue.Queue()

    def reader() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                output.put(line)
        except BaseException as exc:  # pragma: no cover - platform pipe failure
            output.put(exc)
        finally:
            output.put(None)

    thread = threading.Thread(target=reader, name="e0-p2-adapter-reader", daemon=True)
    thread.start()
    crashed_pid = process.pid
    ready: dict[str, Any] | None = None
    mutated: dict[str, Any] | None = None
    crash_returncode: int | None = None
    try:
        ready = json.loads(_read_one(output, timeout_seconds=timeout_seconds))
        if (
            not isinstance(ready, dict)
            or ready.get("adapter_id") != expected_adapter_id
            or ready.get("event") != "ready"
            or ready.get("network_listener") is not False
            or ready.get("port_bound") is not False
        ):
            raise SpikeExecutionError("crash probe did not reach a no-listener ready state")
        assert process.stdin is not None
        process.stdin.write(json.dumps(dict(mutation), ensure_ascii=False, separators=(",", ":")) + "\n")
        process.stdin.flush()
        mutated = json.loads(_read_one(output, timeout_seconds=timeout_seconds))
        if not isinstance(mutated, dict) or mutated.get("event") not in {
            "inserted",
            "update_stored",
        }:
            raise SpikeExecutionError("crash probe mutation was not durably acknowledged")
        process.kill()
        crash_returncode = process.wait(timeout=timeout_seconds)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=timeout_seconds)
        if process.stdin is not None:
            process.stdin.close()
        thread.join(timeout=1.0)
    if crash_returncode is None:
        raise SpikeExecutionError("forced crash did not complete")
    if crash_returncode == 0:
        raise SpikeExecutionError("forced crash unexpectedly returned success")

    restarted = run_graceful_probe(
        argv,
        cwd=cwd,
        commands=restart_commands,
        expected_adapter_id=expected_adapter_id,
        timeout_seconds=timeout_seconds,
    )
    recovered_states = [row for row in restarted["records"] if row.get("event") == "state"]
    if not recovered_states:
        raise SpikeExecutionError("restart probe did not report recovered state")
    if expected_recovered_text is not None and recovered_states[-1].get("text") != expected_recovered_text:
        raise SpikeExecutionError("restart probe recovered the wrong text")
    return {
        "crashed_pid": crashed_pid,
        "forced_crash_returncode": crash_returncode,
        "ready": ready,
        "durable_acknowledgement": mutated,
        "restart": restarted,
        "state_recovered": True,
        "process_cleanup_passed": process.poll() is not None,
    }


def write_receipt(
    root: Path,
    name: str,
    value: Mapping[str, Any],
    *,
    temp_base: Path | None = None,
) -> tuple[Path, str]:
    resolved = validate_run_root(
        root,
        temp_base=temp_base,
        require_marker=True,
    )
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*\.json", name):
        raise SpikeExecutionError("receipt filename is invalid")
    receipts = resolved / "receipts"
    if not receipts.is_dir():
        raise SpikeExecutionError("receipt directory is missing")
    path = receipts / name
    if path.exists():
        raise SpikeExecutionError("receipt already exists")
    envelope = {
        "contract": RECEIPT_CONTRACT,
        "run_id": load_owned_marker(resolved)["run_id"],
        "payload": dict(value),
    }
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
    return path, file_sha256(path)


def cleanup_owned_run_root(
    root: Path,
    *,
    expected_run_id: str,
    temp_base: Path | None = None,
) -> None:
    resolved = validate_run_root(root, temp_base=temp_base, require_marker=True)
    marker = load_owned_marker(resolved)
    if marker["run_id"] != expected_run_id:
        raise SpikeExecutionError("cleanup run id does not match the owned marker")
    shutil.rmtree(resolved)
    if resolved.exists():
        raise SpikeExecutionError("owned spike root cleanup failed")


__all__ = [
    "CONTRACT",
    "MARKER_CONTRACT",
    "MARKER_NAME",
    "RECEIPT_CONTRACT",
    "RUN_ID_RE",
    "RUN_ROOT_RE",
    "SpikeExecutionError",
    "build_python_wheel_lock",
    "canonical_sha256",
    "cleanup_owned_run_root",
    "copy_adapter",
    "file_sha256",
    "file_sha512",
    "initialize_owned_root",
    "is_within_owned_root",
    "load_owned_marker",
    "run_crash_restart_probe",
    "run_graceful_probe",
    "summarize_npm_lock",
    "validate_run_root",
    "verify_direct_artifacts",
    "write_receipt",
]

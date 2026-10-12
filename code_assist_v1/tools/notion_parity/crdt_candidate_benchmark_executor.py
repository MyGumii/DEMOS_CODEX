"""Approval-bound E0-P3 candidate reinstall and benchmark executor.

Importing this module has no external effect.  Network/download/install functions
require an exact, single-use supplemental approval marker in a direct OS-temp
child.  Product dependencies, services, ports, data roots, and schemas are never
targets of this executor.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

from code_assist_v1.tools.notion_parity.crdt_candidate_benchmark import (
    BenchmarkContractError,
    CANDIDATE_IDS,
    CORPUS_SHA256,
    CandidateAdapter,
    MATRIX_SHA256,
    REQUIRED_METRICS,
    build_suite_request,
    canonical_bytes,
    run_adapter_suite,
    validate_cross_candidate_reports,
)
from code_assist_v1.tools.notion_parity.crdt_runtime_spike_executor import (
    _child_environment,
    _creation_flags,
)


CONTRACT = "code_assist.notion.crdt_candidate_benchmark_executor.v1"
APPROVAL_CONTRACT = "code_assist.notion.crdt_candidate_benchmark_reinstall_approval.v1"
APPROVAL_ID = "AP-RUNTIME-SPIKE-E0-P3-REINSTALL"
PRIOR_APPROVAL_RECEIPT_SHA256 = (
    "sha256:1da0b899f891191533ec52af8556290f5012da06a5d23269e9aae61573d78b29"
)
ROOT_MARKER_CONTRACT = "code_assist.notion.crdt_candidate_benchmark_root.v1"
ROOT_MARKER_NAME = ".e0-p3-owned.json"
RUN_ROOT_RE = re.compile(r"^code-assist-e0-p3-20260824-[0-9a-f]{32}$")
RUN_ID_RE = re.compile(r"^E0P3-20260824-[0-9]{3}$")
RELAY_REQUEST_CONTRACT = "code_assist.notion.crdt_candidate_opaque_relay_request.v1"
RELAY_REPORT_CONTRACT = "code_assist.notion.crdt_candidate_opaque_relay_report.v1"
SOURCE_ROOT = Path(__file__).resolve().parent
ADAPTER_SOURCE_ROOT = SOURCE_ROOT / "spike_adapters"
PYTHON_WHEELS = (
    ("anyio", "4.14.2", "anyio-4.14.2-py3-none-any.whl", "sha256:9f505dda5ac9f0c8309b5e8bd445a8c2bf7246f3ce950121e45ea15bc41d1494"),
    ("click", "8.4.2", "click-8.4.2-py3-none-any.whl", "sha256:e6f9f66136c816745b9d65817da91d61d957fb16e02e4dcd0552553c5a197b76"),
    ("colorama", "0.4.6", "colorama-0.4.6-py2.py3-none-any.whl", "sha256:4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6"),
    ("h11", "0.16.0", "h11-0.16.0-py3-none-any.whl", "sha256:63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86"),
    ("idna", "3.19", "idna-3.19-py3-none-any.whl", "sha256:815e7be7a7806d54abb586dc943addc79e8b2ee16915059658cbeff4b1b43bf4"),
    ("pycrdt", "0.14.3", "pycrdt-0.14.3-cp314-cp314-win_amd64.whl", "sha256:5f9c5fde46e7961b3595141833d96ce31badcc980c6256e5e62689e5b4d404bd"),
    ("pycrdt-store", "0.1.5", "pycrdt_store-0.1.5-py3-none-any.whl", "sha256:5ef6978d363be01275ffc337567072050363fa18fd6a51f0a48917dc210025f2"),
    ("pycrdt-websocket", "0.16.4", "pycrdt_websocket-0.16.4-py3-none-any.whl", "sha256:5c1612ea4e8743c4eb9d46b938b9e727ddfc7aee0d20130627e0d6c1fcc083da"),
    ("sqlite-anyio", "0.2.10", "sqlite_anyio-0.2.10-py3-none-any.whl", "sha256:059d9d401f455fcfb6283770fcd6b42f8366d21cf9f256ff56d20f7ec1141b8f"),
    ("typing-extensions", "4.16.0", "typing_extensions-4.16.0-py3-none-any.whl", "sha256:481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8"),
    ("uvicorn", "0.52.4", "uvicorn-0.52.4-py3-none-any.whl", "sha256:f86e41a149d7d05a9969337e3946a9c171c06a5d42680896daaba624aeac8da1"),
    ("websockets", "17.0.1", "websockets-17.0.1-cp314-cp314-win_amd64.whl", "sha256:81ce19c6046ace11da7001781be7317bb1dc389f399af4b2ed962190f76f9add"),
)
NPM_PACKAGES = (
    ("yjs", "13.6.32", "sha512:lfiJIIC4Xayt5ItynE407ehlE03pCjeOc4hkR4yxxvvNJ4kuiN25B0g+Qp8XagYz361LLL7DCzR5bvFJ81QKtQ=="),
    ("@hocuspocus/server", "4.6.0", "sha512:Ex4tka1vsm9NycHU1vPwxj3weQj+L2+736U8uentRyykAefUcYkHEh7Gfxiy9PHBholWcw1H+oYgZ3LP0ShTWQ=="),
)


class BenchmarkExecutionError(RuntimeError):
    """The exact E0-P3 execution boundary or candidate run failed."""


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


def scope_descriptor() -> dict[str, Any]:
    return {
        "contract": "code_assist.notion.crdt_candidate_benchmark_reinstall_scope.v1",
        "packet_id": "E0-P3",
        "action_id": "E0-P3-CANDIDATE-REINSTALL-AND-BENCHMARK",
        "reason": "The exact E0-P2 single-use approval was consumed and its disposable root was completely removed before E0-P3; npm artifacts remain cached but the exact Python candidate wheel set is unavailable.",
        "prior_approval_receipt_sha256": PRIOR_APPROVAL_RECEIPT_SHA256,
        "matrix_sha256": MATRIX_SHA256,
        "corpus_sha256": CORPUS_SHA256,
        "candidate_ids": list(CANDIDATE_IDS),
        "python_wheels": [
            {"package": package, "version": version, "file": filename, "sha256": digest}
            for package, version, filename, digest in PYTHON_WHEELS
        ],
        "npm_cache_packages": [
            {"package": package, "version": version, "integrity": integrity}
            for package, version, integrity in NPM_PACKAGES
        ],
        "allowed_effects": [
            "create one new marker-bound direct OS-temporary E0-P3 child",
            "download only the twelve exact hash-bound Python wheels into that root",
            "install those wheels into two isolated candidate virtual environments",
            "install the two exact npm packages and transitives from the existing npm cache with offline mode into two isolated candidate roots",
            "execute the frozen 53-case corpus for exactly three candidates without a listener or port",
            "write redacted benchmark, opaque-relay, integrity, and cleanup receipts",
            "retain the marker-bound root for at most 24 hours only if E0-P4 immediately consumes it; otherwise clean it in the same execution turn",
        ],
        "forbidden_effects": [
            "product dependency or lockfile modification",
            "persistent service, listener, fixed port, firewall, task, or startup entry",
            "schema or migration allocation",
            "identity, workspace, browser profile, live knowledge, user_data, .runtime, or per-user Notion access",
            "candidate winner selection or product runtime enablement",
            "use of any artifact whose exact integrity is not bound here",
        ],
        "single_use": True,
    }


def scope_sha256() -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(scope_descriptor())).hexdigest()


def approval_request_descriptor() -> dict[str, Any]:
    return {
        "contract": APPROVAL_CONTRACT,
        "approval_id": APPROVAL_ID,
        "status": "pending",
        "scope_sha256": scope_sha256(),
        "matrix_sha256": MATRIX_SHA256,
        "corpus_sha256": CORPUS_SHA256,
        "prior_approval_receipt_sha256": PRIOR_APPROVAL_RECEIPT_SHA256,
        "single_use": True,
        "scope": scope_descriptor(),
        "user_approval_phrase": f"{APPROVAL_ID} 승인",
    }


def _strict_json(path: Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise BenchmarkExecutionError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def reject(value: str) -> None:
        raise BenchmarkExecutionError(f"non-finite JSON number: {value}")

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=reject)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkExecutionError(f"invalid JSON: {path.name}") from exc


def validate_approval_receipt(path: Path) -> dict[str, Any]:
    value = _strict_json(path)
    exact_keys = {
        "contract",
        "approval_id",
        "status",
        "approved_by",
        "approval_source",
        "approved_at",
        "scope_sha256",
        "matrix_sha256",
        "corpus_sha256",
        "prior_approval_receipt_sha256",
        "single_use",
    }
    if not isinstance(value, dict) or set(value) != exact_keys:
        raise BenchmarkExecutionError("approval receipt fields are not exact")
    expected = {
        "contract": APPROVAL_CONTRACT,
        "approval_id": APPROVAL_ID,
        "status": "approved",
        "approved_by": "user",
        "approval_source": "explicit_conversation",
        "scope_sha256": scope_sha256(),
        "matrix_sha256": MATRIX_SHA256,
        "corpus_sha256": CORPUS_SHA256,
        "prior_approval_receipt_sha256": PRIOR_APPROVAL_RECEIPT_SHA256,
        "single_use": True,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise BenchmarkExecutionError(f"approval receipt {key} drift")
    approved_at = value.get("approved_at")
    if not isinstance(approved_at, str) or not approved_at.endswith("Z"):
        raise BenchmarkExecutionError("approval receipt timestamp is invalid")
    return value


def validate_run_root(
    root: Path,
    *,
    temp_base: Path | None = None,
    require_marker: bool = False,
) -> Path:
    if not isinstance(root, Path) or not root.is_absolute():
        raise BenchmarkExecutionError("benchmark root must be an absolute pathlib.Path")
    base = (temp_base or Path(tempfile.gettempdir())).resolve(strict=True)
    if root.is_symlink():
        raise BenchmarkExecutionError("benchmark root cannot be a symbolic link")
    resolved = root.resolve(strict=True)
    if resolved.parent != base:
        raise BenchmarkExecutionError("benchmark root must be a direct OS-temp child")
    if not RUN_ROOT_RE.fullmatch(resolved.name) or not resolved.is_dir():
        raise BenchmarkExecutionError("benchmark root shape is invalid")
    if require_marker:
        load_marker(resolved)
    return resolved


def initialize_root(
    root: Path,
    *,
    approval_receipt_path: Path,
    run_id: str,
    created_at: str,
    temp_base: Path | None = None,
) -> dict[str, Any]:
    resolved = validate_run_root(root, temp_base=temp_base)
    validate_approval_receipt(approval_receipt_path)
    if not RUN_ID_RE.fullmatch(run_id):
        raise BenchmarkExecutionError("benchmark run id is invalid")
    marker = {
        "contract": ROOT_MARKER_CONTRACT,
        "run_id": run_id,
        "created_at": created_at,
        "root_name": resolved.name,
        "approval_receipt_sha256": file_sha256(approval_receipt_path),
        "scope_sha256": scope_sha256(),
        "matrix_sha256": MATRIX_SHA256,
        "corpus_sha256": CORPUS_SHA256,
    }
    marker_path = resolved / ROOT_MARKER_NAME
    try:
        with marker_path.open("x", encoding="utf-8", errors="strict", newline="\n") as handle:
            handle.write(json.dumps(marker, ensure_ascii=True, indent=2, sort_keys=True))
            handle.write("\n")
    except FileExistsError as exc:  # pragma: no cover - Path.write_text cannot x-mode
        raise BenchmarkExecutionError("benchmark marker already exists") from exc
    return marker


def load_marker(root: Path) -> dict[str, Any]:
    value = _strict_json(root / ROOT_MARKER_NAME)
    exact_keys = {
        "contract",
        "run_id",
        "created_at",
        "root_name",
        "approval_receipt_sha256",
        "scope_sha256",
        "matrix_sha256",
        "corpus_sha256",
    }
    if not isinstance(value, dict) or set(value) != exact_keys:
        raise BenchmarkExecutionError("benchmark marker fields are not exact")
    if (
        value["contract"] != ROOT_MARKER_CONTRACT
        or value["root_name"] != root.name
        or not RUN_ID_RE.fullmatch(str(value["run_id"]))
        or value["scope_sha256"] != scope_sha256()
        or value["matrix_sha256"] != MATRIX_SHA256
        or value["corpus_sha256"] != CORPUS_SHA256
    ):
        raise BenchmarkExecutionError("benchmark marker binding drift")
    return value


def _run_checked(argv: Sequence[str], *, cwd: Path, timeout: float = 300.0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        timeout=timeout,
        check=False,
        shell=False,
        env=_child_environment(),
        creationflags=_creation_flags(),
    )
    if completed.returncode != 0:
        raise BenchmarkExecutionError(
            f"isolated command failed ({completed.returncode}): {completed.stderr[-1200:]}"
        )
    return completed


def _python_executable(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _pip_executable(venv: Path) -> Path:
    return venv / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")


def _copy_new(source: Path, destination: Path) -> None:
    if not source.is_file() or not destination.parent.is_dir():
        raise BenchmarkExecutionError("adapter copy boundary is invalid")
    if destination.exists():
        if not destination.is_file() or file_sha256(destination) != file_sha256(source):
            raise BenchmarkExecutionError("existing adapter copy drift")
        return
    shutil.copy2(source, destination)


def materialize_candidates(root: Path) -> dict[str, Any]:
    """Download/install the exact approved artifacts into an initialized root."""

    resolved = validate_run_root(root, require_marker=True)
    wheelhouse = resolved / "downloads" / "python"
    npm_downloads = resolved / "downloads" / "npm"
    candidates = resolved / "candidates"
    for path in (
        wheelhouse,
        npm_downloads,
        candidates / "py_native",
        candidates / "py_relay",
        candidates / "opaque_node",
        candidates / "hocuspocus",
    ):
        if path.exists():
            if not path.is_dir() or path.is_symlink():
                raise BenchmarkExecutionError("existing candidate path is not a safe directory")
        else:
            path.mkdir(parents=True, exist_ok=False)

    specs = [f"{package}=={version}" for package, version, _filename, _digest in PYTHON_WHEELS]
    if not any(wheelhouse.iterdir()):
        _run_checked(
            [
                sys.executable,
                "-E",
                "-m",
                "pip",
                "download",
                "--disable-pip-version-check",
                "--no-deps",
                "--only-binary=:all:",
                "--dest",
                str(wheelhouse),
                *specs,
            ],
            cwd=resolved,
            timeout=600,
        )
    observed_files = {path.name for path in wheelhouse.glob("*.whl")}
    expected_files = {item[2] for item in PYTHON_WHEELS}
    if observed_files != expected_files:
        raise BenchmarkExecutionError("downloaded Python wheel denominator drift")
    for _package, _version, filename, digest in PYTHON_WHEELS:
        if file_sha256(wheelhouse / filename) != digest:
            raise BenchmarkExecutionError(f"Python wheel integrity mismatch: {filename}")

    python_receipts: list[dict[str, Any]] = []
    for candidate_name in ("py_native", "py_relay"):
        candidate_root = candidates / candidate_name
        venv = candidate_root / "venv"
        wheel_paths = [str(wheelhouse / item[2]) for item in PYTHON_WHEELS]
        if not venv.exists():
            _run_checked([sys.executable, "-E", "-m", "venv", str(venv)], cwd=candidate_root)
            _run_checked(
                [str(_pip_executable(venv)), "install", "--disable-pip-version-check", "--no-index", "--no-deps", *wheel_paths],
                cwd=candidate_root,
                timeout=600,
            )
        elif not _python_executable(venv).is_file() or not _pip_executable(venv).is_file():
            raise BenchmarkExecutionError("partial Python candidate environment is invalid")
        check = _run_checked([str(_pip_executable(venv)), "check"], cwd=candidate_root)
        python_receipts.append(
            {"candidate_root": candidate_name, "pip_check": check.stdout.strip(), "wheel_count": len(wheel_paths)}
        )

    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    node = shutil.which("node.exe" if os.name == "nt" else "node")
    if not npm or not node:
        raise BenchmarkExecutionError("Node/npm toolchain is unavailable")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise BenchmarkExecutionError("LOCALAPPDATA is unavailable for explicit npm cache binding")
    npm_cache = (Path(local_app_data) / "npm-cache").resolve(strict=True)
    if not npm_cache.is_dir() or npm_cache.is_symlink():
        raise BenchmarkExecutionError("explicit npm cache path is invalid")
    npm_tarballs = {
        "yjs": (
            "https://registry.npmjs.org/yjs/-/yjs-13.6.32.tgz",
            "yjs-13.6.32.tgz",
            NPM_PACKAGES[0][2],
        ),
        "@hocuspocus/server": (
            "https://registry.npmjs.org/@hocuspocus/server/-/server-4.6.0.tgz",
            "hocuspocus-server-4.6.0.tgz",
            NPM_PACKAGES[1][2],
        ),
    }
    for package, (url, filename, integrity) in npm_tarballs.items():
        tarball = npm_downloads / filename
        if not tarball.exists():
            _run_checked(
                [
                    npm,
                    "pack",
                    "--offline",
                    "--cache",
                    str(npm_cache),
                    "--pack-destination",
                    str(npm_downloads),
                    url,
                ],
                cwd=resolved,
                timeout=300,
            )
        if not tarball.is_file() or file_sha512(tarball) != integrity:
            raise BenchmarkExecutionError(f"cached npm tarball integrity drift: {package}")
    npm_receipts: list[dict[str, Any]] = []
    node_packages = {
        "opaque_node": [str(npm_downloads / npm_tarballs["yjs"][1])],
        "hocuspocus": [
            str(npm_downloads / npm_tarballs["yjs"][1]),
            str(npm_downloads / npm_tarballs["@hocuspocus/server"][1]),
        ],
    }
    for candidate_name, packages in node_packages.items():
        candidate_root = candidates / candidate_name
        install_argv = [
            npm,
            "install",
            "--offline",
            "--cache",
            str(npm_cache),
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--save-exact",
            *packages,
        ]
        _run_checked(install_argv, cwd=candidate_root, timeout=600)
        listed = _run_checked([npm, "ls", "--all", "--json"], cwd=candidate_root)
        lock_path = candidate_root / "package-lock.json"
        lock = _strict_json(lock_path)
        if not isinstance(lock, dict) or lock.get("lockfileVersion") != 3:
            raise BenchmarkExecutionError("npm lockfile v3 missing")
        lock_packages = lock.get("packages", {})
        expected_direct_integrity = {
            f"node_modules/{package}": integrity.replace("sha512:", "sha512-")
            for package, _version, integrity in NPM_PACKAGES
            if any(Path(spec).name == npm_tarballs[package][1] for spec in packages)
        }
        if any(
            not isinstance(lock_packages.get(path), dict)
            or lock_packages[path].get("integrity") != integrity
            for path, integrity in expected_direct_integrity.items()
        ):
            raise BenchmarkExecutionError("npm direct artifact integrity drift")
        missing_integrity = sum(
            1
            for key, item in lock_packages.items()
            if key and isinstance(item, dict) and str(item.get("resolved", "")).startswith("https:")
            and not str(item.get("integrity", "")).startswith("sha512-")
        )
        if missing_integrity:
            raise BenchmarkExecutionError("npm registry lock integrity missing")
        npm_receipts.append(
            {
                "candidate_root": candidate_name,
                "package_lock_sha256": file_sha256(lock_path),
                "missing_registry_integrity_count": 0,
                "npm_ls_sha256": "sha256:" + hashlib.sha256(listed.stdout.encode("utf-8")).hexdigest(),
            }
        )

    _copy_new(ADAPTER_SOURCE_ROOT / "py_native_benchmark_adapter.py", candidates / "py_native" / "adapter.py")
    _copy_new(
        ADAPTER_SOURCE_ROOT / "py_opaque_benchmark_relay_adapter.py",
        candidates / "py_relay" / "relay_adapter.py",
    )
    _copy_new(ADAPTER_SOURCE_ROOT / "yjs_benchmark_adapter.cjs", candidates / "opaque_node" / "adapter.cjs")
    _copy_new(ADAPTER_SOURCE_ROOT / "yjs_benchmark_adapter.cjs", candidates / "hocuspocus" / "yjs_benchmark_adapter.cjs")
    _copy_new(
        ADAPTER_SOURCE_ROOT / "node_hocuspocus_benchmark_adapter.cjs",
        candidates / "hocuspocus" / "adapter.cjs",
    )
    return {
        "contract": CONTRACT,
        "python_wheel_count": len(PYTHON_WHEELS),
        "python_wheel_lock_sha256": "sha256:" + hashlib.sha256(
            canonical_bytes(
                [
                    {"file": filename, "sha256": digest}
                    for _package, _version, filename, digest in PYTHON_WHEELS
                ]
            )
        ).hexdigest(),
        "python_candidates": python_receipts,
        "npm_candidates": npm_receipts,
        "network_listener_started": False,
        "port_bound": False,
    }


def _relay_request(opaque_report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "contract": RELAY_REQUEST_CONTRACT,
        "action": "opaque_relay_suite",
        "matrix_sha256": MATRIX_SHA256,
        "corpus_sha256": CORPUS_SHA256,
        "cases": [
            {
                "case_id": item["case_id"],
                "schedule_id": item["schedule_id"],
                "wire_updates_base64": item["wire_updates_base64"],
                "wire_update_sha256": item["wire_update_sha256"],
            }
            for item in opaque_report["cases"]
        ],
    }


def _run_relay(root: Path, opaque_report: Mapping[str, Any]) -> dict[str, Any]:
    relay_root = root / "candidates" / "py_relay"
    state_dir = relay_root / f"state-{time.time_ns()}"
    state_dir.mkdir(exist_ok=False)
    completed = subprocess.run(
        [
            str(_python_executable(relay_root / "venv")),
            "-E",
            "-X",
            "utf8",
            str(relay_root / "relay_adapter.py"),
            "--state-dir",
            str(state_dir),
        ],
        cwd=relay_root,
        input=json.dumps(_relay_request(opaque_report), ensure_ascii=False, separators=(",", ":")) + "\n",
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        timeout=180,
        check=False,
        shell=False,
        env=_child_environment(),
        creationflags=_creation_flags(),
    )
    if completed.returncode != 0:
        raise BenchmarkExecutionError(f"opaque relay suite failed: {completed.stderr[-1200:]}")
    lines = completed.stdout.splitlines()
    if len(lines) != 1:
        raise BenchmarkExecutionError("opaque relay emitted an invalid report count")
    report = json.loads(lines[0])
    if (
        not isinstance(report, dict)
        or report.get("contract") != RELAY_REPORT_CONTRACT
        or report.get("candidate_id") != "PY_OPAQUE_NODE_COMPACTOR"
        or report.get("matrix_sha256") != MATRIX_SHA256
        or report.get("corpus_sha256") != CORPUS_SHA256
        or report.get("network_listener") is not False
        or report.get("port_bound") is not False
        or report.get("opaque_content_inspection") is not False
        or report.get("case_count") != len(opaque_report["cases"])
        or any(
            item.get("midstream_persist_reload_passed") is not True
            or item.get("final_restart_recovery_passed") is not True
            for item in report.get("cases", ())
        )
    ):
        raise BenchmarkExecutionError("opaque relay report drift")
    return report


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise BenchmarkExecutionError("cannot summarize empty metrics")
    ordered = sorted(map(float, values))
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _resource_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    cases = report["cases"]
    result: dict[str, Any] = {"candidate_id": report["candidate_id"], "case_count": len(cases)}
    for metric in (
        "operations_per_second",
        "snapshot_duration_ms",
        "replay_duration_ms",
        "checkpoint_state_bytes",
        "encoded_update_bytes",
        "peak_process_rss_bytes",
    ):
        values = [float(item["metrics"][metric]) for item in cases]
        result[metric] = {
            "p50": _percentile(values, 0.50),
            "p95": _percentile(values, 0.95),
            "p99": _percentile(values, 0.99),
            "max": max(values),
        }
    return result


def execute_benchmark(root: Path) -> dict[str, Any]:
    resolved = validate_run_root(root, require_marker=True)
    candidates = resolved / "candidates"
    node = shutil.which("node.exe" if os.name == "nt" else "node")
    if not node:
        raise BenchmarkExecutionError("Node runtime is unavailable")
    request = build_suite_request()
    adapters = (
        CandidateAdapter(
            candidate_id="PY_NATIVE_PYCRDT",
            adapter_id="PY_NATIVE_PYCRDT_BENCHMARK",
            argv=(
                str(_python_executable(candidates / "py_native" / "venv")),
                "-E",
                "-X",
                "utf8",
                str(candidates / "py_native" / "adapter.py"),
            ),
            cwd=candidates / "py_native",
        ),
        CandidateAdapter(
            candidate_id="PY_OPAQUE_NODE_COMPACTOR",
            adapter_id="PY_OPAQUE_NODE_COMPACTOR_YJS_BENCHMARK",
            argv=(node, str(candidates / "opaque_node" / "adapter.cjs")),
            cwd=candidates / "opaque_node",
        ),
        CandidateAdapter(
            candidate_id="NODE_YJS_HOCUSPOCUS",
            adapter_id="NODE_YJS_HOCUSPOCUS_BENCHMARK",
            argv=(node, str(candidates / "hocuspocus" / "adapter.cjs")),
            cwd=candidates / "hocuspocus",
        ),
    )
    started = time.perf_counter()
    reports = [run_adapter_suite(adapter, request) for adapter in adapters]
    opaque = next(item for item in reports if item["candidate_id"] == "PY_OPAQUE_NODE_COMPACTOR")
    relay = _run_relay(resolved, opaque)
    convergence = validate_cross_candidate_reports(reports, request)
    redacted_reports: list[dict[str, Any]] = []
    for report in reports:
        redacted = json.loads(json.dumps(report))
        for case in redacted["cases"]:
            case.pop("wire_updates_base64", None)
        redacted_reports.append(redacted)
    return {
        "contract": CONTRACT,
        "matrix_sha256": MATRIX_SHA256,
        "corpus_sha256": CORPUS_SHA256,
        "candidate_count": len(reports),
        "case_count_per_candidate": len(request["cases"]),
        "total_case_count": len(reports) * len(request["cases"]),
        "duration_seconds": round(time.perf_counter() - started, 6),
        "candidate_reports": redacted_reports,
        "opaque_relay_report": relay,
        "cross_candidate_validation": convergence,
        "resource_comparison": [_resource_summary(report) for report in reports],
        "fault_summary": {
            "duplicate_delivery_cases": sum(
                item["schedule_id"] == "DUPLICATE_EVERY_UPDATE"
                for item in request["cases"]
            )
            * len(reports),
            "out_of_order_cases": sum(
                item["schedule_id"] == "DETERMINISTIC_OUT_OF_ORDER"
                for item in request["cases"]
            )
            * len(reports),
            "restart_cases": sum(
                item["schedule_id"] == "RESTART_AFTER_HALF" for item in request["cases"]
            )
            * len(reports),
            "compaction_cases": sum(
                item["schedule_id"] == "COMPACT_THEN_TAIL_REPLAY"
                for item in request["cases"]
            )
            * len(reports),
            "network_disconnect_model": "withhold_then_replay_without_socket",
            "real_network_listener_exercised": False,
            "zero_acknowledged_loss": True,
        },
        "candidate_winner_selected": False,
        "product_dependency_pinned": False,
        "product_runtime_changed": False,
        "persistent_service_or_port_changed": False,
    }


def cleanup_owned_root(
    root: Path,
    *,
    expected_run_id: str,
    temp_base: Path | None = None,
) -> None:
    resolved = validate_run_root(root, temp_base=temp_base, require_marker=True)
    marker = load_marker(resolved)
    if marker["run_id"] != expected_run_id:
        raise BenchmarkExecutionError("cleanup run id does not match marker")
    shutil.rmtree(resolved)
    if resolved.exists():
        raise BenchmarkExecutionError("benchmark root cleanup failed")


__all__ = [
    "APPROVAL_CONTRACT",
    "APPROVAL_ID",
    "BenchmarkExecutionError",
    "CONTRACT",
    "NPM_PACKAGES",
    "PYTHON_WHEELS",
    "ROOT_MARKER_NAME",
    "approval_request_descriptor",
    "cleanup_owned_root",
    "execute_benchmark",
    "file_sha256",
    "file_sha512",
    "initialize_root",
    "load_marker",
    "materialize_candidates",
    "scope_descriptor",
    "scope_sha256",
    "validate_approval_receipt",
    "validate_run_root",
]

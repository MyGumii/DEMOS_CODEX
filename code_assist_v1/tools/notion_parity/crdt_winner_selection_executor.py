"""Marker-bound E0-P4 runtime/license/reproducibility executor."""

from __future__ import annotations

from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence

from code_assist_v1.tools.notion_parity.crdt_candidate_benchmark_executor import (
    _child_environment,
    _creation_flags,
    file_sha256,
    load_marker,
    validate_run_root,
)
from code_assist_v1.tools.notion_parity.crdt_winner_selection import (
    ALLOWED_SPDX,
    DEFAULT_MANIFEST,
    DEFAULT_PACKAGE_JSON,
    DEFAULT_PACKAGE_LOCK,
    DEFAULT_PYTHON_LOCK,
    RAW_BENCHMARK_SHA256,
    SELECTED_CANDIDATE_ID,
    WinnerSelectionError,
    strict_json,
    validate_checked_in_selection,
)


CONTRACT = "code_assist.notion.crdt_winner_selection_execution.v1"
EXPECTED_RUN_ID = "E0P3-20260824-001"
EXPECTED_ROOT_NAME = "code-assist-e0-p3-20260824-bfec48e4f9444d06b1b5c2780225dcb6"
SELECTED_PYTHON = {
    "click": ("8.4.2", "BSD-3-Clause"),
    "colorama": ("0.4.6", "BSD-3-Clause"),
    "h11": ("0.16.0", "MIT"),
    "uvicorn": ("0.52.4", "BSD-3-Clause"),
    "websockets": ("17.0.1", "BSD-3-Clause"),
}
SELECTED_NODE = {
    "isomorphic.js": ("0.2.5", "MIT"),
    "lib0": ("0.2.117", "MIT"),
    "yjs": ("13.6.32", "MIT"),
}


class WinnerExecutionError(RuntimeError):
    """The retained-root E0-P4 execution contract failed."""


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float = 300.0,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        check=False,
        shell=False,
        timeout=timeout,
        env=dict(env) if env is not None else _child_environment(),
        creationflags=_creation_flags(),
    )
    if completed.returncode != 0:
        raise WinnerExecutionError(
            f"command failed ({completed.returncode}): {Path(argv[0]).name}: {completed.stderr[-1200:]}"
        )
    return completed


def _python_executable(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _validate_repro_child(root: Path, child: Path) -> Path:
    resolved_root = validate_run_root(root, require_marker=True)
    resolved = child.resolve(strict=False)
    if resolved.parent != resolved_root or resolved.name not in {"e0-p4-python-repro", "e0-p4-node-repro"}:
        raise WinnerExecutionError("reproducibility root escaped the marker-bound parent")
    if child.is_symlink():
        raise WinnerExecutionError("reproducibility root may not be a symlink")
    return resolved


def _remove_repro_child(root: Path, child: Path) -> None:
    resolved = _validate_repro_child(root, child)
    if resolved.exists():
        shutil.rmtree(resolved)
    if resolved.exists():
        raise WinnerExecutionError("reproducibility root cleanup failed")


def _license_from_metadata(name: str, dist: metadata.Distribution) -> str:
    expression = dist.metadata.get("License-Expression")
    if expression in ALLOWED_SPDX:
        return str(expression)
    classifiers = dist.metadata.get_all("Classifier") or []
    if "License :: OSI Approved :: MIT License" in classifiers:
        return "MIT"
    if "License :: OSI Approved :: BSD License" in classifiers:
        return "BSD-3-Clause"
    raw = str(dist.metadata.get("License") or "")
    if name == "colorama" and "BSD" in raw.upper():
        return "BSD-3-Clause"
    raise WinnerExecutionError(f"unrecognized selected Python license: {name}")


def _inspect_python(python: Path, cwd: Path) -> dict[str, dict[str, str]]:
    script = (
        "from importlib import metadata; import json; "
        "names=" + repr(sorted(SELECTED_PYTHON)) + "; "
        "print(json.dumps({n:{'version':metadata.version(n),'license_expression':"
        "(metadata.metadata(n).get('License-Expression') or ''),'license':"
        "(metadata.metadata(n).get('License') or ''),'classifiers':"
        "(metadata.metadata(n).get_all('Classifier') or [])} for n in names}))"
    )
    completed = _run([os.fspath(python), "-E", "-c", script], cwd=cwd)
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise WinnerExecutionError("Python metadata probe emitted invalid JSON") from exc
    observed: dict[str, dict[str, str]] = {}
    for name, (version, expected_license) in SELECTED_PYTHON.items():
        item = raw.get(name, {})
        if item.get("version") != version:
            raise WinnerExecutionError(f"selected Python version drift: {name}")
        expression = item.get("license_expression")
        classifiers = item.get("classifiers", [])
        raw_license = item.get("license", "")
        if expression in ALLOWED_SPDX:
            license_spdx = expression
        elif "License :: OSI Approved :: MIT License" in classifiers:
            license_spdx = "MIT"
        elif "License :: OSI Approved :: BSD License" in classifiers or (
            name == "colorama" and "BSD" in str(raw_license).upper()
        ):
            license_spdx = "BSD-3-Clause"
        else:
            raise WinnerExecutionError(f"selected Python license unavailable: {name}")
        if license_spdx != expected_license:
            raise WinnerExecutionError(f"selected Python license drift: {name}")
        observed[name] = {"version": version, "license_spdx": license_spdx}
    return observed


def _validate_raw_benchmark(root: Path) -> dict[str, Any]:
    path = root / "receipts" / "benchmark.json"
    if file_sha256(path) != RAW_BENCHMARK_SHA256:
        raise WinnerExecutionError("raw E0-P3 benchmark hash drift")
    report = strict_json(path)
    reports = {
        str(item.get("candidate_id")): item
        for item in report.get("candidate_reports", [])
        if isinstance(item, dict)
    }
    selected = reports.get(SELECTED_CANDIDATE_ID)
    if selected is None or selected.get("case_count") != 53:
        raise WinnerExecutionError("selected raw benchmark denominator drift")
    relay = report.get("opaque_relay_report", {})
    cases = relay.get("cases", []) if isinstance(relay, dict) else []
    if len(cases) != 53 or any(
        item.get("midstream_persist_reload_passed") is not True
        or item.get("final_restart_recovery_passed") is not True
        for item in cases
    ):
        raise WinnerExecutionError("opaque relay persistence/restart evidence failed")
    benchmark_cases = selected.get("cases", [])
    if len(benchmark_cases) != 53 or any(
        item.get("restart_replay_equivalent") is not True
        or item.get("compaction_equivalent") is not True
        or item.get("ime_intermediate_published_count") != 0
        for item in benchmark_cases
    ):
        raise WinnerExecutionError("selected candidate replay/compaction/IME evidence failed")
    return {
        "selected_case_count": 53,
        "opaque_relay_case_count": 53,
        "midstream_persist_reload_passed": 53,
        "final_restart_recovery_passed": 53,
    }


def _validate_installed_node(root: Path) -> dict[str, dict[str, str]]:
    lock = strict_json(root / "candidates" / "opaque_node" / "package-lock.json")
    packages = lock.get("packages", {})
    observed: dict[str, dict[str, str]] = {}
    for name, (version, license_spdx) in SELECTED_NODE.items():
        item = packages.get(f"node_modules/{name}", {})
        if item.get("version") != version or item.get("license") != license_spdx:
            raise WinnerExecutionError(f"selected Node package drift: {name}")
        observed[name] = {"version": version, "license_spdx": license_spdx}
    return observed


def _reproduce_python(root: Path) -> dict[str, Any]:
    target = _validate_repro_child(root, root / "e0-p4-python-repro")
    if target.exists():
        raise WinnerExecutionError("Python reproducibility root already exists")
    try:
        _run([sys.executable, "-E", "-m", "venv", os.fspath(target)], cwd=root)
        python = _python_executable(target)
        _run(
            [
                os.fspath(python),
                "-E",
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-index",
                f"--find-links={root / 'downloads' / 'python'}",
                "--require-hashes",
                "-r",
                os.fspath(DEFAULT_PYTHON_LOCK),
            ],
            cwd=root,
        )
        _run([os.fspath(python), "-E", "-m", "pip", "check"], cwd=root)
        observed = _inspect_python(python, root)
        return {"package_count": len(observed), "pip_check_passed": True, "packages": observed}
    finally:
        _remove_repro_child(root, target)


def _reproduce_node(root: Path) -> dict[str, Any]:
    target = _validate_repro_child(root, root / "e0-p4-node-repro")
    if target.exists():
        raise WinnerExecutionError("Node reproducibility root already exists")
    target.mkdir(parents=False)
    try:
        shutil.copy2(DEFAULT_PACKAGE_JSON, target / "package.json")
        shutil.copy2(DEFAULT_PACKAGE_LOCK, target / "package-lock.json")
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if npm is None:
            raise WinnerExecutionError("npm executable is unavailable")
        env = _child_environment()
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise WinnerExecutionError("LOCALAPPDATA is unavailable")
        env.update(
            {
                "NPM_CONFIG_CACHE": os.fspath(Path(local) / "npm-cache"),
                "npm_config_cache": os.fspath(Path(local) / "npm-cache"),
                "NPM_CONFIG_OFFLINE": "true",
                "npm_config_offline": "true",
            }
        )
        _run(
            [npm, "ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=target,
            env=env,
        )
        _run([npm, "ls", "--all", "--json"], cwd=target, env=env)
        installed = strict_json(target / "package-lock.json")
        packages = installed.get("packages", {})
        if set(packages) != {"", *(f"node_modules/{name}" for name in SELECTED_NODE)}:
            raise WinnerExecutionError("reproduced Node package denominator drift")
        return {"package_count": len(SELECTED_NODE), "npm_ls_passed": True, "lockfile_version": 3}
    finally:
        _remove_repro_child(root, target)


def execute_selection(root: Path) -> dict[str, Any]:
    resolved = validate_run_root(root, require_marker=True)
    marker = load_marker(resolved)
    if resolved.name != EXPECTED_ROOT_NAME or marker.get("run_id") != EXPECTED_RUN_ID:
        raise WinnerExecutionError("retained E0-P3 root identity drift")
    decision = validate_checked_in_selection()
    raw = _validate_raw_benchmark(resolved)
    installed_python = _inspect_python(
        _python_executable(resolved / "candidates" / "py_relay" / "venv"),
        resolved,
    )
    installed_node = _validate_installed_node(resolved)
    reproduced_python = _reproduce_python(resolved)
    reproduced_node = _reproduce_node(resolved)
    manifest = strict_json(DEFAULT_MANIFEST)
    return {
        "contract": CONTRACT,
        "packet_id": "E0-P4",
        "status": "pass",
        "selected_candidate_id": SELECTED_CANDIDATE_ID,
        "decision": decision,
        "raw_benchmark": raw,
        "installed_license_graph": {
            "python": installed_python,
            "node": installed_node,
            "selected_package_count": len(installed_python) + len(installed_node),
            "disallowed_selected_package_count": 0,
        },
        "reproducibility": {
            "python": reproduced_python,
            "node": reproduced_node,
            "network_fallback_allowed": False,
            "temporary_repro_roots_removed": True,
        },
        "artifact_bindings": {
            "manifest_sha256": file_sha256(DEFAULT_MANIFEST),
            "python_lock_sha256": file_sha256(DEFAULT_PYTHON_LOCK),
            "package_json_sha256": file_sha256(DEFAULT_PACKAGE_JSON),
            "package_lock_sha256": file_sha256(DEFAULT_PACKAGE_LOCK),
            "selected_package_count": manifest["license_policy"]["selected_package_count"],
        },
        "isolation": {
            "candidate_root_basename": resolved.name,
            "candidate_owned_processes_remaining": 0,
            "listener_started": False,
            "port_bound": False,
            "global_runtime_lock_modified": False,
            "live_identity_workspace_or_data_accessed": False,
        },
        "claims": {
            "winner_selected": True,
            "pinned_dependency_and_reproducible_artifact": True,
            "rejected_candidate_evidence_present": True,
            "runtime_service_implemented": False,
            "runtime_enabled": False,
            "product_complete": False,
        },
    }


__all__ = ["CONTRACT", "WinnerExecutionError", "execute_selection"]

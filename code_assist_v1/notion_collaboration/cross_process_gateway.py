"""Owned, disposable launcher for the selected cross-process collaboration runtime.

The launcher installs the exact Python and Node dependency locks only below a
caller-owned temporary directory.  It never selects a fixed operator port and
removes only a marker-bound runtime root that it created itself.
"""

from __future__ import annotations

import ctypes
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Any, Mapping
from urllib.request import Request, urlopen
from uuid import uuid4

from .gateway_service import (
    CROSS_PROCESS_SERVICE_CONTRACT,
    FORBIDDEN_OPERATOR_PORTS,
    LOOPBACK_HOST,
)
from .selected_runtime_contract import runtime_binding_sha256


CROSS_PROCESS_LAUNCHER_CONTRACT = "code_assist.notion.t0b_p3.cross_process_launcher.v1"
EXPECTED_PYTHON_PACKAGES = {"uvicorn": "0.52.4", "websockets": "17.0.1"}
EXPECTED_NODE_VERSION = "24.14.1"
EXPECTED_NPM_VERSION = "11.11.0"
EXPECTED_YJS_VERSION = "13.6.32"


class CrossProcessGatewayError(RuntimeError):
    pass


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _exclusive_json(path: Path, value: Any) -> None:
    with path.open("xb") as handle:
        handle.write(_canonical_json(value))
        handle.flush()
        os.fsync(handle.fileno())


def _root_locator(path: Path) -> str:
    import hashlib

    normalized = os.path.normcase(str(path.resolve(strict=False))).encode("utf-8")
    return "sha256:" + hashlib.sha256(normalized).hexdigest()


def _is_reparse(path: Path) -> bool:
    status = path.lstat()
    return path.is_symlink() or bool(getattr(status, "st_file_attributes", 0) & 0x400)


def _port_accepting(port: int) -> bool:
    try:
        with socket.create_connection((LOOPBACK_HOST, port), timeout=0.2):
            return True
    except OSError:
        return False


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _minimal_child_environment() -> dict[str, str]:
    names = (
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    )
    environment = {name: os.environ[name] for name in names if name in os.environ}
    environment.update({"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    return environment


def _dependency_install_environment() -> dict[str, str]:
    environment = _minimal_child_environment()
    for name in ("APPDATA", "LOCALAPPDATA"):
        if name in os.environ:
            environment[name] = os.environ[name]
    return environment


class CrossProcessGatewayRuntime:
    """Provision, start, restart, and exactly clean one disposable runtime."""

    def __init__(
        self,
        runtime_root: Path,
        *,
        allowed_parent: Path,
        catalog_path: Path,
        user_data_root: Path,
        timeout_seconds: float = 30.0,
        max_queue_frames: int = 64,
        max_connections: int = 1_024,
        reconnect_attempts: int = 5,
        operator_fixed_port: int | None = None,
        operator_approval_id: str | None = None,
    ) -> None:
        self.runtime_root_input = Path(runtime_root)
        self.allowed_parent_input = Path(allowed_parent)
        self.catalog_path = Path(catalog_path).resolve(strict=True)
        self.user_data_root = Path(user_data_root).resolve(strict=True)
        self.timeout = float(timeout_seconds)
        self.max_queue_frames = int(max_queue_frames)
        self.max_connections = int(max_connections)
        self.reconnect_attempts = int(reconnect_attempts)
        self.operator_fixed_port = operator_fixed_port
        self.operator_approval_id = operator_approval_id
        if (
            self.timeout <= 0
            or self.max_queue_frames < 1
            or self.max_connections < 1
            or self.reconnect_attempts < 1
        ):
            raise CrossProcessGatewayError("CROSS_PROCESS_CONFIGURATION_INVALID")
        if (operator_fixed_port, operator_approval_id) == (None, None):
            pass
        elif operator_fixed_port != 10113 or operator_approval_id != "AP-DEV-PAIR":
            raise CrossProcessGatewayError("OPERATOR_FIXED_PORT_APPROVAL_INVALID")
        self.launch_id = uuid4().hex
        self.generation = 0
        self.runtime_root: Path | None = None
        self.python_packages: Path | None = None
        self.node_runtime: Path | None = None
        self.node_executable: Path | None = None
        self.process: subprocess.Popen[str] | None = None
        self.descriptor: dict[str, Any] | None = None
        self._stdout_handle: Any = None
        self._stderr_handle: Any = None
        self._provisioned = False
        self._closed = False

    @property
    def repository_parent(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @property
    def source_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _prepare_root(self) -> None:
        if not self.runtime_root_input.is_absolute() or not self.allowed_parent_input.is_absolute():
            raise CrossProcessGatewayError("ABSOLUTE_PATH_REQUIRED")
        parent = self.allowed_parent_input.resolve(strict=True)
        candidate = self.runtime_root_input.resolve(strict=False)
        if candidate.parent != parent or candidate == parent or candidate.exists() or _is_reparse(parent):
            raise CrossProcessGatewayError("RUNTIME_ROOT_INVALID")
        os.mkdir(candidate)
        if _is_reparse(candidate):
            raise CrossProcessGatewayError("RUNTIME_ROOT_REPARSE_FORBIDDEN")
        self.runtime_root = candidate
        _exclusive_json(
            candidate / "marker.json",
            {
                "contract": CROSS_PROCESS_LAUNCHER_CONTRACT,
                "launch_id": self.launch_id,
                "root_locator_sha256": _root_locator(candidate),
            },
        )

    @staticmethod
    def _run_checked(
        command: list[str],
        *,
        cwd: Path,
        timeout: float,
        dependency_install: bool = False,
    ) -> str:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=_dependency_install_environment() if dependency_install else _minimal_child_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise CrossProcessGatewayError("RUNTIME_DEPENDENCY_PROVISION_FAILED")
        return result.stdout.strip()

    def provision(self) -> dict[str, Any]:
        if self._provisioned:
            raise CrossProcessGatewayError("RUNTIME_ALREADY_PROVISIONED")
        self._prepare_root()
        assert self.runtime_root is not None
        self.python_packages = self.runtime_root / "python-packages"
        self.node_runtime = self.runtime_root / "node-runtime"
        self.python_packages.mkdir()
        self.node_runtime.mkdir()
        source_node = self.source_root / "collaboration_runtime" / "node"
        for name in ("package.json", "package-lock.json", "compactor.mjs"):
            shutil.copy2(source_node / name, self.node_runtime / name)

        python_lock = self.source_root / "collaboration_runtime" / "python-requirements.lock"
        self._run_checked(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-compile",
                "--no-warn-script-location",
                "--require-hashes",
                "--target",
                str(self.python_packages),
                "-r",
                str(python_lock),
            ],
            cwd=self.source_root,
            timeout=max(self.timeout, 120.0),
            dependency_install=True,
        )
        observed_python = {
            distribution.metadata["Name"].lower(): distribution.version
            for distribution in metadata.distributions(path=[str(self.python_packages)])
            if distribution.metadata.get("Name")
        }
        if any(observed_python.get(name) != version for name, version in EXPECTED_PYTHON_PACKAGES.items()):
            raise CrossProcessGatewayError("PYTHON_RUNTIME_VERSION_MISMATCH")

        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        node = shutil.which("node.exe" if os.name == "nt" else "node")
        if npm is None or node is None:
            raise CrossProcessGatewayError("NODE_RUNTIME_UNAVAILABLE")
        self.node_executable = Path(node).resolve(strict=True)
        node_version = self._run_checked([str(self.node_executable), "--version"], cwd=self.node_runtime, timeout=self.timeout)
        npm_version = self._run_checked([npm, "--version"], cwd=self.node_runtime, timeout=self.timeout)
        if node_version != f"v{EXPECTED_NODE_VERSION}" or npm_version != EXPECTED_NPM_VERSION:
            raise CrossProcessGatewayError("NODE_RUNTIME_VERSION_MISMATCH")
        self._run_checked(
            [npm, "ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=self.node_runtime,
            timeout=max(self.timeout, 120.0),
            dependency_install=True,
        )
        yjs_package = json.loads((self.node_runtime / "node_modules" / "yjs" / "package.json").read_text(encoding="utf-8"))
        if yjs_package.get("version") != EXPECTED_YJS_VERSION:
            raise CrossProcessGatewayError("YJS_RUNTIME_VERSION_MISMATCH")
        provision = {
            "contract": CROSS_PROCESS_LAUNCHER_CONTRACT,
            "status": "provisioned",
            "runtime_binding_sha256": runtime_binding_sha256(),
            "python_packages": EXPECTED_PYTHON_PACKAGES,
            "node_version": EXPECTED_NODE_VERSION,
            "npm_version": EXPECTED_NPM_VERSION,
            "yjs_version": EXPECTED_YJS_VERSION,
            "temporary_install_only": True,
        }
        _exclusive_json(self.runtime_root / "provision.json", provision)
        self._provisioned = True
        return provision

    def _bootstrap_command(self, descriptor_path: Path, instance_id: str) -> list[str]:
        assert self.runtime_root is not None
        assert self.python_packages is not None
        assert self.node_runtime is not None
        assert self.node_executable is not None
        base_executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)
        bootstrap = (
            "import runpy,sys;"
            "sys.path[:0]=sys.argv[1:3];"
            "sys.argv=['gateway_service']+sys.argv[3:];"
            "runpy.run_module('code_assist_v1.notion_collaboration.gateway_service',run_name='__main__')"
        )
        command = [
            str(base_executable),
            "-E",
            "-s",
            "-c",
            bootstrap,
            str(self.repository_parent),
            str(self.python_packages),
            "--serve-child",
            "--catalog",
            str(self.catalog_path),
            "--user-data-root",
            str(self.user_data_root),
            "--runtime-root",
            str(self.runtime_root),
            "--descriptor",
            str(descriptor_path),
            "--launch-id",
            self.launch_id,
            "--instance-id",
            instance_id,
            "--node-executable",
            str(self.node_executable),
            "--node-runtime-root",
            str(self.node_runtime),
            "--node-entrypoint",
            str(self.node_runtime / "compactor.mjs"),
            "--max-queue-frames",
            str(self.max_queue_frames),
            "--max-connections",
            str(self.max_connections),
            "--reconnect-attempts",
            str(self.reconnect_attempts),
        ]
        if self.operator_fixed_port is not None:
            command.extend(
                [
                    "--operator-fixed-port",
                    str(self.operator_fixed_port),
                    "--operator-approval-id",
                    str(self.operator_approval_id),
                ]
            )
        return command

    def start(self) -> dict[str, Any]:
        if not self._provisioned:
            self.provision()
        if self.process is not None and self.process.poll() is None:
            raise CrossProcessGatewayError("RUNTIME_ALREADY_RUNNING")
        assert self.runtime_root is not None
        self.generation += 1
        instance_id = f"{self.launch_id}-{self.generation}-{uuid4().hex}"
        descriptor_path = self.runtime_root / f"descriptor-{self.generation}.json"
        self._stdout_handle = (self.runtime_root / f"gateway-{self.generation}.stdout.log").open("xb")
        self._stderr_handle = (self.runtime_root / f"gateway-{self.generation}.stderr.log").open("xb")
        self.process = subprocess.Popen(
            self._bootstrap_command(descriptor_path, instance_id),
            cwd=self.repository_parent,
            env=_minimal_child_environment(),
            stdin=subprocess.PIPE,
            stdout=self._stdout_handle,
            stderr=self._stderr_handle,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            close_fds=True,
        )
        deadline = time.monotonic() + self.timeout
        try:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise CrossProcessGatewayError("GATEWAY_EXITED_BEFORE_READY")
                if descriptor_path.is_file():
                    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
                    required = {
                        "contract",
                        "launch_id",
                        "instance_id",
                        "gateway_pid",
                        "compactor_pid",
                        "port",
                        "os_assigned_port",
                        "runtime_binding_sha256",
                        "runtime_versions",
                        "startup_attestation",
                    }
                    if set(descriptor) != required:
                        raise CrossProcessGatewayError("GATEWAY_DESCRIPTOR_INVALID")
                    port = int(descriptor["port"])
                    expected_os_assigned = self.operator_fixed_port is None
                    if (
                        descriptor["contract"] != CROSS_PROCESS_SERVICE_CONTRACT
                        or descriptor["launch_id"] != self.launch_id
                        or descriptor["instance_id"] != instance_id
                        or int(descriptor["gateway_pid"]) != self.process.pid
                        or int(descriptor["compactor_pid"]) == self.process.pid
                        or (
                            self.operator_fixed_port is None
                            and port in FORBIDDEN_OPERATOR_PORTS
                        )
                        or (
                            self.operator_fixed_port is not None
                            and port != self.operator_fixed_port
                        )
                        or descriptor["os_assigned_port"] is not expected_os_assigned
                        or descriptor["runtime_binding_sha256"] != runtime_binding_sha256()
                    ):
                        raise CrossProcessGatewayError("GATEWAY_DESCRIPTOR_BINDING_INVALID")
                    with urlopen(Request(f"http://{LOOPBACK_HOST}:{port}/health"), timeout=1) as response:
                        health = json.loads(response.read().decode("utf-8"))
                    if health.get("state") != "READY" or health.get("ready_for_admission") is not True:
                        raise CrossProcessGatewayError("GATEWAY_HEALTH_INVALID")
                    self.descriptor = descriptor
                    return dict(descriptor)
                time.sleep(0.02)
            raise CrossProcessGatewayError("GATEWAY_READINESS_TIMEOUT")
        except BaseException:
            self.stop(force=True)
            raise

    @property
    def port(self) -> int:
        if self.descriptor is None:
            raise CrossProcessGatewayError("RUNTIME_NOT_READY")
        return int(self.descriptor["port"])

    @property
    def origin(self) -> str:
        return f"http://{LOOPBACK_HOST}:{self.port}"

    @property
    def websocket_url(self) -> str:
        return f"ws://{LOOPBACK_HOST}:{self.port}/realtime"

    def health(self) -> dict[str, Any]:
        with urlopen(Request(f"{self.origin}/health"), timeout=1) as response:
            return json.loads(response.read().decode("utf-8"))

    def stop(self, *, force: bool = False) -> dict[str, bool]:
        process = self.process
        descriptor = self.descriptor
        if process is None:
            return {"gateway_stopped": True, "compactor_stopped": True, "port_closed": True}
        if process.poll() is None and not force and process.stdin is not None and descriptor is not None:
            try:
                process.stdin.write(f"STOP {descriptor['instance_id']}\n")
                process.stdin.flush()
                process.wait(timeout=self.timeout)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                force = True
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=self.timeout)
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        for handle_name in ("_stdout_handle", "_stderr_handle"):
            handle = getattr(self, handle_name)
            if handle is not None:
                handle.close()
                setattr(self, handle_name, None)
        deadline = time.monotonic() + self.timeout
        port = None if descriptor is None else int(descriptor["port"])
        compactor_pid = None if descriptor is None else int(descriptor["compactor_pid"])
        while time.monotonic() < deadline and (
            (port is not None and _port_accepting(port))
            or (compactor_pid is not None and _pid_running(compactor_pid))
        ):
            time.sleep(0.02)
        result = {
            "gateway_stopped": process.poll() is not None,
            "compactor_stopped": compactor_pid is None or not _pid_running(compactor_pid),
            "port_closed": port is None or not _port_accepting(port),
        }
        self.process = None
        self.descriptor = None
        if not all(result.values()):
            raise CrossProcessGatewayError("OWNED_PROCESS_CLEANUP_FAILED")
        return result

    def restart(self) -> dict[str, Any]:
        self.stop()
        return self.start()

    def _marker_matches(self) -> bool:
        if self.runtime_root is None or not self.runtime_root.is_dir() or _is_reparse(self.runtime_root):
            return False
        try:
            marker = json.loads((self.runtime_root / "marker.json").read_text(encoding="utf-8"))
            parent = self.allowed_parent_input.resolve(strict=True)
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        return self.runtime_root.resolve(strict=True).parent == parent and marker == {
            "contract": CROSS_PROCESS_LAUNCHER_CONTRACT,
            "launch_id": self.launch_id,
            "root_locator_sha256": _root_locator(self.runtime_root),
        }

    def close(self) -> dict[str, bool]:
        if self._closed:
            return {"owned_processes_stopped": True, "owned_ports_closed": True, "runtime_root_removed": self.runtime_root is None or not self.runtime_root.exists()}
        stopped = self.stop()
        root_removed = self.runtime_root is None or not self.runtime_root.exists()
        if self.runtime_root is not None and self.runtime_root.exists():
            if not self._marker_matches():
                raise CrossProcessGatewayError("RUNTIME_MARKER_MISMATCH")
            shutil.rmtree(self.runtime_root)
            root_removed = not self.runtime_root.exists()
        self._closed = True
        result = {
            "owned_processes_stopped": stopped["gateway_stopped"] and stopped["compactor_stopped"],
            "owned_ports_closed": stopped["port_closed"],
            "runtime_root_removed": root_removed,
        }
        if not all(result.values()):
            raise CrossProcessGatewayError("OWNED_RUNTIME_CLEANUP_FAILED")
        return result

    def __enter__(self) -> "CrossProcessGatewayRuntime":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


__all__ = [
    "CROSS_PROCESS_LAUNCHER_CONTRACT",
    "CrossProcessGatewayError",
    "CrossProcessGatewayRuntime",
    "EXPECTED_NODE_VERSION",
    "EXPECTED_NPM_VERSION",
    "EXPECTED_PYTHON_PACKAGES",
    "EXPECTED_YJS_VERSION",
]

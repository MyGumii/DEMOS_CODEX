"""Isolated two-process runtime for the synthetic Agent/Notion host pair.

The launcher accepts only a verified synthetic fixture root and a new sibling
runtime root.  Children bind loopback port ``0`` (OS assignment), share the
fixture session authority, publish PID-bound readiness descriptors, and are
cleaned through the exact ``Popen`` handles owned by the launcher.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import site
import socket
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from flask import Flask, jsonify, request, session
from werkzeug.security import check_password_hash
from werkzeug.serving import make_server

from .pair_config import (
    CODE_ORIGIN_KEY,
    NOTION_ORIGIN_KEY,
    PAIR_COOKIE_NAME_KEY,
    PAIR_ENABLED_KEY,
    PAIR_ENVIRONMENT,
    PAIR_ENVIRONMENT_KEY,
    PAIR_SECRET_KEY,
    PairConfiguration,
    PairHost,
    load_pair_configuration,
)
from .synthetic_pair_root import SECRET_RELATIVE, USERS_RELATIVE, verify_synthetic_pair_root


RUNTIME_MARKER_NAME = ".code-assist-synthetic-pair-runtime.json"
FORBIDDEN_OPERATOR_PORTS = frozenset({10011, 10012, 10111, 10112, 10113})
LOOPBACK_HOST = "127.0.0.1"
REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
HOST_ORDER = (PairHost.CODE, PairHost.NOTION)


class DevPairRuntimeError(RuntimeError):
    """Fail-closed synthetic pair launch or cleanup error."""


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite number: {value}")),
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_json_exclusive(path: Path, value: Any) -> None:
    with path.open("xb") as output:
        output.write(_canonical_json_bytes(value))
        output.flush()
        os.fsync(output.fileno())


def _write_json_atomic_create(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        _write_json_exclusive(temporary, value)
        if path.exists():
            raise FileExistsError(path)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _root_locator_sha256(path: Path) -> str:
    return _sha256_bytes(os.path.normcase(str(path.resolve())).encode("utf-8"))


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise DevPairRuntimeError("RUNTIME_PATH_UNREADABLE") from exc
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & REPARSE_FLAG)


def _validate_roots(synthetic_root: Path, runtime_root: Path, allowed_parent: Path) -> tuple[Path, Path, Path]:
    if not all(path.is_absolute() for path in (synthetic_root, runtime_root, allowed_parent)):
        raise DevPairRuntimeError("ABSOLUTE_PATH_REQUIRED")
    parent = allowed_parent.resolve(strict=True)
    if not parent.is_dir() or _is_reparse(parent):
        raise DevPairRuntimeError("PARENT_REPARSE_OR_INVALID")
    fixture = synthetic_root.resolve(strict=True)
    candidate = runtime_root.resolve(strict=False)
    if fixture.parent != parent or candidate.parent != parent:
        raise DevPairRuntimeError("ROOT_PARENT_MISMATCH")
    if candidate.exists():
        raise DevPairRuntimeError("RUNTIME_ROOT_ALREADY_EXISTS")
    if candidate in {fixture, parent} or candidate.name in {"", ".", ".."}:
        raise DevPairRuntimeError("RUNTIME_ROOT_INVALID")
    verify_synthetic_pair_root(fixture)
    return fixture, candidate, parent


def _cookie_name(root_id: str) -> str:
    return "ca_pair_" + hashlib.sha256(root_id.encode("utf-8")).hexdigest()[:20]


def _sanitized_child_environment() -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"}
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result["PYTHONUTF8"] = "1"
    result["PYTHONUNBUFFERED"] = "1"
    return result


def _port_accepting(port: int) -> bool:
    try:
        with socket.create_connection((LOOPBACK_HOST, port), timeout=0.2):
            return True
    except OSError:
        return False


def _wait_port_closed(port: int, deadline: float) -> bool:
    while time.monotonic() < deadline:
        if not _port_accepting(port):
            return True
        time.sleep(0.02)
    return not _port_accepting(port)


@dataclass(frozen=True, slots=True)
class HostDescriptor:
    role: PairHost
    pid: int
    port: int
    origin: str
    launch_id: str
    root_id: str

    @classmethod
    def from_document(cls, value: Any) -> "HostDescriptor":
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "contract",
            "role",
            "pid",
            "port",
            "origin",
            "launch_id",
            "root_id",
        }:
            raise DevPairRuntimeError("DESCRIPTOR_SHAPE_INVALID")
        if value.get("schema_version") != 1 or value.get("contract") != "code_assist.notion.synthetic_pair_host.v1":
            raise DevPairRuntimeError("DESCRIPTOR_CONTRACT_INVALID")
        try:
            role = PairHost(value["role"])
        except (TypeError, ValueError) as exc:
            raise DevPairRuntimeError("DESCRIPTOR_ROLE_INVALID") from exc
        pid = value.get("pid")
        port = value.get("port")
        if type(pid) is not int or pid <= 0 or type(port) is not int or not (1 <= port <= 65535):
            raise DevPairRuntimeError("DESCRIPTOR_PROCESS_INVALID")
        expected_origin = f"http://{LOOPBACK_HOST}:{port}"
        if value.get("origin") != expected_origin:
            raise DevPairRuntimeError("DESCRIPTOR_ORIGIN_INVALID")
        return cls(role, pid, port, expected_origin, str(value.get("launch_id", "")), str(value.get("root_id", "")))

    def public_document(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "pid": self.pid,
            "port": self.port,
            "origin": self.origin,
        }


class SyntheticPairRuntime:
    """Own one disposable pair and expose redacted launch/cleanup evidence."""

    def __init__(
        self,
        synthetic_root: Path,
        runtime_root: Path,
        *,
        allowed_parent: Path,
        startup_timeout_seconds: float = 15.0,
        shutdown_timeout_seconds: float = 5.0,
        fault_before_ready_role: PairHost | None = None,
    ) -> None:
        if startup_timeout_seconds <= 0 or shutdown_timeout_seconds <= 0:
            raise DevPairRuntimeError("TIMEOUT_INVALID")
        self._synthetic_root_input = Path(synthetic_root)
        self._runtime_root_input = Path(runtime_root)
        self._allowed_parent_input = Path(allowed_parent)
        self._startup_timeout = float(startup_timeout_seconds)
        self._shutdown_timeout = float(shutdown_timeout_seconds)
        if fault_before_ready_role is not None and not isinstance(fault_before_ready_role, PairHost):
            raise DevPairRuntimeError("FAULT_ROLE_INVALID")
        self._fault_before_ready_role = fault_before_ready_role
        self._synthetic_root: Path | None = None
        self._runtime_root: Path | None = None
        self._allowed_parent: Path | None = None
        self._launch_id = str(uuid4())
        self._fixture_receipt: dict[str, Any] | None = None
        self._configuration: PairConfiguration | None = None
        self._descriptors: dict[PairHost, HostDescriptor] = {}
        self._processes: dict[PairHost, subprocess.Popen[bytes]] = {}
        self._started = False
        self._closed = False
        self._cleanup: dict[str, Any] = {
            "owned_processes_stopped": False,
            "owned_ports_closed": False,
            "runtime_root_removed": False,
        }

    @property
    def configuration(self) -> PairConfiguration:
        if self._configuration is None:
            raise DevPairRuntimeError("PAIR_NOT_STARTED")
        return self._configuration

    @property
    def origins(self) -> Mapping[PairHost, str]:
        if not self._started:
            raise DevPairRuntimeError("PAIR_NOT_STARTED")
        return {role: descriptor.origin for role, descriptor in self._descriptors.items()}

    @property
    def process_ids(self) -> tuple[int, ...]:
        return tuple(self._descriptors[role].pid for role in HOST_ORDER if role in self._descriptors)

    @property
    def ports(self) -> tuple[int, ...]:
        return tuple(self._descriptors[role].port for role in HOST_ORDER if role in self._descriptors)

    def _create_runtime_root(self) -> None:
        fixture, runtime, parent = _validate_roots(
            self._synthetic_root_input,
            self._runtime_root_input,
            self._allowed_parent_input,
        )
        fixture_receipt = verify_synthetic_pair_root(fixture)
        os.mkdir(runtime)
        if _is_reparse(runtime):
            raise DevPairRuntimeError("RUNTIME_ROOT_REPARSE_FORBIDDEN")
        marker = {
            "schema_version": 1,
            "contract": "code_assist.notion.synthetic_pair_runtime_marker.v1",
            "launch_id": self._launch_id,
            "root_locator_sha256": _root_locator_sha256(runtime),
            "synthetic_root_locator_sha256": fixture_receipt["root_locator_sha256"],
        }
        _write_json_exclusive(runtime / RUNTIME_MARKER_NAME, marker)
        self._synthetic_root = fixture
        self._runtime_root = runtime
        self._allowed_parent = parent
        self._fixture_receipt = fixture_receipt

    def _spawn(self, role: PairHost, cookie_name: str) -> subprocess.Popen[bytes]:
        assert self._synthetic_root is not None and self._runtime_root is not None
        descriptor_path = self._runtime_root / f"{role.value}.json"
        repository_root = Path(__file__).resolve().parents[2]
        site_packages = next(
            (Path(value) for value in site.getsitepackages() if Path(value).name == "site-packages"),
            None,
        )
        base_executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        if site_packages is None or not site_packages.is_dir() or not base_executable.is_file():
            raise DevPairRuntimeError("CHILD_INTERPRETER_INVALID")
        bootstrap = (
            "import runpy,sys;"
            "sys.path[:0]=sys.argv[1:3];"
            "sys.argv=['dev_pair_runtime']+sys.argv[3:];"
            "runpy.run_module('code_assist_v1.notion_collaboration.dev_pair_runtime',run_name='__main__')"
        )
        command = [
            str(base_executable),
            "-E",
            "-c",
            bootstrap,
            str(repository_root),
            str(site_packages),
            "--child",
            "--role",
            role.value,
            "--synthetic-root",
            str(self._synthetic_root),
            "--runtime-root",
            str(self._runtime_root),
            "--descriptor",
            str(descriptor_path),
            "--launch-id",
            self._launch_id,
            "--cookie-name",
            cookie_name,
        ]
        if role is self._fault_before_ready_role:
            command.append("--fault-before-ready")
        return subprocess.Popen(
            command,
            cwd=repository_root,
            env=_sanitized_child_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
        )

    def _wait_descriptor(self, role: PairHost, process: subprocess.Popen[bytes], deadline: float) -> HostDescriptor:
        assert self._runtime_root is not None
        path = self._runtime_root / f"{role.value}.json"
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise DevPairRuntimeError("CHILD_EXITED_BEFORE_READY")
            if path.exists():
                try:
                    document = _load_json(path)
                    if path.read_bytes() != _canonical_json_bytes(document):
                        raise DevPairRuntimeError("DESCRIPTOR_NONCANONICAL")
                    descriptor = HostDescriptor.from_document(document)
                    if descriptor.role is not role or descriptor.pid != process.pid:
                        raise DevPairRuntimeError("DESCRIPTOR_PID_OR_ROLE_MISMATCH")
                    if descriptor.launch_id != self._launch_id:
                        raise DevPairRuntimeError("DESCRIPTOR_LAUNCH_MISMATCH")
                    assert self._fixture_receipt is not None
                    if descriptor.root_id != self._fixture_receipt["root_id"]:
                        raise DevPairRuntimeError("DESCRIPTOR_ROOT_MISMATCH")
                    return descriptor
                except (OSError, UnicodeError, ValueError, json.JSONDecodeError, DevPairRuntimeError) as exc:
                    last_error = exc
            time.sleep(0.02)
        raise DevPairRuntimeError("CHILD_READINESS_TIMEOUT") from last_error

    def _verify_http_readiness(self, descriptor: HostDescriptor, deadline: float) -> None:
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urlopen(Request(descriptor.origin + "/__pair__/ready", method="GET"), timeout=0.5) as response:
                    document = json.loads(response.read().decode("utf-8"))
                expected = {
                    "status": "ready",
                    "role": descriptor.role.value,
                    "pid": descriptor.pid,
                    "launch_id": self._launch_id,
                    "root_id": descriptor.root_id,
                }
                if document != expected:
                    raise DevPairRuntimeError("READINESS_IDENTITY_MISMATCH")
                return
            except (OSError, URLError, UnicodeError, ValueError, json.JSONDecodeError, DevPairRuntimeError) as exc:
                last_error = exc
                time.sleep(0.02)
        raise DevPairRuntimeError("HTTP_READINESS_TIMEOUT") from last_error

    def start(self) -> dict[str, Any]:
        if self._started or self._closed:
            raise DevPairRuntimeError("PAIR_RUNTIME_NOT_STARTABLE")
        try:
            self._create_runtime_root()
            assert self._fixture_receipt is not None and self._synthetic_root is not None
            cookie_name = _cookie_name(self._fixture_receipt["root_id"])
            deadline = time.monotonic() + self._startup_timeout
            for role in HOST_ORDER:
                process = self._spawn(role, cookie_name)
                self._processes[role] = process
                self._descriptors[role] = self._wait_descriptor(role, process, deadline)
            if len(set(self.process_ids)) != 2 or len(set(self.ports)) != 2:
                raise DevPairRuntimeError("PAIR_PROCESS_OR_PORT_COLLISION")
            if set(self.ports) & FORBIDDEN_OPERATOR_PORTS:
                raise DevPairRuntimeError("FORBIDDEN_OPERATOR_PORT_ASSIGNED")
            secret = (self._synthetic_root / SECRET_RELATIVE).read_text(encoding="utf-8").strip()
            load = load_pair_configuration(
                {
                    PAIR_ENABLED_KEY: "true",
                    PAIR_ENVIRONMENT_KEY: PAIR_ENVIRONMENT,
                    CODE_ORIGIN_KEY: self._descriptors[PairHost.CODE].origin,
                    NOTION_ORIGIN_KEY: self._descriptors[PairHost.NOTION].origin,
                    PAIR_COOKIE_NAME_KEY: cookie_name,
                    PAIR_SECRET_KEY: secret,
                }
            )
            if not load.enabled or load.configuration is None:
                raise DevPairRuntimeError("PAIR_CONFIGURATION_REJECTED")
            self._configuration = load.configuration
            for descriptor in self._descriptors.values():
                self._verify_http_readiness(descriptor, deadline)
            self._started = True
            return self.receipt()
        except Exception:
            try:
                self.close()
            except DevPairRuntimeError:
                pass
            raise

    def _marker_bound_for_cleanup(self) -> bool:
        if self._runtime_root is None or self._allowed_parent is None or not self._runtime_root.exists():
            return True
        runtime = self._runtime_root
        if runtime.parent != self._allowed_parent or _is_reparse(runtime):
            return False
        marker_path = runtime / RUNTIME_MARKER_NAME
        try:
            marker = _load_json(marker_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return False
        return (
            isinstance(marker, dict)
            and marker.get("contract") == "code_assist.notion.synthetic_pair_runtime_marker.v1"
            and marker.get("launch_id") == self._launch_id
            and marker.get("root_locator_sha256") == _root_locator_sha256(runtime)
            and marker_path.read_bytes() == _canonical_json_bytes(marker)
        )

    def _remove_runtime_root(self) -> bool:
        if self._runtime_root is None or not self._runtime_root.exists():
            return True
        if not self._marker_bound_for_cleanup():
            return False
        pending = [self._runtime_root]
        while pending:
            directory = pending.pop()
            if _is_reparse(directory):
                return False
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if entry.is_symlink() or _is_reparse(path):
                        return False
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                    elif not entry.is_file(follow_symlinks=False):
                        return False
        shutil.rmtree(self._runtime_root)
        return not self._runtime_root.exists()

    def close(self) -> dict[str, Any]:
        if self._closed:
            return dict(self._cleanup)
        deadline = time.monotonic() + self._shutdown_timeout
        for process in self._processes.values():
            if process.poll() is None:
                process.terminate()
        for process in self._processes.values():
            remaining = max(0.05, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=max(0.05, self._shutdown_timeout))
        processes_stopped = all(process.poll() is not None for process in self._processes.values())
        ports_closed = all(_wait_port_closed(port, deadline) for port in self.ports)
        runtime_removed = self._remove_runtime_root()
        self._cleanup = {
            "owned_processes_stopped": processes_stopped,
            "owned_ports_closed": ports_closed,
            "runtime_root_removed": runtime_removed,
        }
        self._closed = True
        if not all(self._cleanup.values()):
            raise DevPairRuntimeError("OWNED_RUNTIME_CLEANUP_FAILED")
        return dict(self._cleanup)

    def receipt(self) -> dict[str, Any]:
        if not self._started or self._fixture_receipt is None or self._configuration is None:
            raise DevPairRuntimeError("PAIR_NOT_STARTED")
        return {
            "schema_version": 1,
            "contract": "code_assist.notion.synthetic_pair_runtime_receipt.v1",
            "status": "pass",
            "launch_id": self._launch_id,
            "root_id": self._fixture_receipt["root_id"],
            "root_locator_sha256": self._fixture_receipt["root_locator_sha256"],
            "secret_fingerprint": self._configuration.secret_fingerprint,
            "cookie_name": self._configuration.cookie_name,
            "process_count": 2,
            "hosts": [self._descriptors[role].public_document() for role in HOST_ORDER],
            "distinct_process_ids": len(set(self.process_ids)) == 2,
            "distinct_os_assigned_ports": len(set(self.ports)) == 2,
            "forbidden_operator_ports_accessed": False,
            "production_or_existing_preview_roots_accessed": False,
            "real_identity_imported": False,
            "plaintext_secret_or_credentials_recorded": False,
            "cleanup": dict(self._cleanup),
        }

    def __enter__(self) -> "SyntheticPairRuntime":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()


def _load_users(root: Path) -> dict[str, dict[str, Any]]:
    document = _load_json(root / USERS_RELATIVE)
    users = document.get("users") if isinstance(document, dict) else None
    if not isinstance(users, dict):
        raise DevPairRuntimeError("SYNTHETIC_USERS_INVALID")
    return users


def _create_child_app(
    *,
    role: PairHost,
    secret: str,
    cookie_name: str,
    users: Mapping[str, Mapping[str, Any]],
    launch_id: str,
    root_id: str,
    runtime_state: dict[str, Any],
) -> Flask:
    app = Flask(f"synthetic-pair-{role.value}")
    app.config.update(
        SECRET_KEY=secret,
        SESSION_COOKIE_NAME=cookie_name,
        SESSION_COOKIE_DOMAIN=None,
        SESSION_COOKIE_PATH="/",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=False,
    )

    def own_origin() -> bool:
        expected = runtime_state.get("origin")
        return isinstance(expected, str) and request.host_url.rstrip("/") == expected and request.headers.get("Origin") == expected

    def active_subject() -> str | None:
        subject = session.get("user_id")
        user = users.get(subject) if isinstance(subject, str) else None
        if not isinstance(user, Mapping) or user.get("disabled") is not False:
            return None
        return subject

    @app.after_request
    def harden(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/__pair__/ready")
    def ready():
        return jsonify(
            status="ready",
            role=role.value,
            pid=os.getpid(),
            launch_id=launch_id,
            root_id=root_id,
        )

    @app.get("/__pair__/session")
    def current_session():
        subject = active_subject()
        if subject is None:
            return jsonify(error="AUTHENTICATION_REQUIRED"), 401
        user = users[subject]
        return jsonify(subject_id=subject, auth_role=user.get("role"), host_role=role.value)

    @app.post("/api/code/auth/login")
    def login():
        if role is not PairHost.CODE:
            return jsonify(error="ACCOUNT_MUTATION_HOST_REQUIRED"), 403
        if not own_origin():
            return jsonify(error="ORIGIN_REJECTED"), 403
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {"user_id", "password"}:
            return jsonify(error="INVALID_CREDENTIALS"), 401
        user = users.get(payload.get("user_id"))
        password = payload.get("password")
        if (
            not isinstance(user, Mapping)
            or user.get("disabled") is not False
            or not isinstance(password, str)
            or not check_password_hash(str(user.get("password_hash", "")), password)
        ):
            return jsonify(error="INVALID_CREDENTIALS"), 401
        session.clear()
        session["user_id"] = str(user["id"])
        return jsonify(subject_id=user["id"])

    @app.post("/api/code/auth/logout")
    def logout():
        if not own_origin():
            return jsonify(error="ORIGIN_REJECTED"), 403
        session.clear()
        return jsonify(status="logged_out", peer_fanout=False)

    @app.post("/__pair__/mutation-probe")
    def mutation_probe():
        if not own_origin():
            return jsonify(error="ORIGIN_REJECTED"), 403
        subject = active_subject()
        if subject is None:
            return jsonify(error="AUTHENTICATION_REQUIRED"), 401
        return jsonify(subject_id=subject, host_role=role.value)

    return app


def _child_main(args: argparse.Namespace) -> int:
    role = PairHost(args.role)
    synthetic_root = Path(args.synthetic_root).resolve(strict=True)
    runtime_root = Path(args.runtime_root).resolve(strict=True)
    descriptor = Path(args.descriptor).resolve(strict=False)
    if descriptor.parent != runtime_root or descriptor.exists() or _is_reparse(runtime_root):
        raise DevPairRuntimeError("CHILD_DESCRIPTOR_PATH_INVALID")
    fixture_receipt = verify_synthetic_pair_root(synthetic_root)
    marker = _load_json(runtime_root / RUNTIME_MARKER_NAME)
    if (
        not isinstance(marker, dict)
        or marker.get("launch_id") != args.launch_id
        or marker.get("synthetic_root_locator_sha256") != fixture_receipt["root_locator_sha256"]
        or marker.get("root_locator_sha256") != _root_locator_sha256(runtime_root)
    ):
        raise DevPairRuntimeError("CHILD_RUNTIME_MARKER_INVALID")
    if args.fault_before_ready:
        raise DevPairRuntimeError("INJECTED_CHILD_FAILURE_BEFORE_READY")
    secret = (synthetic_root / SECRET_RELATIVE).read_text(encoding="utf-8").strip()
    users = _load_users(synthetic_root)
    state: dict[str, Any] = {}
    app = _create_child_app(
        role=role,
        secret=secret,
        cookie_name=args.cookie_name,
        users=users,
        launch_id=args.launch_id,
        root_id=fixture_receipt["root_id"],
        runtime_state=state,
    )
    server = make_server(LOOPBACK_HOST, 0, app, threaded=True)
    try:
        port = int(server.server_port)
        origin = f"http://{LOOPBACK_HOST}:{port}"
        state["origin"] = origin
        _write_json_atomic_create(
            descriptor,
            {
                "schema_version": 1,
                "contract": "code_assist.notion.synthetic_pair_host.v1",
                "role": role.value,
                "pid": os.getpid(),
                "port": port,
                "origin": origin,
                "launch_id": args.launch_id,
                "root_id": fixture_receipt["root_id"],
            },
        )
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--role", choices=[role.value for role in HOST_ORDER], help=argparse.SUPPRESS)
    parser.add_argument("--synthetic-root", help=argparse.SUPPRESS)
    parser.add_argument("--runtime-root", help=argparse.SUPPRESS)
    parser.add_argument("--descriptor", help=argparse.SUPPRESS)
    parser.add_argument("--launch-id", help=argparse.SUPPRESS)
    parser.add_argument("--cookie-name", help=argparse.SUPPRESS)
    parser.add_argument("--fault-before-ready", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    required = (args.role, args.synthetic_root, args.runtime_root, args.descriptor, args.launch_id, args.cookie_name)
    if not args.child or any(value is None for value in required):
        raise DevPairRuntimeError("DIRECT_CHILD_CONTRACT_REQUIRED")
    return _child_main(args)


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess integration
    raise SystemExit(main())


__all__ = [
    "DevPairRuntimeError",
    "FORBIDDEN_OPERATOR_PORTS",
    "HostDescriptor",
    "LOOPBACK_HOST",
    "RUNTIME_MARKER_NAME",
    "SyntheticPairRuntime",
]

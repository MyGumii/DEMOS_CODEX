"""ASGI entry point and owned lifecycle probes for the collaboration gateway.

The ASGI application is dependency-neutral and can be contract-tested without
opening a listener.  ``serve_main`` imports only the exact selected
uvicorn/websockets runtime.  ``GatewayLifecycleProbe`` is deliberately a
transport-free, OS-port-0 child used to prove PID/port/marker ownership before
the real cross-process WebSocket/Yjs gate in T0B-P3.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import asdict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import site
import socket
import subprocess
import sys
import time
import threading
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.request import Request, urlopen
from uuid import uuid4

from .durable_transport import DurableTransportAuthority
from .protocol_contract import AcknowledgementConflict
from .runtime_gateway import (
    ConnectionCapacityExceeded,
    GatewayRejected,
    GatewayUnavailable,
    ReconnectThrottled,
    RuntimeGateway,
    SessionClosed,
)
from .node_compactor import NodeCompactor
from .selected_runtime_contract import build_startup_attestation, runtime_binding_sha256


ASGI_SERVICE_CONTRACT = "code_assist.notion.t0b_p2.gateway_asgi.v1"
LIFECYCLE_PROBE_CONTRACT = "code_assist.notion.t0b_p2.gateway_lifecycle_probe.v1"
CROSS_PROCESS_SERVICE_CONTRACT = "code_assist.notion.t0b_p3.cross_process_gateway.v1"
LOOPBACK_HOST = "127.0.0.1"
FORBIDDEN_OPERATOR_PORTS = frozenset({10011, 10012, 10111, 10112, 10113})
EXPECTED_RUNTIME_VERSIONS = {"uvicorn": "0.52.4", "websockets": "17.0.1"}
_ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
_ASGISend = Callable[[dict[str, Any]], Awaitable[None]]
_TEST_CLIENT_HTML = b"""<!doctype html><meta charset=utf-8><title>Collaboration transport probe</title><main id=status>ready</main>"""


class GatewayServiceError(RuntimeError):
    pass


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _exclusive_json(path: Path, value: Any) -> None:
    with path.open("xb") as handle:
        handle.write(_canonical_json(value))
        handle.flush()
        os.fsync(handle.fileno())


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _root_hash(path: Path) -> str:
    normalized = os.path.normcase(str(path.resolve(strict=False))).encode("utf-8")
    return "sha256:" + hashlib.sha256(normalized).hexdigest()


def _is_reparse(path: Path) -> bool:
    try:
        stat_result = path.lstat()
    except OSError as exc:
        raise GatewayServiceError("RUNTIME_PATH_UNREADABLE") from exc
    return path.is_symlink() or bool(getattr(stat_result, "st_file_attributes", 0) & 0x400)


def _port_accepting(port: int) -> bool:
    try:
        with socket.create_connection((LOOPBACK_HOST, port), timeout=0.2):
            return True
    except OSError:
        return False


def _stable_error(exc: BaseException) -> str:
    if isinstance(exc, GatewayRejected):
        return "GATEWAY_REJECTED"
    if isinstance(exc, GatewayUnavailable):
        return "GATEWAY_UNAVAILABLE"
    if isinstance(exc, ReconnectThrottled):
        return "RECONNECT_THROTTLED"
    if isinstance(exc, ConnectionCapacityExceeded):
        return "CONNECTION_CAPACITY_EXCEEDED"
    if isinstance(exc, SessionClosed):
        return "SESSION_CLOSED"
    return "PROTOCOL_REJECTED"


class RuntimeGatewayASGI:
    """Minimal ASGI HTTP/WebSocket adapter with no canonical writes of its own."""

    def __init__(self, gateway: RuntimeGateway, *, compactor_handshake_matches: bool) -> None:
        self.gateway = gateway
        self.compactor_handshake_matches = compactor_handshake_matches is True

    async def __call__(self, scope: dict[str, Any], receive: _ASGIReceive, send: _ASGISend) -> None:
        kind = scope.get("type")
        if kind == "lifespan":
            await self._lifespan(receive, send)
        elif kind == "http":
            await self._http(scope, receive, send)
        elif kind == "websocket":
            await self._websocket(scope, receive, send)
        else:
            raise GatewayServiceError("UNSUPPORTED_ASGI_SCOPE")

    async def _lifespan(self, receive: _ASGIReceive, send: _ASGISend) -> None:
        while True:
            message = await receive()
            if message.get("type") == "lifespan.startup":
                try:
                    if self.gateway.health()["state"] == "STOPPED":
                        self.gateway.start(compactor_handshake_matches=self.compactor_handshake_matches)
                    await send({"type": "lifespan.startup.complete"})
                except Exception as exc:
                    await send({"type": "lifespan.startup.failed", "message": _stable_error(exc)})
                    return
            elif message.get("type") == "lifespan.shutdown":
                self.gateway.stop()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _http(self, scope: Mapping[str, Any], receive: _ASGIReceive, send: _ASGISend) -> None:
        while True:
            message = await receive()
            if message.get("type") != "http.request" or not message.get("more_body", False):
                break
        content_type = b"application/json; charset=utf-8"
        if scope.get("method") == "GET" and scope.get("path") == "/health":
            status = 200
            document = self.gateway.health()
        elif scope.get("method") == "GET" and scope.get("path") == "/metrics":
            status = 200
            snapshot = getattr(self.gateway, "metrics_snapshot", None)
            document = snapshot() if callable(snapshot) else self.gateway.metrics.snapshot()
        elif scope.get("method") == "GET" and scope.get("path") == "/test-client":
            status = 200
            document = None
            content_type = b"text/html; charset=utf-8"
        else:
            status = 404
            document = {"error": "NOT_FOUND"}
        body = _TEST_CLIENT_HTML if document is None else _canonical_json(document)
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", content_type),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"content-security-policy", b"default-src 'none'; connect-src 'self' ws:; style-src 'none'; script-src 'none'"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _origin(scope: Mapping[str, Any]) -> str:
        values = [value.decode("latin-1") for key, value in scope.get("headers", []) if key.lower() == b"origin"]
        if len(values) != 1:
            raise GatewayRejected()
        return values[0]

    @staticmethod
    async def _send_json(send: _ASGISend, value: Mapping[str, Any]) -> None:
        await send({"type": "websocket.send", "text": json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))})

    async def _websocket(self, scope: Mapping[str, Any], receive: _ASGIReceive, send: _ASGISend) -> None:
        if scope.get("path") != "/realtime":
            await send({"type": "websocket.close", "code": 4404, "reason": "NOT_FOUND"})
            return
        if (await receive()).get("type") != "websocket.connect":
            await send({"type": "websocket.close", "code": 4400, "reason": "PROTOCOL_REJECTED"})
            return
        await send({"type": "websocket.accept", "subprotocol": "code-assist-collaboration-v1"})
        connection_id: str | None = None
        try:
            while True:
                message = await receive()
                if message.get("type") == "websocket.disconnect":
                    return
                if message.get("type") != "websocket.receive" or not isinstance(message.get("text"), str):
                    raise ValueError("text JSON required")
                document = json.loads(message["text"])
                if not isinstance(document, dict) or not isinstance(document.get("op"), str):
                    raise ValueError("operation required")
                operation = document["op"]
                if operation == "connect" and connection_id is None:
                    descriptor = self.gateway.connect(
                        str(document.get("token", "")),
                        origin=self._origin(scope),
                        subject_id=str(document.get("subject_id", "")),
                        workspace_id=str(document.get("workspace_id", "")),
                        page_id=str(document.get("page_id", "")),
                        client_id=str(document.get("client_id", "")),
                        session_binding_sha256=str(document.get("session_binding_sha256", "")),
                        now_ms=int(time.time() * 1000),
                    )
                    connection_id = descriptor.connection_id
                    await self._send_json(send, {"type": "connected", **asdict(descriptor)})
                elif operation == "submit" and connection_id is not None:
                    payload = base64.b64decode(str(document.get("payload_base64", "")), validate=True)
                    try:
                        receipt = self.gateway.submit_payload(
                            connection_id,
                            operation_id=str(document.get("operation_id", "")),
                            client_sequence=document.get("client_sequence"),
                            acknowledgement_sequence=document.get("acknowledgement_sequence"),
                            payload=payload,
                        )
                    except AcknowledgementConflict:
                        # The authority rejected this exact request before commit.
                        # Keep the session open so its queued update can acknowledge
                        # newer durable state without changing an uncertain retry.
                        await self._send_json(send, {
                            "type": "submit_rejected",
                            "error": "ACKNOWLEDGEMENT_CONFLICT",
                            "operation_id": document.get("operation_id"),
                            "client_sequence": document.get("client_sequence"),
                            "acknowledgement_sequence": document.get("acknowledgement_sequence"),
                        })
                        continue
                    await self._send_json(
                        send,
                        {
                            "type": "ack",
                            "acknowledgement": asdict(receipt.acknowledgement),
                            "broadcast": asdict(receipt.broadcast),
                        },
                    )
                elif operation == "replay" and connection_id is not None:
                    receipt = self.gateway.replay_to_queue(
                        connection_id,
                        after_document_sequence=document.get("after_document_sequence"),
                        limit=document.get("limit"),
                    )
                    await self._send_json(send, {"type": "replay_queued", **asdict(receipt)})
                elif operation == "live_replay" and connection_id is not None:
                    receipt = self.gateway.replay_live_to_queue(
                        connection_id,
                        after_document_sequence=document.get("after_document_sequence"),
                        limit=document.get("limit"),
                    )
                    await self._send_json(
                        send, {"type": "live_replay_queued", **asdict(receipt)}
                    )
                elif operation == "presence" and connection_id is not None:
                    payload = document.get("payload")
                    if not isinstance(payload, dict):
                        raise ValueError("presence payload required")
                    receipt = self.gateway.update_presence(
                        connection_id,
                        payload=payload,
                        now_ms=int(time.time() * 1000),
                        ttl_ms=document.get("ttl_ms", 15_000),
                    )
                    await self._send_json(
                        send, {"type": "presence_ack", **asdict(receipt)}
                    )
                elif operation == "drain" and connection_id is not None:
                    frames = self.gateway.drain(connection_id, max_frames=document.get("max_frames"))
                    await self._send_json(
                        send,
                        {
                            "type": "frames",
                            "frames": [
                                {
                                    **{key: value for key, value in frame.public_document().items() if key != "payload"},
                                    "payload_base64": base64.b64encode(frame.payload).decode("ascii"),
                                }
                                for frame in frames
                            ],
                        },
                    )
                else:
                    raise ValueError("operation order invalid")
        except Exception as exc:
            await self._send_json(send, {"type": "error", "error": _stable_error(exc)})
            await send({"type": "websocket.close", "code": 4403, "reason": _stable_error(exc)})
        finally:
            if connection_id is not None:
                self.gateway.disconnect(connection_id)


class _ProbeHandler(BaseHTTPRequestHandler):
    server_version = "CodeAssistGatewayProbe/1"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        if self.path != "/health":
            self.send_error(404)
            return
        body = _canonical_json({"contract": LIFECYCLE_PROBE_CONTRACT, "status": "ready", "pid": os.getpid()})
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class GatewayLifecycleProbe:
    """Own one disposable child, marker, and OS-assigned loopback port."""

    def __init__(self, runtime_root: Path, *, allowed_parent: Path, timeout_seconds: float = 10.0) -> None:
        self.runtime_root_input = Path(runtime_root)
        self.allowed_parent_input = Path(allowed_parent)
        self.timeout = float(timeout_seconds)
        if self.timeout <= 0:
            raise GatewayServiceError("TIMEOUT_INVALID")
        self.launch_id = uuid4().hex
        self.runtime_root: Path | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.pid: int | None = None
        self.port: int | None = None
        self._closed = False

    def _prepare(self) -> None:
        if not self.runtime_root_input.is_absolute() or not self.allowed_parent_input.is_absolute():
            raise GatewayServiceError("ABSOLUTE_PATH_REQUIRED")
        parent = self.allowed_parent_input.resolve(strict=True)
        candidate = self.runtime_root_input.resolve(strict=False)
        if candidate.parent != parent or candidate == parent or candidate.exists() or _is_reparse(parent):
            raise GatewayServiceError("RUNTIME_ROOT_INVALID")
        os.mkdir(candidate)
        if _is_reparse(candidate):
            raise GatewayServiceError("RUNTIME_ROOT_REPARSE_FORBIDDEN")
        self.runtime_root = candidate
        _exclusive_json(
            candidate / "marker.json",
            {
                "contract": LIFECYCLE_PROBE_CONTRACT,
                "launch_id": self.launch_id,
                "root_locator_sha256": _root_hash(candidate),
            },
        )

    def start(self) -> dict[str, Any]:
        self._prepare()
        assert self.runtime_root is not None
        descriptor = self.runtime_root / "descriptor.json"
        repository_root = Path(__file__).resolve().parents[2]
        site_packages = next(
            (Path(value) for value in site.getsitepackages() if Path(value).name == "site-packages"),
            None,
        )
        base_executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        if site_packages is None or not site_packages.is_dir() or not base_executable.is_file():
            raise GatewayServiceError("CHILD_INTERPRETER_INVALID")
        bootstrap = (
            "import runpy,sys;"
            "sys.path[:0]=sys.argv[1:3];"
            "sys.argv=['gateway_service']+sys.argv[3:];"
            "runpy.run_module('code_assist_v1.notion_collaboration.gateway_service',run_name='__main__')"
        )
        command = [
            str(base_executable),
            "-E",
            "-c",
            bootstrap,
            str(repository_root),
            str(site_packages),
            "--probe-child",
            "--runtime-root",
            str(self.runtime_root),
            "--descriptor",
            str(descriptor),
            "--launch-id",
            self.launch_id,
        ]
        try:
            self.process = subprocess.Popen(
                command,
                cwd=repository_root,
                env=dict(os.environ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                close_fds=True,
            )
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise GatewayServiceError("CHILD_EXITED_BEFORE_READY")
                if descriptor.exists():
                    document = _load_json(descriptor)
                    if set(document) == {"contract", "launch_id", "pid", "port"}:
                        self.pid = int(document["pid"])
                        self.port = int(document["port"])
                        if (
                            document["contract"] != LIFECYCLE_PROBE_CONTRACT
                            or document["launch_id"] != self.launch_id
                            or self.pid != self.process.pid
                            or not (1 <= self.port <= 65535)
                            or self.port in FORBIDDEN_OPERATOR_PORTS
                        ):
                            raise GatewayServiceError("DESCRIPTOR_BINDING_INVALID")
                        with urlopen(Request(f"http://{LOOPBACK_HOST}:{self.port}/health"), timeout=1) as response:
                            health = json.loads(response.read().decode("utf-8"))
                        if health != {"contract": LIFECYCLE_PROBE_CONTRACT, "status": "ready", "pid": self.pid}:
                            raise GatewayServiceError("PROBE_HEALTH_INVALID")
                        return self.receipt()
                time.sleep(0.02)
            raise GatewayServiceError("CHILD_READINESS_TIMEOUT")
        except BaseException:
            self.close()
            raise

    def _marker_matches(self) -> bool:
        if self.runtime_root is None or not self.runtime_root.is_dir() or _is_reparse(self.runtime_root):
            return False
        marker_path = self.runtime_root / "marker.json"
        try:
            marker = _load_json(marker_path)
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        return marker == {
            "contract": LIFECYCLE_PROBE_CONTRACT,
            "launch_id": self.launch_id,
            "root_locator_sha256": _root_hash(self.runtime_root),
        }

    def close(self) -> dict[str, bool]:
        if self._closed:
            return {"owned_process_stopped": True, "owned_port_closed": True, "runtime_root_removed": self.runtime_root is None or not self.runtime_root.exists()}
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=self.timeout)
        process_stopped = self.process is None or self.process.poll() is not None
        deadline = time.monotonic() + self.timeout
        while self.port is not None and time.monotonic() < deadline and _port_accepting(self.port):
            time.sleep(0.02)
        port_closed = self.port is None or not _port_accepting(self.port)
        root_removed = self.runtime_root is None or not self.runtime_root.exists()
        if self.runtime_root is not None and self.runtime_root.exists() and self._marker_matches():
            safe = all(item.is_file() and not _is_reparse(item) for item in self.runtime_root.iterdir())
            if safe:
                shutil.rmtree(self.runtime_root)
                root_removed = not self.runtime_root.exists()
        self._closed = True
        cleanup = {"owned_process_stopped": process_stopped, "owned_port_closed": port_closed, "runtime_root_removed": root_removed}
        if not all(cleanup.values()):
            raise GatewayServiceError("OWNED_RUNTIME_CLEANUP_FAILED")
        return cleanup

    def receipt(self) -> dict[str, Any]:
        if self.pid is None or self.port is None:
            raise GatewayServiceError("PROBE_NOT_READY")
        return {
            "contract": LIFECYCLE_PROBE_CONTRACT,
            "status": "pass",
            "pid": self.pid,
            "port": self.port,
            "os_assigned_port": True,
            "forbidden_operator_port_accessed": False,
            "transport_or_database_accessed": False,
            "websocket_and_yjs_evidence_deferred_to": "T0B-P3",
        }

    def __enter__(self) -> "GatewayLifecycleProbe":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def create_application(
    catalog_path: Path,
    user_data_root: Path,
    *,
    aggregate_enabled: bool,
    realtime_enabled: bool,
    compactor_handshake_matches: bool,
    max_queue_frames: int = 64,
    max_connections: int = 1_024,
    reconnect_attempts: int = 5,
) -> RuntimeGatewayASGI:
    from .canonical_authority import AuthorityUnavailable
    from .iam_runtime import CollaborativeDurableTransportAuthority
    from .editing_authority import CollaborativeEditingTransportAuthority

    try:
        transport = CollaborativeEditingTransportAuthority.open(
            catalog_path, user_data_root
        )
    except AuthorityUnavailable:
        try:
            transport = CollaborativeDurableTransportAuthority.open(
                catalog_path, user_data_root
            )
        except AuthorityUnavailable:
            transport = DurableTransportAuthority.open(catalog_path, user_data_root)
    gateway = RuntimeGateway(
        transport=transport,
        aggregate_enabled=aggregate_enabled,
        realtime_enabled=realtime_enabled,
        max_queue_frames=max_queue_frames,
        max_connections=max_connections,
        reconnect_attempts=reconnect_attempts,
    )
    return RuntimeGatewayASGI(gateway, compactor_handshake_matches=compactor_handshake_matches)


def validate_selected_service_runtime() -> dict[str, str]:
    observed: dict[str, str] = {}
    for package, expected in EXPECTED_RUNTIME_VERSIONS.items():
        try:
            observed[package] = metadata.version(package)
        except metadata.PackageNotFoundError as exc:
            raise GatewayServiceError("SELECTED_RUNTIME_NOT_INSTALLED") from exc
        if observed[package] != expected:
            raise GatewayServiceError("SELECTED_RUNTIME_VERSION_MISMATCH")
    return observed


def _probe_child(args: argparse.Namespace) -> int:
    runtime_root = Path(args.runtime_root).resolve(strict=True)
    descriptor = Path(args.descriptor).resolve(strict=False)
    marker = _load_json(runtime_root / "marker.json")
    if (
        descriptor.parent != runtime_root
        or descriptor.exists()
        or marker.get("contract") != LIFECYCLE_PROBE_CONTRACT
        or marker.get("launch_id") != args.launch_id
        or marker.get("root_locator_sha256") != _root_hash(runtime_root)
    ):
        raise GatewayServiceError("CHILD_MARKER_INVALID")
    server = ThreadingHTTPServer((LOOPBACK_HOST, 0), _ProbeHandler)
    try:
        port = int(server.server_port)
        _exclusive_json(
            descriptor,
            {"contract": LIFECYCLE_PROBE_CONTRACT, "launch_id": args.launch_id, "pid": os.getpid(), "port": port},
        )
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def serve_main(args: argparse.Namespace) -> int:
    validate_selected_service_runtime()
    import uvicorn  # type: ignore[import-not-found]  # exact dedicated lock

    application = create_application(
        Path(args.catalog),
        Path(args.user_data_root),
        aggregate_enabled=args.aggregate_enabled,
        realtime_enabled=args.realtime_enabled,
        compactor_handshake_matches=args.compactor_ready,
    )
    uvicorn.run(application, host=LOOPBACK_HOST, port=args.port, ws="websockets", lifespan="on", log_config=None)
    return 0


def _cross_process_marker_matches(runtime_root: Path, launch_id: str) -> bool:
    try:
        marker = _load_json(runtime_root / "marker.json")
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return marker == {
        "contract": "code_assist.notion.t0b_p3.cross_process_launcher.v1",
        "launch_id": launch_id,
        "root_locator_sha256": _root_hash(runtime_root),
    }


def _control_reader(instance_id: str, stop_event: threading.Event) -> None:
    expected = f"STOP {instance_id}"
    try:
        for line in sys.stdin:
            if line.rstrip("\r\n") == expected:
                stop_event.set()
                return
    finally:
        stop_event.set()


async def _serve_selected_child_async(args: argparse.Namespace) -> int:
    versions = validate_selected_service_runtime()
    import uvicorn  # type: ignore[import-not-found]  # exact dedicated lock

    runtime_root = Path(args.runtime_root).resolve(strict=True)
    descriptor_path = Path(args.descriptor).resolve(strict=False)
    catalog = Path(args.catalog).resolve(strict=True)
    user_data_root = Path(args.user_data_root).resolve(strict=True)
    node_runtime = Path(args.node_runtime_root).resolve(strict=True)
    if (
        descriptor_path.parent != runtime_root
        or descriptor_path.exists()
        or not _cross_process_marker_matches(runtime_root, args.launch_id)
        or node_runtime.parent != runtime_root
        or catalog == user_data_root
        or not catalog.is_relative_to(user_data_root)
    ):
        raise GatewayServiceError("CROSS_PROCESS_CHILD_BINDING_INVALID")

    compactor = NodeCompactor(
        node_executable=Path(args.node_executable),
        entrypoint=Path(args.node_entrypoint),
        runtime_root=node_runtime,
        timeout_seconds=args.child_timeout,
    )
    operator_port = int(args.operator_fixed_port or 0)
    if operator_port:
        if operator_port != 10113 or args.operator_approval_id != "AP-DEV-PAIR":
            raise GatewayServiceError("OPERATOR_FIXED_PORT_APPROVAL_INVALID")
    elif args.operator_approval_id:
        raise GatewayServiceError("OPERATOR_FIXED_PORT_APPROVAL_INVALID")

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    listener.bind((LOOPBACK_HOST, operator_port))
    port = int(listener.getsockname()[1])
    if not operator_port and port in FORBIDDEN_OPERATOR_PORTS:
        listener.close()
        raise GatewayServiceError("FORBIDDEN_OPERATOR_PORT_ASSIGNED")
    stop_event = threading.Event()
    control = threading.Thread(
        target=_control_reader,
        args=(args.instance_id, stop_event),
        name="gateway-owned-control",
        daemon=True,
    )
    try:
        handshake = compactor.start()
        application = create_application(
            catalog,
            user_data_root,
            aggregate_enabled=True,
            realtime_enabled=True,
            compactor_handshake_matches=handshake.get("ready") is True,
            max_queue_frames=args.max_queue_frames,
            max_connections=args.max_connections,
            reconnect_attempts=args.reconnect_attempts,
        )
        configuration = uvicorn.Config(
            application,
            host=None,
            port=0,
            ws="websockets",
            lifespan="on",
            log_config=None,
            access_log=False,
        )
        server = uvicorn.Server(configuration)
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        deadline = time.monotonic() + args.child_timeout
        while not server.started:
            if server_task.done():
                await server_task
                raise GatewayServiceError("SELECTED_GATEWAY_START_FAILED")
            if time.monotonic() >= deadline:
                raise GatewayServiceError("SELECTED_GATEWAY_START_TIMEOUT")
            await asyncio.sleep(0.01)
        health = application.gateway.health()
        if health.get("ready_for_admission") is not True:
            raise GatewayServiceError("SELECTED_GATEWAY_NOT_READY")
        attestation = build_startup_attestation(
            deployment_generation_id=args.launch_id,
            gateway_instance_id=args.instance_id,
            schema_compatibility="compatible",
            gateway_pid=os.getpid(),
            compactor_pid=compactor.pid,
            listener_port=port,
        )
        _exclusive_json(
            descriptor_path,
            {
                "contract": CROSS_PROCESS_SERVICE_CONTRACT,
                "launch_id": args.launch_id,
                "instance_id": args.instance_id,
                "gateway_pid": os.getpid(),
                "compactor_pid": compactor.pid,
                "port": port,
                "os_assigned_port": not bool(operator_port),
                "runtime_binding_sha256": runtime_binding_sha256(),
                "runtime_versions": {
                    **versions,
                    "node": handshake["node_version"],
                    "yjs": handshake["yjs_version"],
                },
                "startup_attestation": attestation,
            },
        )
        control.start()
        control_task = asyncio.create_task(asyncio.to_thread(stop_event.wait))
        done, _pending = await asyncio.wait(
            {server_task, control_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if server_task in done:
            await server_task
        else:
            server.should_exit = True
            await server_task
        if not control_task.done():
            control_task.cancel()
    finally:
        stop_event.set()
        compactor.close()
        listener.close()
    return 0


def _serve_selected_child(args: argparse.Namespace) -> int:
    return asyncio.run(_serve_selected_child_async(args))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--runtime-root", help=argparse.SUPPRESS)
    parser.add_argument("--descriptor", help=argparse.SUPPRESS)
    parser.add_argument("--launch-id", help=argparse.SUPPRESS)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--serve-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--catalog")
    parser.add_argument("--user-data-root")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--aggregate-enabled", action="store_true")
    parser.add_argument("--realtime-enabled", action="store_true")
    parser.add_argument("--compactor-ready", action="store_true")
    parser.add_argument("--instance-id", help=argparse.SUPPRESS)
    parser.add_argument("--node-executable", help=argparse.SUPPRESS)
    parser.add_argument("--node-runtime-root", help=argparse.SUPPRESS)
    parser.add_argument("--node-entrypoint", help=argparse.SUPPRESS)
    parser.add_argument("--child-timeout", type=float, default=30.0, help=argparse.SUPPRESS)
    parser.add_argument("--max-queue-frames", type=int, default=64, help=argparse.SUPPRESS)
    parser.add_argument("--max-connections", type=int, default=1_024, help=argparse.SUPPRESS)
    parser.add_argument("--reconnect-attempts", type=int, default=5, help=argparse.SUPPRESS)
    parser.add_argument("--operator-fixed-port", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--operator-approval-id", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.probe_child:
        if not all((args.runtime_root, args.descriptor, args.launch_id)):
            raise GatewayServiceError("PROBE_CHILD_ARGUMENTS_REQUIRED")
        return _probe_child(args)
    if args.serve_child:
        required = (
            args.catalog,
            args.user_data_root,
            args.runtime_root,
            args.descriptor,
            args.launch_id,
            args.instance_id,
            args.node_executable,
            args.node_runtime_root,
            args.node_entrypoint,
        )
        if not all(required) or min(args.child_timeout, args.max_queue_frames, args.max_connections, args.reconnect_attempts) <= 0:
            raise GatewayServiceError("CROSS_PROCESS_CHILD_ARGUMENTS_REQUIRED")
        return _serve_selected_child(args)
    if args.serve:
        if not all((args.catalog, args.user_data_root)) or args.port < 0 or args.port > 65535:
            raise GatewayServiceError("SERVICE_ARGUMENTS_INVALID")
        return serve_main(args)
    raise GatewayServiceError("EXPLICIT_MODE_REQUIRED")


if __name__ == "__main__":  # pragma: no cover - subprocess and selected-runtime entry point
    raise SystemExit(main())


__all__ = [
    "ASGI_SERVICE_CONTRACT",
    "CROSS_PROCESS_SERVICE_CONTRACT",
    "EXPECTED_RUNTIME_VERSIONS",
    "FORBIDDEN_OPERATOR_PORTS",
    "GatewayLifecycleProbe",
    "GatewayServiceError",
    "LIFECYCLE_PROBE_CONTRACT",
    "LOOPBACK_HOST",
    "RuntimeGatewayASGI",
    "create_application",
    "validate_selected_service_runtime",
]

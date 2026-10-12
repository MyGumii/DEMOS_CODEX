"""Owned JSON-lines supervisor for the selected Node/Yjs compactor."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import queue
import subprocess
import threading
from typing import Any, Mapping
from uuid import uuid4

from .selected_runtime_contract import (
    runtime_binding_sha256,
    validate_compaction_response,
)


NODE_HANDSHAKE_CONTRACT = "code_assist.notion.node_compactor_handshake.v1"
NODE_COMPACTOR_CONTRACT = "code_assist.notion.node_compactor_supervisor.v1"
EXPECTED_NODE_VERSION = "24.14.1"
EXPECTED_YJS_VERSION = "13.6.32"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class NodeCompactorError(RuntimeError):
    pass


class NodeCompactor:
    """Own exactly one listener-free Node child and its stdin/stdout protocol."""

    def __init__(
        self,
        *,
        node_executable: Path,
        entrypoint: Path,
        runtime_root: Path,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.node_executable = Path(node_executable).resolve(strict=True)
        self.entrypoint = Path(entrypoint).resolve(strict=True)
        self.runtime_root = Path(runtime_root).resolve(strict=True)
        self.timeout = float(timeout_seconds)
        if self.timeout <= 0 or self.entrypoint.parent != self.runtime_root:
            raise NodeCompactorError("NODE_COMPACTOR_CONFIGURATION_INVALID")
        if not (self.runtime_root / "node_modules" / "yjs" / "package.json").is_file():
            raise NodeCompactorError("NODE_COMPACTOR_DEPENDENCY_MISSING")
        self.process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[str | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self.handshake: dict[str, Any] | None = None

    def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self._responses.put(line)
        finally:
            self._responses.put(None)

    def start(self) -> dict[str, Any]:
        if self.process is not None:
            raise NodeCompactorError("NODE_COMPACTOR_ALREADY_STARTED")
        self.process = subprocess.Popen(
            [str(self.node_executable), str(self.entrypoint)],
            cwd=self.runtime_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            close_fds=True,
        )
        self._reader = threading.Thread(target=self._read_stdout, name="node-compactor-reader", daemon=True)
        self._reader.start()
        request_id = "handshake_" + uuid4().hex
        response = self._request(
            {
                "op": "handshake",
                "contract": NODE_HANDSHAKE_CONTRACT,
                "request_id": request_id,
                "candidate_id": "PY_OPAQUE_NODE_COMPACTOR",
                "runtime_binding_sha256": runtime_binding_sha256(),
            }
        )
        expected = {
            "contract": NODE_HANDSHAKE_CONTRACT,
            "request_id": request_id,
            "candidate_id": "PY_OPAQUE_NODE_COMPACTOR",
            "runtime_binding_sha256": runtime_binding_sha256(),
            "node_version": EXPECTED_NODE_VERSION,
            "yjs_version": EXPECTED_YJS_VERSION,
            "ready": True,
            "listener_opened": False,
        }
        if response != expected:
            self.close()
            raise NodeCompactorError("NODE_COMPACTOR_HANDSHAKE_INVALID")
        self.handshake = response
        return dict(response)

    def _request(self, document: Mapping[str, Any]) -> dict[str, Any]:
        process = self.process
        if process is None or process.poll() is not None or process.stdin is None:
            raise NodeCompactorError("NODE_COMPACTOR_NOT_RUNNING")
        encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        try:
            process.stdin.write(encoded)
            process.stdin.flush()
            line = self._responses.get(timeout=self.timeout)
        except (BrokenPipeError, OSError, queue.Empty) as exc:
            raise NodeCompactorError("NODE_COMPACTOR_UNAVAILABLE") from exc
        if line is None or len(line.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise NodeCompactorError("NODE_COMPACTOR_RESPONSE_INVALID")
        try:
            envelope = json.loads(line)
        except (ValueError, json.JSONDecodeError) as exc:
            raise NodeCompactorError("NODE_COMPACTOR_RESPONSE_INVALID") from exc
        if not isinstance(envelope, dict) or envelope.get("ok") is not True or not isinstance(envelope.get("result"), dict):
            raise NodeCompactorError("NODE_COMPACTOR_REJECTED")
        return dict(envelope["result"])

    def compact(
        self,
        *,
        request: Mapping[str, Any],
        base_snapshot: bytes | None,
        ordered_updates: tuple[bytes, ...],
    ) -> tuple[dict[str, Any], bytes, bytes]:
        response = self._request(
            {
                "op": "compact",
                "request": dict(request),
                "base_snapshot_base64": None if base_snapshot is None else base64.b64encode(base_snapshot).decode("ascii"),
                "updates_base64": [base64.b64encode(item).decode("ascii") for item in ordered_updates],
            }
        )
        snapshot_encoded = response.pop("snapshot_base64", None)
        vector_encoded = response.pop("state_vector_base64", None)
        try:
            snapshot = base64.b64decode(snapshot_encoded, validate=True)
            state_vector = base64.b64decode(vector_encoded, validate=True)
        except (TypeError, ValueError) as exc:
            raise NodeCompactorError("NODE_COMPACTOR_BYTES_INVALID") from exc
        validated = validate_compaction_response(response, request=request)
        return validated, snapshot, state_vector

    @property
    def pid(self) -> int:
        if self.process is None:
            raise NodeCompactorError("NODE_COMPACTOR_NOT_RUNNING")
        return self.process.pid

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=self.timeout)
        if self._reader is not None:
            self._reader.join(timeout=self.timeout)

    def __enter__(self) -> "NodeCompactor":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


__all__ = [
    "EXPECTED_NODE_VERSION",
    "EXPECTED_YJS_VERSION",
    "NODE_COMPACTOR_CONTRACT",
    "NODE_HANDSHAKE_CONTRACT",
    "NodeCompactor",
    "NodeCompactorError",
]

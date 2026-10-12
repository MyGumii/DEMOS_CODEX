"""Disposable E0-P2 pycrdt adapter; no network endpoint is created."""

from __future__ import annotations

import argparse
import base64
import hashlib
from importlib import metadata
import json
from pathlib import Path
import sys
from typing import Any

import pycrdt
import pycrdt.websocket  # noqa: F401 - proves the gateway distribution imports
import uvicorn


ADAPTER_ID = "PY_NATIVE_PYCRDT"


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")), flush=True)


def _decode_update(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError("update must be base64 text")
    return base64.b64decode(value, validate=True)


def _load_document(state_path: Path) -> pycrdt.Doc:
    document = pycrdt.Doc()
    if state_path.exists():
        payload = state_path.read_bytes()
        if payload:
            document.apply_update(payload)
    return document


def _persist(document: pycrdt.Doc, state_path: Path) -> None:
    payload = document.get_update()
    temporary = state_path.with_suffix(state_path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(state_path)


def _state(document: pycrdt.Doc) -> dict[str, Any]:
    text = document.get("body", type=pycrdt.Text).to_py() or ""
    update = document.get_update()
    return {
        "text": text,
        "update_base64": base64.b64encode(update).decode("ascii"),
        "update_bytes": len(update),
        "update_sha256": hashlib.sha256(update).hexdigest(),
        "state_vector_base64": base64.b64encode(document.get_state()).decode("ascii"),
    }


def _apply_command(
    document: pycrdt.Doc,
    state_path: Path,
    command: dict[str, Any],
) -> bool:
    operation = command.get("op")
    body = document.get("body", type=pycrdt.Text)
    if operation == "ping":
        _emit({"adapter_id": ADAPTER_ID, "event": "pong"})
        return True
    if operation == "state":
        _emit({"adapter_id": ADAPTER_ID, "event": "state", **_state(document)})
        return True
    if operation == "insert":
        index = command.get("index")
        value = command.get("text")
        current = body.to_py() or ""
        if type(index) is not int or not 0 <= index <= len(current):
            raise ValueError("insert index is invalid")
        if not isinstance(value, str) or not value:
            raise ValueError("insert text is invalid")
        with document.transaction():
            body.insert(index, value)
        _persist(document, state_path)
        _emit({"adapter_id": ADAPTER_ID, "event": "inserted", **_state(document)})
        return True
    if operation == "delete":
        start = command.get("start")
        end = command.get("end")
        current = body.to_py() or ""
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(current)
        ):
            raise ValueError("delete range is invalid")
        with document.transaction():
            del body[start:end]
        _persist(document, state_path)
        _emit({"adapter_id": ADAPTER_ID, "event": "deleted", **_state(document)})
        return True
    if operation == "apply_update":
        document.apply_update(_decode_update(command.get("update_base64")))
        _persist(document, state_path)
        _emit({"adapter_id": ADAPTER_ID, "event": "update_applied", **_state(document)})
        return True
    if operation == "shutdown":
        _persist(document, state_path)
        _emit({"adapter_id": ADAPTER_ID, "event": "stopped", **_state(document)})
        return False
    raise ValueError("unsupported operation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", type=Path, required=True)
    args = parser.parse_args()
    state_path = args.state_file.resolve()
    if not state_path.parent.is_dir():
        raise SystemExit("state parent must already exist")

    document = _load_document(state_path)
    document.get("body", type=pycrdt.Text)
    _emit(
        {
            "adapter_id": ADAPTER_ID,
            "event": "ready",
            "pycrdt": metadata.version("pycrdt"),
            "pycrdt_websocket": metadata.version("pycrdt-websocket"),
            "uvicorn": uvicorn.__version__,
            "network_listener": False,
            "port_bound": False,
            **_state(document),
        }
    )
    for line in sys.stdin:
        try:
            command = json.loads(line)
            if not isinstance(command, dict):
                raise ValueError("command must be an object")
            if not _apply_command(document, state_path, command):
                return 0
        except Exception as exc:  # protocol errors are evidence, not tracebacks
            _emit(
                {
                    "adapter_id": ADAPTER_ID,
                    "event": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            return 2
    _persist(document, state_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

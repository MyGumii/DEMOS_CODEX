"""Disposable E0-P2 opaque Python relay probe; it creates no network endpoint."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import uvicorn
import websockets


ADAPTER_ID = "PY_OPAQUE_NODE_COMPACTOR_RELAY"


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")), flush=True)


def _load(state_path: Path) -> list[bytes]:
    if not state_path.exists():
        return []
    value = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"updates"} or not isinstance(value["updates"], list):
        raise ValueError("opaque relay state is invalid")
    return [base64.b64decode(item, validate=True) for item in value["updates"]]


def _persist(updates: list[bytes], state_path: Path) -> None:
    value = {"updates": [base64.b64encode(item).decode("ascii") for item in updates]}
    temporary = state_path.with_suffix(state_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(state_path)


def _state(updates: list[bytes]) -> dict[str, Any]:
    digests = [hashlib.sha256(item).hexdigest() for item in updates]
    aggregate = hashlib.sha256("".join(digests).encode("ascii")).hexdigest()
    return {
        "update_count": len(updates),
        "update_sha256": digests,
        "aggregate_sha256": aggregate,
        "total_bytes": sum(map(len, updates)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", type=Path, required=True)
    args = parser.parse_args()
    state_path = args.state_file.resolve()
    if not state_path.parent.is_dir():
        raise SystemExit("state parent must already exist")
    updates = _load(state_path)
    _emit(
        {
            "adapter_id": ADAPTER_ID,
            "event": "ready",
            "uvicorn": uvicorn.__version__,
            "websockets": websockets.__version__,
            "opaque_content_inspection": False,
            "network_listener": False,
            "port_bound": False,
            **_state(updates),
        }
    )
    for line in sys.stdin:
        try:
            command = json.loads(line)
            if not isinstance(command, dict):
                raise ValueError("command must be an object")
            operation = command.get("op")
            if operation == "ping":
                _emit({"adapter_id": ADAPTER_ID, "event": "pong"})
            elif operation == "state":
                _emit({"adapter_id": ADAPTER_ID, "event": "state", **_state(updates)})
            elif operation == "append_update":
                encoded = command.get("update_base64")
                if not isinstance(encoded, str):
                    raise ValueError("update must be base64 text")
                update = base64.b64decode(encoded, validate=True)
                digest = hashlib.sha256(update).digest()
                if all(hashlib.sha256(item).digest() != digest for item in updates):
                    updates.append(update)
                    _persist(updates, state_path)
                _emit({"adapter_id": ADAPTER_ID, "event": "update_stored", **_state(updates)})
            elif operation == "shutdown":
                _persist(updates, state_path)
                _emit({"adapter_id": ADAPTER_ID, "event": "stopped", **_state(updates)})
                return 0
            else:
                raise ValueError("unsupported operation")
        except Exception as exc:
            _emit(
                {
                    "adapter_id": ADAPTER_ID,
                    "event": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            return 2
    _persist(updates, state_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

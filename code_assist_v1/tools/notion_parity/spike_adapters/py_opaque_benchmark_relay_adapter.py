"""Disposable E0-P3 opaque update relay corpus adapter; no listener is created."""

from __future__ import annotations

import argparse
import base64
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

import uvicorn
import websockets


RELAY_CONTRACT = "code_assist.notion.crdt_candidate_opaque_relay_request.v1"
RELAY_REPORT_CONTRACT = "code_assist.notion.crdt_candidate_opaque_relay_report.v1"
MATRIX_SHA256 = "sha256:a97bde3c677acc198e1f451619367dcec20c59a76aa4934fe7f0905718831d57"
CORPUS_SHA256 = "sha256:9be012b8150b3db48c66f743369a2b9905e234582056e28e427d77f311fca8b2"
CASE_ID_RE = re.compile(r"^[A-Z0-9-]+::[A-Z0-9_]+$")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _aggregate(digests: list[str]) -> str:
    return "sha256:" + hashlib.sha256("".join(digests).encode("ascii")).hexdigest()


def _persist(path: Path, updates: list[bytes]) -> None:
    payload = {
        "updates": [base64.b64encode(update).decode("ascii") for update in updates],
        "digests": [_digest(update) for update in updates],
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _load(path: Path) -> list[bytes]:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs, parse_constant=_reject)
    if not isinstance(value, dict) or set(value) != {"updates", "digests"}:
        raise ValueError("opaque state fields are invalid")
    updates = [base64.b64decode(item, validate=True) for item in value["updates"]]
    if [_digest(item) for item in updates] != value["digests"]:
        raise ValueError("opaque state digest drift")
    return updates


def _validate_request(request: Mapping[str, Any]) -> None:
    if request.get("contract") != RELAY_CONTRACT or request.get("action") != "opaque_relay_suite":
        raise ValueError("opaque relay request contract drift")
    if request.get("matrix_sha256") != MATRIX_SHA256 or request.get("corpus_sha256") != CORPUS_SHA256:
        raise ValueError("opaque relay authority hash drift")
    if not isinstance(request.get("cases"), list) or not request["cases"]:
        raise ValueError("opaque relay cases missing")


def run_suite(request: Mapping[str, Any], state_dir: Path) -> dict[str, Any]:
    _validate_request(request)
    cases: list[dict[str, Any]] = []
    observed_ids: set[str] = set()
    for index, case in enumerate(request["cases"]):
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id) or case_id in observed_ids:
            raise ValueError("opaque relay case id is invalid or duplicate")
        observed_ids.add(case_id)
        encoded = case.get("wire_updates_base64")
        expected_digests = case.get("wire_update_sha256")
        if not isinstance(encoded, list) or not encoded or not isinstance(expected_digests, list):
            raise ValueError("opaque relay wire update ledger missing")
        delivered = [base64.b64decode(item, validate=True) for item in encoded]
        if [_digest(item) for item in delivered] != expected_digests:
            raise ValueError("opaque relay input digest drift")
        state_path = state_dir / f"case-{index:03d}.json"
        updates: list[bytes] = []
        known: set[str] = set()
        split = max(1, len(delivered) // 2)
        for position, update in enumerate(delivered):
            digest = _digest(update)
            if digest not in known:
                updates.append(update)
                known.add(digest)
            _persist(state_path, updates)
            if position + 1 == split:
                updates = _load(state_path)
                known = {_digest(item) for item in updates}
        recovered = _load(state_path)
        recovered_digests = [_digest(item) for item in recovered]
        cases.append(
            {
                "case_id": case_id,
                "schedule_id": case.get("schedule_id"),
                "delivered_update_count": len(delivered),
                "unique_update_count": len(recovered),
                "duplicate_update_count": len(delivered) - len(recovered),
                "total_unique_update_bytes": sum(map(len, recovered)),
                "ordered_unique_update_sha256": recovered_digests,
                "aggregate_sha256": _aggregate(recovered_digests),
                "midstream_persist_reload_passed": True,
                "final_restart_recovery_passed": recovered_digests == [_digest(item) for item in updates],
            }
        )
    return {
        "contract": RELAY_REPORT_CONTRACT,
        "candidate_id": "PY_OPAQUE_NODE_COMPACTOR",
        "adapter_id": "PY_OPAQUE_NODE_COMPACTOR_RELAY_BENCHMARK",
        "matrix_sha256": MATRIX_SHA256,
        "corpus_sha256": CORPUS_SHA256,
        "network_listener": False,
        "port_bound": False,
        "opaque_content_inspection": False,
        "runtime_versions": {
            "uvicorn": uvicorn.__version__,
            "websockets": metadata.version("websockets"),
        },
        "case_count": len(cases),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    state_dir = args.state_dir.resolve(strict=True)
    if not state_dir.is_dir() or any(state_dir.iterdir()):
        raise ValueError("opaque relay state directory must be an empty existing directory")
    lines = [line for line in sys.stdin if line.strip()]
    if len(lines) != 1:
        raise ValueError("exactly one opaque relay request is required")
    request = json.loads(lines[0], object_pairs_hook=_pairs, parse_constant=_reject)
    print(json.dumps(run_suite(request, state_dir), ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2)

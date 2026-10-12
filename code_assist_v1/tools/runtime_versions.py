"""Emit a deterministic, read-only runtime version report as JSON.

This P0.1 diagnostic intentionally checks only the exact SQLite build pair
required by ``docs/knowledge_spaces/SCHEMA_V1.md``.  It does not inspect
configuration, environment variables, application data, or filesystem paths,
and it is not a substitute for the full P0.4 production preflight.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import re
import sqlite3
import subprocess
import sys
from typing import Any, Sequence


REPORT_SCHEMA = "code_assist.runtime_versions.v1"
APPROVED_SQLITE_VERSION = "3.53.4"
APPROVED_SQLITE_SOURCE_ID = (
    "2026-07-24 19:02:57 "
    "bf7c7f30031888f4e796e429ab3978879485813aaca6f641c7b33e4e09459bcc"
)
PRODUCTION_GATE_FAILURE_EXIT = 2


def _canonical_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _installed_packages() -> list[dict[str, str]]:
    """Return installed distribution names and versions without paths."""
    versions: set[tuple[str, str]] = set()
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name") or distribution.name
        version = distribution.version
        if not name or not version:
            continue
        versions.add((_canonical_package_name(str(name)), str(version)))
    return [
        {"name": name, "version": version}
        for name, version in sorted(versions, key=lambda item: (item[0], item[1]))
    ]


def _node_version() -> dict[str, Any]:
    """Read the Node executable version without exposing its resolved path."""
    try:
        completed = subprocess.run(
            ["node", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return {"available": False, "status": "not_found", "version": None}
    except subprocess.TimeoutExpired:
        return {"available": False, "status": "timeout", "version": None}
    except OSError:
        return {"available": False, "status": "os_error", "version": None}

    if completed.returncode != 0:
        return {
            "available": False,
            "return_code": int(completed.returncode),
            "status": "nonzero_exit",
            "version": None,
        }
    version = completed.stdout.strip()
    if version.startswith("v"):
        version = version[1:]
    if not version:
        return {"available": False, "status": "empty_output", "version": None}
    return {"available": True, "status": "available", "version": version}


def _sqlite_runtime() -> dict[str, str]:
    """Query the in-process SQLite library using an in-memory database only."""
    connection = sqlite3.connect(":memory:")
    try:
        version, source_id = connection.execute(
            "SELECT sqlite_version(), sqlite_source_id()"
        ).fetchone()
    finally:
        connection.close()
    return {"source_id": str(source_id), "version": str(version)}


def build_report(mode: str) -> tuple[dict[str, Any], int]:
    sqlite_runtime = _sqlite_runtime()
    mismatch_reasons: list[str] = []
    if sqlite_runtime["version"] != APPROVED_SQLITE_VERSION:
        mismatch_reasons.append("sqlite_version_mismatch")
    if sqlite_runtime["source_id"] != APPROVED_SQLITE_SOURCE_ID:
        mismatch_reasons.append("sqlite_source_id_mismatch")

    exact_match = not mismatch_reasons
    if exact_match:
        gate_status = "pass"
        message = "SQLite runtime exactly matches the production v1 allowlist."
        exit_code = 0
    elif mode == "production":
        gate_status = "fail"
        message = (
            "Production is blocked because the SQLite version/source ID pair "
            "does not exactly match the v1 allowlist."
        )
        exit_code = PRODUCTION_GATE_FAILURE_EXIT
    else:
        gate_status = "warning"
        message = (
            "Development may continue, but this SQLite runtime is not eligible "
            "for production, migration, backup, restore, worker, projector, or "
            "load-test execution."
        )
        exit_code = 0

    report: dict[str, Any] = {
        "gate": {
            "exit_code": exit_code,
            "message": message,
            "reasons": mismatch_reasons,
            "status": gate_status,
        },
        "mode": mode,
        "node": _node_version(),
        "packages": _installed_packages(),
        "python": {
            "implementation": platform.python_implementation(),
            "version": ".".join(str(part) for part in sys.version_info[:3]),
        },
        "report_schema": REPORT_SCHEMA,
        "scope": "p0.1-runtime-versions-only",
        "sqlite": {
            "approved": {
                "source_id": APPROVED_SQLITE_SOURCE_ID,
                "version": APPROVED_SQLITE_VERSION,
            },
            "exact_match": exact_match,
            "runtime": sqlite_runtime,
        },
    }
    return report, exit_code


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print deterministic runtime version JSON without reading app data."
    )
    parser.add_argument(
        "--mode",
        choices=("development", "production"),
        default="development",
        help="Development warns on an SQLite mismatch; production exits 2.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    report, exit_code = build_report(args.mode)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

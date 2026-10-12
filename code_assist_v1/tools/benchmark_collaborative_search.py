"""Bounded F0-P4 search projection concurrency measurement."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sqlite3
import statistics
import time
from typing import Any, Sequence

from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.search_authority import (
    CollaborativeSearchAuthority,
)


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return round(ordered[index], 3)


def _select_workspace(catalog: Path, subject_id: str) -> str:
    con = sqlite3.connect(catalog)
    try:
        row = con.execute(
            "SELECT workspace.id,COUNT(document.id) AS document_count "
            "FROM workspaces workspace "
            "JOIN workspace_memberships membership ON membership.workspace_id=workspace.id "
            "AND membership.subject_id=? AND membership.revoked_at IS NULL "
            "LEFT JOIN workspace_collaboration_documents document "
            "ON document.workspace_id=workspace.id AND document.archived=0 "
            "GROUP BY workspace.id ORDER BY document_count DESC,workspace.id LIMIT 1",
            (subject_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise RuntimeError("subject has no visible workspace membership")
    return str(row[0])


def measure(
    catalog: Path,
    user_data_root: Path,
    subject_id: str,
    *,
    workspace_id: str = "",
    request_count: int = 100,
    active_workers: int = 30,
    index_write_count: int = 10,
) -> dict[str, Any]:
    if request_count < 1 or active_workers < 1 or not 0 <= index_write_count <= request_count:
        raise ValueError("valid bounded load dimensions required")
    authority = CollaborativeSearchAuthority.open(
        catalog,
        user_data_root,
        InMemoryIdentityDirectory((subject_id,)),
    )
    workspace = workspace_id or _select_workspace(catalog, subject_id)
    authority.search(subject_id, workspace, limit=100)
    operations = ["rebuild"] * index_write_count + ["search"] * (
        request_count - index_write_count
    )

    def execute(kind: str) -> tuple[str, float, str]:
        started = time.perf_counter()
        try:
            if kind == "rebuild":
                authority.rebuild_search_index(subject_id, workspace)
            else:
                authority.search(subject_id, workspace, limit=100)
            return kind, (time.perf_counter() - started) * 1000, ""
        except Exception as exc:  # reported fail-closed below
            return kind, (time.perf_counter() - started) * 1000, repr(exc)

    started = time.perf_counter()
    results: list[tuple[str, float, str]] = []
    with ThreadPoolExecutor(max_workers=active_workers) as executor:
        futures = [executor.submit(execute, operation) for operation in operations]
        for future in as_completed(futures):
            results.append(future.result())
    elapsed = time.perf_counter() - started
    errors = [message for _kind, _duration, message in results if message]
    latencies = [duration for _kind, duration, message in results if not message]
    latency_by_kind = {
        kind: [
            duration
            for observed_kind, duration, message in results
            if observed_kind == kind and not message
        ]
        for kind in ("search", "rebuild")
    }
    converged = authority.search(subject_id, workspace, limit=1)["index"]
    busy_errors = sum("locked" in message.lower() or "busy" in message.lower() for message in errors)
    return {
        "contract": "code_assist.notion.collaborative_search_benchmark.v1",
        "status": "pass" if not errors and converged.get("converged") is True else "fail",
        "workspace_id": workspace,
        "request_count": request_count,
        "active_workers": active_workers,
        "search_read_count": request_count - index_write_count,
        "index_write_count": index_write_count,
        "success_count": len(latencies),
        "error_count": len(errors),
        "sqlite_busy_count": busy_errors,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_per_second": round(len(latencies) / elapsed, 3) if elapsed else 0.0,
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3) if latencies else 0.0,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
        "latency_by_kind_ms": {
            kind: {
                "p50": _percentile(values, 0.50),
                "p95": _percentile(values, 0.95),
                "p99": _percentile(values, 0.99),
                "max": round(max(values), 3) if values else 0.0,
            }
            for kind, values in latency_by_kind.items()
        },
        "desired_generation": int(converged["desired_generation"]),
        "built_generation": int(converged["built_generation"]),
        "converged": bool(converged["converged"]),
        "errors": errors[:10],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--user-data-root", type=Path, required=True)
    parser.add_argument("--subject-id", required=True)
    parser.add_argument("--workspace-id", default="")
    parser.add_argument("--request-count", type=int, default=100)
    parser.add_argument("--active-workers", type=int, default=30)
    parser.add_argument("--index-write-count", type=int, default=10)
    args = parser.parse_args(argv)
    report = measure(
        args.catalog,
        args.user_data_root,
        args.subject_id,
        workspace_id=args.workspace_id,
        request_count=args.request_count,
        active_workers=args.active_workers,
        index_write_count=args.index_write_count,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())

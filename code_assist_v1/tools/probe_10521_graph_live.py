"""Exercise the isolated 10521 publication and graph-rename path over HTTP.

This is a one-shot validation probe. It writes only through the application's
authenticated APIs and reads the 10521 SQLite database in read-only mode.
Credentials are supplied through environment variables, never stored here.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

import requests


BASE_URL = "http://127.0.0.1:10521"
DATA_ROOT = Path(__file__).resolve().parents[2] / "data-root"
DB_PATH = DATA_ROOT / "core" / "knowledge_core.db"


def _expect(response: requests.Response, *statuses: int) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise AssertionError(f"HTTP {response.status_code}: non-JSON response") from exc
    if response.status_code not in statuses:
        raise AssertionError(f"HTTP {response.status_code}: {payload}")
    return payload


def _login(user_id: str, password: str) -> tuple[requests.Session, str]:
    session = requests.Session()
    payload = _expect(
        session.post(
            f"{BASE_URL}/api/code/auth/login",
            json={"user_id": user_id, "password": password},
            headers={"Origin": BASE_URL},
            timeout=15,
        ),
        200,
    )
    return session, payload["csrf_token"]


def _headers(csrf_token: str) -> dict[str, str]:
    return {
        "Origin": BASE_URL,
        "X-CSRF-Token": csrf_token,
        "X-Request-ID": str(uuid4()),
    }


def _publish(
    session: requests.Session, csrf_token: str, title: str, markdown: str
) -> dict:
    created = _expect(
        session.post(
            f"{BASE_URL}/api/knowledge/v1/publication-previews",
            json={
                "operation_id": str(uuid4()),
                "source_type": "manual",
                "title": title,
                "markdown": markdown,
            },
            headers=_headers(csrf_token),
            timeout=30,
        ),
        200,
        202,
    )
    preview_id = created["preview_id"]
    for _ in range(20):
        ready = _expect(
            session.get(
                f"{BASE_URL}/api/knowledge/v1/publication-previews/{preview_id}",
                timeout=15,
            ),
            200,
        )
        if ready["status"] == "ready":
            break
        if ready["status"] in {"failed", "cancelled"}:
            raise AssertionError(f"Preview did not become ready: {ready}")
        time.sleep(0.5)
    else:
        raise AssertionError(f"Preview remained pending: {preview_id}")
    committed = _expect(
        session.post(
            f"{BASE_URL}/api/knowledge/v1/publications",
            json={
                "operation_id": str(uuid4()),
                "preview_id": preview_id,
                "source_snapshot_sha256": ready["source_snapshot_sha256"],
                "target_mode": "part",
            },
            headers=_headers(csrf_token),
            timeout=30,
        ),
        201,
    )
    assert len(committed["results"]) == 1
    return committed["results"][0]


def _read_db() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _snapshot() -> dict:
    connection = _read_db()
    try:
        tables = (
            "knowledge_item",
            "knowledge_edge",
            "aggregate_knowledge_edge",
            "semantic_edge_intent",
            "durable_job",
        )
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }
        states = [
            dict(row)
            for row in connection.execute(
                "SELECT job_type, state, COUNT(*) AS count FROM durable_job "
                "GROUP BY job_type, state ORDER BY job_type, state"
            )
        ]
        intents = [
            dict(row)
            for row in connection.execute(
                "SELECT provenance_kind, target_kind, COUNT(*) AS count "
                "FROM semantic_edge_intent GROUP BY provenance_kind, target_kind "
                "ORDER BY provenance_kind, target_kind"
            )
        ]
        return {"counts": counts, "job_states": states, "intents": intents}
    finally:
        connection.close()


def _graph(session: requests.Session, space_id: str) -> dict:
    graph = _expect(
        session.get(f"{BASE_URL}/api/knowledge/v1/spaces/{space_id}/graph", timeout=15),
        200,
    )
    return {
        "node_ids": sorted(row["id"] for row in graph["nodes"]),
        "edges": [
            {
                "source_item_id": row["source_item_id"],
                "target_item_id": row["target_item_id"],
                "edge_scope": row["edge_scope"],
                "edge_type": row["edge_type"],
                "origin": row["origin"],
            }
            for row in graph["edges"]
        ],
    }


def _organization(session: requests.Session) -> dict:
    return _expect(session.get(f"{BASE_URL}/api/admin/v1/org/tree", timeout=15), 200)["root"]


def _rename(
    session: requests.Session, csrf_token: str, unit: dict, suffix: str
) -> dict:
    return _expect(
        session.patch(
            f"{BASE_URL}/api/admin/v1/org/units/{unit['id']}",
            json={
                "operation_id": str(uuid4()),
                "expected_row_version": unit["row_version"],
                "name": f"{unit['name']} {suffix}",
            },
            headers=_headers(csrf_token),
            timeout=30,
        ),
        200,
    )


def _wait_for_jobs(initial: int) -> dict:
    latest = _snapshot()
    for _ in range(40):
        jobs = latest["job_states"]
        pending = sum(
            row["count"]
            for row in jobs
            if row["state"] not in {"succeeded", "failed", "cancelled"}
        )
        if latest["counts"]["durable_job"] > initial and pending == 0:
            return latest
        time.sleep(0.5)
        latest = _snapshot()
    return latest


def main() -> None:
    assert DB_PATH.is_file(), DB_PATH
    if _snapshot()["counts"]["knowledge_item"] > 1:
        raise RuntimeError(
            "The one-shot graph probe requires a fresh 10521 corpus; "
            "refusing to duplicate validation documents or rename units again."
        )
    admin_password = os.environ["SB10521_ADMIN_PASSWORD"]
    user_password = os.environ["SB10521_USER01_PASSWORD"]
    admin, admin_csrf = _login("admin", admin_password)
    user01, user_csrf = _login("user01", user_password)
    admin_spaces = _expect(
        admin.get(f"{BASE_URL}/api/knowledge/v1/spaces", timeout=15), 200
    )["items"]
    user_spaces = _expect(
        user01.get(f"{BASE_URL}/api/knowledge/v1/spaces", timeout=15), 200
    )["items"]
    admin_part_space = next(row["id"] for row in admin_spaces if row["kind"] == "part")
    user_part_space = next(row["id"] for row in user_spaces if row["kind"] == "part")
    team_space = next(row["id"] for row in admin_spaces if row["kind"] == "team")
    assert admin_part_space != user_part_space
    start = _snapshot()
    published = {
        "part_a_related": _publish(
            admin,
            admin_csrf,
            "10521 graph validation AMHS PLC restore",
            "# AMHS PLC restore\n\nThis synthetic AMHS conveyor controller recovery "
            "protocol isolates the failed PLC, restores the latest verified "
            "snapshot, and validates motor current telemetry. The 8.2 A "
            "sensor threshold and 09:00 recovery window match the companion "
            "recovery controller note.\n",
        ),
        "part_a_unrelated": _publish(
            admin,
            admin_csrf,
            "10521 graph validation Japanese soup preparation",
            "# Japanese soup preparation\n\nThis synthetic culinary note describes "
            "kombu, bonito broth, 75 C steeping, and a low-salt tasting panel. "
            "It contains no industrial controller recovery instructions.\n",
        ),
        "part_b_related": _publish(
            user01,
            user_csrf,
            "10521 graph validation AMHS telemetry verification",
            "# AMHS telemetry verification\n\nThis synthetic research note describes "
            "conveyor PLC recovery after a failed controller, the verified "
            "snapshot restore, and motor current telemetry validation against "
            "the 8.2 A threshold during the 09:00 recovery window.\n",
        ),
    }
    assert published["part_a_related"]["space_id"] == admin_part_space
    assert published["part_a_unrelated"]["space_id"] == admin_part_space
    assert published["part_b_related"]["space_id"] == user_part_space
    before_rename = _wait_for_jobs(start["counts"]["durable_job"])
    graphs_before = {
        "part_a": _graph(admin, admin_part_space),
        "part_b": _graph(user01, user_part_space),
        "team": _graph(admin, team_space),
    }
    tree = _organization(admin)
    team = tree["children"][0]
    part_a = next(
        row for row in team["children"]
        if row["leader"] is not None and row["leader"]["user_id"] == "admin"
    )
    rename_part = _rename(admin, admin_csrf, part_a, "Renamed")
    after_part = _wait_for_jobs(before_rename["counts"]["durable_job"])
    admin, admin_csrf = _login("admin", admin_password)
    tree_after_part = _organization(admin)
    team_after_part = tree_after_part["children"][0]
    rename_team = _rename(admin, admin_csrf, team_after_part, "Renamed")
    after_team = _wait_for_jobs(after_part["counts"]["durable_job"])
    admin, admin_csrf = _login("admin", admin_password)
    user01, user_csrf = _login("user01", user_password)
    graphs_after = {
        "part_a": _graph(admin, admin_part_space),
        "part_b": _graph(user01, user_part_space),
        "team": _graph(admin, team_space),
    }
    print(
        json.dumps(
            {
                "published": published,
                "start": start,
                "before_rename": before_rename,
                "rename_part": rename_part,
                "after_part": after_part,
                "rename_team": rename_team,
                "after_team": after_team,
                "graphs_before": graphs_before,
                "graphs_after": graphs_after,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

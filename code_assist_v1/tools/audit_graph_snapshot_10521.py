"""Read-only structural audit of a copied 10521 graph database.

This intentionally does not judge whether an edge is *semantically* correct.
Reviewers must compare each edge's two source documents independently.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3


def audit(database: Path) -> dict[str, object]:
    if not database.is_file():
        raise ValueError("The graph audit database does not exist.")
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        if [row[0] for row in connection.execute("PRAGMA quick_check")] != ["ok"]:
            raise AssertionError("SQLite quick_check failed.")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise AssertionError("SQLite foreign_key_check failed.")

        spaces = {
            row["id"]: dict(row)
            for row in connection.execute(
                "SELECT id,space_kind,owner_org_unit_id FROM knowledge_space"
            )
        }
        units = {
            row["id"]: dict(row)
            for row in connection.execute("SELECT id,kind,parent_id FROM org_unit")
        }
        items = {
            row["id"]: dict(row)
            for row in connection.execute(
                "SELECT id,space_id,current_revision_id FROM knowledge_item"
            )
        }
        bodies = {
            row["revision_id"]: row["content_md"]
            for row in connection.execute(
                "SELECT revision_id,content_md FROM knowledge_revision_body"
            )
        }
        failures: list[str] = []
        pair_types: dict[tuple[str, str, str], set[str]] = {}
        counts = {"local_active": 0, "aggregate_active": 0,
                  "current_revision_spans": 0, "cross_part_only_at_team": 0}

        def check_span(edge: sqlite3.Row, side: str, revision_id: str) -> None:
            location = json.loads(edge[f"{side}_evidence_location_json"])
            body = bodies.get(revision_id)
            if (
                not isinstance(location, dict)
                or location.get("revision_id") != revision_id
                or location.get("unit") != "unicode_codepoint"
                or type(location.get("start")) is not int
                or type(location.get("end")) is not int
                or body is None
                or not 0 <= location["start"] < location["end"] <= len(body)
            ):
                failures.append(f"{edge['id']}:{side}:invalid_location")
                return
            actual = sha256(body[location["start"]:location["end"]].encode("utf-8")).hexdigest()
            if actual != edge[f"{side}_evidence_span_sha256"]:
                failures.append(f"{edge['id']}:{side}:span_hash_mismatch")
                return
            counts["current_revision_spans"] += 1

        def endpoint(edge: sqlite3.Row, side: str, *, aggregate: bool) -> None:
            item = items.get(edge[f"{side}_item_id"])
            revision = edge[f"{side}_revision_id"] if aggregate else edge[f"{side}_evidence_revision_id"]
            if item is None or item["current_revision_id"] != revision:
                failures.append(f"{edge['id']}:{side}:stale_revision")
                return
            if aggregate and item["space_id"] != edge[f"{side}_space_id"]:
                failures.append(f"{edge['id']}:{side}:space_mismatch")
            check_span(edge, side, revision)

        for edge in connection.execute(
            "SELECT * FROM knowledge_edge WHERE edge_status='active'"
        ):
            counts["local_active"] += 1
            source = items.get(edge["source_item_id"])
            target = items.get(edge["target_item_id"])
            if (
                source is None or target is None
                or source["space_id"] != edge["space_id"]
                or target["space_id"] != edge["space_id"]
                or edge["needs_revalidation"] != 0
            ):
                failures.append(f"{edge['id']}:invalid_local_scope_or_state")
            endpoint(edge, "source", aggregate=False)
            endpoint(edge, "target", aggregate=False)
            pair = (edge["space_id"], edge["source_item_id"], edge["target_item_id"])
            pair_types.setdefault(pair, set()).add(edge["edge_type"])

        for edge in connection.execute(
            "SELECT * FROM aggregate_knowledge_edge WHERE edge_status='active'"
        ):
            counts["aggregate_active"] += 1
            source_space = spaces.get(edge["source_space_id"])
            target_space = spaces.get(edge["target_space_id"])
            home_space = spaces.get(edge["home_space_id"])
            home_unit = units.get(edge["home_org_unit_id"])
            if (
                source_space is None or target_space is None or home_space is None
                or home_unit is None
                or source_space["space_kind"] != "part"
                or target_space["space_kind"] != "part"
                or edge["source_space_id"] == edge["target_space_id"]
                or home_space["space_kind"] != "team"
                or home_space["owner_org_unit_id"] != edge["home_org_unit_id"]
                or home_unit["kind"] != "team"
                or units.get(source_space["owner_org_unit_id"], {}).get("parent_id") != edge["home_org_unit_id"]
                or units.get(target_space["owner_org_unit_id"], {}).get("parent_id") != edge["home_org_unit_id"]
            ):
                failures.append(f"{edge['id']}:invalid_aggregate_scope")
            else:
                counts["cross_part_only_at_team"] += 1
            endpoint(edge, "source", aggregate=True)
            endpoint(edge, "target", aggregate=True)

        return {
            "database_sha256": sha256(database.read_bytes()).hexdigest(),
            "schema_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "item_count": len(items),
            **counts,
            "local_pairs_with_multiple_edge_types": sum(len(types) > 1 for types in pair_types.values()),
            "structural_failures": failures,
            "semantic_review": "separate_required",
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="A separate SQLite backup, never the live 10521 DB")
    args = parser.parse_args()
    result = audit(args.database)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["structural_failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

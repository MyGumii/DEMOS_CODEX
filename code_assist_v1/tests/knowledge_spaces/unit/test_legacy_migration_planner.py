from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import builtins
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping, Sequence

import pytest

from code_assist_v1.knowledge_core.legacy.plan_contract import (
    DECISION_TABLE,
    ORGANIZATION_MAPPING_HEADER,
    canonical_json_bytes as canonical_plan_json_bytes,
    plan_contract_errors,
)
from code_assist_v1.knowledge_core.legacy.planner import (
    MigrationPlanError,
    build_migration_plan_draft,
    build_plan_draft,
    confirm_migration_plan,
    parse_inventory_document,
    parse_organization_mapping_csv,
)
from code_assist_v1.tools.legacy_inventory import inventory_snapshot
from code_assist_v1.tools.legacy_inventory_contract import (
    canonical_json_bytes as canonical_inventory_json_bytes,
    inventory_contract_errors,
)


_RESPONSIBILITY_ID = "11111111-1111-4111-8111-111111111111"
_TEAM_ID = "22222222-2222-4222-8222-222222222222"
_PART_ID = "33333333-3333-4333-8333-33333333333a"
_OTHER_PART_ID = "44444444-4444-4444-8444-444444444444"
_ATTESTATION = "a" * 64
_SECRET = "TOP_SECRET_SENTINEL_P2_2_NEVER_EMIT"


def _mkdirs(tmp_path: Path) -> dict[str, Any]:
    roots: dict[str, Any] = {
        "legacy": tmp_path / "legacy",
        "knowledge": [tmp_path / "knowledge-a", tmp_path / "knowledge-b"],
        "runtime": tmp_path / "runtime",
        "user_data": tmp_path / "user-data",
        "sessions": tmp_path / "sessions",
        "workspace": tmp_path / "workspace",
        "scratch": tmp_path / "scratch",
    }
    for path in (
        roots["legacy"],
        *roots["knowledge"],
        roots["runtime"],
        roots["user_data"],
        roots["sessions"],
        roots["workspace"],
        roots["scratch"],
    ):
        path.mkdir()
    return roots


def _probe(path: str) -> dict[str, Any]:
    return {
        "eligible": True,
        "exists": True,
        "reason_codes": [],
        "resolved_path": str(Path(path).resolve(strict=True)),
        "syntactic_unc": False,
        "volume": {"drive_type": "fixed", "filesystem": "NTFS", "root": "T:\\"},
    }


def _coverage(_: Path) -> dict[str, Any]:
    files = [{"language": "python", "path": "fixture.py", "sha256": "a" * 64}]
    callsites = []
    for index in range(1, 9):
        callee = f"fixture.submit.{index}"
        identity = json.dumps(
            ["fixture.py", "fixture", callee, 1],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        callsites.append(
            {
                "access": "submit",
                "callee": callee,
                "callsite_id": "callsite-"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                "column": 0,
                "disposition": "migrate",
                "file": "fixture.py",
                "file_sha256": "a" * 64,
                "kind": "job_submit",
                "line": index,
                "mapping_id": f"fixture-map-{index}",
                "ordinal": 1,
                "reason": "sanitized test coverage",
                "replacement_phase": "P2",
                "scope_owner": "fixture",
                "symbol": "fixture",
            }
        )
    callsites.sort(key=lambda row: row["callsite_id"])
    callsite_ids_sha256 = hashlib.sha256(
        json.dumps(
            sorted(row["callsite_id"] for row in callsites),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    source_fingerprint_sha256 = hashlib.sha256(
        json.dumps(
            [["fixture.py", "a" * 64]],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "callsites": callsites,
        "errors": [],
        "files": files,
        "mapping": {
            "callsite_ids_sha256": callsite_ids_sha256,
            "expected_callsite_ids_sha256": callsite_ids_sha256,
            "expected_submitter_count": 8,
            "path": "fixture-map.json",
            "schema_version": 1,
            "sha256": "b" * 64,
            "unused_mapping_ids": [],
        },
        "report_schema": "code_assist.legacy_inventory.code_coverage.v1",
        "source_fingerprint_sha256": source_fingerprint_sha256,
        "status": "complete",
        "summary": {
            "discovered": 8,
            "mapped": 8,
            "retained_excluded": 0,
            "unclassified": 0,
            "submitter_count": 8,
            "unmapped_submitters": 0,
            "pass": True,
        },
        "unclassified_callsite_ids": [],
        "unmapped_submitter_ids": [],
    }


def _clock() -> datetime:
    return datetime(2026, 8, 13, 1, 2, 3, tzinfo=timezone.utc)


def _filesystem_state(roots: Mapping[str, Any]) -> dict[str, tuple[int, int, str | None]]:
    result: dict[str, tuple[int, int, str | None]] = {}
    all_roots = [
        roots["legacy"],
        *roots["knowledge"],
        roots["runtime"],
        roots["user_data"],
        roots["sessions"],
        roots["workspace"],
    ]
    for root in all_roots:
        for path in [root, *root.rglob("*")]:
            stat = path.stat(follow_symlinks=False)
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            result[f"{root.name}/{path.relative_to(root).as_posix()}"] = (
                stat.st_size,
                stat.st_mtime_ns,
                digest,
            )
    return result


def _mapping_row(
    *,
    claim: str = "",
    record_id: str = "",
    decision: str = "personal",
    user_id: str = "admin",
    role: str = "admin",
    status: str = "active",
    assigned: bool = False,
    leader: bool = False,
    responsibility_code: str = "AMHS",
    responsibility_id: str = _RESPONSIBILITY_ID,
    team_code: str = "TEAM_A",
    team_id: str = _TEAM_ID,
    part_code: str = "PART_A",
    part_id: str = _PART_ID,
) -> list[str]:
    if decision == "quarantine":
        user_id = role = status = ""
        assigned = leader = False
    return [
        "1",
        claim,
        record_id,
        decision,
        user_id,
        role,
        status,
        responsibility_code if assigned else "",
        responsibility_id if assigned else "",
        team_code if assigned else "",
        team_id if assigned else "",
        part_code if assigned else "",
        part_id if assigned else "",
        "true" if leader else "false",
    ]


def _csv_bytes(
    rows: Sequence[Sequence[str]],
    *,
    header: Sequence[str] = ORGANIZATION_MAPPING_HEADER,
) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, dialect="excel", lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _base_mapping_rows(*, assigned: bool = False) -> list[list[str]]:
    return [
        _mapping_row(
            claim="admin",
            user_id="admin",
            role="admin",
            assigned=assigned,
            leader=assigned,
        ),
        _mapping_row(
            claim="user01",
            user_id="user01",
            role="user",
            assigned=assigned,
        ),
    ]


def _complete_mapping_rows(
    fixture: Mapping[str, Any],
    rows: Sequence[Sequence[str]],
) -> list[list[str]]:
    report = fixture["report"]
    complete_rows = [list(row) for row in rows]
    selected_record_ids = {row[2] for row in complete_rows if row[2]}
    explicitly_promoted_record_ids = {
        row[2] for row in complete_rows if row[2] and row[3] == "personal"
    }
    explicit_categories = {
        "knowledge.legacy_flat_markdown",
        "knowledge.legacy_thoughts",
        "knowledge.legacy_inbox",
        "knowledge.legacy_raw",
        "knowledge.legacy_raw_chunks",
        "conversation.user_sessions",
        "external.notion_session_links",
        "operations.jobs",
    }
    for record in report["records"]:
        owner = record.get("owner")
        is_unowned = (
            not isinstance(owner, Mapping)
            or owner.get("kind") != "personal"
            or owner.get("mapping_status") != "confirmed"
        )
        requires_record_disposition = (
            record["category"] == "conversation.global_sessions"
            or (
                is_unowned
                and record["proposed_terminal_classification"] == "quarantined"
                and record["category"] in explicit_categories
            )
        )
        if requires_record_disposition and record["record_id"] not in selected_record_ids:
            complete_rows.append(
                _mapping_row(record_id=record["record_id"], decision="quarantine")
            )
            selected_record_ids.add(record["record_id"])
        if record["record_id"] in explicitly_promoted_record_ids:
            parent_id = record.get("parent_record_id")
            if isinstance(parent_id, str) and parent_id not in selected_record_ids:
                promoted = next(
                    row
                    for row in complete_rows
                    if row[2] == record["record_id"] and row[3] == "personal"
                )
                complete_rows.append(
                    _mapping_row(
                        record_id=parent_id,
                        user_id=promoted[4],
                        role=promoted[5],
                        status=promoted[6],
                    )
                )
                selected_record_ids.add(parent_id)
    return complete_rows


def _plan_from_fixture(
    fixture: Mapping[str, Any],
    rows: Sequence[Sequence[str]],
) -> dict[str, Any]:
    report = fixture["report"]
    complete_rows = _complete_mapping_rows(fixture, rows)
    mapping_bytes = _csv_bytes(complete_rows)
    return build_plan_draft(
        fixture["inventory_bytes"],
        mapping_bytes,
        expected_inventory_manifest_sha256=report["manifest_sha256"],
        expected_snapshot_attestation_sha256=report["source_snapshot"][
            "attestation_sha256"
        ],
        expected_root_binding_sha256=report["source_snapshot"][
            "root_binding_sha256"
        ],
        expected_organization_mapping_document_sha256=hashlib.sha256(
            mapping_bytes
        ).hexdigest(),
        allow_data_quality_errors=True,
    )


@pytest.fixture(scope="module")
def planner_fixture(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("p2-2-planner")
    roots = _mkdirs(root)
    (roots["runtime"] / "users.json").write_text(
        json.dumps(
            {
                "users": {
                    "admin": {
                        "disabled": False,
                        "password_hash": _SECRET,
                        "role": "admin",
                    },
                    "user01": {
                        "disabled": False,
                        "password_hash": _SECRET + "-USER",
                        "role": "user",
                    },
                }
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    admin_wiki = roots["knowledge"][0] / "admin" / "wiki"
    (admin_wiki / "sources").mkdir(parents=True)
    (admin_wiki / "sources" / "secret.md").write_text(
        f"# sanitized\n{_SECRET}\n![asset](../assets/asset.bin)\n",
        encoding="utf-8",
    )
    (admin_wiki / "sources" / "other.md").write_text("# other\n", encoding="utf-8")
    (admin_wiki / "assets").mkdir()
    (admin_wiki / "assets" / "asset.bin").write_bytes(b"sanitized-asset")
    (admin_wiki / "graph").mkdir()
    (admin_wiki / "graph" / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "filename": "secret.md",
                        "id": "source/secret.md",
                        "rel_path": "sources/secret.md",
                    },
                    {
                        "filename": "other.md",
                        "id": "source/other.md",
                        "rel_path": "sources/other.md",
                    },
                ],
                "edges": [
                    {
                        "source": "source/secret.md",
                        "target": "source/other.md",
                        "edge_type": "related",
                    },
                    {
                        "source": "source/secret.md",
                        "target": "source/other.md",
                        "edge_type": "requires",
                    }
                ],
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (admin_wiki / "index.md").write_text("derived index", encoding="utf-8")
    (roots["knowledge"][0] / "admin" / "flat.md").write_text(
        "# legacy flat\n", encoding="utf-8"
    )
    admin_inbox = roots["knowledge"][0] / "admin" / "inbox"
    admin_inbox.mkdir(parents=True)
    (admin_inbox / "valid.json").write_text(
        '{"id":"valid","status":"pending"}', encoding="utf-8"
    )
    (admin_inbox / "malformed.json").write_text(
        '{"unterminated":', encoding="utf-8"
    )
    admin_raw = roots["knowledge"][0] / "admin" / "raw"
    admin_raw.mkdir(parents=True)
    (admin_raw / "valid.txt").write_text("legacy text\n", encoding="utf-8")
    (admin_raw / "unsupported.bin").write_bytes(b"legacy binary")
    (admin_wiki / "graph" / "pending_diffs.json").write_text(
        json.dumps(
            [
                {
                    "candidate_kind": "graph_candidate",
                    "action": "add_edge",
                    "status": "pending",
                    "source": {"source_id": "source/secret.md"},
                    "target_id": "source/other.md",
                    "base_revision": 1,
                },
                {
                    "candidate_kind": "graph_candidate",
                    "action": "add_edge",
                    "status": "rejected",
                    "source": {"source_id": "source/secret.md"},
                    "target_id": "source/other.md",
                    "base_revision": 1,
                },
            ],
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    bad_source = roots["knowledge"][0] / "user01" / "wiki" / "sources" / "bad.md"
    bad_source.parent.mkdir(parents=True)
    bad_source.write_bytes(b"\xff\xfe")
    unowned = roots["knowledge"][0] / "raw_chunks" / "unowned.txt"
    unowned.parent.mkdir(parents=True)
    unowned.write_text(_SECRET, encoding="utf-8")
    global_session = roots["sessions"] / "global-session.json"
    global_session.write_text(
        json.dumps(
            {
                "id": "global-session",
                "messages": [{"role": "user", "content": _SECRET}],
            }
        ),
        encoding="utf-8",
    )
    memory = roots["user_data"] / "admin" / "memory" / "memories.jsonl"
    memory.parent.mkdir(parents=True)
    memory.write_text(json.dumps({"content": _SECRET}) + "\n", encoding="utf-8")
    jobs = roots["user_data"] / "admin" / "jobs"
    jobs.mkdir()
    (jobs / "queued.json").write_text(
        '{"id":"queued","kind":"outlook_sync","status":"queued","user_id":"admin"}',
        encoding="utf-8",
    )
    (jobs / "done.json").write_text(
        '{"id":"done","kind":"outlook_sync","status":"succeeded","user_id":"admin"}',
        encoding="utf-8",
    )
    notion = roots["user_data"] / "admin" / "notion"
    notion.mkdir(parents=True)
    (notion / "notion.db.bak_20260813_000000").write_bytes(b"sanitized backup")

    before = _filesystem_state(roots)
    report, exit_code = inventory_snapshot(
        roots["legacy"],
        roots["knowledge"],
        "sanitized-p2-2-snapshot",
        runtime_root=roots["runtime"],
        user_data_root=roots["user_data"],
        sessions_root=roots["sessions"],
        workspace_root=roots["workspace"],
        snapshot_kind="frozen_copy",
        snapshot_created_at_utc="2026-08-13T00:00:00.000000Z",
        snapshot_attestation_sha256=_ATTESTATION,
        scratch_root=roots["scratch"],
        allow_partial=True,
        clock=_clock,
        code_coverage_provider=_coverage,
        root_probe=_probe,
    )
    assert exit_code == 0, report["errors"]
    assert inventory_contract_errors(report) == ()
    assert _filesystem_state(roots) == before
    inventory_bytes = canonical_inventory_json_bytes(report) + b"\n"
    assert _SECRET.encode() not in inventory_bytes
    return {
        "before": before,
        "inventory_bytes": inventory_bytes,
        "report": report,
        "roots": roots,
    }


def _expect_error(code: str, action: Callable[[], Any]) -> None:
    with pytest.raises(MigrationPlanError) as caught:
        action()
    assert caught.value.code == code
    assert str(caught.value) == code
    assert _SECRET not in str(caught.value)


@pytest.mark.unit
def test_build_plan_is_deterministic_sorted_hash_bound_and_secret_free(
    planner_fixture: Mapping[str, Any],
) -> None:
    rows = _base_mapping_rows(assigned=True)
    first = _plan_from_fixture(planner_fixture, rows)
    second = _plan_from_fixture(planner_fixture, rows)

    assert first == second
    assert plan_contract_errors(first) == ()
    assert [row["record_id"] for row in first["records"]] == sorted(
        row["record_id"] for row in first["records"]
    )
    assert [row["mapping_id"] for row in first["owner_mappings"]] == sorted(
        row["mapping_id"] for row in first["owner_mappings"]
    )
    assert [row["relationship_id"] for row in first["relationships"]] == sorted(
        row["relationship_id"] for row in first["relationships"]
    )
    assert first["reconciliation"]["total_records"] == len(first["records"])
    assert first["reconciliation"]["unclassified_records"] == 0
    assert first["reconciliation"]["target_part_record_count"] == 0
    assert all(row["target_space_kind"] != "part" for row in first["records"])
    assert all(
        row["target_space_kind"] in {None, "personal"}
        for row in first["records"]
    )
    encoded = canonical_plan_json_bytes(first)
    assert _SECRET.encode() not in encoded
    assert _filesystem_state(planner_fixture["roots"]) == planner_fixture["before"]


@pytest.mark.unit
def test_planner_resolves_only_frozen_valid_personal_content_adapters(
    planner_fixture: Mapping[str, Any],
) -> None:
    plan = _plan_from_fixture(planner_fixture, _base_mapping_rows())
    by_path = {
        str(row["source_record"]["relative_path"]): row for row in plan["records"]
    }

    for suffix in ("admin/flat.md", "admin/inbox/valid.json", "admin/raw/valid.txt"):
        row = next(value for path, value in by_path.items() if path.endswith(suffix))
        assert row["action"] == "import"
        assert row["execution_phase"] == "P2.3"
        assert row["import_mode"] == "personal_item"
        assert row["final_terminal_classification"] == "imported"
        assert "reviewed_legacy_personal_content_adapter" in row["reason_codes"]

    for suffix in ("admin/inbox/malformed.json", "admin/raw/unsupported.bin"):
        row = next(value for path, value in by_path.items() if path.endswith(suffix))
        assert row["action"] == "quarantine"
        assert row["execution_phase"] == "none"
        assert row["import_mode"] is None
        assert row["final_terminal_classification"] == "quarantined"
    assert plan_contract_errors(plan) == ()


@pytest.mark.unit
def test_planner_imports_only_canonical_graph_edges_and_pending_candidates(
    planner_fixture: Mapping[str, Any],
) -> None:
    plan = _plan_from_fixture(planner_fixture, _base_mapping_rows())
    graph_rows = [
        row
        for row in plan["records"]
        if row["category"] == "knowledge.wiki_graph_edges"
        and row["source_record"]["record_kind"] == "logical_record"
    ]
    by_type = {
        row["source_record"]["graph_edge"]["relation_type"]: row
        for row in graph_rows
    }
    assert by_type["related"]["action"] == "import"
    assert by_type["related"]["import_mode"] == "graph_edge"
    assert by_type["requires"]["action"] == "quarantine"
    assert by_type["requires"]["import_mode"] is None
    requires_relationship = next(
        row
        for row in plan["relationships"]
        if row["source_relationship"]["relationship_type"] == "requires"
    )
    assert requires_relationship["action"] == "quarantine"

    candidates = [
        row
        for row in plan["records"]
        if row["category"] == "knowledge.wiki_candidates"
        and row["source_record"]["record_kind"] == "logical_record"
    ]
    by_status = {row["source_record"]["candidate"]["status"]: row for row in candidates}
    assert by_status["pending"]["action"] == "import", json.dumps(
        by_status["pending"], indent=2
    )
    assert by_status["pending"]["import_mode"] == "finding"
    assert by_status["rejected"]["action"] == "quarantine"
    assert by_status["rejected"]["import_mode"] is None
    candidate_container = next(
        row
        for row in plan["records"]
        if row["category"] == "knowledge.wiki_candidates"
        and row["source_record"]["record_kind"] == "physical_file"
    )
    assert candidate_container["action"] == "quarantine"
    assert candidate_container["import_mode"] is None
    assert plan_contract_errors(plan) == ()


@pytest.mark.unit
def test_csv_row_order_changes_only_raw_document_binding_and_plan_hash(
    planner_fixture: Mapping[str, Any],
) -> None:
    forward_rows = _base_mapping_rows(assigned=True)
    reverse_rows = list(reversed(forward_rows))
    forward_bytes = _csv_bytes(forward_rows)
    reverse_bytes = _csv_bytes(reverse_rows)
    forward_mappings = parse_organization_mapping_csv(
        forward_bytes,
        expected_document_sha256=hashlib.sha256(forward_bytes).hexdigest(),
    )
    reverse_mappings = parse_organization_mapping_csv(
        reverse_bytes,
        expected_document_sha256=hashlib.sha256(reverse_bytes).hexdigest(),
    )
    forward = _plan_from_fixture(planner_fixture, forward_rows)
    reverse = _plan_from_fixture(planner_fixture, reverse_rows)

    assert forward_mappings == reverse_mappings
    for key in ("organization", "owner_mappings", "records", "relationships"):
        assert forward[key] == reverse[key]
    assert (
        forward["inputs"]["organization_mapping"]["document_sha256"]
        != reverse["inputs"]["organization_mapping"]["document_sha256"]
    )
    assert forward["technical_plan_sha256"] != reverse["technical_plan_sha256"]


@pytest.mark.unit
def test_inventory_parser_rejects_stale_or_malformed_bound_inputs(
    planner_fixture: Mapping[str, Any],
) -> None:
    report = planner_fixture["report"]
    data = planner_fixture["inventory_bytes"]
    digest = hashlib.sha256(data).hexdigest()
    parsed = parse_inventory_document(
        data,
        expected_document_sha256=digest,
        expected_manifest_sha256=report["manifest_sha256"],
        expected_snapshot_attestation_sha256=_ATTESTATION,
        expected_root_binding_sha256=report["source_snapshot"]["root_binding_sha256"],
        allow_data_quality_errors=True,
    )
    assert parsed == report

    bad_cases = [
        (b"\xef\xbb\xbf" + data, digest),
        (b'{"stage":NaN}', hashlib.sha256(b'{"stage":NaN}').hexdigest()),
        (
            b'{"report_schema":"a","report_schema":"b"}',
            hashlib.sha256(b'{"report_schema":"a","report_schema":"b"}').hexdigest(),
        ),
        (data + b" ", digest),
    ]
    for malformed, expected_document in bad_cases:
        _expect_error(
            "INVENTORY_INVALID",
            lambda malformed=malformed, expected_document=expected_document: parse_inventory_document(
                malformed,
                expected_document_sha256=expected_document,
                expected_manifest_sha256=report["manifest_sha256"],
                expected_snapshot_attestation_sha256=_ATTESTATION,
                expected_root_binding_sha256=report["source_snapshot"]["root_binding_sha256"],
                allow_data_quality_errors=True,
            ),
        )

    for key, value in (
        ("expected_manifest_sha256", "f" * 64),
        ("expected_snapshot_attestation_sha256", "e" * 64),
        ("expected_root_binding_sha256", "d" * 64),
    ):
        arguments = {
            "expected_document_sha256": digest,
            "expected_manifest_sha256": report["manifest_sha256"],
            "expected_snapshot_attestation_sha256": _ATTESTATION,
            "expected_root_binding_sha256": report["source_snapshot"]["root_binding_sha256"],
            "allow_data_quality_errors": True,
        }
        arguments[key] = value
        _expect_error(
            "INVENTORY_INVALID",
            lambda arguments=arguments: parse_inventory_document(data, **arguments),
        )


@pytest.mark.unit
def test_incomplete_inventory_requires_explicit_data_quality_override(
    planner_fixture: Mapping[str, Any],
) -> None:
    report = planner_fixture["report"]
    if report["complete"] is True:
        pytest.skip("fixture happened to contain no data-quality finding")
    _expect_error(
        "INVENTORY_INVALID",
        lambda: parse_inventory_document(
            planner_fixture["inventory_bytes"],
            expected_document_sha256=hashlib.sha256(
                planner_fixture["inventory_bytes"]
            ).hexdigest(),
            expected_manifest_sha256=report["manifest_sha256"],
            expected_snapshot_attestation_sha256=_ATTESTATION,
            expected_root_binding_sha256=report["source_snapshot"][
                "root_binding_sha256"
            ],
            allow_data_quality_errors=False,
        ),
    )


@pytest.mark.unit
def test_csv_accepts_explicit_unassigned_disabled_and_leaderless_users() -> None:
    rows = [
        _mapping_row(claim="admin", user_id="admin", role="admin"),
        _mapping_row(
            claim="disabled01",
            user_id="disabled01",
            role="user",
            status="disabled",
        ),
        _mapping_row(
            claim="member01",
            user_id="member01",
            role="user",
            assigned=True,
            leader=False,
        ),
    ]
    data = _csv_bytes(rows)
    parsed = parse_organization_mapping_csv(
        data, expected_document_sha256=hashlib.sha256(data).hexdigest()
    )

    disabled = next(row for row in parsed if row["user_id"] == "disabled01")
    part = next(row for row in parsed if row["user_id"] == "member01")
    assert disabled["part_id"] is None
    assert disabled["is_part_leader"] is False
    assert part["part_id"] == _PART_ID
    assert part["is_part_leader"] is False


def _duplicate_claim_case(rows: list[list[str]]) -> bytes:
    rows.append(_mapping_row(claim="ADMIN", user_id="admin", role="admin"))
    return _csv_bytes(rows)


def _both_selector_case(rows: list[list[str]]) -> bytes:
    rows[0][2] = "rec_" + "1" * 24
    return _csv_bytes(rows)


def _code_to_id_collision_case(rows: list[list[str]]) -> bytes:
    rows = _base_mapping_rows(assigned=True)
    rows[1][10] = "55555555-5555-4555-8555-555555555555"
    return _csv_bytes(rows)


def _id_to_code_collision_case(rows: list[list[str]]) -> bytes:
    rows = _base_mapping_rows(assigned=True)
    rows[1][9] = "TEAM_OTHER"
    return _csv_bytes(rows)


def _multiple_leader_case(rows: list[list[str]]) -> bytes:
    rows = _base_mapping_rows(assigned=True)
    rows[1][-1] = "true"
    return _csv_bytes(rows)


def _same_user_conflict_case(rows: list[list[str]]) -> bytes:
    rows = [
        _mapping_row(claim="admin", user_id="admin", role="admin"),
        _mapping_row(
            record_id="rec_" + "1" * 24,
            user_id="user01",
            role="user",
        ),
        _mapping_row(
            record_id="rec_" + "2" * 24,
            user_id="user01",
            role="admin",
        ),
    ]
    return _csv_bytes(rows)


def _schema_version_case(rows: list[list[str]]) -> bytes:
    rows = [list(row) for row in rows]
    rows[0][0] = "2"
    return _csv_bytes(rows)


@pytest.mark.unit
@pytest.mark.parametrize(
    "make_invalid",
    [
        lambda rows: b"\xef\xbb\xbf" + _csv_bytes(rows),
        lambda rows: _csv_bytes(rows, header=tuple(reversed(ORGANIZATION_MAPPING_HEADER))),
        lambda rows: _csv_bytes(rows) + b"extra,column\r\n",
        lambda rows: _csv_bytes(rows).replace(b"admin", b" admin", 1),
        lambda rows: _csv_bytes(rows).replace(b"admin", "\uff41dmin".encode("utf-8"), 1),
        lambda rows: _csv_bytes(rows).replace(b"admin", b"ad\x00min", 1),
        lambda rows: _csv_bytes(rows).replace(b"admin", "ad\u200bmin".encode("utf-8"), 1),
        _schema_version_case,
        _duplicate_claim_case,
        _both_selector_case,
        lambda rows: _csv_bytes(rows).replace(_PART_ID.encode(), _PART_ID.upper().encode()),
        _code_to_id_collision_case,
        _id_to_code_collision_case,
        _multiple_leader_case,
        _same_user_conflict_case,
        lambda rows: _csv_bytes(
            [_mapping_row(claim="user01", user_id="user01", role="user")]
        ),
        lambda rows: _csv_bytes([]),
    ],
)
def test_csv_parser_rejects_header_bom_controls_nfkc_duplicates_and_collisions(
    make_invalid: Callable[[list[list[str]]], bytes],
) -> None:
    data = make_invalid(_base_mapping_rows(assigned=True))
    _expect_error(
        "ORGANIZATION_MAPPING_INVALID",
        lambda: parse_organization_mapping_csv(
            data, expected_document_sha256=hashlib.sha256(data).hexdigest()
        ),
    )


@pytest.mark.unit
def test_csv_parser_rejects_document_hash_drift_and_malformed_utf8() -> None:
    data = _csv_bytes(_base_mapping_rows())
    _expect_error(
        "ORGANIZATION_MAPPING_INVALID",
        lambda: parse_organization_mapping_csv(
            data, expected_document_sha256="f" * 64
        ),
    )
    malformed = data + b"\xff"
    _expect_error(
        "ORGANIZATION_MAPPING_INVALID",
        lambda: parse_organization_mapping_csv(
            malformed,
            expected_document_sha256=hashlib.sha256(malformed).hexdigest(),
        ),
    )


@pytest.mark.unit
def test_mapping_must_cover_every_inventory_account_and_match_role_status(
    planner_fixture: Mapping[str, Any],
) -> None:
    missing_user = [_base_mapping_rows()[0]]
    _expect_error(
        "ORGANIZATION_MAPPING_INVALID",
        lambda: _plan_from_fixture(planner_fixture, missing_user),
    )

    role_mismatch = _base_mapping_rows()
    role_mismatch[1][5] = "admin"
    _expect_error(
        "ORGANIZATION_MAPPING_INVALID",
        lambda: _plan_from_fixture(planner_fixture, role_mismatch),
    )

    status_mismatch = _base_mapping_rows()
    status_mismatch[1][6] = "disabled"
    _expect_error(
        "ORGANIZATION_MAPPING_INVALID",
        lambda: _plan_from_fixture(planner_fixture, status_mismatch),
    )


@pytest.mark.unit
def test_explicit_reviewed_role_resolves_pre_role_legacy_account(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    (roots["runtime"] / "users.json").write_text(
        json.dumps(
            {
                "users": {
                    "admin": {
                        "disabled": False,
                        "password_hash": _SECRET,
                    }
                }
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    report, exit_code = inventory_snapshot(
        roots["legacy"],
        roots["knowledge"],
        "sanitized-pre-role-snapshot",
        runtime_root=roots["runtime"],
        user_data_root=roots["user_data"],
        sessions_root=roots["sessions"],
        workspace_root=roots["workspace"],
        snapshot_kind="frozen_copy",
        snapshot_created_at_utc="2026-08-13T00:00:00.000000Z",
        snapshot_attestation_sha256=_ATTESTATION,
        scratch_root=roots["scratch"],
        allow_partial=False,
        clock=_clock,
        code_coverage_provider=_coverage,
        root_probe=_probe,
    )

    assert exit_code == 0, report["errors"]
    assert inventory_contract_errors(report) == ()
    registry_child = next(
        row
        for row in report["records"]
        if row["category"] == "identity.users_registry"
        and row["record_kind"] == "logical_record"
    )
    assert registry_child["parse"]["registry_account"]["role"] == (
        "unassigned_legacy"
    )

    inventory_bytes = canonical_inventory_json_bytes(report) + b"\n"
    mapping_bytes = _csv_bytes(
        [_mapping_row(claim="admin", user_id="admin", role="admin")]
    )
    draft = build_plan_draft(
        inventory_bytes,
        mapping_bytes,
        expected_inventory_manifest_sha256=report["manifest_sha256"],
        expected_snapshot_attestation_sha256=_ATTESTATION,
        expected_root_binding_sha256=report["source_snapshot"][
            "root_binding_sha256"
        ],
        expected_organization_mapping_document_sha256=hashlib.sha256(
            mapping_bytes
        ).hexdigest(),
    )

    planned = next(
        row for row in draft["records"] if row["record_id"] == registry_child["record_id"]
    )
    assert planned["action"] == "import"
    assert planned["import_mode"] == "user_account"
    assert planned["resolved_owner_user_id"] == "admin"
    assert plan_contract_errors(draft) == ()


@pytest.mark.unit
def test_record_override_beats_claim_and_unowned_sources_stay_quarantined(
    planner_fixture: Mapping[str, Any],
) -> None:
    report = planner_fixture["report"]
    baseline = _plan_from_fixture(planner_fixture, _base_mapping_rows())
    global_session = next(
        row
        for row in report["records"]
        if row["category"] == "conversation.global_sessions"
        and row["record_kind"] == "physical_file"
    )
    unowned = next(
        row
        for row in report["records"]
        if row["category"] == "knowledge.legacy_raw_chunks"
        and row["owner"]["kind"] != "personal"
    )
    admin_source = next(
        row
        for row in report["records"]
        if row["category"] == "knowledge.wiki_sources"
        and row["owner"]["user_id"] == "admin"
    )
    baseline_by_id = {row["record_id"]: row for row in baseline["records"]}
    assert baseline_by_id[global_session["record_id"]]["action"] == "quarantine"
    assert baseline_by_id[unowned["record_id"]]["action"] == "quarantine"
    assert baseline_by_id[admin_source["record_id"]]["action"] == "import"

    rows = _base_mapping_rows()
    rows.extend(
        [
            _mapping_row(
                record_id=global_session["record_id"],
                user_id="admin",
                role="admin",
            ),
            _mapping_row(
                record_id=unowned["record_id"],
                user_id="admin",
                role="admin",
            ),
            _mapping_row(
                record_id=admin_source["record_id"],
                decision="quarantine",
            ),
        ]
    )
    overridden = _plan_from_fixture(planner_fixture, rows)
    by_id = {row["record_id"]: row for row in overridden["records"]}
    for source in (global_session, unowned):
        planned = by_id[source["record_id"]]
        assert planned["action"] == "quarantine"
        assert planned["resolved_owner_user_id"] == "admin"
        assert planned["target_space_kind"] is None
        assert planned["owner_mapping_id"] is not None
    assert all(
        row["action"] == "quarantine"
        for row in overridden["records"]
        if row["source_record"].get("parent_record_id") == global_session["record_id"]
    )
    quarantined = by_id[admin_source["record_id"]]
    assert quarantined["action"] == "quarantine"
    assert quarantined["resolved_owner_user_id"] is None
    assert quarantined["target_space_kind"] is None
    assert "owner_mapping_quarantine" in quarantined["reason_codes"]


@pytest.mark.unit
def test_scanner_fixed_rebuild_retain_quarantine_and_unavailable_decisions_survive(
    planner_fixture: Mapping[str, Any],
) -> None:
    plan = _plan_from_fixture(planner_fixture, _base_mapping_rows(assigned=True))
    by_category: dict[str, set[tuple[str, str, str]]] = {}
    for row in plan["records"]:
        by_category.setdefault(row["category"], set()).add(
            (
                row["action"],
                row["execution_phase"],
                row["final_terminal_classification"],
            )
        )

    assert ("rebuild", "P2.6", "rebuilt") in by_category[
        "knowledge.wiki_index_markdown"
    ]
    assert ("retain_external", "none", "retained_external") in by_category[
        "context.memory"
    ]
    assert ("quarantine", "none", "quarantined") in by_category[
        "external.notion_backup_candidates"
    ]
    assert ("record_unavailable", "none", "unavailable") in by_category[
        "unavailable.browser_pins"
    ]
    table = {row["category"]: row for row in DECISION_TABLE}
    for row in plan["records"]:
        fixed = table[row["category"]]
        proposed = row["source_record"]["proposed_terminal_classification"]
        if proposed in {"rebuilt", "retained_external", "unavailable"}:
            assert row["action"] == fixed["action"]
            assert row["execution_phase"] == fixed["execution_phase"]


@pytest.mark.unit
def test_relationship_and_legacy_job_routes_are_explicit_and_non_replayable(
    planner_fixture: Mapping[str, Any],
) -> None:
    plan = _plan_from_fixture(planner_fixture, _base_mapping_rows())
    relationship_routes = {
        row["source_relationship"]["relationship_type"]: (
            row["action"],
            row["execution_phase"],
            row["import_mode"],
        )
        for row in plan["relationships"]
    }
    assert relationship_routes["asset_reference"] == (
        "import",
        "P2.3",
        "personal_asset",
    )
    assert relationship_routes["graph_node_source"] == (
        "import",
        "P2.4",
        "graph_node",
    )
    assert relationship_routes["related"] == (
        "import",
        "P2.4",
        "graph_edge",
    )

    jobs = [row for row in plan["records"] if row["category"] == "operations.jobs"]
    assert {row["import_mode"] for row in jobs} == {
        "abandoned_legacy_job",
        "job_history",
    }
    abandoned = next(row for row in jobs if row["import_mode"] == "abandoned_legacy_job")
    assert abandoned["action"] == "import"
    assert abandoned["execution_phase"] == "P2.5"
    assert "legacy_nonterminal_job_abandoned" in abandoned["reason_codes"]


@pytest.mark.unit
def test_part_mapping_is_assertion_only_and_never_routes_legacy_knowledge_to_part(
    planner_fixture: Mapping[str, Any],
) -> None:
    plan = _plan_from_fixture(planner_fixture, _base_mapping_rows(assigned=True))

    assert plan["organization"]["part_units"] == [
        {
            "code": "PART_A",
            "id": _PART_ID,
            "leader_user_id": "admin",
            "team_id": _TEAM_ID,
        }
    ]
    knowledge_imports = [
        row
        for row in plan["records"]
        if row["category"].startswith("knowledge.") and row["action"] == "import"
    ]
    assert knowledge_imports
    assert {row["target_space_kind"] for row in knowledge_imports} == {"personal"}
    assert plan["reconciliation"]["target_part_record_count"] == 0


@pytest.mark.unit
def test_planner_transformation_opens_no_file_or_database_and_mutates_no_input(
    planner_fixture: Mapping[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    report = planner_fixture["report"]
    inventory = deepcopy(report)
    mapping_bytes = _csv_bytes(
        _complete_mapping_rows(planner_fixture, _base_mapping_rows(assigned=True))
    )
    mappings = parse_organization_mapping_csv(
        mapping_bytes,
        expected_document_sha256=hashlib.sha256(mapping_bytes).hexdigest(),
    )
    inventory_before = deepcopy(inventory)
    mappings_before = deepcopy(mappings)

    def forbidden(*_: Any, **__: Any) -> Any:
        raise AssertionError("planner attempted external I/O")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    plan = build_migration_plan_draft(
        inventory,
        mappings,
        inventory_document_sha256=hashlib.sha256(
            planner_fixture["inventory_bytes"]
        ).hexdigest(),
        organization_mapping_document_sha256=hashlib.sha256(mapping_bytes).hexdigest(),
    )

    assert plan_contract_errors(plan) == ()
    assert inventory == inventory_before
    assert mappings == mappings_before
    monkeypatch.undo()
    assert _filesystem_state(planner_fixture["roots"]) == planner_fixture["before"]


@pytest.mark.unit
def test_confirm_plan_binds_exact_draft_document_and_rejects_stale_review(
    planner_fixture: Mapping[str, Any],
) -> None:
    draft = _plan_from_fixture(planner_fixture, _base_mapping_rows(assigned=True))
    draft_bytes = canonical_plan_json_bytes(draft) + b"\n"
    draft_sha = hashlib.sha256(draft_bytes).hexdigest()
    reviewed = confirm_migration_plan(
        draft_bytes,
        expected_draft_sha256=draft_sha,
        review_attestation_sha256="c" * 64,
        reviewed_at_utc="2026-08-13T02:03:04.000000Z",
        reviewer_id="migration-reviewer",
    )

    assert plan_contract_errors(reviewed) == ()
    assert reviewed["draft"] == draft
    assert (
        reviewed["review"]["confirmed_technical_plan_sha256"]
        == draft["technical_plan_sha256"]
    )
    assert _SECRET.encode() not in canonical_plan_json_bytes(reviewed)

    _expect_error(
        "REVIEW_INVALID",
        lambda: confirm_migration_plan(
            draft_bytes,
            expected_draft_sha256="d" * 64,
            review_attestation_sha256="c" * 64,
            reviewed_at_utc="2026-08-13T02:03:04.000000Z",
            reviewer_id="migration-reviewer",
        ),
    )
    _expect_error(
        "REVIEW_INVALID",
        lambda: confirm_migration_plan(
            draft_bytes + b" ",
            expected_draft_sha256=draft_sha,
            review_attestation_sha256="c" * 64,
            reviewed_at_utc="2026-08-13T02:03:04.000000Z",
            reviewer_id="migration-reviewer",
        ),
    )

    tampered = deepcopy(draft)
    tampered["records"][0]["reason_codes"] = ["tampered"]
    tampered_bytes = canonical_plan_json_bytes(tampered) + b"\n"
    _expect_error(
        "PLAN_INVALID",
        lambda: confirm_migration_plan(
            tampered_bytes,
            expected_draft_sha256=hashlib.sha256(tampered_bytes).hexdigest(),
            review_attestation_sha256="c" * 64,
            reviewed_at_utc="2026-08-13T02:03:04.000000Z",
            reviewer_id="migration-reviewer",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("reviewed_at", "reviewer", "attestation"),
    [
        ("2026-02-31T02:03:04.000000Z", "migration-reviewer", "c" * 64),
        ("2026-08-13T02:03:04Z", "migration-reviewer", "c" * 64),
        ("2026-08-13T02:03:04.000000+00:00", "migration-reviewer", "c" * 64),
        ("2026-08-13T02:03:04.000000Z", " reviewer", "c" * 64),
        ("2026-08-13T02:03:04.000000Z", "review\nname", "c" * 64),
        ("2026-08-13T02:03:04.000000Z", "migration-reviewer", "C" * 64),
    ],
)
def test_confirm_plan_rejects_noncanonical_review_metadata(
    planner_fixture: Mapping[str, Any],
    reviewed_at: str,
    reviewer: str,
    attestation: str,
) -> None:
    draft = _plan_from_fixture(planner_fixture, _base_mapping_rows())
    draft_bytes = canonical_plan_json_bytes(draft) + b"\n"
    _expect_error(
        "REVIEW_INVALID",
        lambda: confirm_migration_plan(
            draft_bytes,
            expected_draft_sha256=hashlib.sha256(draft_bytes).hexdigest(),
            review_attestation_sha256=attestation,
            reviewed_at_utc=reviewed_at,
            reviewer_id=reviewer,
        ),
    )

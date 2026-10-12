from __future__ import annotations

from datetime import datetime, timezone
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
from ctypes import wintypes

import pytest

from code_assist_v1.tools.legacy_inventory import inventory_snapshot
from code_assist_v1.tools import legacy_inventory
from code_assist_v1.tools.legacy_inventory_contract import (
    CATEGORY_IDS,
    HARD_ERROR_CODES,
    TERMINAL_CLASSIFICATIONS,
    has_hard_errors,
    inventory_contract_errors,
    manifest_sha256,
    manifest_payload,
    sha256_canonical,
)


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


def _register_fixture_user(roots: dict[str, Any]) -> None:
    (roots["runtime"] / "users.json").write_text(
        '{"users":{"fixture-user":{"disabled":false,"password_hash":"fixture-hash","role":"user"}}}',
        encoding="utf-8",
    )


def _clock() -> datetime:
    return datetime(2026, 8, 12, 1, 2, 3, tzinfo=timezone.utc)


def _run(roots: dict[str, Any], *, allow_partial: bool = False) -> tuple[dict[str, Any], int]:
    return inventory_snapshot(
        roots["legacy"],
        roots["knowledge"],
        "sanitized-snapshot",
        runtime_root=roots["runtime"],
        user_data_root=roots["user_data"],
        sessions_root=roots["sessions"],
        workspace_root=roots["workspace"],
        snapshot_kind="frozen_copy",
        snapshot_created_at_utc="2026-08-12T00:00:00.000000Z",
        snapshot_attestation_sha256="a" * 64,
        scratch_root=roots["scratch"],
        allow_partial=allow_partial,
        clock=_clock,
        code_coverage_provider=_coverage,
        root_probe=_probe,
    )


def _rehash_snapshot_binding_and_manifest(report: dict[str, Any]) -> None:
    snapshot = report["source_snapshot"]
    snapshot["root_binding_sha256"] = sha256_canonical(
        {
            "attestation_sha256": snapshot["attestation_sha256"],
            "created_at_utc": snapshot["created_at_utc"],
            "kind": snapshot["kind"],
            "roots": sorted(snapshot["roots"], key=lambda row: row["root_id"]),
            "scratch_root": snapshot["scratch_root"],
            "snapshot_id": snapshot["snapshot_id"],
        }
    )
    report["manifest_sha256"] = manifest_sha256(report)


def _valid_asset_relationship_report(tmp_path: Path) -> dict[str, Any]:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    wiki = roots["knowledge"][0] / "fixture-user" / "wiki"
    source = wiki / "sources" / "source.md"
    asset = wiki / "assets" / "asset.bin"
    source.parent.mkdir(parents=True)
    asset.parent.mkdir(parents=True)
    source.write_text("![asset](../assets/asset.bin)\n", encoding="utf-8")
    asset.write_bytes(b"sanitized-asset")

    report, exit_code = _run(roots)

    assert exit_code == 0, report["errors"]
    assert inventory_contract_errors(report) == ()
    assert any(
        row.get("relationship_type") == "asset_reference"
        and row.get("status") == "valid"
        for row in report["relationships"]
    )
    return report


def _state(roots: dict[str, Any]) -> dict[str, tuple[int, int, str | None]]:
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


@pytest.mark.unit
def test_empty_snapshot_is_complete_fixed_and_deterministic(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    before = _state(roots)

    first, first_exit = _run(roots)
    second, second_exit = _run(roots)

    assert first_exit == second_exit == 0
    assert first["complete"] is True
    assert {row["category"] for row in first["categories"]} == CATEGORY_IDS
    assert [row["category"] for row in first["categories"]] == sorted(CATEGORY_IDS)
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert manifest_payload(first) == manifest_payload(second)
    assert first["manifest_sha256"] == manifest_sha256(first)
    assert _state(roots) == before


@pytest.mark.unit
def test_contract_rejects_impossible_snapshot_time_after_full_rehash(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    report, exit_code = _run(roots)
    assert exit_code == 0
    assert inventory_contract_errors(report) == ()

    snapshot = report["source_snapshot"]
    snapshot["created_at_utc"] = "2026-02-31T00:00:00.000000Z"
    _rehash_snapshot_binding_and_manifest(report)

    errors = inventory_contract_errors(report)
    assert errors == ("source_snapshot_shape",)


@pytest.mark.unit
def test_contract_rejects_nested_category_extra_after_manifest_rehash(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    report, exit_code = _run(roots)
    assert exit_code == 0
    assert inventory_contract_errors(report) == ()

    report["categories"][0]["safe_extra"] = {
        "nested": {"value": "sanitized"}
    }
    report["manifest_sha256"] = manifest_sha256(report)

    errors = inventory_contract_errors(report)
    assert errors == ("category_shape",)


@pytest.mark.unit
def test_contract_binds_run_provenance_and_chronology(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    report, exit_code = _run(roots)
    assert exit_code == 0
    assert inventory_contract_errors(report) == ()

    completed_before_start = json.loads(json.dumps(report))
    completed_before_start["run"][
        "completed_at_utc"
    ] = "2026-08-12T01:02:02.000000Z"
    assert inventory_contract_errors(completed_before_start) == (
        "run_chronology",
    )

    snapshot_after_start = json.loads(json.dumps(report))
    snapshot_after_start["source_snapshot"][
        "created_at_utc"
    ] = "2026-08-12T02:00:00.000000Z"
    _rehash_snapshot_binding_and_manifest(snapshot_after_start)
    assert inventory_contract_errors(snapshot_after_start) == (
        "source_snapshot_chronology",
    )

    mismatched_run_id = json.loads(json.dumps(report))
    mismatched_run_id["run"]["run_id"] = "run_" + "f" * 24
    assert mismatched_run_id["run"]["run_id"] != report["run"]["run_id"]
    assert inventory_contract_errors(mismatched_run_id) == ("run_binding",)


@pytest.mark.unit
def test_schema_v2_reconciliation_covers_every_physical_and_logical_record(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    session = roots["user_data"] / "fixture-user" / "sessions" / "session-1.json"
    session.parent.mkdir(parents=True)
    session.write_text(
        '{"id":"session-1","messages":[{"role":"user","content":"private"}]}',
        encoding="utf-8",
    )

    report, exit_code = _run(roots)

    assert exit_code == 0, report["errors"]
    assert report["schema_version"] == 2
    reconciliation = report["reconciliation"]
    assert reconciliation["stage"] == "inventory_proposal"
    assert reconciliation["total_records"] == len(report["records"])
    assert reconciliation["proposed_classified_records"] == len(report["records"])
    assert reconciliation["unclassified_records"] == 0
    record_ids = {row["record_id"] for row in report["records"]}
    for row in report["records"]:
        assert row["proposed_terminal_classification"] in TERMINAL_CLASSIFICATIONS
        assert row["classification_authority"] in {"planner_required", "scanner"}
        if row["entry_type"] == "logical_record":
            assert row["parent_record_id"] in record_ids
    assert sum(row["records"] for row in report["categories"]) == len(
        report["records"]
    )
    assert inventory_contract_errors(report) == ()

    invalid_kind = json.loads(json.dumps(report))
    invalid_kind["records"][0]["record_kind"] = "special"
    assert "record_kind" in inventory_contract_errors(
        invalid_kind, verify_manifest=False
    )

    invalid_relationship = json.loads(json.dumps(report))
    invalid_relationship["relationships"].append(
        {
            "from_record_id": "rec_missing",
            "relationship_id": "rel_contract_probe",
            "relationship_type": "contract_probe",
            "status": "invalid",
            "to_record_id": next(iter(record_ids)),
        }
    )
    assert "relationship_endpoint" in inventory_contract_errors(
        invalid_relationship, verify_manifest=False
    )

    invalid_category = json.loads(json.dumps(report))
    invalid_category["categories"][0]["aggregate_sha256"] = "0" * 64
    assert "category_aggregate_hash" in inventory_contract_errors(
        invalid_category, verify_manifest=False
    )

    invalid_action = json.loads(json.dumps(report))
    invalid_action["records"][0]["planned_action"] = "delete_source"
    assert "record_action" in inventory_contract_errors(
        invalid_action, verify_manifest=False
    )

    invalid_relationship_shape = json.loads(json.dumps(report))
    invalid_relationship_shape["relationships"].append(
        {
            "relationship_id": "rel_endpointless",
            "relationship_type": "asset_reference",
            "status": "valid",
        }
    )
    contract_errors = inventory_contract_errors(
        invalid_relationship_shape, verify_manifest=False
    )
    assert "relationship_valid_endpoints" in contract_errors
    assert "relationship_source" in contract_errors
    assert "relationship_target" in contract_errors

    duplicate_source_identity = json.loads(json.dumps(report))
    first_record, second_record = duplicate_source_identity["records"][:2]
    for field in ("root_id", "path_key", "source_ordinal"):
        second_record[field] = first_record[field]
    second_record["source_identity"] = dict(first_record["source_identity"])
    assert "record_source_identity_duplicate" in inventory_contract_errors(
        duplicate_source_identity, verify_manifest=False
    )

    tampered_snapshot = json.loads(json.dumps(report))
    tampered_snapshot["source_snapshot"]["snapshot_id"] = "different-snapshot"
    assert "source_root_binding" in inventory_contract_errors(
        tampered_snapshot, verify_manifest=False
    )

    tampered_root_identity = json.loads(json.dumps(report))
    tampered_root_identity["source_snapshot"]["roots"][0][
        "filesystem_identity"
    ]["inode"] += 1
    assert "source_root_binding" in inventory_contract_errors(
        tampered_root_identity, verify_manifest=False
    )

    tampered_owner_aggregate = json.loads(json.dumps(report))
    tampered_owner_aggregate["owners"][0]["record_count"] += 1
    assert "owners_record_count" in inventory_contract_errors(
        tampered_owner_aggregate, verify_manifest=False
    )

    tampered_owner_paths = json.loads(json.dumps(report))
    tampered_owner_paths["owners"][0]["source_paths"] = []
    assert "owners_source_paths" in inventory_contract_errors(
        tampered_owner_paths, verify_manifest=False
    )


@pytest.mark.unit
def test_contract_rejects_semantic_duplicate_and_cross_owner_valid_relationships(
    tmp_path: Path,
) -> None:
    report = _valid_asset_relationship_report(tmp_path)
    relationship = next(
        row
        for row in report["relationships"]
        if row["relationship_type"] == "asset_reference"
        and row["status"] == "valid"
    )

    semantic_duplicate = json.loads(json.dumps(report))
    duplicate = dict(relationship)
    duplicate["relationship_id"] += "-alternate-id"
    semantic_duplicate["relationships"].append(duplicate)
    assert "relationship_duplicate" in inventory_contract_errors(
        semantic_duplicate, verify_manifest=False
    )

    cross_owner = json.loads(json.dumps(report))
    target = next(
        row
        for row in cross_owner["records"]
        if row["record_id"] == relationship["to_record_id"]
    )
    target["owner"] = {
        "claimed_user_id": "different-owner",
        "kind": "personal",
        "mapping_status": "confirmed",
        "user_id": "different-owner",
    }
    assert "relationship_owner_direction" in inventory_contract_errors(
        cross_owner, verify_manifest=False
    )


@pytest.mark.unit
def test_contract_closes_graph_asset_session_and_job_reconciliation_views(
    tmp_path: Path,
) -> None:
    graph_root = tmp_path / "graph"
    graph_root.mkdir()
    graph_roots = _mkdirs(graph_root)
    _register_fixture_user(graph_roots)
    wiki = graph_roots["knowledge"][0] / "fixture-user" / "wiki"
    sources = wiki / "sources"
    sources.mkdir(parents=True)
    (sources / "a.md").write_text("# A\n", encoding="utf-8")
    (sources / "b.md").write_text("# B\n", encoding="utf-8")
    graph = wiki / "graph" / "graph.json"
    graph.parent.mkdir(parents=True)
    graph.write_text(
        json.dumps(
            {
                "nodes": [{"id": "source/a.md"}, {"id": "source/b.md"}],
                "edges": [
                    {
                        "edge_type": "related",
                        "source": "source/a.md",
                        "target": "source/b.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    graph_report, graph_exit = _run(graph_roots)
    assert graph_exit == 0
    assert inventory_contract_errors(graph_report) == ()
    graph_tamper = json.loads(json.dumps(graph_report))
    graph_child = next(
        row
        for row in graph_tamper["records"]
        if row["category"] == "knowledge.wiki_graph_edges"
        and row["entry_type"] == "logical_record"
    )
    graph_tamper["relationships"] = [
        row
        for row in graph_tamper["relationships"]
        if row.get("source_record_id") != graph_child["record_id"]
    ]
    graph_tamper["summary"]["relationships"] = len(
        graph_tamper["relationships"]
    )
    assert "graph_edge_relationship_closure" in inventory_contract_errors(
        graph_tamper, verify_manifest=False
    )

    graph_evidence_omission = json.loads(json.dumps(graph_report))
    graph_parent = next(
        row
        for row in graph_evidence_omission["records"]
        if row["category"] == "knowledge.wiki_graph_edges"
        and row["record_kind"] == "physical_file"
    )
    del graph_parent["parse"]["graph"]
    assert "graph_parent_counts" in inventory_contract_errors(
        graph_evidence_omission, verify_manifest=False
    )

    graph_node_omission = json.loads(json.dumps(graph_report))
    graph_node = next(
        row
        for row in graph_node_omission["records"]
        if row["category"] == "knowledge.wiki_graph_nodes"
    )
    del graph_node["graph_node_resolution"]
    assert "graph_node_relationship_closure" in inventory_contract_errors(
        graph_node_omission, verify_manifest=False
    )

    asset_root = tmp_path / "asset"
    asset_root.mkdir()
    asset_report = _valid_asset_relationship_report(asset_root)
    asset_tamper = json.loads(json.dumps(asset_report))
    asset_tamper["relationships"] = [
        row
        for row in asset_tamper["relationships"]
        if row.get("relationship_type") != "asset_reference"
    ]
    asset_tamper["summary"]["relationships"] = len(
        asset_tamper["relationships"]
    )
    assert {
        "asset_action_closure",
        "asset_reference_closure",
    }.issubset(inventory_contract_errors(asset_tamper, verify_manifest=False))

    session_root = tmp_path / "session"
    session_root.mkdir()
    session_roots = _mkdirs(session_root)
    _register_fixture_user(session_roots)
    session = (
        session_roots["user_data"]
        / "fixture-user"
        / "sessions"
        / "session-a.json"
    )
    session.parent.mkdir(parents=True)
    session.write_text(
        '{"id":"session-a","messages":[{"role":"user","content":"private"}]}',
        encoding="utf-8",
    )
    session_report, session_exit = _run(session_roots)
    assert session_exit == 0
    assert inventory_contract_errors(session_report) == ()
    session_tamper = json.loads(json.dumps(session_report))
    message = next(
        row
        for row in session_tamper["records"]
        if row.get("child_kind") == "messages"
    )
    message["child_kind"] = "uploads"
    assert {
        "session_child_binding",
        "session_parent_closure",
    }.issubset(inventory_contract_errors(session_tamper, verify_manifest=False))

    session_evidence_omission = json.loads(json.dumps(session_report))
    session_parent = next(
        row
        for row in session_evidence_omission["records"]
        if row["category"] == "conversation.user_sessions"
        and row["record_kind"] == "physical_file"
    )
    del session_parent["parse"]["child_counts"]
    del session_parent["parse"]["child_aggregate_sha256"]
    assert "session_parent_closure" in inventory_contract_errors(
        session_evidence_omission, verify_manifest=False
    )

    job_root = tmp_path / "job"
    job_root.mkdir()
    job_roots = _mkdirs(job_root)
    _register_fixture_user(job_roots)
    job = job_roots["user_data"] / "fixture-user" / "jobs" / "done.json"
    job.parent.mkdir(parents=True)
    job.write_text(
        '{"id":"done","kind":"outlook_sync","status":"succeeded",'
        '"user_id":"fixture-user"}',
        encoding="utf-8",
    )
    job_report, job_exit = _run(job_roots)
    assert job_exit == 0
    assert inventory_contract_errors(job_report) == ()
    job_tamper = json.loads(json.dumps(job_report))
    job_record = next(
        row for row in job_tamper["records"] if row["category"] == "operations.jobs"
    )
    job_record["parse"]["source_status"] = "queued"
    job_tamper["summary"]["job_states"] = {"queued": 1}
    assert "job_semantic_closure" in inventory_contract_errors(
        job_tamper, verify_manifest=False
    )

    job_evidence_omission = json.loads(json.dumps(job_report))
    omitted_job = next(
        row
        for row in job_evidence_omission["records"]
        if row["category"] == "operations.jobs"
    )
    del omitted_job["parse"]["source_status"]
    del omitted_job["parse"]["job_identity"]
    job_evidence_omission["summary"]["job_states"] = {"unknown": 1}
    assert "job_semantic_closure" in inventory_contract_errors(
        job_evidence_omission, verify_manifest=False
    )


@pytest.mark.unit
def test_contract_freezes_owner_kind_and_mapping_combinations(tmp_path: Path) -> None:
    report = _valid_asset_relationship_report(tmp_path)
    tampered = json.loads(json.dumps(report))
    tampered["records"][0]["owner"] = {
        "claimed_user_id": None,
        "kind": "invented_owner_namespace",
        "mapping_status": "confirmed",
        "user_id": None,
    }
    assert "record_owner_shape" in inventory_contract_errors(
        tampered, verify_manifest=False
    )

    remapped_claim = json.loads(json.dumps(report))
    personal = next(
        row
        for row in remapped_claim["records"]
        if row["owner"]["kind"] == "personal"
    )
    personal["owner"]["claimed_user_id"] = "other-user"
    assert "record_owner_shape" in inventory_contract_errors(
        remapped_claim, verify_manifest=False
    )


@pytest.mark.unit
def test_contract_validator_is_total_for_unhashable_json_shaped_identities(
    tmp_path: Path,
) -> None:
    report = _valid_asset_relationship_report(tmp_path)
    mutations = {
        "owner": lambda value: value["records"][0]["owner"].__setitem__(
            "user_id", []
        ),
        "root": lambda value: value["source_snapshot"]["roots"][0].__setitem__(
            "root_id", []
        ),
        "category": lambda value: value["categories"][0].__setitem__(
            "category", []
        ),
        "endpoint": lambda value: value["relationships"][0].__setitem__(
            "from_record_id", []
        ),
    }

    for label, mutate in mutations.items():
        malformed = json.loads(json.dumps(report))
        mutate(malformed)
        errors = inventory_contract_errors(malformed, verify_manifest=False)
        assert isinstance(errors, tuple), label
        assert errors, label


@pytest.mark.unit
def test_contract_closes_code_coverage_shape_high_water_and_revision_evidence(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    report, exit_code = _run(roots)

    assert exit_code == 0
    assert inventory_contract_errors(report) == ()

    missing_files = json.loads(json.dumps(report))
    del missing_files["code_coverage"]["files"]
    assert "code_coverage_shape" in inventory_contract_errors(
        missing_files, verify_manifest=False
    )

    changed_fingerprint = json.loads(json.dumps(report))
    changed_fingerprint["code_coverage"]["source_fingerprint_sha256"] = "d" * 64
    fingerprint_errors = inventory_contract_errors(
        changed_fingerprint, verify_manifest=False
    )
    assert {"code_high_water_binding", "code_revision_binding"}.issubset(
        fingerprint_errors
    )

    changed_mapping = json.loads(json.dumps(report))
    changed_mapping["code_coverage"]["mapping"]["sha256"] = "e" * 64
    mapping_errors = inventory_contract_errors(
        changed_mapping, verify_manifest=False
    )
    assert {"code_high_water_binding", "code_revision_binding"}.issubset(
        mapping_errors
    )

    changed_runtime = json.loads(json.dumps(report))
    changed_runtime["runtime"]["app_revision"] = "f" * 64
    assert "code_revision_binding" in inventory_contract_errors(
        changed_runtime, verify_manifest=False
    )

    skipped = json.loads(json.dumps(report))
    skipped["code_coverage"] = {"status": "skipped"}
    assert "code_coverage_shape" in inventory_contract_errors(
        skipped, verify_manifest=False
    )

    changed_callsite = json.loads(json.dumps(report))
    changed_callsite["code_coverage"]["callsites"][0]["reason"] = ""
    assert "code_callsite_shape" in inventory_contract_errors(
        changed_callsite, verify_manifest=False
    )

    changed_kind = json.loads(json.dumps(report))
    changed_kind["code_coverage"]["callsites"][0]["kind"] = "invented_kind"
    assert "code_callsite_shape" in inventory_contract_errors(
        changed_kind, verify_manifest=False
    )

    loose_numeric_shape = json.loads(json.dumps(report))
    loose_numeric_shape["code_coverage"]["summary"]["unclassified"] = False
    loose_numeric_shape["code_coverage"]["mapping"][
        "expected_submitter_count"
    ] = 8.0
    assert "code_coverage_shape" in inventory_contract_errors(
        loose_numeric_shape, verify_manifest=False
    )


@pytest.mark.unit
def test_classifies_unowned_and_personal_raw_chunks_without_body_leak(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    secret = "source-body-token-never-report-1f4e"
    unowned = roots["knowledge"][0] / "raw_chunks" / "chunk.txt"
    personal = roots["knowledge"][0] / "fixture-user" / "raw_chunks" / "chunk.txt"
    unowned.parent.mkdir(parents=True)
    personal.parent.mkdir(parents=True)
    unowned.write_text(secret, encoding="utf-8")
    personal.write_text(secret, encoding="utf-8")

    report, exit_code = _run(roots)

    assert exit_code == 0
    records = [
        row for row in report["records"]
        if row["category"] == "knowledge.legacy_raw_chunks"
    ]
    assert len(records) == 2
    assert {row["owner"]["kind"] for row in records} == {"unknown", "personal"}
    assert secret not in json.dumps(report, ensure_ascii=False)


@pytest.mark.unit
def test_graph_quality_is_aggregated_and_enum_fields_cannot_leak_tokens(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    wiki = roots["knowledge"][0] / "fixture-user" / "wiki"
    sources = wiki / "sources"
    sources.mkdir(parents=True)
    (sources / "a.md").write_text("# A\n", encoding="utf-8")
    (sources / "b.md").write_text("# B\n", encoding="utf-8")
    graph = wiki / "graph" / "graph.json"
    graph.parent.mkdir(parents=True)
    secret_type = "TOKENISHSECRET_123456789"
    graph.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "filename": "a.md",
                        "id": "source/a.md",
                        "rel_path": "sources/a.md",
                    },
                    {
                        "filename": "b.md",
                        "id": "source/b.md",
                        "rel_path": "sources/b.md",
                    },
                ],
                "edges": [
                    {
                        "source": "source/a.md",
                        "target": "source/b.md",
                        "edge_type": "related",
                    },
                    {
                        "source": "source/a.md",
                        "target": "source/b.md",
                        "edge_type": "related",
                    },
                    {
                        "source": "source/a.md",
                        "target": "missing",
                        "edge_type": secret_type,
                    },
                    {
                        "source": "source/b.md",
                        "target": "source/a.md",
                        "edge_type": "related",
                        "metadata": {"auto_graph": True},
                    },
                ],
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    report, exit_code = _run(roots, allow_partial=True)

    assert exit_code == 0
    assert report["complete"] is False
    assert "UNKNOWN_ENTRY" in {row["code"] for row in report["errors"]}
    container = next(
        row
        for row in report["records"]
        if row["relative_path"].endswith("graph/graph.json")
        and row["entry_type"] == "file"
    )
    assert container["parse"]["graph"] == {
        "duplicate": 1,
        "edges": 4,
        "legacy_auto": 1,
        "nodes": 2,
        "orphan": 1,
        "valid": 1,
    }
    graph_relationships = [
        row for row in report["relationships"]
        if row["relationship_type"] in {"related", "unknown"}
    ]
    assert {row["status"] for row in graph_relationships} == {
        "duplicate", "legacy_auto", "orphan", "valid"
    }
    assert secret_type not in json.dumps(report, ensure_ascii=False)


@pytest.mark.unit
def test_graph_nodes_resolve_to_canonical_wiki_source_records(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    wiki = roots["knowledge"][0] / "fixture-user" / "wiki"
    source = wiki / "sources" / "source-a.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Sanitized source\n", encoding="utf-8")
    graph = wiki / "graph" / "graph.json"
    graph.parent.mkdir(parents=True)
    graph.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "filename": "source-a.md",
                        "id": "source/source-a.md",
                        "rel_path": "sources/source-a.md",
                    }
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )

    report, exit_code = _run(roots)

    assert exit_code == 0, report["errors"]
    node = next(
        row
        for row in report["records"]
        if row["category"] == "knowledge.wiki_graph_nodes"
    )
    source_record = next(
        row
        for row in report["records"]
        if row["category"] == "knowledge.wiki_sources"
    )
    link = next(
        row
        for row in report["relationships"]
        if row["relationship_type"] == "graph_node_source"
    )
    assert link == {
        **link,
        "from_record_id": node["record_id"],
        "status": "valid",
        "to_record_id": source_record["record_id"],
    }


@pytest.mark.unit
def test_graph_node_identity_is_unique_under_nfkc_casefold(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    wiki = roots["knowledge"][0] / "fixture-user" / "wiki"
    source = wiki / "sources" / "a.md"
    source.parent.mkdir(parents=True)
    source.write_text("# A\n", encoding="utf-8")
    graph = wiki / "graph" / "graph.json"
    graph.parent.mkdir(parents=True)
    graph.write_text(
        json.dumps(
            {
                "nodes": [
                    {"id": "source/A.md"},
                    {"id": "source/a.md"},
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )

    report, exit_code = _run(roots, allow_partial=True)

    assert exit_code == 0
    nodes = [
        row
        for row in report["records"]
        if row["category"] == "knowledge.wiki_graph_nodes"
    ]
    assert len(nodes) == 2
    assert all(row["status"] != "active" for row in nodes)
    assert all(row["proposed_terminal_classification"] == "quarantined" for row in nodes)
    assert all(
        row["graph_node_resolution"]["status"] == "unresolved" for row in nodes
    )


@pytest.mark.unit
def test_invalid_registry_auth_and_explicit_role_fail_closed_without_leakage(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    invalid_role = "role-private-token-never-report"
    (roots["runtime"] / "users.json").write_text(
        json.dumps(
            {
                "users": {
                    "missing-hash": {"disabled": False, "role": "user"},
                    "invalid-role": {
                        "disabled": False,
                        "password_hash": "secret-hash-never-report",
                        "role": invalid_role,
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    assert strict["complete"] is partial["complete"] is False
    registry_children = [
        row
        for row in strict["records"]
        if row["category"] == "identity.users_registry"
        and row["entry_type"] == "logical_record"
    ]
    assert len(registry_children) == 2
    assert all(row["planned_action"] == "quarantine_candidate" for row in registry_children)
    assert {row["proposed_terminal_classification"] for row in registry_children} == {
        "quarantined"
    }
    assert {row["code"] for row in strict["errors"]} & {
        "INVALID_JSON",
        "UNKNOWN_ENTRY",
    }
    serialized = json.dumps(strict, ensure_ascii=False, sort_keys=True)
    assert invalid_role not in serialized
    assert "secret-hash-never-report" not in serialized


@pytest.mark.unit
def test_duplicate_json_member_invalidates_registry_without_confirmed_account(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    secret = "duplicate-member-password-never-report"
    (roots["runtime"] / "users.json").write_text(
        '{"users":{"fixture-user":{"disabled":false,'
        f'"password_hash":"{secret}","role":"user","role":"admin"}}}}',
        encoding="utf-8",
    )

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    registry = next(
        row
        for row in strict["records"]
        if row["category"] == "identity.users_registry"
        and row["entry_type"] == "file"
    )
    assert registry["parse"]["status"] == "invalid"
    assert registry["proposed_terminal_classification"] == "quarantined"
    assert "INVALID_JSON" in {row["code"] for row in strict["errors"]}
    assert not [
        row
        for row in strict["records"]
        if row["category"] == "identity.users_registry"
        and row["entry_type"] == "logical_record"
    ]
    assert strict["summary"]["account_count"] == 0
    assert not any(owner["registry_record_id"] for owner in strict["owners"])
    assert secret not in json.dumps(strict, ensure_ascii=False, sort_keys=True)


@pytest.mark.unit
def test_one_character_registry_user_id_is_quarantined_not_confirmed(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    (roots["runtime"] / "users.json").write_text(
        '{"users":{"x":{"disabled":false,'
        '"password_hash":"fixture-hash","role":"user"}}}',
        encoding="utf-8",
    )

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    child = next(
        row
        for row in strict["records"]
        if row["category"] == "identity.users_registry"
        and row["entry_type"] == "logical_record"
    )
    assert child["owner"] == {
        "claimed_user_id": "x",
        "kind": "ambiguous_registry_owner",
        "mapping_status": "ambiguous",
        "user_id": None,
    }
    assert child["planned_action"] == "quarantine_candidate"
    assert child["proposed_terminal_classification"] == "quarantined"
    assert child["reason_codes"] == ["invalid_or_ambiguous_legacy_user"]
    assert strict["summary"]["account_count"] == 0
    assert not any(owner["registry_record_id"] for owner in strict["owners"])


@pytest.mark.unit
def test_registry_nfkc_casefold_collision_is_ambiguous_and_quarantined(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    (roots["runtime"] / "users.json").write_text(
        json.dumps(
            {
                "users": {
                    "Admin": {
                        "password_hash": "first-private-hash",
                        "role": "admin",
                    },
                    "admin": {
                        "password_hash": "second-private-hash",
                        "role": "user",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    children = [
        row
        for row in strict["records"]
        if row["category"] == "identity.users_registry"
        and row["entry_type"] == "logical_record"
    ]
    assert len(children) == 2
    assert all(row["owner"]["mapping_status"] == "ambiguous" for row in children)
    assert all(row["owner"]["user_id"] is None for row in children)
    assert all(row["proposed_terminal_classification"] == "quarantined" for row in children)
    assert "OWNER_ID_COLLISION" in {row["code"] for row in strict["errors"]}
    serialized = json.dumps(strict, ensure_ascii=False, sort_keys=True)
    assert "first-private-hash" not in serialized
    assert "second-private-hash" not in serialized


@pytest.mark.unit
def test_registry_hash_only_account_evidence_is_bound_to_child_identity(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    report, exit_code = _run(roots)
    assert exit_code == 0
    child = next(
        row
        for row in report["records"]
        if row["category"] == "identity.users_registry"
        and row["record_kind"] == "logical_record"
    )
    child["parse"]["registry_account"]["payload_sha256"] = (
        "raw-private-password-material"
    )
    assert "registry_account_closure" in inventory_contract_errors(
        report, verify_manifest=False
    )


@pytest.mark.unit
def test_candidate_scalars_and_unknown_enums_are_quarantined_and_hashed(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    wiki = roots["knowledge"][0] / "fixture-user" / "wiki"
    source_dir = wiki / "sources"
    source_dir.mkdir(parents=True)
    (source_dir / "source-a.md").write_text("# A\n", encoding="utf-8")
    (source_dir / "target-b.md").write_text("# B\n", encoding="utf-8")
    candidates = (
        wiki
        / "graph"
        / "pending_diffs.json"
    )
    candidates.parent.mkdir(parents=True)
    secret = "candidate-enum-private-token-never-report"
    candidates.write_text(
        json.dumps(
            [
                42,
                {"action": secret, "kind": secret, "status": secret},
            ]
        ),
        encoding="utf-8",
    )

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    assert manifest_payload(strict) == manifest_payload(partial)
    children = [
        row
        for row in strict["records"]
        if row["category"] == "knowledge.wiki_candidates"
        and row["entry_type"] == "logical_record"
    ]
    assert len(children) == 2
    assert all(row["planned_action"] == "quarantine_candidate" for row in children)
    assert all(len(row["sha256"]) == 64 for row in children)
    assert secret not in json.dumps(strict, ensure_ascii=False, sort_keys=True)


@pytest.mark.unit
def test_missing_asset_and_browser_pins_emit_synthetic_unavailable_records(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    source = (
        roots["knowledge"][0]
        / "fixture-user"
        / "wiki"
        / "sources"
        / "missing-asset.md"
    )
    source.parent.mkdir(parents=True)
    source.write_text("# Source\n\n![missing](../assets/not-present.bin)\n", encoding="utf-8")

    report, exit_code = _run(roots, allow_partial=True)

    assert exit_code == 0
    assert report["complete"] is False
    missing_asset = next(
        row
        for row in report["records"]
        if row["category"] == "knowledge.wiki_assets"
        and row["entry_type"] == "unavailable"
    )
    browser = next(
        row
        for row in report["records"]
        if row["category"] == "unavailable.browser_pins"
    )
    for row in (missing_asset, browser):
        assert row["entry_type"] == "unavailable"
        assert row["record_kind"] == "unavailable"
        assert row["planned_action"] == "report_only"
        assert row["proposed_terminal_classification"] == "unavailable"
        assert row["classification_authority"] == "scanner"
        assert row["size_bytes"] is None
    source_record = next(
        row
        for row in report["records"]
        if row["category"] == "knowledge.wiki_sources"
    )
    missing_relationship = next(
        row
        for row in report["relationships"]
        if row["relationship_type"] == "asset_reference"
        and row["status"] == "unavailable"
    )
    assert missing_relationship["from_record_id"] == source_record["record_id"]
    assert missing_relationship["to_record_id"] == missing_asset["record_id"]


@pytest.mark.unit
def test_asset_reference_cannot_cross_canonical_owner_boundary(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    (roots["runtime"] / "users.json").write_text(
        json.dumps(
            {
                "users": {
                    "user-a": {"password_hash": "hash-a", "role": "user"},
                    "user-b": {"password_hash": "hash-b", "role": "user"},
                }
            }
        ),
        encoding="utf-8",
    )
    source = roots["knowledge"][0] / "user-a" / "wiki" / "sources" / "a.md"
    target = roots["knowledge"][0] / "user-b" / "wiki" / "assets" / "x.png"
    source.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    source.write_text("![x](../../../user-b/wiki/assets/x.png)\n", encoding="utf-8")
    target.write_bytes(b"sanitized-image")

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    source_record = next(
        row
        for row in strict["records"]
        if row["category"] == "knowledge.wiki_sources"
    )
    target_record = next(
        row
        for row in strict["records"]
        if row["category"] == "knowledge.wiki_assets"
    )
    assert source_record["planned_action"] == "quarantine_candidate"
    assert "cross_owner_asset_reference" in source_record["reason_codes"]
    assert not any(
        row.get("from_record_id") == source_record["record_id"]
        and row.get("to_record_id") == target_record["record_id"]
        and row.get("status") == "valid"
        for row in strict["relationships"]
    )
    assert target_record["planned_action"] == "quarantine_candidate"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("wiki_directory", "filename"),
    (("sources", "evil.bin"), ("pages", "note.md.tmp")),
)
def test_non_markdown_wiki_source_or_page_is_unknown_and_quarantined(
    tmp_path: Path,
    wiki_directory: str,
    filename: str,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    marker = f"{wiki_directory}-{filename}-private-never-report"
    unexpected = (
        roots["knowledge"][0]
        / "fixture-user"
        / "wiki"
        / wiki_directory
        / filename
    )
    unexpected.parent.mkdir(parents=True)
    unexpected.write_text(marker, encoding="utf-8")

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    record = next(
        row
        for row in strict["records"]
        if row["relative_path"].endswith(f"/wiki/{wiki_directory}/{filename}")
    )
    assert record["category"] == "unknown.unclassified"
    assert record["planned_action"] == "quarantine"
    assert record["proposed_terminal_classification"] == "quarantined"
    assert record["classification_authority"] == "scanner"
    assert record["reason_codes"] == ["unclassified_legacy_entry"]
    assert "UNKNOWN_ENTRY" in {row["code"] for row in strict["errors"]}
    assert marker not in json.dumps(strict, ensure_ascii=False, sort_keys=True)


@pytest.mark.unit
def test_invalid_jsonl_is_not_silently_skipped_and_allow_partial_is_zero(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    memory = roots["user_data"] / "fixture-user" / "memory" / "memories.jsonl"
    memory.parent.mkdir(parents=True)
    memory.write_text('{"type":"preference"}\n{broken\n', encoding="utf-8")

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    assert strict["complete"] is partial["complete"] is False
    assert "INVALID_JSONL" in {row["code"] for row in strict["errors"]}
    assert manifest_payload(strict) == manifest_payload(partial)


@pytest.mark.unit
def test_hash_failure_emits_an_explicit_unreadable_terminal_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    note = roots["knowledge"][0] / "fixture-user" / "note.md"
    note.parent.mkdir()
    note.write_text("sanitized", encoding="utf-8")
    original_hash = legacy_inventory._stream_sha256

    def fail_target(entry: Any, state: Any) -> str | None:
        if entry.path == note:
            state.error(
                "HASH_FAILED",
                root_id=entry.root.root_id,
                relative_path=entry.relative_path,
                operation="hash_file",
            )
            return None
        return original_hash(entry, state)

    monkeypatch.setattr(legacy_inventory, "_stream_sha256", fail_target)
    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    record = next(row for row in strict["records"] if row["relative_path"] == "fixture-user/note.md")
    assert record["status"] == "unreadable"
    assert record["sha256"] is None
    assert record["proposed_terminal_classification"] == "quarantined"
    assert "HASH_FAILED" in {row["code"] for row in strict["errors"]}


@pytest.mark.unit
def test_json_parser_rejects_transient_post_hash_session_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    session = roots["user_data"] / "fixture-user" / "sessions" / "session-1.json"
    session.parent.mkdir(parents=True)
    original_bytes = json.dumps(
        {"id": "session-1", "messages": []}, separators=(",", ":")
    ).encode("utf-8")
    marker = "transient-swapped-session-child-never-report"
    swapped_bytes = json.dumps(
        {
            "id": "session-1",
            "messages": [{"role": "user", "content": marker}],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    session.write_bytes(original_bytes)
    original_stat = session.stat()
    source_before = _state(roots)
    original_read_json = legacy_inventory._read_json
    swapped_once = False

    def swap_only_during_verified_read(
        entry: Any,
        state: Any,
        expected_sha256: str | None,
    ) -> Any:
        nonlocal swapped_once
        if entry.path != session or swapped_once:
            return original_read_json(entry, state, expected_sha256)
        swapped_once = True
        session.write_bytes(swapped_bytes)
        try:
            return original_read_json(entry, state, expected_sha256)
        finally:
            session.write_bytes(original_bytes)
            os.utime(
                session,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )

    monkeypatch.setattr(legacy_inventory, "_read_json", swap_only_during_verified_read)

    report, exit_code = _run(roots, allow_partial=True)

    assert swapped_once is True
    assert exit_code == 2
    assert "SOURCE_CHANGED" in {row["code"] for row in report["errors"]}
    assert has_hard_errors(report["errors"]) is True
    physical = next(
        row
        for row in report["records"]
        if row["category"] == "conversation.user_sessions"
        and row["entry_type"] == "file"
    )
    assert physical["parse"]["status"] == "invalid"
    assert not [
        row
        for row in report["records"]
        if row.get("parent_record_id") == physical["record_id"]
    ]
    user_data_root_id = next(
        row["root_id"]
        for row in report["source_snapshot"]["roots"]
        if row["role"] == "user_data"
    )
    high_water = next(
        row
        for row in report["source_snapshot"]["high_water_marks"]
        if row["root_id"] == user_data_root_id
    )
    assert high_water["changed"] is False
    assert high_water["before"] == high_water["after"]
    assert _state(roots) == source_before
    assert marker not in json.dumps(report, ensure_ascii=False, sort_keys=True)


@pytest.mark.unit
def test_final_root_identity_change_is_a_hard_source_gate(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    calls: dict[str, int] = {}
    target = str(roots["knowledge"][0].resolve(strict=True))

    def changing_probe(path: str) -> dict[str, Any]:
        resolved = str(Path(path).resolve(strict=True))
        calls[resolved] = calls.get(resolved, 0) + 1
        fact = _probe(path)
        if resolved == target and calls[resolved] > 1:
            fact["volume"] = {
                "drive_type": "fixed",
                "filesystem": "NTFS",
                "root": "U:\\",
            }
        return fact

    report, exit_code = inventory_snapshot(
        roots["legacy"],
        roots["knowledge"],
        "sanitized-snapshot",
        runtime_root=roots["runtime"],
        user_data_root=roots["user_data"],
        sessions_root=roots["sessions"],
        workspace_root=roots["workspace"],
        snapshot_kind="frozen_copy",
        snapshot_created_at_utc="2026-08-12T00:00:00.000000Z",
        snapshot_attestation_sha256="a" * 64,
        scratch_root=roots["scratch"],
        allow_partial=True,
        clock=_clock,
        code_coverage_provider=_coverage,
        root_probe=changing_probe,
    )

    assert exit_code == 2
    assert "SOURCE_CHANGED" in {row["code"] for row in report["errors"]}


@pytest.mark.unit
def test_scratch_identity_change_blocks_notion_temp_write_and_preserves_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    database = roots["user_data"] / "fixture-user" / "notion" / "notion.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE pages (
              id TEXT PRIMARY KEY,
              title TEXT,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE session_pages (
              session_id TEXT, page_id TEXT, owner_user_id TEXT
            );
            """
        )
        connection.commit()
    finally:
        connection.close()
    before = _state(roots)
    original_scratch = roots["scratch"]
    displaced_scratch = tmp_path / "approved-scratch-original"
    scratch_probe_calls = 0
    scratch_temp_created = False

    def changing_probe(path: str) -> dict[str, Any]:
        nonlocal scratch_probe_calls
        configured = Path(path)
        if configured == original_scratch:
            scratch_probe_calls += 1
            if scratch_probe_calls == 2:
                original_scratch.rename(displaced_scratch)
                original_scratch.mkdir()
        return _probe(path)

    def forbidden_mkdtemp(*args: Any, **kwargs: Any) -> Any:
        nonlocal scratch_temp_created
        scratch_temp_created = True
        raise AssertionError("scratch identity must be checked before temp creation")

    monkeypatch.setattr(legacy_inventory.tempfile, "mkdtemp", forbidden_mkdtemp)

    report, exit_code = inventory_snapshot(
        roots["legacy"],
        roots["knowledge"],
        "scratch-identity-change",
        runtime_root=roots["runtime"],
        user_data_root=roots["user_data"],
        sessions_root=roots["sessions"],
        workspace_root=roots["workspace"],
        snapshot_kind="frozen_copy",
        snapshot_created_at_utc="2026-08-12T00:00:00.000000Z",
        snapshot_attestation_sha256="a" * 64,
        scratch_root=original_scratch,
        allow_partial=True,
        clock=_clock,
        code_coverage_provider=_coverage,
        root_probe=changing_probe,
    )

    assert exit_code == 2
    assert scratch_probe_calls >= 2
    assert scratch_temp_created is False
    assert "SOURCE_CHANGED" in {row["code"] for row in report["errors"]}
    assert list(original_scratch.iterdir()) == []
    assert list(displaced_scratch.iterdir()) == []
    assert _state(roots) == before


@pytest.mark.unit
def test_scratch_parent_is_handle_pinned_before_temp_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    database = roots["user_data"] / "fixture-user" / "notion" / "notion.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE pages (
              id TEXT PRIMARY KEY,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE session_pages (
              session_id TEXT NOT NULL,
              page_id TEXT NOT NULL,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              PRIMARY KEY(session_id, page_id)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()
    before = _state(roots)
    scratch = roots["scratch"]
    displaced = tmp_path / "displaced-scratch"
    original_create = (
        legacy_inventory._WindowsDirectoryAnchor.__dict__[
            "create_relative_directory"
        ].__func__
    )
    attempted = False

    def racing_create(
        cls: type[Any],
        parent: Any,
        name: str,
    ) -> Any:
        nonlocal attempted
        attempted = True
        with pytest.raises(OSError):
            scratch.rename(displaced)
        return original_create(cls, parent, name)

    monkeypatch.setattr(
        legacy_inventory._WindowsDirectoryAnchor,
        "create_relative_directory",
        classmethod(racing_create),
    )

    report, exit_code = _run(roots)

    assert attempted is True
    assert exit_code == 0, report["errors"]
    assert scratch.is_dir()
    assert not displaced.exists()
    assert list(scratch.iterdir()) == []
    assert _state(roots) == before


@pytest.mark.unit
def test_scratch_child_is_handle_pinned_during_sqlite_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    database = roots["user_data"] / "fixture-user" / "notion" / "notion.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE pages (
              id TEXT PRIMARY KEY,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE session_pages (
              session_id TEXT NOT NULL,
              page_id TEXT NOT NULL,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              PRIMARY KEY(session_id, page_id)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()
    before = _state(roots)
    escaped = roots["knowledge"][0] / "moved-scratch-child"
    original_copy = legacy_inventory._copy_sqlite_snapshot
    attempted = False

    def racing_copy(entry: Any, destination: Path, state: Any) -> Path:
        nonlocal attempted
        attempted = True
        with pytest.raises(OSError):
            destination.rename(escaped)
        return original_copy(entry, destination, state)

    monkeypatch.setattr(
        legacy_inventory, "_copy_sqlite_snapshot", racing_copy
    )

    report, exit_code = _run(roots)

    assert attempted is True
    assert exit_code == 0, report["errors"]
    assert not escaped.exists()
    assert list(roots["scratch"].iterdir()) == []
    assert _state(roots) == before


@pytest.mark.unit
def test_scratch_copy_handle_is_unlinkable_into_a_source_while_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    database = roots["user_data"] / "fixture-user" / "notion" / "notion.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE pages (
              id TEXT PRIMARY KEY,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE session_pages (
              session_id TEXT NOT NULL,
              page_id TEXT NOT NULL,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              PRIMARY KEY(session_id, page_id)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()
    before = _state(roots)
    original_write = legacy_inventory._write_windows_handle
    attempted = False

    def racing_write(handle: int, payload: bytes, *, flush: bool = True) -> None:
        nonlocal attempted
        if not attempted:
            attempted = True
            temporary_files = [
                path
                for path in roots["scratch"].rglob("*")
                if path.is_file()
            ]
            assert len(temporary_files) == 1
            escaped = roots["knowledge"][0] / "linked-scratch-copy.db"
            with pytest.raises(OSError):
                os.link(temporary_files[0], escaped)
            assert not escaped.exists()
        original_write(handle, payload, flush=flush)

    monkeypatch.setattr(legacy_inventory, "_write_windows_handle", racing_write)

    report, exit_code = _run(roots)

    assert attempted is True
    assert exit_code == 0, report["errors"]
    assert list(roots["scratch"].iterdir()) == []
    assert _state(roots) == before


@pytest.mark.unit
def test_directory_anchor_denies_reparse_write_handles(tmp_path: Path) -> None:
    root = tmp_path / "pinned"
    root.mkdir()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    with legacy_inventory._pinned_directory(root):
        competing = create_file(
            str(root),
            0x40000000,  # GENERIC_WRITE, needed to set a reparse point.
            0x0001,
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        try:
            assert competing in (None, ctypes.c_void_p(-1).value)
            assert ctypes.get_last_error() == 32  # ERROR_SHARING_VIOLATION
        finally:
            if competing not in (None, ctypes.c_void_p(-1).value):
                close_handle(competing)


@pytest.mark.unit
def test_authority_bearing_jsonl_unknown_status_is_quarantined(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    secret = "memory-status-private-token-never-report"
    memory = roots["user_data"] / "fixture-user" / "memory" / "memories.jsonl"
    memory.parent.mkdir(parents=True)
    memory.write_text(
        json.dumps({"kind": "memory", "status": secret}) + "\n",
        encoding="utf-8",
    )

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    child = next(
        row
        for row in strict["records"]
        if row["category"] == "context.memory"
        and row["entry_type"] == "logical_record"
    )
    assert child["planned_action"] == "quarantine_candidate"
    assert child["proposed_terminal_classification"] == "quarantined"
    assert "UNKNOWN_ENTRY" in {row["code"] for row in strict["errors"]}
    assert secret not in json.dumps(strict, ensure_ascii=False, sort_keys=True)


@pytest.mark.unit
def test_legacy_edge_unknown_type_is_quarantined_without_enum_leak(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    secret = "edge-type-private-token-never-report"
    edges = roots["knowledge"][0] / "fixture-user" / "edges.jsonl"
    edges.parent.mkdir()
    edges.write_text(
        json.dumps({"source": "a", "target": "b", "type": secret}) + "\n",
        encoding="utf-8",
    )

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    child = next(
        row
        for row in strict["records"]
        if row["category"] == "knowledge.legacy_edges"
        and row["entry_type"] == "logical_record"
    )
    assert child["planned_action"] == "quarantine_candidate"
    assert child["proposed_terminal_classification"] == "quarantined"
    assert "UNKNOWN_ENTRY" in {row["code"] for row in strict["errors"]}
    assert secret not in json.dumps(strict, ensure_ascii=False, sort_keys=True)


@pytest.mark.unit
def test_notion_database_is_read_only_and_creates_no_sidecars(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    database = roots["user_data"] / "fixture-user" / "notion" / "notion.db"
    database.parent.mkdir(parents=True)
    session = roots["user_data"] / "fixture-user" / "sessions" / "session-1.json"
    session.parent.mkdir(parents=True)
    session.write_text('{"id":"session-1","messages":[]}', encoding="utf-8")
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE pages (
              id TEXT PRIMARY KEY,
              title TEXT,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE session_pages (
              session_id TEXT NOT NULL,
              page_id TEXT NOT NULL,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              PRIMARY KEY(session_id, page_id)
            );
            INSERT INTO pages VALUES ('page-1', 'sanitized', 'fixture-user', 1);
            INSERT INTO session_pages VALUES ('session-1', 'page-1', 'fixture-user', 1);
            """
        )
        connection.commit()
    finally:
        connection.close()
    before = _state(roots)

    report, exit_code = _run(roots)

    assert exit_code == 0
    notion = next(
        row for row in report["records"]
        if row["category"] == "external.notion_database"
    )
    assert notion["parse"]["table_counts"] == {"pages": 1, "session_pages": 1}
    assert any(row["relationship_type"] == "notion_session_page" for row in report["relationships"])
    assert not list(database.parent.glob("*-wal"))
    assert not list(database.parent.glob("*-shm"))
    assert _state(roots) == before

    first_page_hash = notion["parse"]["table_sha256"]["pages"]
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE pages SET title = ? WHERE id = ?",
            ("same row count, changed cell", "page-1"),
        )
        connection.commit()
    finally:
        connection.close()
    changed_before = _state(roots)
    changed_report, changed_exit = _run(roots)
    changed_notion = next(
        row
        for row in changed_report["records"]
        if row["category"] == "external.notion_database"
    )
    assert changed_exit == 0
    assert changed_notion["parse"]["table_counts"] == notion["parse"]["table_counts"]
    assert changed_notion["parse"]["table_sha256"]["pages"] != first_page_hash
    assert _state(roots) == changed_before


@pytest.mark.unit
def test_live_wal_notion_source_bytes_and_timestamps_are_unchanged(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    database = roots["user_data"] / "fixture-user" / "notion" / "notion.db"
    database.parent.mkdir(parents=True)
    session = roots["user_data"] / "fixture-user" / "sessions" / "session-1.json"
    session.parent.mkdir(parents=True)
    session.write_text('{"id":"session-1","messages":[]}', encoding="utf-8")
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.executescript(
            """
            CREATE TABLE pages (
              id TEXT PRIMARY KEY,
              title TEXT,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE session_pages (
              session_id TEXT NOT NULL,
              page_id TEXT NOT NULL,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              PRIMARY KEY(session_id, page_id)
            );
            INSERT INTO pages VALUES ('page-1', 'sanitized', 'fixture-user', 1);
            INSERT INTO session_pages VALUES ('session-1', 'page-1', 'fixture-user', 1);
            """
        )
        connection.commit()
        assert Path(str(database) + "-wal").is_file()
        assert Path(str(database) + "-shm").is_file()
        before = _state(roots)

        report, exit_code = _run(roots)

        assert exit_code == 0, report["errors"]
        sidecars = {
            row["relative_path"].rsplit("/", 1)[-1]: row["category"]
            for row in report["records"]
            if row["category"] == "external.notion_database_sidecars"
        }
        assert sidecars == {
            "notion.db-shm": "external.notion_database_sidecars",
            "notion.db-wal": "external.notion_database_sidecars",
        }
        assert _state(roots) == before
    finally:
        connection.close()


@pytest.mark.unit
def test_sessions_emit_canonical_hashed_children_without_payloads(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    session = roots["user_data"] / "fixture-user" / "sessions" / "session-1.json"
    session.parent.mkdir(parents=True)
    secret = "session-body-secret-never-report-a17c"
    payload = {
        "id": "session-1",
        "messages": [{"role": "user", "content": secret}, {"role": "assistant", "content": "safe"}],
        "metadata": {"compact_summary": "private summary"},
        "skills_used": ["fixture-skill"],
        "uploaded_files": [{"filename": "private.txt", "token": secret}],
        "bindings": [{"kind": "legacy", "id": secret}],
    }
    session.write_text(json.dumps(payload), encoding="utf-8")

    first, first_exit = _run(roots)
    physical = next(
        row for row in first["records"]
        if row["category"] == "conversation.user_sessions" and row["entry_type"] == "file"
    )
    children = [
        row for row in first["records"]
        if row["category"] == "conversation.user_sessions" and row["entry_type"] == "logical_record"
    ]

    assert first_exit == 0
    assert physical["parse"]["child_counts"] == {
        "bindings": 1,
        "messages": 2,
        "metadata": 1,
        "session_core_metadata": 1,
        "summaries": 1,
        "uploads": 1,
    }
    assert physical["parse"]["child_count"] == 7
    assert len(children) == 7
    assert {row["child_kind"] for row in children} == {
        "bindings",
        "messages",
        "metadata",
        "session_core_metadata",
        "summaries",
        "uploads",
    }
    assert all(len(row["payload_sha256"]) == 64 for row in children)
    assert secret not in json.dumps(first, ensure_ascii=False)

    payload["messages"][0]["content"] = "different private body"
    session.write_text(json.dumps(payload), encoding="utf-8")
    second, second_exit = _run(roots)
    assert second_exit == 0
    assert first["manifest_sha256"] != second["manifest_sha256"]


@pytest.mark.unit
def test_session_core_metadata_is_logically_reconciled(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    session = roots["user_data"] / "fixture-user" / "sessions" / "session-a.json"
    session.parent.mkdir(parents=True)
    first_payload = {
        "id": "session-a",
        "messages": [],
        "title": "private-title-a",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    session.write_text(json.dumps(first_payload), encoding="utf-8")
    first, first_exit = _run(roots)
    first_source = next(
        row
        for row in first["records"]
        if row["category"] == "conversation.user_sessions"
        and row["entry_type"] == "file"
    )

    second_payload = dict(first_payload)
    second_payload["title"] = "private-title-b"
    second_payload["updated_at"] = "2026-01-02T00:00:00Z"
    session.write_text(json.dumps(second_payload), encoding="utf-8")
    second, second_exit = _run(roots)
    second_source = next(
        row
        for row in second["records"]
        if row["category"] == "conversation.user_sessions"
        and row["entry_type"] == "file"
    )

    assert first_exit == second_exit == 0
    assert first_source["parse"]["child_counts"]["session_core_metadata"] == 1
    assert second_source["parse"]["child_counts"]["session_core_metadata"] == 1
    assert (
        first_source["parse"]["child_aggregate_sha256"]["session_core_metadata"]
        != second_source["parse"]["child_aggregate_sha256"]["session_core_metadata"]
    )
    serialized = json.dumps([first, second], ensure_ascii=False, sort_keys=True)
    for secret in ("private-title-a", "private-title-b"):
        assert secret not in serialized


@pytest.mark.unit
def test_session_message_role_must_fit_canonical_conversation_vocabulary(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    session = roots["user_data"] / "fixture-user" / "sessions" / "session-role.json"
    session.parent.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                "session_id": "session-role",
                "messages": [{"role": "developer", "content": "private"}],
            }
        ),
        encoding="utf-8",
    )

    report, exit_code = _run(roots, allow_partial=True)
    physical = next(
        row
        for row in report["records"]
        if row["category"] == "conversation.user_sessions"
        and row["entry_type"] == "file"
    )
    children = [
        row
        for row in report["records"]
        if row["parent_record_id"] == physical["record_id"]
    ]

    assert exit_code == 0
    assert physical["status"] == "invalid"
    assert physical["planned_action"] == "quarantine_candidate"
    assert physical["reason_codes"] == ["invalid_session_message"]
    assert all(row["status"] == "invalid" for row in children)
    assert all(row["reason_codes"] == ["invalid_session_message"] for row in children)


@pytest.mark.unit
def test_invalid_utf8_markdown_is_quarantined_without_breaking_partial_closure(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    source = roots["knowledge"][0] / "fixture-user" / "wiki" / "sources" / "bad.md"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\xff\xfe")

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    assert inventory_contract_errors(partial) == ()
    record = next(
        row
        for row in partial["records"]
        if row["category"] == "knowledge.wiki_sources"
    )
    assert record["status"] == "invalid"
    assert record["planned_action"] == "quarantine_candidate"
    assert record["asset_reference_evidence"] == {
        "declared_path_sha256": [],
        "status": "invalid",
        "syntax": "known_producer_inline_markdown_v1",
    }


@pytest.mark.unit
def test_conflicting_session_identity_aliases_quarantine_without_registration(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    session = roots["user_data"] / "fixture-user" / "sessions" / "session-a.json"
    session.parent.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                "id": "session-b",
                "messages": [],
                "session_id": "session-a",
            }
        ),
        encoding="utf-8",
    )
    database = roots["user_data"] / "fixture-user" / "notion" / "notion.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE pages (
              id TEXT PRIMARY KEY,
              title TEXT,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE session_pages (
              session_id TEXT NOT NULL,
              page_id TEXT NOT NULL,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              PRIMARY KEY(session_id, page_id)
            );
            INSERT INTO pages VALUES ('page-a', 'sanitized', 'fixture-user', 1);
            INSERT INTO session_pages VALUES (
              'session-a', 'page-a', 'fixture-user', 1
            );
            """
        )
        connection.commit()
    finally:
        connection.close()

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    session_record = next(
        row
        for row in strict["records"]
        if row["category"] == "conversation.user_sessions"
        and row["entry_type"] == "file"
    )
    assert session_record["status"] == "invalid"
    assert session_record["parse"]["status"] == "invalid"
    assert session_record["planned_action"] == "quarantine_candidate"
    assert session_record["reason_codes"] == [
        "conflicting_session_identity_aliases"
    ]
    session_children = [
        row
        for row in strict["records"]
        if row.get("parent_record_id") == session_record["record_id"]
    ]
    assert session_children
    assert all(
        row["planned_action"] == "quarantine_candidate"
        and row["proposed_terminal_classification"] == "quarantined"
        for row in session_children
    )
    notion_link = next(
        row
        for row in strict["records"]
        if row["category"] == "external.notion_session_links"
        and row["entry_type"] == "logical_record"
    )
    assert notion_link["status"] == "unavailable"
    assert notion_link["link_resolution"]["session_record_id"] is None
    assert "MISSING_REFERENCE" in {row["code"] for row in strict["errors"]}

    omitted = json.loads(json.dumps(strict))
    omitted_link = next(
        row
        for row in omitted["records"]
        if row["category"] == "external.notion_session_links"
    )
    del omitted_link["link_resolution"]
    omitted["relationships"] = [
        row
        for row in omitted["relationships"]
        if row.get("source_record_id") != omitted_link["record_id"]
    ]
    omitted["summary"]["relationships"] = len(omitted["relationships"])
    assert "notion_relationship_closure" in inventory_contract_errors(
        omitted, verify_manifest=False
    )


@pytest.mark.unit
def test_job_states_are_allowlisted_and_active_jobs_are_never_replayable(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    jobs = roots["user_data"] / "fixture-user" / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "queued.json").write_text(
        '{"id":"queued","kind":"outlook_sync","status":"queued","user_id":"fixture-user"}',
        encoding="utf-8",
    )
    (jobs / "done.json").write_text(
        '{"id":"done","kind":"outlook_sync","status":"succeeded","user_id":"fixture-user"}',
        encoding="utf-8",
    )

    report, exit_code = _run(roots)

    assert exit_code == 0
    physical = [
        row for row in report["records"]
        if row["category"] == "operations.jobs" and row["entry_type"] == "file"
    ]
    queued = next(row for row in physical if row["parse"]["source_status"] == "queued")
    assert queued["status"] == "abandoned_legacy"
    assert queued["reason_codes"] == ["legacy_job_not_replayable"]
    assert report["summary"]["job_states"] == {"queued": 1, "succeeded": 1}


@pytest.mark.unit
def test_job_authority_tuple_mismatch_is_quarantined(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    secret = "job-kind-private-token-never-report"
    job = roots["user_data"] / "fixture-user" / "jobs" / "job-a.json"
    job.parent.mkdir(parents=True)
    job.write_text(
        json.dumps(
            {
                "id": "different-id",
                "kind": secret,
                "status": "alien",
                "user_id": "ghost-user",
            }
        ),
        encoding="utf-8",
    )

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    record = next(row for row in strict["records"] if row["category"] == "operations.jobs")
    assert record["planned_action"] == "quarantine_candidate"
    assert record["proposed_terminal_classification"] == "quarantined"
    assert {
        "job_id_filename_mismatch",
        "job_owner_mismatch",
        "unknown_job_kind",
        "unknown_job_status",
    }.issubset(record["reason_codes"])
    assert secret not in json.dumps(strict, ensure_ascii=False, sort_keys=True)


@pytest.mark.unit
def test_conflicting_job_identity_aliases_are_quarantined(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    job = roots["user_data"] / "fixture-user" / "jobs" / "job-a.json"
    job.parent.mkdir(parents=True)
    job.write_text(
        json.dumps(
            {
                "id": "job-b",
                "job_id": "job-a",
                "kind": "outlook_sync",
                "status": "succeeded",
                "user_id": "fixture-user",
            }
        ),
        encoding="utf-8",
    )

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    record = next(
        row for row in strict["records"] if row["category"] == "operations.jobs"
    )
    assert record["status"] == "unknown"
    assert record["parse"]["status"] == "valid"
    assert record["planned_action"] == "quarantine_candidate"
    assert record["proposed_terminal_classification"] == "quarantined"
    assert record["reason_codes"] == ["conflicting_job_identity_aliases"]
    error = next(
        row
        for row in strict["errors"]
        if row["operation"] == "validate_job_identity"
    )
    assert error["code"] == "UNKNOWN_ENTRY"
    assert error["reason_codes"] == ["conflicting_job_identity_aliases"]


@pytest.mark.unit
def test_job_json_must_be_an_identity_bearing_object(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    job = roots["user_data"] / "fixture-user" / "jobs" / "job-a.json"
    job.parent.mkdir(parents=True)
    job.write_text("[]", encoding="utf-8")

    strict, strict_exit = _run(roots)
    partial, partial_exit = _run(roots, allow_partial=True)

    assert strict_exit == 2
    assert partial_exit == 0
    record = next(
        row for row in strict["records"] if row["category"] == "operations.jobs"
    )
    assert record["status"] == "invalid"
    assert record["planned_action"] == "quarantine_candidate"
    assert record["proposed_terminal_classification"] == "quarantined"
    assert "job_record_not_object" in record["reason_codes"]


@pytest.mark.unit
def test_unregistered_path_owner_is_quarantined_not_personal(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    note = roots["knowledge"][0] / "sbtest01" / "note.md"
    note.parent.mkdir()
    note.write_text("sanitized", encoding="utf-8")

    report, exit_code = _run(roots, allow_partial=True)

    assert exit_code == 0
    record = next(row for row in report["records"] if row["relative_path"] == "sbtest01/note.md")
    assert record["owner"] == {
        "claimed_user_id": "sbtest01",
        "kind": "unresolved_path_owner",
        "mapping_status": "unresolved",
        "user_id": None,
    }
    assert record["planned_action"] == "quarantine_candidate"
    assert "MISSING_REFERENCE" in {row["code"] for row in report["errors"]}


@pytest.mark.unit
def test_notion_timestamped_backup_and_nonfinite_json_are_explicit(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    notion = roots["user_data"] / "fixture-user" / "notion"
    notion.mkdir(parents=True)
    (notion / "notion.db.bak_20260621_212408").write_bytes(b"sanitized backup")
    (roots["runtime"] / "audit.jsonl").write_text('{"value":NaN}\n', encoding="utf-8")
    (roots["sessions"] / "bad.json").write_text('{"value":Infinity}', encoding="utf-8")

    report, exit_code = _run(roots, allow_partial=True)

    assert exit_code == 0
    backup = next(
        row for row in report["records"]
        if row["relative_path"].endswith("notion.db.bak_20260621_212408")
    )
    assert backup["category"] == "external.notion_backup_candidates"
    assert {"INVALID_JSON", "INVALID_JSONL"}.issubset(
        {row["code"] for row in report["errors"]}
    )


@pytest.mark.unit
def test_reparse_attribute_is_detected_without_following_target(tmp_path: Path) -> None:
    class _Stat:
        st_file_attributes = 0x400

    assert legacy_inventory._is_reparse(tmp_path / "cloud-placeholder", _Stat()) is True  # type: ignore[arg-type]


@pytest.mark.unit
def test_actual_layout_classifiers_leave_no_known_entry_unclassified(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    (roots["runtime"] / "users.json").write_text(
        '{"users":{"admin":{"disabled":false,"password_hash":"fixture-hash","role":"admin"}}}', encoding="utf-8"
    )
    (roots["runtime"] / "server-runtime.log").write_text("sanitized", encoding="utf-8")
    (roots["user_data"] / "server-user-data.log").write_text("sanitized", encoding="utf-8")
    outlook = roots["user_data"] / "admin" / "outlook" / "state.json"
    outlook.parent.mkdir(parents=True)
    outlook.write_text('{"status":"active"}', encoding="utf-8")
    knowledge = roots["knowledge"][0] / "admin"
    knowledge.mkdir()
    (knowledge / "edges.jsonl").write_text(
        '{"source":"a","target":"b","edge_type":"related"}\n', encoding="utf-8"
    )
    (knowledge / "tombstones.json").write_text("{}", encoding="utf-8")

    report, exit_code = _run(roots)

    assert exit_code == 0, report["errors"]
    categories = {row["category"] for row in report["records"]}
    assert {
        "external.outlook_state",
        "knowledge.legacy_edges",
        "knowledge.tombstones",
        "operations.server_logs",
    }.issubset(categories)
    assert not [
        row for row in report["records"]
        if row["category"] == "unknown.unclassified"
    ]


@pytest.mark.unit
def test_candidates_preserve_only_allowlisted_hash_evidence(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    wiki = roots["knowledge"][0] / "fixture-user" / "wiki"
    source_dir = wiki / "sources"
    source_dir.mkdir(parents=True)
    (source_dir / "source-a.md").write_text("# A\n", encoding="utf-8")
    (source_dir / "target-b.md").write_text("# B\n", encoding="utf-8")
    candidates = (
        wiki
        / "graph"
        / "pending_diffs.json"
    )
    candidates.parent.mkdir(parents=True)
    secret = "candidate-private-content-never-report"
    candidates.write_text(
        json.dumps(
            [
                {
                    "candidate_kind": "graph_candidate",
                    "action": "add_edge",
                    "status": "pending",
                    "source": {
                        "source_id": "source/source-a.md",
                        "content": secret,
                    },
                    "target_id": "source/target-b.md",
                    "base_revision": 3,
                    "content": secret,
                    "reason": secret,
                }
            ]
        ),
        encoding="utf-8",
    )

    report, exit_code = _run(roots)

    assert exit_code == 0
    physical = next(
        row for row in report["records"]
        if row["category"] == "knowledge.wiki_candidates" and row["entry_type"] == "file"
    )
    assert physical["parse"]["candidate_counts"] == {
        "actions": {"add_edge": 1},
        "evidence": {
            "base_or_revision": 1,
            "identity_evidence": 1,
            "resolved": 1,
            "source": 1,
            "target": 1,
        },
        "kinds": {"graph_candidate": 1},
        "statuses": {"pending": 1},
    }
    child = next(
        row for row in report["records"]
        if row["category"] == "knowledge.wiki_candidates" and row["entry_type"] == "logical_record"
    )
    assert child["candidate"] == {
        "action": "add_edge",
        "base_or_revision_evidence": True,
        "kind": "graph_candidate",
        "identity_evidence": True,
        "payload_sha256": child["candidate"]["payload_sha256"],
        "resolution": child["candidate"]["resolution"],
        "source_artifact_sha256": child["candidate"]["source_artifact_sha256"],
        "status": "pending",
        "target_sha256": child["candidate"]["target_sha256"],
    }
    assert child["candidate"]["resolution"]["status"] == "resolved"

    omitted = json.loads(json.dumps(report))
    omitted_child = next(
        row
        for row in omitted["records"]
        if row["category"] == "knowledge.wiki_candidates"
        and row["entry_type"] == "logical_record"
    )
    del omitted_child["candidate"]["resolution"]
    omitted["relationships"] = [
        row
        for row in omitted["relationships"]
        if row.get("from_record_id") != omitted_child["record_id"]
    ]
    omitted["summary"]["relationships"] = len(omitted["relationships"])
    assert "candidate_relationship_closure" in inventory_contract_errors(
        omitted, verify_manifest=False
    )

    inconsistent_parent = json.loads(json.dumps(report))
    parent = next(
        row
        for row in inconsistent_parent["records"]
        if row["category"] == "knowledge.wiki_candidates"
        and row["record_kind"] == "physical_file"
    )
    parent["parse"]["candidate_counts"]["evidence"]["resolved"] = 0
    assert "candidate_parent_closure" in inventory_contract_errors(
        inconsistent_parent, verify_manifest=False
    )

    malformed_child = json.loads(json.dumps(report))
    malformed = next(
        row
        for row in malformed_child["records"]
        if row["category"] == "knowledge.wiki_candidates"
        and row["record_kind"] == "logical_record"
    )
    malformed["candidate"]["payload_sha256"] = "raw-private-content"
    malformed["candidate"]["identity_evidence"] = "not-bool"
    assert "candidate_relationship_closure" in inventory_contract_errors(
        malformed_child, verify_manifest=False
    )


@pytest.mark.unit
def test_empty_candidate_parent_uses_the_reconciled_public_count_shape(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    candidates = (
        roots["knowledge"][0]
        / "fixture-user"
        / "wiki"
        / "graph"
        / "pending_diffs.json"
    )
    candidates.parent.mkdir(parents=True)
    candidates.write_text("[]", encoding="utf-8")

    report, exit_code = _run(roots)

    assert exit_code == 0, report["errors"]
    parent = next(
        row
        for row in report["records"]
        if row["category"] == "knowledge.wiki_candidates"
        and row["record_kind"] == "physical_file"
    )
    assert parent["parse"]["candidate_counts"] == {
        "actions": {},
        "evidence": {
            "base_or_revision": 0,
            "identity_evidence": 0,
            "resolved": 0,
            "source": 0,
            "target": 0,
        },
        "kinds": {},
        "statuses": {},
    }
    assert inventory_contract_errors(report) == ()


@pytest.mark.unit
def test_hidden_exact_duplicate_evidence_is_bidirectionally_closed(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    wiki = roots["knowledge"][0] / "fixture-user" / "wiki"
    source = wiki / "sources" / "same.md"
    hidden = wiki / "pages" / "same.md"
    source.parent.mkdir(parents=True)
    hidden.parent.mkdir(parents=True)
    source.write_text("# same\n", encoding="utf-8")
    hidden.write_text("# same\n", encoding="utf-8")

    report, exit_code = _run(roots)
    assert exit_code == 0
    assert inventory_contract_errors(report) == ()
    hidden_row = next(
        row
        for row in report["records"]
        if row["category"] == "knowledge.wiki_hidden_pages"
    )
    assert len(hidden_row["exact_source_duplicate_record_ids"]) == 1

    omitted = json.loads(json.dumps(report))
    omitted["relationships"] = [
        row
        for row in omitted["relationships"]
        if row.get("relationship_type") != "exact_content_duplicate"
    ]
    omitted["summary"]["relationships"] = len(omitted["relationships"])
    assert "hidden_duplicate_relationship_closure" in inventory_contract_errors(
        omitted, verify_manifest=False
    )


@pytest.mark.unit
def test_allow_partial_never_relaxes_code_scan_hard_gate(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)

    def failed_provider(_: Path) -> dict[str, Any]:
        raise RuntimeError("raw provider detail must not escape")

    report, exit_code = inventory_snapshot(
        roots["legacy"],
        roots["knowledge"],
        "sanitized-snapshot",
        runtime_root=roots["runtime"],
        user_data_root=roots["user_data"],
        sessions_root=roots["sessions"],
        workspace_root=roots["workspace"],
        allow_partial=True,
        clock=_clock,
        code_coverage_provider=failed_provider,
        root_probe=_probe,
        snapshot_kind="frozen_copy",
        snapshot_created_at_utc="2026-08-12T00:00:00.000000Z",
        snapshot_attestation_sha256="a" * 64,
        scratch_root=roots["scratch"],
    )

    assert exit_code == 2
    assert "CODE_SCAN_FAILED" in {row["code"] for row in report["errors"]}
    assert "raw provider detail must not escape" not in json.dumps(report)


@pytest.mark.unit
def test_allow_partial_never_relaxes_source_change_hard_gate(tmp_path: Path) -> None:
    roots = _mkdirs(tmp_path)
    calls = 0

    def changing_provider(_: Path) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        report = _coverage(Path())
        report["files"] = []
        report["source_fingerprint_sha256"] = f"fingerprint-{calls}"
        return report

    report, exit_code = inventory_snapshot(
        roots["legacy"],
        roots["knowledge"],
        "sanitized-snapshot",
        runtime_root=roots["runtime"],
        user_data_root=roots["user_data"],
        sessions_root=roots["sessions"],
        workspace_root=roots["workspace"],
        allow_partial=True,
        clock=_clock,
        code_coverage_provider=changing_provider,
        root_probe=_probe,
        snapshot_kind="frozen_copy",
        snapshot_created_at_utc="2026-08-12T00:00:00.000000Z",
        snapshot_attestation_sha256="a" * 64,
        scratch_root=roots["scratch"],
    )

    assert exit_code == 2
    assert "SOURCE_CHANGED" in {row["code"] for row in report["errors"]}


@pytest.mark.unit
def test_hard_error_policy_is_central_and_data_quality_remains_partial() -> None:
    assert {
        "CLI_INVALID_ARGUMENT",
        "CODE_SCAN_FAILED",
        "FILE_DISAPPEARED",
        "PATH_ESCAPES_ROOT",
        "PATH_KEY_COLLISION",
        "ROOT_DUPLICATE",
        "ROOT_INELIGIBLE",
        "ROOT_MISSING",
        "ROOT_NOT_ABSOLUTE",
        "ROOT_NOT_DIRECTORY",
        "ROOT_OVERLAP",
        "RECONCILIATION_INCOMPLETE",
        "SOURCE_CHANGED",
        "SQLITE_SNAPSHOT_INCONSISTENT",
        "UNMAPPED_CALLSITE",
        "UNSAFE_REPARSE_ENTRY",
    }.issubset(HARD_ERROR_CODES)
    assert has_hard_errors(
        [{"code": "ROOT_INELIGIBLE", "severity": "error"}]
    )
    assert not has_hard_errors(
        [
            {"code": "INVALID_JSON", "severity": "error"},
            {"code": "INVALID_SQLITE", "severity": "error"},
            {"code": "MISSING_REFERENCE", "severity": "error"},
            {"code": "UNKNOWN_ENTRY", "severity": "error"},
        ]
    )


@pytest.mark.unit
def test_enumeration_denial_preserves_an_unavailable_terminal_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    denied = roots["knowledge"][0] / "fixture-user" / "wiki" / "sources"
    denied.mkdir(parents=True)
    (denied / "sealed.md").write_text("# sealed\n", encoding="utf-8")
    original_scandir = legacy_inventory.os.scandir

    def guarded_scandir(path: Any) -> Any:
        if Path(path).resolve() == denied.resolve():
            raise PermissionError("synthetic enumeration denial")
        return original_scandir(path)

    monkeypatch.setattr(legacy_inventory.os, "scandir", guarded_scandir)

    report, exit_code = _run(roots, allow_partial=True)

    assert exit_code == 0
    assert "ENUMERATION_DENIED" in {row["code"] for row in report["errors"]}
    unavailable = next(
        row
        for row in report["records"]
        if row["relative_path"] == "fixture-user/wiki/sources"
    )
    assert unavailable["entry_type"] == "unavailable"
    assert unavailable["record_kind"] == "unavailable"
    assert unavailable["proposed_terminal_classification"] == "unavailable"
    assert unavailable["classification_authority"] == "scanner"
    assert inventory_contract_errors(report) == ()


@pytest.mark.unit
def test_allow_partial_never_relaxes_sqlite_snapshot_consistency(
    tmp_path: Path,
) -> None:
    roots = _mkdirs(tmp_path)
    _register_fixture_user(roots)
    database = roots["user_data"] / "fixture-user" / "notion" / "notion.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE pages (id TEXT PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()
    Path(str(database) + "-wal").write_bytes(b"sanitized-wal")
    Path(str(database) + "-journal").write_bytes(b"sanitized-journal")

    report, exit_code = _run(roots, allow_partial=True)

    assert exit_code == 2
    assert "SQLITE_SNAPSHOT_INCONSISTENT" in {
        row["code"] for row in report["errors"]
    }

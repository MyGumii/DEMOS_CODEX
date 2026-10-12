from __future__ import annotations

from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

import pytest

from code_assist_v1.knowledge_core.legacy.plan_contract import (
    ACTION_TO_TERMINAL,
    DECISION_TABLE,
    DECISION_TABLE_SHA256,
    ORGANIZATION_MAPPING_HEADER,
    PLAN_REPORT_SCHEMA,
    PLAN_SCHEMA_VERSION,
    PLANNER_NAME,
    PLANNER_VERSION,
    canonical_json_bytes,
    expected_planner_code_revision,
    plan_contract_errors,
    reviewed_plan_sha256,
    sha256_canonical,
    technical_plan_sha256,
)
from code_assist_v1.knowledge_core.legacy.planner import build_plan_draft
from code_assist_v1.tools.legacy_inventory import inventory_snapshot
from code_assist_v1.tools.legacy_inventory_contract import (
    CATEGORY_IDS,
    canonical_json_bytes as canonical_inventory_json_bytes,
    inventory_contract_errors,
    manifest_sha256,
)


_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_SHA_D = "d" * 64
_RESPONSIBILITY_ID = "11111111-1111-4111-8111-111111111111"
_TEAM_ID = "22222222-2222-4222-8222-222222222222"
_PART_ID = "33333333-3333-4333-8333-333333333333"


def _mapping_id(row: dict[str, Any]) -> str:
    return "map_" + sha256_canonical(
        {key: row[key] for key in sorted(row) if key != "mapping_id"}
    )[:24]


def _owner_mapping(
    *,
    claim: str | None = "admin",
    record_id: str | None = None,
    decision: str = "personal",
    user_id: str | None = "admin",
    system_role: str | None = "admin",
    account_status: str | None = "active",
    assigned: bool = False,
    leader: bool = False,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "source_owner_claim": claim,
        "source_record_id": record_id,
        "decision": decision,
        "user_id": user_id,
        "system_role": system_role,
        "account_status": account_status,
        "responsibility_code": "AMHS" if assigned else None,
        "responsibility_id": _RESPONSIBILITY_ID if assigned else None,
        "team_code": "TEAM_A" if assigned else None,
        "team_id": _TEAM_ID if assigned else None,
        "part_code": "PART_A" if assigned else None,
        "part_id": _PART_ID if assigned else None,
        "is_part_leader": leader,
    }
    if decision == "quarantine":
        for key in (
            "user_id",
            "system_role",
            "account_status",
            "responsibility_code",
            "responsibility_id",
            "team_code",
            "team_id",
            "part_code",
            "part_id",
        ):
            row[key] = None
        row["is_part_leader"] = False
    row["mapping_id"] = _mapping_id(row)
    return row


def _root_fact(role: str, root_id: str, ordinal: int) -> dict[str, Any]:
    path = f"C:\\sanitized\\{ordinal:02d}-{root_id}"
    return {
        "configured_path": path,
        "filesystem_identity": {"device": 100, "inode": ordinal + 1},
        "path_source": "explicit",
        "resolved_path": path,
        "role": role,
        "root_id": root_id,
        "reparse": {"components": [], "present": False},
        "volume": {"drive_type": "fixed", "filesystem": "NTFS", "root": "C:\\"},
    }


def _source_snapshot() -> dict[str, Any]:
    roles = ("legacy", "knowledge", "runtime", "sessions", "user_data", "workspace")
    roots = [_root_fact(role, role, index) for index, role in enumerate(roles)]
    high_water = []
    for role in roles:
        marker = {
            "directories": None if role == "legacy" else 0,
            "entries": 0,
            "files": 0,
            "tree_sha256": _SHA_A,
        }
        high_water.append(
            {
                "after": deepcopy(marker),
                "before": deepcopy(marker),
                "changed": False,
                "root_id": role,
            }
        )
    snapshot: dict[str, Any] = {
        "attestation_sha256": _SHA_B,
        "created_at_utc": "2026-08-13T00:00:00.000000Z",
        "high_water_marks": high_water,
        "kind": "frozen_copy",
        "roles": [
            {"role": role, "root_ids": [role], "status": "present"}
            for role in roles
        ],
        "root_binding_sha256": "",
        "roots": roots,
        "scratch_root": _root_fact("scratch", "scratch", len(roles)),
        "snapshot_id": "sanitized-plan-contract",
    }
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
    return snapshot


def _source_record(
    *,
    logical: bool,
    parent_record_id: str | None = None,
) -> dict[str, Any]:
    owner = (
        {
            "claimed_user_id": "admin",
            "kind": "personal",
            "mapping_status": "confirmed",
            "user_id": "admin",
        }
        if logical
        else {
            "claimed_user_id": None,
            "kind": "system",
            "mapping_status": "confirmed",
            "user_id": None,
        }
    )
    ordinal = 1 if logical else 0
    identity = {
        "category": "identity.users_registry",
        "owner": owner,
        "path": "users.json",
        "root_id": "runtime",
        "source_ordinal": ordinal,
    }
    record_id = "rec_" + sha256_canonical(identity)[:24]
    parse: dict[str, Any] = {
        "child_count": 0,
        "logical_sha256": _SHA_C,
        "record_count": 1,
        "schema_version": None,
        "status": "valid",
    }
    if logical:
        parse["registry_account"] = {
            "account_status": "active",
            "payload_sha256": _SHA_D,
            "role": "admin",
            "user_id_sha256": hashlib.sha256(b"admin").hexdigest(),
        }
    return {
        "category": "identity.users_registry",
        "classification_authority": "planner_required",
        "classification_reason_codes": [],
        "entry_type": "logical_record" if logical else "file",
        "owner": owner,
        "parent_record_id": parent_record_id,
        "parse": parse,
        "path_key": "users.json",
        "planned_action": "import",
        "proposed_terminal_classification": "imported",
        "reason_codes": [],
        "record_id": record_id,
        "record_kind": "logical_record" if logical else "physical_file",
        "references": {"declared": 0, "missing": 0, "resolved": 0},
        "relative_path": "users.json",
        "root_id": "runtime",
        "sha256": _SHA_C,
        "size_bytes": 0 if logical else 99,
        "source_class": "identity",
        "source_identity": {
            "path_key": "users.json",
            "root_id": "runtime",
            "source_ordinal": ordinal,
        },
        "source_ordinal": ordinal,
        "stat": {"mtime_ns": 1},
        "status": "active",
    }


def _organization(*, assigned: bool) -> dict[str, Any]:
    value: dict[str, Any] = {
        "responsibility_units": (
            [{"code": "AMHS", "id": _RESPONSIBILITY_ID}] if assigned else []
        ),
        "team_units": (
            [
                {
                    "code": "TEAM_A",
                    "id": _TEAM_ID,
                    "responsibility_id": _RESPONSIBILITY_ID,
                }
            ]
            if assigned
            else []
        ),
        "part_units": (
            [
                {
                    "code": "PART_A",
                    "id": _PART_ID,
                    "leader_user_id": "admin",
                    "team_id": _TEAM_ID,
                }
            ]
            if assigned
            else []
        ),
    }
    value["aggregate_sha256"] = sha256_canonical(value)
    return value


def _contract_probe(path: str) -> dict[str, Any]:
    return {
        "eligible": True,
        "exists": True,
        "reason_codes": [],
        "resolved_path": str(Path(path).resolve(strict=True)),
        "syntactic_unc": False,
        "volume": {"drive_type": "fixed", "filesystem": "NTFS", "root": "T:\\"},
    }


def _contract_coverage(_: Path) -> dict[str, Any]:
    files = [{"language": "python", "path": "fixture.py", "sha256": _SHA_A}]
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
                "file_sha256": _SHA_A,
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
            [["fixture.py", _SHA_A]],
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
            "sha256": _SHA_B,
            "unused_mapping_ids": [],
        },
        "report_schema": "code_assist.legacy_inventory.code_coverage.v1",
        "source_fingerprint_sha256": source_fingerprint_sha256,
        "status": "complete",
        "summary": {
            "discovered": 8,
            "mapped": 8,
            "pass": True,
            "retained_excluded": 0,
            "submitter_count": 8,
            "unclassified": 0,
            "unmapped_submitters": 0,
        },
        "unclassified_callsite_ids": [],
        "unmapped_submitter_ids": [],
    }


@lru_cache(maxsize=2)
def _cached_valid_draft(assigned: bool) -> dict[str, Any]:
    temporary_parent = os.environ.get("TEMP") or None
    with TemporaryDirectory(prefix="p22-contract-", dir=temporary_parent) as temporary:
        base = Path(temporary)
        roots = {
            name: base / name
            for name in (
                "legacy",
                "knowledge",
                "runtime",
                "user_data",
                "sessions",
                "workspace",
                "scratch",
            )
        }
        for path in roots.values():
            path.mkdir()
        (roots["runtime"] / "users.json").write_text(
            json.dumps(
                {
                    "users": {
                        "admin": {
                            "disabled": False,
                            "password_hash": "SANITIZED_TEST_HASH",
                            "role": "admin",
                        }
                    }
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        report, exit_code = inventory_snapshot(
            roots["legacy"],
            [roots["knowledge"]],
            "sanitized-plan-contract",
            runtime_root=roots["runtime"],
            user_data_root=roots["user_data"],
            sessions_root=roots["sessions"],
            workspace_root=roots["workspace"],
            snapshot_kind="frozen_copy",
            snapshot_created_at_utc="2026-08-13T00:00:00.000000Z",
            snapshot_attestation_sha256=_SHA_B,
            scratch_root=roots["scratch"],
            clock=lambda: datetime(2026, 8, 13, 1, 2, 3, tzinfo=timezone.utc),
            code_coverage_provider=_contract_coverage,
            root_probe=_contract_probe,
        )
        assert exit_code == 0, report["errors"]
        assert inventory_contract_errors(report) == ()
        inventory_bytes = canonical_inventory_json_bytes(report) + b"\n"
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, dialect="excel", lineterminator="\r\n")
        writer.writerow(ORGANIZATION_MAPPING_HEADER)
        writer.writerow(
            [
                "1",
                "admin",
                "",
                "personal",
                "admin",
                "admin",
                "active",
                "AMHS" if assigned else "",
                _RESPONSIBILITY_ID if assigned else "",
                "TEAM_A" if assigned else "",
                _TEAM_ID if assigned else "",
                "PART_A" if assigned else "",
                _PART_ID if assigned else "",
                "true" if assigned else "false",
            ]
        )
        mapping_bytes = stream.getvalue().encode("utf-8")
        return build_plan_draft(
            inventory_bytes,
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
        )


def _valid_draft(*, assigned: bool = False) -> dict[str, Any]:
    return deepcopy(_cached_valid_draft(assigned))


def _rehash_draft(draft: dict[str, Any]) -> None:
    records = draft["records"]
    relationships = draft["relationships"]
    mappings = draft["owner_mappings"]
    organization = draft["organization"]
    draft["inputs"]["inventory"]["record_count"] = len(records)
    draft["inputs"]["inventory"]["records_sha256"] = sha256_canonical(
        [row["source_record"] for row in records]
    )
    draft["inputs"]["inventory"]["relationship_count"] = len(relationships)
    draft["inputs"]["inventory"]["relationships_sha256"] = sha256_canonical(
        [row["source_relationship"] for row in relationships]
    )
    draft["inputs"]["organization_mapping"]["row_count"] = len(mappings)
    draft["inputs"]["organization_mapping"]["rows_sha256"] = sha256_canonical(
        mappings
    )
    terminal = Counter(row["final_terminal_classification"] for row in records)
    reconciliation = draft["reconciliation"]
    reconciliation.update(
        {
            "action_counts": dict(sorted(Counter(row["action"] for row in records).items())),
            "category_counts": dict(
                sorted(Counter(row["category"] for row in records).items())
            ),
            "classified_records": len(records),
            "execution_phase_counts": dict(
                sorted(Counter(row["execution_phase"] for row in records).items())
            ),
            "import_mode_counts": dict(
                sorted(
                    Counter(
                        row["import_mode"]
                        for row in records
                        if row["import_mode"] is not None
                    ).items()
                )
            ),
            "organization_sha256": organization["aggregate_sha256"],
            "owner_mapping_count": len(mappings),
            "records_sha256": sha256_canonical(records),
            "relationships_sha256": sha256_canonical(relationships),
            "target_part_record_count": sum(
                row["target_space_kind"] == "part" for row in records
            ),
            "terminal_classification_counts": dict(sorted(terminal.items())),
            "total_records": len(records),
            "total_relationships": len(relationships),
            "unclassified_records": 0,
        }
    )
    draft["technical_plan_sha256"] = technical_plan_sha256(draft)


def _rehash_embedded_inventory(draft: dict[str, Any]) -> None:
    source_inventory = draft["source_inventory"]
    records_by_id = {
        row["record_id"]: row["source_record"] for row in draft["records"]
    }
    relationships_by_id = {
        row["relationship_id"]: row["source_relationship"]
        for row in draft["relationships"]
    }
    reconstructed = dict(source_inventory["metadata"])
    reconstructed["records"] = [
        records_by_id[identity] for identity in source_inventory["record_order"]
    ]
    reconstructed["relationships"] = [
        relationships_by_id[identity]
        for identity in source_inventory["relationship_order"]
    ]
    digest = manifest_sha256(reconstructed)
    source_inventory["metadata"]["manifest_sha256"] = digest
    draft["inputs"]["inventory"]["manifest_sha256"] = digest
    reconstructed["manifest_sha256"] = digest
    draft["inputs"]["inventory"]["document_sha256"] = hashlib.sha256(
        canonical_inventory_json_bytes(reconstructed) + b"\n"
    ).hexdigest()


@pytest.mark.unit
def test_plan_contract_freezes_all_41_category_routes_and_public_vocabularies() -> None:
    expected = {
        "context.memory": ("retain_external", "none"),
        "context.memory_events": ("retain_external", "none"),
        "context.project_assets": ("retain_external", "none"),
        "context.project_chunks": ("retain_external", "none"),
        "context.project_scores": ("retain_external", "none"),
        "context.project_sources": ("retain_external", "none"),
        "context.user_skills": ("retain_external", "none"),
        "context.workspace_files": ("retain_external", "none"),
        "conversation.global_sessions": ("import", "P2.5"),
        "conversation.user_sessions": ("import", "P2.5"),
        "external.notion_backup_candidates": ("quarantine", "none"),
        "external.notion_database": ("retain_external", "none"),
        "external.notion_files": ("retain_external", "none"),
        "external.notion_session_links": ("import", "P2.5"),
        "external.notion_database_sidecars": ("retain_external", "none"),
        "external.outlook_state": ("retain_external", "none"),
        "identity.auth_audit": ("import", "P2.4"),
        "identity.users_registry": ("import", "P2.3"),
        "knowledge.legacy_edges": ("import", "P2.4"),
        "knowledge.legacy_flat_markdown": ("import", "P2.3"),
        "knowledge.legacy_inbox": ("import", "P2.3"),
        "knowledge.legacy_raw": ("import", "P2.3"),
        "knowledge.legacy_raw_chunks": ("import", "P2.3"),
        "knowledge.legacy_thoughts": ("import", "P2.3"),
        "knowledge.tombstones": ("import", "P2.4"),
        "knowledge.wiki_assets": ("import", "P2.3"),
        "knowledge.wiki_backlinks": ("rebuild", "P2.6"),
        "knowledge.wiki_candidates": ("import", "P2.4"),
        "knowledge.wiki_graph_edges": ("import", "P2.4"),
        "knowledge.wiki_graph_nodes": ("import", "P2.4"),
        "knowledge.wiki_health": ("rebuild", "P2.6"),
        "knowledge.wiki_hidden_pages": ("import", "P2.3"),
        "knowledge.wiki_index_markdown": ("rebuild", "P2.6"),
        "knowledge.wiki_index_sqlite": ("rebuild", "P2.6"),
        "knowledge.wiki_log": ("import", "P2.4"),
        "knowledge.wiki_sources": ("import", "P2.3"),
        "operations.jobs": ("import", "P2.5"),
        "operations.server_logs": ("quarantine", "none"),
        "operations.temp_recovery": ("quarantine", "none"),
        "unavailable.browser_pins": ("record_unavailable", "none"),
        "unknown.unclassified": ("quarantine", "none"),
    }
    observed = {
        row["category"]: (row["action"], row["execution_phase"])
        for row in DECISION_TABLE
    }

    assert len(CATEGORY_IDS) == 41
    assert observed == expected
    assert set(observed) == set(CATEGORY_IDS)
    assert DECISION_TABLE_SHA256 == sha256_canonical(DECISION_TABLE)
    assert tuple(ORGANIZATION_MAPPING_HEADER) == (
        "mapping_schema_version",
        "source_owner_claim",
        "source_record_id",
        "decision",
        "user_id",
        "system_role",
        "account_status",
        "responsibility_code",
        "responsibility_id",
        "team_code",
        "team_id",
        "part_code",
        "part_id",
        "is_part_leader",
    )
    assert ACTION_TO_TERMINAL == {
        "import": "imported",
        "quarantine": "quarantined",
        "rebuild": "rebuilt",
        "record_unavailable": "unavailable",
        "retain_external": "retained_external",
    }


@pytest.mark.unit
def test_valid_draft_has_exact_shapes_canonical_hashes_and_no_part_target() -> None:
    draft = _valid_draft(assigned=True)

    assert plan_contract_errors(draft) == ()
    assert list(json.loads(canonical_json_bytes(draft))) == sorted(draft)
    assert draft["technical_plan_sha256"] == technical_plan_sha256(draft)
    assert draft["reconciliation"]["records_sha256"] == sha256_canonical(
        draft["records"]
    )
    assert draft["reconciliation"]["target_part_record_count"] == 0
    assert {row["target_space_kind"] for row in draft["records"]} == {
        None,
        "personal",
    }
    assert all(row["target_space_kind"] != "part" for row in draft["records"])


@pytest.mark.unit
def test_contract_validator_is_total_for_arbitrary_json_shaped_values() -> None:
    malformed: list[Any] = [
        None,
        True,
        False,
        0,
        1.5,
        "draft",
        [],
        ["migration_plan_draft"],
        {},
        {"stage": []},
        {"stage": "migration_plan_draft", "records": [[]]},
        {"stage": "reviewed_migration_plan", "draft": {"stage": []}},
        {"stage": "migration_plan_draft", "owner_mappings": [{"mapping_id": []}]},
    ]
    for value in malformed:
        errors = plan_contract_errors(value)
        assert isinstance(errors, tuple), repr(value)
        assert errors, repr(value)
        assert errors == tuple(sorted(set(errors))), repr(value)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda value: value.__setitem__("unexpected", True), "draft_shape"),
        (
            lambda value: value["planner"].__setitem__("unexpected", True),
            "planner_shape",
        ),
        (
            lambda value: value["inputs"]["inventory"].__setitem__(
                "unexpected", True
            ),
            "inventory_binding",
        ),
        (
            lambda value: value["owner_mappings"][0].__setitem__(
                "unexpected", True
            ),
            "owner_mapping_shape",
        ),
        (
            lambda value: value["records"][0].__setitem__("unexpected", True),
            "record_shape",
        ),
        (
            lambda value: value["reconciliation"].__setitem__(
                "unexpected", True
            ),
            "reconciliation_shape",
        ),
    ],
)
def test_contract_rejects_every_unknown_plan_member(
    mutate: Callable[[dict[str, Any]], None], expected: str
) -> None:
    draft = _valid_draft()
    mutate(draft)
    assert expected in plan_contract_errors(draft, verify_hash=False)


@pytest.mark.unit
def test_contract_rejects_tampered_hashes_collections_and_order() -> None:
    draft = _valid_draft()

    tampered = deepcopy(draft)
    tampered["records"][0]["reason_codes"] = ["tampered"]
    assert "technical_plan_hash" in plan_contract_errors(tampered)

    wrong_record_hash = deepcopy(draft)
    wrong_record_hash["records"][0]["inventory_record_sha256"] = _SHA_D
    assert "record_binding" in plan_contract_errors(
        wrong_record_hash, verify_hash=False
    )

    wrong_collection_hash = deepcopy(draft)
    wrong_collection_hash["inputs"]["inventory"]["records_sha256"] = _SHA_D
    assert "inventory_collection_binding" in plan_contract_errors(
        wrong_collection_hash, verify_hash=False
    )

    reversed_records = deepcopy(draft)
    reversed_records["records"].reverse()
    assert "record_order" in plan_contract_errors(
        reversed_records, verify_hash=False
    )

    duplicate_reason = deepcopy(draft)
    duplicate_reason["records"][0]["reason_codes"] = ["x", "x"]
    assert "record_lists" in plan_contract_errors(
        duplicate_reason, verify_hash=False
    )


@pytest.mark.unit
def test_contract_rejects_category_decision_table_drift_even_when_rehashed() -> None:
    draft = _valid_draft()
    row = draft["records"][0]
    row["category"] = "knowledge.wiki_index_markdown"
    row["source_record"]["category"] = "knowledge.wiki_index_markdown"
    row["inventory_record_sha256"] = sha256_canonical(row["source_record"])
    row["action"] = "import"
    row["execution_phase"] = "P2.3"
    row["final_terminal_classification"] = "imported"
    row["import_mode"] = "personal_item"
    _rehash_draft(draft)

    assert plan_contract_errors(draft), "rebuild-only category accepted as import"


@pytest.mark.unit
def test_contract_rejects_mapping_chain_or_code_id_drift_even_when_rehashed() -> None:
    draft = _valid_draft(assigned=True)
    mapping = draft["owner_mappings"][0]
    mapping["responsibility_code"] = "OTHER_RESPONSIBILITY"
    mapping["mapping_id"] = _mapping_id(mapping)
    logical = next(row for row in draft["records"] if row["resolved_owner_user_id"])
    logical["owner_mapping_id"] = mapping["mapping_id"]
    _rehash_draft(draft)

    assert plan_contract_errors(draft), "mapping code was not bound to organization IDs"


@pytest.mark.unit
def test_contract_requires_at_least_one_explicit_active_admin_mapping() -> None:
    draft = _valid_draft()
    mapping = draft["owner_mappings"][0]
    mapping["system_role"] = "user"
    mapping["mapping_id"] = _mapping_id(mapping)
    logical = next(row for row in draft["records"] if row["resolved_owner_user_id"])
    logical["owner_mapping_id"] = mapping["mapping_id"]
    _rehash_draft(draft)

    assert plan_contract_errors(draft), "plan with no active administrator was accepted"


@pytest.mark.unit
def test_contract_rejects_unsanitized_embedded_snapshot_and_source_record() -> None:
    draft = _valid_draft()
    snapshot_secret = deepcopy(draft)
    snapshot_secret["source_snapshot"]["raw_secret"] = "TOP_SECRET_SENTINEL"
    snapshot_secret["technical_plan_sha256"] = technical_plan_sha256(snapshot_secret)
    assert plan_contract_errors(snapshot_secret), "source snapshot shape was not exact"

    record_secret = deepcopy(draft)
    record = record_secret["records"][0]
    record["source_record"]["raw_secret"] = "TOP_SECRET_SENTINEL"
    record["inventory_record_sha256"] = sha256_canonical(record["source_record"])
    _rehash_draft(record_secret)
    assert plan_contract_errors(record_secret), "embedded inventory record was not exact"

    for key in ("raw_content", "secret_value", "password_value", "api_token_value"):
        nested_secret = deepcopy(draft)
        record = nested_secret["records"][0]
        record["source_record"]["parse"][key] = "TOP_SECRET_SENTINEL"
        record["inventory_record_sha256"] = sha256_canonical(record["source_record"])
        _rehash_embedded_inventory(nested_secret)
        _rehash_draft(nested_secret)
        assert "source_inventory_contract" in plan_contract_errors(
            nested_secret
        ), f"nested sensitive key accepted: {key}"


@pytest.mark.unit
def test_contract_rejects_impossible_embedded_snapshot_time_after_full_rehash() -> None:
    draft = _valid_draft()
    snapshot = draft["source_snapshot"]
    snapshot["created_at_utc"] = "2026-02-31T00:00:00.000000Z"
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
    draft["source_inventory"]["metadata"]["source_snapshot"] = deepcopy(snapshot)
    draft["inputs"]["inventory"]["root_binding_sha256"] = snapshot[
        "root_binding_sha256"
    ]
    _rehash_embedded_inventory(draft)
    _rehash_draft(draft)

    errors = plan_contract_errors(draft)
    assert errors == ("source_inventory_contract",)


@pytest.mark.unit
def test_contract_rejects_nested_embedded_category_extra_after_full_rehash() -> None:
    draft = _valid_draft()
    draft["source_inventory"]["metadata"]["categories"][0]["safe_extra"] = {
        "nested": {"value": "sanitized"}
    }
    _rehash_embedded_inventory(draft)
    _rehash_draft(draft)

    errors = plan_contract_errors(draft)
    assert errors == ("source_inventory_contract",)


@pytest.mark.unit
def test_contract_binds_embedded_semantic_document_in_draft_and_reviewed_plan() -> None:
    draft = _valid_draft()
    inventory_binding = draft["inputs"]["inventory"]
    raw_document_sha256 = inventory_binding["document_sha256"]
    completed_at = "2026-08-13T01:02:04.000000Z"
    assert (
        draft["source_inventory"]["metadata"]["run"]["completed_at_utc"]
        != completed_at
    )

    draft["source_inventory"]["metadata"]["run"][
        "completed_at_utc"
    ] = completed_at
    draft["technical_plan_sha256"] = technical_plan_sha256(draft)

    assert inventory_binding["document_sha256"] == raw_document_sha256
    assert plan_contract_errors(draft) == (
        "source_inventory_document_binding",
    )

    reviewed: dict[str, Any] = {
        "report_schema": PLAN_REPORT_SCHEMA,
        "schema_version": PLAN_SCHEMA_VERSION,
        "stage": "reviewed_migration_plan",
        "draft": draft,
        "review": {
            "confirmed_technical_plan_sha256": draft["technical_plan_sha256"],
            "review_attestation_sha256": _SHA_C,
            "reviewed_at_utc": "2026-08-13T01:02:05.000000Z",
            "reviewer_id": "migration-reviewer",
        },
        "plan_sha256": "",
    }
    reviewed["plan_sha256"] = reviewed_plan_sha256(reviewed)

    assert plan_contract_errors(reviewed) == (
        "source_inventory_document_binding",
    )


@pytest.mark.unit
def test_review_wrapper_binds_exact_draft_hash_attestation_and_real_utc_time() -> None:
    draft = _valid_draft()
    reviewed: dict[str, Any] = {
        "report_schema": PLAN_REPORT_SCHEMA,
        "schema_version": PLAN_SCHEMA_VERSION,
        "stage": "reviewed_migration_plan",
        "draft": draft,
        "review": {
            "confirmed_technical_plan_sha256": draft["technical_plan_sha256"],
            "review_attestation_sha256": _SHA_C,
            "reviewed_at_utc": "2026-08-13T01:02:03.000000Z",
            "reviewer_id": "migration-reviewer",
        },
        "plan_sha256": "",
    }
    reviewed["plan_sha256"] = reviewed_plan_sha256(reviewed)
    assert plan_contract_errors(reviewed) == ()

    stale = deepcopy(reviewed)
    stale["review"]["confirmed_technical_plan_sha256"] = _SHA_D
    stale["plan_sha256"] = reviewed_plan_sha256(stale)
    assert "review_binding" in plan_contract_errors(stale)

    impossible_time = deepcopy(reviewed)
    impossible_time["review"]["reviewed_at_utc"] = "2026-02-31T01:02:03.000000Z"
    impossible_time["plan_sha256"] = reviewed_plan_sha256(impossible_time)
    assert "review_binding" in plan_contract_errors(impossible_time)

    wrapper_tamper = deepcopy(reviewed)
    wrapper_tamper["review"]["reviewer_id"] = "other-reviewer"
    assert "reviewed_plan_hash" in plan_contract_errors(wrapper_tamper)


@pytest.mark.unit
def test_canonical_plan_json_rejects_non_json_and_never_serializes_secret_by_accident() -> None:
    draft = _valid_draft()
    encoded = canonical_json_bytes(draft)

    assert b"TOP_SECRET_SENTINEL" not in encoded
    assert encoded == canonical_json_bytes(json.loads(encoded))
    assert hashlib.sha256(encoded).hexdigest() != draft["technical_plan_sha256"]

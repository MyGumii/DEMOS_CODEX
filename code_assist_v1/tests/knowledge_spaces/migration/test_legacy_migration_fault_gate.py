from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

from code_assist_v1.knowledge_core.legacy.migration_apply import (
    LegacyMigrationApplyService,
)
from code_assist_v1.knowledge_core.legacy.migration_verify import (
    LegacyMigrationVerifier,
    verification_contract_errors,
)
from code_assist_v1.knowledge_core.legacy.plan_contract import (
    canonical_json_bytes,
    plan_contract_errors,
)
from code_assist_v1.knowledge_core.legacy.planner import (
    build_plan_draft,
    confirm_migration_plan,
)
from code_assist_v1.tests.knowledge_spaces.migration.test_legacy_inventory_cli import (
    _new_snapshot,
    _populate_dirty_graph_and_assets,
    _populate_realistic_snapshot,
    _write_json,
    _write_text,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_content_importer import (
    _counts,
    _factory,
    _reviewed_fixture,
    _storage_layout,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_migration_planner import (
    _clock,
    _csv_bytes,
    _mapping_row,
)
from code_assist_v1.tools.legacy_inventory import inventory_snapshot
from code_assist_v1.tools.legacy_inventory_contract import (
    CATEGORY_IDS,
    canonical_json_bytes as canonical_inventory_json_bytes,
    inventory_contract_errors,
)


pytestmark = pytest.mark.migration

_ATTESTATION = "7" * 64


@dataclass(frozen=True)
class _GateFixture:
    plan_bytes: bytes
    plan_sha256: str
    inventory: dict[str, Any]
    draft: dict[str, Any]
    snapshot: Any


def _reviewed_gate_fixture(
    tmp_path: Path,
    *,
    name: str,
    realistic: bool,
    dirty: bool = False,
    bulk_records: int = 0,
    long_unicode_path: bool = False,
) -> _GateFixture:
    snapshot = _new_snapshot(tmp_path, name)
    if realistic:
        _populate_realistic_snapshot(snapshot)
    _write_json(
        snapshot.runtime_root / "users.json",
        {
            "users": {
                "fixture-user": {
                    "disabled": False,
                    "password_hash": "SANITIZED_TEST_HASH",
                    "role": "admin",
                }
            }
        },
    )
    if dirty:
        if not realistic:
            raise AssertionError("dirty fixtures require the realistic baseline")
        _populate_dirty_graph_and_assets(snapshot)
        _write_text(
            snapshot.knowledge_roots[0]
            / "fixture-user"
            / "wiki"
            / "sources"
            / "near-miss.md.tmp",
            "quarantine this recovery file\n",
        )
    if bulk_records:
        for ordinal in range(bulk_records):
            _write_text(
                snapshot.knowledge_roots[0]
                / "fixture-user"
                / "wiki"
                / "sources"
                / f"bulk-{ordinal:04d}.md",
                f"# bulk {ordinal}\n\nrecord {ordinal}\n",
            )
    if long_unicode_path:
        long_relative = (
            Path("raw")
            / ("한글-" + "가" * 70)
            / ("조합-" + "나" * 70)
            / ("경로-" + "다" * 70)
            / "문서-끝.md"
        )
        target = snapshot.knowledge_roots[0] / "fixture-user" / long_relative
        extended_target = Path("\\\\?\\" + str(target.absolute()))
        _write_text(extended_target, "# 긴 유니코드 경로\n")
        assert len(str(target.absolute())) > 260

    report, exit_code = inventory_snapshot(
        snapshot.legacy_root,
        snapshot.knowledge_roots,
        f"p2.7-{name}",
        runtime_root=snapshot.runtime_root,
        user_data_root=snapshot.user_data_root,
        sessions_root=snapshot.sessions_root,
        workspace_root=snapshot.workspace_root,
        snapshot_kind="frozen_copy",
        snapshot_created_at_utc="2026-08-13T00:00:00.000000Z",
        snapshot_attestation_sha256=_ATTESTATION,
        scratch_root=snapshot.scratch_root,
        allow_partial=True,
        clock=_clock,
    )
    assert exit_code == 0, report["errors"]
    assert inventory_contract_errors(report) == ()

    mapping_rows = [
        _mapping_row(
            claim="fixture-user",
            user_id="fixture-user",
            role="admin",
        )
    ]
    mapping_rows.extend(
        _mapping_row(record_id=str(row["record_id"]), decision="quarantine")
        for row in report["records"]
        if row["category"] == "conversation.global_sessions"
    )
    mapping_bytes = _csv_bytes(mapping_rows)
    inventory_bytes = canonical_inventory_json_bytes(report) + b"\n"
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
        allow_data_quality_errors=True,
    )
    assert plan_contract_errors(draft) == ()
    draft_bytes = canonical_json_bytes(draft) + b"\n"
    reviewed = confirm_migration_plan(
        draft_bytes,
        expected_draft_sha256=hashlib.sha256(draft_bytes).hexdigest(),
        review_attestation_sha256="8" * 64,
        reviewed_at_utc="2026-08-13T02:00:00.000000Z",
        reviewer_id="p2.7-fixture-reviewer",
    )
    assert plan_contract_errors(reviewed) == ()
    return _GateFixture(
        canonical_json_bytes(reviewed) + b"\n",
        str(reviewed["plan_sha256"]),
        report,
        draft,
        snapshot,
    )


def _table_counts(factory: Any) -> dict[str, int]:
    connection = factory.open()
    try:
        tables = [
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: int(
                connection.execute(f'SELECT count(*) AS n FROM "{table}"').fetchone()[
                    "n"
                ]
            )
            for table in tables
        }
    finally:
        connection.close()


@pytest.mark.scenario("MIG-001")
def test_mig_001_empty_content_fixture_migrates_and_verifies(
    tmp_path: Path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_gate_fixture(
        tmp_path,
        name="empty",
        realistic=False,
    )
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    result = LegacyMigrationApplyService(factory, layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=1,
    )
    assert result.identity.imported_user_count == 1
    report = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert verification_contract_errors(report) == ()
    assert report["reconciliation"]["unresolved_records"] == 0
    assert report["reconciliation"]["unresolved_relationships"] == 0


@pytest.mark.scenario("MIG-008")
@pytest.mark.scenario("MIG-007")
@pytest.mark.scenario("MIG-006")
@pytest.mark.scenario("MIG-004")
@pytest.mark.scenario("MIG-002")
def test_realistic_fixture_closes_plan_rebuild_scope_and_exact_rerun(
    tmp_path: Path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_gate_fixture(
        tmp_path,
        name="realistic",
        realistic=True,
    )
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    service = LegacyMigrationApplyService(factory, layout)
    first_apply = service.apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=3,
    )
    first_verify = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert verification_contract_errors(first_verify) == ()
    assert len(first_verify["records"]) == len(fixture.draft["records"])
    assert len(first_verify["relationships"]) == len(fixture.draft["relationships"])
    assert first_verify["reconciliation"]["unresolved_records"] == 0
    assert first_verify["reconciliation"]["unresolved_relationships"] == 0
    expected_terminals = Counter(
        str(row["final_terminal_classification"])
        for row in fixture.draft["records"]
    )
    assert first_verify["reconciliation"]["terminal_classification_counts"] == dict(
        sorted(expected_terminals.items())
    )
    assert {
        row["source_record"]["category"] for row in fixture.draft["records"]
    } == {
        row["category"]
        for row in fixture.inventory["categories"]
        if row["records"]
    }
    assert {row["category"] for row in fixture.inventory["categories"]} == CATEGORY_IDS

    rebuild_ids = {
        str(row["record_id"])
        for row in fixture.draft["records"]
        if row["action"] == "rebuild"
    }
    assert rebuild_ids
    assert all(
        row["mapping_id"] is None
        and row["observed_terminal_classification"] == "rebuilt"
        for row in first_verify["records"]
        if row["record_id"] in rebuild_ids
    )
    connection = factory.open()
    try:
        assert connection.execute(
            "SELECT count(*) AS n FROM knowledge_item AS i "
            "JOIN knowledge_space AS s ON s.id=i.space_id "
            "WHERE s.space_kind<>'personal'"
        ).fetchone()["n"] == 0
        assert connection.execute(
            "SELECT count(*) AS n FROM knowledge_space WHERE space_kind='part'"
        ).fetchone()["n"] == 0
    finally:
        connection.close()
    global_rows = [
        row
        for row in first_verify["records"]
        if next(
            plan_row
            for plan_row in fixture.draft["records"]
            if plan_row["record_id"] == row["record_id"]
        )["category"]
        == "conversation.global_sessions"
    ]
    assert global_rows and all(
        row["observed_terminal_classification"] == "quarantined"
        and row["mapping_id"] is None
        for row in global_rows
    )

    counts = _table_counts(factory)
    second_apply = service.apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=3,
    )
    second_verify = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert second_apply.inserted_mapping_count == 0
    assert second_verify == first_verify
    assert _table_counts(factory) == counts
    assert first_apply.inserted_mapping_count > second_apply.inserted_mapping_count


@pytest.mark.scenario("MIG-003")
def test_dirty_fixture_has_complete_quarantine_and_zero_silent_loss(
    tmp_path: Path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_gate_fixture(
        tmp_path,
        name="dirty",
        realistic=True,
        dirty=True,
    )
    assert any(
        row["proposed_terminal_classification"] in {"quarantined", "unavailable"}
        for row in fixture.inventory["records"]
    )
    assert any(
        relationship["status"] in {"duplicate", "orphan"}
        for relationship in fixture.inventory["relationships"]
    )
    assert any(
        "missing_asset_reference" in row["reason_codes"]
        and row["proposed_terminal_classification"] == "unavailable"
        for row in fixture.inventory["records"]
    )
    assert any(
        row["source_record"]["relative_path"].endswith("near-miss.md.tmp")
        and row["action"] == "quarantine"
        for row in fixture.draft["records"]
    )
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    LegacyMigrationApplyService(factory, layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    report = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert verification_contract_errors(report) == ()
    assert report["reconciliation"]["observed_record_count"] == len(
        fixture.inventory["records"]
    )
    assert report["reconciliation"]["observed_relationship_count"] == len(
        fixture.inventory["relationships"]
    )
    assert report["reconciliation"]["unresolved_records"] == 0
    assert report["reconciliation"]["unresolved_relationships"] == 0
    assert any(
        row["observed_terminal_classification"] == "quarantined"
        for row in report["records"]
    )
    assert any(
        row["observed_terminal_classification"] == "unavailable"
        for row in report["records"]
    )
    assert all(
        row["mapping_id"] is None
        and row["observed_terminal_classification"] == "quarantined"
        for row in report["relationships"]
        if next(
            relationship
            for relationship in fixture.draft["relationships"]
            if relationship["relationship_id"] == row["relationship_id"]
        )["action"]
        == "quarantine"
    )


def test_large_unicode_long_path_fixture_crosses_bounded_batches(
    tmp_path: Path, isolated_knowledge_layout: Any, request: pytest.FixtureRequest
) -> None:
    fixture = _reviewed_gate_fixture(
        tmp_path,
        name="large-unicode",
        realistic=False,
        bulk_records=205,
        long_unicode_path=True,
    )
    raw_root = (
        fixture.snapshot.knowledge_roots[0] / "fixture-user" / "raw"
    ).absolute()
    extended_raw_root = Path("\\\\?\\" + str(raw_root))
    request.addfinalizer(
        lambda: shutil.rmtree(extended_raw_root, ignore_errors=False)
        if extended_raw_root.exists()
        else None
    )
    imported_items = [
        row
        for row in fixture.draft["records"]
        if row["action"] == "import" and row["import_mode"] == "personal_item"
    ]
    assert len(imported_items) == 206
    assert any(
        len(
            str(
                fixture.snapshot.knowledge_roots[0]
                / row["source_record"]["relative_path"]
            )
        )
        > 260
        for row in imported_items
    )
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    result = LegacyMigrationApplyService(factory, layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=100,
    )
    assert len(result.content_batches) == 3
    assert sum(len(batch.records) for batch in result.content_batches) == 206
    assert max(len(batch.records) for batch in result.content_batches) <= 100
    report = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert verification_contract_errors(report) == ()
    assert report["reconciliation"]["unresolved_records"] == 0


def test_before_checkpoint_failpoint_rolls_back_identity_effects(
    tmp_path: Path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    baseline_space_count = _table_counts(factory)["knowledge_space"]

    def failpoint(name: str) -> None:
        if name == "migration.before_checkpoint":
            raise RuntimeError("checkpoint interruption")

    with pytest.raises(RuntimeError, match="checkpoint interruption"):
        LegacyMigrationApplyService(
            factory,
            _storage_layout(isolated_knowledge_layout),
            failpoint=failpoint,
        ).apply(
            fixture.plan_bytes,
            expected_plan_sha256=fixture.plan_sha256,
            batch_size=1,
        )
    assert _counts(factory)["legacy_migration_map"] == 0
    connection = factory.open()
    try:
        assert connection.execute("SELECT count(*) AS n FROM app_user").fetchone()[
            "n"
        ] == 0
        assert connection.execute(
            "SELECT count(*) AS n FROM knowledge_space"
        ).fetchone()["n"] == baseline_space_count
    finally:
        connection.close()


@pytest.mark.scenario("MIG-005")
def test_process_kill_after_committed_batch_resumes_from_durable_checkpoint(
    tmp_path: Path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    plan_path = tmp_path / "reviewed-plan.json"
    plan_path.write_bytes(fixture.plan_bytes)
    script = f"""
import os
from pathlib import Path
from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.legacy.migration_apply import LegacyMigrationApplyService
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG, MigrationStorageLayout

def terminate(name):
    if name == 'migration.after_batch_commit':
        os._exit(73)

factory = ConnectionFactory._for_isolated_tests(
    Path({str(factory.database_path)!r}), CANONICAL_MIGRATION_CATALOG
)
layout = MigrationStorageLayout(
    data_root=Path({str(layout.data_root)!r}),
    database=Path({str(layout.database)!r}),
    blob_root=Path({str(layout.blob_root)!r}),
    projection_root=Path({str(layout.projection_root)!r}),
)
LegacyMigrationApplyService(factory, layout, failpoint=terminate).apply(
    Path({str(plan_path)!r}).read_bytes(),
    expected_plan_sha256={fixture.plan_sha256!r},
    batch_size=1,
)
raise SystemExit(74)
"""
    child = subprocess.run(
        [sys.executable, "-E", "-c", script],
        cwd=Path(__file__).resolve().parents[4],
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert child.returncode == 73, (child.stdout, child.stderr)
    midway = _counts(factory)
    assert midway["legacy_migration_map"] == 2
    assert midway["knowledge_item"] == 0

    resumed = LegacyMigrationApplyService(factory, layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=1,
    )
    assert resumed.inserted_mapping_count == 3
    report = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert verification_contract_errors(report) == ()
    assert report["reconciliation"]["unresolved_records"] == 0
    assert report["reconciliation"]["unresolved_relationships"] == 0

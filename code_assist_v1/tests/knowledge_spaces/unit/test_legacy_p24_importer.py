from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

import pytest
import code_assist_v1.tools.windows_volume as windows_volume

from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.legacy.migration_apply import (
    LegacyMigrationApplyService,
)
from code_assist_v1.knowledge_core.legacy.p24_importer import (
    LegacyP24Importer,
)
from code_assist_v1.knowledge_core.legacy.plan_contract import canonical_json_bytes
from code_assist_v1.knowledge_core.legacy.planner import confirm_migration_plan
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_content_importer import (
    _factory,
    _reviewed_fixture,
    _storage_layout,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_migration_planner import (
    _base_mapping_rows,
    _plan_from_fixture,
    planner_fixture,
)


pytestmark = pytest.mark.unit


def _reviewed(planner_fixture: Mapping[str, Any]) -> tuple[bytes, str, dict[str, Any]]:
    draft = _plan_from_fixture(planner_fixture, _base_mapping_rows())
    draft_bytes = canonical_json_bytes(draft) + b"\n"
    reviewed = confirm_migration_plan(
        draft_bytes,
        expected_draft_sha256=hashlib.sha256(draft_bytes).hexdigest(),
        review_attestation_sha256="d" * 64,
        reviewed_at_utc="2026-08-13T03:00:00.000000Z",
        reviewer_id="p2.4-reviewer",
    )
    return canonical_json_bytes(reviewed) + b"\n", str(reviewed["plan_sha256"]), draft


def _selected(draft: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    records = tuple(
        sorted(
            str(row["record_id"])
            for row in draft["records"]
            if row["action"] == "import"
            and row["execution_phase"] == "P2.4"
            and (
                row["import_mode"] in {"graph_node", "graph_edge", "finding"}
                or row["import_mode"] == "source_container"
            )
        )
    )
    relationships = tuple(
        sorted(
            str(row["relationship_id"])
            for row in draft["relationships"]
            if row["action"] == "import"
            and row["execution_phase"] == "P2.4"
            and row["import_mode"] in {"graph_node", "graph_edge", "finding"}
        )
    )
    assert len(records) >= 4
    assert len(relationships) >= 5
    return records, relationships


def test_p24_fixture_freezes_remaining_history_surface(
    planner_fixture: Mapping[str, Any],
) -> None:
    _, _, draft = _reviewed(planner_fixture)
    remaining = sorted(
        (
            str(row["category"]),
            str(row["source_record"]["record_kind"]),
            str(row["import_mode"]),
        )
        for row in draft["records"]
        if row["action"] == "import"
        and row["execution_phase"] == "P2.4"
        and not (
            row["source_record"]["record_kind"] == "logical_record"
            and row["import_mode"] in {"graph_node", "graph_edge", "finding"}
        )
    )
    assert remaining == [
        ("knowledge.wiki_graph_edges", "physical_file", "source_container")
    ]


def _use_planner_fixture_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    def probe(value: str | Path) -> dict[str, object]:
        path = Path(value)
        return {
            "eligible": True,
            "exists": path.exists(),
            "reason_codes": [],
            "resolved_path": str(path.resolve(strict=True)),
            "syntactic_unc": False,
            "volume": {
                "drive_type": "fixed",
                "drive_type_code": 3,
                "filesystem": "NTFS",
                "root": "T:\\",
            },
            "reparse": {"components": [], "present": False},
        }

    monkeypatch.setattr(windows_volume, "probe_path", probe)


def test_p24_imports_reviewed_nodes_edges_findings_and_replays_exactly(
    planner_fixture: Mapping[str, Any],
    isolated_knowledge_layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_planner_fixture_probe(monkeypatch)
    reviewed, plan_sha, draft = _reviewed(planner_fixture)
    records, relationships = _selected(draft)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    applied = LegacyMigrationApplyService(factory, layout).apply(
        reviewed,
        expected_plan_sha256=plan_sha,
        batch_size=10,
    )
    importer = LegacyP24Importer(factory)

    replay = importer.apply_batch(
        reviewed,
        selected_record_ids=records,
        selected_relationship_ids=relationships,
        expected_plan_sha256=plan_sha,
    )

    assert sum(len(batch.records) for batch in applied.p24_batches) == len(records)
    assert sum(len(batch.relationships) for batch in applied.p24_batches) == len(relationships)
    assert sum(batch.inserted_mapping_count for batch in applied.p24_batches) == len(records) + len(relationships)
    assert replay.inserted_mapping_count == 0
    connection = factory.open()
    try:
        assert connection.execute("SELECT count(*) FROM knowledge_edge").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM curation_finding").fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map WHERE json_extract(details_json,'$.schema_version')=2"
        ).fetchone()[0] == len(records) - 1
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map WHERE json_extract(details_json,'$.schema_version')=3"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_p24_relationship_map"
        ).fetchone()[0] == len(relationships)
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE actor_display_name='P2.4 legacy migration'"
        ).fetchone()[0] == len(records) + len(relationships)
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE event_type LIKE 'legacy_migration.%' AND aggregate_type IN ('legacy_migration_map','legacy_migration_p24_relationship_map')"
        ).fetchone()[0] >= len(records) + len(relationships)
    finally:
        connection.close()


def test_p24_revalidates_reviewed_personal_space_before_effect_commit(
    planner_fixture: Mapping[str, Any],
    isolated_knowledge_layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_planner_fixture_probe(monkeypatch)
    reviewed, plan_sha, draft = _reviewed(planner_fixture)
    records, _ = _selected(draft)
    logical = next(
        record_id
        for record_id in records
        if next(
            row for row in draft["records"] if row["record_id"] == record_id
        )["source_record"]["record_kind"]
        == "logical_record"
    )
    factory = _factory(isolated_knowledge_layout)

    def stop(name: str) -> None:
        if name == "p24.after_effect":
            raise RuntimeError("establish-p23-prefix")

    with pytest.raises(RuntimeError, match="establish-p23-prefix"):
        LegacyMigrationApplyService(
            factory,
            _storage_layout(isolated_knowledge_layout),
            failpoint=stop,
        ).apply(reviewed, expected_plan_sha256=plan_sha, batch_size=100)

    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """UPDATE knowledge_space
                   SET status='inactive',updated_at=updated_at+1,
                       row_version=row_version+1
                   WHERE space_kind='personal' AND owner_user_id='admin'"""
            )
    finally:
        connection.close()

    with pytest.raises(Exception) as caught:
        LegacyP24Importer(factory).apply_batch(
            reviewed,
            selected_record_ids=(logical,),
            expected_plan_sha256=plan_sha,
        )
    assert getattr(caught.value, "code", None) == "LEGACY_P24_STATE_CONFLICT"
    connection = factory.open()
    try:
        assert connection.execute(
            """SELECT count(*) FROM legacy_migration_map
               WHERE json_extract(details_json,'$.schema_version') IN (2,3)"""
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_p24_exact_hidden_duplicate_is_one_durable_provenance_relationship(
    tmp_path: Path,
    isolated_knowledge_layout,
) -> None:
    duplicate = b"# imported\r\n![asset](../assets/asset.png)\r\n"
    fixture = _reviewed_fixture(
        tmp_path,
        extra_personal_files={"wiki/pages/duplicate.md": duplicate},
    )
    factory = _factory(isolated_knowledge_layout)
    service = LegacyMigrationApplyService(
        factory, _storage_layout(isolated_knowledge_layout)
    )
    first = service.apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=10,
    )
    replay = service.apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=10,
    )
    assert sum(
        len(batch.relationships) for batch in first.p24_batches
    ) == 1
    assert sum(
        batch.inserted_mapping_count for batch in replay.p24_batches
    ) == 0
    connection = factory.open()
    try:
        row = connection.execute(
            """SELECT effect_kind,item_id,from_mapping_id,to_mapping_id
               FROM legacy_migration_p24_relationship_map
               WHERE relationship_type='exact_content_duplicate'"""
        ).fetchone()
        assert row is not None
        assert row["effect_kind"] == "provenance"
        assert row["item_id"] is not None
        endpoint_items = connection.execute(
            """SELECT item_id FROM legacy_migration_map
               WHERE id IN (?,?) ORDER BY id""",
            (row["from_mapping_id"], row["to_mapping_id"]),
        ).fetchall()
        assert [value["item_id"] for value in endpoint_items] == [
            row["item_id"], row["item_id"]
        ]
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failpoint",
    ("p24.after_effect", "p24.after_mapping", "p24.after_relationship_mapping"),
)
def test_p24_failpoints_roll_back_effect_mapping_audit_and_outbox(
    planner_fixture: Mapping[str, Any],
    isolated_knowledge_layout,
    failpoint: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_planner_fixture_probe(monkeypatch)
    reviewed, plan_sha, draft = _reviewed(planner_fixture)
    records, relationships = _selected(draft)
    factory = _factory(isolated_knowledge_layout)
    def stop(name: str) -> None:
        if name == failpoint:
            raise RuntimeError("p24-test-failpoint")

    with pytest.raises(RuntimeError, match="p24-test-failpoint"):
        LegacyMigrationApplyService(
            factory,
            _storage_layout(isolated_knowledge_layout),
            failpoint=stop,
        ).apply(
            reviewed,
            expected_plan_sha256=plan_sha,
            batch_size=100,
        )
    connection = factory.open()
    try:
        logical_record_count = sum(
            row["source_record"]["record_kind"] == "logical_record"
            for row in draft["records"]
            if str(row.get("record_id")) in records
        )
        expected_records = (
            logical_record_count
            if failpoint == "p24.after_relationship_mapping"
            else 0
        )
        assert connection.execute("SELECT count(*) FROM knowledge_edge").fetchone()[0] == (
            1 if expected_records else 0
        )
        assert connection.execute("SELECT count(*) FROM curation_finding").fetchone()[0] == (
            1 if expected_records else 0
        )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map WHERE json_extract(details_json,'$.schema_version')=2"
        ).fetchone()[0] == expected_records
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_p24_relationship_map"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE action LIKE 'legacy_migration.graph_%' OR action='legacy_migration.finding'"
        ).fetchone()[0] == expected_records
    finally:
        connection.close()

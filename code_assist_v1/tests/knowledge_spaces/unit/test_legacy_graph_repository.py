from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.errors import KnowledgeCoreTransactionError
from code_assist_v1.knowledge_core.legacy.migration_apply import LegacyMigrationApplyService
from code_assist_v1.knowledge_core.repositories.graph import (
    GraphRepository,
    LegacyCandidateFinding,
    LegacyGraphConflictError,
    LegacyGraphEdge,
    legacy_candidate_finding_id,
    legacy_graph_edge_id,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_content_importer import (
    _factory,
    _reviewed_fixture,
    _storage_layout,
)


pytestmark = pytest.mark.unit


def _targets(tmp_path: Path, isolated_knowledge_layout):
    fixture = _reviewed_fixture(
        tmp_path,
        extra_personal_files={"wiki/sources/other.md": b"# other\r\n"},
    )
    factory = _factory(isolated_knowledge_layout)
    LegacyMigrationApplyService(factory, _storage_layout(isolated_knowledge_layout)).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    connection = factory.open()
    try:
        rows = connection.execute(
            """SELECT item.id,item.current_revision_id,item.space_id
               FROM knowledge_item AS item
               ORDER BY item.id"""
        ).fetchall()
        assert len(rows) == 2
        return factory, fixture, tuple(dict(row) for row in rows)
    finally:
        connection.close()


def _edge(fixture, rows) -> LegacyGraphEdge:
    source, target = rows
    record_id = "rec_" + "1" * 24
    return LegacyGraphEdge(
        plan_sha256=fixture.plan_sha256,
        inventory_record_id=record_id,
        edge_id=legacy_graph_edge_id(fixture.plan_sha256, record_id),
        owner_user_id="admin",
        space_id=source["space_id"],
        source_item_id=source["id"],
        source_revision_id=source["current_revision_id"],
        target_item_id=target["id"],
        target_revision_id=target["current_revision_id"],
        edge_type="related",
        evidence_json='{"inventory_relationship_sha256":"' + "a" * 64 + '"}',
        created_at_ms=1_786_582_923_000,
    )


def _finding(fixture, rows, *, kind="graph_candidate") -> LegacyCandidateFinding:
    source, target = rows
    record_id = "rec_" + ("2" if kind == "graph_candidate" else "3") * 24
    return LegacyCandidateFinding(
        plan_sha256=fixture.plan_sha256,
        inventory_record_id=record_id,
        finding_id=legacy_candidate_finding_id(fixture.plan_sha256, record_id),
        owner_user_id="admin",
        space_id=source["space_id"],
        source_item_id=source["id"],
        source_revision_id=source["current_revision_id"],
        target_item_id=target["id"],
        target_revision_id=target["current_revision_id"],
        candidate_kind=kind,
        evidence_json='{"candidate_payload_sha256":"' + "b" * 64 + '"}',
        proposed_action_json='{"legacy_action":"add_edge"}',
        created_at_ms=1_786_582_923_000,
    )


def test_legacy_edge_and_candidate_insert_and_replay_exactly(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    factory, fixture, rows = _targets(tmp_path, isolated_knowledge_layout)
    repository = GraphRepository()
    edge = _edge(fixture, rows)
    finding = _finding(fixture, rows)
    connection = factory.open()
    try:
        with transaction(connection):
            first_edge = repository.put_legacy_edge(connection, edge)
            first_finding = repository.put_legacy_candidate(connection, finding)
        with transaction(connection):
            replay_edge = repository.put_legacy_edge(connection, edge)
            replay_finding = repository.put_legacy_candidate(connection, finding)
        assert first_edge.inserted is True
        assert first_finding.inserted is True
        assert replay_edge.inserted is False
        assert replay_finding.inserted is False
        stored_edge = connection.execute(
            "SELECT edge_status,origin,edge_type FROM knowledge_edge WHERE id=?",
            (edge.edge_id,),
        ).fetchone()
        assert tuple(stored_edge) == ("active", "manual", "related")
        stored_finding = connection.execute(
            """SELECT finding_kind,risk_tier,status,decision_mode,
                      required_reviewer,proposed_action,created_actor_kind
               FROM curation_finding WHERE id=?""",
            (finding.finding_id,),
        ).fetchone()
        assert tuple(stored_finding) == (
            "semantic_edge", "T2", "pending", "human", "space_editor",
            "relation", "system",
        )
    finally:
        connection.close()


def test_wiki_diff_becomes_pending_human_near_duplicate(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    factory, fixture, rows = _targets(tmp_path, isolated_knowledge_layout)
    finding = _finding(fixture, rows, kind="wiki_diff")
    connection = factory.open()
    try:
        with transaction(connection):
            GraphRepository().put_legacy_candidate(connection, finding)
        row = connection.execute(
            """SELECT finding_kind,severity,visibility_effect,proposed_action
               FROM curation_finding WHERE id=?""",
            (finding.finding_id,),
        ).fetchone()
        assert tuple(row) == (
            "near_duplicate", "warning", "agent_hidden", "review_duplicate"
        )
    finally:
        connection.close()


@pytest.mark.parametrize("field", ["edge_type", "evidence_json", "target_item_id"])
def test_edge_conflicts_fail_before_any_second_effect(
    tmp_path: Path, isolated_knowledge_layout, field: str
) -> None:
    factory, fixture, rows = _targets(tmp_path, isolated_knowledge_layout)
    edge = _edge(fixture, rows)
    connection = factory.open()
    try:
        with transaction(connection):
            GraphRepository().put_legacy_edge(connection, edge)
        changed = {
            "edge_type": replace(edge, edge_type="supports"),
            "evidence_json": replace(edge, evidence_json='{"changed":true}'),
            "target_item_id": replace(edge, target_item_id=edge.source_item_id),
        }[field]
        with pytest.raises((LegacyGraphConflictError, ValueError)):
            with transaction(connection):
                GraphRepository().put_legacy_edge(connection, changed)
        assert connection.execute("SELECT count(*) FROM knowledge_edge").fetchone()[0] == 1
    finally:
        connection.close()


def test_graph_writes_require_a_managed_transaction(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    factory, fixture, rows = _targets(tmp_path, isolated_knowledge_layout)
    connection = factory.open()
    try:
        with pytest.raises(KnowledgeCoreTransactionError):
            GraphRepository().put_legacy_edge(connection, _edge(fixture, rows))
    finally:
        connection.close()

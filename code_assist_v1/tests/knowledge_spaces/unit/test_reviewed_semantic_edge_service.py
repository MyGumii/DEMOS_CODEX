from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from code_assist_v1.knowledge_core.db import TransactionMode, transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.repositories.findings import (
    FindingDetection,
    FindingEvidenceSpan,
    FindingRepository,
)
from code_assist_v1.knowledge_core.repositories.semantic_edges import (
    SemanticEdgeRepository,
    semantic_edge_id_for_finding,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.services.candidate_retrieval import (
    IncrementalCandidateRequest,
    IncrementalCandidateRetrievalService,
)
from code_assist_v1.knowledge_core.services.findings import (
    RecordFindingCommand,
    ReviewFindingCommand,
)
from code_assist_v1.knowledge_core.services.lifecycle import (
    DeactivateItemCommand,
    ItemLifecycleService,
)
from code_assist_v1.knowledge_core.services.semantic_edges import (
    ApplySemanticEdgeCommand,
    ReviewedSemanticEdgeService,
    SemanticEdgeIdempotencyError,
    SemanticEdgeNotFoundError,
    SemanticEdgeServiceError,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_incremental_candidate_retrieval import (
    _factory,
    _seed_candidates,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_versioned_finding_service import (
    _service as finding_service,
)


pytestmark = pytest.mark.unit


_SOURCE_BODY = "# Cache invalidation\nalpha quorum cache design\n"
_TARGET_BODY = "# Distributed cache\nprotocol quorum notes\n"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _semantic_detection(factory, ids: dict[str, str]) -> FindingDetection:
    snapshot = IncrementalCandidateRetrievalService(factory).retrieve(
        IncrementalCandidateRequest(
            space_id=ids["alice_space"],
            source_item_id=ids["candidate-a"],
            source_revision_id=ids["candidate-a_revision"],
            source_markdown=_SOURCE_BODY,
            top_k=3,
        )
    )
    assert any(
        candidate.item_id == ids["candidate-b"]
        and candidate.revision_id == ids["candidate-b_revision"]
        for candidate in snapshot.candidates
    )
    return FindingDetection(
        space_id=ids["alice_space"],
        finding_kind="semantic_edge",
        severity="info",
        policy_variant=None,
        source_item_id=ids["candidate-a"],
        source_revision_id=ids["candidate-a_revision"],
        target_item_id=ids["candidate-b"],
        target_revision_id=ids["candidate-b_revision"],
        detector_name="semantic_edge_detector",
        detector_version="v1",
        rule_version="p6.4-fixture-v1",
        model_id="fixture-model",
        prompt_version="fixture-prompt-v1",
        confidence=0.87,
        detector_evidence_sha256="d" * 64,
        candidate_retrieval_evidence_sha256=snapshot.evidence_sha256,
        evidence_codes=("semantic_supports",),
        evidence_spans=(
            FindingEvidenceSpan("source", 2, 1, 2, 6, _sha("alpha")),
            FindingEvidenceSpan("target", 2, 1, 2, 9, _sha("protocol")),
        ),
        candidate_snapshot=snapshot,
        source_markdown=_SOURCE_BODY,
        target_markdown=_TARGET_BODY,
        proposed_relation_type="supports",
    )


def _edge_service(factory, *, failpoint=None) -> ReviewedSemanticEdgeService:
    return ReviewedSemanticEdgeService(
        factory,
        ScopeResolver(
            FeatureFlags(
                core_enabled=True,
                personal_reads=True,
                personal_writes=True,
                part_reads=True,
                part_writes=True,
            )
        ),
        failpoint=failpoint,
    )


def _resolver() -> ScopeResolver:
    return ScopeResolver(
        FeatureFlags(
            core_enabled=True,
            personal_reads=True,
            personal_writes=True,
            part_reads=True,
            part_writes=True,
        )
    )


def _record_and_approve(factory, ids: dict[str, str]):
    service = finding_service(factory)
    recorded = service.record(
        RecordFindingCommand(
            detection=_semantic_detection(factory, ids),
            request_id="61000000-0000-4000-8000-000000000001",
        )
    )
    reviewed = service.review(
        ReviewFindingCommand(
            principal=SessionPrincipal("alice", 1),
            finding_id=recorded.finding.id,
            expected_row_version=1,
            decision="approve",
            reason="The fixed evidence supports this explicit semantic relation.",
            operation_id="61000000-0000-4000-8000-000000000002",
            request_id="61000000-0000-4000-8000-000000000003",
        )
    )
    assert reviewed.outcome == "approved"
    return recorded.finding, reviewed


def _apply_command(ids: dict[str, str], finding, reviewed) -> ApplySemanticEdgeCommand:
    return ApplySemanticEdgeCommand(
        principal=SessionPrincipal("alice", 1),
        space_id=ids["alice_space"],
        finding_id=finding.id,
        expected_finding_row_version=reviewed.row_version,
        source_revision_id=ids["candidate-a_revision"],
        target_revision_id=ids["candidate-b_revision"],
        evidence_sha256=hashlib.sha256(finding.evidence_json.encode("ascii")).hexdigest(),
        reason="Approve the relation against the reviewed immutable revisions.",
        operation_id="61000000-0000-4000-8000-000000000004",
        request_id="61000000-0000-4000-8000-000000000005",
        request_meta={"method": "POST", "authorization": "Bearer secret"},
    )


def _state(factory) -> dict[str, object]:
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            finding = connection.execute(
                """SELECT status,row_version,applied_operation_id,applied_dedup_id,
                          applied_by,applied_at FROM curation_finding
                     WHERE finding_kind='semantic_edge' ORDER BY created_at,id LIMIT 1"""
            ).fetchone()
            edge = connection.execute(
                """SELECT id,edge_type,edge_status,origin,approval_finding_id,
                          source_evidence_revision_id,target_evidence_revision_id,
                          evidence_json,row_version FROM knowledge_edge
                     ORDER BY id LIMIT 1"""
            ).fetchone()
            generation = connection.execute(
                "SELECT generation FROM knowledge_space WHERE space_kind='personal' AND owner_user_id='alice'"
            ).fetchone()[0]
            return {
                "audit": int(connection.execute("SELECT count(*) FROM audit_event").fetchone()[0]),
                "edge": None if edge is None else tuple(edge),
                "finding": None if finding is None else tuple(finding),
                "generation": int(generation),
                "edges": int(connection.execute("SELECT count(*) FROM knowledge_edge").fetchone()[0]),
                "operations": int(connection.execute("SELECT count(*) FROM operation_dedup").fetchone()[0]),
                "outbox": int(connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0]),
            }
    finally:
        connection.close()


@pytest.mark.scenario("CUR-005")
@pytest.mark.scenario("TIME-003")
def test_review_does_not_commit_edge_and_explicit_apply_is_atomic_and_idempotent(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    finding, reviewed = _record_and_approve(factory, ids)
    before = _state(factory)
    assert before["edge"] is None
    assert before["finding"][:2] == ("approved", 2)

    command = _apply_command(ids, finding, reviewed)
    result = _edge_service(factory).apply(command)
    assert result.outcome == "applied"
    assert result.finding_row_version == 3
    assert result.edge_id == semantic_edge_id_for_finding(finding.id)
    assert result.edge_row_version == 1

    after = _state(factory)
    assert after["finding"][0:2] == ("applied", 3)
    assert after["edge"][0:5] == (
        result.edge_id,
        "supports",
        "active",
        "semantic_approved",
        finding.id,
    )
    assert after["edge"][5:7] == (
        ids["candidate-a_revision"],
        ids["candidate-b_revision"],
    )
    evidence = json.loads(after["edge"][7])
    assert evidence["finding_id"] == finding.id
    assert evidence["finding_evidence_sha256"] == command.evidence_sha256
    assert after["generation"] == before["generation"] + 1
    assert after["audit"] == before["audit"] + 1
    assert after["outbox"] == before["outbox"] + 1
    assert after["operations"] == before["operations"] + 1

    replay_request = "61000000-0000-4000-8000-000000000006"
    replay = _edge_service(factory).apply(replace(command, request_id=replay_request))
    assert replay == replace(result, request_id=replay_request)
    assert _state(factory) == after


def test_semantic_edge_rejects_unapproved_or_mismatched_evidence_without_effect(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    service = finding_service(factory)
    pending = service.record(
        RecordFindingCommand(
            detection=_semantic_detection(factory, ids),
            request_id="62000000-0000-4000-8000-000000000001",
        )
    ).finding
    baseline = _state(factory)
    command = ApplySemanticEdgeCommand(
        principal=SessionPrincipal("alice", 1),
        space_id=ids["alice_space"],
        finding_id=pending.id,
        expected_finding_row_version=1,
        source_revision_id=ids["candidate-a_revision"],
        target_revision_id=ids["candidate-b_revision"],
        evidence_sha256=hashlib.sha256(pending.evidence_json.encode("ascii")).hexdigest(),
        reason="Attempt before human review.",
        operation_id="62000000-0000-4000-8000-000000000002",
        request_id="62000000-0000-4000-8000-000000000003",
    )
    with pytest.raises(SemanticEdgeServiceError):
        _edge_service(factory).apply(command)
    assert _state(factory) == baseline

    finding, reviewed = _record_and_approve(factory, ids)
    baseline = _state(factory)
    with pytest.raises(SemanticEdgeServiceError):
        _edge_service(factory).apply(
            replace(_apply_command(ids, finding, reviewed), evidence_sha256="0" * 64)
        )
    assert _state(factory) == baseline


def test_semantic_edge_operation_id_is_bound_to_exact_request(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    finding, reviewed = _record_and_approve(factory, ids)
    command = _apply_command(ids, finding, reviewed)
    _edge_service(factory).apply(command)
    with pytest.raises(SemanticEdgeIdempotencyError):
        _edge_service(factory).apply(replace(command, reason="A different approval reason."))


@pytest.mark.parametrize(
    "field,value",
    (
        ("expected_finding_row_version", 1),
        ("source_revision_id", "63000000-0000-4000-8000-000000000001"),
        ("target_revision_id", "63000000-0000-4000-8000-000000000002"),
        ("evidence_sha256", "0" * 64),
    ),
)
def test_semantic_edge_binds_every_reviewed_revision_and_evidence_field(
    tmp_path: Path, field: str, value: object
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    finding, reviewed = _record_and_approve(factory, ids)
    baseline = _state(factory)
    command = replace(_apply_command(ids, finding, reviewed), **{field: value})
    with pytest.raises(SemanticEdgeServiceError):
        _edge_service(factory).apply(command)
    assert _state(factory) == baseline


def test_semantic_edge_requires_current_human_space_editor_authority_without_disclosure(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    finding, reviewed = _record_and_approve(factory, ids)
    baseline = _state(factory)
    command = replace(
        _apply_command(ids, finding, reviewed),
        principal=SessionPrincipal("bob", 1),
        operation_id="64000000-0000-4000-8000-000000000001",
        request_id="64000000-0000-4000-8000-000000000002",
    )
    with pytest.raises(SemanticEdgeNotFoundError):
        _edge_service(factory).apply(command)
    assert _state(factory) == baseline


@pytest.mark.scenario("CUR-007")
def test_changed_endpoint_marks_reviewed_semantic_finding_obsolete_without_edge(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    finding, reviewed = _record_and_approve(factory, ids)
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            item_version = int(
                connection.execute(
                    "SELECT row_version FROM knowledge_item WHERE id=?",
                    (ids["candidate-b"],),
                ).fetchone()[0]
            )
    finally:
        connection.close()
    ItemLifecycleService(factory, _resolver()).deactivate(
        DeactivateItemCommand(
            principal=SessionPrincipal("alice", 1),
            space_id=ids["alice_space"],
            item_id=ids["candidate-b"],
            expected_item_row_version=item_version,
            operation_id="65000000-0000-4000-8000-000000000001",
            request_id="65000000-0000-4000-8000-000000000002",
            reason="Retire the compared target before applying its relation.",
        )
    )
    before = _state(factory)
    result = _edge_service(factory).apply(_apply_command(ids, finding, reviewed))
    assert result.outcome == "obsolete"
    assert result.edge_id is None
    assert result.edge_row_version is None
    assert result.finding_row_version == 3
    assert result.space_generation == before["generation"]
    after = _state(factory)
    assert after["finding"][0:2] == ("obsolete", 3)
    assert after["edges"] == 0
    assert after["generation"] == before["generation"]
    assert after["audit"] == before["audit"] + 1
    assert after["outbox"] == before["outbox"] + 1
    assert after["operations"] == before["operations"] + 1


def test_active_edge_identity_collision_cannot_apply_a_second_semantic_edge(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    finding, reviewed = _record_and_approve(factory, ids)
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO knowledge_edge(
                     id,space_id,source_item_id,target_item_id,edge_type,edge_status,
                     origin,source_evidence_revision_id,target_evidence_revision_id,
                     approval_finding_id,evidence_json,needs_revalidation,created_by,
                     updated_by,created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,?,'active','manual',NULL,NULL,NULL,'{}',0,
                     'alice','alice',700,700,1)""",
                (
                    "66000000-0000-4000-8000-000000000001",
                    ids["alice_space"],
                    ids["candidate-a"],
                    ids["candidate-b"],
                    "supports",
                ),
            )
    finally:
        connection.close()
    baseline = _state(factory)
    with pytest.raises(SemanticEdgeServiceError, match="already occupied"):
        _edge_service(factory).apply(_apply_command(ids, finding, reviewed))
    assert _state(factory) == baseline


def test_obsoletion_failpoint_rolls_back_to_the_reviewed_finding(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    finding, reviewed = _record_and_approve(factory, ids)
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            item_version = int(
                connection.execute(
                    "SELECT row_version FROM knowledge_item WHERE id=?",
                    (ids["candidate-b"],),
                ).fetchone()[0]
            )
    finally:
        connection.close()
    ItemLifecycleService(factory, _resolver()).deactivate(
        DeactivateItemCommand(
            principal=SessionPrincipal("alice", 1),
            space_id=ids["alice_space"],
            item_id=ids["candidate-b"],
            expected_item_row_version=item_version,
            operation_id="67000000-0000-4000-8000-000000000001",
            request_id="67000000-0000-4000-8000-000000000002",
            reason="Retire the target before testing stale rollback.",
        )
    )
    baseline = _state(factory)

    def stop(name: str) -> None:
        if name == "semantic_edge.after_obsolete":
            raise RuntimeError(name)

    with pytest.raises(RuntimeError, match="after_obsolete"):
        _edge_service(factory, failpoint=stop).apply(
            _apply_command(ids, finding, reviewed)
        )
    assert _state(factory) == baseline


@pytest.mark.parametrize(
    "failpoint",
    (
        "semantic_edge.after_finding_apply",
        "semantic_edge.after_edge_insert",
        "semantic_edge.after_effect",
        "semantic_edge.after_audit",
        "semantic_edge.after_outbox",
        "semantic_edge.before_commit",
    ),
)
def test_semantic_edge_failpoints_roll_back_finding_edge_generation_and_events(
    tmp_path: Path, failpoint: str
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    finding, reviewed = _record_and_approve(factory, ids)
    baseline = _state(factory)

    def stop(name: str) -> None:
        if name == failpoint:
            raise RuntimeError(failpoint)

    with pytest.raises(RuntimeError, match=failpoint):
        _edge_service(factory, failpoint=stop).apply(
            _apply_command(ids, finding, reviewed)
        )
    assert _state(factory) == baseline


def test_semantic_edge_persisted_output_is_body_free_and_request_meta_is_sanitized(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    finding, reviewed = _record_and_approve(factory, ids)
    command = replace(
        _apply_command(ids, finding, reviewed),
        request_meta={
            "method": "POST",
            "path": "/api/knowledge/v1/semantic-edge",
            "authorization": "Bearer semantic-edge-secret",
            "cookie": "session=semantic-edge-secret",
        },
    )
    _edge_service(factory).apply(command)
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            serialized = "\n".join(
                str(row[0])
                for table, column in (
                    ("knowledge_edge", "evidence_json"),
                    ("audit_event", "details_json"),
                    ("audit_event", "request_meta_json"),
                    ("outbox_event", "payload_json"),
                    ("operation_dedup", "result_json"),
                )
                for row in connection.execute(f"SELECT {column} FROM {table}").fetchall()
                if row[0] is not None
            )
    finally:
        connection.close()
    assert _SOURCE_BODY not in serialized
    assert _TARGET_BODY not in serialized
    assert "semantic-edge-secret" not in serialized
    assert "authorization" not in serialized
    assert "cookie" not in serialized

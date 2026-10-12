from __future__ import annotations

from dataclasses import replace
import hashlib
import inspect
from pathlib import Path

import pytest

from code_assist_v1.knowledge_core.db import TransactionMode, transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.repositories.findings import (
    FindingConflictError,
    FindingDetection,
    FindingEvidenceSpan,
    FindingPolicyError,
    FindingRepository,
    finding_policy,
)
from code_assist_v1.knowledge_core.repositories import findings as repository_module
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.services.candidate_retrieval import (
    IncrementalCandidateRequest,
    IncrementalCandidateRetrievalService,
)
from code_assist_v1.knowledge_core.services.findings import (
    FindingAuthorizationError,
    FindingIdempotencyError,
    FindingNotFoundError,
    ReadFindingRequest,
    RecordFindingCommand,
    ReviewFindingCommand,
    VersionedFindingService,
)
from code_assist_v1.knowledge_core.services import findings as service_module
from code_assist_v1.tests.knowledge_spaces.unit.test_incremental_candidate_retrieval import (
    _factory,
    _retrieve,
    _seed_candidates,
)


pytestmark = pytest.mark.unit


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _near_duplicate(factory, ids: dict[str, str]) -> FindingDetection:
    target = "# Cache invalidation\nalpha quorum cache design\n"
    snapshot = _retrieve(factory, ids, top_k=3)
    assert any(candidate.item_id == ids["candidate-a"] for candidate in snapshot.candidates)
    return FindingDetection(
        space_id=ids["alice_space"],
        finding_kind="near_duplicate",
        severity="warning",
        policy_variant=None,
        source_item_id=ids["source"],
        source_revision_id=ids["source_revision"],
        target_item_id=ids["candidate-a"],
        target_revision_id=ids["candidate-a_revision"],
        detector_name="semantic_detector",
        detector_version="v1",
        rule_version="p6.3-test-v1",
        model_id="fixture-model",
        prompt_version="fixture-prompt-v1",
        confidence=0.91,
        detector_evidence_sha256="d" * 64,
        candidate_retrieval_evidence_sha256=snapshot.evidence_sha256,
        evidence_codes=("semantic_near_duplicate",),
        evidence_spans=(
            FindingEvidenceSpan("source", 2, 1, 2, 6, _sha("alpha")),
            FindingEvidenceSpan("target", 2, 1, 2, 6, _sha("alpha")),
        ),
        candidate_snapshot=snapshot,
        source_markdown=ids["source_body"],
        target_markdown=target,
    )


def _service(factory) -> VersionedFindingService:
    connection = factory.open()
    try:
        with transaction(connection):
            spaces = SpaceRepository()
            rows = connection.execute(
                "SELECT id FROM knowledge_space WHERE space_kind='personal' ORDER BY id"
            ).fetchall()
            for row in rows:
                current = spaces.require_by_id(connection, str(row[0]))
                if not current.write_enabled:
                    spaces.update_state(
                        connection,
                        space_id=current.id,
                        expected_row_version=current.row_version,
                        status="active",
                        write_enabled=True,
                        updated_at_ms=120,
                    )
    finally:
        connection.close()
    return VersionedFindingService(
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
    )


def _counts(factory) -> dict[str, int]:
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            return {
                table: int(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                )
                for table in (
                    "curation_finding",
                    "audit_event",
                    "outbox_event",
                    "operation_dedup",
                )
            }
    finally:
        connection.close()


@pytest.mark.scenario("CUR-003")
def test_versioned_finding_persists_exact_body_free_evidence(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    repository = FindingRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            result = repository.put_detection(
                connection, _near_duplicate(factory, ids), now_ms=1_000
            )
            assert result.inserted is True
            assert result.finding.risk_tier == "T2"
            assert result.finding.decision_mode == "human"
            assert result.finding.required_reviewer == "space_editor"
            assert result.finding.visibility_effect == "agent_hidden"
            assert result.finding.proposed_action == "review_duplicate"
            evidence = result.finding.evidence
            assert set(evidence) == {
                "candidate_retrieval_evidence_sha256",
                "detector_evidence_sha256",
                "evidence_codes",
                "evidence_spans",
                "finding_policy_version",
                "schema_version",
                "source_content_sha256",
                "source_normalized_content_sha256",
                "target_content_sha256",
                "target_normalized_content_sha256",
            }
            assert ids["source_body"] not in result.finding.evidence_json
            replay = repository.put_detection(
                connection, _near_duplicate(factory, ids), now_ms=2_000
            )
            assert replay.inserted is False
            assert replay.finding == result.finding
    finally:
        connection.close()


def test_finding_span_hash_is_rebound_to_canonical_revision(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    invalid = _near_duplicate(factory, ids)
    invalid = replace(
        invalid,
        evidence_spans=(
            FindingEvidenceSpan("source", 2, 1, 2, 6, "0" * 64),
            FindingEvidenceSpan("target", 2, 1, 2, 6, _sha("alpha")),
        ),
    )
    connection = factory.open()
    try:
        with transaction(connection):
            with pytest.raises(FindingPolicyError, match="excerpt hash"):
                FindingRepository().put_detection(connection, invalid, now_ms=1_000)
    finally:
        connection.close()


def test_service_records_and_reviews_in_one_audited_idempotent_transaction(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    service = _service(factory)
    recorded = service.record(
        RecordFindingCommand(
            detection=_near_duplicate(factory, ids),
            request_id="10000000-0000-4000-8000-000000000001",
            request_meta={"path": "/internal/findings", "cookie": "secret"},
        )
    )
    assert recorded.inserted is True
    baseline = _counts(factory)
    assert baseline["curation_finding"] == 5
    assert baseline["audit_event"] == 1
    assert baseline["outbox_event"] == 1
    command = ReviewFindingCommand(
        principal=SessionPrincipal("alice", 1),
        finding_id=recorded.finding.id,
        expected_row_version=1,
        decision="approve",
        reason="Reviewed against the exact source and target revisions.",
        operation_id="10000000-0000-4000-8000-000000000002",
        request_id="10000000-0000-4000-8000-000000000003",
        request_meta={"method": "POST", "authorization": "Bearer sentinel"},
    )
    result = service.review(command)
    assert result.outcome == "approved"
    assert result.row_version == 2
    after = _counts(factory)
    assert after["curation_finding"] == baseline["curation_finding"]
    assert after["audit_event"] == baseline["audit_event"] + 1
    assert after["outbox_event"] == baseline["outbox_event"] + 1
    assert after["operation_dedup"] == baseline["operation_dedup"] + 1
    replay_request = "10000000-0000-4000-8000-000000000004"
    replay = service.review(replace(command, request_id=replay_request))
    assert replay == replace(result, request_id=replay_request)
    assert _counts(factory) == after
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            serialized = "\n".join(
                str(row[0])
                for table, column in (
                    ("curation_finding", "evidence_json"),
                    ("audit_event", "details_json || request_meta_json"),
                    ("outbox_event", "payload_json"),
                )
                for row in connection.execute(
                    f"SELECT {column} FROM {table} ORDER BY rowid"
                ).fetchall()
            )
            assert ids["source_body"] not in serialized
            assert "Bearer sentinel" not in serialized
            assert '"cookie"' not in serialized
    finally:
        connection.close()


def test_finding_detail_read_reauthorizes_curator_and_is_nondisclosing(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    service = _service(factory)
    recorded = service.record(
        RecordFindingCommand(
            detection=_near_duplicate(factory, ids),
            request_id="10500000-0000-4000-8000-000000000001",
        )
    )
    baseline = _counts(factory)
    value = service.read(
        ReadFindingRequest(
            principal=SessionPrincipal("alice", 1),
            space_id=ids["alice_space"],
            finding_id=recorded.finding.id,
        )
    )
    assert replace(
        value,
        source_space_id=None,
        selected_view_space_id=None,
        inherited=False,
        origin=None,
        overlay_grant_id=None,
        capabilities=(),
        corpus_closure_sha256=None,
    ) == recorded.finding
    assert value.source_space_id == ids["alice_space"]
    assert value.selected_view_space_id == ids["alice_space"]
    assert value.origin == "native"
    assert value.inherited is False
    assert "read" in value.capabilities
    assert value.corpus_closure_sha256 is not None
    assert value.evidence["evidence_spans"][0]["excerpt_sha256"] == _sha("alpha")
    assert _counts(factory) == baseline

    with pytest.raises(FindingNotFoundError):
        service.read(
            ReadFindingRequest(
                principal=SessionPrincipal("bob", 1),
                space_id=ids["alice_space"],
                finding_id=recorded.finding.id,
            )
        )
    assert _counts(factory) == baseline


def test_review_idempotency_rejects_changed_decision(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    service = _service(factory)
    finding = service.record(
        RecordFindingCommand(
            _near_duplicate(factory, ids), "20000000-0000-4000-8000-000000000001"
        )
    ).finding
    command = ReviewFindingCommand(
        principal=SessionPrincipal("alice", 1),
        finding_id=finding.id,
        expected_row_version=1,
        decision="reject",
        reason="The proposed match is not meaningful.",
        operation_id="20000000-0000-4000-8000-000000000002",
        request_id="20000000-0000-4000-8000-000000000003",
    )
    service.review(command)
    before = _counts(factory)
    with pytest.raises(FindingIdempotencyError):
        service.review(replace(command, decision="approve"))
    assert _counts(factory) == before


@pytest.mark.parametrize(
    "failpoint",
    [
        "curation.after_review_state",
        "curation.after_visibility_state",
        "curation.after_domain_effect",
        "finding.after_review_audit",
        "finding.after_review_outbox",
        "finding.before_review_commit",
    ],
)
def test_review_failpoints_roll_back_finding_audit_outbox_and_dedup(
    tmp_path: Path, failpoint: str
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    recorded = _service(factory).record(
        RecordFindingCommand(
            _near_duplicate(factory, ids), "30000000-0000-4000-8000-000000000001"
        )
    )
    before = _counts(factory)

    def fail(name: str) -> None:
        if name == failpoint:
            raise RuntimeError("bounded failpoint")

    service = VersionedFindingService(
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
        failpoint=fail,
    )
    with pytest.raises(RuntimeError, match="bounded failpoint"):
        service.review(
            ReviewFindingCommand(
                principal=SessionPrincipal("alice", 1),
                finding_id=recorded.finding.id,
                expected_row_version=1,
                decision="ignore",
                reason="Suppress this exact detector input.",
                operation_id="30000000-0000-4000-8000-000000000002",
                request_id="30000000-0000-4000-8000-000000000003",
            )
        )
    assert _counts(factory) == before


@pytest.mark.scenario("CUR-006")
@pytest.mark.scenario("CUR-011")
def test_t3_review_records_approval_but_never_executes_lifecycle_effect(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    detection = FindingDetection(
        space_id=ids["alice_space"],
        finding_kind="purge",
        severity="warning",
        policy_variant=None,
        source_item_id=ids["source"],
        source_revision_id=ids["source_revision"],
        target_item_id=None,
        target_revision_id=None,
        detector_name="retention_policy",
        detector_version="v1",
        rule_version="p6.3-test-v1",
        model_id=None,
        prompt_version=None,
        confidence=None,
        detector_evidence_sha256="e" * 64,
        candidate_retrieval_evidence_sha256=None,
        evidence_codes=("purge_eligible_candidate",),
        evidence_spans=(),
        candidate_snapshot=None,
        source_markdown=None,
        target_markdown=None,
    )
    service = _service(factory)
    finding = service.record(
        RecordFindingCommand(detection, "40000000-0000-4000-8000-000000000001")
    ).finding
    result = service.review(
        ReviewFindingCommand(
            principal=SessionPrincipal("alice", 1),
            finding_id=finding.id,
            expected_row_version=1,
            decision="approve",
            reason="Approval only; purge remains a separate administrator action.",
            operation_id="40000000-0000-4000-8000-000000000002",
            request_id="40000000-0000-4000-8000-000000000003",
        )
    )
    assert result.outcome == "approved"
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            item = connection.execute(
                "SELECT lifecycle_status,current_revision_id FROM knowledge_item WHERE id=?",
                (ids["source"],),
            ).fetchone()
            assert tuple(item) == ("active", ids["source_revision"])
            assert connection.execute(
                "SELECT count(*) FROM knowledge_revision_body WHERE item_id=?",
                (ids["source"],),
            ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.scenario("CUR-011")
@pytest.mark.parametrize(
    ("kind", "severity", "variant", "expected"),
    [
        ("lint", "blocker", None, ("T0", "automatic", "none", "publication_blocked", "quarantine")),
        ("lint", "error", None, ("T0", "automatic", "none", "publication_blocked", "quarantine")),
        ("lint", "warning", None, ("T0", "automatic", "none", "badge", "none")),
        ("lint", "info", None, ("T0", "automatic", "none", "none", "none")),
        ("exact_repeat", "info", "no_op", ("T0", "automatic", "none", "none", "none")),
        ("exact_repeat", "info", "provenance", ("T1", "automatic", "none", "none", "provenance")),
        ("curation_clear", "info", None, ("T1", "automatic", "none", "none", "publish")),
        ("weak_complementary", "warning", None, ("T2", "human", "space_editor", "badge", "none")),
        ("near_duplicate", "warning", None, ("T2", "human", "space_editor", "agent_hidden", "review_duplicate")),
        ("update", "info", None, ("T2", "human", "space_editor", "agent_hidden", "update")),
        ("potential_conflict", "error", None, ("T2", "human", "space_editor", "agent_hidden", "review_conflict")),
        ("semantic_edge", "info", None, ("T2", "human", "space_editor", "none", "relation")),
        ("replace", "warning", None, ("T2", "human", "space_editor", "agent_hidden", "replace")),
        ("merge", "warning", None, ("T2", "human", "space_editor", "agent_hidden", "merge")),
        ("curation_release", "info", None, ("T2", "human", "space_editor", "agent_hidden", "publish")),
        ("curation_quarantine", "error", None, ("T2", "human", "space_editor", "agent_hidden", "quarantine")),
        ("deactivate", "warning", None, ("T3", "human", "space_editor", "none", "deactivate")),
        ("purge", "warning", None, ("T3", "human", "admin", "none", "purge")),
    ],
)
def test_server_derives_every_frozen_finding_policy_tuple(
    kind: str, severity: str, variant: str | None, expected: tuple[str, ...]
) -> None:
    policy = finding_policy(kind, severity, variant)
    assert (
        policy.risk_tier,
        policy.decision_mode,
        policy.required_reviewer,
        policy.visibility_effect,
        policy.proposed_action,
    ) == expected


@pytest.mark.scenario("CUR-004")
def test_weak_complementary_remains_published_badge_with_no_action(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    source = "# Cache invalidation\nalpha quorum cache design\n"
    target = "# Distributed cache\nprotocol quorum notes\n"
    snapshot = IncrementalCandidateRetrievalService(factory).retrieve(
        IncrementalCandidateRequest(
            space_id=ids["alice_space"],
            source_item_id=ids["candidate-a"],
            source_revision_id=ids["candidate-a_revision"],
            source_markdown=source,
            top_k=3,
        )
    )
    assert any(candidate.item_id == ids["candidate-b"] for candidate in snapshot.candidates)
    detection = replace(
        _near_duplicate(factory, ids),
        finding_kind="weak_complementary",
        source_item_id=ids["candidate-a"],
        source_revision_id=ids["candidate-a_revision"],
        target_item_id=ids["candidate-b"],
        target_revision_id=ids["candidate-b_revision"],
        candidate_retrieval_evidence_sha256=snapshot.evidence_sha256,
        candidate_snapshot=snapshot,
        source_markdown=source,
        target_markdown=target,
        evidence_spans=(
            FindingEvidenceSpan("source", 2, 1, 2, 6, _sha("alpha")),
            FindingEvidenceSpan("target", 2, 1, 2, 9, _sha("protocol")),
        ),
    )
    finding = _service(factory).record(
        RecordFindingCommand(detection, "50000000-0000-4000-8000-000000000001")
    ).finding
    assert (
        finding.risk_tier,
        finding.status,
        finding.visibility_effect,
        finding.proposed_action,
    ) == ("T2", "pending", "badge", "none")
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            row = connection.execute(
                "SELECT curation_status FROM knowledge_item WHERE id=?",
                (ids["candidate-a"],),
            ).fetchone()
            assert row[0] == "published"
    finally:
        connection.close()


@pytest.mark.scenario("CUR-008")
def test_ignored_exact_input_is_suppressed_but_rule_change_is_new(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    service = _service(factory)
    command = RecordFindingCommand(
        _near_duplicate(factory, ids), "60000000-0000-4000-8000-000000000001"
    )
    first = service.record(command)
    service.review(
        ReviewFindingCommand(
            principal=SessionPrincipal("alice", 1),
            finding_id=first.finding.id,
            expected_row_version=1,
            decision="ignore",
            reason="Suppress only this exact versioned detector input.",
            operation_id="60000000-0000-4000-8000-000000000002",
            request_id="60000000-0000-4000-8000-000000000003",
        )
    )
    exact = service.record(replace(command, request_id="60000000-0000-4000-8000-000000000004"))
    assert exact.inserted is False
    assert exact.suppressed_by_ignored_exact_input is True
    changed = service.record(
        RecordFindingCommand(
            replace(command.detection, rule_version="p6.3-test-v2"),
            "60000000-0000-4000-8000-000000000005",
        )
    )
    assert changed.inserted is True
    assert changed.finding.id != first.finding.id
    changed_model = service.record(
        RecordFindingCommand(
            replace(
                command.detection,
                model_id="fixture-model-v2",
                prompt_version="fixture-prompt-v2",
            ),
            "60000000-0000-4000-8000-000000000006",
        )
    )
    assert changed_model.inserted is True
    assert changed_model.finding.id not in {first.finding.id, changed.finding.id}


def test_automatic_finding_rejects_human_review_and_model_metadata(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    deterministic = FindingDetection(
        space_id=ids["alice_space"],
        finding_kind="lint",
        severity="warning",
        policy_variant=None,
        source_item_id=ids["source"],
        source_revision_id=ids["source_revision"],
        target_item_id=None,
        target_revision_id=None,
        detector_name="structural_lint",
        detector_version="v1",
        rule_version="p6.3-test-v1",
        model_id=None,
        prompt_version=None,
        confidence=None,
        detector_evidence_sha256="f" * 64,
        candidate_retrieval_evidence_sha256=None,
        evidence_codes=("heading_warning",),
        evidence_spans=(),
        candidate_snapshot=None,
        source_markdown=None,
        target_markdown=None,
    )
    service = _service(factory)
    finding = service.record(
        RecordFindingCommand(
            deterministic, "70000000-0000-4000-8000-000000000001"
        )
    ).finding
    with pytest.raises(FindingAuthorizationError, match="not human-reviewable"):
        service.review(
            ReviewFindingCommand(
                principal=SessionPrincipal("alice", 1),
                finding_id=finding.id,
                expected_row_version=1,
                decision="approve",
                reason="This client must not turn automatic policy into review.",
                operation_id="70000000-0000-4000-8000-000000000002",
                request_id="70000000-0000-4000-8000-000000000003",
            )
        )
    with pytest.raises(FindingPolicyError, match="cannot claim model"):
        connection = factory.open()
        try:
            with transaction(connection):
                FindingRepository().put_detection(
                    connection,
                    replace(
                        deterministic,
                        model_id="untrusted-model",
                        prompt_version="untrusted-prompt",
                    ),
                    now_ms=900,
                )
        finally:
            connection.close()


def test_non_admin_cannot_approve_t3_purge_finding(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            foreign_revision = str(
                connection.execute(
                    "SELECT current_revision_id FROM knowledge_item WHERE id=?",
                    (ids["foreign"],),
                ).fetchone()[0]
            )
    finally:
        connection.close()
    detection = FindingDetection(
        space_id=ids["bob_space"],
        finding_kind="purge",
        severity="warning",
        policy_variant=None,
        source_item_id=ids["foreign"],
        source_revision_id=foreign_revision,
        target_item_id=None,
        target_revision_id=None,
        detector_name="retention_policy",
        detector_version="v1",
        rule_version="p6.3-test-v1",
        model_id=None,
        prompt_version=None,
        confidence=None,
        detector_evidence_sha256="a" * 64,
        candidate_retrieval_evidence_sha256=None,
        evidence_codes=("purge_eligible_candidate",),
        evidence_spans=(),
        candidate_snapshot=None,
        source_markdown=None,
        target_markdown=None,
    )
    service = _service(factory)
    finding = service.record(
        RecordFindingCommand(detection, "80000000-0000-4000-8000-000000000001")
    ).finding
    with pytest.raises(FindingAuthorizationError, match="administrator"):
        service.review(
            ReviewFindingCommand(
                principal=SessionPrincipal("bob", 1),
                finding_id=finding.id,
                expected_row_version=1,
                decision="approve",
                reason="A non-admin cannot approve this T3 finding.",
                operation_id="80000000-0000-4000-8000-000000000002",
                request_id="80000000-0000-4000-8000-000000000003",
            )
        )


def test_p6_3_service_has_no_model_or_domain_action_execution_surface() -> None:
    source = inspect.getsource(service_module)
    assert "openai" not in source.casefold()
    assert "requests" not in source.casefold()
    assert "subprocess" not in source.casefold()
    assert "model_client" not in source.casefold()
    assert ".deactivate(" not in source
    assert ".purge(" not in source
    assert ".merge(" not in source
    assert "edge_repository" not in source.casefold()


@pytest.mark.parametrize(
    "failpoint",
    [
        "finding.after_record_audit",
        "finding.after_record_outbox",
        "finding.before_record_commit",
    ],
)
def test_record_failpoints_roll_back_finding_audit_and_outbox(
    tmp_path: Path, failpoint: str
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    _service(factory)
    before = _counts(factory)

    def fail(name: str) -> None:
        if name == failpoint:
            raise RuntimeError("bounded record failpoint")

    service = VersionedFindingService(
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
        failpoint=fail,
    )
    with pytest.raises(RuntimeError, match="bounded record failpoint"):
        service.record(
            RecordFindingCommand(
                _near_duplicate(factory, ids), "90000000-0000-4000-8000-000000000001"
            )
        )
    assert _counts(factory) == before


def test_compared_revision_change_makes_pending_finding_obsolete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    service = _service(factory)
    finding = service.record(
        RecordFindingCommand(
            _near_duplicate(factory, ids), "91000000-0000-4000-8000-000000000001"
        )
    ).finding
    original = repository_module._load_review_endpoint_state

    def changed_state(connection, item_id: str):
        state = original(connection, item_id)
        if item_id == ids["source"]:
            return replace(
                state,
                current_revision_id="91000000-0000-4000-8000-000000000002",
            )
        return state

    monkeypatch.setattr(repository_module, "_load_review_endpoint_state", changed_state)
    result = service.review(
        ReviewFindingCommand(
            principal=SessionPrincipal("alice", 1),
            finding_id=finding.id,
            expected_row_version=1,
            decision="approve",
            reason="This review must fail closed because the source changed.",
            operation_id="91000000-0000-4000-8000-000000000003",
            request_id="91000000-0000-4000-8000-000000000004",
        )
    )
    assert result.outcome == "obsolete"
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            row = connection.execute(
                "SELECT status,reviewed_by,reviewed_at,review_reason,row_version FROM curation_finding WHERE id=?",
                (finding.id,),
            ).fetchone()
            assert tuple(row) == ("obsolete", None, None, None, 2)
    finally:
        connection.close()


def test_transient_markdown_and_cross_space_endpoints_fail_closed(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            foreign_revision = str(
                connection.execute(
                    "SELECT current_revision_id FROM knowledge_item WHERE id=?",
                    (ids["foreign"],),
                ).fetchone()[0]
            )
    finally:
        connection.close()
    repository = FindingRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            with pytest.raises(FindingConflictError, match="no longer matches"):
                repository.put_detection(
                    connection,
                    replace(
                        _near_duplicate(factory, ids),
                        source_markdown="tampered body",
                    ),
                    now_ms=1_000,
                )
            cross_space = replace(
                _near_duplicate(factory, ids),
                target_item_id=ids["foreign"],
                target_revision_id=foreign_revision,
                target_markdown="# alpha cache foreign canary\n",
                evidence_spans=(
                    FindingEvidenceSpan("source", 2, 1, 2, 6, _sha("alpha")),
                    FindingEvidenceSpan("target", 1, 3, 1, 8, _sha("alpha")),
                ),
            )
            with pytest.raises(FindingPolicyError, match="one exact space"):
                repository.put_detection(connection, cross_space, now_ms=1_000)
    finally:
        connection.close()


@pytest.mark.scenario("CUR-003")
@pytest.mark.scenario("CUR-009")
def test_potential_conflict_is_evidence_backed_pending_review_not_fact(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    detection = replace(
        _near_duplicate(factory, ids),
        finding_kind="potential_conflict",
        severity="error",
        evidence_codes=("potential_conflict_candidate",),
    )
    finding = _service(factory).record(
        RecordFindingCommand(detection, "92000000-0000-4000-8000-000000000001")
    ).finding
    assert (
        finding.status,
        finding.risk_tier,
        finding.decision_mode,
        finding.visibility_effect,
        finding.proposed_action,
    ) == ("pending", "T2", "human", "agent_hidden", "review_conflict")
    evidence = finding.evidence
    assert len(evidence["evidence_spans"]) == 2
    assert evidence["candidate_retrieval_evidence_sha256"] == detection.candidate_snapshot.evidence_sha256


def test_finding_rejects_resealed_or_unbound_candidate_snapshot(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    detection = _near_duplicate(factory, ids)
    forged_snapshot = replace(detection.candidate_snapshot, evidence_sha256="0" * 64)
    connection = factory.open()
    try:
        with transaction(connection):
            with pytest.raises(FindingPolicyError, match="Candidate retrieval evidence"):
                FindingRepository().put_detection(
                    connection,
                    replace(detection, candidate_snapshot=forged_snapshot),
                    now_ms=1_000,
                )
    finally:
        connection.close()

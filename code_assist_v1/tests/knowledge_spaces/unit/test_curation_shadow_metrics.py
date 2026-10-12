from __future__ import annotations

from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

import pytest

from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.scope import (
    ScopeNotFoundError,
    ScopeResolver,
    SessionPrincipal,
)
from code_assist_v1.knowledge_core.services.curation_metrics import (
    CurationShadowMetricsRequest,
    CurationShadowMetricsService,
)
from code_assist_v1.knowledge_core.services.findings import (
    RecordFindingCommand,
    ReviewFindingCommand,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_incremental_candidate_retrieval import (
    _factory,
    _seed_candidates,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_versioned_finding_service import (
    _near_duplicate,
    _service,
)


pytestmark = pytest.mark.unit


def _uuid(label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"p6.7-shadow:{label}"))


def _metrics(factory) -> CurationShadowMetricsService:
    return CurationShadowMetricsService(
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


def _table_counts(factory) -> tuple[int, int, int, int]:
    connection = factory.open(read_only=True)
    try:
        return tuple(
            int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in (
                "curation_finding",
                "audit_event",
                "outbox_event",
                "operation_dedup",
            )
        )
    finally:
        connection.close()


def _space_finding_count(factory, space_id: str) -> int:
    connection = factory.open(read_only=True)
    try:
        return int(
            connection.execute(
                "SELECT count(*) FROM curation_finding WHERE space_id=?", (space_id,)
            ).fetchone()[0]
        )
    finally:
        connection.close()


def test_zero_model_shadow_report_is_body_free_read_only_and_passes(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    _service(factory)
    before = _table_counts(factory)
    report = _metrics(factory).read(
        CurationShadowMetricsRequest(
            principal=SessionPrincipal("alice", 1),
            space_id=ids["alice_space"],
            sample_size=10,
        )
    )
    value = report.as_dict()
    assert value["finding_count"] == _space_finding_count(factory, ids["alice_space"])
    assert value["mode"] == "shadow_proposal_only"
    assert value["model_proposal_count"] == 0
    assert value["model_adjudicated_count"] == 0
    assert value["model_automatic_execution_count"] == 0
    assert value["risky_model_automatic_execution_count"] == 0
    assert value["t3_model_automatic_execution_count"] == 0
    assert value["precision_numerator"] == 0
    assert value["precision_denominator"] == 0
    assert value["precision_ppm"] is None
    assert value["report_status"] == "pass"
    assert value["sample"] == []
    assert value["schema_version"] == 1
    assert value["space_id"] == ids["alice_space"]
    assert _table_counts(factory) == before


@pytest.mark.scenario("CUR-005")
@pytest.mark.scenario("CUR-006")
@pytest.mark.scenario("CUR-011")
def test_human_adjudications_form_deterministic_precision_sample_with_zero_auto_effect(
    tmp_path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    findings = _service(factory)
    reviewed_ids: set[str] = set()
    for index, decision in enumerate(("approve", "reject", "ignore"), 1):
        detection = replace(
            _near_duplicate(factory, ids),
            detector_evidence_sha256=f"{index}" * 64,
            rule_version=f"p6.7-shadow-v{index}",
        )
        recorded = findings.record(
            RecordFindingCommand(
                detection=detection,
                request_id=_uuid(f"record:{index}"),
            )
        ).finding
        reviewed_ids.add(recorded.id)
        findings.review(
            ReviewFindingCommand(
                principal=SessionPrincipal("alice", 1),
                finding_id=recorded.id,
                expected_row_version=1,
                decision=decision,
                reason=f"Human precision label {decision}.",
                operation_id=_uuid(f"review-operation:{index}"),
                request_id=_uuid(f"review-request:{index}"),
            )
        )
    before = _table_counts(factory)
    report = _metrics(factory).read(
        CurationShadowMetricsRequest(
            principal=SessionPrincipal("alice", 1),
            space_id=ids["alice_space"],
            sample_size=100,
        )
    )
    assert report.report_status == "pass"
    assert report.model_proposal_count == 3
    assert report.model_adjudicated_count == 3
    assert report.model_accepted_count == 1
    assert report.model_rejected_count == 1
    assert report.model_ignored_count == 1
    assert report.precision_numerator == 1
    assert report.precision_denominator == 3
    assert report.precision_ppm == 333_333
    assert report.model_automatic_execution_count == 0
    assert report.risky_model_automatic_execution_count == 0
    assert report.t3_model_automatic_execution_count == 0
    assert {sample.finding_id for sample in report.sample} == reviewed_ids
    assert {sample.verdict for sample in report.sample} == {
        "accepted",
        "rejected",
        "ignored",
    }
    serialized = str(report.as_dict())
    assert "source_markdown" not in serialized
    assert ids["source_body"] not in serialized
    assert _table_counts(factory) == before


def test_shadow_metrics_require_current_curate_authority(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    with pytest.raises(ScopeNotFoundError):
        _metrics(factory).read(
            CurationShadowMetricsRequest(
                principal=SessionPrincipal("bob", 1),
                space_id=ids["alice_space"],
            )
        )


@pytest.mark.parametrize("sample_size", (0, 101, True, 1.0))
def test_shadow_sample_size_is_strict_and_bounded(tmp_path, sample_size) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    with pytest.raises(ValueError, match="sample_size"):
        _metrics(factory).read(
            CurationShadowMetricsRequest(
                principal=SessionPrincipal("alice", 1),
                space_id=ids["alice_space"],
                sample_size=sample_size,
            )
        )

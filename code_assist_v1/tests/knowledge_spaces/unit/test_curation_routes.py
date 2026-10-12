from __future__ import annotations

from types import SimpleNamespace

from flask import Flask

from code_assist_v1.knowledge_core.canonical_routes import register_canonical_routes
from code_assist_v1.knowledge_core.composition import (
    CanonicalRouteBindings,
    HOST_PROFILES,
    KnowledgeCoreContainer,
    ServiceRegistry,
)
from code_assist_v1.knowledge_core.domain import FeatureFlags, HostKind
from code_assist_v1.knowledge_core.routes import register_routes
from code_assist_v1.knowledge_core.services.curation_metrics import (
    CurationShadowMetricsRequest,
)
from code_assist_v1.knowledge_core.services.curation_review import (
    CurationReviewFacade,
)
from code_assist_v1.knowledge_core.services.findings import (
    ReadFindingRequest,
    ReviewFindingCommand,
)
from code_assist_v1.knowledge_core.services.merges import (
    MergeApplyCommand,
    MergePreviewCommand,
)
from code_assist_v1.knowledge_core.services.proposals import (
    ProposalApplyCommand,
    ProposalPreviewCommand,
    ProposalRebaseCommand,
)


SPACE = "11111111-1111-4111-8111-111111111111"
FINDING = "22222222-2222-4222-8222-222222222222"
PROPOSAL = "33333333-3333-4333-8333-333333333333"
SOURCE_ITEM = "44444444-4444-4444-8444-444444444444"
SOURCE_REVISION = "55555555-5555-4555-8555-555555555555"
TARGET_ITEM = "66666666-6666-4666-8666-666666666666"
TARGET_REVISION = "77777777-7777-4777-8777-777777777777"
OPERATION = "88888888-8888-4888-8888-888888888888"
SESSION = "99999999-9999-4999-8999-999999999999"
LEASE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TOKEN = "A" * 43


class _Surface:
    def __getattr__(self, _name: str):
        return lambda *_args, **_kwargs: None


class _Result:
    def __init__(self, **value: object) -> None:
        self.value = value

    def as_dict(self) -> dict[str, object]:
        return dict(self.value)


def _finding() -> SimpleNamespace:
    return SimpleNamespace(
        confidence=0.75,
        created_at_ms=1_000,
        decision_mode="human",
        detector_name="semantic-shadow",
        detector_version="1",
        evidence={"codes": ["fixed_revision"]},
        finding_kind="near_duplicate",
        id=FINDING,
        model_id="model-shadow",
        prompt_version="prompt-v1",
        proposed_action="review_duplicate",
        proposed_action_evidence={"target_revision_id": TARGET_REVISION},
        required_reviewer="space_editor",
        review_reason=None,
        reviewed_at_ms=None,
        reviewed_by=None,
        risk_tier="T2",
        row_version=1,
        rule_version="rule-v1",
        severity="warning",
        source_item_id=SOURCE_ITEM,
        source_revision_id=SOURCE_REVISION,
        space_id=SPACE,
        status="pending",
        target_item_id=TARGET_ITEM,
        target_revision_id=TARGET_REVISION,
        updated_at_ms=1_000,
        visibility_effect="agent_hidden",
    )


class _Curation:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def get_finding(self, value: ReadFindingRequest):
        self.calls.append(("get_finding", value))
        return _finding()

    def review_finding(self, value: ReviewFindingCommand):
        self.calls.append(("review_finding", value))
        return _Result(
            finding_id=value.finding_id,
            operation_id=value.operation_id,
            outcome="approved",
            request_id=value.request_id,
            row_version=2,
        )

    def read_shadow_metrics(self, value: CurationShadowMetricsRequest):
        self.calls.append(("read_shadow_metrics", value))
        return _Result(
            mode="shadow_proposal_only",
            model_automatic_execution_count=0,
            precision_denominator=0,
            precision_numerator=0,
            precision_ppm=None,
            report_status="pass",
            risky_model_automatic_execution_count=0,
            sample=[],
            schema_version=1,
            space_id=value.space_id,
            t3_model_automatic_execution_count=0,
        )

    def preview_proposal(self, value: ProposalPreviewCommand):
        self.calls.append(("preview_proposal", value))
        return _Result(
            proposal_id=value.proposal_id,
            request_id=value.request_id,
            stale=False,
        )

    def rebase_proposal(self, value: ProposalRebaseCommand):
        self.calls.append(("rebase_proposal", value))
        return _Result(
            operation_id=value.operation_id,
            proposal_id=PROPOSAL,
            request_id=value.request_id,
            status="pending_review",
        )

    def apply_proposal(self, value: ProposalApplyCommand):
        self.calls.append(("apply_proposal", value))
        return _Result(
            operation_id=value.operation_id,
            proposal_id=value.proposal_id,
            request_id=value.request_id,
            status="applied",
        )

    def preview_merge(self, value: MergePreviewCommand):
        self.calls.append(("preview_merge", value))
        return _Result(request_id=value.request_id, stale=False)

    def apply_merge(self, value: MergeApplyCommand):
        self.calls.append(("apply_merge", value))
        return _Result(
            operation_id=value.operation_id,
            request_id=value.request_id,
            status="applied",
        )


def _app(curation: object | None) -> Flask:
    app = Flask(__name__)
    surface = _Surface()
    register_canonical_routes(
        app,
        scope_selection_service=surface,
        conversation_service=surface,
        retrieval_service=surface,
        management_read_service=surface,
        agent_context_service=surface,
        turn_executor=surface,
        conversation_summarizer=surface,
        principal_resolver=lambda: {"id": "reviewer", "scope_epoch": 1},
        curation_service=curation,
    )
    return app


def _json_headers(*, lease: bool = False) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": OPERATION,
    }
    if lease:
        headers.update(
            {
                "X-Knowledge-Fence": "7",
                "X-Knowledge-Lease-Token": TOKEN,
            }
        )
    return headers


def test_curation_routes_are_absent_until_the_optional_service_is_bound() -> None:
    rules = {rule.rule for rule in _app(None).url_map.iter_rules()}
    assert f"/api/knowledge/v1/spaces/<space_id>/curation/findings/<finding_id>" not in rules
    assert f"/api/knowledge/v1/spaces/<space_id>/curation/shadow-metrics" not in rules
    assert f"/api/knowledge/v1/spaces/<space_id>/merges/apply" not in rules


def test_curation_review_facade_only_delegates_to_authoritative_services() -> None:
    calls: list[tuple[str, object]] = []

    def delegate(name: str):
        def invoke(value: object):
            calls.append((name, value))
            return name

        return invoke

    facade = CurationReviewFacade(
        findings=SimpleNamespace(
            read=delegate("finding.read"),
            review=delegate("finding.review"),
        ),
        proposals=SimpleNamespace(
            preview=delegate("proposal.preview"),
            rebase=delegate("proposal.rebase"),
            apply=delegate("proposal.apply"),
        ),
        merges=SimpleNamespace(
            preview=delegate("merge.preview"),
            apply=delegate("merge.apply"),
        ),
        metrics=SimpleNamespace(read=delegate("metrics.read")),
    )
    marker = object()
    assert facade.get_finding(marker) == "finding.read"
    assert facade.review_finding(marker) == "finding.review"
    assert facade.preview_proposal(marker) == "proposal.preview"
    assert facade.rebase_proposal(marker) == "proposal.rebase"
    assert facade.apply_proposal(marker) == "proposal.apply"
    assert facade.preview_merge(marker) == "merge.preview"
    assert facade.apply_merge(marker) == "merge.apply"
    assert facade.read_shadow_metrics(marker) == "metrics.read"
    assert calls == [
        ("finding.read", marker),
        ("finding.review", marker),
        ("proposal.preview", marker),
        ("proposal.rebase", marker),
        ("proposal.apply", marker),
        ("merge.preview", marker),
        ("merge.apply", marker),
        ("metrics.read", marker),
    ]


def test_composition_keeps_curation_in_one_explicit_disabled_service_slot() -> None:
    surface = _Surface()
    curation = _Curation()
    app = Flask(__name__)
    container = KnowledgeCoreContainer(
        contract_version=1,
        host=HOST_PROFILES[HostKind.CODE],
        flags=FeatureFlags(
            core_enabled=True,
            personal_reads=True,
            personal_writes=True,
        ),
        services=ServiceRegistry(
            agent_context=surface,
            conversations=surface,
            curation=curation,
            editing=surface,
            lifecycle=surface,
            management_reads=surface,
            publication=surface,
            retrieval=surface,
            scope_selection=surface,
        ),
        canonical_route_bindings=CanonicalRouteBindings(
            turn_executor=surface,
            conversation_summarizer=surface,
            principal_resolver=lambda: {"id": "reviewer", "scope_epoch": 1},
        ),
    )
    assert register_routes(app, container) == (
        "canonical_knowledge_v1",
        "canonical_publication_v1",
        "canonical_editing_v1",
        "canonical_lifecycle_v1",
        "canonical_curation_v1",
    )
    assert any(
        rule.rule.endswith("/curation/shadow-metrics")
        for rule in app.url_map.iter_rules()
    )

    read_only = Flask("read-only-curation")
    read_only_container = KnowledgeCoreContainer(
        contract_version=1,
        host=HOST_PROFILES[HostKind.CODE],
        flags=FeatureFlags(core_enabled=True, personal_reads=True),
        services=container.services,
        canonical_route_bindings=container.canonical_route_bindings,
    )
    assert register_routes(read_only, read_only_container) == (
        "canonical_knowledge_v1",
    )
    assert not any(
        rule.rule.endswith("/curation/shadow-metrics")
        for rule in read_only.url_map.iter_rules()
    )


def test_finding_detail_review_and_shadow_metrics_are_strictly_bound() -> None:
    curation = _Curation()
    client = _app(curation).test_client()

    detail = client.get(
        f"/api/knowledge/v1/spaces/{SPACE}/curation/findings/{FINDING}"
    )
    assert detail.status_code == 200
    assert detail.headers["Cache-Control"] == "no-store, max-age=0"
    assert detail.json["finding"]["id"] == FINDING
    assert detail.json["finding"]["evidence"] == {"codes": ["fixed_revision"]}
    assert detail.json["finding"]["visibility_effect"] == "agent_hidden"
    assert isinstance(curation.calls[0][1], ReadFindingRequest)

    reviewed = client.post(
        f"/api/knowledge/v1/spaces/{SPACE}/curation/findings/{FINDING}/review",
        headers=_json_headers(),
        json={
            "decision": "approve",
            "expected_finding_row_version": 1,
            "operation_id": OPERATION,
            "reason": "Reviewed fixed evidence.",
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json["outcome"] == "approved"
    command = curation.calls[1][1]
    assert isinstance(command, ReviewFindingCommand)
    assert command.principal.user_id == "reviewer"
    assert command.expected_row_version == 1

    metrics = client.get(
        f"/api/knowledge/v1/spaces/{SPACE}/curation/shadow-metrics?sample_size=7"
    )
    assert metrics.status_code == 200
    assert metrics.json["mode"] == "shadow_proposal_only"
    assert metrics.json["model_automatic_execution_count"] == 0
    request = curation.calls[2][1]
    assert isinstance(request, CurationShadowMetricsRequest)
    assert request.sample_size == 7


def test_proposal_routes_bind_versions_lease_and_secret_only_to_commands() -> None:
    curation = _Curation()
    client = _app(curation).test_client()
    base = f"/api/knowledge/v1/spaces/{SPACE}/change-proposals/{PROPOSAL}"

    preview = client.post(f"{base}/preview", json={})
    assert preview.status_code == 200
    assert isinstance(curation.calls[-1][1], ProposalPreviewCommand)

    rebased = client.post(
        f"{base}/rebase",
        headers=_json_headers(),
        json={
            "expected_finding_row_version": 1,
            "expected_proposal_row_version": 1,
            "expected_source_revision_id": SOURCE_REVISION,
            "expected_target_revision_id": TARGET_REVISION,
            "operation_id": OPERATION,
            "proposed_markdown": "# Reviewed rebase\n",
        },
    )
    assert rebased.status_code == 201
    rebase = curation.calls[-1][1]
    assert isinstance(rebase, ProposalRebaseCommand)
    assert rebase.proposed_markdown == "# Reviewed rebase\n"

    applied = client.post(
        f"{base}/apply",
        headers=_json_headers(lease=True),
        json={
            "editor_session_id": SESSION,
            "expected_finding_row_version": 2,
            "expected_proposal_row_version": 2,
            "expected_source_revision_id": SOURCE_REVISION,
            "expected_target_item_row_version": 4,
            "expected_target_revision_id": TARGET_REVISION,
            "lease_id": LEASE,
            "operation_id": OPERATION,
            "preview_sha256": "b" * 64,
        },
    )
    assert applied.status_code == 200
    command = curation.calls[-1][1]
    assert isinstance(command, ProposalApplyCommand)
    assert command.raw_token == TOKEN
    assert command.fencing_generation == 7
    assert TOKEN not in applied.get_data(as_text=True)


def test_merge_routes_bind_two_independent_leases_without_disclosure() -> None:
    curation = _Curation()
    client = _app(curation).test_client()
    path = f"/api/knowledge/v1/spaces/{SPACE}/merges"
    body = {
        "result_markdown": "# Explicit merge result\n",
        "source_item_id": SOURCE_ITEM,
        "source_revision_id": SOURCE_REVISION,
        "target_item_id": TARGET_ITEM,
        "target_revision_id": TARGET_REVISION,
    }
    preview = client.post(f"{path}/preview", json=body)
    assert preview.status_code == 200
    assert isinstance(curation.calls[-1][1], MergePreviewCommand)

    applied = client.post(
        f"{path}/apply",
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": OPERATION,
            "X-Knowledge-Source-Fence": "3",
            "X-Knowledge-Source-Lease-Token": TOKEN,
            "X-Knowledge-Target-Fence": "5",
            "X-Knowledge-Target-Lease-Token": "B" * 43,
        },
        json={
            **body,
            "operation_id": OPERATION,
            "preview_sha256": "c" * 64,
            "source_editor_session_id": SESSION,
            "source_lease_id": LEASE,
            "target_editor_session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "target_lease_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        },
    )
    assert applied.status_code == 200
    command = curation.calls[-1][1]
    assert isinstance(command, MergeApplyCommand)
    assert command.source_raw_token == TOKEN
    assert command.target_raw_token == "B" * 43
    assert command.source_fencing_generation == 3
    assert command.target_fencing_generation == 5
    assert TOKEN not in applied.get_data(as_text=True)


def test_curation_route_rejects_alias_or_secret_shape_before_service_call() -> None:
    curation = _Curation()
    client = _app(curation).test_client()
    response = client.post(
        f"/api/knowledge/v1/spaces/{SPACE}/curation/findings/{FINDING}/review",
        headers=_json_headers(),
        json={
            "decision": "approve",
            "expected_finding_row_version": 1,
            "operation_id": OPERATION,
            "reason": "fixed",
            "lease_token": TOKEN,
        },
    )
    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert not curation.calls
    assert TOKEN not in response.get_data(as_text=True)

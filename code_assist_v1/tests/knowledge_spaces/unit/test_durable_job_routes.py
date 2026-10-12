from __future__ import annotations

from types import SimpleNamespace

from flask import Flask
import pytest

from code_assist_v1.knowledge_core.canonical_routes import register_canonical_routes
from code_assist_v1.knowledge_core.composition import (
    CanonicalRouteBindings,
    HOST_PROFILES,
    KnowledgeCoreContainer,
    ServiceRegistry,
)
from code_assist_v1.knowledge_core.domain import FeatureFlags, HostKind
from code_assist_v1.knowledge_core.routes import register_routes
from code_assist_v1.knowledge_core.services.jobs import (
    CancelDurableJobCommand,
    CancelDurableJobResult,
    DurableJobCancellationError,
    DurableJobNotFoundError,
    ReadDurableJobRequest,
)


pytestmark = pytest.mark.unit
JOB = "11111111-1111-4111-8111-111111111111"
OPERATION = "22222222-2222-4222-8222-222222222222"


class _Surface:
    def __getattr__(self, _name: str):
        return lambda *_args, **_kwargs: None


def _job() -> SimpleNamespace:
    return SimpleNamespace(
        attempt_count=0,
        cancel_requested=False,
        cancellable=True,
        completed_at_ms=None,
        created_at_ms=1_000,
        error=None,
        handler_version="v1",
        id=JOB,
        job_type="fixture.public-job",
        max_attempts=3,
        progress_permille=250,
        result=None,
        state="queued",
        updated_at_ms=2_000,
    )


class _Jobs:
    def __init__(self, *, missing: bool = False) -> None:
        self.calls: list[tuple[str, object]] = []
        self.missing = missing

    def read(self, value: ReadDurableJobRequest):
        self.calls.append(("read", value))
        if self.missing:
            raise DurableJobNotFoundError("The requested durable job was not found.")
        return SimpleNamespace(request_id=value.request_id, job=_job())

    def cancel(self, value: CancelDurableJobCommand):
        self.calls.append(("cancel", value))
        return CancelDurableJobResult(
            request_id=value.request_id,
            operation_id=value.operation_id,
            resource_id=value.job_id,
            status="cancelled",
        )


def _app(jobs: object | None, *, mutations: bool = False) -> Flask:
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
        principal_resolver=lambda: {"id": "route-user", "scope_epoch": 7},
        job_service=jobs,
        job_mutations_enabled=mutations,
    )
    return app


def test_job_routes_are_absent_until_the_service_is_explicitly_bound() -> None:
    rules = {rule.rule for rule in _app(None).url_map.iter_rules()}
    assert "/api/knowledge/v1/jobs/<job_id>" not in rules
    assert "/api/knowledge/v1/jobs/<job_id>/cancel" not in rules

    with pytest.raises(TypeError, match="job mutations require"):
        _app(None, mutations=True)


def test_read_only_binding_exposes_actor_authorized_status_but_not_cancel() -> None:
    jobs = _Jobs()
    client = _app(jobs).test_client()

    response = client.get(f"/api/knowledge/v1/jobs/{JOB}")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert set(response.json) == {
        "attempt_count",
        "cancel_requested",
        "cancellable",
        "completed_at",
        "created_at",
        "error",
        "handler_version",
        "job_id",
        "job_type",
        "max_attempts",
        "progress_permille",
        "request_id",
        "result",
        "state",
        "status_url",
        "updated_at",
    }
    assert response.json["job_id"] == JOB
    assert response.json["state"] == "queued"
    assert response.json["progress_permille"] == 250
    assert response.json["created_at"] == "1970-01-01T00:00:01.000Z"
    assert response.json["updated_at"] == "1970-01-01T00:00:02.000Z"
    assert response.json["completed_at"] is None
    assert response.json["status_url"] == f"/api/knowledge/v1/jobs/{JOB}"
    call = jobs.calls[0][1]
    assert isinstance(call, ReadDurableJobRequest)
    assert call.principal.user_id == "route-user"
    assert call.principal.captured_scope_epoch == 7
    assert call.job_id == JOB
    assert call.request_id == response.json["request_id"]

    cancel = client.post(
        f"/api/knowledge/v1/jobs/{JOB}/cancel",
        json={"operation_id": OPERATION},
    )
    assert cancel.status_code == 404
    assert [name for name, _ in jobs.calls] == ["read"]


def test_mutation_binding_maps_exact_operation_and_request_metadata() -> None:
    jobs = _Jobs()
    client = _app(jobs, mutations=True).test_client()
    response = client.post(
        f"/api/knowledge/v1/jobs/{JOB}/cancel",
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": OPERATION,
            "User-Agent": "durable-route-test",
        },
        json={"operation_id": OPERATION},
    )
    assert response.status_code == 200
    assert response.json == {
        "operation_id": OPERATION,
        "request_id": response.json["request_id"],
        "resource_id": JOB,
        "row_version": None,
        "status": "cancelled",
    }
    command = jobs.calls[-1][1]
    assert isinstance(command, CancelDurableJobCommand)
    assert command.principal.user_id == "route-user"
    assert command.principal.captured_scope_epoch == 7
    assert command.job_id == JOB
    assert command.operation_id == OPERATION
    assert command.request_id == response.json["request_id"]
    assert command.request_meta["method"] == "POST"
    assert command.request_meta["path"] == f"/api/knowledge/v1/jobs/{JOB}/cancel"
    assert command.request_meta["user_agent"] == "durable-route-test"


@pytest.mark.parametrize(
    "body,headers",
    (
        (
            {"operation_id": OPERATION, "lease_token": "must-not-pass"},
            {"Idempotency-Key": OPERATION},
        ),
        (
            {"operation_id": OPERATION},
            {"Idempotency-Key": "33333333-3333-4333-8333-333333333333"},
        ),
    ),
)
def test_cancel_rejects_alias_or_idempotency_drift_before_service_call(
    body: dict[str, object],
    headers: dict[str, str],
) -> None:
    jobs = _Jobs()
    client = _app(jobs, mutations=True).test_client()
    response = client.post(
        f"/api/knowledge/v1/jobs/{JOB}/cancel",
        headers={"Content-Type": "application/json", **headers},
        json=body,
    )
    assert response.status_code in {400, 409}
    assert response.json["error"]["code"] in {
        "VALIDATION_ERROR",
        "IDEMPOTENCY_CONFLICT",
    }
    assert not jobs.calls
    assert "must-not-pass" not in response.get_data(as_text=True)


def test_not_found_is_nondisclosing_and_query_strings_fail_closed() -> None:
    jobs = _Jobs(missing=True)
    client = _app(jobs).test_client()
    missing = client.get(f"/api/knowledge/v1/jobs/{JOB}")
    assert missing.status_code == 404
    assert missing.json["error"]["code"] == "NOT_FOUND"
    assert "owner" not in missing.get_data(as_text=True).casefold()

    malformed = client.get(f"/api/knowledge/v1/jobs/{JOB}?actor_user_id=other")
    assert malformed.status_code == 400
    assert malformed.json["error"]["code"] == "VALIDATION_ERROR"
    assert [name for name, _ in jobs.calls] == ["read"]


def test_non_cancellable_service_error_uses_the_frozen_public_vocabulary() -> None:
    class RejectedJobs(_Jobs):
        def cancel(self, value: CancelDurableJobCommand):
            self.calls.append(("cancel", value))
            raise DurableJobCancellationError(
                "This durable job does not permit cancellation."
            )

    jobs = RejectedJobs()
    response = _app(jobs, mutations=True).test_client().post(
        f"/api/knowledge/v1/jobs/{JOB}/cancel",
        headers={"Idempotency-Key": OPERATION},
        json={"operation_id": OPERATION},
    )
    assert response.status_code == 409
    assert response.json["error"] == {
        "code": "STATE_CONFLICT",
        "details": {},
        "message": "This durable job does not permit cancellation.",
        "retryable": False,
    }


def test_composition_reports_one_disabled_job_group_and_keeps_cancel_off() -> None:
    surface = _Surface()
    jobs = _Jobs()
    app = Flask("durable-job-composition")
    container = KnowledgeCoreContainer(
        contract_version=1,
        host=HOST_PROFILES[HostKind.CODE],
        flags=FeatureFlags(core_enabled=True, personal_reads=True),
        services=ServiceRegistry(
            agent_context=surface,
            conversations=surface,
            jobs=jobs,
            management_reads=surface,
            retrieval=surface,
            scope_selection=surface,
        ),
        canonical_route_bindings=CanonicalRouteBindings(
            turn_executor=surface,
            conversation_summarizer=surface,
            principal_resolver=lambda: {"id": "route-user", "scope_epoch": 7},
        ),
    )
    assert register_routes(app, container) == (
        "canonical_knowledge_v1",
        "canonical_jobs_v1",
    )
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/knowledge/v1/jobs/<job_id>" in rules
    assert "/api/knowledge/v1/jobs/<job_id>/cancel" not in rules


def test_composition_enables_cancel_only_with_write_flag_and_bound_service() -> None:
    surface = _Surface()
    jobs = _Jobs()
    app = Flask("durable-job-write-composition")
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
            editing=surface,
            jobs=jobs,
            lifecycle=surface,
            management_reads=surface,
            publication=surface,
            retrieval=surface,
            scope_selection=surface,
        ),
        canonical_route_bindings=CanonicalRouteBindings(
            turn_executor=surface,
            conversation_summarizer=surface,
            principal_resolver=lambda: {"id": "route-user", "scope_epoch": 7},
        ),
    )
    assert register_routes(app, container) == (
        "canonical_knowledge_v1",
        "canonical_publication_v1",
        "canonical_editing_v1",
        "canonical_lifecycle_v1",
        "canonical_jobs_v1",
    )
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/knowledge/v1/jobs/<job_id>" in rules
    assert "/api/knowledge/v1/jobs/<job_id>/cancel" in rules

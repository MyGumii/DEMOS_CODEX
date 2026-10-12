from __future__ import annotations

import time
import threading
from collections.abc import Callable
from uuid import UUID, uuid5

import pytest

from code_assist_v1.model_runtime import (
    LocalInferenceOwnershipError,
    assert_local_inference_allowed,
)

from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    db_now_ms,
    transaction,
)
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.inference_worker import (
    DurableInferenceWorker,
    DurableInferenceWorkerConfig,
)
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.jobs import (
    JobEnqueueDraft,
    canonical_job_input_sha256,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.scope import Capability, ScopeResolver
from code_assist_v1.knowledge_core.worker import (
    DurableWorkKernel,
    HandlerRegistry,
    PreparedJobEffect,
    RegisteredJobHandler,
)


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("3787cab2-99c9-4de3-8f62-d1b931261f88")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p7.2-inference-worker-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _flags() -> FeatureFlags:
    return FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=True,
        part_reads=True,
        part_writes=True,
    )


def _seed_actor(factory: ConnectionFactory, user_id: str) -> tuple[str, int]:
    users = UserRepository()
    spaces = SpaceRepository()
    space_id = _uuid(f"space:{user_id}")
    connection = factory.open()
    try:
        with transaction(connection):
            users.import_legacy(
                connection,
                LegacyUserRecord(
                    id=user_id,
                    display_name=user_id,
                    password_hash=f"scrypt:p7.2:{user_id}",
                    system_role="user",
                    account_status="active",
                    created_at_ms=100,
                    updated_at_ms=100,
                ),
            )
            space = spaces.ensure_personal(
                connection,
                space_id=space_id,
                user_id=user_id,
                created_at_ms=100,
            ).space
            spaces.update_state(
                connection,
                space_id=space_id,
                expected_row_version=space.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=101,
            )
            user = users.require(connection, user_id)
        return space_id, user.scope_epoch
    finally:
        connection.close()


def _handler(
    job_type: str,
    calls: list[tuple[str, str | None]],
    *,
    actor_kind: str = "user",
    prepare_hook: Callable[[], None] | None = None,
) -> RegisteredJobHandler:
    def prepare(job):
        calls.append((job.job_type, job.actor_user_id))
        if prepare_hook is not None:
            prepare_hook()
        return PreparedJobEffect(
            result={"job_id": job.id, "schema_version": 1}
        )

    def commit_effect(connection, job, prepared) -> None:
        del connection, job, prepared

    return RegisteredJobHandler.build(
        job_type=job_type,
        handler_version="v1",
        actor_kind=actor_kind,
        required_capability=(Capability.EDIT if actor_kind == "user" else None),
        prepare=prepare,
        commit_effect=commit_effect,
    )


def _enqueue_user(
    factory: ConnectionFactory,
    kernel: DurableWorkKernel,
    *,
    user_id: str,
    space_id: str,
    scope_epoch: int,
    job_type: str,
    ordinal: int,
) -> None:
    users = UserRepository()
    input_ref = {"ordinal": ordinal, "schema_version": 1}
    operation_id = _uuid(f"operation:{user_id}:{job_type}:{ordinal}")
    dedup_id = _uuid(f"dedup:{user_id}:{job_type}:{ordinal}")
    job_id = f"00000000-0000-4000-8000-{ordinal:012d}"
    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            users.begin_operation(
                connection,
                actor_user_id=user_id,
                operation_type=f"job:{job_type}",
                operation_id=operation_id,
                request_sha256=canonical_job_input_sha256(input_ref),
                dedup_id=dedup_id,
                now_ms=now_ms,
            )
            result = kernel.enqueue_in_transaction(
                connection,
                JobEnqueueDraft(
                    job_id=job_id,
                    dedup_id=dedup_id,
                    operation_id=operation_id,
                    actor_kind="user",
                    actor_user_id=user_id,
                    space_id=space_id,
                    job_type=job_type,
                    handler_version="v1",
                    effect_key=f"p7.2:{job_type}:{user_id}:{ordinal}",
                    input_ref=input_ref,
                    expected_state={},
                    authorization_snapshot={},
                    authorization_scope_epoch=scope_epoch,
                    authorization_part_id=None,
                    authorization_capabilities=(),
                    max_attempts=3,
                    cancellable=True,
                    available_at_ms=0,
                ),
            )
            users.commit_operation(
                connection,
                dedup_id=dedup_id,
                result={"job_id": result.job.id, "schema_version": 1},
                http_status=202,
                completed_at_ms=now_ms,
            )
    finally:
        connection.close()


def _enqueue_system(
    factory: ConnectionFactory,
    kernel: DurableWorkKernel,
    *,
    job_type: str,
    ordinal: int,
) -> None:
    connection = factory.open()
    try:
        with transaction(connection):
            kernel.enqueue_in_transaction(
                connection,
                JobEnqueueDraft(
                    job_id=f"10000000-0000-4000-8000-{ordinal:012d}",
                    dedup_id=None,
                    operation_id=None,
                    actor_kind="system",
                    actor_user_id=None,
                    space_id=None,
                    job_type=job_type,
                    handler_version="v1",
                    effect_key=f"p7.2:{job_type}:system:{ordinal}",
                    input_ref={"ordinal": ordinal, "schema_version": 1},
                    expected_state={},
                    authorization_snapshot={},
                    authorization_scope_epoch=None,
                    authorization_part_id=None,
                    authorization_capabilities=(),
                    max_attempts=3,
                    cancellable=True,
                    available_at_ms=0,
                ),
            )
    finally:
        connection.close()


def _config() -> DurableInferenceWorkerConfig:
    return DurableInferenceWorkerConfig(
        claim_owner="p7.2-worker",
        lease_ms=3_000,
        heartbeat_interval_ms=500,
        idle_poll_ms=5,
        maximum_idle_poll_ms=20,
    )


def test_web_enqueues_and_restart_stable_actor_fairness_prevents_starvation(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
) -> None:
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "web")
    factory = _factory(isolated_knowledge_layout)
    first_space, first_epoch = _seed_actor(factory, "worker-a")
    second_space, second_epoch = _seed_actor(factory, "worker-b")
    calls: list[tuple[str, str | None]] = []
    handler = _handler("fixture.gpu", calls)
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry((handler,)),
        scope_resolver=ScopeResolver(_flags()),
    )
    _enqueue_user(
        factory,
        kernel,
        user_id="worker-a",
        space_id=first_space,
        scope_epoch=first_epoch,
        job_type="fixture.gpu",
        ordinal=1,
    )
    _enqueue_user(
        factory,
        kernel,
        user_id="worker-a",
        space_id=first_space,
        scope_epoch=first_epoch,
        job_type="fixture.gpu",
        ordinal=2,
    )
    _enqueue_user(
        factory,
        kernel,
        user_id="worker-b",
        space_id=second_space,
        scope_epoch=second_epoch,
        job_type="fixture.gpu",
        ordinal=3,
    )

    # The producer process can persist durable references but cannot execute
    # local inference.  Only the separately selected worker role may claim.
    with pytest.raises(
        LocalInferenceOwnershipError, match="disabled in production web"
    ):
        assert_local_inference_allowed()
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "worker")

    # Reconstruct the service between every claim.  Fairness is durable
    # scheduler state, not an in-memory cursor that resets after a crash.
    assert DurableInferenceWorker(kernel, _config()).run_once().run.job.state == "succeeded"
    assert DurableInferenceWorker(kernel, _config()).run_once().run.job.state == "succeeded"
    assert DurableInferenceWorker(kernel, _config()).run_once().run.job.state == "succeeded"
    assert calls == [
        ("fixture.gpu", "worker-a"),
        ("fixture.gpu", "worker-b"),
        ("fixture.gpu", "worker-a"),
    ]


def test_exact_handler_versions_rotate_before_an_older_lane_can_monopolize(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
) -> None:
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "worker")
    factory = _factory(isolated_knowledge_layout)
    calls: list[tuple[str, str | None]] = []
    first = _handler("fixture.gpu-a", calls, actor_kind="system")
    second = _handler("fixture.gpu-b", calls, actor_kind="system")
    kernel = DurableWorkKernel(factory, HandlerRegistry((first, second)))
    _enqueue_system(factory, kernel, job_type="fixture.gpu-a", ordinal=1)
    _enqueue_system(factory, kernel, job_type="fixture.gpu-a", ordinal=2)
    _enqueue_system(factory, kernel, job_type="fixture.gpu-b", ordinal=3)

    DurableInferenceWorker(kernel, _config()).run_once()
    DurableInferenceWorker(kernel, _config()).run_once()
    DurableInferenceWorker(kernel, _config()).run_once()
    assert [value[0] for value in calls] == [
        "fixture.gpu-a",
        "fixture.gpu-b",
        "fixture.gpu-a",
    ]


def test_long_preparation_renews_claim_until_atomic_terminal_commit(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
) -> None:
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "worker")
    factory = _factory(isolated_knowledge_layout)
    calls: list[tuple[str, str | None]] = []

    def prepare_under_owned_runtime() -> None:
        assert_local_inference_allowed()
        time.sleep(0.45)

    handler = _handler(
        "fixture.slow-gpu",
        calls,
        actor_kind="system",
        prepare_hook=prepare_under_owned_runtime,
    )
    kernel = DurableWorkKernel(factory, HandlerRegistry((handler,)))
    _enqueue_system(factory, kernel, job_type="fixture.slow-gpu", ordinal=1)
    config = DurableInferenceWorkerConfig(
        claim_owner="p7.2-heartbeat",
        lease_ms=600,
        heartbeat_interval_ms=100,
        idle_poll_ms=5,
        maximum_idle_poll_ms=20,
    )

    result = DurableInferenceWorker(kernel, config).run_once()
    assert result.run is not None and result.run.job.state == "succeeded"
    assert result.heartbeat_count >= 2
    assert result.run.job.attempt_count == 1


def test_bounded_service_stops_after_requested_job_count_and_reports_results(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
) -> None:
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "worker")
    factory = _factory(isolated_knowledge_layout)
    calls: list[tuple[str, str | None]] = []
    handler = _handler("fixture.service", calls, actor_kind="system")
    kernel = DurableWorkKernel(factory, HandlerRegistry((handler,)))
    _enqueue_system(factory, kernel, job_type="fixture.service", ordinal=1)
    _enqueue_system(factory, kernel, job_type="fixture.service", ordinal=2)

    result = DurableInferenceWorker(kernel, _config()).serve(
        threading.Event(), maximum_jobs=2
    )
    assert result.claimed == 2
    assert result.idle_polls == 0
    assert result.terminal_counts == (("succeeded", 2),)


def test_idle_service_uses_stop_aware_backoff_without_starting_hidden_work(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
) -> None:
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "worker")
    factory = _factory(isolated_knowledge_layout)
    calls: list[tuple[str, str | None]] = []
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(
            (_handler("fixture.idle", calls, actor_kind="system"),)
        ),
    )

    class _StopAfterWait:
        def __init__(self) -> None:
            self.stopped = False
            self.waits: list[float | None] = []

        def is_set(self) -> bool:
            return self.stopped

        def wait(self, timeout: float | None = None) -> bool:
            self.waits.append(timeout)
            self.stopped = True
            return True

    stop = _StopAfterWait()
    result = DurableInferenceWorker(kernel, _config()).serve(stop)
    assert result.iterations == 1
    assert result.claimed == 0
    assert result.idle_polls == 1
    assert stop.waits == [pytest.approx(0.005)]
    assert calls == []


def test_bounded_maintenance_runs_before_each_claim_poll(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
) -> None:
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "worker")
    factory = _factory(isolated_knowledge_layout)
    calls: list[tuple[str, str | None]] = []
    maintenance: list[str] = []
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(
            (_handler("fixture.maintenance", calls, actor_kind="system"),)
        ),
    )
    result = DurableInferenceWorker(
        kernel,
        _config(),
        before_poll=lambda: maintenance.append("bounded"),
    ).run_once()
    assert result.idle is True
    assert maintenance == ["bounded"]
    assert calls == []


def test_config_rejects_heartbeat_that_cannot_protect_the_lease() -> None:
    with pytest.raises(ValueError, match="one third"):
        DurableInferenceWorkerConfig(
            claim_owner="worker",
            lease_ms=600,
            heartbeat_interval_ms=200,
        )

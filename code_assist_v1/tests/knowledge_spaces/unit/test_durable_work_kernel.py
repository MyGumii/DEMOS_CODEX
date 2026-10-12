from __future__ import annotations

from collections.abc import Mapping
import time
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    TransactionMode,
    db_now_ms,
    transaction,
)
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.audit import (
    AuditEventDraft,
    AuditRepository,
)
from code_assist_v1.knowledge_core.repositories.jobs import (
    HandlerKey,
    JobEffectConflictError,
    JobEnqueueDraft,
    JobRepository,
    canonical_job_input_sha256,
)
from code_assist_v1.knowledge_core.repositories.higher_spaces import (
    HigherSpaceRepository,
)
from code_assist_v1.knowledge_core.repositories.outbox import (
    OutboxEventDraft,
    OutboxRepository,
)
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.scope import (
    Capability,
    ScopeResolver,
    SessionPrincipal,
)
from code_assist_v1.knowledge_core.services.higher_authority import (
    HigherAuthorityService,
    SetHigherRoleCommand,
)
from code_assist_v1.knowledge_core.services.organization import (
    MoveOrgUnitCommand,
    OrgChangePreviewCommand,
    OrganizationService,
    OrgMoveResolution,
)
from code_assist_v1.knowledge_core.worker import (
    DurableWorkKernel,
    HandlerRegistry,
    JobExpectedStateConflictError,
    JobHandlerRegistryError,
    PermanentJobExecutionError,
    PreparedJobEffect,
    RegisteredJobHandler,
)


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("e76d9950-8982-405e-94e1-239c717a1874")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p1.9-durable-kernel-test",
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


def _seed_actor(factory: ConnectionFactory, label: str) -> dict[str, object]:
    user_id = f"job-{label}"
    space_id = _uuid(f"space:{label}")
    users = UserRepository()
    spaces = SpaceRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            users.import_legacy(
                connection,
                LegacyUserRecord(
                    id=user_id,
                    display_name=f"Job {label}",
                    password_hash=f"scrypt:p1.9:{label}",
                    system_role="user",
                    account_status="active",
                    created_at_ms=100,
                    updated_at_ms=100,
                ),
            )
            ensured = spaces.ensure_personal(
                connection,
                space_id=space_id,
                user_id=user_id,
                created_at_ms=100,
            )
            enabled = spaces.update_state(
                connection,
                space_id=space_id,
                expected_row_version=ensured.space.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=101,
            )
            user = users.require(connection, user_id)
        return {
            "space_id": space_id,
            "space_row_version": enabled.row_version,
            "user_id": user_id,
            "user_row_version": user.row_version,
            "scope_epoch": user.scope_epoch,
        }
    finally:
        connection.close()


def _seed_part_actor(factory: ConnectionFactory, label: str) -> dict[str, object]:
    user_id = f"job-{label}"
    leader_id = f"leader-{label}"
    team_id = _uuid(f"team:{label}")
    part_a_id = _uuid(f"part-a:{label}")
    part_b_id = _uuid(f"part-b:{label}")
    space_id = _uuid(f"part-space:{label}")
    membership_id = _uuid(f"membership-a:{label}")
    users = UserRepository()
    organization = OrganizationRepository()
    spaces = SpaceRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            users.import_legacy(
                connection,
                LegacyUserRecord(
                    id=user_id,
                    display_name=f"Job {label}",
                    password_hash=f"scrypt:p1.9:{label}",
                    system_role="user",
                    account_status="active",
                    created_at_ms=100,
                    updated_at_ms=100,
                ),
            )
            users.import_legacy(
                connection,
                LegacyUserRecord(
                    id=leader_id,
                    display_name=f"Leader {label}",
                    password_hash=f"scrypt:p1.9:leader:{label}",
                    system_role="user",
                    account_status="active",
                    created_at_ms=100,
                    updated_at_ms=100,
                ),
            )
            organization.create_unit(
                connection,
                unit_id=team_id,
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name=f"Team {label}",
                actor_user_id=user_id,
                now_ms=100,
            )
            for part_id, suffix in ((part_a_id, "A"), (part_b_id, "B")):
                organization.create_unit(
                    connection,
                    unit_id=part_id,
                    kind="part",
                    parent_id=team_id,
                    name=f"Part {label} {suffix}",
                    actor_user_id=user_id,
                    now_ms=101,
                )
            organization.open_membership(
                connection,
                membership_id=membership_id,
                user_id=user_id,
                part_id=part_a_id,
                assigned_by=user_id,
                reason="P1.9 membership-move fixture",
                valid_from_ms=102,
            )
            user = users.require(connection, user_id)
            moved = organization.compare_and_swap_user_part(
                connection,
                user_id=user_id,
                expected_current_part_id=None,
                target_part_id=part_a_id,
                expected_scope_epoch=user.scope_epoch,
                expected_row_version=user.row_version,
                updated_at_ms=103,
            )
            leader_membership_id = _uuid(f"leader-membership:{label}")
            organization.open_membership(
                connection,
                membership_id=leader_membership_id,
                user_id=leader_id,
                part_id=part_a_id,
                assigned_by=user_id,
                reason="P1.9 leader fixture",
                valid_from_ms=102,
            )
            leader = users.require(connection, leader_id)
            organization.compare_and_swap_user_part(
                connection,
                user_id=leader_id,
                expected_current_part_id=None,
                target_part_id=part_a_id,
                expected_scope_epoch=leader.scope_epoch,
                expected_row_version=leader.row_version,
                updated_at_ms=103,
            )
            organization.open_part_leader(
                connection,
                assignment_id=_uuid(f"leader-assignment:{label}"),
                user_id=leader_id,
                part_id=part_a_id,
                assigned_by=user_id,
                reason="P1.9 leader fixture",
                valid_from_ms=104,
            )
            ensured = spaces.ensure_part(
                connection,
                space_id=space_id,
                part_id=part_a_id,
                created_at_ms=105,
            )
            enabled = spaces.update_state(
                connection,
                space_id=space_id,
                expected_row_version=ensured.space.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=106,
            )
        return {
            "membership_id": membership_id,
            "part_a_id": part_a_id,
            "part_b_id": part_b_id,
            "space_id": space_id,
            "space_row_version": enabled.row_version,
            "user_id": user_id,
            "user_row_version": moved.row_version,
            "scope_epoch": moved.scope_epoch,
        }
    finally:
        connection.close()


def _higher_flags() -> FeatureFlags:
    return FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        part_reads=True,
        higher_spaces=True,
        team_writes=True,
    )


def _seed_higher_actor(factory: ConnectionFactory, label: str) -> dict[str, object]:
    team_id = _uuid(f"higher-team:{label}")
    part_id = _uuid(f"higher-part:{label}")
    users = UserRepository()
    organization = OrganizationRepository()
    spaces = SpaceRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            for user_id, admin in (
                ("admin", True),
                (f"higher-{label}", False),
                (f"replacement-{label}", False),
            ):
                users.import_legacy(
                    connection,
                    LegacyUserRecord(
                        id=user_id,
                        display_name=user_id.title(),
                        password_hash=f"scrypt:sbe-2.3:{user_id}",
                        system_role="admin" if admin else "user",
                        account_status="active",
                        created_at_ms=100,
                        updated_at_ms=100,
                    ),
                )
            organization.create_unit(
                connection,
                unit_id=team_id,
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name=f"Higher Team {label}",
                actor_user_id="admin",
                now_ms=100,
            )
            organization.create_unit(
                connection,
                unit_id=part_id,
                kind="part",
                parent_id=team_id,
                name=f"Higher Part {label}",
                actor_user_id="admin",
                now_ms=101,
            )
            spaces.ensure_higher(
                connection,
                space_id=team_id,
                org_unit_id=team_id,
                kind="team",
                created_at_ms=104,
            )
            spaces.ensure_part(
                connection,
                space_id=part_id,
                part_id=part_id,
                created_at_ms=104,
            )
    finally:
        connection.close()
    service = HigherAuthorityService(
        factory,
        ScopeResolver(_higher_flags()),
        cursor_secret=b"sbe-2.3-worker-role-cursor-secret-v1",
    )
    assigned = service.set_role(
        SetHigherRoleCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid(f"higher-role-assign:{label}"),
            request_id=_uuid(f"higher-role-request:{label}"),
            org_unit_id=team_id,
            role="team_leader",
            assignee_user_id=f"higher-{label}",
            expected_current_assignment_id=None,
            expected_current_assignment_generation=None,
            expected_current_assignment_row_version=None,
            expected_space_row_version=1,
            enable_writes=True,
            reason="assign higher job fixture role",
        )
    )
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            leader_epoch = users.require(
                connection, f"higher-{label}"
            ).scope_epoch
    finally:
        connection.close()
    return {
        "assigned": assigned,
        "part_id": part_id,
        "replacement_user_id": f"replacement-{label}",
        "scope_epoch": leader_epoch,
        "space_id": team_id,
        "space_row_version": assigned.space_row_version,
        "user_id": f"higher-{label}",
    }


def _handler(
    facts: Mapping[str, object],
    *,
    effect_calls: list[str],
    prepare_calls: list[str] | None = None,
) -> RegisteredJobHandler:
    spaces = SpaceRepository()
    audits = AuditRepository()
    outbox = OutboxRepository()
    operation_id = _uuid(f"operation:{facts['user_id']}")

    def prepare(job):
        if prepare_calls is not None:
            prepare_calls.append(job.id)
        return PreparedJobEffect(
            result={
                "schema_version": 1,
                "space_id": job.space_id,
                "space_row_version": int(job.expected_state["space_row_version"]) + 1,
            },
            payload={"prepared": True},
        )

    def revalidate_expected_state(connection, job) -> None:
        current = spaces.require_by_id(connection, str(job.space_id))
        if current.row_version != job.expected_state["space_row_version"]:
            raise JobExpectedStateConflictError(
                "The target aggregate changed after durable preparation."
            )

    def commit_effect(connection, job, prepared) -> None:
        effect_calls.append(job.id)
        expected = int(job.expected_state["space_row_version"])
        updated = spaces.update_state(
            connection,
            space_id=str(job.space_id),
            expected_row_version=expected,
            status="active",
            write_enabled=False,
            updated_at_ms=db_now_ms(connection),
        )
        audits.append(
            connection,
            AuditEventDraft(
                event_uuid=_uuid(f"audit:{job.id}"),
                occurred_at_ms=db_now_ms(connection),
                request_id=_uuid(f"request:{facts['user_id']}"),
                operation_id=operation_id,
                action="fixture.job.effect",
                outcome="succeeded",
                actor_kind="user",
                actor_user_id=str(facts["user_id"]),
                actor_display_name=f"Job {facts['user_id']}",
                actor_role_snapshot="user",
                space_id=str(job.space_id),
                details={
                    "job_id": job.id,
                    "row_version": updated.row_version,
                    "schema_version": 1,
                },
            ),
        )
        outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"fixture-job:{job.effect_key}:v1",
                space_id=str(job.space_id),
                aggregate_type="knowledge_space",
                aggregate_id=str(job.space_id),
                aggregate_version=updated.row_version,
                event_type="fixture.job.succeeded",
                payload={
                    "job_id": job.id,
                    "schema_version": 1,
                    "space_id": str(job.space_id),
                },
                available_at_ms=db_now_ms(connection),
            ),
        )

    return RegisteredJobHandler.build(
        job_type="fixture.effect",
        handler_version="v1",
        actor_kind="user",
        required_capability=Capability.EDIT,
        prepare=prepare,
        revalidate_expected_state=revalidate_expected_state,
        commit_effect=commit_effect,
    )


def _enqueue(
    factory: ConnectionFactory,
    kernel: DurableWorkKernel,
    facts: Mapping[str, object],
    label: str,
    *,
    cancellable: bool = True,
):
    users = UserRepository()
    input_ref = {
        "resource_id": _uuid(f"resource:{label}"),
        "schema_version": 1,
    }
    operation_id = _uuid(f"operation:{facts['user_id']}")
    dedup_id = _uuid(f"dedup:{facts['user_id']}")
    job_id = _uuid(f"job:{facts['user_id']}")
    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            users.begin_operation(
                connection,
                actor_user_id=str(facts["user_id"]),
                operation_type="job:fixture.effect",
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
                    actor_user_id=str(facts["user_id"]),
                    space_id=str(facts["space_id"]),
                    job_type="fixture.effect",
                    handler_version="v1",
                    effect_key=f"fixture:{facts['user_id']}:v1",
                    input_ref=input_ref,
                    expected_state={
                        "space_row_version": int(facts["space_row_version"]),
                    },
                    authorization_snapshot={},
                    authorization_scope_epoch=int(facts["scope_epoch"]),
                    authorization_part_id=None,
                    authorization_capabilities=(),
                    max_attempts=3,
                    cancellable=cancellable,
                    available_at_ms=now_ms,
                ),
            )
            users.commit_operation(
                connection,
                dedup_id=dedup_id,
                result={"job_id": result.job.id, "schema_version": 1},
                http_status=202,
                completed_at_ms=now_ms,
            )
            return result
    finally:
        connection.close()


def _kernel(
    factory: ConnectionFactory,
    facts: Mapping[str, object],
    *,
    effect_calls: list[str],
    prepare_calls: list[str] | None = None,
    failpoint=None,
    flags: FeatureFlags | None = None,
) -> DurableWorkKernel:
    return DurableWorkKernel(
        factory,
        HandlerRegistry(
            (
                _handler(
                    facts,
                    effect_calls=effect_calls,
                    prepare_calls=prepare_calls,
                ),
            )
        ),
        scope_resolver=ScopeResolver(_flags() if flags is None else flags),
        failpoint=failpoint,
    )


@pytest.mark.scenario("JOB-001")
@pytest.mark.scenario("JOB-003")
@pytest.mark.scenario("JOB-004")
def test_enqueue_run_and_response_loss_replay_commit_one_domain_effect(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    facts = _seed_actor(factory, "stable")
    effect_calls: list[str] = []
    prepare_calls: list[str] = []
    kernel = _kernel(
        factory, facts, effect_calls=effect_calls, prepare_calls=prepare_calls
    )
    enqueued = _enqueue(factory, kernel, facts, "stable")

    kernel.verify_registry_coverage()
    recovery, claim = kernel.recover_and_claim(
        claim_owner="worker-stable", lease_ms=60_000
    )
    assert recovery.retried_ids == ()
    assert recovery.dead_ids == ()
    assert recovery.cancelled_ids == ()
    assert claim is not None and claim.job.id == enqueued.job.id

    completed = kernel.run_claim(claim)
    assert completed.job.state == "succeeded"
    assert completed.replayed is False
    assert effect_calls == [enqueued.job.id]
    assert prepare_calls == [enqueued.job.id]

    prepared_again = _handler(
        facts, effect_calls=effect_calls
    ).prepare(completed.job)
    replay = kernel.commit_prepared(claim.fence, prepared_again)
    assert replay.job.result == completed.job.result
    assert replay.replayed is True
    assert effect_calls == [enqueued.job.id]

    with pytest.raises(JobEffectConflictError):
        kernel.commit_prepared(
            claim.fence,
            PreparedJobEffect(result={"schema_version": 999}),
        )

    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            space = SpaceRepository().require_by_id(
                connection, str(facts["space_id"])
            )
            assert space.write_enabled is False
            assert space.row_version == int(facts["space_row_version"]) + 1
            assert connection.execute(
                "SELECT count(*) FROM audit_event WHERE action='fixture.job.effect'"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM outbox_event WHERE event_type='fixture.job.succeeded'"
            ).fetchone()[0] == 1
    finally:
        connection.close()


def test_job_after_enqueue_failpoint_rolls_back_producer_transaction(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    facts = _seed_actor(factory, "enqueue-fault")

    def stop(name: str) -> None:
        if name == "job.after_enqueue":
            raise RuntimeError(name)

    kernel = _kernel(factory, facts, effect_calls=[], failpoint=stop)
    with pytest.raises(RuntimeError, match="job.after_enqueue"):
        _enqueue(factory, kernel, facts, "enqueue-fault")

    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM durable_job").fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM operation_dedup WHERE operation_type='job:fixture.effect'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_job_kill_after_claim_is_recovered_and_committed_once(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    facts = _seed_actor(factory, "claim-fault")
    effect_calls: list[str] = []

    def stop(name: str) -> None:
        if name == "job.after_claim":
            raise SystemExit(92)

    faulting = _kernel(
        factory, facts, effect_calls=effect_calls, failpoint=stop
    )
    enqueued = _enqueue(factory, faulting, facts, "claim-fault")
    with pytest.raises(SystemExit, match="92"):
        faulting.recover_and_claim(claim_owner="claim-fault", lease_ms=10)

    deadline = time.monotonic() + 2
    while True:
        connection = factory.open(read_only=True)
        try:
            row = connection.execute(
                "SELECT state,claim_expires_at FROM durable_job WHERE id=?",
                (enqueued.job.id,),
            ).fetchone()
            now_ms = db_now_ms(connection)
        finally:
            connection.close()
        assert row is not None and row[0] == "claimed"
        if row[1] is not None and now_ms >= int(row[1]):
            break
        if time.monotonic() >= deadline:
            raise AssertionError("durable job claim did not expire")
        time.sleep(0.005)

    replacement = _kernel(factory, facts, effect_calls=effect_calls)
    recovery, claim = replacement.recover_and_claim(
        claim_owner="claim-replacement", lease_ms=60_000
    )
    assert recovery.retried_ids == (enqueued.job.id,)
    assert claim is not None and claim.fence.attempt_count == 2
    completed = replacement.run_claim(claim)
    assert completed.job.state == "succeeded"
    assert effect_calls == [enqueued.job.id]


@pytest.mark.parametrize(
    "failpoint",
    ("job.after_domain_commit_before_ack", "job.after_ack"),
)
def test_job_post_commit_response_loss_replays_exact_effect(
    isolated_knowledge_layout,
    failpoint: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    facts = _seed_actor(factory, f"post-commit-{failpoint}")
    effect_calls: list[str] = []

    def stop(name: str) -> None:
        if name == failpoint:
            raise RuntimeError(name)

    faulting = _kernel(
        factory, facts, effect_calls=effect_calls, failpoint=stop
    )
    enqueued = _enqueue(factory, faulting, facts, f"post-commit-{failpoint}")
    _recovery, claim = faulting.recover_and_claim(
        claim_owner=f"worker-{failpoint}", lease_ms=60_000
    )
    assert claim is not None
    with pytest.raises(RuntimeError, match=failpoint):
        faulting.run_claim(claim)

    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            stored = JobRepository().require(connection, enqueued.job.id)
            assert stored.state == "succeeded"
            assert connection.execute(
                "SELECT count(*) FROM audit_event WHERE action='fixture.job.effect'"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM outbox_event WHERE logical_key=?",
                (f"fixture-job:{enqueued.job.effect_key}:v1",),
            ).fetchone()[0] == 1
    finally:
        connection.close()

    replacement = _kernel(factory, facts, effect_calls=effect_calls)
    prepared = _handler(facts, effect_calls=effect_calls).prepare(stored)
    replay = replacement.commit_prepared(claim.fence, prepared)
    assert replay.replayed is True
    assert replay.job.result == stored.result
    assert effect_calls == [enqueued.job.id]


@pytest.mark.scenario("JOB-006")
def test_account_disable_after_prepare_rolls_back_effect_then_records_failure(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    facts = _seed_actor(factory, "disabled")
    effect_calls: list[str] = []
    kernel = _kernel(factory, facts, effect_calls=effect_calls)
    _enqueue(factory, kernel, facts, "disabled", cancellable=False)
    _recovery, claim = kernel.recover_and_claim(
        claim_owner="worker-disabled", lease_ms=60_000
    )
    assert claim is not None

    connection = factory.open()
    try:
        with transaction(connection):
            JobRepository().start(
                connection, claim.fence, now_ms=db_now_ms(connection)
            )
    finally:
        connection.close()
    prepared = kernel.registry.require_job(claim.job).prepare(claim.job)

    connection = factory.open()
    try:
        with transaction(connection):
            current = UserRepository().require(
                connection, str(facts["user_id"])
            )
            UserRepository().transition_account_status(
                connection,
                user_id=current.id,
                target_status="disabled",
                expected_row_version=current.row_version,
                changed_at_ms=db_now_ms(connection),
            )
    finally:
        connection.close()

    rejected = kernel.commit_prepared(claim.fence, prepared)
    assert rejected.job.state == "failed"
    assert rejected.job.error is not None
    assert rejected.job.error["code"] == "JOB_AUTHORIZATION_REJECTED"
    assert effect_calls == []

    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            space = SpaceRepository().require_by_id(
                connection, str(facts["space_id"])
            )
            assert space.write_enabled is True
            assert connection.execute(
                "SELECT count(*) FROM audit_event WHERE action='fixture.job.effect'"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM outbox_event WHERE event_type='fixture.job.succeeded'"
            ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.scenario("JOB-006")
def test_membership_move_after_prepare_rejects_old_part_effect(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    facts = _seed_part_actor(factory, "moved")
    effect_calls: list[str] = []
    kernel = _kernel(factory, facts, effect_calls=effect_calls)
    _enqueue(factory, kernel, facts, "moved", cancellable=False)
    _recovery, claim = kernel.recover_and_claim(
        claim_owner="worker-moved", lease_ms=60_000
    )
    assert claim is not None

    connection = factory.open()
    try:
        with transaction(connection):
            running = JobRepository().start(
                connection, claim.fence, now_ms=db_now_ms(connection)
            )
    finally:
        connection.close()
    prepared = kernel.registry.require_job(running).prepare(running)

    connection = factory.open()
    try:
        with transaction(connection):
            organization = OrganizationRepository()
            changed_at = db_now_ms(connection)
            organization.close_current_membership(
                connection,
                user_id=str(facts["user_id"]),
                expected_membership_id=str(facts["membership_id"]),
                closed_at_ms=changed_at,
            )
            organization.open_membership(
                connection,
                membership_id=_uuid("membership-b:moved"),
                user_id=str(facts["user_id"]),
                part_id=str(facts["part_b_id"]),
                assigned_by=str(facts["user_id"]),
                reason="P1.9 move during prepare",
                valid_from_ms=changed_at,
            )
            organization.compare_and_swap_user_part(
                connection,
                user_id=str(facts["user_id"]),
                expected_current_part_id=str(facts["part_a_id"]),
                target_part_id=str(facts["part_b_id"]),
                expected_scope_epoch=int(facts["scope_epoch"]),
                expected_row_version=int(facts["user_row_version"]),
                updated_at_ms=changed_at,
            )
            organization.invalidate_user_jobs_for_scope_epoch(
                connection,
                user_id=str(facts["user_id"]),
                invalidated_scope_epoch=int(facts["scope_epoch"]),
                updated_at_ms=changed_at,
            )
    finally:
        connection.close()

    rejected = kernel.commit_prepared(claim.fence, prepared)
    assert rejected.job.state == "failed"
    assert rejected.job.error is not None
    assert rejected.job.error["code"] == "JOB_AUTHORIZATION_REJECTED"
    assert effect_calls == []

    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            space = SpaceRepository().require_by_id(
                connection, str(facts["space_id"])
            )
            assert space.write_enabled is True
            assert space.row_version == facts["space_row_version"]
            assert connection.execute(
                "SELECT count(*) FROM audit_event WHERE action='fixture.job.effect'"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM outbox_event WHERE event_type='fixture.job.succeeded'"
            ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.scenario("HSPACE-011")
def test_same_user_higher_role_aba_rejects_prepared_job_effect(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    facts = _seed_higher_actor(factory, "aba")
    effect_calls: list[str] = []
    kernel = _kernel(
        factory,
        facts,
        effect_calls=effect_calls,
        flags=_higher_flags(),
    )
    enqueued = _enqueue(factory, kernel, facts, "higher-aba", cancellable=False)
    fence = enqueued.job.expected_state["_authorization_fence"]
    assigned = facts["assigned"]
    assert fence["role_assignment_id"] == assigned.assignment_id
    assert fence["role_assignment_generation"] == 1

    _recovery, claim = kernel.recover_and_claim(
        claim_owner="worker-higher-aba", lease_ms=60_000
    )
    assert claim is not None
    connection = factory.open()
    try:
        with transaction(connection):
            running = JobRepository().start(
                connection, claim.fence, now_ms=db_now_ms(connection)
            )
    finally:
        connection.close()

    prepared = kernel.registry.require_job(running).prepare(running)

    service = HigherAuthorityService(
        factory,
        ScopeResolver(_higher_flags()),
        cursor_secret=b"sbe-2.3-worker-role-cursor-secret-v1",
    )
    replaced = service.set_role(
        SetHigherRoleCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("higher-role-replace:aba"),
            request_id=_uuid("higher-role-replace-request:aba"),
            org_unit_id=str(facts["space_id"]),
            role="team_leader",
            assignee_user_id=str(facts["replacement_user_id"]),
            expected_current_assignment_id=assigned.assignment_id,
            expected_current_assignment_generation=assigned.assignment_generation,
            expected_current_assignment_row_version=assigned.assignment_row_version,
            expected_space_row_version=assigned.space_row_version,
            enable_writes=True,
            reason="replace role during prepared job",
        )
    )
    reassigned = service.set_role(
        SetHigherRoleCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("higher-role-reassign-same-user:aba"),
            request_id=_uuid("higher-role-reassign-same-user-request:aba"),
            org_unit_id=str(facts["space_id"]),
            role="team_leader",
            assignee_user_id=str(facts["user_id"]),
            expected_current_assignment_id=replaced.assignment_id,
            expected_current_assignment_generation=replaced.assignment_generation,
            expected_current_assignment_row_version=replaced.assignment_row_version,
            expected_space_row_version=replaced.space_row_version,
            enable_writes=True,
            reason="reassign original user as a new episode",
        )
    )
    assert reassigned.assignment_generation == 3
    assert reassigned.assignment_id != assigned.assignment_id

    rejected = kernel.commit_prepared(claim.fence, prepared)
    assert rejected.job.state == "failed"
    assert rejected.job.error is not None
    assert rejected.job.error["code"] == "JOB_AUTHORIZATION_REJECTED"
    assert effect_calls == []
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            current = HigherSpaceRepository().get_open_role(
                connection,
                org_unit_id=str(facts["space_id"]),
                role="team_leader",
            )
            space = SpaceRepository().require_by_id(
                connection, str(facts["space_id"])
            )
    finally:
        connection.close()
    assert current is not None and current.id == reassigned.assignment_id
    assert space.write_enabled is True


@pytest.mark.scenario("HSPACE-014")
def test_org_move_rejects_prepared_non_cancellable_closure_effect(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    facts = _seed_part_actor(factory, "org-move")
    effect_calls: list[str] = []
    kernel = _kernel(factory, facts, effect_calls=effect_calls)
    enqueued = _enqueue(
        factory,
        kernel,
        facts,
        "org-move",
        cancellable=False,
    )
    old_closure_sha = enqueued.job.expected_state["_authorization_fence"][
        "corpus_closure_sha256"
    ]
    _recovery, claim = kernel.recover_and_claim(
        claim_owner="worker-org-move", lease_ms=60_000
    )
    assert claim is not None
    connection = factory.open()
    try:
        with transaction(connection):
            running = JobRepository().start(
                connection, claim.fence, now_ms=db_now_ms(connection)
            )
    finally:
        connection.close()
    prepared = kernel.registry.require_job(running).prepare(running)

    admin_id = "admin-orgmove"
    destination_team_id = _uuid("destination-team:org-move")
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy(
                connection,
                LegacyUserRecord(
                    id=admin_id,
                    display_name="Organization Move Admin",
                    password_hash="scrypt:sbe-2.3:org-move-admin",
                    system_role="admin",
                    account_status="active",
                    created_at_ms=100,
                    updated_at_ms=100,
                ),
            )
            OrganizationRepository().create_unit(
                connection,
                unit_id=destination_team_id,
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Destination Team Org Move",
                actor_user_id=admin_id,
                now_ms=200,
            )
            SpaceRepository().ensure_higher(
                connection,
                space_id=destination_team_id,
                org_unit_id=destination_team_id,
                kind="team",
                created_at_ms=200,
            )
    finally:
        connection.close()

    organization = OrganizationService(
        factory,
        preview_hmac_key=b"sbe-2.3-worker-org-move-preview-v1",
    )
    preview = organization.preview_change(
        OrgChangePreviewCommand(
            actor_user_id=admin_id,
            actor_scope_epoch=1,
            request_id=_uuid("org-move-preview-request"),
            unit_id=str(facts["part_a_id"]),
            expected_row_version=1,
            change="move",
            new_parent_id=destination_team_id,
        )
    )
    assert preview.allowed is True
    operation_id = _uuid("org-move-operation")
    organization.move_unit(
        MoveOrgUnitCommand(
            actor_user_id=admin_id,
            actor_scope_epoch=1,
            operation_id=operation_id,
            request_id=_uuid("org-move-request"),
            unit_id=str(facts["part_a_id"]),
            expected_row_version=1,
            preview_token=preview.preview_token,
            new_parent_id=destination_team_id,
            resolution=OrgMoveResolution(),
            reason="Fence every old closure before moving the Part",
        )
    )

    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            current = JobRepository().require(connection, enqueued.job.id)
            assert current.state == "running"
            assert current.cancel_requested is False
            resolved = ScopeResolver(_flags()).resolve_in_transaction(
                connection,
                SessionPrincipal(
                    user_id=str(facts["user_id"]),
                    captured_scope_epoch=int(facts["scope_epoch"]),
                ),
                str(facts["space_id"]),
                Capability.EDIT,
            )
            assert resolved.access_plan is not None
            assert resolved.access_plan.corpus_closure.sha256 != old_closure_sha
            invalidated = connection.execute(
                """SELECT json_extract(details_json,'$.invalidated_job_count')
                     FROM audit_event
                    WHERE action='organization.unit.move'
                      AND operation_id=?""",
                (operation_id,),
            ).fetchone()
            assert invalidated is not None and int(invalidated[0]) == 1
    finally:
        connection.close()

    rejected = kernel.commit_prepared(claim.fence, prepared)
    assert rejected.job.state == "failed"
    assert rejected.job.error is not None
    assert rejected.job.error["code"] == "JOB_AUTHORIZATION_REJECTED"
    assert effect_calls == []


@pytest.mark.scenario("JOB-006")
def test_expected_state_change_after_prepare_rolls_back_effect(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    facts = _seed_actor(factory, "state")
    effect_calls: list[str] = []
    kernel = _kernel(factory, facts, effect_calls=effect_calls)
    _enqueue(factory, kernel, facts, "state", cancellable=False)
    _recovery, claim = kernel.recover_and_claim(
        claim_owner="worker-state", lease_ms=60_000
    )
    assert claim is not None
    connection = factory.open()
    try:
        with transaction(connection):
            running = JobRepository().start(
                connection, claim.fence, now_ms=db_now_ms(connection)
            )
    finally:
        connection.close()
    prepared = kernel.registry.require_job(running).prepare(running)

    connection = factory.open()
    try:
        with transaction(connection):
            spaces = SpaceRepository()
            current = spaces.require_by_id(connection, str(facts["space_id"]))
            disabled = spaces.update_state(
                connection,
                space_id=current.id,
                expected_row_version=current.row_version,
                status="active",
                write_enabled=False,
                updated_at_ms=db_now_ms(connection),
            )
            spaces.update_state(
                connection,
                space_id=disabled.id,
                expected_row_version=disabled.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=db_now_ms(connection),
            )
    finally:
        connection.close()

    rejected = kernel.commit_prepared(claim.fence, prepared)
    assert rejected.job.state == "failed"
    assert rejected.job.error is not None
    assert rejected.job.error["code"] == "JOB_EXPECTED_STATE_CONFLICT"
    assert effect_calls == []


def test_running_cooperative_cancellation_converges_terminal_without_effect(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    facts = _seed_actor(factory, "cancel")
    effect_calls: list[str] = []
    kernel = _kernel(factory, facts, effect_calls=effect_calls)
    _enqueue(factory, kernel, facts, "cancel", cancellable=True)
    _recovery, claim = kernel.recover_and_claim(
        claim_owner="worker-cancel", lease_ms=60_000
    )
    assert claim is not None
    connection = factory.open()
    try:
        with transaction(connection):
            running = JobRepository().start(
                connection, claim.fence, now_ms=db_now_ms(connection)
            )
    finally:
        connection.close()
    prepared = kernel.registry.require_job(running).prepare(running)
    requested = kernel.request_cancel(running.id)
    assert requested.changed is True and requested.immediate is False

    cancelled = kernel.commit_prepared(claim.fence, prepared)
    assert cancelled.job.state == "cancelled"
    assert cancelled.job.cancel_requested is True
    assert effect_calls == []


def test_handler_registry_is_exact_immutable_and_unknown_work_blocks_startup(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    facts = _seed_actor(factory, "registry")
    effect_calls: list[str] = []
    handler = _handler(facts, effect_calls=effect_calls)
    registry = HandlerRegistry((handler,))
    assert registry.keys == (HandlerKey("fixture.effect", "v1"),)
    with pytest.raises(JobHandlerRegistryError):
        HandlerRegistry((handler, handler))
    with pytest.raises((JobHandlerRegistryError, ValueError)):
        RegisteredJobHandler.build(
            job_type=" ",
            handler_version="v1",
            actor_kind="system",
            prepare=lambda job: PreparedJobEffect(result={}),
            commit_effect=lambda connection, job, prepared: None,
        )

    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            JobRepository().enqueue(
                connection,
                JobEnqueueDraft(
                    job_id=_uuid("unknown-job"),
                    dedup_id=None,
                    operation_id=None,
                    actor_kind="system",
                    actor_user_id=None,
                    space_id=None,
                    job_type="unknown.fixture",
                    handler_version="v9",
                    effect_key="unknown:fixture:v9",
                    input_ref={"schema_version": 1},
                    expected_state={},
                    authorization_snapshot={
                        "actor_kind": "system",
                        "schema_version": 1,
                    },
                    authorization_scope_epoch=None,
                    authorization_part_id=None,
                    authorization_capabilities=(),
                    max_attempts=2,
                    cancellable=False,
                    available_at_ms=now_ms,
                ),
            )
    finally:
        connection.close()

    kernel = DurableWorkKernel(
        factory,
        registry,
        scope_resolver=ScopeResolver(_flags()),
    )
    with pytest.raises(JobHandlerRegistryError):
        kernel.verify_registry_coverage()


def test_registry_startup_rejects_matching_handler_version_with_wrong_actor_kind(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    facts = _seed_actor(factory, "registry-actor-kind")
    handler = _handler(facts, effect_calls=[])
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry((handler,)),
        scope_resolver=ScopeResolver(_flags()),
    )

    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            JobRepository().enqueue(
                connection,
                JobEnqueueDraft(
                    job_id=_uuid("wrong-actor-kind-job"),
                    dedup_id=None,
                    operation_id=None,
                    actor_kind="system",
                    actor_user_id=None,
                    space_id=None,
                    job_type=handler.key.job_type,
                    handler_version=handler.key.handler_version,
                    effect_key="wrong-actor-kind:fixture:v1",
                    input_ref={"schema_version": 1},
                    expected_state={},
                    authorization_snapshot={
                        "actor_kind": "system",
                        "schema_version": 1,
                    },
                    authorization_scope_epoch=None,
                    authorization_part_id=None,
                    authorization_capabilities=(),
                    max_attempts=2,
                    cancellable=False,
                    available_at_ms=now_ms,
                ),
            )
    finally:
        connection.close()

    with pytest.raises(JobHandlerRegistryError):
        kernel.verify_registry_coverage()

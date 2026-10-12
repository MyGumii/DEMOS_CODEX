from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import sqlite3
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    db_now_ms,
    transaction,
)
from code_assist_v1.knowledge_core.errors import KnowledgeCoreTransactionError
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.jobs import (
    ClaimFence,
    HandlerKey,
    JobCancellationRequestedError,
    JobCancellationUnsupportedError,
    JobClaimLostError,
    JobEffectConflictError,
    JobEnqueueDraft,
    JobNotFoundError,
    JobRepository,
    JobRepositoryError,
    JobStateConflictError,
    canonical_job_input_sha256,
)
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.migrations.v001_triggers import TRIGGER_DEFINITIONS


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("0fd5ba87-f4a8-4356-8240-ef3a87b4f1c0")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p1.9-durable-job-repository-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _draft(
    connection,
    label: str,
    *,
    handler: HandlerKey = HandlerKey("fixture.job", "v1"),
    effect_key: str | None = None,
    max_attempts: int = 3,
    cancellable: bool = True,
    available_at_ms: int | None = None,
    input_ref: dict[str, object] | None = None,
    expected_state: dict[str, object] | None = None,
) -> JobEnqueueDraft:
    now_ms = db_now_ms(connection)
    return JobEnqueueDraft(
        job_id=_uuid(f"job:{label}"),
        dedup_id=None,
        operation_id=None,
        actor_kind="system",
        actor_user_id=None,
        space_id=None,
        job_type=handler.job_type,
        handler_version=handler.handler_version,
        effect_key=effect_key or f"fixture:{label}",
        input_ref=input_ref or {"label": label},
        expected_state=expected_state or {},
        authorization_snapshot={"schema_version": 1, "actor_kind": "system"},
        authorization_scope_epoch=None,
        authorization_part_id=None,
        authorization_capabilities=(),
        max_attempts=max_attempts,
        cancellable=cancellable,
        available_at_ms=now_ms if available_at_ms is None else available_at_ms,
        created_at_ms=None,
    )


def _enqueue(connection, repository: JobRepository, label: str, **kwargs):
    # SQLite's Windows clock normally has a wider tick than these adjacent
    # statements.  Retry only the pre-insert exact DB-time proof if a boundary
    # is crossed; no row exists when that validation raises.
    for _ in range(20):
        draft = _draft(connection, label, **kwargs)
        try:
            return repository.enqueue(connection, draft), draft
        except ValueError as exc:
            if "created_at_ms must equal" not in str(exc):
                raise
    raise AssertionError("Could not obtain one stable SQLite millisecond tick.")


def _claim(
    connection,
    repository: JobRepository,
    *,
    handler: HandlerKey = HandlerKey("fixture.job", "v1"),
    owner: str = "worker-a",
    now_ms: int | None = None,
    lease_ms: int = 10_000,
):
    claimed = repository.claim_next(
        connection,
        handler_keys=(handler,),
        claim_owner=owner,
        lease_ms=lease_ms,
        now_ms=db_now_ms(connection) if now_ms is None else now_ms,
    )
    assert claimed is not None
    return claimed


def test_repository_requires_a_caller_owned_transaction(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    repository = JobRepository()
    try:
        with pytest.raises(KnowledgeCoreTransactionError):
            repository.get(connection, _uuid("absent"))
        with transaction(connection):
            with pytest.raises(JobNotFoundError):
                repository.require(connection, _uuid("absent"))
    finally:
        connection.close()


def test_enqueue_canonicalizes_hashes_and_exact_replay(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    repository = JobRepository()
    try:
        with transaction(connection):
            first, draft = _enqueue(
                connection,
                repository,
                "canonical",
                input_ref={"z": [True, None, 7], "a": "한글"},
                expected_state={"row_version": 3},
            )
            second = repository.enqueue(connection, draft)
            assert first.inserted is True
            assert second.inserted is False
            assert second.job == first.job
            assert first.job.input_ref_json == '{"a":"한글","z":[true,null,7]}'
            assert first.job.input_sha256 == sha256(
                first.job.input_ref_json.encode("utf-8")
            ).hexdigest()
            assert first.job.authorization_snapshot_sha256 == sha256(
                first.job.authorization_snapshot_json.encode("utf-8")
            ).hexdigest()
            assert first.job.input_ref == {"a": "한글", "z": [True, None, 7]}
            assert first.job.expected_state == {"row_version": 3}
            assert first.job.authorization_capabilities == ()
            assert repository.verify_integrity(connection) == 1
            assert repository.list_nonterminal_handler_keys(connection) == (
                HandlerKey("fixture.job", "v1"),
            )

            conflicting = replace(draft, effect_key="fixture:other")
            with pytest.raises(JobEffectConflictError):
                repository.enqueue(connection, conflicting)
    finally:
        connection.close()


def test_canonical_input_hash_uses_utf16_key_order_and_binds_operation_dedup(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    repository = JobRepository()
    users = UserRepository()
    try:
        with transaction(connection):
            users.import_legacy(
                connection,
                LegacyUserRecord(
                    id="actor",
                    display_name="Actor",
                    password_hash="scrypt:p1.9:actor",
                    system_role="user",
                    account_status="active",
                    created_at_ms=1,
                    updated_at_ms=1,
                ),
            )
            input_ref = {"\ue000": 2, "\U00010000": 1}
            expected_json = '{"\U00010000":1,"\ue000":2}'
            input_hash = canonical_job_input_sha256(input_ref)
            assert input_hash == sha256(expected_json.encode("utf-8")).hexdigest()
            now_ms = db_now_ms(connection)
            operation_id = _uuid("dedup-operation")
            dedup_id = _uuid("dedup-row")
            users.begin_operation(
                connection,
                actor_user_id="actor",
                operation_type="job:fixture.job",
                operation_id=operation_id,
                request_sha256=input_hash,
                dedup_id=dedup_id,
                now_ms=now_ms,
            )
            snapshot = {
                "schema_version": 1,
                "actor_kind": "user",
                "actor_user_id": "actor",
                "account_status": "active",
                "system_role": "user",
                "current_part_id": None,
                "scope_epoch": 1,
                "space_id": None,
                "capabilities": [],
            }
            draft = JobEnqueueDraft(
                    job_id=_uuid("dedup-job"),
                    dedup_id=dedup_id,
                    operation_id=operation_id,
                    actor_kind="user",
                    actor_user_id="actor",
                    space_id=None,
                    job_type="fixture.job",
                    handler_version="v1",
                    effect_key="fixture:dedup",
                    input_ref=input_ref,
                    expected_state={},
                    authorization_snapshot=snapshot,
                    authorization_scope_epoch=1,
                    authorization_part_id=None,
                    authorization_capabilities=(),
                    max_attempts=2,
                    cancellable=True,
                    available_at_ms=now_ms,
                )
            result = repository.enqueue(connection, draft)
            assert result.job.input_ref_json == expected_json
            assert result.job.input_sha256 == input_hash

            users.commit_operation(
                connection,
                dedup_id=dedup_id,
                result={"job_id": result.job.id},
                http_status=202,
                completed_at_ms=now_ms,
            )
            replay = repository.enqueue(connection, draft)
            assert replay.inserted is False
            assert replay.job == result.job

            # Once the producer operation is committed, its exact durable row
            # remains replayable but a new job/effect identity cannot be born.
            with pytest.raises(JobStateConflictError):
                repository.enqueue(
                    connection,
                    replace(
                        _draft(connection, "dedup-other"),
                        dedup_id=dedup_id,
                        operation_id=operation_id,
                        actor_kind="user",
                        actor_user_id="actor",
                        input_ref=input_ref,
                        authorization_snapshot=snapshot,
                        authorization_scope_epoch=1,
                        effect_key="fixture:dedup-other",
                    ),
                )
    finally:
        connection.close()


@pytest.mark.parametrize("terminal_status", ("failed_final", "committed"))
def test_terminal_operation_without_a_job_cannot_enqueue_new_durable_work(
    isolated_knowledge_layout,
    terminal_status: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    repository = JobRepository()
    users = UserRepository()
    try:
        with transaction(connection):
            users.import_legacy(
                connection,
                LegacyUserRecord(
                    id="failed-actor",
                    display_name="Failed Actor",
                    password_hash="scrypt:p1.9:failed",
                    system_role="user",
                    account_status="active",
                    created_at_ms=1,
                    updated_at_ms=1,
                ),
            )
            input_ref = {"schema_version": 1, "value": terminal_status}
            input_hash = canonical_job_input_sha256(input_ref)
            now_ms = db_now_ms(connection)
            operation_id = _uuid(f"{terminal_status}-operation")
            dedup_id = _uuid(f"{terminal_status}-dedup")
            users.begin_operation(
                connection,
                actor_user_id="failed-actor",
                operation_type="job:fixture.job",
                operation_id=operation_id,
                request_sha256=input_hash,
                dedup_id=dedup_id,
                now_ms=now_ms,
            )
            if terminal_status == "failed_final":
                users.fail_operation_final(
                    connection,
                    dedup_id=dedup_id,
                    error={"code": "STATE_CONFLICT"},
                    http_status=409,
                    completed_at_ms=now_ms,
                )
            else:
                users.commit_operation(
                    connection,
                    dedup_id=dedup_id,
                    result={"accepted": True},
                    http_status=202,
                    completed_at_ms=now_ms,
                )
            snapshot = {
                "schema_version": 1,
                "actor_kind": "user",
                "actor_user_id": "failed-actor",
                "account_status": "active",
                "system_role": "user",
                "current_part_id": None,
                "scope_epoch": 1,
                "space_id": None,
                "capabilities": [],
            }
            with pytest.raises(JobStateConflictError):
                repository.enqueue(
                    connection,
                    JobEnqueueDraft(
                        job_id=_uuid(f"{terminal_status}-without-job"),
                        dedup_id=dedup_id,
                        operation_id=operation_id,
                        actor_kind="user",
                        actor_user_id="failed-actor",
                        space_id=None,
                        job_type="fixture.job",
                        handler_version="v1",
                        effect_key=f"fixture:{terminal_status}-without-job",
                        input_ref=input_ref,
                        expected_state={},
                        authorization_snapshot=snapshot,
                        authorization_scope_epoch=1,
                        authorization_part_id=None,
                        authorization_capabilities=(),
                        max_attempts=2,
                        cancellable=True,
                        available_at_ms=now_ms,
                    ),
                )
            assert (
                repository.get(connection, _uuid(f"{terminal_status}-without-job"))
                is None
            )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "bad_input",
    (
        {"float": 1.5},
        {"too_large": 1 << 53},
        {"surrogate": "\ud800"},
        {"bytes": b"not-json"},
    ),
)
def test_enqueue_rejects_values_outside_supported_ijson_subset(
    isolated_knowledge_layout,
    bad_input: dict[str, object],
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            with pytest.raises(ValueError):
                JobRepository().enqueue(connection, _draft(connection, "bad", input_ref=bad_input))
            assert connection.execute("SELECT count(*) FROM durable_job").fetchone()[0] == 0
    finally:
        connection.close()


def test_claim_start_renew_progress_effect_fence_and_success(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    repository = JobRepository()
    try:
        with transaction(connection):
            _enqueue(connection, repository, "success")
            now_ms = db_now_ms(connection)
            assert repository.claim_next(
                connection,
                handler_keys=(HandlerKey("unknown", "v1"),),
                claim_owner="worker-a",
                lease_ms=10_000,
                now_ms=now_ms,
            ) is None
            now_ms = db_now_ms(connection)
            claimed = _claim(connection, repository, now_ms=now_ms)
            assert claimed.job.state == "claimed"
            assert claimed.fence.attempt_count == 1
            running = repository.start(connection, claimed.fence, now_ms=now_ms)
            assert running.state == "running"
            renewed = repository.renew(
                connection,
                claimed.fence,
                lease_ms=20_000,
                now_ms=now_ms,
            )
            assert renewed.claim_expires_at_ms is not None
            assert renewed.claim_expires_at_ms >= now_ms + 20_000
            progressed = repository.set_progress(
                connection,
                claimed.fence,
                progress_permille=450,
                now_ms=now_ms,
            )
            assert progressed.progress_permille == 450
            assert repository.assert_effect_fence(
                connection, fence=claimed.fence, now_ms=now_ms
            ).state == "running"

            connection.execute(
                """INSERT INTO outbox_event(
                     logical_key,space_id,aggregate_type,aggregate_id,
                     aggregate_version,event_type,payload_json,
                     payload_schema_version,state,available_at,claim_owner,
                     claim_expires_at,attempt_count,last_error_json,created_at,
                     completed_at
                   ) VALUES ('fixture:domain-effect',NULL,'fixture','one',1,
                     'fixture.applied','{}',1,'pending',?,NULL,NULL,0,NULL,?,NULL)""",
                (now_ms, now_ms),
            )
            succeeded = repository.succeed(
                connection,
                claimed.fence,
                result={"effect": "fixture:domain-effect"},
                now_ms=now_ms,
            )
            assert succeeded.state == "succeeded"
            assert succeeded.result == {"effect": "fixture:domain-effect"}
            assert succeeded.progress_permille == 1000
            assert succeeded.claim_owner is None

        with transaction(connection):
            job = repository.require(connection, _uuid("job:success"))
            assert job.state == "succeeded"
            replayed = repository.succeed(
                connection,
                claimed.fence,
                result={"effect": "fixture:domain-effect"},
                now_ms=db_now_ms(connection),
            )
            assert replayed == job
            with pytest.raises(JobEffectConflictError):
                repository.succeed(
                    connection,
                    claimed.fence,
                    result={"effect": "different"},
                    now_ms=db_now_ms(connection),
                )
            assert connection.execute(
                "SELECT count(*) FROM outbox_event WHERE logical_key='fixture:domain-effect'"
            ).fetchone()[0] == 1
            assert repository.list_nonterminal_handler_keys(connection) == ()
    finally:
        connection.close()


def test_domain_effect_and_success_roll_back_together(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    repository = JobRepository()
    fence: ClaimFence
    try:
        with transaction(connection):
            _enqueue(connection, repository, "rollback")
            now_ms = db_now_ms(connection)
            claimed = _claim(connection, repository, now_ms=now_ms)
            fence = claimed.fence
            repository.start(connection, fence, now_ms=now_ms)

        with pytest.raises(RuntimeError, match="crash"):
            with transaction(connection):
                now_ms = db_now_ms(connection)
                repository.assert_effect_fence(connection, fence=fence, now_ms=now_ms)
                connection.execute(
                    """INSERT INTO outbox_event(
                         logical_key,space_id,aggregate_type,aggregate_id,
                         aggregate_version,event_type,payload_json,
                         payload_schema_version,state,available_at,claim_owner,
                         claim_expires_at,attempt_count,last_error_json,created_at,
                         completed_at
                       ) VALUES ('fixture:rolled-back',NULL,'fixture','two',1,
                         'fixture.applied','{}',1,'pending',?,NULL,NULL,0,NULL,?,NULL)""",
                    (now_ms, now_ms),
                )
                repository.succeed(
                    connection, fence, result={"ok": True}, now_ms=now_ms
                )
                raise RuntimeError("crash")

        with transaction(connection):
            assert repository.require(connection, fence.job_id).state == "running"
            assert connection.execute(
                "SELECT count(*) FROM outbox_event WHERE logical_key='fixture:rolled-back'"
            ).fetchone()[0] == 0
    finally:
        connection.close()


def test_claim_attempt_is_aba_fence_and_expiry_is_fail_closed(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    repository = JobRepository()
    try:
        with transaction(connection):
            _enqueue(connection, repository, "aba", max_attempts=3)
            now_ms = db_now_ms(connection)
            first = _claim(connection, repository, now_ms=now_ms, lease_ms=2)
            repository.start(connection, first.fence, now_ms=now_ms)
            retried = repository.retry_or_dead(
                connection,
                first.fence,
                error={"code": "TRANSIENT"},
                delay_ms=0,
                now_ms=now_ms,
            )
            assert retried.state == "retry"
            second = _claim(connection, repository, owner="worker-b", now_ms=now_ms)
            assert second.fence.attempt_count == 2
            with pytest.raises(JobClaimLostError):
                repository.start(connection, first.fence, now_ms=now_ms)
            repository.start(connection, second.fence, now_ms=now_ms)
            expiry = second.job.claim_expires_at_ms
            assert expiry is not None
            with pytest.raises(JobClaimLostError):
                repository.assert_effect_fence(
                    connection,
                    fence=second.fence,
                    now_ms=expiry,
                )
    finally:
        connection.close()


def test_retry_exhaustion_uses_schema_legal_retry_to_dead(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    repository = JobRepository()
    try:
        with transaction(connection):
            _enqueue(connection, repository, "dead", max_attempts=1)
            now_ms = db_now_ms(connection)
            claimed = _claim(connection, repository, now_ms=now_ms)
            repository.start(connection, claimed.fence, now_ms=now_ms)
            dead = repository.retry_or_dead(
                connection,
                claimed.fence,
                error={"code": "EXHAUSTED"},
                delay_ms=10,
                now_ms=now_ms,
            )
            assert dead.state == "dead"
            assert dead.error == {"code": "EXHAUSTED"}
            assert dead.completed_at_ms == now_ms
    finally:
        connection.close()


def test_expired_recovery_retries_or_dead_letters_by_attempt_count(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    repository = JobRepository()
    try:
        with transaction(connection):
            _enqueue(connection, repository, "recover-retry", max_attempts=2)
            _enqueue(connection, repository, "recover-dead", max_attempts=1)
            now_ms = db_now_ms(connection)
            first = _claim(connection, repository, now_ms=now_ms, lease_ms=1)
            second = _claim(connection, repository, now_ms=now_ms, lease_ms=1)
            repository.start(connection, second.fence, now_ms=now_ms)
            recovery = repository.recover_expired(
                connection,
                now_ms=now_ms + 1,
                error={"code": "CLAIM_EXPIRED"},
            )
            assert recovery.retried_ids == (first.fence.job_id,)
            assert recovery.dead_ids == (second.fence.job_id,)
            assert repository.require(connection, first.fence.job_id).state == "retry"
            assert repository.require(connection, second.fence.job_id).state == "dead"
    finally:
        connection.close()


def test_cancellation_is_immediate_before_claim_and_cooperative_after_claim(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    repository = JobRepository()
    try:
        with transaction(connection):
            queued, _ = _enqueue(connection, repository, "cancel-queued")
            now_ms = db_now_ms(connection)
            immediate = repository.request_cancel(
                connection, job_id=queued.job.id, now_ms=now_ms
            )
            assert immediate.changed is True and immediate.immediate is True
            assert immediate.job.state == "cancelled"

            preflagged, _ = _enqueue(
                connection, repository, "cancel-preflagged"
            )
            connection.execute(
                """UPDATE durable_job SET cancel_requested=1,updated_at=?
                     WHERE id=? AND state='queued' AND cancellable=1""",
                (now_ms, preflagged.job.id),
            )
            rescued = repository.request_cancel(
                connection, job_id=preflagged.job.id, now_ms=now_ms
            )
            assert rescued.changed is True and rescued.immediate is True
            assert rescued.job.state == "cancelled"

            _enqueue(connection, repository, "cancel-running")
            now_ms = db_now_ms(connection)
            claimed = _claim(connection, repository, now_ms=now_ms)
            repository.start(connection, claimed.fence, now_ms=now_ms)
            requested = repository.request_cancel(
                connection, job_id=claimed.fence.job_id, now_ms=now_ms
            )
            assert requested.changed is True and requested.immediate is False
            assert requested.job.state == "running"
            with pytest.raises(JobCancellationRequestedError):
                repository.assert_effect_fence(
                    connection, fence=claimed.fence, now_ms=now_ms
                )
            cancelled = repository.acknowledge_cancel(
                connection, claimed.fence, now_ms=now_ms
            )
            assert cancelled.state == "cancelled"
            assert cancelled.completed_at_ms == now_ms

            _enqueue(connection, repository, "not-cancellable", cancellable=False)
            with pytest.raises(JobCancellationUnsupportedError):
                repository.request_cancel(
                    connection,
                    job_id=_uuid("job:not-cancellable"),
                    now_ms=now_ms,
                )
    finally:
        connection.close()


def test_recovery_cancels_flagged_queued_and_expired_active_jobs(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    repository = JobRepository()
    try:
        with transaction(connection):
            queued, _ = _enqueue(connection, repository, "swept-queued")
            now_ms = db_now_ms(connection)
            connection.execute(
                """UPDATE durable_job SET cancel_requested=1,updated_at=?
                     WHERE id=? AND state='queued' AND cancellable=1""",
                (now_ms, queued.job.id),
            )

            _enqueue(connection, repository, "swept-active")
            claim_now = db_now_ms(connection)
            claimed = _claim(
                connection,
                repository,
                now_ms=claim_now,
                lease_ms=1,
            )
            repository.start(connection, claimed.fence, now_ms=claim_now)
            repository.request_cancel(
                connection,
                job_id=claimed.fence.job_id,
                now_ms=claim_now,
            )

            recovery = repository.recover_expired(
                connection,
                now_ms=claim_now + 1,
                error={"code": "CLAIM_EXPIRED"},
            )
            assert set(recovery.cancelled_ids) == {
                queued.job.id,
                claimed.fence.job_id,
            }
            assert recovery.retried_ids == ()
            assert recovery.dead_ids == ()
            assert repository.require(connection, queued.job.id).state == "cancelled"
            assert (
                repository.require(connection, claimed.fence.job_id).state
                == "cancelled"
            )
    finally:
        connection.close()


def test_integrity_scan_rejects_a_permanently_tampered_job(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    repository = JobRepository()
    trigger_sql = dict(TRIGGER_DEFINITIONS)["trg_durable_job_state_update"]
    try:
        with transaction(connection):
            _enqueue(connection, repository, "tampered")
    finally:
        connection.close()

    # A raw migration-only fixture bypasses the runtime authorizer.  Recreate
    # the canonical trigger before commit so only the row remains corrupted;
    # the physical schema itself stays canonical when the factory reopens it.
    raw = sqlite3.connect(factory.database_path, isolation_level=None)
    try:
        raw.execute("BEGIN IMMEDIATE")
        raw.execute("DROP TRIGGER trg_durable_job_state_update")
        raw.execute(
            "UPDATE durable_job SET input_sha256=? WHERE id=?",
            ("0" * 64, _uuid("job:tampered")),
        )
        raw.execute(trigger_sql)
        raw.execute("COMMIT")
    finally:
        raw.close()

    reopened = factory.open()
    try:
        with transaction(reopened):
            with pytest.raises(JobRepositoryError):
                repository.verify_integrity(reopened)
    finally:
        reopened.close()


def test_integrity_scan_rejects_noncanonical_terminal_result(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = JobRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            _enqueue(connection, repository, "noncanonical-result")
            now_ms = db_now_ms(connection)
            claimed = _claim(connection, repository, now_ms=now_ms)
            repository.start(connection, claimed.fence, now_ms=now_ms)
            repository.succeed(
                connection,
                claimed.fence,
                result={"value": 1},
                now_ms=now_ms,
            )
    finally:
        connection.close()

    trigger_sql = dict(TRIGGER_DEFINITIONS)["trg_durable_job_state_update"]
    raw = sqlite3.connect(factory.database_path, isolation_level=None)
    try:
        raw.execute("BEGIN IMMEDIATE")
        raw.execute("DROP TRIGGER trg_durable_job_state_update")
        raw.execute(
            "UPDATE durable_job SET result_json=? WHERE id=?",
            ('{"value": 1}', _uuid("job:noncanonical-result")),
        )
        raw.execute(trigger_sql)
        raw.execute("COMMIT")
    finally:
        raw.close()

    reopened = factory.open()
    try:
        with transaction(reopened):
            with pytest.raises(JobRepositoryError):
                repository.verify_integrity(reopened)
    finally:
        reopened.close()


def test_failure_and_illegal_progress_regression_are_stable_conflicts(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    repository = JobRepository()
    try:
        with transaction(connection):
            _enqueue(connection, repository, "failure")
            now_ms = db_now_ms(connection)
            claimed = _claim(connection, repository, now_ms=now_ms)
            repository.start(connection, claimed.fence, now_ms=now_ms)
            repository.set_progress(
                connection, claimed.fence, progress_permille=500, now_ms=now_ms
            )
            with pytest.raises(JobStateConflictError):
                repository.set_progress(
                    connection, claimed.fence, progress_permille=499, now_ms=now_ms
                )
            failed = repository.fail(
                connection,
                claimed.fence,
                error={"code": "FINAL"},
                now_ms=now_ms,
            )
            assert failed.state == "failed"
            assert failed.error == {"code": "FINAL"}
    finally:
        connection.close()

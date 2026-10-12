"""Minimal durable-work coordinator and immutable handler registry.

P1.9 deliberately implements no daemon, thread, route, or legacy producer
cutover.  The coordinator exposes one-job primitives that a later deployment
worker can drive.  Expensive preparation runs outside SQLite transactions;
the claim fence, current authorization, handler-specific expected state,
canonical domain effect, and terminal job result are then committed in one
caller-owned SQLite transaction.

Only trusted deployment code constructs :class:`HandlerRegistry`.  Job rows
contain durable references rather than Python closures, so a fresh process can
rebuild the same registry and safely reclaim expired work.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Literal, Protocol, TypeAlias

from .db import (
    ConnectionFactory,
    ManagedConnection,
    TransactionMode,
    db_now_ms,
    transaction,
)
from .errors import KnowledgeCoreError
from .repositories.jobs import (
    ClaimFence,
    ClaimResult,
    DurableJobRecord,
    EnqueueResult,
    HandlerKey,
    JobCancellationRequestedError,
    JobClaimLostError,
    JobEnqueueDraft,
    JobRepository,
    JobRepositoryError,
    JobStateConflictError,
    RecoveryResult,
)
from .scope import Capability, ScopeResolver, SessionPrincipal


ActorKind = Literal["user", "system"]
PreparedPayload: TypeAlias = object


class JobKernelError(KnowledgeCoreError):
    code = "JOB_KERNEL_ERROR"


class JobHandlerRegistryError(JobKernelError):
    code = "JOB_HANDLER_REGISTRY_INVALID"


class JobAuthorizationRejectedError(JobKernelError):
    code = "JOB_AUTHORIZATION_REJECTED"


class JobExpectedStateConflictError(JobKernelError):
    code = "JOB_EXPECTED_STATE_CONFLICT"


class RetryableJobExecutionError(JobKernelError):
    code = "JOB_EXECUTION_RETRYABLE"


class PermanentJobExecutionError(JobKernelError):
    code = "JOB_EXECUTION_FAILED"


@dataclass(frozen=True, slots=True)
class PreparedJobEffect:
    """Prepared external result and its stable canonical result envelope.

    ``payload`` is process-local and must never be persisted as the job input.
    ``result`` is persisted only after the registered commit callback applies
    its canonical effect in the same transaction.
    """

    result: Mapping[str, object]
    payload: PreparedPayload = None
    cleanup: Callable[[], None] | None = field(
        default=None, repr=False, compare=False
    )


class PrepareJob(Protocol):
    def __call__(self, job: DurableJobRecord) -> PreparedJobEffect: ...


class ValidateJobInput(Protocol):
    def __call__(
        self,
        input_ref: Mapping[str, object],
        expected_state: Mapping[str, object],
    ) -> None: ...


class RevalidateExpectedState(Protocol):
    def __call__(
        self, connection: ManagedConnection, job: DurableJobRecord
    ) -> None: ...


class CommitJobEffect(Protocol):
    def __call__(
        self,
        connection: ManagedConnection,
        job: DurableJobRecord,
        prepared: PreparedJobEffect,
    ) -> None: ...


class ConvergeTerminalJob(Protocol):
    def __call__(
        self, connection: ManagedConnection, job: DurableJobRecord
    ) -> None: ...


TerminalJobState = Literal["failed", "cancelled", "dead"]


class PrepareTerminalJob(Protocol):
    """Append handler-owned terminal evidence before a job state transition.

    The callback executes in the same caller-owned transaction as the terminal
    durable-job update.  Most handlers need no pre-transition evidence; the
    automatic semantic-edge handler uses it to satisfy the v10 immutable
    decision-ledger invariant for failure, cancellation, and retry exhaustion.
    """

    def __call__(
        self,
        connection: ManagedConnection,
        job: DurableJobRecord,
        terminal_state: TerminalJobState,
    ) -> None: ...


def _noop_validate(
    input_ref: Mapping[str, object], expected_state: Mapping[str, object]
) -> None:
    del input_ref, expected_state


def _noop_expected_state(
    connection: ManagedConnection, job: DurableJobRecord
) -> None:
    del connection, job


def _noop_terminal_job(
    connection: ManagedConnection, job: DurableJobRecord
) -> None:
    del connection, job


def _noop_reconcile_pending(connection: ManagedConnection) -> None:
    del connection


def _noop_prepare_terminal_job(
    connection: ManagedConnection,
    job: DurableJobRecord,
    terminal_state: TerminalJobState,
) -> None:
    del connection, job, terminal_state


@dataclass(frozen=True, slots=True)
class RegisteredJobHandler:
    """One exact, retained deployment handler version."""

    key: HandlerKey
    actor_kind: ActorKind
    accept_system_actor: bool
    required_capability: Capability | None
    validate_input: ValidateJobInput
    prepare: PrepareJob
    revalidate_expected_state: RevalidateExpectedState
    commit_effect: CommitJobEffect
    prepare_terminal: PrepareTerminalJob
    converge_terminal: ConvergeTerminalJob
    reconcile_pending: Callable[[ManagedConnection], None] = _noop_reconcile_pending

    @classmethod
    def build(
        cls,
        *,
        job_type: str,
        handler_version: str,
        actor_kind: ActorKind,
        prepare: PrepareJob,
        commit_effect: CommitJobEffect,
        required_capability: Capability | str | None = None,
        accept_system_actor: bool = False,
        validate_input: ValidateJobInput = _noop_validate,
        revalidate_expected_state: RevalidateExpectedState = _noop_expected_state,
        prepare_terminal: PrepareTerminalJob = _noop_prepare_terminal_job,
        converge_terminal: ConvergeTerminalJob = _noop_terminal_job,
        reconcile_pending: Callable[[ManagedConnection], None] = _noop_reconcile_pending,
    ) -> "RegisteredJobHandler":
        if actor_kind not in ("user", "system"):
            raise JobHandlerRegistryError("A handler actor kind is invalid.")
        if not isinstance(accept_system_actor, bool):
            raise JobHandlerRegistryError("accept_system_actor must be boolean.")
        if actor_kind == "system" and accept_system_actor:
            raise JobHandlerRegistryError(
                "A system handler already has exact system authority."
            )
        if not callable(prepare) or not callable(commit_effect):
            raise JobHandlerRegistryError("A handler requires prepare and commit callbacks.")
        if (
            not callable(validate_input)
            or not callable(revalidate_expected_state)
            or not callable(prepare_terminal)
            or not callable(converge_terminal)
            or not callable(reconcile_pending)
        ):
            raise JobHandlerRegistryError("A handler validator is not callable.")
        capability: Capability | None
        if required_capability is None:
            capability = None
        else:
            try:
                capability = Capability(required_capability)
            except (TypeError, ValueError) as exc:
                raise JobHandlerRegistryError(
                    "A handler required capability is invalid."
                ) from exc
        if actor_kind == "user" and capability is None:
            raise JobHandlerRegistryError(
                "A user handler requires one canonical capability."
            )
        if actor_kind == "system" and capability is not None:
            raise JobHandlerRegistryError(
                "A system handler cannot inherit a user capability."
            )
        try:
            key = HandlerKey(job_type=job_type, handler_version=handler_version)
        except (TypeError, ValueError) as exc:
            raise JobHandlerRegistryError("A handler key is invalid.") from exc
        return cls(
            key=key,
            actor_kind=actor_kind,
            accept_system_actor=accept_system_actor,
            required_capability=capability,
            validate_input=validate_input,
            prepare=prepare,
            revalidate_expected_state=revalidate_expected_state,
            commit_effect=commit_effect,
            prepare_terminal=prepare_terminal,
            converge_terminal=converge_terminal,
            reconcile_pending=reconcile_pending,
        )

    def accepts_actor(self, actor_kind: ActorKind) -> bool:
        return self.actor_kind == actor_kind or (
            actor_kind == "system" and self.accept_system_actor
        )


class HandlerRegistry:
    """Immutable exact-version registry recreated by every worker process."""

    __slots__ = ("__handlers", "__keys")

    def __init__(self, handlers: Iterable[RegisteredJobHandler]) -> None:
        values = tuple(handlers)
        mapping: dict[HandlerKey, RegisteredJobHandler] = {}
        for handler in values:
            if not isinstance(handler, RegisteredJobHandler):
                raise JobHandlerRegistryError(
                    "The handler registry accepts registered handlers only."
                )
            if handler.key in mapping:
                raise JobHandlerRegistryError(
                    "The handler registry contains a duplicate exact version."
                )
            mapping[handler.key] = handler
        if not mapping:
            raise JobHandlerRegistryError(
                "The durable worker requires a non-empty handler registry."
            )
        ordered = tuple(
            sorted(mapping, key=lambda key: (key.job_type, key.handler_version))
        )
        object.__setattr__(self, "_HandlerRegistry__handlers", MappingProxyType(mapping))
        object.__setattr__(self, "_HandlerRegistry__keys", ordered)

    @property
    def keys(self) -> tuple[HandlerKey, ...]:
        return self.__keys

    def get(self, key: HandlerKey) -> RegisteredJobHandler | None:
        return self.__handlers.get(key)

    def require(self, key: HandlerKey) -> RegisteredJobHandler:
        value = self.get(key)
        if value is None:
            raise JobHandlerRegistryError(
                "The durable job references an unregistered handler version."
            )
        return value

    def require_job(self, job: DurableJobRecord) -> RegisteredJobHandler:
        handler = self.require(
            HandlerKey(job_type=job.job_type, handler_version=job.handler_version)
        )
        if not handler.accepts_actor(job.actor_kind):
            raise JobHandlerRegistryError(
                "The durable job actor kind differs from its registered handler."
            )
        return handler

    def assert_covers(self, keys: Iterable[HandlerKey]) -> None:
        missing = tuple(
            sorted(
                {key for key in keys if key not in self.__handlers},
                key=lambda key: (key.job_type, key.handler_version),
            )
        )
        if missing:
            raise JobHandlerRegistryError(
                "The deployment registry does not cover every non-terminal job."
            )


@dataclass(frozen=True, slots=True)
class JobRunResult:
    job: DurableJobRecord
    prepared: bool
    replayed: bool


def _stable_error(error: BaseException, *, retryable: bool) -> dict[str, object]:
    if isinstance(error, JobKernelError):
        code = error.code
    elif isinstance(error, JobRepositoryError):
        code = error.code
    else:
        code = "JOB_EXECUTION_ERROR"
    return {
        "code": code,
        "message": "The durable job could not commit its canonical effect.",
        "retryable": retryable,
        "schema_version": 1,
    }


class DurableWorkKernel:
    """One-job orchestration with no implicit loop, thread, or service start."""

    __slots__ = ("_factory", "_failpoint", "_jobs", "_registry", "_scope")

    def __init__(
        self,
        factory: ConnectionFactory,
        registry: HandlerRegistry,
        *,
        scope_resolver: ScopeResolver | None = None,
        jobs: JobRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("DurableWorkKernel requires a ConnectionFactory.")
        if not isinstance(registry, HandlerRegistry):
            raise TypeError("DurableWorkKernel requires a HandlerRegistry.")
        if scope_resolver is not None and not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("scope_resolver must be a ScopeResolver or None.")
        if failpoint is not None and not callable(failpoint):
            raise TypeError("failpoint must be callable or None.")
        self._factory = factory
        self._registry = registry
        self._scope = scope_resolver
        self._failpoint = failpoint
        self._jobs = jobs if jobs is not None else JobRepository()
        if not isinstance(self._jobs, JobRepository):
            raise TypeError("jobs must be a JobRepository.")

    @property
    def registry(self) -> HandlerRegistry:
        return self._registry

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    @property
    def database_path(self):
        """Absolute canonical database path used to scope P7 ownership."""

        return self._factory.database_path

    def enqueue_in_transaction(
        self,
        connection: ManagedConnection,
        draft: JobEnqueueDraft,
    ) -> EnqueueResult:
        """Validate and enqueue inside the producer's existing transaction.

        The producer owns operation-dedup, audit, outbox, and its surrounding
        transaction.  This method never commits independently.
        """

        if not isinstance(draft, JobEnqueueDraft):
            raise TypeError("enqueue requires a JobEnqueueDraft.")
        handler = self._registry.require(
            HandlerKey(draft.job_type, draft.handler_version)
        )
        if not handler.accepts_actor(draft.actor_kind):
            raise JobAuthorizationRejectedError(
                "The handler does not accept this actor kind."
            )
        handler.validate_input(draft.input_ref, draft.expected_state)
        normalized = self._bind_authorization(connection, draft, handler)
        result = self._jobs.enqueue(connection, normalized)
        self._hit("job.after_enqueue")
        return result

    def verify_registry_coverage(self) -> None:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._jobs.verify_integrity(connection)
                self._assert_registry_covers_jobs(connection)
        finally:
            connection.close()

    def recover_and_claim(
        self,
        *,
        claim_owner: str,
        lease_ms: int,
        avoid_actor: tuple[ActorKind, str | None] | None = None,
    ) -> tuple[RecoveryResult, ClaimResult | None]:
        """Recover expired work and atomically claim at most one eligible row."""

        connection = self._factory.open()
        try:
            with transaction(connection):
                self._jobs.verify_integrity(connection)
                self._assert_registry_covers_jobs(connection)
                self._reconcile_pending(connection)
                now_ms = db_now_ms(connection)
                self._prepare_recovery_terminals(connection, now_ms=now_ms)
                recovery = self._jobs.recover_expired(
                    connection,
                    now_ms=now_ms,
                    error={
                        "code": "JOB_CLAIM_EXPIRED",
                        "message": "The prior worker claim expired.",
                        "retryable": True,
                        "schema_version": 1,
                    },
                )
                for terminal_id in (
                    *recovery.cancelled_ids,
                    *recovery.dead_ids,
                ):
                    terminal = self._jobs.require(connection, terminal_id)
                    self._registry.require_job(terminal).converge_terminal(
                        connection, terminal
                    )
                claim = self._jobs.claim_next(
                    connection,
                    handler_keys=self._registry.keys,
                    claim_owner=claim_owner,
                    lease_ms=lease_ms,
                    now_ms=now_ms,
                    avoid_actor=avoid_actor,
                )
            if claim is not None:
                self._hit("job.after_claim")
            return recovery, claim
        finally:
            connection.close()

    def recover_expired(self) -> RecoveryResult:
        """Recover every expired/cancelled row without claiming new work."""

        connection = self._factory.open()
        try:
            with transaction(connection):
                self._jobs.verify_integrity(connection)
                self._assert_registry_covers_jobs(connection)
                self._reconcile_pending(connection)
                now_ms = db_now_ms(connection)
                self._prepare_recovery_terminals(connection, now_ms=now_ms)
                recovery = self._jobs.recover_expired(
                    connection,
                    now_ms=now_ms,
                    error={
                        "code": "JOB_CLAIM_EXPIRED",
                        "message": "The prior worker claim expired.",
                        "retryable": True,
                        "schema_version": 1,
                    },
                )
                for terminal_id in (*recovery.cancelled_ids, *recovery.dead_ids):
                    terminal = self._jobs.require(connection, terminal_id)
                    self._registry.require_job(terminal).converge_terminal(
                        connection, terminal
                    )
                return recovery
        finally:
            connection.close()

    def claim_next(
        self,
        *,
        claim_owner: str,
        lease_ms: int,
        handler_keys: Iterable[HandlerKey] | None = None,
        avoid_actor: tuple[ActorKind, str | None] | None = None,
        durable_fairness: bool = False,
    ) -> ClaimResult | None:
        """Claim at most one row from a reviewed handler subset.

        P7 uses this primitive to rotate exact handler versions and actor lanes;
        the durable database row remains the only work authority.
        """

        keys = tuple(handler_keys) if handler_keys is not None else self._registry.keys
        for key in keys:
            self._registry.require(key)
        connection = self._factory.open()
        try:
            with transaction(connection):
                self._jobs.verify_integrity(connection)
                self._assert_registry_covers_jobs(connection)
                self._reconcile_pending(connection)
                claim = self._jobs.claim_next(
                    connection,
                    handler_keys=keys,
                    claim_owner=claim_owner,
                    lease_ms=lease_ms,
                    now_ms=db_now_ms(connection),
                    avoid_actor=avoid_actor,
                    durable_fairness=durable_fairness,
                )
            if claim is not None:
                self._hit("job.after_claim")
            return claim
        finally:
            connection.close()

    def _reconcile_pending(self, connection: ManagedConnection) -> None:
        """Opt-in bounded domain cleanup after cancellation by other services.

        Each exact handler version owns its metadata-only work bound. Existing
        handlers default to no work; no external I/O is allowed in this hook.
        """
        for key in self._registry.keys:
            self._registry.require(key).reconcile_pending(connection)

    def _assert_registry_covers_jobs(
        self, connection: ManagedConnection
    ) -> None:
        for key, actor_kind in self._jobs.list_nonterminal_handler_bindings(
            connection
        ):
            handler = self._registry.require(key)
            if not handler.accepts_actor(actor_kind):
                raise JobHandlerRegistryError(
                    "A non-terminal job actor kind differs from its registered handler."
                )

    def renew_claim(self, fence: ClaimFence, *, lease_ms: int) -> DurableJobRecord:
        connection = self._factory.open()
        try:
            with transaction(connection):
                return self._jobs.renew(
                    connection,
                    fence=fence,
                    lease_ms=lease_ms,
                    now_ms=db_now_ms(connection),
                )
        finally:
            connection.close()

    def request_cancel(self, job_id: str):
        connection = self._factory.open()
        try:
            with transaction(connection):
                current = self._jobs.require(connection, job_id)
                if current.state in {"queued", "retry"} and current.cancellable:
                    self._registry.require_job(current).prepare_terminal(
                        connection, current, "cancelled"
                    )
                return self._jobs.request_cancel(
                    connection, job_id=job_id, now_ms=db_now_ms(connection)
                )
        finally:
            connection.close()

    def revalidate_job_authority(
        self, connection: ManagedConnection, job_id: str,
    ) -> DurableJobRecord:
        """Read current authority for a retained server-owned job identity.

        A successor producer may use a completed parent's authority, but must
        separately prove its exact source/result binding. This grants neither
        a claim nor a domain effect, and never trusts a caller-provided record.
        """
        current = self._jobs.require(connection, job_id)
        handler = self._registry.require_job(current)
        self._revalidate_authorization(connection, current, handler)
        return current

    def preflight_preparation(self, fence: ClaimFence) -> DurableJobRecord:
        """Reject revoked/cancelled/stale work immediately before external I/O.

        No transaction survives this method. Authority can still change after
        it returns; commit_prepared must retain its independent writer fence.
        Handlers opt in because their pre-effect state can differ from their
        initial state. This is not a new claim or a lease extension.
        """
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                current = self._jobs.assert_effect_fence(
                    connection, fence=fence, now_ms=db_now_ms(connection))
                self.revalidate_job_authority(connection, current.id)
                self._registry.require_job(current).revalidate_expected_state(connection, current)
                return current
        finally:
            connection.close()

    def run_claim(self, claim: ClaimResult) -> JobRunResult:
        """Run one short handler invocation; later P7 owns loops and heartbeats."""

        if not isinstance(claim, ClaimResult):
            raise TypeError("run_claim requires a ClaimResult.")
        handler = self._registry.require_job(claim.job)
        try:
            started = self._start_claim(claim.fence)
        except JobCancellationRequestedError:
            # A role/user cancellation can arrive after claim but before the
            # first body read. Acknowledge that exact live attempt just as for
            # cancellation during preparation, without invoking the provider.
            return JobRunResult(job=self._acknowledge_cancel(claim.fence), prepared=False, replayed=False)
        try:
            prepared = handler.prepare(started)
            if not isinstance(prepared, PreparedJobEffect):
                raise PermanentJobExecutionError(
                    "The handler returned an invalid prepared effect."
                )
        except RetryableJobExecutionError as error:
            job = self._record_retry(claim.fence, error)
            return JobRunResult(job=job, prepared=False, replayed=False)
        except Exception as error:
            job = self._record_failure(claim.fence, error)
            return JobRunResult(job=job, prepared=False, replayed=False)
        return self.commit_prepared(claim.fence, prepared)

    def commit_prepared(
        self,
        fence: ClaimFence,
        prepared: PreparedJobEffect,
    ) -> JobRunResult:
        """Revalidate and commit the canonical effect plus success atomically."""

        if not isinstance(fence, ClaimFence):
            raise TypeError("commit_prepared requires a ClaimFence.")
        if not isinstance(prepared, PreparedJobEffect):
            raise TypeError("commit_prepared requires a PreparedJobEffect.")
        connection = self._factory.open()
        result: JobRunResult | None = None
        try:
            try:
                with transaction(connection):
                    current = self._jobs.require(connection, fence.job_id)
                    if current.state == "succeeded":
                        replayed = self._jobs.succeed(
                            connection,
                            fence=fence,
                            result=prepared.result,
                            now_ms=db_now_ms(connection),
                        )
                        result = JobRunResult(
                            job=replayed, prepared=True, replayed=True
                        )
                    else:
                        now_ms = db_now_ms(connection)
                        current = self._jobs.assert_effect_fence(
                            connection, fence=fence, now_ms=now_ms
                        )
                        handler = self._registry.require_job(current)
                        self._revalidate_authorization(connection, current, handler)
                        handler.revalidate_expected_state(connection, current)
                        handler.commit_effect(connection, current, prepared)
                        commit_now_ms = db_now_ms(connection)
                        succeeded = self._jobs.succeed(
                            connection,
                            fence=fence,
                            result=prepared.result,
                            now_ms=commit_now_ms,
                        )
                        result = JobRunResult(
                            job=succeeded, prepared=True, replayed=False
                        )
                if result is None:
                    raise JobKernelError(
                        "The durable job commit produced no result."
                    )
                self._hit("job.after_domain_commit_before_ack")
                self._hit("job.after_ack")
                return result
            except (
                JobAuthorizationRejectedError,
                JobExpectedStateConflictError,
                PermanentJobExecutionError,
            ) as error:
                # The effect transaction has exited and rolled back before the
                # durable rejection is recorded in a fresh transaction.
                return JobRunResult(
                    job=self._record_failure(fence, error),
                    prepared=True,
                    replayed=False,
                )
            except JobCancellationRequestedError:
                return JobRunResult(
                    job=self._acknowledge_cancel(fence),
                    prepared=True,
                    replayed=False,
                )
            except JobClaimLostError:
                raise
            except RetryableJobExecutionError as error:
                return JobRunResult(
                    job=self._record_retry(fence, error),
                    prepared=True,
                    replayed=False,
                )
        finally:
            connection.close()
            if prepared.cleanup is not None:
                prepared.cleanup()

    def _acknowledge_cancel(self, fence: ClaimFence) -> DurableJobRecord:
        """Converge a live cooperative cancellation to a terminal row.

        If the lease expired between detecting the request and this transaction,
        the repository raises ``JobClaimLostError``.  Recovery then owns the
        expired row and must converge its cancellation without reviving work.
        """

        connection = self._factory.open()
        try:
            with transaction(connection):
                current = self._jobs.require(connection, fence.job_id)
                self._registry.require_job(current).prepare_terminal(
                    connection, current, "cancelled"
                )
                terminal = self._jobs.acknowledge_cancel(
                    connection, fence=fence, now_ms=db_now_ms(connection)
                )
                self._registry.require_job(terminal).converge_terminal(
                    connection, terminal
                )
                return terminal
        finally:
            connection.close()

    def _start_claim(self, fence: ClaimFence) -> DurableJobRecord:
        connection = self._factory.open()
        try:
            with transaction(connection):
                return self._jobs.start(
                    connection, fence=fence, now_ms=db_now_ms(connection)
                )
        finally:
            connection.close()

    def _record_retry(
        self, fence: ClaimFence, error: BaseException
    ) -> DurableJobRecord:
        connection = self._factory.open()
        try:
            with transaction(connection):
                now_ms = db_now_ms(connection)
                current = self._jobs.require(connection, fence.job_id)
                will_be_dead = (
                    not current.cancel_requested
                    and current.attempt_count == current.max_attempts
                )
                if current.cancel_requested or will_be_dead:
                    self._registry.require_job(current).prepare_terminal(
                        connection,
                        current,
                        "cancelled" if current.cancel_requested else "dead",
                    )
                try:
                    result = self._jobs.retry_or_dead(
                        connection,
                        fence=fence,
                        error=_stable_error(error, retryable=True),
                        delay_ms=0,
                        now_ms=now_ms,
                    )
                except JobCancellationRequestedError:
                    result = self._jobs.acknowledge_cancel(
                        connection, fence=fence, now_ms=now_ms
                    )
                if result.state in {"cancelled", "dead"}:
                    self._registry.require_job(result).converge_terminal(
                        connection, result
                    )
                return result
        finally:
            connection.close()

    def _record_failure(
        self, fence: ClaimFence, error: BaseException
    ) -> DurableJobRecord:
        connection = self._factory.open()
        try:
            with transaction(connection):
                now_ms = db_now_ms(connection)
                current = self._jobs.require(connection, fence.job_id)
                self._registry.require_job(current).prepare_terminal(
                    connection,
                    current,
                    "cancelled" if current.cancel_requested else "failed",
                )
                try:
                    result = self._jobs.fail(
                        connection,
                        fence=fence,
                        error=_stable_error(error, retryable=False),
                        now_ms=now_ms,
                    )
                except JobCancellationRequestedError:
                    result = self._jobs.acknowledge_cancel(
                        connection, fence=fence, now_ms=now_ms
                    )
                self._registry.require_job(result).converge_terminal(
                    connection, result
                )
                return result
        finally:
            connection.close()

    def _prepare_recovery_terminals(
        self, connection: ManagedConnection, *, now_ms: int
    ) -> None:
        """Prepare exact handler evidence for rows recovery will terminalize.

        The selection mirrors :meth:`JobRepository.recover_expired` and runs
        immediately before it under the same write transaction.  A later CAS
        failure therefore rolls the evidence back with the state transition.
        """

        rows = connection.execute(
            """SELECT id,'cancelled' AS terminal_state
                   FROM durable_job
                  WHERE state IN ('queued','retry') AND cancellable=1
                    AND cancel_requested=1
                  UNION ALL
                 SELECT id,
                        CASE WHEN cancel_requested=1
                             THEN 'cancelled' ELSE 'dead' END AS terminal_state
                   FROM durable_job
                  WHERE state IN ('claimed','running') AND claim_expires_at<=?
                    AND (cancel_requested=1 OR attempt_count=max_attempts)
                  ORDER BY id""",
            (now_ms,),
        ).fetchall()
        for row in rows:
            job = self._jobs.require(connection, str(row["id"]))
            terminal_state = str(row["terminal_state"])
            if terminal_state not in {"cancelled", "dead"}:
                raise JobKernelError(
                    "Recovery selected an invalid terminal job state."
                )
            self._registry.require_job(job).prepare_terminal(
                connection,
                job,
                terminal_state,  # type: ignore[arg-type]
            )

    def _bind_authorization(
        self,
        connection: ManagedConnection,
        draft: JobEnqueueDraft,
        handler: RegisteredJobHandler,
    ) -> JobEnqueueDraft:
        if draft.actor_kind == "system":
            return replace(
                draft,
                actor_user_id=None,
                authorization_scope_epoch=None,
                authorization_part_id=None,
                authorization_capabilities=(),
                authorization_snapshot={
                    "actor_kind": "system",
                    "schema_version": 1,
                },
            )
        if self._scope is None or handler.required_capability is None:
            raise JobAuthorizationRejectedError(
                "User-job enqueue requires a canonical scope resolver."
            )
        if draft.actor_user_id is None or draft.authorization_scope_epoch is None:
            raise JobAuthorizationRejectedError(
                "A user job requires an actor and captured scope epoch."
            )
        if draft.space_id is None:
            raise JobAuthorizationRejectedError(
                "A user job requires one concrete knowledge space."
            )
        try:
            resolved = self._scope.resolve_in_transaction(
                connection,
                SessionPrincipal(
                    user_id=draft.actor_user_id,
                    captured_scope_epoch=draft.authorization_scope_epoch,
                ),
                draft.space_id,
                handler.required_capability,
            )
        except KnowledgeCoreError as exc:
            raise JobAuthorizationRejectedError(
                "The actor is not authorized to enqueue this job."
            ) from exc
        capabilities = tuple(
            sorted(capability.value for capability in resolved.effective_capabilities)
        )
        snapshot = {
            "account_status": "active",
            "actor_kind": "user",
            "actor_user_id": resolved.actor_user_id,
            "capabilities": list(capabilities),
            "current_part_id": resolved.current_part_id,
            "schema_version": 1,
            "scope_epoch": resolved.scope_epoch,
            "space_id": resolved.space.id,
            "system_role": resolved.system_role,
        }
        plan = resolved.access_plan
        expected_state = dict(draft.expected_state)
        if plan is not None:
            expected_state["_authorization_fence"] = {
                "corpus_closure_generation": plan.corpus_closure.generation,
                "corpus_closure_sha256": plan.corpus_closure.sha256,
                "organization_snapshot_version": (
                    str(plan.corpus_closure.organization_snapshot_version)
                ),
                "native_mutation_space_id": plan.native_mutation_space_id,
                "role_assignment_generation": plan.role_assignment_generation,
                "role_assignment_id": plan.role_assignment_id,
            }
        return replace(
            draft,
            authorization_part_id=resolved.current_part_id,
            authorization_capabilities=capabilities,
            authorization_snapshot=snapshot,
            expected_state=expected_state,
        )

    def _revalidate_authorization(
        self,
        connection: ManagedConnection,
        job: DurableJobRecord,
        handler: RegisteredJobHandler,
    ) -> None:
        if job.actor_kind == "system":
            if not handler.accepts_actor("system"):
                raise JobAuthorizationRejectedError(
                    "The current handler actor contract changed."
                )
            return
        if (
            handler.actor_kind != "user"
            or handler.required_capability is None
            or self._scope is None
            or job.actor_user_id is None
            or job.authorization_scope_epoch is None
            or job.space_id is None
        ):
            raise JobAuthorizationRejectedError(
                "The user job has no enforceable authorization contract."
            )
        if handler.required_capability.value not in job.authorization_capabilities:
            raise JobAuthorizationRejectedError(
                "The captured snapshot lacks the handler capability."
            )
        try:
            resolved = self._scope.resolve_in_transaction(
                connection,
                SessionPrincipal(
                    user_id=job.actor_user_id,
                    captured_scope_epoch=job.authorization_scope_epoch,
                ),
                job.space_id,
                handler.required_capability,
            )
        except KnowledgeCoreError as exc:
            raise JobAuthorizationRejectedError(
                "Current canonical authorization rejects the job effect."
            ) from exc
        if resolved.current_part_id != job.authorization_part_id:
            raise JobAuthorizationRejectedError(
                "The current part no longer matches the captured authorization."
            )
        plan = resolved.access_plan
        if plan is not None:
            fence = job.expected_state.get("_authorization_fence")
            # Successor enqueues always carry the closure fence.  Pre-SBE
            # lower-scope jobs may omit it, so require absence compatibility
            # only while higher-space rollout is disabled; never ignore a
            # fence that is actually present.
            if fence is None and not self._scope.flags.higher_spaces:
                return
            if not isinstance(fence, Mapping):
                raise JobAuthorizationRejectedError(
                    "The captured closure or higher-role episode is stale."
                )
            fence_mode = fence.get("fence_mode", "full_closure")
            if fence_mode not in {"full_closure", "mutation_authority"}:
                raise JobAuthorizationRejectedError(
                    "The captured authorization fence mode is invalid."
                )
            if not self._scope.flags.higher_spaces:
                captured_org_version = fence.get(
                    "organization_snapshot_version"
                )
                if captured_org_version is None:
                    return
                if (
                    captured_org_version
                    != str(plan.corpus_closure.organization_snapshot_version)
                ):
                    raise JobAuthorizationRejectedError(
                        "The captured organization closure is stale."
                    )
                return
            expected = {
                "corpus_closure_generation": plan.corpus_closure.generation,
                "corpus_closure_sha256": plan.corpus_closure.sha256,
                "organization_snapshot_version": (
                    str(plan.corpus_closure.organization_snapshot_version)
                ),
                "native_mutation_space_id": plan.native_mutation_space_id,
                "role_assignment_generation": plan.role_assignment_generation,
                "role_assignment_id": plan.role_assignment_id,
            }
            if fence_mode == "mutation_authority":
                # The item-edit transaction enqueues its curation job before
                # atomically advancing the revision. That intentional content
                # mutation changes the corpus closure itself. The job's exact
                # item/revision expected_state fences content; this mode keeps
                # the independent organization and native-role authority fence
                # without making the job reject its own committed edit.
                expected = {
                    key: value
                    for key, value in expected.items()
                    if key
                    in {
                        "organization_snapshot_version",
                        "native_mutation_space_id",
                        "role_assignment_generation",
                        "role_assignment_id",
                    }
                }
            if any(
                fence.get(key) != value for key, value in expected.items()
            ):
                raise JobAuthorizationRejectedError(
                    "The captured closure or higher-role episode is stale."
                )


__all__ = [
    "CommitJobEffect",
    "ConvergeTerminalJob",
    "DurableWorkKernel",
    "HandlerRegistry",
    "JobAuthorizationRejectedError",
    "JobExpectedStateConflictError",
    "JobHandlerRegistryError",
    "JobKernelError",
    "JobRunResult",
    "PermanentJobExecutionError",
    "PrepareTerminalJob",
    "PreparedJobEffect",
    "RegisteredJobHandler",
    "RetryableJobExecutionError",
    "TerminalJobState",
]

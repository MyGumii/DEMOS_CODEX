"""Durable leased outbox worker for file-projection materialization.

The worker is explicit: import and construction start no thread or service.
Each claimed event either proves that its space's complete immutable generation
and manifest match current canonical state before acknowledgement, or returns
to the durable retry/dead state machine.  Events without a space are deliberate
projection no-ops and are still acknowledged so the shared outbox has a bounded
lag metric.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import re
import sqlite3
import threading
from typing import Protocol

from ..db import (
    ConnectionFactory,
    ManagedConnection,
    TransactionMode,
    db_now_ms,
    transaction,
)
from ..errors import KnowledgeCoreError
from ..migrations.storage_preflight import MigrationStorageLayout
from ..repositories.outbox import (
    OutboxClaimResult,
    OutboxEventRecord,
    OutboxRecoveryResult,
    OutboxRepository,
)
from .materializer import (
    FullProjectionMaterializer,
    ProjectionCanonicalDriftError,
    ProjectionMaterializationError,
    ProjectionSpaceResult,
)
from .temporal import TemporalProjectionMaterializer
from .graph import AggregateGraphProjectionMaterializer


_CLAIM_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$", re.ASCII)
_RECOVERY_CONTENTION_EXTRA_RETRIES = 3
_RECOVERY_CONTENTION_INITIAL_DELAY_MS = 100
_RECOVERY_CONTENTION_MAX_DELAY_MS = 5_000


def _is_recovery_sqlite_contention(error: BaseException) -> bool:
    """Accept only direct SQLite lock contention with successful rollback."""
    if not isinstance(error, sqlite3.OperationalError):
        return False
    # transaction() preserves the original error and adds a note if rollback
    # also fails. Do not turn an uncertain cleanup into an ordinary retry.
    if getattr(error, "__notes__", ()):
        return False
    code = getattr(error, "sqlite_errorcode", None)
    return (
        isinstance(code, int)
        and not isinstance(code, bool)
        and (code & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    )


class ProjectionWorkerError(KnowledgeCoreError):
    code = "KNOWLEDGE_CORE_PROJECTION_WORKER_FAILED"


class ProjectionRecoveryContentionError(ProjectionWorkerError):
    """A recovery-only contention failure after rollback and physical close."""

    code = "KNOWLEDGE_CORE_PROJECTION_RECOVERY_CONTENTION"

    def __init__(self, original: sqlite3.OperationalError) -> None:
        if not _is_recovery_sqlite_contention(original):
            raise TypeError("Recovery contention requires a direct safe SQLite error.")
        super().__init__("The pre-claim projection recovery is temporarily busy.")
        self.original = original


class ProjectionHeartbeatError(ProjectionWorkerError):
    code = "KNOWLEDGE_CORE_PROJECTION_HEARTBEAT_FAILED"


class StopSignal(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


@dataclass(frozen=True, slots=True)
class ProjectionWorkerConfig:
    claim_owner: str
    lease_ms: int = 300_000
    heartbeat_interval_ms: int = 30_000
    idle_poll_ms: int = 100
    maximum_idle_poll_ms: int = 5_000
    maximum_attempts: int = 5
    retry_delay_ms: int = 250
    footer_v2: bool = False
    aggregate_graphs: bool = False
    settle_space_batch_size: int = 1

    def __post_init__(self) -> None:
        if (
            not isinstance(self.claim_owner, str)
            or _CLAIM_OWNER_RE.fullmatch(self.claim_owner) is None
        ):
            raise ValueError("claim_owner has an invalid projection-worker shape.")
        for name in (
            "lease_ms",
            "heartbeat_interval_ms",
            "idle_poll_ms",
            "maximum_idle_poll_ms",
            "maximum_attempts",
            "settle_space_batch_size",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer.")
        if (
            isinstance(self.retry_delay_ms, bool)
            or not isinstance(self.retry_delay_ms, int)
            or self.retry_delay_ms < 0
        ):
            raise ValueError("retry_delay_ms must be a nonnegative integer.")
        if self.lease_ms < 300:
            raise ValueError("lease_ms must be at least 300 milliseconds.")
        if self.heartbeat_interval_ms * 3 >= self.lease_ms:
            raise ValueError(
                "heartbeat_interval_ms must be less than one third of lease_ms."
            )
        if self.maximum_idle_poll_ms < self.idle_poll_ms:
            raise ValueError(
                "maximum_idle_poll_ms cannot be lower than idle_poll_ms."
            )
        if not isinstance(self.footer_v2, bool):
            raise ValueError("footer_v2 must be Boolean.")
        if not isinstance(self.aggregate_graphs, bool):
            raise ValueError("aggregate_graphs must be Boolean.")


@dataclass(frozen=True, slots=True)
class ProjectionIterationResult:
    recovery: OutboxRecoveryResult
    claim: OutboxClaimResult | None
    event: OutboxEventRecord | None
    projection: ProjectionSpaceResult | None
    heartbeat_count: int

    @property
    def idle(self) -> bool:
        return self.claim is None


@dataclass(frozen=True, slots=True)
class ProjectionServiceResult:
    iterations: int
    claimed: int
    idle_polls: int
    applied: int
    retried: int
    dead: int
    recovered_retry: int
    recovered_dead: int
    heartbeat_count: int


def _safe_error(exc: BaseException) -> dict[str, object]:
    if isinstance(exc, ProjectionCanonicalDriftError):
        reason = "canonical_drift"
    elif isinstance(exc, ProjectionMaterializationError):
        reason = "materialization_failed"
    elif isinstance(exc, ProjectionHeartbeatError):
        reason = "heartbeat_failed"
    else:
        reason = "worker_failed"
    return {"code": "PROJECTION_RETRY", "reason": reason, "schema_version": 1}


def _error_json(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class _HeartbeatKeeper:
    def __init__(
        self,
        worker: "OutboxProjectionWorker",
        claim: OutboxClaimResult,
    ) -> None:
        self._worker = worker
        self._claim = claim
        self._stop = threading.Event()
        self._count = 0
        self._error: Exception | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"projection-heartbeat-{claim.event.id}",
            daemon=False,
        )

    @property
    def count(self) -> int:
        return self._count

    @property
    def error(self) -> Exception | None:
        return self._error

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self._worker.config.lease_ms / 1000))
        if self._thread.is_alive():
            raise ProjectionHeartbeatError(
                "The projection heartbeat did not stop within its lease bound."
            )

    def _run(self) -> None:
        interval = self._worker.config.heartbeat_interval_ms / 1000
        while not self._stop.wait(interval):
            try:
                self._worker._renew(self._claim)
                self._count += 1
            except Exception as exc:
                self._error = exc
                return


class OutboxProjectionWorker:
    """Claim one durable event at a time and settle complete space generations."""

    __slots__ = (
        "_config",
        "_factory",
        "_failpoint",
        "_materializer",
        "_aggregate_graph_materializer",
        "_temporal_materializer",
        "_repository",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        layout: MigrationStorageLayout,
        config: ProjectionWorkerConfig,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("factory must be a ConnectionFactory.")
        if not isinstance(layout, MigrationStorageLayout):
            raise TypeError("layout must be a MigrationStorageLayout.")
        if not isinstance(config, ProjectionWorkerConfig):
            raise TypeError("config must be ProjectionWorkerConfig.")
        if failpoint is not None and not callable(failpoint):
            raise TypeError("failpoint must be callable or None.")
        self._factory = factory
        self._config = config
        self._failpoint = failpoint
        self._repository = OutboxRepository()
        self._materializer = FullProjectionMaterializer(
            factory, layout, failpoint=failpoint
        )
        self._temporal_materializer = (
            TemporalProjectionMaterializer(factory, layout)
            if config.footer_v2
            else None
        )
        self._aggregate_graph_materializer = (
            AggregateGraphProjectionMaterializer(factory, layout)
            if config.aggregate_graphs
            else None
        )

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    @property
    def config(self) -> ProjectionWorkerConfig:
        return self._config

    def run_once(self) -> ProjectionIterationResult:
        recovery = self._recover_expired()
        claim = self._claim_next()
        if claim is None:
            return ProjectionIterationResult(recovery, None, None, None, 0)
        self._hit("projector.after_claim")

        # Decode before any filesystem work.  The payload is not authority for
        # projection content, but malformed durable state must not be silently
        # acknowledged.
        try:
            claim.event.payload
        except Exception as exc:
            terminal = self._record_failure(claim, exc)
            return ProjectionIterationResult(
                recovery, claim, terminal, None, 0
            )
        keeper = _HeartbeatKeeper(self, claim)
        keeper.start()
        projection: ProjectionSpaceResult | None = None
        applied: OutboxEventRecord | None = None
        failure: Exception | None = None
        try:
            if claim.event.space_id is not None:
                if not self._space_is_settled(claim.event.space_id):
                    self._mark_building(claim.event.space_id)
                    projection = self._materializer.rebuild_space(
                        claim.event.space_id
                    )
                    if self._temporal_materializer is not None:
                        self._temporal_materializer.rebuild_space(claim.event.space_id)
            # Organization mutations intentionally have no physical space_id,
            # but may invalidate the old/new aggregate target union.  Settle a
            # bounded page for every claimed event when this worker is enabled.
            if self._aggregate_graph_materializer is not None:
                self._aggregate_graph_materializer.rebuild_pending()
            self._after_materialized(claim, projection)
            self._hit("projector.before_outbox_ack")
            applied = self._acknowledge(claim)
        except Exception as exc:
            failure = exc
        finally:
            try:
                keeper.close()
            except Exception as exc:
                failure = failure or exc

        if failure is not None:
            terminal = self._record_failure(claim, failure)
            return ProjectionIterationResult(
                recovery, claim, terminal, projection, keeper.count
            )
        if keeper.error is not None and applied is None:
            raise ProjectionHeartbeatError(
                "The projection heartbeat failed before terminal convergence."
            ) from keeper.error
        return ProjectionIterationResult(
            recovery, claim, applied, projection, keeper.count
        )

    def serve(
        self,
        stop: StopSignal,
        *,
        maximum_events: int | None = None,
    ) -> ProjectionServiceResult:
        if not callable(getattr(stop, "is_set", None)) or not callable(
            getattr(stop, "wait", None)
        ):
            raise TypeError("stop must expose is_set() and wait().")
        if maximum_events is not None and (
            isinstance(maximum_events, bool)
            or not isinstance(maximum_events, int)
            or maximum_events < 1
        ):
            raise ValueError("maximum_events must be a positive integer or None.")

        iterations = claimed = idle_polls = applied = retried = dead = 0
        recovered_retry = recovered_dead = heartbeat_count = 0
        idle_delay = self._config.idle_poll_ms
        recovery_retries = 0
        recovery_delay = _RECOVERY_CONTENTION_INITIAL_DELAY_MS
        while not stop.is_set():
            try:
                result = self.run_once()
            except ProjectionRecoveryContentionError as exc:
                # Only _recover_expired() creates this exception, after its
                # ordinary transaction and connection have been cleaned up.
                # No event has been claimed, so this is not an event attempt.
                if stop.is_set():
                    break
                if recovery_retries >= _RECOVERY_CONTENTION_EXTRA_RETRIES:
                    raise exc.original from None
                recovery_retries += 1
                if stop.wait(recovery_delay / 1000):
                    break
                recovery_delay = min(
                    _RECOVERY_CONTENTION_MAX_DELAY_MS, recovery_delay * 2
                )
                continue
            recovery_retries = 0
            recovery_delay = _RECOVERY_CONTENTION_INITIAL_DELAY_MS
            iterations += 1
            recovered_retry += len(result.recovery.retried_ids)
            recovered_dead += len(result.recovery.dead_ids)
            heartbeat_count += result.heartbeat_count
            if result.idle:
                idle_polls += 1
                stop.wait(idle_delay / 1000)
                idle_delay = min(
                    self._config.maximum_idle_poll_ms, idle_delay * 2
                )
                continue
            idle_delay = self._config.idle_poll_ms
            claimed += 1
            if result.event is not None:
                if result.event.state == "applied":
                    applied += 1
                elif result.event.state == "retry":
                    retried += 1
                elif result.event.state == "dead":
                    dead += 1
            if maximum_events is not None and claimed >= maximum_events:
                break
        return ProjectionServiceResult(
            iterations=iterations,
            claimed=claimed,
            idle_polls=idle_polls,
            applied=applied,
            retried=retried,
            dead=dead,
            recovered_retry=recovered_retry,
            recovered_dead=recovered_dead,
            heartbeat_count=heartbeat_count,
        )

    def _recover_expired(self) -> OutboxRecoveryResult:
        connection = self._factory.open()
        try:
            with transaction(connection):
                now_ms = db_now_ms(connection)
                result = self._repository.recover_expired(
                    connection,
                    now_ms=now_ms,
                    max_attempts=self._config.maximum_attempts,
                    error={
                        "code": "PROJECTION_CLAIM_EXPIRED",
                        "schema_version": 1,
                    },
                )
                if result.dead_ids:
                    placeholders = ",".join("?" for _ in result.dead_ids)
                    spaces = connection.execute(
                        f"""SELECT DISTINCT space_id FROM outbox_event
                              WHERE id IN ({placeholders}) AND space_id IS NOT NULL""",
                        result.dead_ids,
                    ).fetchall()
                    error_json = _error_json(
                        {
                            "code": "PROJECTION_CLAIM_EXPIRED",
                            "reason": "attempts_exhausted",
                            "schema_version": 1,
                        }
                    )
                    for row in spaces:
                        self._mark_projection_error_if_unsettled(
                            connection,
                            space_id=str(row["space_id"]),
                            error_json=error_json,
                            now_ms=now_ms,
                        )
                return result
        except sqlite3.OperationalError as exc:
            if not _is_recovery_sqlite_contention(exc):
                raise
            contention = exc
        finally:
            connection.close()
        # Construct the retry marker only after physical close succeeds. Factory
        # validation, rollback notes, close failures and all other errors escape.
        raise ProjectionRecoveryContentionError(contention) from contention

    def _claim_next(self) -> OutboxClaimResult | None:
        connection = self._factory.open()
        try:
            with transaction(connection):
                return self._repository.claim_next(
                    connection,
                    claim_owner=self._config.claim_owner,
                    lease_ms=self._config.lease_ms,
                    now_ms=db_now_ms(connection),
                    max_attempts=self._config.maximum_attempts,
                )
        finally:
            connection.close()

    def _renew(self, claim: OutboxClaimResult) -> None:
        connection = self._factory.open()
        try:
            with transaction(connection):
                self._repository.renew(
                    connection,
                    claim.fence,
                    lease_ms=self._config.lease_ms,
                    now_ms=db_now_ms(connection),
                )
        finally:
            connection.close()

    def _mark_building(self, space_id: str) -> None:
        connection = self._factory.open()
        try:
            with transaction(connection):
                now_ms = db_now_ms(connection)
                space = connection.execute(
                    "SELECT generation FROM knowledge_space WHERE id=?",
                    (space_id,),
                ).fetchone()
                if space is None:
                    raise ProjectionCanonicalDriftError(
                        "The outbox event's knowledge space is unavailable."
                    )
                generation = int(space["generation"])
                state = connection.execute(
                    """SELECT desired_generation,built_generation,row_version
                         FROM projection_state
                        WHERE projection_name='markdown' AND space_id=?""",
                    (space_id,),
                ).fetchone()
                if state is None:
                    connection.execute(
                        """INSERT INTO projection_state(
                               projection_name,space_id,desired_generation,
                               built_generation,checksum_sha256,status,last_error_json,
                               updated_at,built_at,row_version
                           ) VALUES ('markdown',?,?,0,NULL,'building',NULL,?,NULL,1)""",
                        (space_id, generation, now_ms),
                    )
                    return
                if (
                    int(state["desired_generation"]) > generation
                    or int(state["built_generation"]) > generation
                ):
                    raise ProjectionCanonicalDriftError(
                        "Projection state is ahead of canonical generation."
                    )
                cursor = connection.execute(
                    """UPDATE projection_state
                          SET desired_generation=?,status='building',
                              last_error_json=NULL,updated_at=?,row_version=row_version+1
                        WHERE projection_name='markdown' AND space_id=?
                          AND row_version=?""",
                    (generation, now_ms, space_id, int(state["row_version"])),
                )
                if cursor.rowcount != 1:
                    raise ProjectionCanonicalDriftError(
                        "Projection state changed before the build marker committed."
                    )
        finally:
            connection.close()

    def _space_is_settled(self, space_id: str) -> bool:
        """Return true when a prior build already covers this trigger.

        Outbox delivery is at least once. A worker crash or competing build can
        therefore leave a retry row after another worker has durably settled
        the exact current generation. Rebuilding that immutable generation is
        both redundant and unsafe; the acknowledgement transaction below
        repeats the same proof before applying the row.
        """

        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                row = connection.execute(
                    """SELECT s.generation,s.temporal_generation,
                              p.desired_generation,p.built_generation,
                              p.checksum_sha256,p.status
                         FROM knowledge_space AS s
                         LEFT JOIN projection_state AS p
                           ON p.space_id=s.id AND p.projection_name='markdown'
                        WHERE s.id=?""",
                    (space_id,),
                ).fetchone()
                if row is None or not (
                    row["status"] == "ready"
                    and row["checksum_sha256"] is not None
                    and int(row["generation"])
                    == int(row["desired_generation"])
                    == int(row["built_generation"])
                ):
                    return False
                if not self._config.footer_v2:
                    return True
                temporal = connection.execute(
                    """SELECT desired_generation,built_generation,
                              checksum_sha256,status
                         FROM projection_state
                        WHERE projection_name='markdown_v2' AND space_id=?""",
                    (space_id,),
                ).fetchone()
                expected = int(row["generation"]) + int(
                    row["temporal_generation"]
                )
                return temporal is not None and (
                    temporal["status"] == "ready"
                    and temporal["checksum_sha256"] is not None
                    and expected
                    == int(temporal["desired_generation"])
                    == int(temporal["built_generation"])
                )
        finally:
            connection.close()

    def _acknowledge(self, claim: OutboxClaimResult) -> OutboxEventRecord:
        connection = self._factory.open()
        try:
            with transaction(connection):
                now_ms = db_now_ms(connection)
                if claim.event.space_id is not None:
                    row = connection.execute(
                        """SELECT s.generation,p.desired_generation,
                                  p.built_generation,p.checksum_sha256,p.status
                             FROM knowledge_space AS s
                             JOIN projection_state AS p
                               ON p.space_id=s.id AND p.projection_name='markdown'
                            WHERE s.id=?""",
                        (claim.event.space_id,),
                    ).fetchone()
                    if row is None or not (
                        row["status"] == "ready"
                        and row["checksum_sha256"] is not None
                        and int(row["generation"])
                        == int(row["desired_generation"])
                        == int(row["built_generation"])
                    ):
                        raise ProjectionCanonicalDriftError(
                            "The projection is not settled at current canonical generation."
                        )
                    if self._config.footer_v2:
                        temporal = connection.execute(
                            """SELECT s.generation,s.temporal_generation,
                                      p.desired_generation,p.built_generation,
                                      p.checksum_sha256,p.status
                                 FROM knowledge_space AS s
                                 JOIN projection_state AS p
                                   ON p.space_id=s.id
                                  AND p.projection_name='markdown_v2'
                                WHERE s.id=?""",
                            (claim.event.space_id,),
                        ).fetchone()
                        expected = (
                            None
                            if temporal is None
                            else int(temporal["generation"])
                            + int(temporal["temporal_generation"])
                        )
                        if temporal is None or not (
                            temporal["status"] == "ready"
                            and temporal["checksum_sha256"] is not None
                            and expected
                            == int(temporal["desired_generation"])
                            == int(temporal["built_generation"])
                        ):
                            raise ProjectionCanonicalDriftError(
                                "The temporal projection is not settled at current generation."
                            )
                if self._config.settle_space_batch_size == 1:
                    return self._repository.mark_applied(
                        connection, claim.fence, now_ms=now_ms
                    )
                return self._acknowledge_batch(
                    connection, claim, now_ms=now_ms
                )
        finally:
            connection.close()

    def _acknowledge_batch(
        self,
        connection: ManagedConnection,
        claim: OutboxClaimResult,
        *,
        now_ms: int,
    ) -> OutboxEventRecord:
        applied = self._repository.mark_applied(
            connection, claim.fence, now_ms=now_ms
        )
        if claim.event.space_id is not None:
            self._repository.settle_ready_space_batch(
                connection,
                space_id=claim.event.space_id,
                claim_owner=self._config.claim_owner,
                lease_ms=self._config.lease_ms,
                max_attempts=self._config.maximum_attempts,
                now_ms=now_ms,
                limit=self._config.settle_space_batch_size - 1,
            )
        return applied

    def _record_failure(
        self, claim: OutboxClaimResult, exc: Exception
    ) -> OutboxEventRecord:
        safe_error = _safe_error(exc)
        delay = self._config.retry_delay_ms * (
            2 ** min(max(0, claim.event.attempt_count - 1), 8)
        )
        connection = self._factory.open()
        try:
            with transaction(connection):
                now_ms = db_now_ms(connection)
                if claim.event.space_id is not None:
                    self._mark_projection_error_if_unsettled(
                        connection,
                        space_id=claim.event.space_id,
                        error_json=_error_json(safe_error),
                        now_ms=now_ms,
                    )
                return self._repository.retry_or_dead(
                    connection,
                    claim.fence,
                    error=safe_error,
                    delay_ms=delay,
                    max_attempts=self._config.maximum_attempts,
                    now_ms=now_ms,
                )
        finally:
            connection.close()

    @staticmethod
    def _mark_projection_error_if_unsettled(
        connection: ManagedConnection,
        *,
        space_id: str,
        error_json: str,
        now_ms: int,
    ) -> None:
        """Record lag only when no worker already settled the current generation.

        Different outbox rows for one space may be processed concurrently.  A
        stale row that fails or exhausts its lease must not overwrite a newer
        worker's complete, checksum-bound projection with a false error state.
        """

        state = connection.execute(
            """SELECT p.desired_generation,p.built_generation,
                      p.checksum_sha256,p.status,p.row_version,s.generation
                 FROM projection_state AS p
                 JOIN knowledge_space AS s ON s.id=p.space_id
                WHERE p.projection_name='markdown' AND p.space_id=?""",
            (space_id,),
        ).fetchone()
        if state is None:
            return
        settled = (
            state["status"] == "ready"
            and state["checksum_sha256"] is not None
            and int(state["generation"])
            == int(state["desired_generation"])
            == int(state["built_generation"])
        )
        if settled:
            return
        cursor = connection.execute(
            """UPDATE projection_state
                  SET status='error',last_error_json=?,updated_at=?,
                      row_version=row_version+1
                WHERE projection_name='markdown' AND space_id=? AND row_version=?""",
            (error_json, now_ms, space_id, int(state["row_version"])),
        )
        if cursor.rowcount != 1:
            raise ProjectionCanonicalDriftError(
                "Projection state changed while recording a worker failure."
            )

    def _after_materialized(
        self,
        claim: OutboxClaimResult,
        projection: ProjectionSpaceResult | None,
    ) -> None:
        """Fault-injection seam; production behavior is intentionally empty."""


__all__ = [
    "OutboxProjectionWorker",
    "ProjectionHeartbeatError",
    "ProjectionRecoveryContentionError",
    "ProjectionIterationResult",
    "ProjectionServiceResult",
    "ProjectionWorkerConfig",
    "ProjectionWorkerError",
]

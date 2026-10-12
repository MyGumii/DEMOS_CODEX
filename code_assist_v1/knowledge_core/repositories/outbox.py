"""Durable canonical outbox enqueue and leased-consumer primitives.

Immutable event identity is inserted in the producer transaction.  The state
machine below is the only consumer mutation surface: claims are fenced by the
monotonic attempt count and owner, expired work is recovered durably, and an
event becomes terminal only while its live lease is still proven in the same
transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json

from ..db import ManagedConnection, db_now_ms
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


class OutboxRepositoryError(KnowledgeCoreDatabaseError):
    code = "OUTBOX_REPOSITORY_ERROR"


class OutboxConflictError(OutboxRepositoryError):
    code = "OUTBOX_EVENT_CONFLICT"


class OutboxClaimLostError(OutboxRepositoryError):
    code = "OUTBOX_CLAIM_LOST"


class OutboxStateConflictError(OutboxRepositoryError):
    code = "OUTBOX_STATE_CONFLICT"


@dataclass(frozen=True, slots=True)
class OutboxEventDraft:
    logical_key: str
    space_id: str | None
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    event_type: str
    payload: Mapping[str, object]
    available_at_ms: int


@dataclass(frozen=True, slots=True)
class OutboxEventRecord:
    id: int
    logical_key: str
    space_id: str | None
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    event_type: str
    payload_json: str
    state: str
    available_at_ms: int
    claim_owner: str | None
    claim_expires_at_ms: int | None
    attempt_count: int
    last_error_json: str | None
    created_at_ms: int
    completed_at_ms: int | None

    @property
    def payload(self) -> dict[str, object]:
        try:
            value = json.loads(self.payload_json)
        except (json.JSONDecodeError, TypeError) as exc:
            raise OutboxRepositoryError(
                "Stored outbox payload is not valid JSON."
            ) from exc
        if not isinstance(value, dict):
            raise OutboxRepositoryError("Stored outbox payload is not an object.")
        return value

    @property
    def last_error(self) -> dict[str, object] | None:
        if self.last_error_json is None:
            return None
        try:
            value = json.loads(self.last_error_json)
        except (json.JSONDecodeError, TypeError) as exc:
            raise OutboxRepositoryError(
                "Stored outbox error is not valid JSON."
            ) from exc
        if not isinstance(value, dict):
            raise OutboxRepositoryError("Stored outbox error is not an object.")
        return value


@dataclass(frozen=True, slots=True)
class OutboxClaimFence:
    event_id: int
    claim_owner: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class OutboxClaimResult:
    event: OutboxEventRecord
    fence: OutboxClaimFence


@dataclass(frozen=True, slots=True)
class OutboxRecoveryResult:
    retried_ids: tuple[int, ...]
    dead_ids: tuple[int, ...]


_COLUMNS = (
    "id,logical_key,space_id,aggregate_type,aggregate_id,aggregate_version,"
    "event_type,payload_json,state,available_at,claim_owner,claim_expires_at,"
    "attempt_count,last_error_json,created_at,completed_at"
)

_STATES = {"pending", "processing", "retry", "applied", "dead"}

# These events are emitted after the producer advances the physical content or
# local-graph generation of ``space_id``.  Temporal, organization-move, and
# aggregate-home mutations own their exact invalidation paths in their domain
# services and must not be double-counted here.
_AGGREGATE_GRAPH_INVALIDATION_EVENTS = frozenset(
    {
        "change_proposal.applied",
        "knowledge.item.published",
        "knowledge_item.deactivated",
        "knowledge_item.edited",
        "knowledge_item.merged",
        "knowledge_item.purged",
        "knowledge_item.restored",
        "semantic_edge.applied",
    }
)


def _canonical_json(value: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Outbox payload must be finite JSON.") from exc
    if not isinstance(json.loads(encoded), dict):
        raise ValueError("Outbox payload must be a JSON object.")
    return encoded


def _integer(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer of at least {minimum}.")
    return value


def _required_text(value: object, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field} has an invalid text shape.")
    return value


def _require_transaction(connection: ManagedConnection) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("OutboxRepository requires a ManagedConnection.")
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Outbox mutation requires an existing managed transaction."
        )
    return connection


def _fence(value: OutboxClaimFence) -> OutboxClaimFence:
    if not isinstance(value, OutboxClaimFence):
        raise TypeError("fence must be OutboxClaimFence.")
    _integer(value.event_id, field="event_id", minimum=1)
    _required_text(value.claim_owner, field="claim_owner", maximum=200)
    _integer(value.attempt_count, field="attempt_count", minimum=1)
    return value


def _row(row: object) -> OutboxEventRecord:
    try:
        record = OutboxEventRecord(
            id=int(row["id"]),  # type: ignore[index]
            logical_key=str(row["logical_key"]),  # type: ignore[index]
            space_id=row["space_id"],  # type: ignore[index,arg-type]
            aggregate_type=str(row["aggregate_type"]),  # type: ignore[index]
            aggregate_id=str(row["aggregate_id"]),  # type: ignore[index]
            aggregate_version=int(row["aggregate_version"]),  # type: ignore[index]
            event_type=str(row["event_type"]),  # type: ignore[index]
            payload_json=str(row["payload_json"]),  # type: ignore[index]
            state=str(row["state"]),  # type: ignore[index]
            available_at_ms=int(row["available_at"]),  # type: ignore[index]
            claim_owner=row["claim_owner"],  # type: ignore[index,arg-type]
            claim_expires_at_ms=(
                None
                if row["claim_expires_at"] is None  # type: ignore[index]
                else int(row["claim_expires_at"])  # type: ignore[index]
            ),
            attempt_count=int(row["attempt_count"]),  # type: ignore[index]
            last_error_json=row["last_error_json"],  # type: ignore[index,arg-type]
            created_at_ms=int(row["created_at"]),  # type: ignore[index]
            completed_at_ms=(
                None
                if row["completed_at"] is None  # type: ignore[index]
                else int(row["completed_at"])  # type: ignore[index]
            ),
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise OutboxRepositoryError("Stored outbox state is malformed.") from exc
    if record.state not in _STATES:
        raise OutboxRepositoryError("Stored outbox state is invalid.")
    if record.id < 1 or record.attempt_count < 0:
        raise OutboxRepositoryError("Stored outbox counters are invalid.")
    claimed = record.claim_owner is not None
    if claimed != (record.claim_expires_at_ms is not None):
        raise OutboxRepositoryError("Stored outbox claim fields are incoherent.")
    if claimed != (record.state == "processing"):
        raise OutboxRepositoryError("Stored outbox claim state is incoherent.")
    if (record.completed_at_ms is not None) != (record.state in {"applied", "dead"}):
        raise OutboxRepositoryError("Stored outbox completion state is incoherent.")
    record.payload
    record.last_error
    return record


class OutboxRepository:
    __slots__ = ()

    def append(
        self, connection: ManagedConnection, draft: OutboxEventDraft
    ) -> OutboxEventRecord:
        connection = _require_transaction(connection)
        payload_json = _canonical_json(draft.payload)
        cursor = connection.execute(
            """INSERT INTO outbox_event(
                 logical_key,space_id,aggregate_type,aggregate_id,
                 aggregate_version,event_type,payload_json,
                 payload_schema_version,state,available_at,claim_owner,
                 claim_expires_at,attempt_count,last_error_json,created_at,
                 completed_at
               ) VALUES (?,?,?,?,?, ?,?,1,'pending',?,NULL,NULL,0,NULL,?,NULL)
               ON CONFLICT(logical_key) DO NOTHING""",
            (
                draft.logical_key,
                draft.space_id,
                draft.aggregate_type,
                draft.aggregate_id,
                draft.aggregate_version,
                draft.event_type,
                payload_json,
                draft.available_at_ms,
                draft.available_at_ms,
            ),
        )
        row = connection.execute(
            f"SELECT {_COLUMNS} FROM outbox_event WHERE logical_key=?",
            (draft.logical_key,),
        ).fetchone()
        if row is None:
            raise OutboxRepositoryError("The inserted outbox event cannot be read.")
        record = _row(row)
        identity = (
            record.space_id,
            record.aggregate_type,
            record.aggregate_id,
            record.aggregate_version,
            record.event_type,
            record.payload_json,
            record.available_at_ms,
        )
        expected = (
            draft.space_id,
            draft.aggregate_type,
            draft.aggregate_id,
            draft.aggregate_version,
            draft.event_type,
            payload_json,
            draft.available_at_ms,
        )
        if cursor.rowcount != 1 and identity != expected:
            raise OutboxConflictError(
                "The outbox logical key identifies a different event."
            )
        if record.space_id is not None:
            observed_now = db_now_ms(connection)
            space = connection.execute(
                "SELECT generation FROM knowledge_space WHERE id=?",
                (record.space_id,),
            ).fetchone()
            if space is None:
                raise OutboxRepositoryError(
                    "The outbox event's canonical space is unavailable."
                )
            generation = int(space["generation"])
            connection.execute(
                """INSERT INTO projection_state(
                       projection_name,space_id,desired_generation,built_generation,
                       checksum_sha256,status,last_error_json,updated_at,built_at,
                       row_version
                   ) VALUES ('markdown',?,?,0,NULL,'idle',NULL,?,NULL,1)
                   ON CONFLICT(projection_name,space_id) DO UPDATE SET
                       desired_generation=excluded.desired_generation,
                       status='idle',last_error_json=NULL,updated_at=excluded.updated_at,
                       row_version=projection_state.row_version+1
                   WHERE projection_state.desired_generation
                         < excluded.desired_generation""",
                (record.space_id, generation, observed_now),
            )
            projection = connection.execute(
                """SELECT desired_generation,built_generation
                     FROM projection_state
                    WHERE projection_name='markdown' AND space_id=?""",
                (record.space_id,),
            ).fetchone()
            if projection is None or not (
                int(projection["desired_generation"]) == generation
                and 0
                <= int(projection["built_generation"])
                <= generation
            ):
                raise OutboxRepositoryError(
                    "The canonical projection generation marker is incoherent."
                )
            v2_installed = connection.execute(
                """SELECT count(*) FROM sqlite_schema
                    WHERE type='table' AND name='projection_manifest_v2_ledger'"""
            ).fetchone()
            if v2_installed is not None and int(v2_installed[0]) == 1:
                v2_space = connection.execute(
                    """SELECT generation,temporal_generation
                         FROM knowledge_space WHERE id=?""",
                    (record.space_id,),
                ).fetchone()
                if v2_space is None:
                    raise OutboxRepositoryError(
                        "The projection-v2 space generation is unavailable."
                    )
                rendered_generation = int(v2_space["generation"]) + int(
                    v2_space["temporal_generation"]
                )
                connection.execute(
                    """INSERT INTO projection_state(
                           projection_name,space_id,desired_generation,
                           built_generation,checksum_sha256,status,last_error_json,
                           updated_at,built_at,row_version
                       ) VALUES ('markdown_v2',?,?,0,NULL,'idle',NULL,?,NULL,1)
                       ON CONFLICT(projection_name,space_id) DO UPDATE SET
                           desired_generation=excluded.desired_generation,
                           status='idle',last_error_json=NULL,
                           updated_at=excluded.updated_at,
                           row_version=projection_state.row_version+1
                       WHERE projection_state.desired_generation
                             < excluded.desired_generation""",
                    (record.space_id, rendered_generation, observed_now),
                )
                v2_projection = connection.execute(
                    """SELECT desired_generation,built_generation
                         FROM projection_state
                        WHERE projection_name='markdown_v2' AND space_id=?""",
                    (record.space_id,),
                ).fetchone()
                if v2_projection is None or not (
                    int(v2_projection["desired_generation"])
                    == rendered_generation
                    and 0
                    <= int(v2_projection["built_generation"])
                    <= rendered_generation
                ):
                    raise OutboxRepositoryError(
                        "The projection-v2 generation marker is incoherent."
                    )
            aggregate_installed = connection.execute(
                """SELECT count(*) FROM sqlite_schema
                    WHERE type='table' AND name='aggregate_generation_vector'"""
            ).fetchone()
            if (
                aggregate_installed is not None
                and int(aggregate_installed[0]) == 1
                and record.event_type in _AGGREGATE_GRAPH_INVALIDATION_EVENTS
                and cursor.rowcount == 1
            ):
                # The producer appends this event in the same transaction after
                # advancing its physical space generation.  Mark only the
                # current ancestor chain; source-vector capture belongs to the
                # bounded projection worker and never to mutation latency.
                from .aggregate_graph import AggregateGraphRepository

                AggregateGraphRepository().invalidate_current_ancestors(
                    connection,
                    source_space_id=record.space_id,
                    now_ms=observed_now,
                )
        return record

    def get(
        self, connection: ManagedConnection, event_id: int
    ) -> OutboxEventRecord | None:
        connection = _require_transaction(connection)
        event_id = _integer(event_id, field="event_id", minimum=1)
        row = connection.execute(
            f"SELECT {_COLUMNS} FROM outbox_event WHERE id=?", (event_id,)
        ).fetchone()
        return None if row is None else _row(row)

    def require(
        self, connection: ManagedConnection, event_id: int
    ) -> OutboxEventRecord:
        record = self.get(connection, event_id)
        if record is None:
            raise OutboxStateConflictError("The durable outbox event is absent.")
        return record

    def verify_integrity(self, connection: ManagedConnection) -> int:
        connection = _require_transaction(connection)
        rows = connection.execute(
            f"SELECT {_COLUMNS} FROM outbox_event ORDER BY id"
        ).fetchall()
        for value in rows:
            _row(value)
        return len(rows)

    def recover_expired(
        self,
        connection: ManagedConnection,
        *,
        now_ms: int,
        max_attempts: int,
        error: Mapping[str, object],
    ) -> OutboxRecoveryResult:
        connection = _require_transaction(connection)
        now_ms = _integer(now_ms, field="now_ms")
        max_attempts = _integer(max_attempts, field="max_attempts", minimum=1)
        error_json = _canonical_json(error)
        rows = connection.execute(
            """SELECT id,claim_owner,claim_expires_at,attempt_count
                 FROM outbox_event
                WHERE state='processing' AND claim_expires_at<=?
                ORDER BY claim_expires_at,id""",
            (now_ms,),
        ).fetchall()
        retried: list[int] = []
        dead: list[int] = []
        exhausted_retry = connection.execute(
            """SELECT id FROM outbox_event
                WHERE state='retry' AND attempt_count>=? ORDER BY id""",
            (max_attempts,),
        ).fetchall()
        for value in exhausted_retry:
            event_id = int(value["id"])
            cursor = connection.execute(
                """UPDATE outbox_event SET state='dead',completed_at=?
                     WHERE id=? AND state='retry' AND attempt_count>=?""",
                (now_ms, event_id, max_attempts),
            )
            if cursor.rowcount == 1:
                dead.append(event_id)
        for value in rows:
            event_id = int(value["id"])
            cursor = connection.execute(
                """UPDATE outbox_event
                      SET state='retry',claim_owner=NULL,claim_expires_at=NULL,
                          last_error_json=?,available_at=?
                    WHERE id=? AND state='processing' AND claim_owner=?
                      AND claim_expires_at=? AND claim_expires_at<=?
                      AND attempt_count=?""",
                (
                    error_json,
                    now_ms,
                    event_id,
                    value["claim_owner"],
                    value["claim_expires_at"],
                    now_ms,
                    value["attempt_count"],
                ),
            )
            if cursor.rowcount != 1:
                continue
            if int(value["attempt_count"]) >= max_attempts:
                terminal = connection.execute(
                    """UPDATE outbox_event SET state='dead',completed_at=?
                         WHERE id=? AND state='retry' AND attempt_count>=?""",
                    (now_ms, event_id, max_attempts),
                )
                if terminal.rowcount != 1:
                    raise OutboxRepositoryError(
                        "An exhausted outbox event could not become dead."
                    )
                dead.append(event_id)
            else:
                retried.append(event_id)
        return OutboxRecoveryResult(tuple(retried), tuple(dead))

    def claim_next(
        self,
        connection: ManagedConnection,
        *,
        claim_owner: str,
        lease_ms: int,
        now_ms: int,
        max_attempts: int,
    ) -> OutboxClaimResult | None:
        connection = _require_transaction(connection)
        claim_owner = _required_text(
            claim_owner, field="claim_owner", maximum=200
        )
        lease_ms = _integer(lease_ms, field="lease_ms", minimum=1)
        now_ms = _integer(now_ms, field="now_ms")
        max_attempts = _integer(max_attempts, field="max_attempts", minimum=1)
        expires_at = _integer(now_ms + lease_ms, field="claim_expires_at")
        row = connection.execute(
            f"""UPDATE outbox_event
                   SET state='processing',claim_owner=?,claim_expires_at=?,
                       attempt_count=attempt_count+1
                 WHERE id=(
                   SELECT candidate.id FROM outbox_event AS candidate
                    WHERE candidate.state IN ('pending','retry')
                      AND candidate.available_at<=?
                      AND candidate.attempt_count<?
                    ORDER BY EXISTS (
                      SELECT 1 FROM outbox_event AS active
                       WHERE active.state='processing'
                         AND (
                           active.space_id=candidate.space_id
                           OR (active.space_id IS NULL
                               AND candidate.space_id IS NULL)
                         )
                    ),candidate.available_at,candidate.id LIMIT 1
                 )
                   AND state IN ('pending','retry') AND available_at<=?
                   AND attempt_count<?
                 RETURNING {_COLUMNS}""",
            (
                claim_owner,
                expires_at,
                now_ms,
                max_attempts,
                now_ms,
                max_attempts,
            ),
        ).fetchone()
        if row is None:
            return None
        event = _row(row)
        return OutboxClaimResult(
            event=event,
            fence=OutboxClaimFence(event.id, claim_owner, event.attempt_count),
        )

    def _raise_claim_lost(
        self,
        connection: ManagedConnection,
        fence: OutboxClaimFence,
        *,
        now_ms: int,
    ) -> None:
        fence = _fence(fence)
        current = self.require(connection, fence.event_id)
        if current.state != "processing":
            raise OutboxStateConflictError(
                "The outbox event is not processing."
            )
        if (
            current.claim_owner != fence.claim_owner
            or current.attempt_count != fence.attempt_count
            or current.claim_expires_at_ms is None
            or current.claim_expires_at_ms <= now_ms
        ):
            raise OutboxClaimLostError(
                "The outbox owner, attempt fence, or lease is no longer valid."
            )
        raise OutboxStateConflictError(
            "The outbox compare-and-swap was rejected."
        )

    def renew(
        self,
        connection: ManagedConnection,
        fence: OutboxClaimFence,
        *,
        lease_ms: int,
        now_ms: int,
    ) -> OutboxEventRecord:
        connection = _require_transaction(connection)
        fence = _fence(fence)
        lease_ms = _integer(lease_ms, field="lease_ms", minimum=1)
        now_ms = _integer(now_ms, field="now_ms")
        proposed = _integer(now_ms + lease_ms, field="claim_expires_at")
        cursor = connection.execute(
            """UPDATE outbox_event
                  SET claim_expires_at=max(claim_expires_at,?)
                WHERE id=? AND state='processing' AND claim_owner=?
                  AND attempt_count=? AND claim_expires_at>?""",
            (
                proposed,
                fence.event_id,
                fence.claim_owner,
                fence.attempt_count,
                now_ms,
            ),
        )
        if cursor.rowcount != 1:
            self._raise_claim_lost(connection, fence, now_ms=now_ms)
        return self.require(connection, fence.event_id)

    def mark_applied(
        self,
        connection: ManagedConnection,
        fence: OutboxClaimFence,
        *,
        now_ms: int,
    ) -> OutboxEventRecord:
        connection = _require_transaction(connection)
        fence = _fence(fence)
        now_ms = _integer(now_ms, field="now_ms")
        cursor = connection.execute(
            """UPDATE outbox_event
                  SET state='applied',claim_owner=NULL,claim_expires_at=NULL,
                      last_error_json=NULL,completed_at=?
                WHERE id=? AND state='processing' AND claim_owner=?
                  AND attempt_count=? AND claim_expires_at>?""",
            (
                now_ms,
                fence.event_id,
                fence.claim_owner,
                fence.attempt_count,
                now_ms,
            ),
        )
        if cursor.rowcount != 1:
            self._raise_claim_lost(connection, fence, now_ms=now_ms)
        return self.require(connection, fence.event_id)

    def settle_ready_space_batch(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        claim_owner: str,
        lease_ms: int,
        max_attempts: int,
        now_ms: int,
        limit: int,
    ) -> tuple[int, ...]:
        """Converge already-covered projection triggers for one settled space.

        The caller must first prove, in this same transaction, that the
        physical projection matches the space's current canonical generation.
        Each coalesced row still traverses the guarded
        pending/retry -> processing -> applied state machine with its own
        monotonic attempt fence; only the redundant filesystem rebuild is
        skipped.
        """

        connection = _require_transaction(connection)
        space_id = _required_text(space_id, field="space_id", maximum=255)
        claim_owner = _required_text(
            claim_owner, field="claim_owner", maximum=200
        )
        lease_ms = _integer(lease_ms, field="lease_ms", minimum=1)
        max_attempts = _integer(
            max_attempts, field="max_attempts", minimum=1
        )
        now_ms = _integer(now_ms, field="now_ms")
        limit = _integer(limit, field="limit", minimum=1)
        expires_at = _integer(now_ms + lease_ms, field="claim_expires_at")
        rows = connection.execute(
            f"""SELECT {_COLUMNS} FROM outbox_event
                  WHERE space_id=? AND state IN ('pending','retry')
                    AND available_at<=? AND attempt_count<?
                  ORDER BY available_at,id LIMIT ?""",
            (space_id, now_ms, max_attempts, limit),
        ).fetchall()
        candidates = tuple(_row(row) for row in rows)
        settled: list[int] = []
        for candidate in candidates:
            cursor = connection.execute(
                """UPDATE outbox_event
                      SET state='processing',claim_owner=?,claim_expires_at=?,
                          attempt_count=attempt_count+1
                    WHERE id=? AND state=? AND available_at<=?
                      AND attempt_count=? AND attempt_count<?""",
                (
                    claim_owner,
                    expires_at,
                    candidate.id,
                    candidate.state,
                    now_ms,
                    candidate.attempt_count,
                    max_attempts,
                ),
            )
            if cursor.rowcount != 1:
                raise OutboxStateConflictError(
                    "A coalesced outbox event changed before it was claimed."
                )
            fence = OutboxClaimFence(
                candidate.id,
                claim_owner,
                candidate.attempt_count + 1,
            )
            self.mark_applied(connection, fence, now_ms=now_ms)
            settled.append(candidate.id)
        return tuple(settled)

    def retry_or_dead(
        self,
        connection: ManagedConnection,
        fence: OutboxClaimFence,
        *,
        error: Mapping[str, object],
        delay_ms: int,
        max_attempts: int,
        now_ms: int,
    ) -> OutboxEventRecord:
        connection = _require_transaction(connection)
        fence = _fence(fence)
        error_json = _canonical_json(error)
        delay_ms = _integer(delay_ms, field="delay_ms")
        max_attempts = _integer(max_attempts, field="max_attempts", minimum=1)
        now_ms = _integer(now_ms, field="now_ms")
        available_at = _integer(now_ms + delay_ms, field="available_at")
        cursor = connection.execute(
            """UPDATE outbox_event
                  SET state='retry',claim_owner=NULL,claim_expires_at=NULL,
                      last_error_json=?,available_at=?
                WHERE id=? AND state='processing' AND claim_owner=?
                  AND attempt_count=? AND claim_expires_at>?""",
            (
                error_json,
                available_at,
                fence.event_id,
                fence.claim_owner,
                fence.attempt_count,
                now_ms,
            ),
        )
        if cursor.rowcount != 1:
            self._raise_claim_lost(connection, fence, now_ms=now_ms)
        current = self.require(connection, fence.event_id)
        if current.attempt_count >= max_attempts:
            terminal = connection.execute(
                """UPDATE outbox_event SET state='dead',completed_at=?
                     WHERE id=? AND state='retry' AND attempt_count>=?""",
                (now_ms, fence.event_id, max_attempts),
            )
            if terminal.rowcount != 1:
                raise OutboxRepositoryError(
                    "The exhausted outbox event could not become dead."
                )
            current = self.require(connection, fence.event_id)
        return current


__all__ = [
    "OutboxClaimFence",
    "OutboxClaimLostError",
    "OutboxClaimResult",
    "OutboxConflictError",
    "OutboxEventDraft",
    "OutboxEventRecord",
    "OutboxRecoveryResult",
    "OutboxRepository",
    "OutboxRepositoryError",
    "OutboxStateConflictError",
]

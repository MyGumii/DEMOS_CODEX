"""Connection-scoped v10 semantic graph storage primitives.

The repository intentionally owns no authorization, LCA calculation, model
work, job claiming, or transaction.  Later SBE services compose these bounded
reads and the insert-only intent primitive inside one managed transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
import json
import re
import sqlite3
from typing import TypeVar

from ..db import ManagedConnection
from ..domain import (
    AggregateEdgeStatus,
    ExplicitReferenceAvailability,
    SemanticEdgeDirection,
    SemanticIntentProvenanceKind,
    SemanticIntentTargetKind,
    SemanticRelationType,
)
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


SEMANTIC_INTENT_BATCH_MAX_SIZE = 200
SEMANTIC_LEDGER_MAX_PAGE_SIZE = 100
SEMANTIC_DECISION_MAX_ROWS_PER_JOB = 13
EXPLICIT_REFERENCE_MAX_ROWS_PER_REVISION = 1000
EXPLICIT_REFERENCE_UNAVAILABLE_REASONS = frozenset(
    {"target_inactive", "target_archived", "permission_lost", "target_missing"}
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)


class SemanticGraphContractRepositoryError(KnowledgeCoreDatabaseError):
    code = "SEMANTIC_GRAPH_CONTRACT_REPOSITORY_ERROR"


class SemanticGraphContractConflictError(SemanticGraphContractRepositoryError):
    code = "SEMANTIC_GRAPH_CONTRACT_CONFLICT"


class SemanticLifecycleVectorError(SemanticGraphContractRepositoryError):
    code = "SEMANTIC_LIFECYCLE_VECTOR_INVALID"


@dataclass(frozen=True, slots=True)
class SemanticLifecycleVector:
    item_lifecycle_generation: int
    wiki_lifecycle_generation: int
    latest_episode_id: str | None
    latest_episode_no: int | None
    latest_episode_restored_at: int | None


@dataclass(frozen=True, slots=True)
class SemanticEdgeIntentSeedRunRecord:
    id: str
    effect_key: str
    cause_kind: SemanticIntentProvenanceKind
    scope_kind: str
    scope_id: str | None
    detector_generation: int
    previous_detector_generation: int | None
    old_ancestor_space_id: str | None
    new_ancestor_space_id: str | None
    organization_event_audit_id: int | None
    detector_rollout_audit_id: int | None
    lifecycle_restore_audit_id: int | None
    snapshot_domain_high_water: int
    snapshot_audit_high_water: int
    cursor_updated_at: int
    cursor_revision_id: str | None
    state: str
    fence: int
    scanned_count: int
    intent_count: int
    result_sha256: str | None
    error_json: str | None
    created_audit_event_id: int
    created_outbox_event_id: int
    created_at: int
    updated_at: int
    completed_at: int | None
    row_version: int


@dataclass(frozen=True, slots=True)
class SemanticEdgeIntentRecord:
    id: str
    effect_key: str
    source_space_id: str
    source_item_id: str
    source_revision_id: str
    source_body_sha256: str
    target_kind: SemanticIntentTargetKind
    target_space_id: str
    target_org_unit_id: str | None
    detector_generation: int
    source_revision_row_version: int
    source_space_generation: int
    target_space_generation: int
    organization_generation: int
    closure_proof_sha256: str
    item_lifecycle_generation: int
    wiki_lifecycle_generation: int
    latest_episode_id: str | None
    latest_episode_no: int | None
    latest_episode_restored_at: int | None
    provenance_kind: SemanticIntentProvenanceKind
    publication_transition_id: str | None
    publication_audit_event_id: int | None
    publication_outbox_event_id: int | None
    seed_run_id: str | None
    seed_snapshot_high_water: int | None
    seed_cursor_updated_at: int | None
    seed_cursor_revision_id: str | None
    organization_event_audit_id: int | None
    detector_rollout_audit_id: int | None
    lifecycle_restore_audit_id: int | None
    created_at: int

    @property
    def lifecycle_vector(self) -> SemanticLifecycleVector:
        return SemanticLifecycleVector(
            self.item_lifecycle_generation,
            self.wiki_lifecycle_generation,
            self.latest_episode_id,
            self.latest_episode_no,
            self.latest_episode_restored_at,
        )


@dataclass(frozen=True, slots=True)
class AggregateKnowledgeEdgeRecord:
    id: str
    semantic_key: str
    home_space_id: str
    home_org_unit_id: str
    source_space_id: str
    source_item_id: str
    source_revision_id: str
    target_space_id: str
    target_item_id: str
    target_revision_id: str
    edge_type: SemanticRelationType
    direction: SemanticEdgeDirection
    edge_status: AggregateEdgeStatus
    origin: str
    actor_kind: str
    actor_user_id: str | None
    system_job_id: str | None
    operation_id: str
    effect_key: str
    detector_generation: int | None
    model_provider: str | None
    model_name: str | None
    prompt_schema_version: str | None
    scoring_version: str | None
    confidence_ppm: int | None
    source_evidence_span_sha256: str
    target_evidence_span_sha256: str
    source_evidence_location_json: str
    target_evidence_location_json: str
    closure_proof_sha256: str
    source_item_row_version: int
    target_item_row_version: int
    audit_event_id: int
    outbox_event_id: int
    created_at: int
    updated_at: int
    deactivated_at: int | None
    row_version: int


@dataclass(frozen=True, slots=True)
class SemanticEdgeDecisionRecord:
    id: str
    job_id: str
    intent_id: str
    record_kind: str
    ordinal: int | None
    outcome: str
    candidate_space_id: str | None
    candidate_item_id: str | None
    candidate_revision_id: str | None
    retrieval_version: str | None
    retrieval_score_json: str | None
    model_provider: str | None
    model_name: str | None
    prompt_schema_version: str | None
    response_sha256: str | None
    edge_type: SemanticRelationType | None
    direction: SemanticEdgeDirection | None
    model_grade: int | None
    source_evidence_span_sha256: str | None
    target_evidence_span_sha256: str | None
    validation_result: str | None
    terminal_reason: str | None
    candidate_count: int | None
    candidate_ids_sha256: str | None
    local_edge_id: str | None
    aggregate_edge_id: str | None
    evidence_json: str
    created_at: int


@dataclass(frozen=True, slots=True)
class SemanticEdgeSuppressionRecord:
    id: str
    semantic_key: str
    scope_kind: str
    scope_space_id: str
    local_edge_id: str | None
    aggregate_edge_id: str | None
    reason: str
    detector_generation_floor: int
    detector_generation_ceiling: int | None
    status: str
    actor_user_id: str
    operation_id: str
    effect_key: str
    audit_event_id: int
    outbox_event_id: int
    superseding_local_edge_id: str | None
    superseding_aggregate_edge_id: str | None
    lifted_by_user_id: str | None
    lifted_at: int | None
    lift_audit_event_id: int | None
    lift_outbox_event_id: int | None
    created_at: int
    updated_at: int
    row_version: int


@dataclass(frozen=True, slots=True)
class AggregateGenerationVectorRecord:
    selected_space_id: str
    source_vector_json: str
    source_vector_sha256: str
    home_edge_generation: int
    desired_generation: int
    built_generation: int
    manifest_sha256: str | None
    closure_proof_sha256: str
    organization_row_version: int
    status: str
    last_error_json: str | None
    created_at: int
    updated_at: int
    row_version: int


@dataclass(frozen=True, slots=True)
class ExplicitReferenceRecord:
    id: str
    source_space_id: str
    source_item_id: str
    source_revision_id: str
    ordinal: int
    syntax_version: str
    target_space_id: str
    target_item_id: str
    target_revision_id: str
    closure_proof_sha256: str
    actor_user_id: str
    operation_id: str
    effect_key: str
    audit_event_id: int
    outbox_event_id: int
    availability_status: ExplicitReferenceAvailability
    unavailable_reason: str | None
    created_at: int
    updated_at: int
    row_version: int


_RecordT = TypeVar("_RecordT")
_ENUM_FIELDS: dict[str, type[Enum]] = {
    "availability_status": ExplicitReferenceAvailability,
    "cause_kind": SemanticIntentProvenanceKind,
    "direction": SemanticEdgeDirection,
    "edge_status": AggregateEdgeStatus,
    "edge_type": SemanticRelationType,
    "provenance_kind": SemanticIntentProvenanceKind,
    "target_kind": SemanticIntentTargetKind,
}


def _connection(value: object) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError(
            "SemanticGraphContractRepository requires a ManagedConnection."
        )
    return value


def _write_connection(value: object) -> ManagedConnection:
    connection = _connection(value)
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Semantic graph contract writes require a caller-owned transaction."
        )
    return connection


def _uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _bounded_limit(value: object, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError("limit is outside the bounded repository range.")
    return value


def _timestamp(value: object, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 9_223_372_036_854_775_807
    ):
        raise ValueError(f"{field} must be a nonnegative SQLite timestamp.")
    return value


def _value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise SemanticGraphContractRepositoryError(
            "A canonical semantic graph row cannot be mapped."
        ) from exc


def _columns(record_type: type[object]) -> str:
    return ",".join(field.name for field in fields(record_type))


def _record(record_type: type[_RecordT], row: object) -> _RecordT:
    values: dict[str, object] = {}
    try:
        for field in fields(record_type):
            value = _value(row, field.name)
            enum_type = _ENUM_FIELDS.get(field.name)
            if enum_type is not None and value is not None:
                value = enum_type(value)
            if field.name.endswith("_json") and value is not None:
                parsed = json.loads(value)  # type: ignore[arg-type]
                if type(parsed) not in (dict, list):
                    raise ValueError("JSON field is not a container")
            values[field.name] = value
        return record_type(**values)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SemanticGraphContractRepositoryError(
            "A canonical semantic graph row is invalid."
        ) from exc


def _sql_values(record: object) -> tuple[object, ...]:
    return tuple(
        value.value if isinstance(value, Enum) else value
        for value in (getattr(record, field.name) for field in fields(record))
    )


def derive_semantic_lifecycle_vector(
    connection: ManagedConnection,
    *,
    source_space_id: str,
    source_item_id: str,
) -> SemanticLifecycleVector:
    """Derive the exact item/Wiki generation proof captured by an intent."""

    connection = _connection(connection)
    source_space_id = _uuid(source_space_id, field="source_space_id")
    source_item_id = _uuid(source_item_id, field="source_item_id")
    state = connection.execute(
        """SELECT i.lifecycle_status,i.latest_deactivation_episode_id,
                  s.space_kind,s.status AS space_status,
                  p.lifecycle_status AS wiki_status,p.lifecycle_generation
             FROM knowledge_item i JOIN knowledge_space s ON s.id=i.space_id
             LEFT JOIN personal_wiki_profile p ON p.space_id=s.id
            WHERE i.id=? AND i.space_id=?""",
        (source_item_id, source_space_id),
    ).fetchone()
    if state is None or _value(state, "space_status") != "active":
        raise SemanticLifecycleVectorError(
            "The semantic lifecycle source is unavailable."
        )
    lifecycle_status = _value(state, "lifecycle_status")
    if lifecycle_status in ("purged", "merged"):
        raise SemanticLifecycleVectorError(
            "Purged or merged items have no semantic lifecycle vector."
        )
    episode = connection.execute(
        """SELECT id,episode_no,restored_at,purged_at
             FROM item_deactivation_episode WHERE item_id=?
             ORDER BY episode_no DESC LIMIT 1""",
        (source_item_id,),
    ).fetchone()
    if episode is None:
        item_generation = 1
        episode_id = None
        episode_no = None
        restored_at = None
    else:
        if _value(episode, "purged_at") is not None:
            raise SemanticLifecycleVectorError(
                "A purged deactivation episode is not semantically eligible."
            )
        episode_id = _uuid(_value(episode, "id"), field="latest_episode_id")
        episode_no_value = _value(episode, "episode_no")
        if (
            isinstance(episode_no_value, bool)
            or not isinstance(episode_no_value, int)
            or episode_no_value < 1
        ):
            raise SemanticLifecycleVectorError(
                "The latest deactivation episode is malformed."
            )
        episode_no = episode_no_value
        restored_value = _value(episode, "restored_at")
        if restored_value is None:
            item_generation = 2 * episode_no
            restored_at = None
        else:
            if isinstance(restored_value, bool) or not isinstance(restored_value, int):
                raise SemanticLifecycleVectorError(
                    "The latest restore timestamp is malformed."
                )
            item_generation = 2 * episode_no + 1
            restored_at = restored_value
    if _value(state, "latest_deactivation_episode_id") != episode_id:
        raise SemanticLifecycleVectorError(
            "The item does not point to its latest deactivation episode."
        )
    if (lifecycle_status == "active") != (item_generation % 2 == 1):
        raise SemanticLifecycleVectorError(
            "The item state and derived lifecycle generation disagree."
        )
    if _value(state, "space_kind") == "personal":
        wiki_generation = _value(state, "lifecycle_generation")
        if (
            _value(state, "wiki_status") != "active"
            or isinstance(wiki_generation, bool)
            or not isinstance(wiki_generation, int)
            or wiki_generation < 1
        ):
            raise SemanticLifecycleVectorError(
                "The personal Wiki lifecycle proof is unavailable."
            )
    else:
        wiki_generation = 0
    return SemanticLifecycleVector(
        item_generation,
        wiki_generation,
        episode_id,
        episode_no,
        restored_at,
    )


class SemanticGraphContractRepository:
    """Bounded v10 reads plus exact insert-only semantic-intent replay."""

    __slots__ = ()

    def get_intent(
        self, connection: ManagedConnection, intent_id: str
    ) -> SemanticEdgeIntentRecord | None:
        connection = _connection(connection)
        intent_id = _uuid(intent_id, field="intent_id")
        row = connection.execute(
            f"SELECT {_columns(SemanticEdgeIntentRecord)} "
            "FROM semantic_edge_intent WHERE id=?",
            (intent_id,),
        ).fetchone()
        return None if row is None else _record(SemanticEdgeIntentRecord, row)

    def get_intent_by_effect_key(
        self, connection: ManagedConnection, effect_key: str
    ) -> SemanticEdgeIntentRecord | None:
        connection = _connection(connection)
        if not isinstance(effect_key, str) or effect_key != effect_key.strip() or not effect_key:
            raise ValueError("effect_key must be nonblank trimmed text.")
        row = connection.execute(
            f"SELECT {_columns(SemanticEdgeIntentRecord)} "
            "FROM semantic_edge_intent WHERE effect_key=?",
            (effect_key,),
        ).fetchone()
        return None if row is None else _record(SemanticEdgeIntentRecord, row)

    def append_intent(
        self,
        connection: ManagedConnection,
        record: SemanticEdgeIntentRecord,
    ) -> SemanticEdgeIntentRecord:
        connection = _write_connection(connection)
        if not isinstance(record, SemanticEdgeIntentRecord):
            raise TypeError("A SemanticEdgeIntentRecord is required.")
        columns = _columns(SemanticEdgeIntentRecord)
        placeholders = ",".join("?" for _ in fields(SemanticEdgeIntentRecord))
        try:
            cursor = connection.execute(
                f"INSERT INTO semantic_edge_intent({columns}) "
                f"VALUES ({placeholders}) ON CONFLICT(effect_key) DO NOTHING",
                _sql_values(record),
            )
        except sqlite3.IntegrityError as exc:
            raise SemanticGraphContractConflictError(
                "The semantic intent violates the v10 storage contract."
            ) from exc
        stored = self.get_intent_by_effect_key(connection, record.effect_key)
        if stored is None or stored != record:
            raise SemanticGraphContractConflictError(
                "The semantic intent effect key is bound incompatibly."
            )
        if cursor.rowcount not in (0, 1):
            raise SemanticGraphContractRepositoryError(
                "The semantic intent insert returned an invalid row count."
            )
        return stored

    def list_intents_after(
        self,
        connection: ManagedConnection,
        *,
        after_created_at: int = 0,
        after_id: str | None = None,
        limit: int = SEMANTIC_INTENT_BATCH_MAX_SIZE,
    ) -> tuple[SemanticEdgeIntentRecord, ...]:
        connection = _connection(connection)
        limit = _bounded_limit(limit, maximum=SEMANTIC_INTENT_BATCH_MAX_SIZE)
        if isinstance(after_created_at, bool) or not isinstance(after_created_at, int) or after_created_at < 0:
            raise ValueError("after_created_at must be non-negative.")
        if after_id is None:
            after_id = "00000000-0000-0000-0000-000000000000"
        else:
            after_id = _uuid(after_id, field="after_id")
        rows = connection.execute(
            f"""SELECT {_columns(SemanticEdgeIntentRecord)}
                  FROM semantic_edge_intent
                 WHERE (created_at,id)>(?,?)
                 ORDER BY created_at,id LIMIT ?""",
            (after_created_at, after_id, limit),
        ).fetchall()
        return tuple(_record(SemanticEdgeIntentRecord, row) for row in rows)

    def get_seed_run(
        self, connection: ManagedConnection, seed_run_id: str
    ) -> SemanticEdgeIntentSeedRunRecord | None:
        connection = _connection(connection)
        seed_run_id = _uuid(seed_run_id, field="seed_run_id")
        row = connection.execute(
            f"SELECT {_columns(SemanticEdgeIntentSeedRunRecord)} "
            "FROM semantic_edge_intent_seed_run WHERE id=?",
            (seed_run_id,),
        ).fetchone()
        return None if row is None else _record(SemanticEdgeIntentSeedRunRecord, row)

    def get_seed_run_by_effect_key(
        self, connection: ManagedConnection, effect_key: str
    ) -> SemanticEdgeIntentSeedRunRecord | None:
        connection = _connection(connection)
        if (
            not isinstance(effect_key, str)
            or effect_key != effect_key.strip()
            or not effect_key
        ):
            raise ValueError("effect_key must be nonblank trimmed text.")
        row = connection.execute(
            f"SELECT {_columns(SemanticEdgeIntentSeedRunRecord)} "
            "FROM semantic_edge_intent_seed_run WHERE effect_key=?",
            (effect_key,),
        ).fetchone()
        return None if row is None else _record(SemanticEdgeIntentSeedRunRecord, row)

    def append_seed_run(
        self,
        connection: ManagedConnection,
        record: SemanticEdgeIntentSeedRunRecord,
    ) -> SemanticEdgeIntentSeedRunRecord:
        connection = _write_connection(connection)
        if not isinstance(record, SemanticEdgeIntentSeedRunRecord):
            raise TypeError("A SemanticEdgeIntentSeedRunRecord is required.")
        columns = _columns(SemanticEdgeIntentSeedRunRecord)
        placeholders = ",".join("?" for _ in fields(record))
        try:
            cursor = connection.execute(
                f"INSERT INTO semantic_edge_intent_seed_run({columns}) "
                f"VALUES ({placeholders}) ON CONFLICT(effect_key) DO NOTHING",
                _sql_values(record),
            )
        except sqlite3.IntegrityError as exc:
            raise SemanticGraphContractConflictError(
                "The semantic seed run violates the v10 storage contract."
            ) from exc
        stored = self.get_seed_run_by_effect_key(connection, record.effect_key)
        if stored is None or stored != record:
            raise SemanticGraphContractConflictError(
                "The semantic seed-run effect key is bound incompatibly."
            )
        if cursor.rowcount not in (0, 1):
            raise SemanticGraphContractRepositoryError(
                "The semantic seed-run insert returned an invalid row count."
            )
        return stored

    def start_seed_run(
        self,
        connection: ManagedConnection,
        *,
        seed_run_id: str,
        expected_fence: int,
        now_ms: int,
    ) -> SemanticEdgeIntentSeedRunRecord:
        connection = _write_connection(connection)
        seed_run_id = _uuid(seed_run_id, field="seed_run_id")
        if isinstance(expected_fence, bool) or not isinstance(expected_fence, int) or expected_fence < 1:
            raise ValueError("expected_fence must be positive.")
        now_ms = _timestamp(now_ms, field="now_ms")
        current = self.get_seed_run(connection, seed_run_id)
        if current is None:
            raise SemanticGraphContractConflictError("The semantic seed run is unavailable.")
        if current.state == "running":
            return current
        if current.state not in {"pending", "failed"} or current.fence != expected_fence:
            raise SemanticGraphContractConflictError("The semantic seed-run fence is stale.")
        new_fence = expected_fence if current.state == "pending" else expected_fence + 1
        cursor = connection.execute(
            """UPDATE semantic_edge_intent_seed_run
                  SET state='running',fence=?,error_json=NULL,completed_at=NULL,
                      updated_at=?,row_version=row_version+1
                WHERE id=? AND state=? AND fence=?""",
            (new_fence, now_ms, seed_run_id, current.state, expected_fence),
        )
        if cursor.rowcount != 1:
            raise SemanticGraphContractConflictError("The semantic seed-run start lost its fence.")
        started = self.get_seed_run(connection, seed_run_id)
        if started is None:
            raise SemanticGraphContractRepositoryError("The semantic seed run disappeared.")
        return started

    def advance_seed_run(
        self,
        connection: ManagedConnection,
        *,
        seed_run_id: str,
        fence: int,
        cursor_updated_at: int,
        cursor_revision_id: str,
        scanned_increment: int,
        intent_increment: int,
        now_ms: int,
    ) -> SemanticEdgeIntentSeedRunRecord:
        connection = _write_connection(connection)
        seed_run_id = _uuid(seed_run_id, field="seed_run_id")
        cursor_revision_id = _uuid(cursor_revision_id, field="cursor_revision_id")
        cursor_updated_at = _timestamp(cursor_updated_at, field="cursor_updated_at")
        now_ms = _timestamp(now_ms, field="now_ms")
        for value, name in (
            (fence, "fence"),
            (scanned_increment, "scanned_increment"),
            (intent_increment, "intent_increment"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < (1 if name == "fence" else 0):
                raise ValueError(f"{name} is invalid.")
        cursor = connection.execute(
            """UPDATE semantic_edge_intent_seed_run
                  SET cursor_updated_at=?,cursor_revision_id=?,
                      scanned_count=scanned_count+?,intent_count=intent_count+?,
                      updated_at=?,row_version=row_version+1
                WHERE id=? AND state='running' AND fence=?
                  AND (cursor_revision_id IS NULL
                       OR (cursor_updated_at,cursor_revision_id)<(?,?))""",
            (
                cursor_updated_at,
                cursor_revision_id,
                scanned_increment,
                intent_increment,
                now_ms,
                seed_run_id,
                fence,
                cursor_updated_at,
                cursor_revision_id,
            ),
        )
        if cursor.rowcount != 1:
            raise SemanticGraphContractConflictError("The semantic seed-run cursor is stale.")
        advanced = self.get_seed_run(connection, seed_run_id)
        if advanced is None:
            raise SemanticGraphContractRepositoryError("The semantic seed run disappeared.")
        return advanced

    def complete_seed_run(
        self,
        connection: ManagedConnection,
        *,
        seed_run_id: str,
        fence: int,
        result_sha256: str,
        now_ms: int,
    ) -> SemanticEdgeIntentSeedRunRecord:
        connection = _write_connection(connection)
        seed_run_id = _uuid(seed_run_id, field="seed_run_id")
        if isinstance(fence, bool) or not isinstance(fence, int) or fence < 1:
            raise ValueError("fence must be positive.")
        if not isinstance(result_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", result_sha256) is None:
            raise ValueError("result_sha256 must be a lowercase SHA-256 digest.")
        now_ms = _timestamp(now_ms, field="now_ms")
        cursor = connection.execute(
            """UPDATE semantic_edge_intent_seed_run
                  SET state='completed',result_sha256=?,updated_at=?,completed_at=?,
                      row_version=row_version+1
                WHERE id=? AND state='running' AND fence=?""",
            (result_sha256, now_ms, now_ms, seed_run_id, fence),
        )
        if cursor.rowcount != 1:
            raise SemanticGraphContractConflictError("The semantic seed-run completion lost its fence.")
        completed = self.get_seed_run(connection, seed_run_id)
        if completed is None:
            raise SemanticGraphContractRepositoryError("The semantic seed run disappeared.")
        return completed

    def append_decision(
        self,
        connection: ManagedConnection,
        record: SemanticEdgeDecisionRecord,
    ) -> SemanticEdgeDecisionRecord:
        connection = _write_connection(connection)
        if not isinstance(record, SemanticEdgeDecisionRecord):
            raise TypeError("A SemanticEdgeDecisionRecord is required.")
        columns = _columns(SemanticEdgeDecisionRecord)
        placeholders = ",".join("?" for _ in fields(record))
        try:
            cursor = connection.execute(
                f"INSERT INTO semantic_edge_decision({columns}) "
                f"VALUES ({placeholders}) ON CONFLICT(id) DO NOTHING",
                _sql_values(record),
            )
        except sqlite3.IntegrityError as exc:
            raise SemanticGraphContractConflictError(
                "The semantic decision violates the v10 storage contract."
            ) from exc
        row = connection.execute(
            f"SELECT {columns} FROM semantic_edge_decision WHERE id=?",
            (record.id,),
        ).fetchone()
        stored = None if row is None else _record(SemanticEdgeDecisionRecord, row)
        if stored != record:
            raise SemanticGraphContractConflictError(
                "The semantic decision identity is bound incompatibly."
            )
        if cursor.rowcount not in (0, 1):
            raise SemanticGraphContractRepositoryError(
                "The semantic decision insert returned an invalid row count."
            )
        return stored

    def get_aggregate_edge(
        self, connection: ManagedConnection, edge_id: str
    ) -> AggregateKnowledgeEdgeRecord | None:
        connection = _connection(connection)
        edge_id = _uuid(edge_id, field="edge_id")
        row = connection.execute(
            f"SELECT {_columns(AggregateKnowledgeEdgeRecord)} "
            "FROM aggregate_knowledge_edge WHERE id=?",
            (edge_id,),
        ).fetchone()
        return None if row is None else _record(AggregateKnowledgeEdgeRecord, row)

    def list_aggregate_edges(
        self,
        connection: ManagedConnection,
        *,
        home_space_id: str,
        edge_status: AggregateEdgeStatus = AggregateEdgeStatus.ACTIVE,
        after_id: str | None = None,
        limit: int = 50,
    ) -> tuple[AggregateKnowledgeEdgeRecord, ...]:
        connection = _connection(connection)
        home_space_id = _uuid(home_space_id, field="home_space_id")
        if not isinstance(edge_status, AggregateEdgeStatus):
            raise TypeError("edge_status must be an AggregateEdgeStatus.")
        limit = _bounded_limit(limit, maximum=SEMANTIC_LEDGER_MAX_PAGE_SIZE)
        after = "00000000-0000-0000-0000-000000000000" if after_id is None else _uuid(after_id, field="after_id")
        rows = connection.execute(
            f"""SELECT {_columns(AggregateKnowledgeEdgeRecord)}
                  FROM aggregate_knowledge_edge
                 WHERE home_space_id=? AND edge_status=? AND id>?
                 ORDER BY id LIMIT ?""",
            (home_space_id, edge_status.value, after, limit),
        ).fetchall()
        return tuple(_record(AggregateKnowledgeEdgeRecord, row) for row in rows)

    def list_job_decisions(
        self, connection: ManagedConnection, *, job_id: str
    ) -> tuple[SemanticEdgeDecisionRecord, ...]:
        connection = _connection(connection)
        job_id = _uuid(job_id, field="job_id")
        rows = connection.execute(
            f"""SELECT {_columns(SemanticEdgeDecisionRecord)}
                  FROM semantic_edge_decision WHERE job_id=?
                 ORDER BY record_kind,ordinal,id
                 LIMIT ?""",
            (job_id, SEMANTIC_DECISION_MAX_ROWS_PER_JOB),
        ).fetchall()
        return tuple(_record(SemanticEdgeDecisionRecord, row) for row in rows)

    def get_suppression(
        self, connection: ManagedConnection, suppression_id: str
    ) -> SemanticEdgeSuppressionRecord | None:
        connection = _connection(connection)
        suppression_id = _uuid(suppression_id, field="suppression_id")
        row = connection.execute(
            f"SELECT {_columns(SemanticEdgeSuppressionRecord)} "
            "FROM semantic_edge_suppression WHERE id=?",
            (suppression_id,),
        ).fetchone()
        return None if row is None else _record(SemanticEdgeSuppressionRecord, row)

    def get_generation_vector(
        self, connection: ManagedConnection, selected_space_id: str
    ) -> AggregateGenerationVectorRecord | None:
        connection = _connection(connection)
        selected_space_id = _uuid(selected_space_id, field="selected_space_id")
        row = connection.execute(
            f"SELECT {_columns(AggregateGenerationVectorRecord)} "
            "FROM aggregate_generation_vector WHERE selected_space_id=?",
            (selected_space_id,),
        ).fetchone()
        return None if row is None else _record(AggregateGenerationVectorRecord, row)

    def get_reference(
        self, connection: ManagedConnection, reference_id: str
    ) -> ExplicitReferenceRecord | None:
        connection = _connection(connection)
        reference_id = _uuid(reference_id, field="reference_id")
        row = connection.execute(
            f"SELECT {_columns(ExplicitReferenceRecord)} "
            "FROM knowledge_explicit_reference WHERE id=?",
            (reference_id,),
        ).fetchone()
        return None if row is None else _record(ExplicitReferenceRecord, row)

    def append_reference(
        self,
        connection: ManagedConnection,
        record: ExplicitReferenceRecord,
    ) -> ExplicitReferenceRecord:
        connection = _write_connection(connection)
        if not isinstance(record, ExplicitReferenceRecord):
            raise TypeError("An ExplicitReferenceRecord is required.")
        columns = _columns(ExplicitReferenceRecord)
        placeholders = ",".join("?" for _ in fields(ExplicitReferenceRecord))
        try:
            cursor = connection.execute(
                f"INSERT INTO knowledge_explicit_reference({columns}) "
                f"VALUES ({placeholders}) ON CONFLICT(effect_key) DO NOTHING",
                _sql_values(record),
            )
        except sqlite3.IntegrityError as exc:
            raise SemanticGraphContractConflictError(
                "The explicit reference violates the v10 storage contract."
            ) from exc
        stored = connection.execute(
            f"SELECT {_columns(ExplicitReferenceRecord)} "
            "FROM knowledge_explicit_reference WHERE effect_key=?",
            (record.effect_key,),
        ).fetchone()
        mapped = None if stored is None else _record(ExplicitReferenceRecord, stored)
        if mapped is None or mapped != record:
            raise SemanticGraphContractConflictError(
                "The explicit reference effect key is bound incompatibly."
            )
        if cursor.rowcount not in (0, 1):
            raise SemanticGraphContractRepositoryError(
                "The explicit-reference insert returned an invalid row count."
            )
        return mapped

    def mark_target_references_unavailable(
        self,
        connection: ManagedConnection,
        *,
        target_item_id: str,
        reason: str,
        now_ms: int,
    ) -> int:
        """Hide every currently available stable reference to one target.

        The caller owns the target lifecycle/authorization transition and its
        audit/outbox records.  This bounded companion update must run in that
        same transaction so readers can never observe an inactive target with
        an apparently available explicit reference.
        """

        connection = _write_connection(connection)
        target_item_id = _uuid(target_item_id, field="target_item_id")
        if reason not in EXPLICIT_REFERENCE_UNAVAILABLE_REASONS:
            raise ValueError("reason is not a canonical unavailable-reference reason.")
        now_ms = _timestamp(now_ms, field="now_ms")
        row = connection.execute(
            """SELECT count(*) AS reference_count
                 FROM knowledge_explicit_reference
                WHERE target_item_id=? AND availability_status='available'""",
            (target_item_id,),
        ).fetchone()
        if row is None:
            raise SemanticGraphContractRepositoryError(
                "The explicit-reference availability count is unavailable."
            )
        expected = int(_value(row, "reference_count"))
        if expected == 0:
            return 0
        try:
            cursor = connection.execute(
                """UPDATE knowledge_explicit_reference
                      SET availability_status='unavailable',unavailable_reason=?,
                          updated_at=?,row_version=row_version+1
                    WHERE target_item_id=? AND availability_status='available'""",
                (reason, now_ms, target_item_id),
            )
        except sqlite3.IntegrityError as exc:
            raise SemanticGraphContractConflictError(
                "The explicit-reference availability transition is invalid."
            ) from exc
        if cursor.rowcount != expected:
            raise SemanticGraphContractConflictError(
                "The explicit-reference availability set changed during transition."
            )
        return expected

    def list_references(
        self,
        connection: ManagedConnection,
        *,
        source_revision_id: str,
        after_ordinal: int = 0,
        limit: int = 100,
    ) -> tuple[ExplicitReferenceRecord, ...]:
        connection = _connection(connection)
        source_revision_id = _uuid(source_revision_id, field="source_revision_id")
        if isinstance(after_ordinal, bool) or not isinstance(after_ordinal, int) or not 0 <= after_ordinal <= EXPLICIT_REFERENCE_MAX_ROWS_PER_REVISION:
            raise ValueError("after_ordinal is outside the reference range.")
        limit = _bounded_limit(limit, maximum=EXPLICIT_REFERENCE_MAX_ROWS_PER_REVISION)
        rows = connection.execute(
            f"""SELECT {_columns(ExplicitReferenceRecord)}
                  FROM knowledge_explicit_reference
                 WHERE source_revision_id=? AND ordinal>?
                 ORDER BY ordinal,id LIMIT ?""",
            (source_revision_id, after_ordinal, limit),
        ).fetchall()
        return tuple(_record(ExplicitReferenceRecord, row) for row in rows)


__all__ = [
    "AggregateGenerationVectorRecord",
    "AggregateKnowledgeEdgeRecord",
    "EXPLICIT_REFERENCE_MAX_ROWS_PER_REVISION",
    "EXPLICIT_REFERENCE_UNAVAILABLE_REASONS",
    "ExplicitReferenceRecord",
    "SEMANTIC_DECISION_MAX_ROWS_PER_JOB",
    "SEMANTIC_INTENT_BATCH_MAX_SIZE",
    "SEMANTIC_LEDGER_MAX_PAGE_SIZE",
    "SemanticEdgeDecisionRecord",
    "SemanticEdgeIntentRecord",
    "SemanticEdgeIntentSeedRunRecord",
    "SemanticEdgeSuppressionRecord",
    "SemanticGraphContractConflictError",
    "SemanticGraphContractRepository",
    "SemanticGraphContractRepositoryError",
    "SemanticLifecycleVector",
    "SemanticLifecycleVectorError",
    "derive_semantic_lifecycle_vector",
]

"""Connection-scoped v12 temporal assertion and verification primitives."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from ..db import ManagedConnection
from ..domain import (
    TemporalAssertionKind,
    TemporalAssertionOrigin,
    TemporalPrecision,
)
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


class TemporalRepositoryError(KnowledgeCoreDatabaseError):
    code = "TEMPORAL_REPOSITORY_ERROR"


class TemporalStateConflictError(TemporalRepositoryError):
    code = "TEMPORAL_HEAD_CONFLICT"


class TemporalItemUnavailableError(TemporalRepositoryError):
    code = "NOT_FOUND"


@dataclass(frozen=True, slots=True)
class TemporalItemRecord:
    space_id: str
    item_id: str
    revision_id: str
    revision_no: int
    registered_at_ms: int
    content_modified_at_ms: int
    content_generation: int
    temporal_generation: int
    space_row_version: int


@dataclass(frozen=True, slots=True)
class TemporalAssertionDraft:
    id: str
    space_id: str
    item_id: str
    revision_id: str
    assertion_kind: TemporalAssertionKind
    event_kind: str
    value_precision: TemporalPrecision | None
    instant_value_ms: int | None
    date_value: str | None
    source_timezone: str | None
    source_utc_offset_minutes: int | None
    origin: TemporalAssertionOrigin
    evidence_sha256: str
    evidence_reference: str
    predecessor_event_id: str | None
    head_generation: int
    actor_user_id: str
    operation_id: str
    effect_key: str
    temporal_summary_sha256: str
    audit_event_id: int
    outbox_event_id: int
    committed_at_ms: int


@dataclass(frozen=True, slots=True)
class TemporalAssertionRecord:
    event_sequence: int
    id: str
    space_id: str
    item_id: str
    revision_id: str
    assertion_kind: TemporalAssertionKind
    event_kind: str
    value_precision: TemporalPrecision | None
    instant_value_ms: int | None
    date_value: str | None
    source_timezone: str | None
    source_utc_offset_minutes: int | None
    origin: TemporalAssertionOrigin
    evidence_sha256: str
    evidence_reference: str
    predecessor_event_id: str | None
    head_generation: int
    actor_user_id: str
    operation_id: str
    effect_key: str
    temporal_summary_sha256: str
    audit_event_id: int
    outbox_event_id: int
    committed_at_ms: int


@dataclass(frozen=True, slots=True)
class TemporalHeadRecord:
    revision_id: str
    assertion_kind: TemporalAssertionKind
    space_id: str
    item_id: str
    head_event_id: str
    head_generation: int
    state: str
    temporal_summary_sha256: str
    updated_at_ms: int
    row_version: int


@dataclass(frozen=True, slots=True)
class VerificationDraft:
    id: str
    space_id: str
    item_id: str
    revision_id: str
    event_kind: str
    temporal_summary_sha256: str
    verifier_user_id: str
    authority_proof_sha256: str
    method: str
    evidence_sha256: str
    evidence_reference: str
    note: str | None
    subject_verified_at_ms: int | None
    predecessor_event_id: str | None
    head_generation: int
    operation_id: str
    effect_key: str
    audit_event_id: int
    outbox_event_id: int
    attested_at_ms: int


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    event_sequence: int
    id: str
    space_id: str
    item_id: str
    revision_id: str
    event_kind: str
    temporal_summary_sha256: str
    verifier_user_id: str
    authority_proof_sha256: str
    method: str
    evidence_sha256: str
    evidence_reference: str
    note: str | None
    subject_verified_at_ms: int | None
    predecessor_event_id: str | None
    head_generation: int
    operation_id: str
    effect_key: str
    audit_event_id: int
    outbox_event_id: int
    attested_at_ms: int


@dataclass(frozen=True, slots=True)
class VerificationHeadRecord:
    revision_id: str
    space_id: str
    item_id: str
    head_event_id: str
    head_generation: int
    state: str
    temporal_summary_sha256: str
    updated_at_ms: int
    row_version: int


def _connection(value: object, *, write: bool = False) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError("TemporalRepository requires a ManagedConnection.")
    if write and not value.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Temporal writes require a caller-owned transaction."
        )
    return value


def _assertion(row: object) -> TemporalAssertionRecord:
    try:
        precision = row["value_precision"]  # type: ignore[index]
        return TemporalAssertionRecord(
            event_sequence=int(row["event_sequence"]),  # type: ignore[index]
            id=str(row["id"]),  # type: ignore[index]
            space_id=str(row["space_id"]),  # type: ignore[index]
            item_id=str(row["item_id"]),  # type: ignore[index]
            revision_id=str(row["revision_id"]),  # type: ignore[index]
            assertion_kind=TemporalAssertionKind(row["assertion_kind"]),  # type: ignore[index]
            event_kind=str(row["event_kind"]),  # type: ignore[index]
            value_precision=(
                None if precision is None else TemporalPrecision(precision)
            ),
            instant_value_ms=row["instant_value_ms"],  # type: ignore[index,arg-type]
            date_value=row["date_value"],  # type: ignore[index,arg-type]
            source_timezone=row["source_timezone"],  # type: ignore[index,arg-type]
            source_utc_offset_minutes=row["source_utc_offset_minutes"],  # type: ignore[index,arg-type]
            origin=TemporalAssertionOrigin(row["origin"]),  # type: ignore[index]
            evidence_sha256=str(row["evidence_sha256"]),  # type: ignore[index]
            evidence_reference=str(row["evidence_reference"]),  # type: ignore[index]
            predecessor_event_id=row["predecessor_event_id"],  # type: ignore[index,arg-type]
            head_generation=int(row["head_generation"]),  # type: ignore[index]
            actor_user_id=str(row["actor_user_id"]),  # type: ignore[index]
            operation_id=str(row["operation_id"]),  # type: ignore[index]
            effect_key=str(row["effect_key"]),  # type: ignore[index]
            temporal_summary_sha256=str(row["temporal_summary_sha256"]),  # type: ignore[index]
            audit_event_id=int(row["audit_event_id"]),  # type: ignore[index]
            outbox_event_id=int(row["outbox_event_id"]),  # type: ignore[index]
            committed_at_ms=int(row["committed_at"]),  # type: ignore[index]
        )
    except (KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
        raise TemporalRepositoryError("A temporal assertion row is invalid.") from exc


def _temporal_head(row: object) -> TemporalHeadRecord:
    try:
        return TemporalHeadRecord(
            revision_id=str(row["revision_id"]),  # type: ignore[index]
            assertion_kind=TemporalAssertionKind(row["assertion_kind"]),  # type: ignore[index]
            space_id=str(row["space_id"]),  # type: ignore[index]
            item_id=str(row["item_id"]),  # type: ignore[index]
            head_event_id=str(row["head_event_id"]),  # type: ignore[index]
            head_generation=int(row["head_generation"]),  # type: ignore[index]
            state=str(row["state"]),  # type: ignore[index]
            temporal_summary_sha256=str(row["temporal_summary_sha256"]),  # type: ignore[index]
            updated_at_ms=int(row["updated_at"]),  # type: ignore[index]
            row_version=int(row["row_version"]),  # type: ignore[index]
        )
    except (KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
        raise TemporalRepositoryError("A temporal head row is invalid.") from exc


def _verification(row: object) -> VerificationRecord:
    try:
        return VerificationRecord(
            event_sequence=int(row["event_sequence"]),  # type: ignore[index]
            id=str(row["id"]),  # type: ignore[index]
            space_id=str(row["space_id"]),  # type: ignore[index]
            item_id=str(row["item_id"]),  # type: ignore[index]
            revision_id=str(row["revision_id"]),  # type: ignore[index]
            event_kind=str(row["event_kind"]),  # type: ignore[index]
            temporal_summary_sha256=str(row["temporal_summary_sha256"]),  # type: ignore[index]
            verifier_user_id=str(row["verifier_user_id"]),  # type: ignore[index]
            authority_proof_sha256=str(row["authority_proof_sha256"]),  # type: ignore[index]
            method=str(row["method"]),  # type: ignore[index]
            evidence_sha256=str(row["evidence_sha256"]),  # type: ignore[index]
            evidence_reference=str(row["evidence_reference"]),  # type: ignore[index]
            note=row["note"],  # type: ignore[index,arg-type]
            subject_verified_at_ms=row["subject_verified_at_ms"],  # type: ignore[index,arg-type]
            predecessor_event_id=row["predecessor_event_id"],  # type: ignore[index,arg-type]
            head_generation=int(row["head_generation"]),  # type: ignore[index]
            operation_id=str(row["operation_id"]),  # type: ignore[index]
            effect_key=str(row["effect_key"]),  # type: ignore[index]
            audit_event_id=int(row["audit_event_id"]),  # type: ignore[index]
            outbox_event_id=int(row["outbox_event_id"]),  # type: ignore[index]
            attested_at_ms=int(row["attested_at"]),  # type: ignore[index]
        )
    except (KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
        raise TemporalRepositoryError("A verification row is invalid.") from exc


def _verification_head(row: object) -> VerificationHeadRecord:
    try:
        return VerificationHeadRecord(
            revision_id=str(row["revision_id"]),  # type: ignore[index]
            space_id=str(row["space_id"]),  # type: ignore[index]
            item_id=str(row["item_id"]),  # type: ignore[index]
            head_event_id=str(row["head_event_id"]),  # type: ignore[index]
            head_generation=int(row["head_generation"]),  # type: ignore[index]
            state=str(row["state"]),  # type: ignore[index]
            temporal_summary_sha256=str(row["temporal_summary_sha256"]),  # type: ignore[index]
            updated_at_ms=int(row["updated_at"]),  # type: ignore[index]
            row_version=int(row["row_version"]),  # type: ignore[index]
        )
    except (KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
        raise TemporalRepositoryError("A verification head row is invalid.") from exc


_ASSERTION_COLUMNS = (
    "event_sequence,id,space_id,item_id,revision_id,assertion_kind,event_kind,"
    "value_precision,instant_value_ms,date_value,source_timezone,"
    "source_utc_offset_minutes,origin,evidence_sha256,evidence_reference,"
    "predecessor_event_id,head_generation,actor_user_id,operation_id,effect_key,"
    "temporal_summary_sha256,audit_event_id,outbox_event_id,committed_at"
)
_TEMPORAL_HEAD_COLUMNS = (
    "revision_id,assertion_kind,space_id,item_id,head_event_id,head_generation,"
    "state,temporal_summary_sha256,updated_at,row_version"
)
_VERIFICATION_COLUMNS = (
    "event_sequence,id,space_id,item_id,revision_id,event_kind,"
    "temporal_summary_sha256,verifier_user_id,authority_proof_sha256,method,"
    "evidence_sha256,evidence_reference,note,subject_verified_at_ms,"
    "predecessor_event_id,head_generation,operation_id,effect_key,audit_event_id,"
    "outbox_event_id,attested_at"
)
_VERIFICATION_HEAD_COLUMNS = (
    "revision_id,space_id,item_id,head_event_id,head_generation,state,"
    "temporal_summary_sha256,updated_at,row_version"
)


class TemporalRepository:
    __slots__ = ()

    def require_current_item(
        self, connection: ManagedConnection, *, space_id: str, item_id: str
    ) -> TemporalItemRecord:
        connection = _connection(connection)
        row = connection.execute(
            """SELECT i.space_id,i.id AS item_id,i.current_revision_id AS revision_id,
                      r.revision_no,i.created_at AS registered_at,
                      r.created_at AS content_modified_at,s.generation,
                      s.temporal_generation,s.row_version AS space_row_version
                 FROM knowledge_item i
                 JOIN knowledge_revision r ON r.id=i.current_revision_id
                 JOIN knowledge_space s ON s.id=i.space_id
                WHERE i.id=? AND i.space_id=? AND i.lifecycle_status='active'
                  AND i.curation_status='published' AND s.status='active'""",
            (item_id, space_id),
        ).fetchone()
        if row is None:
            raise TemporalItemUnavailableError(
                "The current published temporal item is unavailable."
            )
        try:
            return TemporalItemRecord(
                space_id=str(row["space_id"]),
                item_id=str(row["item_id"]),
                revision_id=str(row["revision_id"]),
                revision_no=int(row["revision_no"]),
                registered_at_ms=int(row["registered_at"]),
                content_modified_at_ms=int(row["content_modified_at"]),
                content_generation=int(row["generation"]),
                temporal_generation=int(row["temporal_generation"]),
                space_row_version=int(row["space_row_version"]),
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise TemporalRepositoryError("The temporal item row is invalid.") from exc

    def get_assertion_head(
        self,
        connection: ManagedConnection,
        *,
        revision_id: str,
        kind: TemporalAssertionKind,
    ) -> tuple[TemporalHeadRecord, TemporalAssertionRecord] | None:
        connection = _connection(connection)
        head_row = connection.execute(
            f"SELECT {_TEMPORAL_HEAD_COLUMNS} FROM revision_temporal_head "
            "WHERE revision_id=? AND assertion_kind=?",
            (revision_id, kind.value),
        ).fetchone()
        if head_row is None:
            return None
        head = _temporal_head(head_row)
        event = self.get_assertion(connection, head.head_event_id)
        if event is None:
            raise TemporalRepositoryError("The temporal head event is unavailable.")
        return head, event

    def get_assertion(
        self, connection: ManagedConnection, assertion_id: str
    ) -> TemporalAssertionRecord | None:
        connection = _connection(connection)
        row = connection.execute(
            f"SELECT {_ASSERTION_COLUMNS} FROM revision_temporal_assertion WHERE id=?",
            (assertion_id,),
        ).fetchone()
        return None if row is None else _assertion(row)

    def get_verification_head(
        self, connection: ManagedConnection, *, revision_id: str
    ) -> tuple[VerificationHeadRecord, VerificationRecord] | None:
        connection = _connection(connection)
        row = connection.execute(
            f"SELECT {_VERIFICATION_HEAD_COLUMNS} FROM revision_verification_head "
            "WHERE revision_id=?",
            (revision_id,),
        ).fetchone()
        if row is None:
            return None
        head = _verification_head(row)
        event = self.get_verification(connection, head.head_event_id)
        if event is None:
            raise TemporalRepositoryError("The verification head event is unavailable.")
        return head, event

    def get_verification(
        self, connection: ManagedConnection, attestation_id: str
    ) -> VerificationRecord | None:
        connection = _connection(connection)
        row = connection.execute(
            f"SELECT {_VERIFICATION_COLUMNS} "
            "FROM revision_verification_attestation WHERE id=?",
            (attestation_id,),
        ).fetchone()
        return None if row is None else _verification(row)

    def append_assertion(
        self, connection: ManagedConnection, draft: TemporalAssertionDraft
    ) -> TemporalAssertionRecord:
        connection = _connection(connection, write=True)
        if not isinstance(draft, TemporalAssertionDraft):
            raise TypeError("append_assertion requires a TemporalAssertionDraft.")
        try:
            cursor = connection.execute(
                """INSERT INTO revision_temporal_assertion(
                     id,space_id,item_id,revision_id,assertion_kind,event_kind,
                     value_precision,instant_value_ms,date_value,source_timezone,
                     source_utc_offset_minutes,origin,evidence_sha256,
                     evidence_reference,predecessor_event_id,head_generation,
                     actor_user_id,operation_id,effect_key,temporal_summary_sha256,
                     audit_event_id,outbox_event_id,committed_at
                   ) VALUES (?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?,?,?,?, ?,?,?)""",
                (
                    draft.id,
                    draft.space_id,
                    draft.item_id,
                    draft.revision_id,
                    draft.assertion_kind.value,
                    draft.event_kind,
                    None if draft.value_precision is None else draft.value_precision.value,
                    draft.instant_value_ms,
                    draft.date_value,
                    draft.source_timezone,
                    draft.source_utc_offset_minutes,
                    draft.origin.value,
                    draft.evidence_sha256,
                    draft.evidence_reference,
                    draft.predecessor_event_id,
                    draft.head_generation,
                    draft.actor_user_id,
                    draft.operation_id,
                    draft.effect_key,
                    draft.temporal_summary_sha256,
                    draft.audit_event_id,
                    draft.outbox_event_id,
                    draft.committed_at_ms,
                ),
            )
            if draft.head_generation == 1:
                head_cursor = connection.execute(
                    """INSERT INTO revision_temporal_head(
                         revision_id,assertion_kind,space_id,item_id,head_event_id,
                         head_generation,state,temporal_summary_sha256,updated_at,
                         row_version
                       ) VALUES (?,?,?,?,?,?, ?,?,?,1)""",
                    (
                        draft.revision_id,
                        draft.assertion_kind.value,
                        draft.space_id,
                        draft.item_id,
                        draft.id,
                        draft.head_generation,
                        "asserted" if draft.event_kind == "assert" else "revoked",
                        draft.temporal_summary_sha256,
                        draft.committed_at_ms,
                    ),
                )
            else:
                head_cursor = connection.execute(
                    """UPDATE revision_temporal_head
                          SET head_event_id=?,head_generation=?,state=?,
                              temporal_summary_sha256=?,updated_at=?,
                              row_version=row_version+1
                        WHERE revision_id=? AND assertion_kind=?
                          AND head_event_id=? AND head_generation=?""",
                    (
                        draft.id,
                        draft.head_generation,
                        "asserted" if draft.event_kind == "assert" else "revoked",
                        draft.temporal_summary_sha256,
                        draft.committed_at_ms,
                        draft.revision_id,
                        draft.assertion_kind.value,
                        draft.predecessor_event_id,
                        draft.head_generation - 1,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise TemporalStateConflictError(
                "The temporal assertion head changed or the event is invalid."
            ) from exc
        if cursor.rowcount != 1 or head_cursor.rowcount != 1:
            raise TemporalStateConflictError(
                "The temporal assertion did not advance exactly one head."
            )
        stored = self.get_assertion(connection, draft.id)
        if stored is None:
            raise TemporalRepositoryError("The temporal assertion was not stored.")
        return stored

    def append_verification(
        self, connection: ManagedConnection, draft: VerificationDraft
    ) -> VerificationRecord:
        connection = _connection(connection, write=True)
        if not isinstance(draft, VerificationDraft):
            raise TypeError("append_verification requires a VerificationDraft.")
        try:
            cursor = connection.execute(
                """INSERT INTO revision_verification_attestation(
                     id,space_id,item_id,revision_id,event_kind,
                     temporal_summary_sha256,verifier_actor_kind,verifier_user_id,
                     authority_proof_sha256,method,evidence_sha256,evidence_reference,
                     note,subject_verified_at_ms,predecessor_event_id,head_generation,
                     operation_id,effect_key,audit_event_id,outbox_event_id,attested_at
                   ) VALUES (?,?,?,?,?, ?,'user',?,?,?,?,?, ?,?,?,?,?,?,?,?,?)""",
                (
                    draft.id,
                    draft.space_id,
                    draft.item_id,
                    draft.revision_id,
                    draft.event_kind,
                    draft.temporal_summary_sha256,
                    draft.verifier_user_id,
                    draft.authority_proof_sha256,
                    draft.method,
                    draft.evidence_sha256,
                    draft.evidence_reference,
                    draft.note,
                    draft.subject_verified_at_ms,
                    draft.predecessor_event_id,
                    draft.head_generation,
                    draft.operation_id,
                    draft.effect_key,
                    draft.audit_event_id,
                    draft.outbox_event_id,
                    draft.attested_at_ms,
                ),
            )
            if draft.head_generation == 1:
                head_cursor = connection.execute(
                    """INSERT INTO revision_verification_head(
                         revision_id,space_id,item_id,head_event_id,head_generation,
                         state,temporal_summary_sha256,updated_at,row_version
                       ) VALUES (?,?,?,?,?, ?,?,?,1)""",
                    (
                        draft.revision_id,
                        draft.space_id,
                        draft.item_id,
                        draft.id,
                        draft.head_generation,
                        "attested" if draft.event_kind == "attest" else "revoked",
                        draft.temporal_summary_sha256,
                        draft.attested_at_ms,
                    ),
                )
            else:
                head_cursor = connection.execute(
                    """UPDATE revision_verification_head
                          SET head_event_id=?,head_generation=?,state=?,
                              temporal_summary_sha256=?,updated_at=?,
                              row_version=row_version+1
                        WHERE revision_id=? AND head_event_id=?
                          AND head_generation=?""",
                    (
                        draft.id,
                        draft.head_generation,
                        "attested" if draft.event_kind == "attest" else "revoked",
                        draft.temporal_summary_sha256,
                        draft.attested_at_ms,
                        draft.revision_id,
                        draft.predecessor_event_id,
                        draft.head_generation - 1,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise TemporalStateConflictError(
                "The verification head changed or the event is invalid."
            ) from exc
        if cursor.rowcount != 1 or head_cursor.rowcount != 1:
            raise TemporalStateConflictError(
                "The verification event did not advance exactly one head."
            )
        stored = self.get_verification(connection, draft.id)
        if stored is None:
            raise TemporalRepositoryError("The verification event was not stored.")
        return stored

    def advance_temporal_generation(
        self,
        connection: ManagedConnection,
        *,
        item: TemporalItemRecord,
        now_ms: int,
    ) -> int:
        connection = _connection(connection, write=True)
        cursor = connection.execute(
            """UPDATE knowledge_space
                  SET temporal_generation=temporal_generation+1,updated_at=?,
                      row_version=row_version+1
                WHERE id=? AND generation=? AND temporal_generation=?
                  AND row_version=?""",
            (
                now_ms,
                item.space_id,
                item.content_generation,
                item.temporal_generation,
                item.space_row_version,
            ),
        )
        if cursor.rowcount != 1:
            raise TemporalStateConflictError(
                "The temporal space generation changed before commit."
            )
        return item.temporal_generation + 1

    def invalidate_current_ancestors(
        self, connection: ManagedConnection, *, space_id: str, now_ms: int
    ) -> int:
        connection = _connection(connection, write=True)
        from .aggregate_graph import AggregateGraphRepository

        return len(
            AggregateGraphRepository().invalidate_current_ancestors(
                connection,
                source_space_id=space_id,
                now_ms=now_ms,
            )
        )

    def list_assertions(
        self,
        connection: ManagedConnection,
        *,
        revision_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[TemporalAssertionRecord, ...]:
        connection = _connection(connection)
        rows = connection.execute(
            f"""SELECT {_ASSERTION_COLUMNS} FROM revision_temporal_assertion
                 WHERE revision_id=? AND event_sequence>?
                 ORDER BY event_sequence,id LIMIT ?""",
            (revision_id, after_sequence, limit),
        ).fetchall()
        return tuple(_assertion(row) for row in rows)

    def list_verifications(
        self,
        connection: ManagedConnection,
        *,
        revision_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[VerificationRecord, ...]:
        connection = _connection(connection)
        rows = connection.execute(
            f"""SELECT {_VERIFICATION_COLUMNS}
                   FROM revision_verification_attestation
                  WHERE revision_id=? AND event_sequence>?
                  ORDER BY event_sequence,id LIMIT ?""",
            (revision_id, after_sequence, limit),
        ).fetchall()
        return tuple(_verification(row) for row in rows)


__all__ = [
    "TemporalAssertionDraft",
    "TemporalAssertionRecord",
    "TemporalHeadRecord",
    "TemporalItemRecord",
    "TemporalItemUnavailableError",
    "TemporalRepository",
    "TemporalRepositoryError",
    "TemporalStateConflictError",
    "VerificationDraft",
    "VerificationHeadRecord",
    "VerificationRecord",
]

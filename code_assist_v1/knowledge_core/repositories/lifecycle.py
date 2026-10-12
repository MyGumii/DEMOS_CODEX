"""Transactional item deactivate/restore SQL boundary.

The repository owns no connection or transaction.  It mutates one lifecycle
episode, the item aggregate, canonical search visibility, the persistent lease
fence when required, and the space generation.  It never writes a content
revision, revision body, semantic edge, deterministic item relation, audit, or
outbox row; the transaction-owning service composes the latter two event rows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Literal

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError
from .leases import (
    LeaseHeldRepositoryError,
    LeaseIntegrityError,
    LeaseItemNotFoundError,
    LeaseRepository,
)


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MAX_SQLITE_INTEGER = (1 << 63) - 1
_PURGE_DELAY_MS = 7_776_000_000


class LifecycleRepositoryError(KnowledgeCoreDatabaseError):
    code = "LIFECYCLE_REPOSITORY_ERROR"


class LifecycleItemNotFoundError(LifecycleRepositoryError):
    code = "LIFECYCLE_ITEM_NOT_FOUND"


class LifecycleStateConflictError(LifecycleRepositoryError):
    code = "LIFECYCLE_STATE_CONFLICT"

    def __init__(
        self,
        message: str,
        *,
        current_status: str,
        current_revision_id: str,
        current_row_version: int,
    ) -> None:
        super().__init__(message)
        self.current_status = current_status
        self.current_revision_id = current_revision_id
        self.current_row_version = current_row_version


class LifecycleIntegrityError(LifecycleRepositoryError):
    code = "LIFECYCLE_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class LifecycleEdgeCounts:
    active_incoming: int
    active_outgoing: int
    inactive_incoming: int
    inactive_outgoing: int
    total: int

    def as_dict(self) -> dict[str, int]:
        return {
            "active_incoming": self.active_incoming,
            "active_outgoing": self.active_outgoing,
            "inactive_incoming": self.inactive_incoming,
            "inactive_outgoing": self.inactive_outgoing,
            "total": self.total,
        }


@dataclass(frozen=True, slots=True)
class LifecycleReferenceCounts:
    active_incoming: int
    active_outgoing: int
    conversation_pins: int
    inactive_incoming: int
    inactive_outgoing: int
    total: int

    def as_dict(self) -> dict[str, int]:
        return {
            "active_incoming": self.active_incoming,
            "active_outgoing": self.active_outgoing,
            "conversation_pins": self.conversation_pins,
            "inactive_incoming": self.inactive_incoming,
            "inactive_outgoing": self.inactive_outgoing,
            "total": self.total,
        }


@dataclass(frozen=True, slots=True)
class RestoreSearchRequirement:
    item_id: str
    space_id: str
    item_row_version: int
    revision_id: str
    title: str
    content_sha256: str
    content_size_bytes: int
    content_blob_hash: str | None
    content_md: str | None


@dataclass(frozen=True, slots=True)
class RestoreSearchEvidence:
    item_id: str
    space_id: str
    item_row_version: int
    revision_id: str
    title: str
    content_sha256: str
    content_size_bytes: int
    content_blob_hash: str | None
    content_md: str


@dataclass(frozen=True, slots=True)
class DeactivateDraft:
    item_id: str
    space_id: str
    actor_user_id: str
    episode_id: str
    expected_item_row_version: int
    reason: str
    now_ms: int


@dataclass(frozen=True, slots=True)
class RestoreDraft:
    item_id: str
    space_id: str
    actor_user_id: str
    expected_item_row_version: int
    now_ms: int
    search_evidence: RestoreSearchEvidence | None


@dataclass(frozen=True, slots=True)
class DeactivateWriteResult:
    item_id: str
    revision_id: str
    episode_id: str
    episode_no: int
    item_row_version: int
    deactivated_at_ms: int
    purge_eligible_at_ms: int
    edge_counts: LifecycleEdgeCounts
    reference_counts: LifecycleReferenceCounts
    own_or_expired_lease_released: bool
    removed_search_document_count: int
    space_generation: int


@dataclass(frozen=True, slots=True)
class RestoreWriteResult:
    item_id: str
    revision_id: str
    episode_id: str
    episode_no: int
    item_row_version: int
    deactivated_at_ms: int
    restored_at_ms: int
    inactive_duration_seconds: int
    review_job_queued: Literal[False]
    restored_search_document_count: int
    space_generation: int


def _connection(value: object) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError("ItemLifecycleRepository requires a ManagedConnection.")
    return value


def _write_transaction(value: object) -> ManagedConnection:
    connection = _connection(value)
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Lifecycle mutation requires an existing managed transaction."
        )
    return connection


def _uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_ID_RE.fullmatch(value) is None:
        raise ValueError("actor_user_id must be a canonical user ID.")
    return value


def _positive(value: object, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= _MAX_SQLITE_INTEGER
    ):
        raise ValueError(f"{field} must be a positive SQLite integer.")
    return value


def _utc_ms(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_SQLITE_INTEGER
    ):
        raise ValueError("now_ms must be a non-negative SQLite integer.")
    return value


def _reason(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("A deactivation reason is required.")
    result = value.strip()
    if not 1 <= len(result) <= 2000:
        raise ValueError("A deactivation reason must contain 1..2000 characters.")
    return result


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LifecycleIntegrityError(f"Stored {field} is invalid.")
    return value


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise LifecycleIntegrityError(f"Stored {field} is invalid.")
    return value


def _prepare_deactivate(value: DeactivateDraft) -> DeactivateDraft:
    if not isinstance(value, DeactivateDraft):
        raise TypeError("deactivate requires a DeactivateDraft.")
    now = _utc_ms(value.now_ms)
    if now > _MAX_SQLITE_INTEGER - _PURGE_DELAY_MS:
        raise LifecycleIntegrityError("Purge eligibility exceeds SQLite time range.")
    return DeactivateDraft(
        item_id=_uuid(value.item_id, field="item_id"),
        space_id=_uuid(value.space_id, field="space_id"),
        actor_user_id=_user(value.actor_user_id),
        episode_id=_uuid(value.episode_id, field="episode_id"),
        expected_item_row_version=_positive(
            value.expected_item_row_version, field="expected_item_row_version"
        ),
        reason=_reason(value.reason),
        now_ms=now,
    )


def _prepare_restore(value: RestoreDraft) -> RestoreDraft:
    if not isinstance(value, RestoreDraft):
        raise TypeError("restore requires a RestoreDraft.")
    evidence = value.search_evidence
    if evidence is not None and not isinstance(evidence, RestoreSearchEvidence):
        raise TypeError("search_evidence must be verified restore evidence.")
    return RestoreDraft(
        item_id=_uuid(value.item_id, field="item_id"),
        space_id=_uuid(value.space_id, field="space_id"),
        actor_user_id=_user(value.actor_user_id),
        expected_item_row_version=_positive(
            value.expected_item_row_version, field="expected_item_row_version"
        ),
        now_ms=_utc_ms(value.now_ms),
        search_evidence=evidence,
    )


def _row_value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (IndexError, KeyError, TypeError) as exc:
        raise LifecycleIntegrityError("A lifecycle row could not be decoded.") from exc


def _item_row(
    connection: ManagedConnection, *, item_id: str, space_id: str
) -> object:
    row = connection.execute(
        """SELECT i.id,i.space_id,i.lifecycle_status,i.curation_status,
                  i.current_revision_id,i.latest_deactivation_episode_id,
                  i.row_version,r.title,r.raw_content_sha256,
                  r.normalized_content_sha256,r.content_size_bytes,
                  r.normalizer_version,b.content_md,b.content_blob_hash,
                  b.content_size_bytes AS body_size_bytes,
                  b.content_sha256 AS body_sha256,
                  s.item_id AS search_item_id,s.space_id AS search_space_id,
                  s.revision_id AS search_revision_id,s.title AS search_title,
                  s.content_md AS search_content_md
             FROM knowledge_item AS i
             LEFT JOIN knowledge_revision AS r
               ON r.id=i.current_revision_id AND r.item_id=i.id
             LEFT JOIN knowledge_revision_body AS b
               ON b.revision_id=i.current_revision_id AND b.item_id=i.id
             LEFT JOIN search_document AS s ON s.item_id=i.id
            WHERE i.id=? AND i.space_id=?""",
        (item_id, space_id),
    ).fetchone()
    if row is None:
        raise LifecycleItemNotFoundError(
            "The requested item was not found in this space."
        )
    revision = _row_value(row, "current_revision_id")
    if not isinstance(revision, str) or _UUID_RE.fullmatch(revision) is None:
        raise LifecycleIntegrityError("The lifecycle item has no current revision.")
    if _row_value(row, "title") is None or _row_value(row, "body_sha256") is None:
        raise LifecycleIntegrityError("The current revision body is unavailable.")
    raw_sha = _sha(_row_value(row, "raw_content_sha256"), field="revision raw SHA")
    body_sha = _sha(_row_value(row, "body_sha256"), field="revision body SHA")
    size = _integer(_row_value(row, "content_size_bytes"), field="revision size")
    body_size = _integer(_row_value(row, "body_size_bytes"), field="body size")
    if raw_sha != body_sha or size != body_size:
        raise LifecycleIntegrityError("Revision metadata and body evidence disagree.")
    content = _row_value(row, "content_md")
    blob_hash = _row_value(row, "content_blob_hash")
    if (content is None) == (blob_hash is None):
        raise LifecycleIntegrityError("The revision body storage shape is invalid.")
    if content is not None:
        if not isinstance(content, str):
            raise LifecycleIntegrityError("The inline revision body is invalid.")
        encoded = content.encode("utf-8")
        if len(encoded) != size or sha256(encoded).hexdigest() != raw_sha:
            raise LifecycleIntegrityError("The inline revision body hash is invalid.")
    elif _sha(blob_hash, field="body blob SHA") != raw_sha:
        raise LifecycleIntegrityError("The revision body blob binding is invalid.")
    _validate_search_shape(row)
    return row


def _validate_search_shape(row: object) -> None:
    lifecycle = _row_value(row, "lifecycle_status")
    curation = _row_value(row, "curation_status")
    visible = lifecycle == "active" and curation == "published"
    search_item = _row_value(row, "search_item_id")
    if not visible:
        if search_item is not None:
            raise LifecycleIntegrityError(
                "A non-visible lifecycle item retains a search document."
            )
        return
    if search_item is None:
        raise LifecycleIntegrityError("A visible lifecycle item has no search document.")
    content = _row_value(row, "search_content_md")
    if (
        search_item != _row_value(row, "id")
        or _row_value(row, "search_space_id") != _row_value(row, "space_id")
        or _row_value(row, "search_revision_id")
        != _row_value(row, "current_revision_id")
        or _row_value(row, "search_title") != _row_value(row, "title")
        or not isinstance(content, str)
    ):
        raise LifecycleIntegrityError("The canonical search document is inconsistent.")
    encoded = content.encode("utf-8")
    if (
        len(encoded) != _row_value(row, "content_size_bytes")
        or sha256(encoded).hexdigest() != _row_value(row, "raw_content_sha256")
    ):
        raise LifecycleIntegrityError("The canonical search content is inconsistent.")


def _state_conflict(row: object, message: str) -> LifecycleStateConflictError:
    return LifecycleStateConflictError(
        message,
        current_status=str(_row_value(row, "lifecycle_status")),
        current_revision_id=str(_row_value(row, "current_revision_id")),
        current_row_version=int(_row_value(row, "row_version")),
    )


def _edge_counts(connection: ManagedConnection, item_id: str) -> LifecycleEdgeCounts:
    row = connection.execute(
        """SELECT
             COALESCE(SUM(edge_status='active' AND target_item_id=?),0),
             COALESCE(SUM(edge_status='active' AND source_item_id=?),0),
             COALESCE(SUM(edge_status='inactive' AND target_item_id=?),0),
             COALESCE(SUM(edge_status='inactive' AND source_item_id=?),0),
             COUNT(*)
           FROM knowledge_edge
          WHERE source_item_id=? OR target_item_id=?""",
        (item_id, item_id, item_id, item_id, item_id, item_id),
    ).fetchone()
    if row is None:
        raise LifecycleIntegrityError("Edge impact counts are unavailable.")
    values = tuple(_integer(row[index], field="edge count") for index in range(5))
    if values[4] != sum(values[:4]):
        raise LifecycleIntegrityError("Edge impact counts do not reconcile.")
    return LifecycleEdgeCounts(*values)


def _reference_counts(
    connection: ManagedConnection, item_id: str
) -> LifecycleReferenceCounts:
    relation = connection.execute(
        """SELECT
             COALESCE(SUM(relation_status='active' AND target_item_id=?),0),
             COALESCE(SUM(relation_status='active' AND source_item_id=?),0),
             COALESCE(SUM(relation_status='inactive' AND target_item_id=?),0),
             COALESCE(SUM(relation_status='inactive' AND source_item_id=?),0),
             COUNT(*)
           FROM item_relation
          WHERE source_item_id=? OR target_item_id=?""",
        (item_id, item_id, item_id, item_id, item_id, item_id),
    ).fetchone()
    pins = connection.execute(
        "SELECT COUNT(*) FROM conversation_pin WHERE item_id=?", (item_id,)
    ).fetchone()
    if relation is None or pins is None:
        raise LifecycleIntegrityError("Reference impact counts are unavailable.")
    values = tuple(_integer(relation[index], field="reference count") for index in range(5))
    pin_count = _integer(pins[0], field="pin count")
    if values[4] != sum(values[:4]):
        raise LifecycleIntegrityError("Reference impact counts do not reconcile.")
    return LifecycleReferenceCounts(
        active_incoming=values[0],
        active_outgoing=values[1],
        conversation_pins=pin_count,
        inactive_incoming=values[2],
        inactive_outgoing=values[3],
        total=values[4] + pin_count,
    )


def _advance_space(
    connection: ManagedConnection, *, space_id: str, now_ms: int
) -> int:
    row = connection.execute(
        "SELECT status,generation,row_version FROM knowledge_space WHERE id=?",
        (space_id,),
    ).fetchone()
    if row is None or row[0] != "active":
        raise LifecycleIntegrityError("The lifecycle target space is not active.")
    generation = _integer(row[1], field="space generation")
    row_version = _positive(row[2], field="space row_version")
    if generation >= _MAX_SQLITE_INTEGER or row_version >= _MAX_SQLITE_INTEGER:
        raise LifecycleIntegrityError("The target space version is exhausted.")
    cursor = connection.execute(
        """UPDATE knowledge_space
              SET generation=generation+1,updated_at=?,row_version=row_version+1
            WHERE id=? AND status='active' AND generation=? AND row_version=?""",
        (now_ms, space_id, generation, row_version),
    )
    if cursor.rowcount != 1:
        raise LifecycleIntegrityError("The target space generation changed.")
    return generation + 1


def verify_restore_search_content(
    requirement: RestoreSearchRequirement, payload: bytes | str
) -> RestoreSearchEvidence:
    """Bind caller-loaded Markdown bytes to one frozen restore requirement."""

    if not isinstance(requirement, RestoreSearchRequirement):
        raise TypeError("A RestoreSearchRequirement is required.")
    if isinstance(payload, str):
        content = payload
        encoded = payload.encode("utf-8")
    elif isinstance(payload, bytes):
        encoded = bytes(payload)
        try:
            content = encoded.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise LifecycleIntegrityError(
                "The staged restore body is not valid UTF-8."
            ) from exc
    else:
        raise TypeError("Restore content must be bytes or text.")
    if (
        len(encoded) != requirement.content_size_bytes
        or sha256(encoded).hexdigest() != requirement.content_sha256
    ):
        raise LifecycleIntegrityError(
            "The staged restore body does not match the current revision."
        )
    if requirement.content_blob_hash is not None and (
        requirement.content_blob_hash != requirement.content_sha256
    ):
        raise LifecycleIntegrityError("The restore blob identity is inconsistent.")
    return RestoreSearchEvidence(
        item_id=requirement.item_id,
        space_id=requirement.space_id,
        item_row_version=requirement.item_row_version,
        revision_id=requirement.revision_id,
        title=requirement.title,
        content_sha256=requirement.content_sha256,
        content_size_bytes=requirement.content_size_bytes,
        content_blob_hash=requirement.content_blob_hash,
        content_md=content,
    )


class ItemLifecycleRepository:
    """Connection-scoped lifecycle transition and visibility operations."""

    __slots__ = ("_leases",)

    def __init__(self, leases: LeaseRepository | None = None) -> None:
        self._leases = leases or LeaseRepository()

    def restore_search_requirement(
        self,
        connection: ManagedConnection,
        *,
        item_id: str,
        space_id: str,
        expected_item_row_version: int,
    ) -> RestoreSearchRequirement | None:
        connection = _connection(connection)
        item_id = _uuid(item_id, field="item_id")
        space_id = _uuid(space_id, field="space_id")
        expected = _positive(
            expected_item_row_version, field="expected_item_row_version"
        )
        row = _item_row(connection, item_id=item_id, space_id=space_id)
        if (
            _row_value(row, "lifecycle_status") != "inactive"
            or _row_value(row, "row_version") != expected
            or _row_value(row, "curation_status") != "published"
        ):
            return None
        content = _row_value(row, "content_md")
        blob_hash = _row_value(row, "content_blob_hash")
        return RestoreSearchRequirement(
            item_id=item_id,
            space_id=space_id,
            item_row_version=expected,
            revision_id=str(_row_value(row, "current_revision_id")),
            title=str(_row_value(row, "title")),
            content_sha256=str(_row_value(row, "body_sha256")),
            content_size_bytes=int(_row_value(row, "body_size_bytes")),
            content_blob_hash=None if blob_hash is None else str(blob_hash),
            content_md=None if content is None else str(content),
        )

    def deactivate(
        self,
        connection: ManagedConnection,
        draft: DeactivateDraft,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> DeactivateWriteResult:
        connection = _write_transaction(connection)
        draft = _prepare_deactivate(draft)
        row = _item_row(connection, item_id=draft.item_id, space_id=draft.space_id)
        if (
            _row_value(row, "lifecycle_status") != "active"
            or _row_value(row, "row_version") != draft.expected_item_row_version
        ):
            raise _state_conflict(
                row, "The item state or expected row version changed."
            )
        edge_counts = _edge_counts(connection, draft.item_id)
        reference_counts = _reference_counts(connection, draft.item_id)
        try:
            released = self._leases.clear_for_lifecycle(
                connection,
                item_id=draft.item_id,
                space_id=draft.space_id,
                actor_user_id=draft.actor_user_id,
                now_ms=draft.now_ms,
            )
        except (LeaseItemNotFoundError, LeaseIntegrityError) as exc:
            raise LifecycleIntegrityError(str(exc)) from exc
        if failpoint is not None:
            failpoint("lifecycle.after_lease_clear")
        latest = _row_value(row, "latest_deactivation_episode_id")
        if latest is not None:
            prior = connection.execute(
                """SELECT id,restored_by,restored_at,purged_by,purged_at
                     FROM item_deactivation_episode WHERE id=? AND item_id=?""",
                (latest, draft.item_id),
            ).fetchone()
            if (
                prior is None
                or prior[1] is None
                or prior[2] is None
                or prior[3] is not None
                or prior[4] is not None
            ):
                raise LifecycleIntegrityError(
                    "The active item does not point to a closed restore episode."
                )
        next_row = connection.execute(
            """SELECT COALESCE(MAX(episode_no),0)+1
                 FROM item_deactivation_episode WHERE item_id=?""",
            (draft.item_id,),
        ).fetchone()
        if next_row is None:
            raise LifecycleIntegrityError("The next deactivation episode is unavailable.")
        episode_no = _positive(next_row[0], field="episode_no")
        purge_eligible = draft.now_ms + _PURGE_DELAY_MS
        connection.execute(
            """INSERT INTO item_deactivation_episode(
                 id,item_id,episode_no,deactivated_by,deactivated_at,reason,
                 purge_eligible_at,restored_by,restored_at,purged_by,purged_at
               ) VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,NULL)""",
            (
                draft.episode_id,
                draft.item_id,
                episode_no,
                draft.actor_user_id,
                draft.now_ms,
                draft.reason,
                purge_eligible,
            ),
        )
        if failpoint is not None:
            failpoint("lifecycle.after_episode")
        cursor = connection.execute(
            """UPDATE knowledge_item
                  SET lifecycle_status='inactive',latest_deactivation_episode_id=?,
                      updated_by=?,updated_at=?,row_version=row_version+1
                WHERE id=? AND space_id=? AND lifecycle_status='active'
                  AND row_version=? AND current_revision_id=?""",
            (
                draft.episode_id,
                draft.actor_user_id,
                draft.now_ms,
                draft.item_id,
                draft.space_id,
                draft.expected_item_row_version,
                _row_value(row, "current_revision_id"),
            ),
        )
        if cursor.rowcount != 1:
            raise LifecycleIntegrityError("The item deactivation compare-and-swap failed.")
        if failpoint is not None:
            failpoint("lifecycle.after_item_state")
        removed = connection.execute(
            "DELETE FROM search_document WHERE item_id=? AND space_id=?",
            (draft.item_id, draft.space_id),
        ).rowcount
        expected_removed = 1 if _row_value(row, "curation_status") == "published" else 0
        if removed != expected_removed:
            raise LifecycleIntegrityError("Search visibility removal was inconsistent.")
        if failpoint is not None:
            failpoint("lifecycle.after_search_visibility")
        generation = _advance_space(
            connection, space_id=draft.space_id, now_ms=draft.now_ms
        )
        current = _item_row(
            connection, item_id=draft.item_id, space_id=draft.space_id
        )
        if (
            _row_value(current, "lifecycle_status") != "inactive"
            or _row_value(current, "curation_status")
            != _row_value(row, "curation_status")
            or _row_value(current, "current_revision_id")
            != _row_value(row, "current_revision_id")
            or _row_value(current, "latest_deactivation_episode_id")
            != draft.episode_id
            or _row_value(current, "row_version")
            != draft.expected_item_row_version + 1
            or _edge_counts(connection, draft.item_id) != edge_counts
            or _reference_counts(connection, draft.item_id) != reference_counts
        ):
            raise LifecycleIntegrityError("The deactivation postcondition failed.")
        return DeactivateWriteResult(
            item_id=draft.item_id,
            revision_id=str(_row_value(row, "current_revision_id")),
            episode_id=draft.episode_id,
            episode_no=episode_no,
            item_row_version=draft.expected_item_row_version + 1,
            deactivated_at_ms=draft.now_ms,
            purge_eligible_at_ms=purge_eligible,
            edge_counts=edge_counts,
            reference_counts=reference_counts,
            own_or_expired_lease_released=released is not None,
            removed_search_document_count=removed,
            space_generation=generation,
        )

    def restore(
        self,
        connection: ManagedConnection,
        draft: RestoreDraft,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> RestoreWriteResult:
        connection = _write_transaction(connection)
        draft = _prepare_restore(draft)
        row = _item_row(connection, item_id=draft.item_id, space_id=draft.space_id)
        if (
            _row_value(row, "lifecycle_status") != "inactive"
            or _row_value(row, "row_version") != draft.expected_item_row_version
        ):
            raise _state_conflict(
                row, "The item state or expected row version changed."
            )
        lease = self._leases.require_item(
            connection, item_id=draft.item_id, space_id=draft.space_id
        ).lease
        if lease.held:
            raise LifecycleIntegrityError("An inactive item retains a held edit lease.")
        episode_id = _row_value(row, "latest_deactivation_episode_id")
        if not isinstance(episode_id, str) or _UUID_RE.fullmatch(episode_id) is None:
            raise LifecycleIntegrityError("The inactive item has no latest episode.")
        episode = connection.execute(
            """SELECT id,episode_no,deactivated_at,restored_by,restored_at,
                      purged_by,purged_at
                 FROM item_deactivation_episode
                WHERE id=? AND item_id=?""",
            (episode_id, draft.item_id),
        ).fetchone()
        maximum = connection.execute(
            "SELECT MAX(episode_no) FROM item_deactivation_episode WHERE item_id=?",
            (draft.item_id,),
        ).fetchone()
        if (
            episode is None
            or maximum is None
            or episode[1] != maximum[0]
            or episode[3] is not None
            or episode[4] is not None
            or episode[5] is not None
            or episode[6] is not None
        ):
            raise LifecycleIntegrityError("The latest deactivation episode is not open.")
        deactivated_at = _integer(episode[2], field="deactivated_at")
        if draft.now_ms < deactivated_at:
            raise LifecycleIntegrityError("Restore time precedes deactivation time.")
        published = _row_value(row, "curation_status") == "published"
        evidence = draft.search_evidence
        if published:
            if evidence is None:
                raise LifecycleIntegrityError(
                    "Published restore requires verified search content."
                )
            self._verify_restore_evidence(row, evidence, draft)
        elif evidence is not None:
            raise LifecycleIntegrityError(
                "A non-published restore supplied unexpected search content."
            )
        edge_counts = _edge_counts(connection, draft.item_id)
        reference_counts = _reference_counts(connection, draft.item_id)
        cursor = connection.execute(
            """UPDATE item_deactivation_episode
                  SET restored_by=?,restored_at=?
                WHERE id=? AND item_id=? AND restored_by IS NULL
                  AND restored_at IS NULL AND purged_by IS NULL AND purged_at IS NULL""",
            (draft.actor_user_id, draft.now_ms, episode_id, draft.item_id),
        )
        if cursor.rowcount != 1:
            raise LifecycleIntegrityError("The restore episode compare-and-swap failed.")
        if failpoint is not None:
            failpoint("lifecycle.after_episode")
        cursor = connection.execute(
            """UPDATE knowledge_item
                  SET lifecycle_status='active',updated_by=?,updated_at=?,
                      row_version=row_version+1
                WHERE id=? AND space_id=? AND lifecycle_status='inactive'
                  AND row_version=? AND current_revision_id=?
                  AND latest_deactivation_episode_id=?""",
            (
                draft.actor_user_id,
                draft.now_ms,
                draft.item_id,
                draft.space_id,
                draft.expected_item_row_version,
                _row_value(row, "current_revision_id"),
                episode_id,
            ),
        )
        if cursor.rowcount != 1:
            raise LifecycleIntegrityError("The item restore compare-and-swap failed.")
        if failpoint is not None:
            failpoint("lifecycle.after_item_state")
        restored_search = 0
        if evidence is not None:
            connection.execute(
                """INSERT INTO search_document(
                     item_id,space_id,revision_id,title,content_md,updated_at
                   ) VALUES (?,?,?,?,?,?)""",
                (
                    draft.item_id,
                    draft.space_id,
                    evidence.revision_id,
                    evidence.title,
                    evidence.content_md,
                    draft.now_ms,
                ),
            )
            restored_search = 1
        if failpoint is not None:
            failpoint("lifecycle.after_search_visibility")
        generation = _advance_space(
            connection, space_id=draft.space_id, now_ms=draft.now_ms
        )
        current = _item_row(
            connection, item_id=draft.item_id, space_id=draft.space_id
        )
        closed = connection.execute(
            """SELECT restored_by,restored_at,purged_by,purged_at
                 FROM item_deactivation_episode WHERE id=? AND item_id=?""",
            (episode_id, draft.item_id),
        ).fetchone()
        if (
            closed is None
            or tuple(closed) != (draft.actor_user_id, draft.now_ms, None, None)
            or _row_value(current, "lifecycle_status") != "active"
            or _row_value(current, "curation_status")
            != _row_value(row, "curation_status")
            or _row_value(current, "current_revision_id")
            != _row_value(row, "current_revision_id")
            or _row_value(current, "latest_deactivation_episode_id") != episode_id
            or _row_value(current, "row_version")
            != draft.expected_item_row_version + 1
            or _edge_counts(connection, draft.item_id) != edge_counts
            or _reference_counts(connection, draft.item_id) != reference_counts
        ):
            raise LifecycleIntegrityError("The restore postcondition failed.")
        return RestoreWriteResult(
            item_id=draft.item_id,
            revision_id=str(_row_value(row, "current_revision_id")),
            episode_id=episode_id,
            episode_no=_positive(episode[1], field="episode_no"),
            item_row_version=draft.expected_item_row_version + 1,
            deactivated_at_ms=deactivated_at,
            restored_at_ms=draft.now_ms,
            inactive_duration_seconds=(draft.now_ms - deactivated_at) // 1000,
            review_job_queued=False,
            restored_search_document_count=restored_search,
            space_generation=generation,
        )

    @staticmethod
    def _verify_restore_evidence(
        row: object, evidence: RestoreSearchEvidence, draft: RestoreDraft
    ) -> None:
        if (
            evidence.item_id != draft.item_id
            or evidence.space_id != draft.space_id
            or evidence.item_row_version != draft.expected_item_row_version
            or evidence.revision_id != _row_value(row, "current_revision_id")
            or evidence.title != _row_value(row, "title")
            or evidence.content_sha256 != _row_value(row, "body_sha256")
            or evidence.content_size_bytes != _row_value(row, "body_size_bytes")
            or evidence.content_blob_hash != _row_value(row, "content_blob_hash")
        ):
            raise LifecycleIntegrityError(
                "Verified restore content is stale or belongs to another item."
            )
        encoded = evidence.content_md.encode("utf-8")
        if (
            len(encoded) != evidence.content_size_bytes
            or sha256(encoded).hexdigest() != evidence.content_sha256
        ):
            raise LifecycleIntegrityError("Verified restore content hash is invalid.")


__all__ = [
    "DeactivateDraft",
    "DeactivateWriteResult",
    "ItemLifecycleRepository",
    "LifecycleEdgeCounts",
    "LifecycleIntegrityError",
    "LifecycleItemNotFoundError",
    "LifecycleReferenceCounts",
    "LifecycleRepositoryError",
    "LifecycleStateConflictError",
    "RestoreDraft",
    "RestoreSearchEvidence",
    "RestoreSearchRequirement",
    "RestoreWriteResult",
    "verify_restore_search_content",
]

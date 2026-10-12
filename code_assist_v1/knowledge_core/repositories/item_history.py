"""Bounded item-detail and immutable revision-history reads.

The repository owns neither a connection nor a transaction.  Callers must
authorize one concrete space in the same read transaction before using it.
Revision bodies are returned as immutable storage references; any filesystem
or blob I/O belongs to the service after the database snapshot has closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Literal

from ..db import ManagedConnection
from ..errors import KnowledgeCoreError
from .leases import LeaseIntegrityError, LeaseRecord, LeaseRepository


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_MAX_INT = 9_223_372_036_854_775_807
_LIFECYCLE_STATES = frozenset({"active", "inactive", "merged", "purged_tombstone"})
_CURATION_STATES = frozenset({"published", "provisional", "quarantined"})
_CHANGE_KINDS = frozenset({"initial", "edit", "merge", "import", "approved_update"})


class ItemHistoryError(KnowledgeCoreError):
    code = "INTERNAL_ERROR"
    http_status = 500
    retryable = False


class ItemHistoryNotFoundError(ItemHistoryError):
    code = "NOT_FOUND"
    http_status = 404


class ItemHistoryIntegrityError(ItemHistoryError):
    pass


@dataclass(frozen=True, slots=True)
class RevisionBodyReference:
    content_md: str | None
    content_blob_hash: str | None
    content_size_bytes: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ManagementRevisionRecord:
    id: str
    item_id: str
    revision_no: int
    parent_revision_id: str | None
    base_revision_id: str | None
    title: str
    content_sha256: str
    normalized_content_sha256: str
    content_size_bytes: int
    normalizer_version: str
    change_kind: str
    reason: str
    created_by_user_id: str
    created_by_display_name: str
    created_at_ms: int
    body: RevisionBodyReference | None

    @property
    def body_present(self) -> bool:
        return self.body is not None


@dataclass(frozen=True, slots=True)
class ManagementLeaseSummary:
    holder_user_id: str
    holder_display_name: str
    fencing_generation: int
    heartbeat_at_ms: int
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class ManagementEdgeCounts:
    active_incoming: int
    active_outgoing: int
    inactive_incoming: int
    inactive_outgoing: int
    total: int


@dataclass(frozen=True, slots=True)
class ManagementReferenceCounts:
    active_incoming: int
    active_outgoing: int
    conversation_pins: int
    inactive_incoming: int
    inactive_outgoing: int
    total: int


@dataclass(frozen=True, slots=True)
class ManagementDeactivationSummary:
    episode_id: str
    episode_no: int
    deactivated_by_user_id: str
    deactivated_by_display_name: str
    deactivated_at_ms: int
    reason: str
    purge_eligible_at_ms: int


@dataclass(frozen=True, slots=True)
class ManagementItemDetailRecord:
    id: str
    space_id: str
    lifecycle_status: str
    curation_status: str
    current_revision_id: str | None
    anchor_revision: ManagementRevisionRecord
    row_version: int
    created_at_ms: int
    updated_at_ms: int
    last_reviewed_at_ms: int | None
    lease: ManagementLeaseSummary | None
    edge_counts: ManagementEdgeCounts
    reference_counts: ManagementReferenceCounts
    deactivation: ManagementDeactivationSummary | None


def _connection(value: object) -> ManagedConnection:
    if not isinstance(value, ManagedConnection) or not value.in_transaction:
        raise TypeError("An active ManagedConnection transaction is required.")
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ItemHistoryIntegrityError(f"Stored {field} is invalid.")
    return value


def _optional_uuid(value: object, field: str) -> str | None:
    return None if value is None else _uuid(value, field)


def _request_uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _integer(value: object, field: str, *, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= _MAX_INT
    ):
        raise ItemHistoryIntegrityError(f"Stored {field} is invalid.")
    return value


def _request_positive(value: object, field: str, maximum: int = _MAX_INT) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise ValueError(f"{field} is invalid.")
    return value


def _request_nonnegative(value: object, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_INT
    ):
        raise ValueError(f"{field} is invalid.")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ItemHistoryIntegrityError(f"Stored {field} is invalid.")
    return value


def _text(value: object, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(
            (ord(character) < 32 and character not in {"\t", "\n", "\r"})
            or ord(character) == 127
            for character in value
        )
    ):
        raise ItemHistoryIntegrityError(f"Stored {field} is invalid.")
    return value


def _enum(value: object, field: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ItemHistoryIntegrityError(f"Stored {field} is invalid.")
    return value


def _user(value: object, field: str) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ItemHistoryIntegrityError(f"Stored {field} is invalid.")
    return value


def _row_value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (IndexError, KeyError, TypeError) as exc:
        raise ItemHistoryIntegrityError("A history row could not be decoded.") from exc


def _revision_columns(alias: str = "r") -> str:
    return f"""{alias}.id AS revision_id,{alias}.item_id AS revision_item_id,
              {alias}.revision_no,{alias}.parent_revision_id,{alias}.base_revision_id,
              {alias}.title,{alias}.raw_content_sha256,
              {alias}.normalized_content_sha256,{alias}.content_size_bytes,
              {alias}.normalizer_version,{alias}.change_kind,{alias}.change_reason,
              {alias}.created_by,{alias}.created_at AS revision_created_at,
              creator.display_name AS creator_display_name,
              body.revision_id AS body_revision_id,body.item_id AS body_item_id,
              body.content_md,body.content_blob_hash,
              body.content_size_bytes AS body_size_bytes,
              body.content_sha256 AS body_sha256"""


def _decode_revision(row: object, *, item_lifecycle: str) -> ManagementRevisionRecord:
    revision_id = _uuid(_row_value(row, "revision_id"), "revision ID")
    item_id = _uuid(_row_value(row, "revision_item_id"), "revision item ID")
    raw_sha = _sha(_row_value(row, "raw_content_sha256"), "revision content SHA")
    size = _integer(_row_value(row, "content_size_bytes"), "revision content size")
    body_id = _row_value(row, "body_revision_id")
    body: RevisionBodyReference | None = None
    if body_id is not None:
        if item_lifecycle == "purged_tombstone":
            raise ItemHistoryIntegrityError(
                "A purged tombstone still retains a revision body."
            )
        if (
            _uuid(body_id, "revision body ID") != revision_id
            or _uuid(_row_value(row, "body_item_id"), "revision body item ID")
            != item_id
        ):
            raise ItemHistoryIntegrityError("Stored revision body identity is invalid.")
        body_size = _integer(_row_value(row, "body_size_bytes"), "revision body size")
        body_sha = _sha(_row_value(row, "body_sha256"), "revision body SHA")
        content_md = _row_value(row, "content_md")
        blob_hash = _row_value(row, "content_blob_hash")
        if body_size != size or body_sha != raw_sha or (content_md is None) == (
            blob_hash is None
        ):
            raise ItemHistoryIntegrityError("Stored revision body evidence is invalid.")
        if content_md is not None:
            if not isinstance(content_md, str):
                raise ItemHistoryIntegrityError("Stored revision Markdown is invalid.")
            encoded = content_md.encode("utf-8")
            if len(encoded) != size or sha256(encoded).hexdigest() != raw_sha:
                raise ItemHistoryIntegrityError("Stored revision Markdown hash is invalid.")
            exact_blob_hash = None
        else:
            exact_blob_hash = _sha(blob_hash, "revision body blob SHA")
            if exact_blob_hash != raw_sha:
                raise ItemHistoryIntegrityError("Stored revision blob binding is invalid.")
        body = RevisionBodyReference(
            content_md=content_md,
            content_blob_hash=exact_blob_hash,
            content_size_bytes=body_size,
            content_sha256=body_sha,
        )
    elif item_lifecycle != "purged_tombstone":
        raise ItemHistoryIntegrityError("A non-purged revision body is unavailable.")
    return ManagementRevisionRecord(
        id=revision_id,
        item_id=item_id,
        revision_no=_integer(_row_value(row, "revision_no"), "revision number", positive=True),
        parent_revision_id=_optional_uuid(
            _row_value(row, "parent_revision_id"), "parent revision ID"
        ),
        base_revision_id=_optional_uuid(
            _row_value(row, "base_revision_id"), "base revision ID"
        ),
        title=_text(_row_value(row, "title"), "revision title", 500),
        content_sha256=raw_sha,
        normalized_content_sha256=_sha(
            _row_value(row, "normalized_content_sha256"),
            "normalized revision content SHA",
        ),
        content_size_bytes=size,
        normalizer_version=_text(
            _row_value(row, "normalizer_version"), "normalizer version", 120
        ),
        change_kind=_enum(
            _row_value(row, "change_kind"), "revision change kind", _CHANGE_KINDS
        ),
        reason=_text(_row_value(row, "change_reason"), "revision reason", 2000),
        created_by_user_id=_user(_row_value(row, "created_by"), "revision creator"),
        created_by_display_name=_text(
            _row_value(row, "creator_display_name"), "revision creator display name", 200
        ),
        created_at_ms=_integer(
            _row_value(row, "revision_created_at"), "revision created timestamp"
        ),
        body=body,
    )


class ItemHistoryRepository:
    """Read one item and its immutable history from an authorized snapshot."""

    __slots__ = ("_leases",)

    def __init__(self, leases: LeaseRepository | None = None) -> None:
        self._leases = leases or LeaseRepository()

    def get_item(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        item_id: str,
        now_ms: int,
    ) -> ManagementItemDetailRecord:
        connection = _connection(connection)
        space = _request_uuid(space_id, "space_id")
        item = _request_uuid(item_id, "item_id")
        now = _request_nonnegative(now_ms, "now_ms")
        row = connection.execute(
            f"""SELECT i.id,i.space_id,i.lifecycle_status,i.curation_status,
                       i.current_revision_id,i.latest_deactivation_episode_id,
                       i.row_version,i.created_at AS item_created_at,
                       i.updated_at AS item_updated_at,
                       (SELECT MAX(x.revision_no) FROM knowledge_revision AS x
                         WHERE x.item_id=i.id) AS latest_revision_no,
                       (SELECT MAX(f.reviewed_at) FROM curation_finding AS f
                         WHERE f.source_item_id=i.id AND f.reviewed_at IS NOT NULL)
                           AS last_reviewed_at,
                       {_revision_columns()}
                  FROM knowledge_item AS i
                  JOIN knowledge_revision AS r
                    ON r.item_id=i.id AND r.id=COALESCE(
                         i.current_revision_id,
                         (SELECT lr.id FROM knowledge_revision AS lr
                           WHERE lr.item_id=i.id
                           ORDER BY lr.revision_no DESC,lr.id DESC LIMIT 1)
                       )
                  JOIN app_user AS creator ON creator.id=r.created_by
                  LEFT JOIN knowledge_revision_body AS body
                    ON body.revision_id=r.id AND body.item_id=r.item_id
                 WHERE i.id=? AND i.space_id=?""",
            (item, space),
        ).fetchone()
        if row is None:
            raise ItemHistoryNotFoundError("The requested item was not found.")
        lifecycle = _enum(
            _row_value(row, "lifecycle_status"), "item lifecycle", _LIFECYCLE_STATES
        )
        curation = _enum(
            _row_value(row, "curation_status"), "item curation", _CURATION_STATES
        )
        current_revision_id = _optional_uuid(
            _row_value(row, "current_revision_id"), "current revision ID"
        )
        if (lifecycle == "purged_tombstone") != (current_revision_id is None):
            raise ItemHistoryIntegrityError("Stored item revision state is invalid.")
        revision = _decode_revision(row, item_lifecycle=lifecycle)
        latest_revision_no = _integer(
            _row_value(row, "latest_revision_no"), "latest revision number", positive=True
        )
        if revision.revision_no != latest_revision_no or (
            current_revision_id is not None and current_revision_id != revision.id
        ):
            raise ItemHistoryIntegrityError("Stored current revision is not the latest revision.")
        row_item = _uuid(_row_value(row, "id"), "item ID")
        row_space = _uuid(_row_value(row, "space_id"), "item space ID")
        if row_item != item or row_space != space or revision.item_id != item:
            raise ItemHistoryIntegrityError("Stored item escaped the requested space.")
        lease = self._lease_summary(connection, item_id=item, lifecycle=lifecycle, now_ms=now)
        deactivation = self._deactivation_summary(
            connection,
            item_id=item,
            episode_id=_row_value(row, "latest_deactivation_episode_id"),
            lifecycle=lifecycle,
        )
        last_reviewed = _row_value(row, "last_reviewed_at")
        return ManagementItemDetailRecord(
            id=row_item,
            space_id=row_space,
            lifecycle_status=lifecycle,
            curation_status=curation,
            current_revision_id=current_revision_id,
            anchor_revision=revision,
            row_version=_integer(_row_value(row, "row_version"), "item row version", positive=True),
            created_at_ms=_integer(_row_value(row, "item_created_at"), "item created timestamp"),
            updated_at_ms=_integer(_row_value(row, "item_updated_at"), "item updated timestamp"),
            last_reviewed_at_ms=(
                None
                if last_reviewed is None
                else _integer(last_reviewed, "last reviewed timestamp")
            ),
            lease=lease,
            edge_counts=self._edge_counts(connection, item),
            reference_counts=self._reference_counts(connection, item),
            deactivation=deactivation,
        )

    def list_revisions(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        item_id: str,
        before_revision_no: int | None = None,
        before_revision_id: str | None = None,
        limit: int = 51,
    ) -> tuple[ManagementRevisionRecord, ...]:
        connection = _connection(connection)
        space = _request_uuid(space_id, "space_id")
        item = _request_uuid(item_id, "item_id")
        limit = _request_positive(limit, "limit", 101)
        if (before_revision_no is None) != (before_revision_id is None):
            raise ValueError("Both revision cursor fields must be supplied together.")
        lifecycle = self._item_lifecycle(connection, space_id=space, item_id=item)
        clauses = ["r.item_id=?"]
        parameters: list[object] = [item]
        if before_revision_no is not None:
            number = _request_positive(before_revision_no, "before_revision_no")
            revision_id = _request_uuid(before_revision_id, "before_revision_id")
            clauses.append("(r.revision_no<? OR (r.revision_no=? AND r.id<?))")
            parameters.extend((number, number, revision_id))
        parameters.append(limit)
        rows = connection.execute(
            f"""SELECT {_revision_columns()}
                  FROM knowledge_revision AS r
                  JOIN app_user AS creator ON creator.id=r.created_by
                  LEFT JOIN knowledge_revision_body AS body
                    ON body.revision_id=r.id AND body.item_id=r.item_id
                 WHERE {' AND '.join(clauses)}
                 ORDER BY r.revision_no DESC,r.id DESC LIMIT ?""",
            tuple(parameters),
        ).fetchall()
        revisions = tuple(
            _decode_revision(row, item_lifecycle=lifecycle) for row in rows
        )
        if any(revision.item_id != item for revision in revisions):
            raise ItemHistoryIntegrityError("Stored revision escaped its item.")
        return revisions

    def get_revision(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        item_id: str,
        revision_id: str,
    ) -> ManagementRevisionRecord:
        connection = _connection(connection)
        space = _request_uuid(space_id, "space_id")
        item = _request_uuid(item_id, "item_id")
        revision = _request_uuid(revision_id, "revision_id")
        lifecycle = self._item_lifecycle(connection, space_id=space, item_id=item)
        row = connection.execute(
            f"""SELECT {_revision_columns()}
                  FROM knowledge_revision AS r
                  JOIN app_user AS creator ON creator.id=r.created_by
                  LEFT JOIN knowledge_revision_body AS body
                    ON body.revision_id=r.id AND body.item_id=r.item_id
                 WHERE r.id=? AND r.item_id=?""",
            (revision, item),
        ).fetchone()
        if row is None:
            raise ItemHistoryNotFoundError("The requested revision was not found.")
        result = _decode_revision(row, item_lifecycle=lifecycle)
        if result.id != revision or result.item_id != item:
            raise ItemHistoryIntegrityError("Stored revision identity is invalid.")
        return result

    @staticmethod
    def _item_lifecycle(
        connection: ManagedConnection, *, space_id: str, item_id: str
    ) -> str:
        row = connection.execute(
            "SELECT lifecycle_status FROM knowledge_item WHERE id=? AND space_id=?",
            (item_id, space_id),
        ).fetchone()
        if row is None:
            raise ItemHistoryNotFoundError("The requested item was not found.")
        return _enum(row[0], "item lifecycle", _LIFECYCLE_STATES)

    def _lease_summary(
        self,
        connection: ManagedConnection,
        *,
        item_id: str,
        lifecycle: str,
        now_ms: int,
    ) -> ManagementLeaseSummary | None:
        try:
            lease = self._leases.get(connection, item_id)
        except LeaseIntegrityError as exc:
            raise ItemHistoryIntegrityError(str(exc)) from exc
        if lease is None:
            raise ItemHistoryIntegrityError("The item lease row is unavailable.")
        if not lease.held or lease.expired(now_ms):
            return None
        if lifecycle != "active":
            raise ItemHistoryIntegrityError("A non-active item retains a live lease.")
        self._validate_live_lease(lease)
        row = connection.execute(
            "SELECT display_name FROM app_user WHERE id=?",
            (lease.holder_user_id,),
        ).fetchone()
        if row is None:
            raise ItemHistoryIntegrityError("The lease holder is unavailable.")
        return ManagementLeaseSummary(
            holder_user_id=_user(lease.holder_user_id, "lease holder"),
            holder_display_name=_text(row[0], "lease holder display name", 200),
            fencing_generation=_integer(
                lease.fencing_generation, "lease fencing generation", positive=True
            ),
            heartbeat_at_ms=_integer(lease.heartbeat_at_ms, "lease heartbeat timestamp"),
            expires_at_ms=_integer(lease.expires_at_ms, "lease expiry timestamp"),
        )

    @staticmethod
    def _validate_live_lease(lease: LeaseRecord) -> None:
        if (
            lease.holder_user_id is None
            or lease.editor_session_id is None
            or lease.token_hmac is None
            or lease.hmac_key_id is None
            or lease.base_item_row_version is None
            or lease.base_revision_id is None
            or lease.acquired_at_ms is None
            or lease.heartbeat_at_ms is None
            or lease.expires_at_ms is None
            or lease.absolute_expires_at_ms is None
        ):
            raise ItemHistoryIntegrityError("Stored live lease is incomplete.")

    @staticmethod
    def _edge_counts(
        connection: ManagedConnection, item_id: str
    ) -> ManagementEdgeCounts:
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
            raise ItemHistoryIntegrityError("Item edge impact is unavailable.")
        values = tuple(_integer(row[index], "edge count") for index in range(5))
        if values[4] != sum(values[:4]):
            raise ItemHistoryIntegrityError("Item edge impact does not reconcile.")
        return ManagementEdgeCounts(*values)

    @staticmethod
    def _reference_counts(
        connection: ManagedConnection, item_id: str
    ) -> ManagementReferenceCounts:
        row = connection.execute(
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
        pin = connection.execute(
            "SELECT COUNT(*) FROM conversation_pin WHERE item_id=?", (item_id,)
        ).fetchone()
        if row is None or pin is None:
            raise ItemHistoryIntegrityError("Item reference impact is unavailable.")
        values = tuple(_integer(row[index], "reference count") for index in range(5))
        pins = _integer(pin[0], "conversation pin count")
        if values[4] != sum(values[:4]):
            raise ItemHistoryIntegrityError("Item reference impact does not reconcile.")
        return ManagementReferenceCounts(
            active_incoming=values[0],
            active_outgoing=values[1],
            conversation_pins=pins,
            inactive_incoming=values[2],
            inactive_outgoing=values[3],
            total=values[4] + pins,
        )

    @staticmethod
    def _deactivation_summary(
        connection: ManagedConnection,
        *,
        item_id: str,
        episode_id: object,
        lifecycle: str,
    ) -> ManagementDeactivationSummary | None:
        if lifecycle != "inactive":
            return None
        exact_episode = _uuid(episode_id, "latest deactivation episode ID")
        row = connection.execute(
            """SELECT d.id,d.item_id,d.episode_no,d.deactivated_by,
                      d.deactivated_at,d.reason,d.purge_eligible_at,
                      d.restored_at,d.purged_at,u.display_name
                 FROM item_deactivation_episode AS d
                 JOIN app_user AS u ON u.id=d.deactivated_by
                WHERE d.id=? AND d.item_id=?""",
            (exact_episode, item_id),
        ).fetchone()
        if row is None or row[7] is not None or row[8] is not None:
            raise ItemHistoryIntegrityError("The active deactivation episode is invalid.")
        actor = _user(row[3], "deactivation actor")
        occurred = _integer(row[4], "deactivation timestamp")
        audits = connection.execute(
            """SELECT actor_user_id,actor_display_name
                 FROM audit_event
                WHERE item_id=? AND action='knowledge_item.deactivate'
                  AND occurred_at=?
                  AND json_extract(details_json,'$.episode_id')=?
                ORDER BY id LIMIT 2""",
            (item_id, occurred, exact_episode),
        ).fetchall()
        if len(audits) != 1 or audits[0][0] != actor:
            raise ItemHistoryIntegrityError("The deactivation audit projection is invalid.")
        display = _text(audits[0][1], "deactivation actor display snapshot", 200)
        return ManagementDeactivationSummary(
            episode_id=_uuid(row[0], "deactivation episode ID"),
            episode_no=_integer(row[2], "deactivation episode number", positive=True),
            deactivated_by_user_id=actor,
            deactivated_by_display_name=display,
            deactivated_at_ms=occurred,
            reason=_text(row[5], "deactivation reason", 2000),
            purge_eligible_at_ms=_integer(row[6], "purge eligibility timestamp"),
        )


__all__ = [
    "ItemHistoryError",
    "ItemHistoryIntegrityError",
    "ItemHistoryNotFoundError",
    "ItemHistoryRepository",
    "ManagementDeactivationSummary",
    "ManagementEdgeCounts",
    "ManagementItemDetailRecord",
    "ManagementLeaseSummary",
    "ManagementReferenceCounts",
    "ManagementRevisionRecord",
    "RevisionBodyReference",
]

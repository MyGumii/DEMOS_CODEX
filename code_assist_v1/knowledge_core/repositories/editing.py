"""Canonical immutable item-edit transaction primitives.

The repository owns only the revision/item/curation/search effect.  The P5.2
service owns the surrounding transaction, lease proof/release, durable rescan
enqueue, audit, outbox, and operation idempotency.  No method commits.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import re
import sqlite3

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MAX_MARKDOWN_CHARACTERS = 2_000_000


class ItemEditRepositoryError(KnowledgeCoreDatabaseError):
    code = "ITEM_EDIT_REPOSITORY_ERROR"


class ItemEditNotFoundError(ItemEditRepositoryError):
    code = "ITEM_NOT_FOUND"


class ItemEditConflictError(ItemEditRepositoryError):
    code = "ITEM_EDIT_CONFLICT"

    def __init__(
        self,
        message: str,
        *,
        current_revision_id: str | None = None,
        current_row_version: int | None = None,
    ) -> None:
        super().__init__(message)
        self.current_revision_id = current_revision_id
        self.current_row_version = current_row_version


class ItemEditIntegrityError(ItemEditRepositoryError):
    code = "ITEM_EDIT_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class ItemEditDraft:
    item_id: str
    space_id: str
    actor_user_id: str
    operation_id: str
    dedup_id: str
    revision_id: str
    transition_id: str
    rescan_job_id: str
    rescan_job_dedup_id: str
    expected_item_row_version: int
    expected_revision_id: str
    content_md: str
    raw_content_sha256: str
    normalized_content_sha256: str
    content_size_bytes: int
    normalizer_version: str
    metadata: Mapping[str, object]
    reason: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ItemEditWriteResult:
    item_id: str
    space_id: str
    base_revision_id: str
    revision_id: str
    transition_id: str
    rescan_job_id: str
    revision_no: int
    item_row_version: int
    space_generation: int
    obsoleted_finding_count: int
    obsoleted_proposal_count: int
    revalidation_edge_count: int
    removed_search_document_count: int


@dataclass(frozen=True, slots=True)
class _PreparedEdit:
    draft: ItemEditDraft
    metadata_json: str
    reason: str


def _require_transaction(connection: ManagedConnection) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("ItemEditRepository requires a ManagedConnection.")
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Item editing requires a caller-owned managed transaction."
        )
    return connection


def _uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _user_id(value: object) -> str:
    if not isinstance(value, str) or _USER_ID_RE.fullmatch(value) is None:
        raise ValueError("actor_user_id must be a canonical user ID.")
    return value


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _utc_ms(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("created_at_ms must be a non-negative integer.")
    return value


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")
    return value


def _canonical_object(value: object) -> str:
    if not isinstance(value, Mapping):
        raise ValueError("metadata must be a mapping.")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must be a finite JSON object.") from exc
    if not isinstance(json.loads(encoded), dict):
        raise ValueError("metadata must encode a JSON object.")
    return encoded


def _prepare(draft: ItemEditDraft) -> _PreparedEdit:
    if not isinstance(draft, ItemEditDraft):
        raise TypeError("apply requires an ItemEditDraft.")
    for field in (
        "item_id",
        "space_id",
        "operation_id",
        "dedup_id",
        "revision_id",
        "transition_id",
        "rescan_job_id",
        "rescan_job_dedup_id",
        "expected_revision_id",
    ):
        _uuid(getattr(draft, field), field=field)
    _user_id(draft.actor_user_id)
    _positive_int(draft.expected_item_row_version, field="expected_item_row_version")
    _utc_ms(draft.created_at_ms)
    if (
        not isinstance(draft.content_md, str)
        or not 1 <= len(draft.content_md) <= _MAX_MARKDOWN_CHARACTERS
        or "\x00" in draft.content_md
    ):
        raise ValueError("content_md must contain 1..2000000 non-NUL characters.")
    try:
        raw = draft.content_md.encode("utf-8")
        normalized = (
            draft.content_md.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        )
    except UnicodeEncodeError as exc:
        raise ValueError("content_md must be valid Unicode.") from exc
    if (
        _sha(draft.raw_content_sha256, field="raw_content_sha256")
        != sha256(raw).hexdigest()
        or _sha(
            draft.normalized_content_sha256,
            field="normalized_content_sha256",
        )
        != sha256(normalized).hexdigest()
        or draft.content_size_bytes != len(raw)
    ):
        raise ValueError("The Markdown bytes do not match their declared evidence.")
    if draft.normalizer_version != "markdown_lf_v1":
        raise ValueError("normalizer_version must be markdown_lf_v1.")
    if not isinstance(draft.reason, str) or not 1 <= len(draft.reason.strip()) <= 2000:
        raise ValueError("reason must contain 1..2000 non-blank characters.")
    return _PreparedEdit(
        draft=draft,
        metadata_json=_canonical_object(draft.metadata),
        reason=draft.reason.strip(),
    )


def _value(row: object, name: str) -> object:
    try:
        return row[name]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise ItemEditIntegrityError(
            "A canonical edit row could not be decoded."
        ) from exc


class ItemEditRepository:
    """Write and revalidate one immutable content revision without committing."""

    __slots__ = ()

    def apply(
        self,
        connection: ManagedConnection,
        draft: ItemEditDraft,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> ItemEditWriteResult:
        connection = _require_transaction(connection)
        prepared = _prepare(draft)
        value = prepared.draft

        item = connection.execute(
            """SELECT i.lifecycle_status,i.curation_status,i.current_revision_id,
                      i.row_version,r.title,b.content_sha256,b.content_size_bytes
                 FROM knowledge_item AS i
                 JOIN knowledge_revision AS r
                   ON r.id=i.current_revision_id AND r.item_id=i.id
                 JOIN knowledge_revision_body AS b
                   ON b.revision_id=r.id AND b.item_id=r.item_id
                WHERE i.id=? AND i.space_id=?""",
            (value.item_id, value.space_id),
        ).fetchone()
        if item is None:
            exists = connection.execute(
                "SELECT 1 FROM knowledge_item WHERE id=?", (value.item_id,)
            ).fetchone()
            if exists is None:
                raise ItemEditNotFoundError("The item does not exist.")
            raise ItemEditIntegrityError(
                "The item, space, current revision, and body are not a closed aggregate."
            )
        current_revision = str(_value(item, "current_revision_id"))
        current_version = int(_value(item, "row_version"))
        if str(_value(item, "lifecycle_status")) != "active":
            raise ItemEditConflictError(
                "Only an active item can be edited.",
                current_revision_id=current_revision,
                current_row_version=current_version,
            )
        if (
            current_revision != value.expected_revision_id
            or current_version != value.expected_item_row_version
        ):
            raise ItemEditConflictError(
                "The item revision or row version changed before editing.",
                current_revision_id=current_revision,
                current_row_version=current_version,
            )

        operation = connection.execute(
            """SELECT status FROM operation_dedup
                WHERE id=? AND actor_user_id=? AND operation_type='knowledge_item.edit'
                  AND operation_id=? AND status='in_progress'""",
            (
                value.dedup_id,
                value.actor_user_id,
                value.operation_id,
            ),
        ).fetchone()
        if operation is None:
            raise ItemEditIntegrityError(
                "The edit does not have its exact in-progress operation reservation."
            )
        job = connection.execute(
            """SELECT dedup_id,operation_id,actor_kind,actor_user_id,space_id,
                      job_type,handler_version,state,input_ref_json
                 FROM durable_job WHERE id=?""",
            (value.rescan_job_id,),
        ).fetchone()
        if job is None:
            raise ItemEditIntegrityError("The revision rescan job is missing.")
        try:
            job_input = json.loads(str(_value(job, "input_ref_json")))
        except (TypeError, ValueError) as exc:
            raise ItemEditIntegrityError(
                "The revision rescan input is invalid."
            ) from exc
        if (
            _value(job, "dedup_id") != value.rescan_job_dedup_id
            or _value(job, "operation_id") != value.operation_id
            or _value(job, "actor_kind") != "user"
            or _value(job, "actor_user_id") != value.actor_user_id
            or _value(job, "space_id") != value.space_id
            or _value(job, "job_type") != "curation.rescan"
            or _value(job, "handler_version") != "v1"
            or _value(job, "state") != "queued"
            or job_input
            != {
                "item_id": value.item_id,
                "revision_id": value.revision_id,
                "rule_version": "curation-rescan-v1",
            }
        ):
            raise ItemEditIntegrityError(
                "The revision rescan job is not bound to this edit."
            )

        collisions = connection.execute(
            """SELECT
                 EXISTS(SELECT 1 FROM knowledge_revision WHERE id=?) AS revision_exists,
                 EXISTS(SELECT 1 FROM item_curation_transition WHERE id=?) AS transition_exists""",
            (value.revision_id, value.transition_id),
        ).fetchone()
        if collisions is None or int(_value(collisions, "revision_exists")) != 0 or int(
            _value(collisions, "transition_exists")
        ) != 0:
            raise ItemEditConflictError(
                "A deterministic edit identity already belongs to another effect.",
                current_revision_id=current_revision,
                current_row_version=current_version,
            )
        revision_no_row = connection.execute(
            "SELECT coalesce(max(revision_no),0)+1 AS revision_no FROM knowledge_revision WHERE item_id=?",
            (value.item_id,),
        ).fetchone()
        if revision_no_row is None:
            raise ItemEditIntegrityError("The next revision number is unavailable.")
        revision_no = int(_value(revision_no_row, "revision_no"))
        if revision_no < 2:
            raise ItemEditIntegrityError("The item revision history is incomplete.")
        title = str(_value(item, "title"))
        from_status = str(_value(item, "curation_status"))
        result_version = current_version + 1

        try:
            connection.execute(
                """INSERT INTO knowledge_revision(
                     id,item_id,revision_no,parent_revision_id,base_revision_id,
                     title,raw_content_sha256,normalized_content_sha256,
                     content_size_bytes,normalizer_version,metadata_json,
                     change_kind,change_reason,created_by,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,'edit',?,?,?)""",
                (
                    value.revision_id,
                    value.item_id,
                    revision_no,
                    current_revision,
                    current_revision,
                    title,
                    value.raw_content_sha256,
                    value.normalized_content_sha256,
                    value.content_size_bytes,
                    value.normalizer_version,
                    prepared.metadata_json,
                    prepared.reason,
                    value.actor_user_id,
                    value.created_at_ms,
                ),
            )
            if failpoint is not None:
                failpoint("edit.after_revision_insert")
            connection.execute(
                """INSERT INTO knowledge_revision_body(
                     revision_id,item_id,content_md,content_blob_hash,
                     content_size_bytes,content_sha256,created_at
                   ) VALUES (?,?,?,NULL,?,?,?)""",
                (
                    value.revision_id,
                    value.item_id,
                    value.content_md,
                    value.content_size_bytes,
                    value.raw_content_sha256,
                    value.created_at_ms,
                ),
            )
            connection.execute(
                """INSERT INTO item_curation_transition(
                     id,item_id,space_id,transition_kind,from_status,to_status,
                     base_revision_id,result_revision_id,finding_id,rescan_job_id,
                     decision_job_id,dedup_id,operation_id,actor_kind,actor_user_id,
                     result_item_row_version,reason,created_at
                   ) VALUES (?,?,?,'revision_rescan',?,'provisional',?,?,NULL,?,
                     NULL,?,?,'user',?,?,?,?)""",
                (
                    value.transition_id,
                    value.item_id,
                    value.space_id,
                    from_status,
                    current_revision,
                    value.revision_id,
                    value.rescan_job_id,
                    value.dedup_id,
                    value.operation_id,
                    value.actor_user_id,
                    result_version,
                    prepared.reason,
                    value.created_at_ms,
                ),
            )
            cursor = connection.execute(
                """UPDATE knowledge_item
                      SET current_revision_id=?,curation_status='provisional',
                          last_curation_transition_id=?,updated_by=?,updated_at=?,
                          row_version=row_version+1
                    WHERE id=? AND space_id=? AND lifecycle_status='active'
                      AND current_revision_id=? AND row_version=?""",
                (
                    value.revision_id,
                    value.transition_id,
                    value.actor_user_id,
                    value.created_at_ms,
                    value.item_id,
                    value.space_id,
                    current_revision,
                    current_version,
                ),
            )
            if cursor.rowcount != 1:
                raise ItemEditConflictError(
                    "The item changed before its new revision could commit.",
                    current_revision_id=current_revision,
                    current_row_version=current_version,
                )
            if failpoint is not None:
                failpoint("edit.after_current_pointer_update")

            finding_cursor = connection.execute(
                """UPDATE curation_finding
                      SET status='obsolete',updated_at=?,row_version=row_version+1
                    WHERE space_id=? AND status IN ('pending','approved')
                      AND ((source_item_id=? AND source_revision_id=?)
                        OR (target_item_id=? AND target_revision_id=?))""",
                (
                    value.created_at_ms,
                    value.space_id,
                    value.item_id,
                    current_revision,
                    value.item_id,
                    current_revision,
                ),
            )
            proposal_cursor = connection.execute(
                """UPDATE change_proposal
                      SET state='obsolete',updated_by=?,updated_at=?,
                          row_version=row_version+1
                    WHERE state IN ('pending_review','approved')
                      AND EXISTS (
                        SELECT 1 FROM curation_finding AS f
                         WHERE f.id=change_proposal.finding_id
                           AND f.status='obsolete' AND f.space_id=?
                           AND ((f.source_item_id=? AND f.source_revision_id=?)
                             OR (f.target_item_id=? AND f.target_revision_id=?)))""",
                (
                    value.actor_user_id,
                    value.created_at_ms,
                    value.space_id,
                    value.item_id,
                    current_revision,
                    value.item_id,
                    current_revision,
                ),
            )
            edge_cursor = connection.execute(
                """UPDATE knowledge_edge
                      SET edge_status='inactive',needs_revalidation=1,
                          updated_by=CASE
                            WHEN origin='semantic_auto' THEN NULL ELSE ? END,
                          updated_at=?,row_version=row_version+1
                    WHERE space_id=? AND needs_revalidation=0
                      AND ((source_item_id=? AND source_evidence_revision_id=?)
                        OR (target_item_id=? AND target_evidence_revision_id=?))""",
                (
                    value.actor_user_id,
                    value.created_at_ms,
                    value.space_id,
                    value.item_id,
                    current_revision,
                    value.item_id,
                    current_revision,
                ),
            )
            aggregate_edge_cursor = connection.execute(
                """UPDATE aggregate_knowledge_edge
                      SET edge_status='needs_revalidation',deactivated_at=?,
                          updated_at=?,row_version=row_version+1
                    WHERE edge_status='active'
                      AND ((source_space_id=? AND source_item_id=?
                            AND source_revision_id=?)
                        OR (target_space_id=? AND target_item_id=?
                            AND target_revision_id=?))""",
                (
                    value.created_at_ms,
                    value.created_at_ms,
                    value.space_id,
                    value.item_id,
                    current_revision,
                    value.space_id,
                    value.item_id,
                    current_revision,
                ),
            )
            search_cursor = connection.execute(
                "DELETE FROM search_document WHERE item_id=? AND space_id=?",
                (value.item_id, value.space_id),
            )
            space_cursor = connection.execute(
                """UPDATE knowledge_space
                      SET generation=generation+1,updated_at=?,row_version=row_version+1
                    WHERE id=? AND status='active' AND write_enabled=1""",
                (value.created_at_ms, value.space_id),
            )
            if space_cursor.rowcount != 1:
                raise ItemEditConflictError(
                    "The target space changed before the edit could commit.",
                    current_revision_id=current_revision,
                    current_row_version=current_version,
                )
        except sqlite3.IntegrityError as exc:
            raise ItemEditIntegrityError(
                "The immutable edit violates the canonical schema."
            ) from exc

        current = connection.execute(
            """SELECT i.current_revision_id,i.curation_status,
                      i.last_curation_transition_id,i.row_version,s.generation,
                      EXISTS(SELECT 1 FROM search_document d WHERE d.item_id=i.id)
                        AS search_exists
                 FROM knowledge_item AS i
                 JOIN knowledge_space AS s ON s.id=i.space_id
                WHERE i.id=? AND i.space_id=?""",
            (value.item_id, value.space_id),
        ).fetchone()
        if (
            current is None
            or _value(current, "current_revision_id") != value.revision_id
            or _value(current, "curation_status") != "provisional"
            or _value(current, "last_curation_transition_id") != value.transition_id
            or int(_value(current, "row_version")) != result_version
            or int(_value(current, "search_exists")) != 0
        ):
            raise ItemEditIntegrityError("The immutable edit postcondition failed.")
        stale_findings = connection.execute(
            """SELECT count(*) FROM curation_finding
                WHERE space_id=? AND status IN ('pending','approved')
                  AND ((source_item_id=? AND source_revision_id=?)
                    OR (target_item_id=? AND target_revision_id=?))""",
            (
                value.space_id,
                value.item_id,
                current_revision,
                value.item_id,
                current_revision,
            ),
        ).fetchone()
        stale_edges = connection.execute(
            """SELECT count(*) FROM knowledge_edge
                WHERE space_id=? AND needs_revalidation=0
                  AND ((source_item_id=? AND source_evidence_revision_id=?)
                    OR (target_item_id=? AND target_evidence_revision_id=?))""",
            (
                value.space_id,
                value.item_id,
                current_revision,
                value.item_id,
                current_revision,
            ),
        ).fetchone()
        stale_aggregate_edges = connection.execute(
            """SELECT count(*) FROM aggregate_knowledge_edge
                WHERE edge_status='active'
                  AND ((source_space_id=? AND source_item_id=?
                        AND source_revision_id=?)
                    OR (target_space_id=? AND target_item_id=?
                        AND target_revision_id=?))""",
            (
                value.space_id,
                value.item_id,
                current_revision,
                value.space_id,
                value.item_id,
                current_revision,
            ),
        ).fetchone()
        if stale_findings is None or int(stale_findings[0]) != 0:
            raise ItemEditIntegrityError("A stale finding remains actionable.")
        if stale_edges is None or int(stale_edges[0]) != 0:
            raise ItemEditIntegrityError("A stale semantic edge remains valid.")
        if stale_aggregate_edges is None or int(stale_aggregate_edges[0]) != 0:
            raise ItemEditIntegrityError("A stale aggregate edge remains active.")
        return ItemEditWriteResult(
            item_id=value.item_id,
            space_id=value.space_id,
            base_revision_id=current_revision,
            revision_id=value.revision_id,
            transition_id=value.transition_id,
            rescan_job_id=value.rescan_job_id,
            revision_no=revision_no,
            item_row_version=result_version,
            space_generation=int(_value(current, "generation")),
            obsoleted_finding_count=finding_cursor.rowcount,
            obsoleted_proposal_count=proposal_cursor.rowcount,
            revalidation_edge_count=(
                edge_cursor.rowcount + aggregate_edge_cursor.rowcount
            ),
            removed_search_document_count=search_cursor.rowcount,
        )


__all__ = [
    "ItemEditConflictError",
    "ItemEditDraft",
    "ItemEditIntegrityError",
    "ItemEditNotFoundError",
    "ItemEditRepository",
    "ItemEditRepositoryError",
    "ItemEditWriteResult",
]

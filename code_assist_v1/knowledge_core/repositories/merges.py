"""Canonical transactional merge primitives.

The repository owns no connection or transaction.  It reads the exact fixed
source/target revisions used by the preview and applies one reviewed merge in
the caller's already-open managed transaction.  It never rewires a semantic
edge; affected edges are only made inactive and marked for revalidation.
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
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MAX_MARKDOWN_CHARACTERS = 2_000_000


class MergeRepositoryError(KnowledgeCoreDatabaseError):
    code = "MERGE_REPOSITORY_ERROR"


class MergeNotFoundError(MergeRepositoryError):
    code = "MERGE_ITEM_NOT_FOUND"


class MergeConflictError(MergeRepositoryError):
    code = "MERGE_CONFLICT"

    def __init__(
        self,
        message: str,
        *,
        item_id: str | None = None,
        current_revision_id: str | None = None,
        current_row_version: int | None = None,
    ) -> None:
        super().__init__(message)
        self.item_id = item_id
        self.current_revision_id = current_revision_id
        self.current_row_version = current_row_version


class MergeIntegrityError(MergeRepositoryError):
    code = "MERGE_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class MergeItemSnapshot:
    item_id: str
    space_id: str
    lifecycle_status: str
    curation_status: str
    current_revision_id: str
    row_version: int
    title: str
    raw_content_sha256: str
    normalized_content_sha256: str
    content_size_bytes: int
    normalizer_version: str
    content_md: str | None
    content_blob_hash: str | None

    def binding(self) -> dict[str, object]:
        return {
            "content_blob_hash": self.content_blob_hash,
            "content_size_bytes": self.content_size_bytes,
            "curation_status": self.curation_status,
            "current_revision_id": self.current_revision_id,
            "item_id": self.item_id,
            "lifecycle_status": self.lifecycle_status,
            "normalizer_version": self.normalizer_version,
            "normalized_content_sha256": self.normalized_content_sha256,
            "raw_content_sha256": self.raw_content_sha256,
            "row_version": self.row_version,
            "space_id": self.space_id,
            "title": self.title,
        }


@dataclass(frozen=True, slots=True)
class MergeEdgeSnapshot:
    id: str
    source_item_id: str
    target_item_id: str
    edge_type: str
    edge_status: str
    origin: str
    source_evidence_revision_id: str | None
    target_evidence_revision_id: str | None
    needs_revalidation: int
    row_version: int

    def binding(self) -> dict[str, object]:
        return {
            "edge_status": self.edge_status,
            "edge_type": self.edge_type,
            "id": self.id,
            "needs_revalidation": self.needs_revalidation,
            "origin": self.origin,
            "row_version": self.row_version,
            "source_evidence_revision_id": self.source_evidence_revision_id,
            "source_item_id": self.source_item_id,
            "target_evidence_revision_id": self.target_evidence_revision_id,
            "target_item_id": self.target_item_id,
        }

    def identity(self) -> tuple[object, ...]:
        return (
            self.id,
            self.source_item_id,
            self.target_item_id,
            self.edge_type,
            self.origin,
            self.source_evidence_revision_id,
            self.target_evidence_revision_id,
        )


@dataclass(frozen=True, slots=True)
class MergeReferenceSnapshot:
    id: str
    source_item_id: str
    source_revision_id: str
    target_item_id: str
    target_revision_id: str
    relation_type: str
    relation_status: str
    row_version: int

    def binding(self) -> dict[str, object]:
        return {
            "id": self.id,
            "relation_status": self.relation_status,
            "relation_type": self.relation_type,
            "row_version": self.row_version,
            "source_item_id": self.source_item_id,
            "source_revision_id": self.source_revision_id,
            "target_item_id": self.target_item_id,
            "target_revision_id": self.target_revision_id,
        }


@dataclass(frozen=True, slots=True)
class MergeReadSnapshot:
    source: MergeItemSnapshot
    target: MergeItemSnapshot
    edges: tuple[MergeEdgeSnapshot, ...]
    references: tuple[MergeReferenceSnapshot, ...]

    @property
    def edge_impact_sha256(self) -> str:
        return sha256_canonical([edge.binding() for edge in self.edges])

    @property
    def reference_impact_sha256(self) -> str:
        return sha256_canonical([row.binding() for row in self.references])

    def binding(self) -> dict[str, object]:
        return {
            "edge_impact_sha256": self.edge_impact_sha256,
            "reference_impact_sha256": self.reference_impact_sha256,
            "source": self.source.binding(),
            "target": self.target.binding(),
        }


@dataclass(frozen=True, slots=True)
class MergeApplyDraft:
    space_id: str
    actor_user_id: str
    operation_id: str
    dedup_id: str
    source_item_id: str
    source_revision_id: str
    source_expected_row_version: int
    target_item_id: str
    target_revision_id: str
    target_expected_row_version: int
    result_revision_id: str
    transition_id: str
    rescan_job_id: str
    rescan_job_dedup_id: str
    relation_id: str
    merge_event_id: str
    preview_sha256: str
    edge_impact_sha256: str
    reference_impact_sha256: str
    result_markdown: str
    raw_content_sha256: str
    normalized_content_sha256: str
    content_size_bytes: int
    normalizer_version: str
    metadata: Mapping[str, object]
    relation_evidence: Mapping[str, object]
    reason: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class MergeWriteResult:
    merge_event_id: str
    relation_id: str
    result_revision_id: str
    transition_id: str
    rescan_job_id: str
    source_row_version: int
    target_row_version: int
    revision_no: int
    space_generation: int
    obsoleted_finding_count: int
    obsoleted_proposal_count: int
    revalidation_edge_count: int
    removed_search_document_count: int


def _transaction(connection: ManagedConnection) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("MergeRepository requires a ManagedConnection.")
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Merge repository operations require a managed transaction."
        )
    return connection


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("actor_user_id must be a canonical user ID.")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")
    return value


def _positive(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _utc_ms(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("created_at_ms must be a non-negative integer.")
    return value


def canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ValueError("Merge evidence must be finite JSON.") from exc


def sha256_canonical(value: object) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def _canonical_object(value: object, field: str) -> str:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping.")
    encoded = canonical_json_bytes(dict(value)).decode("ascii")
    if not isinstance(json.loads(encoded), dict):
        raise ValueError(f"{field} must encode an object.")
    return encoded


def _row_value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise MergeIntegrityError("A canonical merge row could not be decoded.") from exc


def _read_item(
    connection: ManagedConnection,
    *,
    space_id: str,
    item_id: str,
    revision_id: str,
) -> MergeItemSnapshot:
    row = connection.execute(
        """SELECT i.id,i.space_id,i.lifecycle_status,i.curation_status,
                  i.current_revision_id,i.row_version,r.title,
                  r.raw_content_sha256,r.normalized_content_sha256,
                  r.content_size_bytes,r.normalizer_version,
                  b.content_md,b.content_blob_hash,b.content_size_bytes AS body_size,
                  b.content_sha256 AS body_sha
             FROM knowledge_item AS i
             JOIN knowledge_revision AS r
               ON r.id=i.current_revision_id AND r.item_id=i.id
             JOIN knowledge_revision_body AS b
               ON b.revision_id=r.id AND b.item_id=r.item_id
            WHERE i.id=? AND i.space_id=? AND r.id=?""",
        (item_id, space_id, revision_id),
    ).fetchone()
    if row is None:
        current = connection.execute(
            "SELECT current_revision_id,row_version FROM knowledge_item WHERE id=?",
            (item_id,),
        ).fetchone()
        if current is None:
            raise MergeNotFoundError("A merge item does not exist.")
        raise MergeConflictError(
            "A merge item no longer has the fixed current revision in this space.",
            item_id=item_id,
            current_revision_id=str(_row_value(current, "current_revision_id")),
            current_row_version=int(_row_value(current, "row_version")),
        )
    raw_sha = str(_row_value(row, "raw_content_sha256"))
    size = int(_row_value(row, "content_size_bytes"))
    if str(_row_value(row, "body_sha")) != raw_sha or int(
        _row_value(row, "body_size")
    ) != size:
        raise MergeIntegrityError("A fixed merge revision body is inconsistent.")
    content_md = _row_value(row, "content_md")
    blob_hash = _row_value(row, "content_blob_hash")
    if (content_md is None) == (blob_hash is None):
        raise MergeIntegrityError("A fixed merge revision body is not materializable.")
    return MergeItemSnapshot(
        item_id=str(_row_value(row, "id")),
        space_id=str(_row_value(row, "space_id")),
        lifecycle_status=str(_row_value(row, "lifecycle_status")),
        curation_status=str(_row_value(row, "curation_status")),
        current_revision_id=str(_row_value(row, "current_revision_id")),
        row_version=int(_row_value(row, "row_version")),
        title=str(_row_value(row, "title")),
        raw_content_sha256=_sha(raw_sha, "raw_content_sha256"),
        normalized_content_sha256=_sha(
            str(_row_value(row, "normalized_content_sha256")),
            "normalized_content_sha256",
        ),
        content_size_bytes=size,
        normalizer_version=str(_row_value(row, "normalizer_version")),
        content_md=None if content_md is None else str(content_md),
        content_blob_hash=None if blob_hash is None else _sha(blob_hash, "content_blob_hash"),
    )


class MergeRepository:
    """Read fixed merge evidence and atomically apply one reviewed merge."""

    __slots__ = ()

    def read_snapshot(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        source_item_id: str,
        source_revision_id: str,
        target_item_id: str,
        target_revision_id: str,
    ) -> MergeReadSnapshot:
        connection = _transaction(connection)
        space_id = _uuid(space_id, "space_id")
        source_item_id = _uuid(source_item_id, "source_item_id")
        source_revision_id = _uuid(source_revision_id, "source_revision_id")
        target_item_id = _uuid(target_item_id, "target_item_id")
        target_revision_id = _uuid(target_revision_id, "target_revision_id")
        if source_item_id == target_item_id:
            raise ValueError("A merge requires two different items.")
        source = _read_item(
            connection,
            space_id=space_id,
            item_id=source_item_id,
            revision_id=source_revision_id,
        )
        target = _read_item(
            connection,
            space_id=space_id,
            item_id=target_item_id,
            revision_id=target_revision_id,
        )
        edge_rows = connection.execute(
            """SELECT id,source_item_id,target_item_id,edge_type,edge_status,origin,
                      source_evidence_revision_id,target_evidence_revision_id,
                      needs_revalidation,row_version
                 FROM knowledge_edge
                WHERE space_id=? AND (
                      source_item_id IN (?,?) OR target_item_id IN (?,?))
                ORDER BY id""",
            (
                space_id,
                source_item_id,
                target_item_id,
                source_item_id,
                target_item_id,
            ),
        ).fetchall()
        edges = tuple(
            MergeEdgeSnapshot(
                id=str(_row_value(row, "id")),
                source_item_id=str(_row_value(row, "source_item_id")),
                target_item_id=str(_row_value(row, "target_item_id")),
                edge_type=str(_row_value(row, "edge_type")),
                edge_status=str(_row_value(row, "edge_status")),
                origin=str(_row_value(row, "origin")),
                source_evidence_revision_id=(
                    None
                    if _row_value(row, "source_evidence_revision_id") is None
                    else str(_row_value(row, "source_evidence_revision_id"))
                ),
                target_evidence_revision_id=(
                    None
                    if _row_value(row, "target_evidence_revision_id") is None
                    else str(_row_value(row, "target_evidence_revision_id"))
                ),
                needs_revalidation=int(_row_value(row, "needs_revalidation")),
                row_version=int(_row_value(row, "row_version")),
            )
            for row in edge_rows
        )
        reference_rows = connection.execute(
            """SELECT id,source_item_id,source_revision_id,target_item_id,
                      target_revision_id,relation_type,relation_status,row_version
                 FROM item_relation
                WHERE source_item_id IN (?,?) OR target_item_id IN (?,?)
                ORDER BY id""",
            (
                source_item_id,
                target_item_id,
                source_item_id,
                target_item_id,
            ),
        ).fetchall()
        references = tuple(
            MergeReferenceSnapshot(
                id=str(_row_value(row, "id")),
                source_item_id=str(_row_value(row, "source_item_id")),
                source_revision_id=str(_row_value(row, "source_revision_id")),
                target_item_id=str(_row_value(row, "target_item_id")),
                target_revision_id=str(_row_value(row, "target_revision_id")),
                relation_type=str(_row_value(row, "relation_type")),
                relation_status=str(_row_value(row, "relation_status")),
                row_version=int(_row_value(row, "row_version")),
            )
            for row in reference_rows
        )
        return MergeReadSnapshot(source, target, edges, references)

    def apply(
        self,
        connection: ManagedConnection,
        draft: MergeApplyDraft,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> MergeWriteResult:
        connection = _transaction(connection)
        if not isinstance(draft, MergeApplyDraft):
            raise TypeError("apply requires a MergeApplyDraft.")
        for field in (
            "space_id",
            "operation_id",
            "dedup_id",
            "source_item_id",
            "source_revision_id",
            "target_item_id",
            "target_revision_id",
            "result_revision_id",
            "transition_id",
            "rescan_job_id",
            "rescan_job_dedup_id",
            "relation_id",
            "merge_event_id",
        ):
            _uuid(getattr(draft, field), field)
        _user(draft.actor_user_id)
        _positive(draft.source_expected_row_version, "source_expected_row_version")
        _positive(draft.target_expected_row_version, "target_expected_row_version")
        for field in (
            "preview_sha256",
            "edge_impact_sha256",
            "reference_impact_sha256",
            "raw_content_sha256",
            "normalized_content_sha256",
        ):
            _sha(getattr(draft, field), field)
        _utc_ms(draft.created_at_ms)
        if draft.source_item_id == draft.target_item_id:
            raise ValueError("A merge requires two different items.")
        if (
            not isinstance(draft.result_markdown, str)
            or not 1 <= len(draft.result_markdown) <= _MAX_MARKDOWN_CHARACTERS
            or "\x00" in draft.result_markdown
        ):
            raise ValueError("result_markdown must contain 1..2000000 non-NUL characters.")
        try:
            raw = draft.result_markdown.encode("utf-8")
            normalized = draft.result_markdown.replace("\r\n", "\n").replace(
                "\r", "\n"
            ).encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("result_markdown must be valid Unicode.") from exc
        if (
            sha256(raw).hexdigest() != draft.raw_content_sha256
            or sha256(normalized).hexdigest() != draft.normalized_content_sha256
            or len(raw) != draft.content_size_bytes
            or draft.normalizer_version != "markdown_lf_v1"
        ):
            raise ValueError("The result Markdown does not match its evidence.")
        metadata_json = _canonical_object(draft.metadata, "metadata")
        evidence_json = _canonical_object(draft.relation_evidence, "relation_evidence")
        if not isinstance(draft.reason, str) or not 1 <= len(draft.reason.strip()) <= 2000:
            raise ValueError("reason must contain 1..2000 non-blank characters.")

        snapshot = self.read_snapshot(
            connection,
            space_id=draft.space_id,
            source_item_id=draft.source_item_id,
            source_revision_id=draft.source_revision_id,
            target_item_id=draft.target_item_id,
            target_revision_id=draft.target_revision_id,
        )
        if snapshot.source.lifecycle_status != "active":
            raise MergeConflictError(
                "The merge source is no longer active.",
                item_id=draft.source_item_id,
                current_revision_id=snapshot.source.current_revision_id,
                current_row_version=snapshot.source.row_version,
            )
        if snapshot.target.lifecycle_status != "active":
            raise MergeConflictError(
                "The merge target is no longer active.",
                item_id=draft.target_item_id,
                current_revision_id=snapshot.target.current_revision_id,
                current_row_version=snapshot.target.row_version,
            )
        if (
            snapshot.source.row_version != draft.source_expected_row_version
            or snapshot.target.row_version != draft.target_expected_row_version
        ):
            changed = (
                snapshot.source
                if snapshot.source.row_version != draft.source_expected_row_version
                else snapshot.target
            )
            raise MergeConflictError(
                "A merge item row version changed after preview.",
                item_id=changed.item_id,
                current_revision_id=changed.current_revision_id,
                current_row_version=changed.row_version,
            )
        if (
            snapshot.edge_impact_sha256 != draft.edge_impact_sha256
            or snapshot.reference_impact_sha256 != draft.reference_impact_sha256
        ):
            raise MergeConflictError("Merge edge or reference impact changed after preview.")

        operation = connection.execute(
            """SELECT status FROM operation_dedup
                WHERE id=? AND actor_user_id=?
                  AND operation_type='knowledge_item.merge'
                  AND operation_id=? AND status='in_progress'""",
            (draft.dedup_id, draft.actor_user_id, draft.operation_id),
        ).fetchone()
        if operation is None:
            raise MergeIntegrityError("The merge operation reservation is missing.")
        job = connection.execute(
            """SELECT dedup_id,operation_id,actor_kind,actor_user_id,space_id,
                      job_type,handler_version,state,input_ref_json
                 FROM durable_job WHERE id=?""",
            (draft.rescan_job_id,),
        ).fetchone()
        if job is None:
            raise MergeIntegrityError("The target revision rescan job is missing.")
        try:
            job_input = json.loads(str(_row_value(job, "input_ref_json")))
        except (TypeError, ValueError) as exc:
            raise MergeIntegrityError("The target rescan input is invalid.") from exc
        if (
            _row_value(job, "dedup_id") != draft.rescan_job_dedup_id
            or _row_value(job, "operation_id") != draft.operation_id
            or _row_value(job, "actor_kind") != "user"
            or _row_value(job, "actor_user_id") != draft.actor_user_id
            or _row_value(job, "space_id") != draft.space_id
            or _row_value(job, "job_type") != "curation.rescan"
            or _row_value(job, "handler_version") != "v1"
            or _row_value(job, "state") != "queued"
            or job_input
            != {
                "item_id": draft.target_item_id,
                "revision_id": draft.result_revision_id,
                "rule_version": "curation-rescan-v1",
            }
        ):
            raise MergeIntegrityError("The target rescan job is not bound to this merge.")
        collisions = connection.execute(
            """SELECT
                 EXISTS(SELECT 1 FROM knowledge_revision WHERE id=?) AS revision_exists,
                 EXISTS(SELECT 1 FROM item_curation_transition WHERE id=?) AS transition_exists,
                 EXISTS(SELECT 1 FROM item_relation WHERE id=?) AS relation_exists,
                 EXISTS(SELECT 1 FROM merge_event WHERE id=?) AS event_exists""",
            (
                draft.result_revision_id,
                draft.transition_id,
                draft.relation_id,
                draft.merge_event_id,
            ),
        ).fetchone()
        if collisions is None or any(int(collisions[index]) != 0 for index in range(4)):
            raise MergeConflictError("A deterministic merge identity is already in use.")
        revision_row = connection.execute(
            "SELECT coalesce(max(revision_no),0)+1 FROM knowledge_revision WHERE item_id=?",
            (draft.target_item_id,),
        ).fetchone()
        if revision_row is None or int(revision_row[0]) < 2:
            raise MergeIntegrityError("The target revision history is incomplete.")
        revision_no = int(revision_row[0])
        target_result_version = snapshot.target.row_version + 1
        source_result_version = snapshot.source.row_version + 1
        edge_identities = tuple(edge.identity() for edge in snapshot.edges)

        try:
            connection.execute(
                """INSERT INTO knowledge_revision(
                     id,item_id,revision_no,parent_revision_id,base_revision_id,title,
                     raw_content_sha256,normalized_content_sha256,content_size_bytes,
                     normalizer_version,metadata_json,change_kind,change_reason,
                     created_by,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,'merge',?,?,?)""",
                (
                    draft.result_revision_id,
                    draft.target_item_id,
                    revision_no,
                    draft.target_revision_id,
                    draft.target_revision_id,
                    snapshot.target.title,
                    draft.raw_content_sha256,
                    draft.normalized_content_sha256,
                    draft.content_size_bytes,
                    draft.normalizer_version,
                    metadata_json,
                    draft.reason.strip(),
                    draft.actor_user_id,
                    draft.created_at_ms,
                ),
            )
            connection.execute(
                """INSERT INTO knowledge_revision_body(
                     revision_id,item_id,content_md,content_blob_hash,
                     content_size_bytes,content_sha256,created_at
                   ) VALUES (?,?,?,NULL,?,?,?)""",
                (
                    draft.result_revision_id,
                    draft.target_item_id,
                    draft.result_markdown,
                    draft.content_size_bytes,
                    draft.raw_content_sha256,
                    draft.created_at_ms,
                ),
            )
            if failpoint is not None:
                failpoint("merge.after_result_revision")
            connection.execute(
                """INSERT INTO item_curation_transition(
                     id,item_id,space_id,transition_kind,from_status,to_status,
                     base_revision_id,result_revision_id,finding_id,rescan_job_id,
                     decision_job_id,dedup_id,operation_id,actor_kind,actor_user_id,
                     result_item_row_version,reason,created_at
                   ) VALUES (?,?,?,'revision_rescan',?,'provisional',?,?,NULL,?,NULL,
                     ?,?,'user',?,?,?,?)""",
                (
                    draft.transition_id,
                    draft.target_item_id,
                    draft.space_id,
                    snapshot.target.curation_status,
                    draft.target_revision_id,
                    draft.result_revision_id,
                    draft.rescan_job_id,
                    draft.dedup_id,
                    draft.operation_id,
                    draft.actor_user_id,
                    target_result_version,
                    draft.reason.strip(),
                    draft.created_at_ms,
                ),
            )
            target_cursor = connection.execute(
                """UPDATE knowledge_item
                      SET current_revision_id=?,curation_status='provisional',
                          last_curation_transition_id=?,updated_by=?,updated_at=?,
                          row_version=row_version+1
                    WHERE id=? AND space_id=? AND lifecycle_status='active'
                      AND current_revision_id=? AND row_version=?""",
                (
                    draft.result_revision_id,
                    draft.transition_id,
                    draft.actor_user_id,
                    draft.created_at_ms,
                    draft.target_item_id,
                    draft.space_id,
                    draft.target_revision_id,
                    draft.target_expected_row_version,
                ),
            )
            if target_cursor.rowcount != 1:
                raise MergeConflictError("The merge target changed before commit.")

            finding_cursor = connection.execute(
                """UPDATE curation_finding
                      SET status='obsolete',updated_at=?,row_version=row_version+1
                    WHERE space_id=? AND status IN ('pending','approved') AND (
                      (source_item_id=? AND source_revision_id=?) OR
                      (target_item_id=? AND target_revision_id=?) OR
                      (source_item_id=? AND source_revision_id=?) OR
                      (target_item_id=? AND target_revision_id=?))""",
                (
                    draft.created_at_ms,
                    draft.space_id,
                    draft.source_item_id,
                    draft.source_revision_id,
                    draft.source_item_id,
                    draft.source_revision_id,
                    draft.target_item_id,
                    draft.target_revision_id,
                    draft.target_item_id,
                    draft.target_revision_id,
                ),
            )
            proposal_cursor = connection.execute(
                """UPDATE change_proposal
                      SET state='obsolete',updated_by=?,updated_at=?,row_version=row_version+1
                    WHERE state IN ('pending_review','approved') AND EXISTS (
                      SELECT 1 FROM curation_finding f
                       WHERE f.id=change_proposal.finding_id AND f.status='obsolete'
                         AND f.space_id=? AND (
                          (f.source_item_id=? AND f.source_revision_id=?) OR
                          (f.target_item_id=? AND f.target_revision_id=?) OR
                          (f.source_item_id=? AND f.source_revision_id=?) OR
                          (f.target_item_id=? AND f.target_revision_id=?)))""",
                (
                    draft.actor_user_id,
                    draft.created_at_ms,
                    draft.space_id,
                    draft.source_item_id,
                    draft.source_revision_id,
                    draft.source_item_id,
                    draft.source_revision_id,
                    draft.target_item_id,
                    draft.target_revision_id,
                    draft.target_item_id,
                    draft.target_revision_id,
                ),
            )
            edge_cursor = connection.execute(
                """UPDATE knowledge_edge
                      SET edge_status='inactive',needs_revalidation=1,
                          updated_by=?,updated_at=?,row_version=row_version+1
                    WHERE space_id=? AND needs_revalidation=0 AND (
                      source_item_id IN (?,?) OR target_item_id IN (?,?))""",
                (
                    draft.actor_user_id,
                    draft.created_at_ms,
                    draft.space_id,
                    draft.source_item_id,
                    draft.target_item_id,
                    draft.source_item_id,
                    draft.target_item_id,
                ),
            )
            search_cursor = connection.execute(
                "DELETE FROM search_document WHERE space_id=? AND item_id IN (?,?)",
                (draft.space_id, draft.source_item_id, draft.target_item_id),
            )
            source_cursor = connection.execute(
                """UPDATE knowledge_item
                      SET lifecycle_status='merged',redirect_item_id=?,updated_by=?,
                          updated_at=?,row_version=row_version+1
                    WHERE id=? AND space_id=? AND lifecycle_status='active'
                      AND current_revision_id=? AND row_version=?""",
                (
                    draft.target_item_id,
                    draft.actor_user_id,
                    draft.created_at_ms,
                    draft.source_item_id,
                    draft.space_id,
                    draft.source_revision_id,
                    draft.source_expected_row_version,
                ),
            )
            if source_cursor.rowcount != 1:
                raise MergeConflictError("The merge source changed before commit.")
            if failpoint is not None:
                failpoint("merge.after_source_state")
            connection.execute(
                """INSERT INTO item_relation(
                     id,source_item_id,source_revision_id,target_item_id,
                     target_revision_id,relation_type,relation_status,evidence_json,
                     created_by,updated_by,created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,?,'merged_into','active',?,?,?,?,?,1)""",
                (
                    draft.relation_id,
                    draft.source_item_id,
                    draft.source_revision_id,
                    draft.target_item_id,
                    draft.result_revision_id,
                    evidence_json,
                    draft.actor_user_id,
                    draft.actor_user_id,
                    draft.created_at_ms,
                    draft.created_at_ms,
                ),
            )
            connection.execute(
                """INSERT INTO merge_event(
                     id,dedup_id,space_id,source_item_id,source_revision_id,
                     target_item_id,target_revision_id,result_revision_id,
                     preview_sha256,relation_id,actor_user_id,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    draft.merge_event_id,
                    draft.dedup_id,
                    draft.space_id,
                    draft.source_item_id,
                    draft.source_revision_id,
                    draft.target_item_id,
                    draft.target_revision_id,
                    draft.result_revision_id,
                    draft.preview_sha256,
                    draft.relation_id,
                    draft.actor_user_id,
                    draft.created_at_ms,
                ),
            )
            if failpoint is not None:
                failpoint("merge.after_redirect")
            space_cursor = connection.execute(
                """UPDATE knowledge_space
                      SET generation=generation+1,updated_at=?,row_version=row_version+1
                    WHERE id=? AND status='active' AND write_enabled=1""",
                (draft.created_at_ms, draft.space_id),
            )
            if space_cursor.rowcount != 1:
                raise MergeConflictError("The merge space changed before commit.")
        except sqlite3.IntegrityError as exc:
            raise MergeIntegrityError("The reviewed merge violates canonical schema.") from exc

        state = connection.execute(
            """SELECT s.lifecycle_status,s.redirect_item_id,s.current_revision_id AS source_revision,
                      s.row_version AS source_version,t.lifecycle_status,
                      t.current_revision_id AS target_revision,t.curation_status,
                      t.last_curation_transition_id,t.row_version AS target_version,
                      k.generation,
                      EXISTS(SELECT 1 FROM search_document d WHERE d.item_id=s.id OR d.item_id=t.id)
                        AS search_exists
                 FROM knowledge_item s JOIN knowledge_item t
                 JOIN knowledge_space k ON k.id=s.space_id
                WHERE s.id=? AND t.id=? AND s.space_id=? AND t.space_id=s.space_id""",
            (draft.source_item_id, draft.target_item_id, draft.space_id),
        ).fetchone()
        if (
            state is None
            or _row_value(state, "lifecycle_status") != "merged"
            or _row_value(state, "redirect_item_id") != draft.target_item_id
            or _row_value(state, "source_revision") != draft.source_revision_id
            or int(_row_value(state, "source_version")) != source_result_version
            or state[4] != "active"
            or _row_value(state, "target_revision") != draft.result_revision_id
            or _row_value(state, "curation_status") != "provisional"
            or _row_value(state, "last_curation_transition_id") != draft.transition_id
            or int(_row_value(state, "target_version")) != target_result_version
            or int(_row_value(state, "search_exists")) != 0
        ):
            raise MergeIntegrityError("The merge item postcondition failed.")
        current_edges = connection.execute(
            """SELECT id,source_item_id,target_item_id,edge_type,origin,
                      source_evidence_revision_id,target_evidence_revision_id,
                      edge_status,needs_revalidation
                 FROM knowledge_edge WHERE space_id=? AND (
                      source_item_id IN (?,?) OR target_item_id IN (?,?)) ORDER BY id""",
            (
                draft.space_id,
                draft.source_item_id,
                draft.target_item_id,
                draft.source_item_id,
                draft.target_item_id,
            ),
        ).fetchall()
        after_identities = tuple(tuple(row[index] for index in range(7)) for row in current_edges)
        if after_identities != edge_identities or any(
            row[7] != "inactive" or int(row[8]) != 1 for row in current_edges
        ):
            raise MergeIntegrityError("A semantic edge was rewired or remained actionable.")
        relation = connection.execute(
            """SELECT source_item_id,source_revision_id,target_item_id,target_revision_id,
                      relation_type,relation_status,evidence_json
                 FROM item_relation WHERE id=?""",
            (draft.relation_id,),
        ).fetchone()
        event = connection.execute(
            "SELECT preview_sha256,relation_id FROM merge_event WHERE id=? AND dedup_id=?",
            (draft.merge_event_id, draft.dedup_id),
        ).fetchone()
        if (
            relation is None
            or tuple(relation[:6])
            != (
                draft.source_item_id,
                draft.source_revision_id,
                draft.target_item_id,
                draft.result_revision_id,
                "merged_into",
                "active",
            )
            or relation[6] != evidence_json
            or event is None
            or tuple(event) != (draft.preview_sha256, draft.relation_id)
        ):
            raise MergeIntegrityError("The merge relation/event closure failed.")
        return MergeWriteResult(
            merge_event_id=draft.merge_event_id,
            relation_id=draft.relation_id,
            result_revision_id=draft.result_revision_id,
            transition_id=draft.transition_id,
            rescan_job_id=draft.rescan_job_id,
            source_row_version=source_result_version,
            target_row_version=target_result_version,
            revision_no=revision_no,
            space_generation=int(_row_value(state, "generation")),
            obsoleted_finding_count=finding_cursor.rowcount,
            obsoleted_proposal_count=proposal_cursor.rowcount,
            revalidation_edge_count=edge_cursor.rowcount,
            removed_search_document_count=search_cursor.rowcount,
        )


__all__ = [
    "MergeApplyDraft",
    "MergeConflictError",
    "MergeEdgeSnapshot",
    "MergeIntegrityError",
    "MergeItemSnapshot",
    "MergeNotFoundError",
    "MergeReadSnapshot",
    "MergeReferenceSnapshot",
    "MergeRepository",
    "MergeRepositoryError",
    "MergeWriteResult",
    "canonical_json_bytes",
    "sha256_canonical",
]

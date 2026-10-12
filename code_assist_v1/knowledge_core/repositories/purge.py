"""Reference-aware P5.6 permanent-purge repository boundary.

The repository owns no connection or transaction.  It derives a complete,
hashable preview from canonical rows and performs the irreversible body-link
removal only inside a caller-owned managed transaction.  Revision, item,
artifact, extraction, provenance, and audit metadata are never deleted here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import re
import sqlite3
from typing import Literal

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError
from .derived_body_purge import DerivedBodyInventory, inventory_derived_bodies
from ..retained_blob_references import blob_is_referenced, visual_blob_references


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MAX_SQLITE_INTEGER = 9_223_372_036_854_775_807
_GC_DELAY_MS = 86_400_000


class PurgeRepositoryError(KnowledgeCoreDatabaseError):
    """Base class for stable purge repository failures."""


class PurgeItemNotFoundError(PurgeRepositoryError):
    """The requested item is absent from the requested space."""


class PurgeStateConflictError(PurgeRepositoryError):
    """The item or its reference set no longer matches the preview."""

    def __init__(self, message: str, *, snapshot: "PurgeSnapshot | None" = None) -> None:
        super().__init__(message)
        self.snapshot = snapshot


class PurgeIntegrityError(PurgeRepositoryError):
    """Stored canonical purge state violates a repository postcondition."""


@dataclass(frozen=True, slots=True, order=True)
class PurgeBlocker:
    scope: Literal["item_body", "artifact_body"]
    kind: str
    reference_id: str
    status: str
    artifact_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "reference_id": self.reference_id,
            "scope": self.scope,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class PurgeSizes:
    revision_body_count: int
    revision_body_bytes: int
    artifact_body_count: int
    artifact_body_bytes: int
    extraction_body_count: int
    extraction_body_bytes: int
    candidate_blob_count: int
    candidate_blob_bytes: int
    derived_body_count: int | None = None
    derived_body_bytes: int | None = None

    def as_dict(self) -> dict[str, int]:
        result = {
            "artifact_body_bytes": self.artifact_body_bytes,
            "artifact_body_count": self.artifact_body_count,
            "candidate_blob_bytes": self.candidate_blob_bytes,
            "candidate_blob_count": self.candidate_blob_count,
            "extraction_body_bytes": self.extraction_body_bytes,
            "extraction_body_count": self.extraction_body_count,
            "revision_body_bytes": self.revision_body_bytes,
            "revision_body_count": self.revision_body_count,
        }
        if self.derived_body_count is not None:
            result.update(derived_body_count=self.derived_body_count, derived_body_bytes=self.derived_body_bytes)
        return result


@dataclass(frozen=True, slots=True)
class ArtifactPurgeDecision:
    artifact_id: str
    cleanup_allowed: bool
    retained_reason: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "cleanup_allowed": self.cleanup_allowed,
            "retained_reason": self.retained_reason,
        }


@dataclass(frozen=True, slots=True)
class PurgeSnapshot:
    item_id: str
    space_id: str
    revision_id: str
    episode_id: str
    episode_no: int
    row_version: int
    deactivated_at_ms: int
    purge_eligible_at_ms: int
    eligible: bool
    blockers: tuple[PurgeBlocker, ...]
    artifact_decisions: tuple[ArtifactPurgeDecision, ...]
    retained_categories: tuple[str, ...]
    deleted_categories: tuple[str, ...]
    sizes: PurgeSizes
    candidate_blobs: tuple[tuple[str, int], ...]
    state_sha256: str
    derived_bodies: DerivedBodyInventory | None = None


@dataclass(frozen=True, slots=True)
class PurgeDraft:
    item_id: str
    space_id: str
    actor_user_id: str
    expected_item_row_version: int
    expected_state_sha256: str
    now_ms: int


@dataclass(frozen=True, slots=True)
class PurgeWriteResult:
    item_id: str
    revision_id: str
    episode_id: str
    episode_no: int
    item_row_version: int
    purged_at_ms: int
    revision_body_delete_count: int
    artifact_body_delete_count: int
    extraction_body_delete_count: int
    gc_candidate_count: int
    retained_artifact_count: int
    space_generation: int
    derived_body_delete_count: int | None = None
    derived_body_delete_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class PurgeEligibleItem:
    space_id: str
    item_id: str
    title: str
    deactivated_at_ms: int
    purge_eligible_at_ms: int
    blocking_reference_count: int


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _connection(value: object, *, write: bool = False) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError("ItemPurgeRepository requires a ManagedConnection.")
    if write and not value.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Permanent purge requires an existing managed transaction."
        )
    return value


def _uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_ID_RE.fullmatch(value) is None:
        raise ValueError("actor_user_id must be a canonical user ID.")
    return value


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")
    return value


def _positive(value: object, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= _MAX_SQLITE_INTEGER
    ):
        raise ValueError(f"{field} must be a positive SQLite integer.")
    return value


def _utc_ms(value: object, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_SQLITE_INTEGER
    ):
        raise ValueError(f"{field} must be a non-negative SQLite timestamp.")
    return value


def _item_blockers(connection: ManagedConnection, item_id: str) -> list[PurgeBlocker]:
    rows: list[PurgeBlocker] = []
    for row in connection.execute(
        "SELECT conversation_id FROM conversation_pin WHERE item_id=? ORDER BY conversation_id",
        (item_id,),
    ):
        rows.append(PurgeBlocker("item_body", "conversation_pin", str(row[0]), "active"))
    for row in connection.execute(
        """SELECT id,relation_status FROM item_relation
             WHERE relation_status='active' AND (source_item_id=? OR target_item_id=?)
             ORDER BY id""",
        (item_id, item_id),
    ):
        rows.append(PurgeBlocker("item_body", "item_relation", str(row[0]), str(row[1])))
    for row in connection.execute(
        """SELECT id,edge_status FROM knowledge_edge
             WHERE edge_status='active' AND (source_item_id=? OR target_item_id=?)
             ORDER BY id""",
        (item_id, item_id),
    ):
        rows.append(PurgeBlocker("item_body", "knowledge_edge", str(row[0]), str(row[1])))
    for row in connection.execute(
        """SELECT id,state FROM change_proposal
             WHERE state IN ('pending_review','approved','apply_failed')
               AND (base_item_id=? OR source_item_id=? OR target_item_id=?)
             ORDER BY id""",
        (item_id, item_id, item_id),
    ):
        rows.append(PurgeBlocker("item_body", "change_proposal", str(row[0]), str(row[1])))
    return rows


def _artifact_ids(connection: ManagedConnection, item_id: str) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in connection.execute(
            """SELECT artifact_id FROM item_source WHERE item_id=?
               UNION
               SELECT g.initial_artifact_id FROM knowledge_item i
                 JOIN lineage_group g ON g.id=i.lineage_group_id
                WHERE i.id=?
               UNION
               SELECT g.base_artifact_id FROM knowledge_item i
                 JOIN lineage_group g ON g.id=i.lineage_group_id
                WHERE i.id=? AND g.base_artifact_id IS NOT NULL
               ORDER BY 1""",
            (item_id, item_id, item_id),
        )
    )


def _artifact_blockers(
    connection: ManagedConnection, *, artifact_id: str, purging_item_id: str
) -> list[PurgeBlocker]:
    result: list[PurgeBlocker] = []
    for row in connection.execute(
        """SELECT DISTINCT i.id,i.lifecycle_status FROM item_source s
             JOIN knowledge_item i ON i.id=s.item_id
            WHERE s.artifact_id=? AND i.id<>? AND i.lifecycle_status<>'purged_tombstone'
            ORDER BY i.id""",
        (artifact_id, purging_item_id),
    ):
        result.append(PurgeBlocker("artifact_body", "surviving_item", str(row[0]), str(row[1]), artifact_id))
    for row in connection.execute(
        """SELECT DISTINCT i.id,i.lifecycle_status FROM lineage_group g
             JOIN lineage_member m ON m.lineage_group_id=g.id
             JOIN knowledge_item i ON i.id=m.item_id
            WHERE (g.initial_artifact_id=? OR g.base_artifact_id=?)
              AND i.id<>? AND i.lifecycle_status<>'purged_tombstone'
            ORDER BY i.id""",
        (artifact_id, artifact_id, purging_item_id),
    ):
        result.append(PurgeBlocker("artifact_body", "surviving_lineage_item", str(row[0]), str(row[1]), artifact_id))
    queries = (
        (
            "publication_preview",
            "SELECT id,status FROM publication_preview WHERE artifact_id=? AND status IN ('queued','extracting','ready') ORDER BY id",
            (artifact_id,),
        ),
        (
            "conversation_snapshot",
            "SELECT id,status FROM conversation_context_binding WHERE source_artifact_id=? AND binding_mode='snapshot' AND status<>'detached' ORDER BY id",
            (artifact_id,),
        ),
        (
            "change_proposal_artifact",
            "SELECT id,state FROM change_proposal WHERE source_artifact_id=? AND state IN ('pending_review','approved','apply_failed') ORDER BY id",
            (artifact_id,),
        ),
        (
            "backup_hold",
            "SELECT id,state FROM backup_run WHERE state IN ('preparing','copying') ORDER BY id",
            (),
        ),
    )
    for kind, sql, parameters in queries:
        for row in connection.execute(sql, parameters):
            result.append(PurgeBlocker("artifact_body", kind, str(row[0]), str(row[1]), artifact_id))
    return result


def _candidate_blob_rows(
    connection: ManagedConnection,
    *,
    item_id: str,
    cleanup_artifacts: tuple[str, ...],
) -> tuple[tuple[str, int], ...]:
    candidates: set[str] = {
        str(row[0])
        for row in connection.execute(
            "SELECT content_blob_hash FROM knowledge_revision_body WHERE item_id=? AND content_blob_hash IS NOT NULL",
            (item_id,),
        )
    }
    if cleanup_artifacts:
        candidates.update(row[0] for row in visual_blob_references(connection,artifact_ids=cleanup_artifacts))
        marks = ",".join("?" for _ in cleanup_artifacts)
        candidates.update(
            str(row[0])
            for row in connection.execute(
                f"""SELECT bb.content_sha256 FROM artifact_blob_body bb
                      JOIN artifact_blob b ON b.id=bb.artifact_blob_id
                     WHERE b.artifact_id IN ({marks})
                    UNION
                    SELECT xb.content_blob_hash FROM extraction_snapshot_body xb
                     WHERE xb.artifact_id IN ({marks}) AND xb.content_blob_hash IS NOT NULL""",
                (*cleanup_artifacts, *cleanup_artifacts),
            )
        )
    result: list[tuple[str, int]] = []
    for digest in sorted(candidates):
        if visual_blob_references(connection,digest=digest,exclude_artifacts=cleanup_artifacts):
            continue
        outside_revision = connection.execute(
            "SELECT 1 FROM knowledge_revision_body WHERE content_blob_hash=? AND item_id<>? LIMIT 1",
            (digest, item_id),
        ).fetchone()
        if cleanup_artifacts:
            marks = ",".join("?" for _ in cleanup_artifacts)
            outside_artifact = connection.execute(
                f"""SELECT 1 FROM artifact_blob_body bb JOIN artifact_blob b ON b.id=bb.artifact_blob_id
                     WHERE bb.content_sha256=? AND b.artifact_id NOT IN ({marks}) LIMIT 1""",
                (digest, *cleanup_artifacts),
            ).fetchone()
            outside_extraction = connection.execute(
                f"""SELECT 1 FROM extraction_snapshot_body
                     WHERE content_blob_hash=? AND artifact_id NOT IN ({marks}) LIMIT 1""",
                (digest, *cleanup_artifacts),
            ).fetchone()
        else:
            outside_artifact = connection.execute(
                "SELECT 1 FROM artifact_blob_body WHERE content_sha256=? LIMIT 1", (digest,)
            ).fetchone()
            outside_extraction = connection.execute(
                "SELECT 1 FROM extraction_snapshot_body WHERE content_blob_hash=? LIMIT 1", (digest,)
            ).fetchone()
        proposal = connection.execute(
            "SELECT 1 FROM change_proposal WHERE proposed_content_blob_hash=? LIMIT 1",
            (digest,),
        ).fetchone()
        if any(value is not None for value in (outside_revision, outside_artifact, outside_extraction, proposal)):
            continue
        blob = connection.execute("SELECT size_bytes FROM blob_object WHERE sha256=?", (digest,)).fetchone()
        if blob is None:
            raise PurgeIntegrityError("A purge body references a missing blob object.")
        result.append((digest, int(blob[0])))
    return tuple(result)


def _snapshot(
    connection: ManagedConnection, *, item_id: str, space_id: str, now_ms: int
) -> PurgeSnapshot:
    row = connection.execute(
        """SELECT i.lifecycle_status,i.current_revision_id,
                  i.latest_deactivation_episode_id,i.row_version,
                  e.episode_no,e.deactivated_at,e.purge_eligible_at,
                  e.restored_by,e.restored_at,e.purged_by,e.purged_at,
                  (SELECT MAX(x.episode_no) FROM item_deactivation_episode x WHERE x.item_id=i.id)
             FROM knowledge_item i
             LEFT JOIN item_deactivation_episode e
               ON e.id=i.latest_deactivation_episode_id AND e.item_id=i.id
            WHERE i.id=? AND i.space_id=?""",
        (item_id, space_id),
    ).fetchone()
    if row is None:
        raise PurgeItemNotFoundError("The requested item was not found in this space.")
    if row[0] != "inactive" or row[1] is None or row[2] is None:
        raise PurgeStateConflictError("Only an inactive item with an open episode can be purged.")
    if (
        row[4] is None
        or row[4] != row[11]
        or any(row[index] is not None for index in (7, 8, 9, 10))
    ):
        raise PurgeIntegrityError("The latest deactivation episode is not open and maximal.")
    revision_id = _uuid(str(row[1]), field="revision_id")
    episode_id = _uuid(str(row[2]), field="episode_id")
    episode_no = _positive(int(row[4]), field="episode_no")
    row_version = _positive(int(row[3]), field="row_version")
    deactivated_at = _utc_ms(int(row[5]), field="deactivated_at")
    purge_eligible_at = _utc_ms(int(row[6]), field="purge_eligible_at")
    if purge_eligible_at - deactivated_at != 7_776_000_000:
        raise PurgeIntegrityError("Purge eligibility is not exactly 90 days.")
    if connection.execute("SELECT 1 FROM search_document WHERE item_id=? LIMIT 1", (item_id,)).fetchone() is not None:
        raise PurgeIntegrityError("An inactive purge candidate remains searchable.")

    blockers = _item_blockers(connection, item_id)
    decisions: list[ArtifactPurgeDecision] = []
    cleanup_artifacts: list[str] = []
    for artifact_id in _artifact_ids(connection, item_id):
        artifact_blockers = _artifact_blockers(
            connection, artifact_id=artifact_id, purging_item_id=item_id
        )
        blockers.extend(artifact_blockers)
        allowed = not artifact_blockers
        if allowed:
            cleanup_artifacts.append(artifact_id)
        decisions.append(
            ArtifactPurgeDecision(
                artifact_id=artifact_id,
                cleanup_allowed=allowed,
                retained_reason=None if allowed else "retained_due_reference",
            )
        )

    revision_sizes = connection.execute(
        "SELECT COUNT(*),COALESCE(SUM(content_size_bytes),0) FROM knowledge_revision_body WHERE item_id=?",
        (item_id,),
    ).fetchone()
    if revision_sizes is None or int(revision_sizes[0]) < 1:
        raise PurgeIntegrityError("The inactive item has no retained revision body.")
    artifact_count = artifact_bytes = extraction_count = extraction_bytes = 0
    cleanup_tuple = tuple(sorted(cleanup_artifacts))
    if cleanup_tuple:
        marks = ",".join("?" for _ in cleanup_tuple)
        values = connection.execute(
            f"""SELECT COUNT(*),COALESCE(SUM(b.size_bytes),0)
                   FROM artifact_blob_body bb JOIN artifact_blob b ON b.id=bb.artifact_blob_id
                  WHERE b.artifact_id IN ({marks})""",
            cleanup_tuple,
        ).fetchone()
        artifact_count, artifact_bytes = int(values[0]), int(values[1])
        values = connection.execute(
            f"""SELECT COUNT(*),COALESCE(SUM(x.extracted_size_bytes),0)
                   FROM extraction_snapshot_body xb
                   JOIN extraction_snapshot x ON x.id=xb.snapshot_id AND x.artifact_id=xb.artifact_id
                  WHERE xb.artifact_id IN ({marks})""",
            cleanup_tuple,
        ).fetchone()
        extraction_count, extraction_bytes = int(values[0]), int(values[1])
    candidates = _candidate_blob_rows(
        connection, item_id=item_id, cleanup_artifacts=cleanup_tuple
    )
    derived = inventory_derived_bodies(connection, cleanup_tuple)
    sizes = PurgeSizes(
        revision_body_count=int(revision_sizes[0]),
        revision_body_bytes=int(revision_sizes[1]),
        artifact_body_count=artifact_count,
        artifact_body_bytes=artifact_bytes,
        extraction_body_count=extraction_count,
        extraction_body_bytes=extraction_bytes,
        candidate_blob_count=len(candidates),
        candidate_blob_bytes=sum(size for _, size in candidates),
        derived_body_count=derived.count if derived.tables else None,
        derived_body_bytes=derived.size_bytes if derived.tables else None,
    )
    item_blocked = any(value.scope == "item_body" for value in blockers)
    eligible = now_ms >= purge_eligible_at and not item_blocked
    retained = [
        "audit_history",
        "item_identity",
        "item_provenance",
        "item_tombstone",
        "revision_hashes",
        "revision_metadata",
    ]
    deleted = ["revision_bodies", "search_documents"]
    if artifact_count:
        deleted.append("artifact_body_links")
    if extraction_count:
        deleted.append("extraction_body_links")
    if derived.count:
        deleted.append("derived_source_and_authoring_bodies")
        retained.append("derived_source_and_authoring_metadata")
    if any(not value.cleanup_allowed for value in decisions):
        retained.extend(("referenced_artifact_bodies", "referenced_extraction_bodies"))
    if candidates:
        retained.append("blob_objects_pending_gc")
    facts: dict[str, object] = {
        "artifact_decisions": [value.as_dict() for value in sorted(decisions, key=lambda x: x.artifact_id)],
        "blockers": [value.as_dict() for value in sorted(blockers)],
        "deactivated_at_ms": deactivated_at,
        "deleted_categories": sorted(set(deleted)),
        "episode_id": episode_id,
        "episode_no": episode_no,
        "item_id": item_id,
        "purge_eligible_at_ms": purge_eligible_at,
        "retained_categories": sorted(set(retained)),
        "revision_id": revision_id,
        "row_version": row_version,
        "schema_version": 1,
        "sizes": sizes.as_dict(),
        "candidate_blobs": [
            {"sha256": digest, "size_bytes": size} for digest, size in candidates
        ],
        "space_id": space_id,
    }
    if derived.tables:
        facts["derived_bodies"] = derived.facts()
    state_sha = sha256(_canonical_json(facts)).hexdigest()
    return PurgeSnapshot(
        item_id=item_id,
        space_id=space_id,
        revision_id=revision_id,
        episode_id=episode_id,
        episode_no=episode_no,
        row_version=row_version,
        deactivated_at_ms=deactivated_at,
        purge_eligible_at_ms=purge_eligible_at,
        eligible=eligible,
        blockers=tuple(sorted(blockers)),
        artifact_decisions=tuple(sorted(decisions, key=lambda x: x.artifact_id)),
        retained_categories=tuple(sorted(set(retained))),
        deleted_categories=tuple(sorted(set(deleted))),
        sizes=sizes,
        candidate_blobs=candidates,
        state_sha256=state_sha,
        derived_bodies=derived,
    )


class ItemPurgeRepository:
    """Connection-scoped preview and permanent-body purge primitives."""

    def preview(
        self,
        connection: ManagedConnection,
        *,
        item_id: str,
        space_id: str,
        now_ms: int,
    ) -> PurgeSnapshot:
        connection = _connection(connection)
        return _snapshot(
            connection,
            item_id=_uuid(item_id, field="item_id"),
            space_id=_uuid(space_id, field="space_id"),
            now_ms=_utc_ms(now_ms, field="now_ms"),
        )

    def list_eligible(
        self,
        connection: ManagedConnection,
        *,
        now_ms: int,
        after_purge_eligible_at_ms: int | None,
        after_item_id: str | None,
        limit: int,
    ) -> tuple[tuple[PurgeEligibleItem, ...], bool]:
        """List time-eligible inactive items, including blocked candidates."""

        connection = _connection(connection)
        now = _utc_ms(now_ms, field="now_ms")
        page_size = _positive(limit, field="limit")
        if page_size > 100:
            raise ValueError("limit cannot exceed 100.")
        if (after_purge_eligible_at_ms is None) != (after_item_id is None):
            raise ValueError("The purge queue cursor is incomplete.")
        if after_purge_eligible_at_ms is None:
            after_time = -1
            after_id = ""
        else:
            after_time = _utc_ms(
                after_purge_eligible_at_ms,
                field="after_purge_eligible_at_ms",
            )
            after_id = _uuid(after_item_id, field="after_item_id")
        rows = connection.execute(
            """SELECT i.id,i.space_id,r.title,e.deactivated_at,e.purge_eligible_at
                 FROM knowledge_item i
                 JOIN knowledge_revision r
                   ON r.id=i.current_revision_id AND r.item_id=i.id
                 JOIN item_deactivation_episode e
                   ON e.id=i.latest_deactivation_episode_id AND e.item_id=i.id
                WHERE i.lifecycle_status='inactive'
                  AND e.restored_by IS NULL AND e.restored_at IS NULL
                  AND e.purged_by IS NULL AND e.purged_at IS NULL
                  AND e.purge_eligible_at<=?
                  AND (e.purge_eligible_at>? OR
                       (e.purge_eligible_at=? AND i.id>?))
                ORDER BY e.purge_eligible_at,i.id LIMIT ?""",
            (now, after_time, after_time, after_id, page_size + 1),
        ).fetchall()
        has_more = len(rows) > page_size
        values: list[PurgeEligibleItem] = []
        for row in rows[:page_size]:
            item_id = _uuid(str(row[0]), field="item_id")
            space_id = _uuid(str(row[1]), field="space_id")
            snapshot = _snapshot(
                connection,
                item_id=item_id,
                space_id=space_id,
                now_ms=now,
            )
            values.append(
                PurgeEligibleItem(
                    space_id=space_id,
                    item_id=item_id,
                    title=str(row[2]),
                    deactivated_at_ms=_utc_ms(int(row[3]), field="deactivated_at"),
                    purge_eligible_at_ms=_utc_ms(
                        int(row[4]), field="purge_eligible_at"
                    ),
                    blocking_reference_count=len(snapshot.blockers),
                )
            )
        return tuple(values), has_more

    def purge(
        self,
        connection: ManagedConnection,
        draft: PurgeDraft,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> PurgeWriteResult:
        connection = _connection(connection, write=True)
        if not isinstance(draft, PurgeDraft):
            raise TypeError("purge requires a PurgeDraft.")
        item_id = _uuid(draft.item_id, field="item_id")
        space_id = _uuid(draft.space_id, field="space_id")
        actor = _user(draft.actor_user_id)
        expected_row = _positive(draft.expected_item_row_version, field="expected_item_row_version")
        expected_state = _sha(draft.expected_state_sha256, field="expected_state_sha256")
        now_ms = _utc_ms(draft.now_ms, field="now_ms")
        snapshot = _snapshot(connection, item_id=item_id, space_id=space_id, now_ms=now_ms)
        if snapshot.row_version != expected_row or snapshot.state_sha256 != expected_state:
            raise PurgeStateConflictError("The purge preview is stale.", snapshot=snapshot)
        if not snapshot.eligible:
            raise PurgeStateConflictError("The item is not purge eligible.", snapshot=snapshot)

        cursor = connection.execute(
            """UPDATE item_deactivation_episode SET purged_by=?,purged_at=?
                 WHERE id=? AND item_id=? AND episode_no=?
                   AND restored_by IS NULL AND restored_at IS NULL
                   AND purged_by IS NULL AND purged_at IS NULL
                   AND purge_eligible_at<=CAST(unixepoch('subsec') * 1000 AS INTEGER)""",
            (actor, now_ms, snapshot.episode_id, item_id, snapshot.episode_no),
        )
        if cursor.rowcount != 1:
            raise PurgeStateConflictError("The purge eligibility compare-and-swap failed.")
        if failpoint is not None:
            failpoint("lifecycle.purge_after_episode")
        cursor = connection.execute(
            """UPDATE knowledge_item
                  SET lifecycle_status='purged_tombstone',current_revision_id=NULL,
                      updated_by=?,updated_at=?,row_version=row_version+1
                WHERE id=? AND space_id=? AND lifecycle_status='inactive'
                  AND current_revision_id=? AND latest_deactivation_episode_id=?
                  AND row_version=?""",
            (actor, now_ms, item_id, space_id, snapshot.revision_id, snapshot.episode_id, expected_row),
        )
        if cursor.rowcount != 1:
            raise PurgeStateConflictError("The item purge compare-and-swap failed.")
        if failpoint is not None:
            failpoint("lifecycle.purge_after_tombstone")
        search_deleted = connection.execute(
            "DELETE FROM search_document WHERE item_id=? AND space_id=?", (item_id, space_id)
        ).rowcount
        if search_deleted != 0:
            raise PurgeIntegrityError("An inactive item unexpectedly retained FTS visibility.")
        if failpoint is not None:
            failpoint("lifecycle.purge_after_fts_remove")
        try:
            revision_deleted = connection.execute(
                "DELETE FROM knowledge_revision_body WHERE item_id=?", (item_id,)
            ).rowcount
            artifact_deleted = extraction_deleted = 0
            cleanup_artifacts = tuple(
                value.artifact_id for value in snapshot.artifact_decisions if value.cleanup_allowed
            )
            for artifact_id in cleanup_artifacts:
                extraction_deleted += connection.execute(
                    "DELETE FROM extraction_snapshot_body WHERE artifact_id=?", (artifact_id,)
                ).rowcount
                connection.execute(
                    """UPDATE extraction_snapshot SET body_retention_status='purged'
                        WHERE artifact_id=? AND status='ready' AND body_retention_status='present'""",
                    (artifact_id,),
                )
                artifact_deleted += connection.execute(
                    """DELETE FROM artifact_blob_body WHERE artifact_blob_id IN
                         (SELECT id FROM artifact_blob WHERE artifact_id=?)""",
                    (artifact_id,),
                ).rowcount
                connection.execute(
                    """UPDATE artifact_blob SET body_retention_status='purged'
                        WHERE artifact_id=? AND body_retention_status='present'""",
                    (artifact_id,),
                )
                updated = connection.execute(
                    """UPDATE source_artifact SET body_retention_status='purged',
                              body_retention_reason='eligible_item_purge',body_purged_at=?
                        WHERE id=? AND body_retention_status='present'""",
                    (now_ms, artifact_id),
                )
                if updated.rowcount != 1:
                    raise PurgeStateConflictError("Artifact body cleanup lost its state fence.")
        except sqlite3.IntegrityError as exc:
            raise PurgeStateConflictError("A canonical purge retention gate rejected the mutation.") from exc
        if revision_deleted != snapshot.sizes.revision_body_count:
            raise PurgeIntegrityError("Revision body deletion count changed after preview validation.")
        if artifact_deleted != snapshot.sizes.artifact_body_count or extraction_deleted != snapshot.sizes.extraction_body_count:
            raise PurgeIntegrityError("Artifact body deletion counts changed after preview validation.")
        if snapshot.derived_bodies is not None and snapshot.derived_bodies.tables:
            remaining = inventory_derived_bodies(connection, cleanup_artifacts)
            if remaining.tables != snapshot.derived_bodies.tables or remaining.rows:
                raise PurgeIntegrityError("Previewed derived bodies remain after source-body cleanup.")
        if failpoint is not None:
            failpoint("lifecycle.purge_after_body_delete")

        gc_count = len(snapshot.candidate_blobs)
        for digest, _ in snapshot.candidate_blobs:
            if blob_is_referenced(connection,digest):
                raise PurgeIntegrityError(
                    "A previewed GC blob remains canonically referenced."
                )
            cursor = connection.execute(
                """INSERT INTO blob_gc_candidate(
                     blob_sha256,unreferenced_since,delete_after,state,
                     claim_owner,claim_expires_at,attempt_count,last_error_json,updated_at
                   ) VALUES (?,?,?,'quarantined',NULL,NULL,0,NULL,?)
                   ON CONFLICT(blob_sha256) DO NOTHING""",
                (digest, now_ms, now_ms + _GC_DELAY_MS, now_ms),
            )
        if failpoint is not None:
            failpoint("lifecycle.purge_after_gc_enqueue")
        space = connection.execute(
            "SELECT generation,row_version FROM knowledge_space WHERE id=?", (space_id,)
        ).fetchone()
        if space is None:
            raise PurgeIntegrityError("The purge target space is unavailable.")
        generation, space_version = int(space[0]), int(space[1])
        cursor = connection.execute(
            """UPDATE knowledge_space SET generation=generation+1,updated_at=?,row_version=row_version+1
                 WHERE id=? AND generation=? AND row_version=?""",
            (now_ms, space_id, generation, space_version),
        )
        if cursor.rowcount != 1:
            raise PurgeIntegrityError("The purge space generation changed.")
        current = connection.execute(
            "SELECT lifecycle_status,current_revision_id,row_version FROM knowledge_item WHERE id=? AND space_id=?",
            (item_id, space_id),
        ).fetchone()
        if current is None or tuple(current) != ("purged_tombstone", None, expected_row + 1):
            raise PurgeIntegrityError("The item purge postcondition failed.")
        if connection.execute("SELECT 1 FROM knowledge_revision_body WHERE item_id=? LIMIT 1", (item_id,)).fetchone() is not None:
            raise PurgeIntegrityError("A purged tombstone still retains a revision body.")
        return PurgeWriteResult(
            item_id=item_id,
            revision_id=snapshot.revision_id,
            episode_id=snapshot.episode_id,
            episode_no=snapshot.episode_no,
            item_row_version=expected_row + 1,
            purged_at_ms=now_ms,
            revision_body_delete_count=revision_deleted,
            artifact_body_delete_count=artifact_deleted,
            extraction_body_delete_count=extraction_deleted,
            gc_candidate_count=gc_count,
            retained_artifact_count=sum(not value.cleanup_allowed for value in snapshot.artifact_decisions),
            space_generation=generation + 1,
            derived_body_delete_count=snapshot.sizes.derived_body_count,
            derived_body_delete_bytes=snapshot.sizes.derived_body_bytes,
        )


__all__ = [
    "ArtifactPurgeDecision",
    "ItemPurgeRepository",
    "PurgeBlocker",
    "PurgeDraft",
    "PurgeEligibleItem",
    "PurgeIntegrityError",
    "PurgeItemNotFoundError",
    "PurgeRepositoryError",
    "PurgeSizes",
    "PurgeSnapshot",
    "PurgeStateConflictError",
    "PurgeWriteResult",
]

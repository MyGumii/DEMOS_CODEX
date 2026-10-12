"""Canonical fixed-input change-proposal primitives.

The repository owns no connection or transaction.  It reads the immutable
proposal/finding/revision evidence used by preview and performs only the
single-transaction rebase or apply effects supplied by the service.  Blob
materialization, authorization, lease HMAC validation, durable job enqueue,
audit, outbox, and operation completion remain service responsibilities.
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


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MAX_MARKDOWN_CHARACTERS = 2_000_000
_ACTIONABLE_FINDING_STATES = frozenset({"pending", "approved", "apply_failed"})
_ACTIONABLE_PROPOSAL_STATES = frozenset(
    {"pending_review", "approved", "apply_failed"}
)
_REBASE_FINDING_STATES = frozenset((*_ACTIONABLE_FINDING_STATES, "obsolete"))
_REBASE_PROPOSAL_STATES = frozenset((*_ACTIONABLE_PROPOSAL_STATES, "obsolete"))


class ProposalRepositoryError(KnowledgeCoreDatabaseError):
    code = "PROPOSAL_REPOSITORY_ERROR"


class ProposalNotFoundError(ProposalRepositoryError):
    code = "PROPOSAL_NOT_FOUND"


class ProposalConflictError(ProposalRepositoryError):
    code = "PROPOSAL_CONFLICT"

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


class ProposalIntegrityError(ProposalRepositoryError):
    code = "PROPOSAL_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class ProposalRevisionSnapshot:
    item_id: str
    revision_id: str
    space_id: str
    space_kind: str
    owner_user_id: str | None
    lifecycle_status: str
    curation_status: str
    item_current_revision_id: str
    item_row_version: int
    title: str
    raw_content_sha256: str
    normalized_content_sha256: str
    content_size_bytes: int
    normalizer_version: str
    content_md: str | None
    content_blob_hash: str | None

    def body_binding(self) -> dict[str, object]:
        return {
            "content_blob_hash": self.content_blob_hash,
            "content_size_bytes": self.content_size_bytes,
            "normalizer_version": self.normalizer_version,
            "normalized_content_sha256": self.normalized_content_sha256,
            "raw_content_sha256": self.raw_content_sha256,
        }

    def fixed_binding(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "revision_id": self.revision_id,
            "space_id": self.space_id,
            **self.body_binding(),
        }


@dataclass(frozen=True, slots=True)
class ProposalBodySnapshot:
    content_md: str | None
    content_blob_hash: str | None
    content_size_bytes: int
    content_sha256: str

    def binding(self) -> dict[str, object]:
        return {
            "content_blob_hash": self.content_blob_hash,
            "content_size_bytes": self.content_size_bytes,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True, slots=True)
class ProposalReadSnapshot:
    proposal_id: str
    finding_id: str
    space_id: str
    proposal_kind: Literal["stable_source_update", "personal_to_part"]
    proposal_series_id: str
    proposal_version: int
    supersedes_proposal_id: str | None
    lineage_group_id: str | None
    proposal_state: str
    proposal_row_version: int
    finding_status: str
    finding_input_row_version: int
    finding_row_version: int
    finding_rule_version: str
    finding_reviewed_by: str | None
    source_artifact_id: str | None
    extraction_snapshot_id: str | None
    extraction_snapshot_sha256: str | None
    fixed_preview_sha256: str
    base: ProposalRevisionSnapshot
    source: ProposalRevisionSnapshot | None
    target: ProposalRevisionSnapshot
    source_current: ProposalRevisionSnapshot | None
    target_current: ProposalRevisionSnapshot
    proposed: ProposalBodySnapshot

    @property
    def stale(self) -> bool:
        return (
            self.target_current.revision_id != self.target.revision_id
            or self.target_current.lifecycle_status != "active"
            or (
                self.source is not None
                and (
                    self.source_current is None
                    or self.source_current.revision_id != self.source.revision_id
                    or self.source_current.lifecycle_status != "active"
                )
            )
        )

    def fixed_preview_payload(self) -> dict[str, object]:
        return {
            "base_item_id": self.base.item_id,
            "base_revision_id": self.base.revision_id,
            "extraction_snapshot_id": self.extraction_snapshot_id,
            "extraction_snapshot_sha256": self.extraction_snapshot_sha256,
            "finding_input_row_version": self.finding_input_row_version,
            "proposal_kind": self.proposal_kind,
            "proposal_version": self.proposal_version,
            "proposed_content_sha256": self.proposed.content_sha256,
            "rule_version": self.finding_rule_version,
            "source_artifact_id": self.source_artifact_id,
            "source_item_id": None if self.source is None else self.source.item_id,
            "source_revision_id": (
                None if self.source is None else self.source.revision_id
            ),
            "target_item_id": self.target.item_id,
            "target_revision_id": self.target.revision_id,
        }

    def binding(self) -> dict[str, object]:
        return {
            "base": self.base.fixed_binding(),
            "extraction_snapshot_id": self.extraction_snapshot_id,
            "extraction_snapshot_sha256": self.extraction_snapshot_sha256,
            "finding_id": self.finding_id,
            "finding_input_row_version": self.finding_input_row_version,
            "finding_row_version": self.finding_row_version,
            "finding_rule_version": self.finding_rule_version,
            "finding_status": self.finding_status,
            "fixed_preview_sha256": self.fixed_preview_sha256,
            "lineage_group_id": self.lineage_group_id,
            "proposal_id": self.proposal_id,
            "proposal_kind": self.proposal_kind,
            "proposal_row_version": self.proposal_row_version,
            "proposal_series_id": self.proposal_series_id,
            "proposal_state": self.proposal_state,
            "proposal_version": self.proposal_version,
            "proposed": self.proposed.binding(),
            "source": None if self.source is None else self.source.fixed_binding(),
            "source_artifact_id": self.source_artifact_id,
            "source_current_revision_id": (
                None if self.source_current is None else self.source_current.revision_id
            ),
            "space_id": self.space_id,
            "supersedes_proposal_id": self.supersedes_proposal_id,
            "target": self.target.fixed_binding(),
            "target_current_revision_id": self.target_current.revision_id,
            "target_current_row_version": self.target_current.item_row_version,
        }


@dataclass(frozen=True, slots=True)
class ProposalRebaseDraft:
    old_proposal_id: str
    old_finding_id: str
    expected_old_proposal_row_version: int
    expected_old_finding_row_version: int
    new_proposal_id: str
    new_finding_id: str
    operation_id: str
    dedup_id: str
    actor_user_id: str
    expected_target_revision_id: str
    expected_source_revision_id: str | None
    proposed_content_md: str | None
    proposed_content_blob_hash: str | None
    proposed_content_size_bytes: int
    proposed_content_sha256: str
    fixed_preview_sha256: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ProposalRebaseWriteResult:
    proposal_id: str
    finding_id: str
    proposal_series_id: str
    proposal_version: int
    proposal_row_version: int
    finding_row_version: int
    preview_sha256: str
    target_revision_id: str
    source_revision_id: str | None


@dataclass(frozen=True, slots=True)
class ProposalApplyDraft:
    proposal_id: str
    finding_id: str
    expected_proposal_row_version: int
    expected_finding_row_version: int
    space_id: str
    actor_user_id: str
    operation_id: str
    dedup_id: str
    expected_target_item_row_version: int
    expected_target_revision_id: str
    expected_source_revision_id: str | None
    preview_sha256: str
    result_revision_id: str
    transition_id: str
    rescan_job_id: str
    rescan_job_dedup_id: str
    content_md: str
    raw_content_sha256: str
    normalized_content_sha256: str
    content_size_bytes: int
    normalizer_version: str
    metadata: Mapping[str, object]
    reason: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ProposalApplyWriteResult:
    proposal_id: str
    finding_id: str
    target_item_id: str
    result_revision_id: str
    transition_id: str
    rescan_job_id: str
    revision_no: int
    item_row_version: int
    proposal_row_version: int
    finding_row_version: int
    space_generation: int
    obsoleted_finding_count: int
    obsoleted_proposal_count: int
    revalidation_edge_count: int
    removed_search_document_count: int


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
        raise ValueError("Proposal evidence must be finite JSON.") from exc


def sha256_canonical(value: object) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def _transaction(connection: ManagedConnection) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection) or not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Proposal repository methods require a caller-owned transaction."
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


def _row(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProposalIntegrityError("A canonical proposal row could not be decoded.") from exc


def _canonical_object(value: object, field: str) -> str:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping.")
    encoded = canonical_json_bytes(dict(value)).decode("ascii")
    if not isinstance(json.loads(encoded), dict):
        raise ValueError(f"{field} must encode an object.")
    return encoded


def _body(
    *,
    content_md: object,
    content_blob_hash: object,
    content_size_bytes: object,
    content_sha256: object,
    field: str,
) -> ProposalBodySnapshot:
    if (
        isinstance(content_size_bytes, bool)
        or not isinstance(content_size_bytes, int)
        or content_size_bytes < 0
        or not isinstance(content_sha256, str)
        or _SHA_RE.fullmatch(content_sha256) is None
        or (content_md is None) == (content_blob_hash is None)
        or (content_md is not None and not isinstance(content_md, str))
        or (
            content_blob_hash is not None
            and (
                not isinstance(content_blob_hash, str)
                or _SHA_RE.fullmatch(content_blob_hash) is None
            )
        )
    ):
        raise ProposalIntegrityError(f"{field} body evidence is invalid.")
    if content_md is not None:
        try:
            encoded = content_md.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ProposalIntegrityError(f"{field} Markdown is invalid Unicode.") from exc
        if (
            len(encoded) != content_size_bytes
            or sha256(encoded).hexdigest() != content_sha256
        ):
            raise ProposalIntegrityError(f"{field} Markdown evidence is inconsistent.")
    return ProposalBodySnapshot(
        content_md if isinstance(content_md, str) else None,
        content_blob_hash if isinstance(content_blob_hash, str) else None,
        content_size_bytes,
        content_sha256,
    )


def _read_revision(
    connection: ManagedConnection, *, item_id: str, revision_id: str
) -> ProposalRevisionSnapshot:
    row = connection.execute(
        """SELECT i.id AS item_id,i.space_id,i.lifecycle_status,i.curation_status,
                  i.current_revision_id,i.row_version,s.space_kind,s.owner_user_id,
                  r.id AS revision_id,r.title,r.raw_content_sha256,
                  r.normalized_content_sha256,r.content_size_bytes,
                  r.normalizer_version,b.content_md,b.content_blob_hash,
                  b.content_size_bytes AS body_size,b.content_sha256 AS body_sha
             FROM knowledge_item AS i JOIN knowledge_space AS s ON s.id=i.space_id
             JOIN knowledge_revision AS r ON r.item_id=i.id AND r.id=?
             JOIN knowledge_revision_body AS b
               ON b.revision_id=r.id AND b.item_id=r.item_id
            WHERE i.id=?""",
        (revision_id, item_id),
    ).fetchone()
    if row is None:
        raise ProposalNotFoundError("A fixed proposal revision is unavailable.")
    raw_sha = _sha(_row(row, "raw_content_sha256"), "raw_content_sha256")
    size = _row(row, "content_size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ProposalIntegrityError("A fixed proposal revision size is invalid.")
    body = _body(
        content_md=_row(row, "content_md"),
        content_blob_hash=_row(row, "content_blob_hash"),
        content_size_bytes=_row(row, "body_size"),
        content_sha256=_row(row, "body_sha"),
        field="revision",
    )
    if body.content_size_bytes != size or body.content_sha256 != raw_sha:
        raise ProposalIntegrityError("A fixed proposal revision body is inconsistent.")
    return ProposalRevisionSnapshot(
        item_id=str(_row(row, "item_id")),
        revision_id=str(_row(row, "revision_id")),
        space_id=str(_row(row, "space_id")),
        space_kind=str(_row(row, "space_kind")),
        owner_user_id=(
            None if _row(row, "owner_user_id") is None else str(_row(row, "owner_user_id"))
        ),
        lifecycle_status=str(_row(row, "lifecycle_status")),
        curation_status=str(_row(row, "curation_status")),
        item_current_revision_id=str(_row(row, "current_revision_id")),
        item_row_version=int(_row(row, "row_version")),
        title=str(_row(row, "title")),
        raw_content_sha256=raw_sha,
        normalized_content_sha256=_sha(
            _row(row, "normalized_content_sha256"), "normalized_content_sha256"
        ),
        content_size_bytes=size,
        normalizer_version=str(_row(row, "normalizer_version")),
        content_md=body.content_md,
        content_blob_hash=body.content_blob_hash,
    )


def _read_current_revision(
    connection: ManagedConnection, *, item_id: str
) -> ProposalRevisionSnapshot:
    row = connection.execute(
        "SELECT current_revision_id FROM knowledge_item WHERE id=?",
        (item_id,),
    ).fetchone()
    if row is None or _row(row, "current_revision_id") is None:
        raise ProposalNotFoundError("A proposal item has no current revision.")
    return _read_revision(
        connection, item_id=item_id, revision_id=str(_row(row, "current_revision_id"))
    )


def _state_compatible(finding: str, proposal: str) -> bool:
    return (
        (finding == "pending" and proposal == "pending_review")
        or (finding == proposal and finding in {"approved", "obsolete", "applied", "apply_failed"})
        or (finding in {"rejected", "ignored"} and proposal == "obsolete")
    )


def fixed_preview_payload(
    *,
    proposal_kind: str,
    proposal_version: int,
    base_item_id: str,
    base_revision_id: str,
    source_item_id: str | None,
    source_revision_id: str | None,
    target_item_id: str,
    target_revision_id: str,
    source_artifact_id: str | None,
    extraction_snapshot_id: str | None,
    extraction_snapshot_sha256: str | None,
    proposed_content_sha256: str,
    finding_input_row_version: int,
    rule_version: str,
) -> dict[str, object]:
    return {
        "base_item_id": base_item_id,
        "base_revision_id": base_revision_id,
        "extraction_snapshot_id": extraction_snapshot_id,
        "extraction_snapshot_sha256": extraction_snapshot_sha256,
        "finding_input_row_version": finding_input_row_version,
        "proposal_kind": proposal_kind,
        "proposal_version": proposal_version,
        "proposed_content_sha256": proposed_content_sha256,
        "rule_version": rule_version,
        "source_artifact_id": source_artifact_id,
        "source_item_id": source_item_id,
        "source_revision_id": source_revision_id,
        "target_item_id": target_item_id,
        "target_revision_id": target_revision_id,
    }


class ProposalRepository:
    """Read fixed proposal evidence and apply atomic rebase/apply effects."""

    __slots__ = ()

    def read_snapshot(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        proposal_id: str,
    ) -> ProposalReadSnapshot:
        connection = _transaction(connection)
        space_id = _uuid(space_id, "space_id")
        proposal_id = _uuid(proposal_id, "proposal_id")
        row = connection.execute(
            """SELECT p.*,f.space_id,f.status AS finding_status,
                      f.row_version AS finding_row_version,f.rule_version,
                      f.reviewed_by,f.finding_kind,f.risk_tier,f.decision_mode,
                      f.required_reviewer,f.proposed_action,
                      f.source_item_id AS finding_source_item_id,
                      f.source_revision_id AS finding_source_revision_id,
                      f.target_item_id AS finding_target_item_id,
                      f.target_revision_id AS finding_target_revision_id
                 FROM change_proposal AS p
                 JOIN curation_finding AS f ON f.id=p.finding_id
                WHERE p.id=? AND f.space_id=?""",
            (proposal_id, space_id),
        ).fetchone()
        if row is None:
            raise ProposalNotFoundError("The change proposal does not exist in this space.")
        proposal_kind = str(_row(row, "proposal_kind"))
        finding_status = str(_row(row, "finding_status"))
        proposal_state = str(_row(row, "state"))
        if (
            proposal_kind not in {"stable_source_update", "personal_to_part"}
            or _row(row, "finding_kind") != "update"
            or _row(row, "risk_tier") != "T2"
            or _row(row, "decision_mode") != "human"
            or _row(row, "required_reviewer") != "space_editor"
            or _row(row, "proposed_action") != "update"
            or _row(row, "finding_source_item_id") != _row(row, "target_item_id")
            or _row(row, "finding_source_revision_id") != _row(row, "target_revision_id")
            or _row(row, "finding_target_item_id") is not None
            or _row(row, "finding_target_revision_id") is not None
            or not _state_compatible(finding_status, proposal_state)
        ):
            raise ProposalIntegrityError("The finding/proposal state binding is invalid.")
        proposed = _body(
            content_md=_row(row, "proposed_content_md"),
            content_blob_hash=_row(row, "proposed_content_blob_hash"),
            content_size_bytes=_row(row, "proposed_content_size_bytes"),
            content_sha256=_row(row, "proposed_content_sha256"),
            field="proposed",
        )
        base = _read_revision(
            connection,
            item_id=str(_row(row, "base_item_id")),
            revision_id=str(_row(row, "base_revision_id")),
        )
        target = _read_revision(
            connection,
            item_id=str(_row(row, "target_item_id")),
            revision_id=str(_row(row, "target_revision_id")),
        )
        if target.space_id != space_id:
            raise ProposalIntegrityError("The proposal target is outside its finding space.")
        target_current = _read_current_revision(connection, item_id=target.item_id)
        source: ProposalRevisionSnapshot | None = None
        source_current: ProposalRevisionSnapshot | None = None
        source_artifact_id = _row(row, "source_artifact_id")
        extraction_snapshot_id = _row(row, "extraction_snapshot_id")
        extraction_sha: str | None = None
        lineage_group_id = _row(row, "lineage_group_id")
        if proposal_kind == "stable_source_update":
            if (
                base.item_id != target.item_id
                or base.revision_id != target.revision_id
                or source_artifact_id is None
                or extraction_snapshot_id is None
                or lineage_group_id is not None
                or _row(row, "source_item_id") is not None
                or _row(row, "source_revision_id") is not None
            ):
                raise ProposalIntegrityError("The stable-source proposal shape is invalid.")
            extraction = connection.execute(
                """SELECT x.status,x.extracted_sha256,x.extracted_size_bytes,
                          x.body_retention_status,b.content_md,b.content_blob_hash,
                          b.content_size_bytes,b.content_sha256,
                          a.body_retention_status AS artifact_retention
                     FROM extraction_snapshot AS x
                     JOIN extraction_snapshot_body AS b
                       ON b.snapshot_id=x.id AND b.artifact_id=x.artifact_id
                     JOIN source_artifact AS a ON a.id=x.artifact_id
                    WHERE x.id=? AND x.artifact_id=?""",
                (extraction_snapshot_id, source_artifact_id),
            ).fetchone()
            if extraction is None:
                raise ProposalIntegrityError("The stable source snapshot is unavailable.")
            extracted = _body(
                content_md=_row(extraction, "content_md"),
                content_blob_hash=_row(extraction, "content_blob_hash"),
                content_size_bytes=_row(extraction, "content_size_bytes"),
                content_sha256=_row(extraction, "content_sha256"),
                field="extraction",
            )
            if (
                _row(extraction, "status") != "ready"
                or _row(extraction, "body_retention_status") != "present"
                or _row(extraction, "artifact_retention") != "present"
                or _row(extraction, "extracted_sha256") != extracted.content_sha256
                or _row(extraction, "extracted_size_bytes") != extracted.content_size_bytes
                or extracted != proposed
            ):
                raise ProposalIntegrityError("The stable source snapshot binding is invalid.")
            extraction_sha = extracted.content_sha256
        else:
            source_item_id = _row(row, "source_item_id")
            source_revision_id = _row(row, "source_revision_id")
            if (
                source_item_id is None
                or source_revision_id is None
                or lineage_group_id is None
                or source_artifact_id is not None
                or extraction_snapshot_id is not None
            ):
                raise ProposalIntegrityError("The personal-to-part proposal shape is invalid.")
            source = _read_revision(
                connection,
                item_id=str(source_item_id),
                revision_id=str(source_revision_id),
            )
            source_current = _read_current_revision(connection, item_id=source.item_id)
            member = connection.execute(
                """SELECT target.derived_from_item_id,target.derived_from_revision_id
                     FROM lineage_member AS target JOIN lineage_member AS source
                       ON source.lineage_group_id=target.lineage_group_id
                    WHERE target.lineage_group_id=? AND target.item_id=?
                      AND source.item_id=?""",
                (lineage_group_id, target.item_id, source.item_id),
            ).fetchone()
            if (
                member is None
                or _row(member, "derived_from_item_id") != base.item_id
                or _row(member, "derived_from_revision_id") != base.revision_id
                or source.space_kind != "personal"
                or target.space_kind != "part"
                or source.item_id == target.item_id
            ):
                raise ProposalIntegrityError("The personal-to-part lineage binding is invalid.")
        finding_version = int(_row(row, "finding_input_row_version"))
        if finding_version != int(_row(row, "finding_row_version")) and proposal_state == "pending_review":
            raise ProposalIntegrityError("A pending proposal lost its finding input version.")
        payload = fixed_preview_payload(
            proposal_kind=proposal_kind,
            proposal_version=int(_row(row, "proposal_version")),
            base_item_id=base.item_id,
            base_revision_id=base.revision_id,
            source_item_id=None if source is None else source.item_id,
            source_revision_id=None if source is None else source.revision_id,
            target_item_id=target.item_id,
            target_revision_id=target.revision_id,
            source_artifact_id=(
                None if source_artifact_id is None else str(source_artifact_id)
            ),
            extraction_snapshot_id=(
                None if extraction_snapshot_id is None else str(extraction_snapshot_id)
            ),
            extraction_snapshot_sha256=extraction_sha,
            proposed_content_sha256=proposed.content_sha256,
            finding_input_row_version=finding_version,
            rule_version=str(_row(row, "rule_version")),
        )
        fixed_sha = _sha(_row(row, "preview_sha256"), "preview_sha256")
        if sha256_canonical(payload) != fixed_sha:
            raise ProposalIntegrityError("The fixed proposal preview hash is invalid.")
        return ProposalReadSnapshot(
            proposal_id=str(_row(row, "id")),
            finding_id=str(_row(row, "finding_id")),
            space_id=space_id,
            proposal_kind=proposal_kind,  # type: ignore[arg-type]
            proposal_series_id=str(_row(row, "proposal_series_id")),
            proposal_version=int(_row(row, "proposal_version")),
            supersedes_proposal_id=(
                None
                if _row(row, "supersedes_proposal_id") is None
                else str(_row(row, "supersedes_proposal_id"))
            ),
            lineage_group_id=(
                None if lineage_group_id is None else str(lineage_group_id)
            ),
            proposal_state=proposal_state,
            proposal_row_version=int(_row(row, "row_version")),
            finding_status=finding_status,
            finding_input_row_version=finding_version,
            finding_row_version=int(_row(row, "finding_row_version")),
            finding_rule_version=str(_row(row, "rule_version")),
            finding_reviewed_by=(
                None if _row(row, "reviewed_by") is None else str(_row(row, "reviewed_by"))
            ),
            source_artifact_id=(
                None if source_artifact_id is None else str(source_artifact_id)
            ),
            extraction_snapshot_id=(
                None if extraction_snapshot_id is None else str(extraction_snapshot_id)
            ),
            extraction_snapshot_sha256=extraction_sha,
            fixed_preview_sha256=fixed_sha,
            base=base,
            source=source,
            target=target,
            source_current=source_current,
            target_current=target_current,
            proposed=proposed,
        )

    def rebase(
        self,
        connection: ManagedConnection,
        draft: ProposalRebaseDraft,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> ProposalRebaseWriteResult:
        connection = _transaction(connection)
        if not isinstance(draft, ProposalRebaseDraft):
            raise TypeError("rebase requires a ProposalRebaseDraft.")
        for field in (
            "old_proposal_id", "old_finding_id", "new_proposal_id", "new_finding_id",
            "operation_id", "dedup_id", "expected_target_revision_id",
        ):
            _uuid(getattr(draft, field), field)
        _user(draft.actor_user_id)
        _positive(draft.expected_old_proposal_row_version, "expected_old_proposal_row_version")
        _positive(draft.expected_old_finding_row_version, "expected_old_finding_row_version")
        _sha(draft.proposed_content_sha256, "proposed_content_sha256")
        _sha(draft.fixed_preview_sha256, "fixed_preview_sha256")
        _utc_ms(draft.created_at_ms)
        proposed = _body(
            content_md=draft.proposed_content_md,
            content_blob_hash=draft.proposed_content_blob_hash,
            content_size_bytes=draft.proposed_content_size_bytes,
            content_sha256=draft.proposed_content_sha256,
            field="rebased proposed",
        )
        old_row = connection.execute(
            "SELECT space_id FROM curation_finding WHERE id=?",
            (draft.old_finding_id,),
        ).fetchone()
        if old_row is None:
            raise ProposalNotFoundError("The proposal finding no longer exists.")
        snapshot = self.read_snapshot(
            connection,
            space_id=str(_row(old_row, "space_id")),
            proposal_id=draft.old_proposal_id,
        )
        if (
            snapshot.finding_id != draft.old_finding_id
            or snapshot.proposal_row_version != draft.expected_old_proposal_row_version
            or snapshot.finding_row_version != draft.expected_old_finding_row_version
            or snapshot.proposal_state not in _REBASE_PROPOSAL_STATES
            or snapshot.finding_status not in _REBASE_FINDING_STATES
            or snapshot.target_current.revision_id != draft.expected_target_revision_id
            or snapshot.target_current.lifecycle_status != "active"
            or (
                snapshot.source_current is not None
                and snapshot.source_current.revision_id != draft.expected_source_revision_id
            )
            or (
                snapshot.source_current is None
                and draft.expected_source_revision_id is not None
            )
        ):
            raise ProposalConflictError(
                "The proposal changed before it could be rebased.",
                current_revision_id=snapshot.target_current.revision_id,
                current_row_version=snapshot.target_current.item_row_version,
            )
        operation = connection.execute(
            """SELECT 1 FROM operation_dedup
                WHERE id=? AND actor_user_id=? AND operation_type='proposal_rebase'
                  AND operation_id=? AND status='in_progress'""",
            (draft.dedup_id, draft.actor_user_id, draft.operation_id),
        ).fetchone()
        if operation is None:
            raise ProposalIntegrityError("The rebase operation reservation is missing.")
        collisions = connection.execute(
            """SELECT EXISTS(SELECT 1 FROM curation_finding WHERE id=?),
                      EXISTS(SELECT 1 FROM change_proposal WHERE id=?)""",
            (draft.new_finding_id, draft.new_proposal_id),
        ).fetchone()
        if collisions is None or int(collisions[0]) or int(collisions[1]):
            raise ProposalConflictError("A deterministic rebase identity is already in use.")
        new_target = snapshot.target_current
        new_source = snapshot.source_current
        if snapshot.proposal_kind == "stable_source_update":
            if proposed != snapshot.proposed:
                raise ProposalConflictError("A stable-source rebase cannot replace source evidence.")
            new_base = new_target
        else:
            if new_source is None:
                raise ProposalIntegrityError("The personal source current revision is missing.")
            new_base = snapshot.base
        expected_preview = fixed_preview_payload(
            proposal_kind=snapshot.proposal_kind,
            proposal_version=snapshot.proposal_version + 1,
            base_item_id=new_base.item_id,
            base_revision_id=new_base.revision_id,
            source_item_id=None if new_source is None else new_source.item_id,
            source_revision_id=None if new_source is None else new_source.revision_id,
            target_item_id=new_target.item_id,
            target_revision_id=new_target.revision_id,
            source_artifact_id=snapshot.source_artifact_id,
            extraction_snapshot_id=snapshot.extraction_snapshot_id,
            extraction_snapshot_sha256=snapshot.extraction_snapshot_sha256,
            proposed_content_sha256=proposed.content_sha256,
            finding_input_row_version=1,
            rule_version=snapshot.finding_rule_version,
        )
        if sha256_canonical(expected_preview) != draft.fixed_preview_sha256:
            raise ValueError("The rebased fixed preview hash is invalid.")
        evidence_json = _canonical_object(
            {
                "base_item_id": new_base.item_id,
                "base_revision_id": new_base.revision_id,
                "proposal_kind": snapshot.proposal_kind,
                "proposal_version": snapshot.proposal_version + 1,
                "proposed_content_sha256": proposed.content_sha256,
                "schema_version": 1,
                "source_item_id": None if new_source is None else new_source.item_id,
                "source_revision_id": None if new_source is None else new_source.revision_id,
                "supersedes_proposal_id": snapshot.proposal_id,
                "target_item_id": new_target.item_id,
                "target_revision_id": new_target.revision_id,
            },
            "rebase evidence",
        )
        action_json = _canonical_object(
            {
                "preview_sha256": draft.fixed_preview_sha256,
                "proposal_id": draft.new_proposal_id,
                "proposal_kind": snapshot.proposal_kind,
                "proposal_version": snapshot.proposal_version + 1,
                "schema_version": 1,
            },
            "rebase action",
        )
        try:
            connection.execute(
                """INSERT INTO curation_finding(
                     id,space_id,finding_kind,risk_tier,severity,status,
                     decision_mode,required_reviewer,visibility_effect,
                     proposed_action,source_item_id,source_revision_id,
                     target_item_id,target_revision_id,detector_name,
                     detector_version,rule_version,model_id,prompt_version,
                     confidence,evidence_json,proposed_action_json,reviewed_by,
                     reviewed_at,review_reason,applied_operation_id,
                     applied_dedup_id,applied_job_id,applied_by,applied_at,
                     last_application_error_json,created_actor_kind,created_by,
                     created_at,updated_at,row_version)
                   VALUES (?,?,'update','T2','info','pending','human','space_editor',
                     'agent_hidden','update',?,?,NULL,NULL,'proposal-rebase','1',?,
                     NULL,NULL,1.0,?,?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
                     'user',?,?,?,1)""",
                (
                    draft.new_finding_id,
                    snapshot.space_id,
                    new_target.item_id,
                    new_target.revision_id,
                    snapshot.finding_rule_version,
                    evidence_json,
                    action_json,
                    draft.actor_user_id,
                    draft.created_at_ms,
                    draft.created_at_ms,
                ),
            )
            connection.execute(
                """INSERT INTO change_proposal(
                     id,finding_id,finding_input_row_version,proposal_kind,
                     proposal_series_id,proposal_version,supersedes_proposal_id,
                     lineage_group_id,base_item_id,base_revision_id,source_item_id,
                     source_revision_id,target_item_id,target_revision_id,
                     source_artifact_id,extraction_snapshot_id,proposed_content_md,
                     proposed_content_blob_hash,proposed_content_size_bytes,
                     proposed_content_sha256,preview_sha256,state,
                     applied_operation_id,applied_dedup_id,last_application_error_json,
                     created_by,updated_by,created_at,updated_at,row_version)
                   VALUES (?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending_review',
                     NULL,NULL,NULL,?,?,?,?,1)""",
                (
                    draft.new_proposal_id,
                    draft.new_finding_id,
                    snapshot.proposal_kind,
                    snapshot.proposal_series_id,
                    snapshot.proposal_version + 1,
                    snapshot.proposal_id,
                    snapshot.lineage_group_id,
                    new_base.item_id,
                    new_base.revision_id,
                    None if new_source is None else new_source.item_id,
                    None if new_source is None else new_source.revision_id,
                    new_target.item_id,
                    new_target.revision_id,
                    snapshot.source_artifact_id,
                    snapshot.extraction_snapshot_id,
                    proposed.content_md,
                    proposed.content_blob_hash,
                    proposed.content_size_bytes,
                    proposed.content_sha256,
                    draft.fixed_preview_sha256,
                    draft.actor_user_id,
                    draft.actor_user_id,
                    draft.created_at_ms,
                    draft.created_at_ms,
                ),
            )
            if failpoint is not None:
                failpoint("proposal_rebase.after_new_pair")
            if snapshot.finding_status == "obsolete":
                finding_changed = 0
            else:
                finding_cursor = connection.execute(
                    """UPDATE curation_finding
                      SET status='obsolete',applied_operation_id=NULL,
                          applied_dedup_id=NULL,applied_job_id=NULL,applied_by=NULL,
                          applied_at=NULL,last_application_error_json=NULL,
                          updated_at=?,row_version=row_version+1
                    WHERE id=? AND row_version=?
                      AND status IN ('pending','approved','apply_failed')""",
                    (
                        draft.created_at_ms,
                        snapshot.finding_id,
                        draft.expected_old_finding_row_version,
                    ),
                )
                if finding_cursor.rowcount != 1:
                    raise ProposalConflictError("The old finding changed during rebase.")
                finding_changed = finding_cursor.rowcount
            if snapshot.proposal_state == "obsolete":
                proposal_changed = 0
            else:
                proposal_cursor = connection.execute(
                    """UPDATE change_proposal
                      SET state='obsolete',applied_operation_id=NULL,
                          applied_dedup_id=NULL,last_application_error_json=NULL,
                          updated_by=?,updated_at=?,row_version=row_version+1
                    WHERE id=? AND row_version=?
                      AND state IN ('pending_review','approved','apply_failed')""",
                    (
                        draft.actor_user_id,
                        draft.created_at_ms,
                        snapshot.proposal_id,
                        draft.expected_old_proposal_row_version,
                    ),
                )
                if proposal_cursor.rowcount != 1:
                    raise ProposalConflictError("The old proposal changed during rebase.")
                proposal_changed = proposal_cursor.rowcount
            if finding_changed != proposal_changed:
                raise ProposalIntegrityError(
                    "The old proposal/finding obsoletion was not atomic."
                )
        except sqlite3.IntegrityError as exc:
            raise ProposalIntegrityError("The rebased proposal violates canonical policy.") from exc
        if failpoint is not None:
            failpoint("proposal_rebase.after_old_pair_obsolete")
        new_snapshot = self.read_snapshot(
            connection, space_id=snapshot.space_id, proposal_id=draft.new_proposal_id
        )
        old_state = connection.execute(
            """SELECT p.state,f.status FROM change_proposal p
                 JOIN curation_finding f ON f.id=p.finding_id WHERE p.id=?""",
            (snapshot.proposal_id,),
        ).fetchone()
        if (
            new_snapshot.proposal_state != "pending_review"
            or new_snapshot.finding_status != "pending"
            or new_snapshot.fixed_preview_sha256 != draft.fixed_preview_sha256
            or old_state is None
            or tuple(old_state) != ("obsolete", "obsolete")
        ):
            raise ProposalIntegrityError("The proposal rebase postcondition failed.")
        return ProposalRebaseWriteResult(
            draft.new_proposal_id,
            draft.new_finding_id,
            snapshot.proposal_series_id,
            snapshot.proposal_version + 1,
            new_snapshot.proposal_row_version,
            new_snapshot.finding_row_version,
            draft.fixed_preview_sha256,
            new_target.revision_id,
            None if new_source is None else new_source.revision_id,
        )

    def apply(
        self,
        connection: ManagedConnection,
        draft: ProposalApplyDraft,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> ProposalApplyWriteResult:
        connection = _transaction(connection)
        if not isinstance(draft, ProposalApplyDraft):
            raise TypeError("apply requires a ProposalApplyDraft.")
        for field in (
            "proposal_id", "finding_id", "space_id", "operation_id", "dedup_id",
            "expected_target_revision_id", "result_revision_id", "transition_id",
            "rescan_job_id", "rescan_job_dedup_id",
        ):
            _uuid(getattr(draft, field), field)
        if draft.expected_source_revision_id is not None:
            _uuid(draft.expected_source_revision_id, "expected_source_revision_id")
        _user(draft.actor_user_id)
        _positive(draft.expected_proposal_row_version, "expected_proposal_row_version")
        _positive(draft.expected_finding_row_version, "expected_finding_row_version")
        _positive(draft.expected_target_item_row_version, "expected_target_item_row_version")
        _sha(draft.preview_sha256, "preview_sha256")
        _sha(draft.raw_content_sha256, "raw_content_sha256")
        _sha(draft.normalized_content_sha256, "normalized_content_sha256")
        _utc_ms(draft.created_at_ms)
        if (
            not isinstance(draft.content_md, str)
            or not 1 <= len(draft.content_md) <= _MAX_MARKDOWN_CHARACTERS
            or "\x00" in draft.content_md
            or not isinstance(draft.reason, str)
            or not 1 <= len(draft.reason.strip()) <= 2000
        ):
            raise ValueError("The approved proposal body or reason is invalid.")
        raw = draft.content_md.encode("utf-8")
        normalized = draft.content_md.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        if (
            len(raw) != draft.content_size_bytes
            or sha256(raw).hexdigest() != draft.raw_content_sha256
            or sha256(normalized).hexdigest() != draft.normalized_content_sha256
            or draft.normalizer_version != "markdown_lf_v1"
        ):
            raise ValueError("The approved proposal body does not match its evidence.")
        metadata_json = _canonical_object(draft.metadata, "metadata")
        snapshot = self.read_snapshot(
            connection, space_id=draft.space_id, proposal_id=draft.proposal_id
        )
        if (
            snapshot.finding_id != draft.finding_id
            or snapshot.proposal_state != "approved"
            or snapshot.finding_status != "approved"
            or snapshot.finding_reviewed_by != draft.actor_user_id
            or snapshot.proposal_row_version != draft.expected_proposal_row_version
            or snapshot.finding_row_version != draft.expected_finding_row_version
            or snapshot.fixed_preview_sha256 != draft.preview_sha256
            or snapshot.target_current.revision_id != draft.expected_target_revision_id
            or snapshot.target_current.item_row_version != draft.expected_target_item_row_version
            or snapshot.target_current.lifecycle_status != "active"
            or (
                snapshot.source_current is None
                and draft.expected_source_revision_id is not None
            )
            or (
                snapshot.source_current is not None
                and snapshot.source_current.revision_id != draft.expected_source_revision_id
            )
        ):
            raise ProposalConflictError(
                "The approved proposal is stale or no longer exact.",
                current_revision_id=snapshot.target_current.revision_id,
                current_row_version=snapshot.target_current.item_row_version,
            )
        operation = connection.execute(
            """SELECT 1 FROM operation_dedup WHERE id=? AND actor_user_id=?
                 AND operation_type='proposal_apply' AND operation_id=?
                 AND status='in_progress'""",
            (draft.dedup_id, draft.actor_user_id, draft.operation_id),
        ).fetchone()
        if operation is None:
            raise ProposalIntegrityError("The proposal apply reservation is missing.")
        job = connection.execute(
            """SELECT dedup_id,operation_id,actor_kind,actor_user_id,space_id,
                      job_type,handler_version,state,input_ref_json
                 FROM durable_job WHERE id=?""",
            (draft.rescan_job_id,),
        ).fetchone()
        if job is None:
            raise ProposalIntegrityError("The proposal rescan job is missing.")
        try:
            job_input = json.loads(str(_row(job, "input_ref_json")))
        except (TypeError, ValueError) as exc:
            raise ProposalIntegrityError("The proposal rescan input is invalid.") from exc
        if (
            _row(job, "dedup_id") != draft.rescan_job_dedup_id
            or _row(job, "operation_id") != draft.operation_id
            or _row(job, "actor_kind") != "user"
            or _row(job, "actor_user_id") != draft.actor_user_id
            or _row(job, "space_id") != draft.space_id
            or _row(job, "job_type") != "curation.rescan"
            or _row(job, "handler_version") != "v1"
            or _row(job, "state") != "queued"
            or job_input
            != {
                "item_id": snapshot.target.item_id,
                "revision_id": draft.result_revision_id,
                "rule_version": "curation-rescan-v1",
            }
        ):
            raise ProposalIntegrityError("The proposal rescan job binding is invalid.")
        collisions = connection.execute(
            """SELECT EXISTS(SELECT 1 FROM knowledge_revision WHERE id=?),
                      EXISTS(SELECT 1 FROM item_curation_transition WHERE id=?)""",
            (draft.result_revision_id, draft.transition_id),
        ).fetchone()
        if collisions is None or int(collisions[0]) or int(collisions[1]):
            raise ProposalConflictError("A deterministic proposal effect identity exists.")
        revision_no_row = connection.execute(
            "SELECT coalesce(max(revision_no),0)+1 FROM knowledge_revision WHERE item_id=?",
            (snapshot.target.item_id,),
        ).fetchone()
        if revision_no_row is None or int(revision_no_row[0]) < 2:
            raise ProposalIntegrityError("The target revision history is incomplete.")
        revision_no = int(revision_no_row[0])
        result_version = snapshot.target_current.item_row_version + 1
        from_status = snapshot.target_current.curation_status
        try:
            connection.execute(
                """INSERT INTO knowledge_revision(
                     id,item_id,revision_no,parent_revision_id,base_revision_id,title,
                     raw_content_sha256,normalized_content_sha256,content_size_bytes,
                     normalizer_version,metadata_json,change_kind,change_reason,
                     created_by,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,'approved_update',?,?,?)""",
                (
                    draft.result_revision_id,
                    snapshot.target.item_id,
                    revision_no,
                    draft.expected_target_revision_id,
                    draft.expected_target_revision_id,
                    snapshot.target.title,
                    draft.raw_content_sha256,
                    draft.normalized_content_sha256,
                    draft.content_size_bytes,
                    draft.normalizer_version,
                    metadata_json,
                    draft.reason,
                    draft.actor_user_id,
                    draft.created_at_ms,
                ),
            )
            if failpoint is not None:
                failpoint("proposal_apply.after_revision")
            connection.execute(
                """INSERT INTO knowledge_revision_body(
                     revision_id,item_id,content_md,content_blob_hash,
                     content_size_bytes,content_sha256,created_at)
                   VALUES (?,?,?,NULL,?,?,?)""",
                (
                    draft.result_revision_id,
                    snapshot.target.item_id,
                    draft.content_md,
                    draft.content_size_bytes,
                    draft.raw_content_sha256,
                    draft.created_at_ms,
                ),
            )
            connection.execute(
                """INSERT INTO item_curation_transition(
                     id,item_id,space_id,transition_kind,from_status,to_status,
                     base_revision_id,result_revision_id,finding_id,rescan_job_id,
                     decision_job_id,dedup_id,operation_id,actor_kind,actor_user_id,
                     result_item_row_version,reason,created_at)
                   VALUES (?,?,?,'revision_rescan',?,'provisional',?,?,NULL,?,NULL,
                     ?,?,'user',?,?,?,?)""",
                (
                    draft.transition_id,
                    snapshot.target.item_id,
                    draft.space_id,
                    from_status,
                    draft.expected_target_revision_id,
                    draft.result_revision_id,
                    draft.rescan_job_id,
                    draft.dedup_id,
                    draft.operation_id,
                    draft.actor_user_id,
                    result_version,
                    draft.reason,
                    draft.created_at_ms,
                ),
            )
            item_cursor = connection.execute(
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
                    snapshot.target.item_id,
                    draft.space_id,
                    draft.expected_target_revision_id,
                    draft.expected_target_item_row_version,
                ),
            )
            if item_cursor.rowcount != 1:
                raise ProposalConflictError("The proposal target changed before apply.")
            if failpoint is not None:
                failpoint("proposal_apply.after_current_pointer")
            finding_cursor = connection.execute(
                """UPDATE curation_finding
                      SET status='applied',applied_operation_id=?,applied_dedup_id=?,
                          applied_by=?,applied_at=?,last_application_error_json=NULL,
                          updated_at=?,row_version=row_version+1
                    WHERE id=? AND status='approved' AND row_version=?
                      AND reviewed_by=?""",
                (
                    draft.operation_id,
                    draft.dedup_id,
                    draft.actor_user_id,
                    draft.created_at_ms,
                    draft.created_at_ms,
                    snapshot.finding_id,
                    draft.expected_finding_row_version,
                    draft.actor_user_id,
                ),
            )
            if finding_cursor.rowcount != 1:
                raise ProposalConflictError("The approved finding changed during apply.")
            proposal_cursor = connection.execute(
                """UPDATE change_proposal
                      SET state='applied',applied_operation_id=?,applied_dedup_id=?,
                          last_application_error_json=NULL,updated_by=?,updated_at=?,
                          row_version=row_version+1
                    WHERE id=? AND finding_id=? AND state='approved' AND row_version=?""",
                (
                    draft.operation_id,
                    draft.dedup_id,
                    draft.actor_user_id,
                    draft.created_at_ms,
                    snapshot.proposal_id,
                    snapshot.finding_id,
                    draft.expected_proposal_row_version,
                ),
            )
            if proposal_cursor.rowcount != 1:
                raise ProposalConflictError("The approved proposal changed during apply.")
            if failpoint is not None:
                failpoint("proposal_apply.after_pair_applied")
            stale_findings = connection.execute(
                """UPDATE curation_finding
                      SET status='obsolete',applied_operation_id=NULL,
                          applied_dedup_id=NULL,applied_job_id=NULL,applied_by=NULL,
                          applied_at=NULL,last_application_error_json=NULL,
                          updated_at=?,row_version=row_version+1
                    WHERE id<>? AND space_id=?
                      AND status IN ('pending','approved','apply_failed')
                      AND ((source_item_id=? AND source_revision_id=?)
                        OR (target_item_id=? AND target_revision_id=?))""",
                (
                    draft.created_at_ms,
                    snapshot.finding_id,
                    draft.space_id,
                    snapshot.target.item_id,
                    draft.expected_target_revision_id,
                    snapshot.target.item_id,
                    draft.expected_target_revision_id,
                ),
            )
            stale_proposals = connection.execute(
                """UPDATE change_proposal
                      SET state='obsolete',applied_operation_id=NULL,
                          applied_dedup_id=NULL,last_application_error_json=NULL,
                          updated_by=?,updated_at=?,row_version=row_version+1
                    WHERE id<>? AND state IN ('pending_review','approved','apply_failed')
                      AND EXISTS (
                        SELECT 1 FROM curation_finding f
                         WHERE f.id=change_proposal.finding_id AND f.status='obsolete'
                           AND f.space_id=?
                           AND ((f.source_item_id=? AND f.source_revision_id=?)
                             OR (f.target_item_id=? AND f.target_revision_id=?)))""",
                (
                    draft.actor_user_id,
                    draft.created_at_ms,
                    snapshot.proposal_id,
                    draft.space_id,
                    snapshot.target.item_id,
                    draft.expected_target_revision_id,
                    snapshot.target.item_id,
                    draft.expected_target_revision_id,
                ),
            )
            edges = connection.execute(
                """UPDATE knowledge_edge
                      SET edge_status='inactive',needs_revalidation=1,
                          updated_by=?,updated_at=?,row_version=row_version+1
                    WHERE space_id=? AND needs_revalidation=0
                      AND ((source_item_id=? AND source_evidence_revision_id=?)
                        OR (target_item_id=? AND target_evidence_revision_id=?))""",
                (
                    draft.actor_user_id,
                    draft.created_at_ms,
                    draft.space_id,
                    snapshot.target.item_id,
                    draft.expected_target_revision_id,
                    snapshot.target.item_id,
                    draft.expected_target_revision_id,
                ),
            )
            search = connection.execute(
                "DELETE FROM search_document WHERE item_id=? AND space_id=?",
                (snapshot.target.item_id, draft.space_id),
            )
            space = connection.execute(
                """UPDATE knowledge_space
                      SET generation=generation+1,updated_at=?,row_version=row_version+1
                    WHERE id=? AND status='active' AND write_enabled=1""",
                (draft.created_at_ms, draft.space_id),
            )
            if space.rowcount != 1:
                raise ProposalConflictError("The proposal target space is no longer writable.")
        except sqlite3.IntegrityError as exc:
            raise ProposalIntegrityError("The proposal apply violates canonical policy.") from exc
        if failpoint is not None:
            failpoint("proposal_apply.after_domain_effect")
        closure = connection.execute(
            """SELECT i.current_revision_id,i.curation_status,
                      i.last_curation_transition_id,i.row_version,s.generation,
                      p.state,p.row_version AS proposal_row_version,
                      f.status,f.row_version AS finding_row_version,
                      EXISTS(SELECT 1 FROM search_document d WHERE d.item_id=i.id)
                 FROM knowledge_item i JOIN knowledge_space s ON s.id=i.space_id
                 JOIN change_proposal p ON p.id=?
                 JOIN curation_finding f ON f.id=p.finding_id
                WHERE i.id=? AND i.space_id=?""",
            (snapshot.proposal_id, snapshot.target.item_id, draft.space_id),
        ).fetchone()
        if (
            closure is None
            or tuple(closure[:4])
            != (
                draft.result_revision_id,
                "provisional",
                draft.transition_id,
                result_version,
            )
            or _row(closure, "state") != "applied"
            or _row(closure, "status") != "applied"
            or int(closure[-1]) != 0
        ):
            raise ProposalIntegrityError("The proposal apply postcondition failed.")
        return ProposalApplyWriteResult(
            snapshot.proposal_id,
            snapshot.finding_id,
            snapshot.target.item_id,
            draft.result_revision_id,
            draft.transition_id,
            draft.rescan_job_id,
            revision_no,
            result_version,
            int(_row(closure, "proposal_row_version")),
            int(_row(closure, "finding_row_version")),
            int(_row(closure, "generation")),
            stale_findings.rowcount,
            stale_proposals.rowcount,
            edges.rowcount,
            search.rowcount,
        )


__all__ = [
    "ProposalApplyDraft",
    "ProposalApplyWriteResult",
    "ProposalBodySnapshot",
    "ProposalConflictError",
    "ProposalIntegrityError",
    "ProposalNotFoundError",
    "ProposalReadSnapshot",
    "ProposalRebaseDraft",
    "ProposalRebaseWriteResult",
    "ProposalRepository",
    "ProposalRepositoryError",
    "ProposalRevisionSnapshot",
    "canonical_json_bytes",
    "fixed_preview_payload",
    "sha256_canonical",
]

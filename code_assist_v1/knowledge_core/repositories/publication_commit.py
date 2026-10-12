"""Atomic P4.3-P4.4 publication-operation persistence.

The repository consumes one already-authorized, ready publication preview and
creates the canonical publication operation plus its per-space effects inside
the caller's transaction.  It performs no scope resolution, transaction
finalization, audit, outbox, filesystem, extraction, model, or route work.

Changed stable Notion sources are fixed as T2 human-reviewed update proposals;
they never become a second canonical item or overwrite a current revision.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re
import sqlite3
from typing import Callable, Literal, Mapping, Sequence
from uuid import UUID, uuid5

from ..db import ManagedConnection, db_now_ms
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError
from .curation import (
    CurationConflictError,
    CurationIntegrityError,
    CurationRepository,
    StableSourceUpdateDraft,
)
from .wiki_authoring import WikiAuthoringRepository
from .publication_authority import (
    HigherPublicationAuthorityConflictError,
    HigherPublicationAuthorityIntegrityError,
    HigherPublicationAuthorityRepository,
    HigherPublicationOperationAuthorityDraft,
    HigherPublicationPreviewAuthorityRecord,
)


PublicationOutcome = Literal[
    "created", "provenance_added", "update_proposed", "no_op"
]
PublicationTargetKind = Literal["personal", "part", "team", "responsibility"]
PublicationTargetMode = Literal["personal", "part", "both", "explicit"]

_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_OPERATION_NAMESPACE = UUID("5468b0cb-5ba4-475e-a988-986a1ed36c43")
_LINEAGE_NAMESPACE = UUID("ef7a52e9-4135-4fd8-a916-4acf582566b8")
_ITEM_NAMESPACE = UUID("7c60a247-235a-4f27-8308-8e0a6ee46006")
_REVISION_NAMESPACE = UUID("41614c33-d8c9-4536-aedf-f5321c186d61")
_SOURCE_NAMESPACE = UUID("83606e83-f78e-4f4a-913e-1a006fe9ae31")
_MAX_SQLITE_INTEGER = 9_223_372_036_854_775_807


class PublicationCommitRepositoryError(KnowledgeCoreDatabaseError):
    code = "PUBLICATION_COMMIT_REPOSITORY_ERROR"


class PublicationCommitConflictError(PublicationCommitRepositoryError):
    code = "PUBLICATION_COMMIT_CONFLICT"


class PublicationCommitIntegrityError(PublicationCommitRepositoryError):
    code = "PUBLICATION_COMMIT_INTEGRITY_ERROR"


class PublicationUpdateProposalRequiredError(PublicationCommitRepositoryError):
    code = "PUBLICATION_UPDATE_PROPOSAL_REQUIRED"


@dataclass(frozen=True, slots=True)
class PublicationCommitTarget:
    space_id: str
    kind: PublicationTargetKind


@dataclass(frozen=True, slots=True)
class PublicationCommitDraft:
    dedup_id: str
    actor_user_id: str
    operation_id: str
    request_sha256: str
    preview_id: str
    artifact_id: str
    extraction_snapshot_id: str
    source_snapshot_sha256: str
    expected_preview_row_version: int
    target_mode: PublicationTargetMode
    targets: tuple[PublicationCommitTarget, ...]
    created_at_ms: int
    higher_authority: HigherPublicationPreviewAuthorityRecord | None = None
    frozen_source: "FrozenPublicationSource | None" = None


@dataclass(frozen=True, slots=True)
class PublicationTargetResult:
    target_space_id: str
    outcome: PublicationOutcome
    item_id: str
    resolved_revision_id: str
    created_revision_id: str | None
    source_artifact_id: str
    curation_finding_id: str | None
    change_proposal_id: str | None

    def to_mapping(self) -> dict[str, object]:
        return {
            "created_revision_id": self.created_revision_id,
            "finding_id": self.curation_finding_id,
            "item_id": self.item_id,
            "outcome": self.outcome,
            "resolved_revision_id": self.resolved_revision_id,
            "space_id": self.target_space_id,
            "source_artifact_id": self.source_artifact_id,
        }


@dataclass(frozen=True, slots=True)
class PublicationCommitResult:
    publication_operation_id: str
    lineage_group_id: str | None
    preview_id: str
    source_artifact_id: str
    target_mode: PublicationTargetMode
    targets: tuple[PublicationTargetResult, ...]
    authored_snapshot_id: str | None = None
    reused_extraction_snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class FrozenPublicationSource:
    title: str
    raw_sha256: str
    normalized_sha256: str
    size_bytes: int
    normalizer_version: str
    content_md: str | None
    content_blob_hash: str | None
    content_sha256: str
    origin_type: str
    original_filename: str | None
    origin_key: str
    origin_revision_key: str
    publisher_user_id: str
    captured_part_id: str | None
    notion_owner_user_id: str | None
    notion_page_id: str | None
    notion_revision: int | None
    notion_updated_at: int | None
    notion_asset_manifest_sha256: str | None
    notion_source_snapshot_sha256: str | None
    source_metadata_json: str
    # Existing fields describe the selected canonical body. Source provenance
    # and author-created references always remain on this exact extraction.
    extraction_source: "FrozenPublicationSource | None" = None
    authored_snapshot_id: str | None = None
    authored_fence: tuple[object, ...] | None = None
    authored_preparation: "FrozenPublicationSource | None" = None
    reuse_targets: tuple[PublicationCommitTarget, ...] = ()
    reuse_fence: tuple[tuple[object, ...], ...] | None = None
    reused_extraction_snapshot_id: str | None = None

    @property
    def reference_source_md(self) -> str:
        source = self.extraction_source or self
        return source.content_md or ""


@dataclass(frozen=True, slots=True)
class _SourceBinding:
    actor_user_id: str
    preview_id: str
    artifact_id: str
    extraction_snapshot_id: str
    source_snapshot_sha256: str
    created_at_ms: int


def _uuid(value: object, *, field: str) -> str:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{field} must be a canonical lowercase UUID.") from None
    canonical = str(parsed)
    if not isinstance(value, str) or value != canonical:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return canonical


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("actor_user_id is invalid.")
    return value


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 value.")
    return value


def _now(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_SQLITE_INTEGER
    ):
        raise ValueError("created_at_ms must be a non-negative integer.")
    return value


def _canonical(value: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Publication metadata must be finite JSON.") from exc
    if type(json.loads(encoded)) is not dict:
        raise ValueError("Publication metadata must be an object.")
    return encoded


def _derived(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, ":".join(parts)))


def publication_operation_id(actor_user_id: str, operation_id: str) -> str:
    actor = _user(actor_user_id)
    operation = _uuid(operation_id, field="operation_id")
    return _derived(_OPERATION_NAMESPACE, actor, operation)


def publication_lineage_group_id(actor_user_id: str, operation_id: str) -> str:
    actor = _user(actor_user_id)
    operation = _uuid(operation_id, field="operation_id")
    return _derived(_LINEAGE_NAMESPACE, actor, operation)


def _target_ids(actor: str, operation: str, space_id: str) -> tuple[str, str, str]:
    identity = (actor, operation, space_id)
    return (
        _derived(_ITEM_NAMESPACE, *identity),
        _derived(_REVISION_NAMESPACE, *identity),
        _derived(_SOURCE_NAMESPACE, *identity),
    )


def _prepare(value: PublicationCommitDraft) -> PublicationCommitDraft:
    if not isinstance(value, PublicationCommitDraft):
        raise TypeError("A PublicationCommitDraft is required.")
    dedup = _uuid(value.dedup_id, field="dedup_id")
    actor = _user(value.actor_user_id)
    operation = _uuid(value.operation_id, field="operation_id")
    request_sha = _sha(value.request_sha256, field="request_sha256")
    preview = _uuid(value.preview_id, field="preview_id")
    artifact = _uuid(value.artifact_id, field="artifact_id")
    extraction = _uuid(value.extraction_snapshot_id, field="extraction_snapshot_id")
    source_snapshot = _sha(
        value.source_snapshot_sha256, field="source_snapshot_sha256"
    )
    preview_row_version = value.expected_preview_row_version
    if (
        isinstance(preview_row_version, bool)
        or not isinstance(preview_row_version, int)
        or preview_row_version < 1
    ):
        raise ValueError("expected_preview_row_version must be positive.")
    if value.target_mode not in {"personal", "part", "both", "explicit"}:
        raise ValueError("target_mode is invalid.")
    if not isinstance(value.targets, tuple):
        raise ValueError("Publication targets do not match target_mode.")
    actual_kinds = tuple(
        target.kind if isinstance(target, PublicationCommitTarget) else None
        for target in value.targets
    )
    if value.target_mode == "explicit":
        authority = value.higher_authority
        if (
            not isinstance(authority, HigherPublicationPreviewAuthorityRecord)
            or actual_kinds != (authority.target_space_kind,)
            or len(value.targets) != 1
            or value.targets[0].space_id != authority.target_space_id
        ):
            raise ValueError("The explicit publication authority is not exact.")
    elif value.higher_authority is not None or actual_kinds != {
        "personal": ("personal",),
        "part": ("part",),
        "both": ("personal", "part"),
    }[value.target_mode]:
        raise ValueError("Publication targets do not match target_mode.")
    seen: set[str] = set()
    targets: list[PublicationCommitTarget] = []
    for target in value.targets:
        space_id = _uuid(target.space_id, field="target.space_id")
        if space_id in seen:
            raise ValueError("Publication target spaces must be unique.")
        seen.add(space_id)
        targets.append(PublicationCommitTarget(space_id, target.kind))
    if value.frozen_source is not None and not isinstance(
        value.frozen_source, FrozenPublicationSource
    ):
        raise TypeError("frozen_source must be a FrozenPublicationSource.")
    return PublicationCommitDraft(
        dedup,
        actor,
        operation,
        request_sha,
        preview,
        artifact,
        extraction,
        source_snapshot,
        preview_row_version,
        value.target_mode,
        tuple(targets),
        _now(value.created_at_ms),
        value.higher_authority,
        value.frozen_source,
    )


def _require_write(connection: ManagedConnection) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("PublicationCommitRepository requires a ManagedConnection.")
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Publication commit writes require an existing managed transaction."
        )
    return connection


def _value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, TypeError, IndexError) as exc:
        raise PublicationCommitIntegrityError(
            "A canonical publication row cannot be mapped."
        ) from exc


class PublicationCommitRepository:
    """Create and verify one immutable publication operation."""

    __slots__ = ("_authority", "_curation")

    def __init__(
        self,
        curation: CurationRepository | None = None,
        authority: HigherPublicationAuthorityRepository | None = None,
    ) -> None:
        if curation is not None and not isinstance(curation, CurationRepository):
            raise TypeError("curation must be a CurationRepository.")
        if authority is not None and not isinstance(
            authority, HigherPublicationAuthorityRepository
        ):
            raise TypeError(
                "authority must be a HigherPublicationAuthorityRepository."
            )
        self._curation = curation or CurationRepository()
        self._authority = authority or HigherPublicationAuthorityRepository()

    def freeze_source(
        self,
        connection: ManagedConnection,
        *,
        actor_user_id: str,
        preview_id: str,
        artifact_id: str,
        extraction_snapshot_id: str,
        source_snapshot_sha256: str,
        effective_at_ms: int,
        authored_snapshot_id: str | None = None,
        authorized_targets: tuple[PublicationCommitTarget, ...] = (),
    ) -> FrozenPublicationSource:
        """Read and hash the immutable source before a write transaction opens."""

        if not isinstance(connection, ManagedConnection):
            raise TypeError("PublicationCommitRepository requires a ManagedConnection.")
        binding = _SourceBinding(
            _user(actor_user_id),
            _uuid(preview_id, field="preview_id"),
            _uuid(artifact_id, field="artifact_id"),
            _uuid(extraction_snapshot_id, field="extraction_snapshot_id"),
            _sha(source_snapshot_sha256, field="source_snapshot_sha256"),
            _now(effective_at_ms),
        )
        source = self._source(connection, binding)
        if authored_snapshot_id is None:
            return source
        snapshot = _uuid(authored_snapshot_id, field="authored_snapshot_id")
        fence = self._authored_source_fence(connection, binding, snapshot)
        row = connection.execute(
            "SELECT content_md,manifest_json FROM authored_wiki_snapshot_body WHERE snapshot_id=?",
            (snapshot,),
        ).fetchone()
        if row is None:
            raise PublicationCommitConflictError("The selected authored Wiki body is unavailable.")
        content, manifest_json = row
        try:
            encoded = content.encode("utf-8")
            manifest_bytes = manifest_json.encode("utf-8")
            manifest = json.loads(manifest_json)
            if ("\r" in content or len(encoded) != fence[4] or hashlib.sha256(encoded).hexdigest() != fence[3]
                    or len(manifest_bytes) != fence[6]
                    or hashlib.sha256(manifest_bytes).hexdigest() != fence[5]
                    or manifest.get("source_snapshot_id") != binding.extraction_snapshot_id
                    or manifest.get("source_sha256") != binding.source_snapshot_sha256
                    or manifest.get("all_chunks_processed") is not True
                    or manifest.get("rendered_markdown_sha256") != fence[3]):
                raise ValueError("Authored Wiki identities disagree.")
        except (AttributeError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise PublicationCommitIntegrityError("The selected authored Wiki is inconsistent.") from exc
        if self._authored_source_fence(connection, binding, snapshot) != fence:
            raise PublicationCommitConflictError("The selected authored Wiki changed during preparation.")
        if source.origin_type == "notion":
            raise PublicationCommitConflictError("Authored stable-source updates are not yet integrated.")
        prepared = replace(source, raw_sha256=fence[3], normalized_sha256=hashlib.sha256(
            content.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest(),
            size_bytes=len(encoded), normalizer_version="markdown_lf_v1", content_md=content,
            content_blob_hash=None, content_sha256=fence[3], extraction_source=source,
            authored_snapshot_id=snapshot, authored_fence=fence)
        if source.origin_type != "upload" or not authorized_targets:
            return prepared
        matches = self._reusable_upload_sources(connection, binding, authorized_targets)
        if not matches:
            return replace(prepared, reuse_targets=authorized_targets, reuse_fence=())
        # All matching target histories must agree. Pick the earliest already
        # published body, never this new model response merely because it differs.
        chosen = matches[0]
        content_row = connection.execute(
            "SELECT content_md FROM knowledge_revision_body WHERE revision_id=? AND item_id=?",
            (chosen[2], chosen[1])).fetchone()
        if content_row is None or not isinstance(content_row[0], str):
            raise PublicationCommitConflictError("The reusable canonical Wiki body is unavailable.")
        reused = content_row[0]
        if ((chosen[8] is not None and "\r" in reused) or len(reused.encode("utf-8")) != chosen[13]
                or hashlib.sha256(reused.encode("utf-8")).hexdigest() != chosen[12]
                or hashlib.sha256(reused.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest() != chosen[24]):
            raise PublicationCommitIntegrityError("The reusable canonical Wiki failed integrity validation.")
        if self._reusable_upload_sources(connection, binding, authorized_targets) != matches:
            raise PublicationCommitConflictError("The reusable Wiki changed during preparation.")
        selected_fence = chosen[9:17] if chosen[8] is not None else None
        return replace(prepared, content_md=reused, raw_sha256=chosen[12], normalized_sha256=chosen[24],
            size_bytes=chosen[13], content_sha256=chosen[12], authored_snapshot_id=chosen[8],
            authored_fence=selected_fence, authored_preparation=prepared,
            normalizer_version=chosen[25],
            reused_extraction_snapshot_id=chosen[26] if chosen[8] is None else None,
            reuse_targets=authorized_targets, reuse_fence=matches)

    @staticmethod
    def _reusable_upload_sources(connection, binding, targets):
        """Metadata only, scoped to caller-resolved native publication targets.

        Never load a previous author's source/preview/manifest text. A zero-hit
        result is also fenced so a concurrent first publication cannot duplicate
        a newly generated, differently worded Wiki.
        """
        spaces = tuple(target.space_id for target in targets)
        if not spaces or len(spaces) > 2 or len(set(spaces)) != len(spaces):
            raise PublicationCommitIntegrityError("The reuse publication targets are invalid.")
        rows = connection.execute(f"""WITH matches AS (
            SELECT DISTINCT i.space_id,i.id AS item_id,i.current_revision_id,
            i.row_version,i.lifecycle_status,i.curation_status,p.id AS operation_id,p.created_at AS operation_created,
            s.id AS snapshot_id,r.id AS run_id,r.row_version AS run_version,r.plan_sha256,
            CASE WHEN sel.authored_snapshot_id IS NULL THEN px.extracted_sha256 ELSE s.content_sha256 END AS content_sha256,
            CASE WHEN sel.authored_snapshot_id IS NULL THEN px.extracted_size_bytes ELSE s.content_size_bytes END AS content_size_bytes,
            s.manifest_sha256,s.manifest_size_bytes,s.created_at AS snapshot_created,
            v.raw_content_sha256,v.normalized_content_sha256,v.content_size_bytes AS revision_size,
            b.content_sha256 AS body_sha256,b.content_size_bytes AS body_size,
            v.normalizer_version AS revision_normalizer,
            CASE WHEN sel.authored_snapshot_id IS NULL THEN px.normalized_content_sha256 ELSE s.content_sha256 END AS selected_normalized_sha256,
            CASE WHEN sel.authored_snapshot_id IS NULL THEN px.normalizer_version ELSE 'markdown_lf_v1' END AS selected_normalizer,
            px.id AS selected_extraction_id,
            initial.raw_content_sha256 AS initial_sha256,initial.normalized_content_sha256 AS initial_normalized_sha256,
            initial.content_size_bytes AS initial_size,initial.normalizer_version AS initial_normalizer,
            CASE WHEN sel.authored_snapshot_id IS NOT NULL AND (s.id IS NULL OR r.status IS NOT 'ready') THEN 1 ELSE 0 END AS invalid_selection
            FROM source_artifact a JOIN extraction_snapshot x ON x.artifact_id=a.id AND x.id=?
            JOIN source_artifact prior ON prior.origin_type='upload'
              AND prior.raw_sha256=a.raw_sha256 AND prior.raw_size_bytes=a.raw_size_bytes
              AND prior.media_type=a.media_type AND prior.original_filename=a.original_filename
            JOIN extraction_snapshot px ON px.artifact_id=prior.id AND px.status='ready'
              AND px.extracted_sha256=x.extracted_sha256 AND px.extracted_size_bytes=x.extracted_size_bytes
              AND px.normalized_content_sha256=x.normalized_content_sha256
              AND px.normalizer_version=x.normalizer_version
              AND px.extractor_name=x.extractor_name AND px.extractor_version=x.extractor_version
              AND px.warnings_json=x.warnings_json
            JOIN item_source l ON l.artifact_id=prior.id AND l.extraction_snapshot_id=px.id
            JOIN knowledge_item i ON i.id=l.item_id
            LEFT JOIN knowledge_revision v ON v.id=i.current_revision_id AND v.item_id=i.id
            LEFT JOIN knowledge_revision_body b ON b.revision_id=v.id AND b.item_id=i.id
            JOIN publication_target t ON t.item_id=i.id AND t.resolved_revision_id=l.revision_id
            JOIN publication_operation p ON p.id=t.publication_operation_id
              AND p.artifact_id=prior.id AND p.extraction_snapshot_id=px.id
            LEFT JOIN knowledge_revision initial ON initial.id=t.resolved_revision_id AND initial.item_id=i.id
            LEFT JOIN publication_authored_selection sel ON sel.publication_operation_id=p.id
            LEFT JOIN authored_wiki_snapshot s ON s.id=sel.authored_snapshot_id
            LEFT JOIN publication_authoring_run r ON r.id=s.run_id
            WHERE a.id=? AND a.origin_type='upload' AND x.extracted_sha256=?
              AND i.space_id IN ({','.join('?' for _ in spaces)})),
            summaries AS (SELECT space_id,count(DISTINCT item_id) AS item_count,
              sum(CASE WHEN lifecycle_status<>'active' OR curation_status NOT IN ('published','provisional')
                OR invalid_selection<>0
                OR initial_sha256 IS NOT content_sha256 OR initial_normalized_sha256 IS NOT selected_normalized_sha256
                OR initial_size IS NOT content_size_bytes OR initial_normalizer IS NOT selected_normalizer
                OR raw_content_sha256 IS NOT content_sha256 OR normalized_content_sha256 IS NOT selected_normalized_sha256
                OR revision_normalizer IS NOT selected_normalizer OR selected_normalizer<>'markdown_lf_v1'
                OR revision_size IS NOT content_size_bytes OR body_sha256 IS NOT content_sha256
                OR body_size IS NOT content_size_bytes THEN 1 ELSE 0 END) AS invalid_count
              FROM matches GROUP BY space_id),
            ranked AS (SELECT *,row_number() OVER(PARTITION BY space_id
              ORDER BY operation_created,operation_id,item_id,snapshot_id) AS choice FROM matches)
            SELECT ranked.space_id,item_id,current_revision_id,row_version,lifecycle_status,curation_status,
              operation_id,operation_created,snapshot_id,run_id,run_version,plan_sha256,content_sha256,
              content_size_bytes,manifest_sha256,manifest_size_bytes,snapshot_created,raw_content_sha256,
              normalized_content_sha256,revision_size,body_sha256,body_size,item_count,invalid_count,
              selected_normalized_sha256,selected_normalizer,selected_extraction_id
            FROM ranked JOIN summaries ON summaries.space_id=ranked.space_id WHERE choice=1
            ORDER BY operation_created,operation_id,ranked.space_id,item_id,snapshot_id""",
            (binding.extraction_snapshot_id, binding.artifact_id, binding.source_snapshot_sha256, *spaces)).fetchall()
        matches = tuple(tuple(row) for row in rows)
        for row in matches:
            if (row[4] != "active" or row[5] not in {"published", "provisional"}
                    or row[23] != 0 or row[17:22] != (row[12], row[24], row[13], row[12], row[13])):
                raise PublicationCommitConflictError("Existing source knowledge was revised or is unavailable; explicit resolution is required.")
        if any(row[22] > 1 for row in matches) or len({(row[12], row[13], row[24], row[25]) for row in matches}) > 1:
            raise PublicationCommitConflictError("Publication targets have conflicting prior source knowledge.")
        return matches

    @staticmethod
    def _authored_source_fence(connection, binding, snapshot_id):
        # No extraction, plan, Wiki or manifest text is read by the writer.
        exists = connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='authored_wiki_snapshot'").fetchone()
        if exists is None:
            raise PublicationCommitConflictError("Authored publication storage is not activated.")
        row = connection.execute("""SELECT r.id,r.row_version,r.plan_sha256,
            s.content_sha256,s.content_size_bytes,s.manifest_sha256,s.manifest_size_bytes,
            s.created_at,p.row_version,u.scope_epoch
            FROM authored_wiki_snapshot s JOIN authored_wiki_snapshot_body b ON b.snapshot_id=s.id
            JOIN publication_authoring_run r ON r.id=s.run_id
            JOIN publication_preview p ON p.id=r.preview_id
            JOIN app_user u ON u.id=r.actor_user_id AND u.account_status='active'
              AND u.scope_epoch=p.captured_scope_epoch
            WHERE s.id=? AND r.actor_user_id=? AND r.preview_id=? AND r.artifact_id=?
              AND r.extraction_snapshot_id=? AND r.source_sha256=? AND r.status='ready'
              AND p.status='ready' AND p.expires_at>?
              AND p.actor_user_id=r.actor_user_id AND p.artifact_id=r.artifact_id
              AND p.extraction_snapshot_id=r.extraction_snapshot_id
              AND p.source_snapshot_sha256=r.source_sha256""",
            (snapshot_id, binding.actor_user_id, binding.preview_id, binding.artifact_id,
             binding.extraction_snapshot_id, binding.source_snapshot_sha256,
             max(binding.created_at_ms, db_now_ms(connection)))).fetchone()
        if row is None:
            raise PublicationCommitConflictError("The selected authored Wiki is not ready and source-bound.")
        return tuple(row)

    @staticmethod
    def _revalidate_frozen_source(
        connection: ManagedConnection,
        draft: PublicationCommitDraft,
        source: FrozenPublicationSource,
    ) -> None:
        """Fence immutable source metadata without fetching the full body again."""

        if source.authored_snapshot_id is not None or source.authored_preparation is not None:
            if source.reuse_fence is not None:
                if (source.reuse_targets != draft.targets or source.reuse_fence !=
                        PublicationCommitRepository._reusable_upload_sources(connection, draft, draft.targets)):
                    raise PublicationCommitConflictError("The stable-source reuse preparation is stale.")
            selected = source
            source = source.authored_preparation or source
            if (source.extraction_source is None or source.extraction_source.extraction_source is not None
                    or source.authored_fence != PublicationCommitRepository._authored_source_fence(
                        connection, draft, source.authored_snapshot_id)
                    or source.content_sha256 != source.authored_fence[3]
                    or source.raw_sha256 != source.authored_fence[3]
                    or source.size_bytes != source.authored_fence[4]):
                raise PublicationCommitConflictError("The authored publication preparation changed.")
            if selected.authored_preparation is not None:
                chosen = selected.reuse_fence[0] if selected.reuse_fence else None
                if (chosen is None or selected.authored_snapshot_id != chosen[8]
                        or selected.authored_fence != (chosen[9:17] if chosen[8] is not None else None)
                        or selected.reused_extraction_snapshot_id != (chosen[26] if chosen[8] is None else None)
                        or selected.content_sha256 != chosen[12] or selected.raw_sha256 != chosen[12]
                        or selected.normalized_sha256 != chosen[24] or selected.size_bytes != chosen[13]
                        or selected.normalizer_version != chosen[25] or selected.content_blob_hash is not None
                        or selected.extraction_source != source.extraction_source):
                    raise PublicationCommitConflictError("The reused publication body is inconsistent.")
            source = source.extraction_source
        row = connection.execute(
            """SELECT artifact.publisher_user_id,artifact.origin_type,
                      artifact.original_filename,
                      artifact.origin_key,artifact.origin_revision_key,
                      artifact.metadata_json,artifact.body_retention_status,
                      snapshot.status,snapshot.extracted_sha256,
                      snapshot.normalized_content_sha256,
                      snapshot.extracted_size_bytes,snapshot.normalizer_version,
                      snapshot.body_retention_status AS snapshot_retention,
                      CASE WHEN body.content_md IS NULL THEN 0 ELSE 1 END AS inline_body,
                      length(CAST(body.content_md AS BLOB)) AS inline_size,
                      body.content_blob_hash,body.content_size_bytes,
                      body.content_sha256,preview.captured_part_id,
                      json_extract(preview.ready_result_json,'$.title') AS title
               FROM publication_preview AS preview
               JOIN source_artifact AS artifact ON artifact.id=preview.artifact_id
               JOIN extraction_snapshot AS snapshot
                 ON snapshot.id=preview.extraction_snapshot_id
                AND snapshot.artifact_id=preview.artifact_id
               JOIN extraction_snapshot_body AS body
                 ON body.snapshot_id=snapshot.id AND body.artifact_id=snapshot.artifact_id
               WHERE preview.id=? AND preview.actor_user_id=?
                 AND preview.artifact_id=? AND preview.extraction_snapshot_id=?
                 AND preview.source_snapshot_sha256=?
                 AND preview.status='ready' AND preview.expires_at>?""",
            (
                draft.preview_id,
                draft.actor_user_id,
                draft.artifact_id,
                draft.extraction_snapshot_id,
                draft.source_snapshot_sha256,
                draft.created_at_ms,
            ),
        ).fetchone()
        if row is None:
            raise PublicationCommitConflictError(
                "The frozen publication source is no longer ready and exact."
            )
        actual = (
            _value(row, "publisher_user_id"),
            _value(row, "origin_type"),
            _value(row, "original_filename"),
            str(_value(row, "origin_key")),
            str(_value(row, "origin_revision_key")),
            _value(row, "metadata_json"),
            _value(row, "body_retention_status"),
            _value(row, "status"),
            _value(row, "extracted_sha256"),
            _value(row, "normalized_content_sha256"),
            _value(row, "extracted_size_bytes"),
            _value(row, "normalizer_version"),
            _value(row, "snapshot_retention"),
            _value(row, "inline_body"),
            _value(row, "inline_size"),
            _value(row, "content_blob_hash"),
            _value(row, "content_size_bytes"),
            _value(row, "content_sha256"),
            _value(row, "captured_part_id"),
            str(_value(row, "title")).strip(),
        )
        expected = (
            source.publisher_user_id,
            source.origin_type,
            source.original_filename,
            source.origin_key,
            source.origin_revision_key,
            source.source_metadata_json,
            "present",
            "ready",
            source.raw_sha256,
            source.normalized_sha256,
            source.size_bytes,
            source.normalizer_version,
            "present",
            1 if source.content_md is not None else 0,
            source.size_bytes if source.content_md is not None else None,
            source.content_blob_hash,
            source.size_bytes,
            source.content_sha256,
            source.captured_part_id,
            source.title,
        )
        if actual != expected:
            raise PublicationCommitConflictError(
                "The frozen publication source changed before commit."
            )

    @staticmethod
    def _source(
        connection: ManagedConnection,
        draft: PublicationCommitDraft | _SourceBinding,
    ) -> FrozenPublicationSource:
        row = connection.execute(
            """SELECT artifact.publisher_user_id,artifact.origin_type,
                      artifact.original_filename,
                      artifact.origin_key,artifact.origin_revision_key,
                      artifact.metadata_json,artifact.body_retention_status,
                      snapshot.status,snapshot.extracted_sha256,
                      snapshot.normalized_content_sha256,
                      snapshot.extracted_size_bytes,snapshot.normalizer_version,
                      snapshot.body_retention_status AS snapshot_retention,
                      body.content_md,body.content_blob_hash,
                      body.content_size_bytes,body.content_sha256,
                      preview.captured_part_id,
                      json_extract(preview.ready_result_json,'$.title') AS title
               FROM publication_preview AS preview
               JOIN source_artifact AS artifact ON artifact.id=preview.artifact_id
               JOIN extraction_snapshot AS snapshot
                 ON snapshot.id=preview.extraction_snapshot_id
                AND snapshot.artifact_id=preview.artifact_id
               JOIN extraction_snapshot_body AS body
                 ON body.snapshot_id=snapshot.id AND body.artifact_id=snapshot.artifact_id
               WHERE preview.id=? AND preview.actor_user_id=?
                 AND preview.artifact_id=? AND preview.extraction_snapshot_id=?
                 AND preview.source_snapshot_sha256=?
                 AND preview.status='ready' AND preview.expires_at>?""",
            (
                draft.preview_id,
                draft.actor_user_id,
                draft.artifact_id,
                draft.extraction_snapshot_id,
                draft.source_snapshot_sha256,
                draft.created_at_ms,
            ),
        ).fetchone()
        if row is None:
            raise PublicationCommitConflictError(
                "The publication preview is not ready and exact."
            )
        title = _value(row, "title")
        normalized = _value(row, "normalized_content_sha256")
        raw = _value(row, "extracted_sha256")
        size = _value(row, "extracted_size_bytes")
        normalizer = _value(row, "normalizer_version")
        body_size = _value(row, "content_size_bytes")
        body_sha = _value(row, "content_sha256")
        content_md = _value(row, "content_md")
        content_blob = _value(row, "content_blob_hash")
        if (
            _value(row, "publisher_user_id") != draft.actor_user_id
            or _value(row, "body_retention_status") != "present"
            or _value(row, "snapshot_retention") != "present"
            or _value(row, "status") != "ready"
            or not isinstance(title, str)
            or not title.strip()
            or len(title.strip()) > 500
            or not isinstance(raw, str)
            or _SHA_RE.fullmatch(raw) is None
            or not isinstance(normalized, str)
            or _SHA_RE.fullmatch(normalized) is None
            or type(size) is not int
            or size < 0
            or size != body_size
            or body_sha != raw
            or not isinstance(normalizer, str)
            or not normalizer.strip()
            or ((content_md is None) == (content_blob is None))
            or (content_md is not None and not isinstance(content_md, str))
            or (
                content_blob is not None
                and (
                    not isinstance(content_blob, str)
                    or _SHA_RE.fullmatch(content_blob) is None
                )
            )
        ):
            raise PublicationCommitIntegrityError(
                "The ready preview source evidence is inconsistent."
            )
        origin_type = str(_value(row, "origin_type"))
        origin_key = str(_value(row, "origin_key"))
        origin_revision_key = str(_value(row, "origin_revision_key"))
        notion_owner: str | None = None
        notion_page: str | None = None
        notion_revision: int | None = None
        notion_updated_at: int | None = None
        notion_asset_sha: str | None = None
        notion_snapshot_sha: str | None = None
        if origin_type == "notion":
            try:
                metadata = json.loads(str(_value(row, "metadata_json")))
            except (json.JSONDecodeError, RecursionError) as exc:
                raise PublicationCommitIntegrityError(
                    "The Notion source provenance is invalid."
                ) from exc
            provenance = (
                metadata.get("notion_provenance")
                if isinstance(metadata, dict)
                else None
            )
            if not isinstance(provenance, dict) or set(provenance) != {
                "asset_manifest_sha256",
                "owner_user_id",
                "page_id",
                "revision",
                "source_snapshot_sha256",
                "updated_at",
            }:
                raise PublicationCommitIntegrityError(
                    "The Notion source provenance is incomplete."
                )
            notion_owner_value = provenance.get("owner_user_id")
            notion_page_value = provenance.get("page_id")
            notion_revision_value = provenance.get("revision")
            notion_updated_value = provenance.get("updated_at")
            notion_asset_value = provenance.get("asset_manifest_sha256")
            notion_snapshot_value = provenance.get("source_snapshot_sha256")
            if (
                not isinstance(notion_owner_value, str)
                or _USER_RE.fullmatch(notion_owner_value) is None
                or not isinstance(notion_page_value, str)
                or not notion_page_value
                or notion_page_value != notion_page_value.strip()
                or len(notion_page_value) > 1000
                or (
                    notion_revision_value is not None
                    and (
                        isinstance(notion_revision_value, bool)
                        or not isinstance(notion_revision_value, int)
                        or notion_revision_value < 0
                    )
                )
                or (
                    notion_updated_value is not None
                    and (
                        isinstance(notion_updated_value, bool)
                        or not isinstance(notion_updated_value, int)
                        or notion_updated_value < 0
                    )
                )
                or not isinstance(notion_asset_value, str)
                or _SHA_RE.fullmatch(notion_asset_value) is None
                or not isinstance(notion_snapshot_value, str)
                or _SHA_RE.fullmatch(notion_snapshot_value) is None
                or origin_key
                != f"notion:{notion_owner_value}:{notion_page_value}"
                or origin_revision_key != notion_snapshot_value
            ):
                raise PublicationCommitIntegrityError(
                    "The Notion source provenance is inconsistent."
                )
            notion_owner = notion_owner_value
            notion_page = notion_page_value
            notion_revision = notion_revision_value
            notion_updated_at = notion_updated_value
            notion_asset_sha = notion_asset_value
            notion_snapshot_sha = notion_snapshot_value
        captured_part = _value(row, "captured_part_id")
        if captured_part is not None:
            captured_part = str(captured_part)
        source = FrozenPublicationSource(
            title.strip(),
            raw,
            normalized,
            size,
            normalizer,
            content_md if isinstance(content_md, str) else None,
            content_blob if isinstance(content_blob, str) else None,
            body_sha,
            origin_type,
            (
                str(_value(row, "original_filename"))
                if _value(row, "original_filename") is not None
                else None
            ),
            origin_key,
            origin_revision_key,
            str(_value(row, "publisher_user_id")),
            captured_part,
            notion_owner,
            notion_page,
            notion_revision,
            notion_updated_at,
            notion_asset_sha,
            notion_snapshot_sha,
            str(_value(row, "metadata_json")),
        )
        if source.content_md is not None:
            encoded = source.content_md.encode("utf-8")
            if (
                len(encoded) != source.size_bytes
                or hashlib.sha256(encoded).hexdigest() != source.content_sha256
            ):
                raise PublicationCommitIntegrityError(
                    "The ready preview Markdown body is inconsistent."
                )
        return source

    @staticmethod
    def _verify_targets(
        connection: ManagedConnection, draft: PublicationCommitDraft
    ) -> None:
        for target in draft.targets:
            row = connection.execute(
                """SELECT space_kind,owner_user_id,owner_org_unit_id,status,
                          write_enabled
                   FROM knowledge_space WHERE id=?""",
                (target.space_id,),
            ).fetchone()
            if row is None or (
                _value(row, "space_kind") != target.kind
                or _value(row, "status") != "active"
                or _value(row, "write_enabled") != 1
                or (
                    target.kind == "personal"
                    and (
                        _value(row, "owner_user_id") != draft.actor_user_id
                        or _value(row, "owner_org_unit_id") is not None
                    )
                )
                or (
                    target.kind in {"part", "team", "responsibility"}
                    and (
                        _value(row, "owner_user_id") is not None
                        or _value(row, "owner_org_unit_id") is None
                    )
                )
            ):
                raise PublicationCommitConflictError(
                    "A publication target is no longer writable and exact."
                )

    @staticmethod
    def _same_source(
        connection: ManagedConnection,
        draft: PublicationCommitDraft,
        target: PublicationCommitTarget,
    ) -> tuple[str, str] | None:
        rows = connection.execute(
            """SELECT item.id AS item_id,item.current_revision_id AS revision_id
               FROM item_source AS source
               JOIN knowledge_item AS item ON item.id=source.item_id
               WHERE source.artifact_id=? AND source.extraction_snapshot_id=?
                 AND source.revision_id=item.current_revision_id
                 AND item.space_id=? AND item.lifecycle_status='active'
               ORDER BY item.id""",
            (draft.artifact_id, draft.extraction_snapshot_id, target.space_id),
        ).fetchall()
        if len(rows) > 1:
            raise PublicationCommitIntegrityError(
                "One source snapshot resolves to multiple current target items."
            )
        if not rows:
            return None
        return str(_value(rows[0], "item_id")), str(_value(rows[0], "revision_id"))

    @staticmethod
    def _stable_origin_changed(
        connection: ManagedConnection,
        draft: PublicationCommitDraft,
        source: FrozenPublicationSource,
        target: PublicationCommitTarget,
    ) -> tuple[str, str] | None:
        rows = connection.execute(
            """SELECT DISTINCT item.id AS item_id,
                      item.current_revision_id AS revision_id
               FROM item_source AS link
               JOIN source_artifact AS prior ON prior.id=link.artifact_id
               JOIN knowledge_item AS item ON item.id=link.item_id
               WHERE item.space_id=? AND item.lifecycle_status='active'
                 AND prior.origin_type=? AND prior.origin_key=?
                 AND prior.origin_revision_key<>?
               ORDER BY item.id""",
            (
                target.space_id,
                source.origin_type,
                source.origin_key,
                source.origin_revision_key,
            ),
        ).fetchall()
        if len(rows) > 1:
            raise PublicationCommitIntegrityError(
                "One stable source resolves to multiple current target items."
            )
        if not rows:
            return None
        return (
            str(_value(rows[0], "item_id")),
            str(_value(rows[0], "revision_id")),
        )

    @staticmethod
    def _same_origin_revision(
        connection: ManagedConnection,
        source: FrozenPublicationSource,
        target: PublicationCommitTarget,
    ) -> tuple[str, str] | None:
        rows = connection.execute(
            """SELECT item.id AS item_id,item.current_revision_id AS revision_id,
                      revision.raw_content_sha256,
                      revision.normalized_content_sha256,
                      revision.content_size_bytes,revision.normalizer_version,
                      body.content_md,body.content_blob_hash,
                      body.content_size_bytes AS body_size,
                      body.content_sha256 AS body_sha
               FROM item_source AS link
               JOIN source_artifact AS prior ON prior.id=link.artifact_id
               JOIN knowledge_item AS item ON item.id=link.item_id
               JOIN knowledge_revision AS revision
                 ON revision.id=item.current_revision_id AND revision.item_id=item.id
               JOIN knowledge_revision_body AS body
                 ON body.revision_id=revision.id AND body.item_id=item.id
               WHERE item.space_id=? AND item.lifecycle_status='active'
                 AND link.revision_id=item.current_revision_id
                 AND prior.origin_type=? AND prior.origin_key=?
                 AND prior.origin_revision_key=?
               ORDER BY item.id""",
            (
                target.space_id,
                source.origin_type,
                source.origin_key,
                source.origin_revision_key,
            ),
        ).fetchall()
        if len(rows) > 1:
            raise PublicationCommitIntegrityError(
                "One stable source revision resolves to multiple current items."
            )
        if not rows:
            return None
        row = rows[0]
        actual = tuple(
            _value(row, key)
            for key in (
                "raw_content_sha256",
                "normalized_content_sha256",
                "content_size_bytes",
                "normalizer_version",
                "content_md",
                "content_blob_hash",
                "body_size",
                "body_sha",
            )
        )
        expected = (
            source.raw_sha256,
            source.normalized_sha256,
            source.size_bytes,
            source.normalizer_version,
            source.content_md,
            source.content_blob_hash,
            source.size_bytes,
            source.content_sha256,
        )
        if actual != expected:
            raise PublicationCommitIntegrityError(
                "A stable source revision has conflicting canonical content."
            )
        return str(_value(row, "item_id")), str(_value(row, "revision_id"))

    @staticmethod
    def _exact_body(
        connection: ManagedConnection,
        source: FrozenPublicationSource,
        target: PublicationCommitTarget,
    ) -> tuple[str, str, str | None] | None:
        if source.origin_type == "upload" and source.original_filename is None:
            raise PublicationCommitIntegrityError(
                "An ordinary upload is missing its frozen filename identity."
            )
        rows = connection.execute(
            """SELECT item.id AS item_id,item.current_revision_id AS revision_id,
                      item.lineage_group_id
               FROM knowledge_item AS item
               JOIN knowledge_revision AS revision
                 ON revision.id=item.current_revision_id AND revision.item_id=item.id
               JOIN knowledge_revision_body AS body
                 ON body.revision_id=revision.id AND body.item_id=item.id
               WHERE item.space_id=? AND item.lifecycle_status='active'
                 AND item.curation_status IN ('published','provisional')
                 AND revision.raw_content_sha256=?
                 AND revision.normalized_content_sha256=?
                 AND revision.content_size_bytes=?
                 AND revision.normalizer_version=?
                 AND body.content_md IS ? AND body.content_blob_hash IS ?
                 AND body.content_size_bytes=? AND body.content_sha256=?
                 AND (?<>'upload' OR EXISTS (
                   SELECT 1 FROM item_source AS provenance
                   JOIN source_artifact AS prior ON prior.id=provenance.artifact_id
                   WHERE provenance.item_id=item.id
                     AND provenance.revision_id=revision.id
                     AND prior.origin_type='upload'
                     AND prior.original_filename=?
                 ))
               ORDER BY item.id""",
            (
                target.space_id,
                source.raw_sha256,
                source.normalized_sha256,
                source.size_bytes,
                source.normalizer_version,
                source.content_md,
                source.content_blob_hash,
                source.size_bytes,
                source.content_sha256,
                source.origin_type,
                source.original_filename,
            ),
        ).fetchall()
        if len(rows) > 1:
            if source.origin_type == "upload":
                raise PublicationCommitConflictError(
                    "More than one exact current upload filename exists in the target space."
                )
            return None
        if not rows:
            return None
        lineage = _value(rows[0], "lineage_group_id")
        return (
            str(_value(rows[0], "item_id")),
            str(_value(rows[0], "revision_id")),
            None if lineage is None else str(lineage),
        )

    @staticmethod
    def _insert_source_link(
        connection: ManagedConnection,
        *,
        source_id: str,
        item_id: str,
        revision_id: str,
        draft: PublicationCommitDraft,
        role: Literal["initial", "evidence"],
    ) -> None:
        connection.execute(
            """INSERT INTO item_source(
                 id,item_id,revision_id,artifact_id,extraction_snapshot_id,
                 role,created_by,created_at
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                source_id,
                item_id,
                revision_id,
                draft.artifact_id,
                draft.extraction_snapshot_id,
                role,
                draft.actor_user_id,
                draft.created_at_ms,
            ),
        )

    @staticmethod
    def _create_item(
        connection: ManagedConnection,
        *,
        draft: PublicationCommitDraft,
        target: PublicationCommitTarget,
        source: FrozenPublicationSource,
        lineage_group_id: str | None,
    ) -> tuple[str, str, str]:
        item_id, revision_id, source_id = _target_ids(
            draft.actor_user_id, draft.operation_id, target.space_id
        )
        collisions = connection.execute(
            """SELECT
                 EXISTS(SELECT 1 FROM knowledge_item WHERE id=?) AS item_exists,
                 EXISTS(SELECT 1 FROM knowledge_revision WHERE id=?) AS revision_exists,
                 EXISTS(SELECT 1 FROM item_source WHERE id=?) AS source_exists""",
            (item_id, revision_id, source_id),
        ).fetchone()
        if collisions is None or any(
            _value(collisions, key) != 0
            for key in ("item_exists", "revision_exists", "source_exists")
        ):
            raise PublicationCommitConflictError(
                "A deterministic publication target identity already exists."
            )
        metadata_value = {
                "publication_preview_id": draft.preview_id,
                "schema_version": 1,
                "source_snapshot_sha256": draft.source_snapshot_sha256,
        }
        if source.authored_snapshot_id is not None:
            metadata_value.update({"body_kind": "authored",
                "authored_snapshot_id": source.authored_snapshot_id,
                "authored_manifest_sha256": source.authored_fence[5]})
        elif source.reused_extraction_snapshot_id is not None:
            metadata_value.update({"body_kind": "extraction",
                "reused_extraction_snapshot_id": source.reused_extraction_snapshot_id})
        metadata = _canonical(metadata_value)
        connection.execute(
            """INSERT INTO knowledge_item(
                 id,space_id,lineage_group_id,lifecycle_status,curation_status,
                 current_revision_id,last_curation_transition_id,
                 latest_deactivation_episode_id,redirect_item_id,created_by,
                 updated_by,created_at,updated_at,row_version
               ) VALUES (?,?,?,'active','provisional',?,NULL,NULL,NULL,?,?,?,?,1)""",
            (
                item_id,
                target.space_id,
                lineage_group_id,
                revision_id,
                draft.actor_user_id,
                draft.actor_user_id,
                draft.created_at_ms,
                draft.created_at_ms,
            ),
        )
        connection.execute(
            """INSERT INTO knowledge_revision(
                 id,item_id,revision_no,parent_revision_id,base_revision_id,
                 title,raw_content_sha256,normalized_content_sha256,
                 content_size_bytes,normalizer_version,metadata_json,
                 change_kind,change_reason,created_by,created_at
               ) VALUES (?,?,1,NULL,NULL,?,?,?,?,?,?,'initial',?,?,?)""",
            (
                revision_id,
                item_id,
                source.title,
                source.raw_sha256,
                source.normalized_sha256,
                source.size_bytes,
                source.normalizer_version,
                metadata,
                "Published from an immutable preview.",
                draft.actor_user_id,
                draft.created_at_ms,
            ),
        )
        connection.execute(
            """INSERT INTO knowledge_revision_body(
                 revision_id,item_id,content_md,content_blob_hash,
                 content_size_bytes,content_sha256,created_at
               ) VALUES (?,?,?,?,?,?,?)""",
            (
                revision_id,
                item_id,
                source.content_md,
                source.content_blob_hash,
                source.size_bytes,
                source.content_sha256,
                draft.created_at_ms,
            ),
        )
        PublicationCommitRepository._insert_source_link(
            connection,
            source_id=source_id,
            item_id=item_id,
            revision_id=revision_id,
            draft=draft,
            role="initial",
        )
        return item_id, revision_id, source_id

    @staticmethod
    def _attach_lineage(
        connection: ManagedConnection,
        *,
        draft: PublicationCommitDraft,
        target: PublicationCommitTarget,
        lineage_group_id: str,
        item_id: str,
        revision_id: str,
        derived_from: tuple[str, str] | None,
    ) -> None:
        row = connection.execute(
            "SELECT lineage_group_id FROM knowledge_item WHERE id=? AND space_id=?",
            (item_id, target.space_id),
        ).fetchone()
        if row is None:
            raise PublicationCommitIntegrityError("The lineage target disappeared.")
        existing = _value(row, "lineage_group_id")
        if existing is None:
            connection.execute(
                """UPDATE knowledge_item SET lineage_group_id=?,updated_by=?,
                     updated_at=?,row_version=row_version+1
                   WHERE id=? AND space_id=? AND lineage_group_id IS NULL""",
                (
                    lineage_group_id,
                    draft.actor_user_id,
                    draft.created_at_ms,
                    item_id,
                    target.space_id,
                ),
            )
        elif existing != lineage_group_id:
            raise PublicationCommitConflictError(
                "An exact target already belongs to another lineage group."
            )
        member = connection.execute(
            """SELECT initial_revision_id,derived_from_item_id,
                      derived_from_revision_id,member_role,created_by,created_at
               FROM lineage_member WHERE lineage_group_id=? AND item_id=?""",
            (lineage_group_id, item_id),
        ).fetchone()
        role = "origin" if derived_from is None else "part_copy"
        expected = (
            revision_id,
            None if derived_from is None else derived_from[0],
            None if derived_from is None else derived_from[1],
            role,
            draft.actor_user_id,
            draft.created_at_ms,
        )
        if member is not None:
            actual = tuple(
                _value(member, key)
                for key in (
                    "initial_revision_id",
                    "derived_from_item_id",
                    "derived_from_revision_id",
                    "member_role",
                    "created_by",
                    "created_at",
                )
            )
            if actual != expected:
                raise PublicationCommitConflictError(
                    "The lineage member is already bound incompatibly."
                )
            return
        connection.execute(
            """INSERT INTO lineage_member(
                 lineage_group_id,item_id,initial_revision_id,
                 derived_from_item_id,derived_from_revision_id,member_role,
                 created_by,created_at
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                lineage_group_id,
                item_id,
                revision_id,
                expected[1],
                expected[2],
                role,
                draft.actor_user_id,
                draft.created_at_ms,
            ),
        )

    def _resolve_target(
        self,
        connection: ManagedConnection,
        *,
        draft: PublicationCommitDraft,
        target: PublicationCommitTarget,
        source: FrozenPublicationSource,
        lineage_group_id: str | None,
    ) -> PublicationTargetResult:
        finding_id: str | None = None
        proposal_id: str | None = None
        same = PublicationCommitRepository._same_source(connection, draft, target)
        if same is not None:
            item_id, revision_id = same
            outcome: PublicationOutcome = "no_op"
            created_revision_id = None
        else:
            same_origin = PublicationCommitRepository._same_origin_revision(
                connection, source, target
            )
            if same_origin is not None:
                item_id, revision_id = same_origin
                outcome = "no_op"
                created_revision_id = None
            else:
                changed = PublicationCommitRepository._stable_origin_changed(
                    connection, draft, source, target
                )
                if changed is not None:
                    if (
                        source.origin_type != "notion"
                        or source.notion_owner_user_id is None
                        or source.notion_page_id is None
                        or source.notion_asset_manifest_sha256 is None
                        or source.notion_source_snapshot_sha256 is None
                    ):
                        raise PublicationUpdateProposalRequiredError(
                            "A non-Notion stable source change has no P4.4 policy."
                        )
                    item_id, revision_id = changed
                    try:
                        proposal = self._curation.put_stable_source_update(
                            connection,
                            StableSourceUpdateDraft(
                                publication_operation_id=publication_operation_id(
                                    draft.actor_user_id, draft.operation_id
                                ),
                                actor_user_id=draft.actor_user_id,
                                target_space_id=target.space_id,
                                target_item_id=item_id,
                                target_revision_id=revision_id,
                                source_artifact_id=draft.artifact_id,
                                extraction_snapshot_id=draft.extraction_snapshot_id,
                                extraction_snapshot_sha256=(
                                    draft.source_snapshot_sha256
                                ),
                                preview_id=draft.preview_id,
                                captured_part_id=source.captured_part_id,
                                publisher_user_id=source.publisher_user_id,
                                notion_owner_user_id=(
                                    source.notion_owner_user_id
                                ),
                                notion_page_id=source.notion_page_id,
                                notion_revision=source.notion_revision,
                                notion_updated_at=source.notion_updated_at,
                                notion_asset_manifest_sha256=(
                                    source.notion_asset_manifest_sha256
                                ),
                                notion_source_snapshot_sha256=(
                                    source.notion_source_snapshot_sha256
                                ),
                                created_at_ms=draft.created_at_ms,
                            ),
                        )
                    except CurationConflictError as exc:
                        raise PublicationCommitConflictError(str(exc)) from exc
                    except CurationIntegrityError as exc:
                        raise PublicationCommitIntegrityError(str(exc)) from exc
                    finding_id = proposal.finding_id
                    proposal_id = proposal.proposal_id
                    outcome = "update_proposed"
                    created_revision_id = None
                else:
                    reused = next((row for row in (source.reuse_fence or ()) if row[0] == target.space_id), None)
                    # A current byte-identical revision may have only historical
                    # source links. The fenced native anchor is authoritative;
                    # a current-revision filename lookup must not duplicate it.
                    exact = ((reused[1], reused[2], None) if reused is not None else
                        PublicationCommitRepository._exact_body(connection, source, target))
                    if exact is None:
                        item_id, revision_id, _ = (
                            PublicationCommitRepository._create_item(
                                connection,
                                draft=draft,
                                target=target,
                                source=source,
                                lineage_group_id=lineage_group_id,
                            )
                        )
                        outcome = "created"
                        created_revision_id = revision_id
                    else:
                        item_id, revision_id, _ = exact
                        source_id = _target_ids(
                            draft.actor_user_id,
                            draft.operation_id,
                            target.space_id,
                        )[2]
                        by_identity = connection.execute(
                            """SELECT id FROM item_source
                               WHERE item_id=? AND revision_id=? AND artifact_id=?
                                 AND extraction_snapshot_id=?""",
                            (
                                item_id,
                                revision_id,
                                draft.artifact_id,
                                draft.extraction_snapshot_id,
                            ),
                        ).fetchone()
                        if by_identity is not None:
                            outcome = "no_op"
                        else:
                            PublicationCommitRepository._insert_source_link(
                                connection,
                                source_id=source_id,
                                item_id=item_id,
                                revision_id=revision_id,
                                draft=draft,
                                role="evidence",
                            )
                            outcome = "provenance_added"
                        created_revision_id = None
        return PublicationTargetResult(
            target.space_id,
            outcome,
            item_id,
            revision_id,
            created_revision_id,
            draft.artifact_id,
            finding_id,
            proposal_id,
        )

    def commit(
        self,
        connection: ManagedConnection,
        draft: PublicationCommitDraft,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> PublicationCommitResult:
        connection = _require_write(connection)
        if failpoint is not None and not callable(failpoint):
            raise TypeError("failpoint must be callable or None.")
        draft = _prepare(draft)
        if draft.frozen_source is None:
            source = self._source(connection, draft)
        else:
            source = draft.frozen_source
            self._revalidate_frozen_source(connection, draft, source)
        self._verify_targets(connection, draft)
        operation_id = publication_operation_id(
            draft.actor_user_id, draft.operation_id
        )
        existing = connection.execute(
            "SELECT 1 FROM publication_operation WHERE id=?", (operation_id,)
        ).fetchone()
        if existing is not None:
            raise PublicationCommitConflictError(
                "The publication operation already exists outside replay handling."
            )
        dedup = connection.execute(
            """SELECT 1 FROM operation_dedup
               WHERE id=? AND actor_user_id=? AND operation_type='publication'
                 AND operation_id=? AND request_sha256=? AND status='in_progress'""",
            (
                draft.dedup_id,
                draft.actor_user_id,
                draft.operation_id,
                draft.request_sha256,
            ),
        ).fetchone()
        if dedup is None:
            raise PublicationCommitConflictError(
                "The publication idempotency reservation is not exact."
            )

        explicit_authority_id: str | None = None
        if draft.target_mode == "explicit":
            if draft.higher_authority is None:
                raise PublicationCommitIntegrityError(
                    "The explicit publication authority is unavailable."
                )
            try:
                explicit_authority = self._authority.put_operation(
                    connection,
                    HigherPublicationOperationAuthorityDraft(
                        publication_operation_id=operation_id,
                        preview=draft.higher_authority,
                        created_at_ms=draft.created_at_ms,
                    ),
                )
            except HigherPublicationAuthorityConflictError as exc:
                raise PublicationCommitConflictError(str(exc)) from exc
            except HigherPublicationAuthorityIntegrityError as exc:
                raise PublicationCommitIntegrityError(str(exc)) from exc
            explicit_authority_id = explicit_authority.authority_id

        lineage_id: str | None = None
        if draft.target_mode == "both":
            lineage_id = publication_lineage_group_id(
                draft.actor_user_id, draft.operation_id
            )
            try:
                connection.execute(
                    """INSERT INTO lineage_group(
                         id,initial_artifact_id,base_artifact_id,
                         base_extraction_snapshot_id,base_content_sha256,
                         sync_policy,created_by,created_at
                       ) VALUES (?,?,?,?,?,'none',?,?)""",
                    (
                        lineage_id,
                        draft.artifact_id,
                        draft.artifact_id,
                        draft.extraction_snapshot_id,
                        source.normalized_sha256,
                        draft.actor_user_id,
                        draft.created_at_ms,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PublicationCommitConflictError(
                    "The deterministic lineage group cannot be created."
                ) from exc
        try:
            schema_row = connection.execute(
                """SELECT sql FROM sqlite_schema
                     WHERE type='table' AND name='publication_operation'"""
            ).fetchone()
            has_explicit_authority_column = (
                schema_row is not None
                and "explicit_authority_id" in str(schema_row[0])
            )
            if has_explicit_authority_column:
                connection.execute(
                    """INSERT INTO publication_operation(
                         id,dedup_id,actor_user_id,operation_id,request_sha256,
                         artifact_id,extraction_snapshot_id,target_mode,
                         lineage_group_id,explicit_authority_id,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        operation_id,
                        draft.dedup_id,
                        draft.actor_user_id,
                        draft.operation_id,
                        draft.request_sha256,
                        draft.artifact_id,
                        draft.extraction_snapshot_id,
                        draft.target_mode,
                        lineage_id,
                        explicit_authority_id,
                        draft.created_at_ms,
                    ),
                )
            elif draft.target_mode != "explicit":
                connection.execute(
                    """INSERT INTO publication_operation(
                         id,dedup_id,actor_user_id,operation_id,request_sha256,
                         artifact_id,extraction_snapshot_id,target_mode,
                         lineage_group_id,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        operation_id,
                        draft.dedup_id,
                        draft.actor_user_id,
                        draft.operation_id,
                        draft.request_sha256,
                        draft.artifact_id,
                        draft.extraction_snapshot_id,
                        draft.target_mode,
                        lineage_id,
                        draft.created_at_ms,
                    ),
                )
            else:  # pragma: no cover - pre-v11 databases cannot authorize this mode
                raise sqlite3.IntegrityError(
                    "explicit publication requires schema v11"
                )
        except sqlite3.IntegrityError as exc:
            raise PublicationCommitConflictError(
                "The publication operation violates canonical constraints."
            ) from exc
        if failpoint is not None:
            failpoint("publication.after_operation")

        results: list[PublicationTargetResult] = []
        personal_anchor: tuple[str, str] | None = None
        target_failpoints = (
            (
                "publication.after_first_target_item",
                "publication.after_first_target_revision",
            ),
            (
                "publication.after_second_target_item",
                "publication.after_second_target_revision",
            ),
        )
        try:
            for target_index, target in enumerate(draft.targets):
                resolved = self._resolve_target(
                    connection,
                    draft=draft,
                    target=target,
                    source=source,
                    lineage_group_id=lineage_id,
                )
                results.append(resolved)
                if failpoint is not None:
                    if target_index >= len(target_failpoints):
                        raise PublicationCommitIntegrityError(
                            "The publication target failpoint binding is invalid."
                        )
                    item_point, revision_point = target_failpoints[target_index]
                    failpoint(item_point)
                    failpoint(revision_point)
            attach_new_lineage = lineage_id is not None and any(
                result.outcome == "created" for result in results
            )
            for target_index, (target, result) in enumerate(
                zip(draft.targets, results, strict=True)
            ):
                if attach_new_lineage:
                    derived = None if target.kind == "personal" else personal_anchor
                    if target.kind == "part" and derived is None:
                        raise PublicationCommitIntegrityError(
                            "The personal lineage origin was not resolved first."
                        )
                    self._attach_lineage(
                        connection,
                        draft=draft,
                        target=target,
                        lineage_group_id=lineage_id,
                        item_id=result.item_id,
                        revision_id=result.resolved_revision_id,
                        derived_from=derived,
                    )
                    if target.kind == "personal":
                        personal_anchor = (
                            result.item_id,
                            result.resolved_revision_id,
                        )
                response_json = _canonical(result.to_mapping())
                connection.execute(
                    """INSERT INTO publication_target(
                         publication_operation_id,target_space_id,outcome,
                         item_id,resolved_revision_id,created_revision_id,
                         curation_finding_id,response_json,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        operation_id,
                        target.space_id,
                        result.outcome,
                        result.item_id,
                        result.resolved_revision_id,
                        result.created_revision_id,
                        result.curation_finding_id,
                        response_json,
                        draft.created_at_ms,
                    ),
                )
                if failpoint is not None and target_index == 0:
                    failpoint("publication.after_first_target_result")
                if result.outcome == "created":
                    cursor = connection.execute(
                        """UPDATE knowledge_space
                           SET generation=generation+1,updated_at=?,
                               row_version=row_version+1
                           WHERE id=? AND status='active' AND write_enabled=1""",
                        (draft.created_at_ms, target.space_id),
                    )
                    if cursor.rowcount != 1:
                        raise PublicationCommitConflictError(
                            "The target space changed during publication."
                        )
            if source.authored_snapshot_id is not None:
                if any(result.outcome == "update_proposed" for result in results):
                    raise PublicationCommitConflictError("Authored update proposals are not yet integrated.")
                self._verify_selected_bodies(connection, operation_id, source.content_sha256, source.size_bytes)
                connection.execute("INSERT INTO publication_authored_selection VALUES(?,?)",
                                   (operation_id, source.authored_snapshot_id))
                if failpoint is not None:
                    failpoint("publication.after_authored_selection")
            elif source.reused_extraction_snapshot_id is not None:
                if source.authored_preparation is None or not source.reuse_fence:
                    raise PublicationCommitIntegrityError("The extraction reuse preparation is missing.")
                self._verify_selected_bodies(connection, operation_id, source.content_sha256, source.size_bytes)
                connection.execute("""INSERT INTO publication_extraction_reuse(
                    publication_operation_id,requested_authored_snapshot_id,extraction_snapshot_id,
                    source_publication_operation_id) VALUES(?,?,?,?)""",
                    (operation_id, source.authored_preparation.authored_snapshot_id,
                     source.reused_extraction_snapshot_id, source.reuse_fence[0][6]))
                if failpoint is not None:
                    failpoint("publication.after_extraction_reuse")
            elif connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='publication_authoring_run'").fetchone() is not None:
                pending = connection.execute("SELECT id FROM publication_authoring_run WHERE preview_id=? AND actor_user_id=? AND status='pending'",
                    (draft.preview_id, draft.actor_user_id)).fetchone()
                if pending is not None:
                    WikiAuthoringRepository().extraction_only(connection, run_id=pending[0],
                        actor_user_id=draft.actor_user_id, now_ms=draft.created_at_ms,
                        failure_code="extraction_selected")
            cursor = connection.execute(
                """UPDATE publication_preview
                   SET status='consumed',updated_at=?,row_version=row_version+1
                   WHERE id=? AND actor_user_id=? AND status='ready'
                     AND row_version=?""",
                (
                    draft.created_at_ms,
                    draft.preview_id,
                    draft.actor_user_id,
                    draft.expected_preview_row_version,
                ),
            )
            if cursor.rowcount != 1:
                raise PublicationCommitConflictError(
                    "The ready preview changed before it could be consumed."
                )
        except sqlite3.IntegrityError as exc:
            raise PublicationCommitConflictError(
                "The publication target violates canonical constraints."
            ) from exc
        return PublicationCommitResult(
            operation_id,
            lineage_id,
            draft.preview_id,
            draft.artifact_id,
            draft.target_mode,
            tuple(results),
            source.authored_snapshot_id,
            source.reused_extraction_snapshot_id,
        )

    @staticmethod
    def _verify_selected_bodies(connection, operation_id, content_sha256, size_bytes, *, retained=True):
        rows = connection.execute("""SELECT r.raw_content_sha256,r.content_size_bytes,
            b.content_sha256,b.content_size_bytes FROM publication_target t
            JOIN knowledge_revision r ON r.id=t.resolved_revision_id AND r.item_id=t.item_id
            LEFT JOIN knowledge_revision_body b ON b.revision_id=r.id AND b.item_id=r.item_id
            WHERE t.publication_operation_id=?""", (operation_id,)).fetchall()
        expected = (content_sha256, size_bytes, content_sha256, size_bytes)
        if not rows or any(tuple(row)[:(4 if retained else 2)] != expected[:(4 if retained else 2)] for row in rows):
            raise PublicationCommitIntegrityError("Selected publication differs from the published revision body.")

    def require_exact_replay(
        self,
        connection: ManagedConnection,
        *,
        actor_user_id: str,
        operation_id: str,
        dedup_id: str,
        request_sha256: str,
        preview_id: str,
        authored_snapshot_id: str | None = None,
        reused_extraction_snapshot_id: str | None = None,
        requested_authored_snapshot_id: str | None = None,
    ) -> PublicationCommitResult:
        if not isinstance(connection, ManagedConnection):
            raise TypeError("PublicationCommitRepository requires a ManagedConnection.")
        actor = _user(actor_user_id)
        operation = _uuid(operation_id, field="operation_id")
        dedup = _uuid(dedup_id, field="dedup_id")
        request_sha = _sha(request_sha256, field="request_sha256")
        preview = _uuid(preview_id, field="preview_id")
        canonical_operation_id = publication_operation_id(actor, operation)
        if authored_snapshot_id is not None and reused_extraction_snapshot_id is not None:
            raise PublicationCommitIntegrityError("The committed body selection is ambiguous.")
        if reused_extraction_snapshot_id is not None:
            selected = connection.execute("""SELECT px.extracted_sha256,px.extracted_size_bytes
                FROM publication_extraction_reuse receipt
                JOIN publication_operation p ON p.id=receipt.publication_operation_id
                JOIN source_artifact a ON a.id=p.artifact_id AND a.origin_type='upload'
                JOIN extraction_snapshot x ON x.id=p.extraction_snapshot_id AND x.artifact_id=a.id
                JOIN publication_operation op ON op.id=receipt.source_publication_operation_id
                  AND op.id<>p.id AND op.created_at<=p.created_at
                JOIN source_artifact prior ON prior.id=op.artifact_id AND prior.origin_type='upload'
                  AND prior.raw_sha256=a.raw_sha256 AND prior.raw_size_bytes=a.raw_size_bytes
                  AND prior.media_type=a.media_type AND prior.original_filename=a.original_filename
                JOIN extraction_snapshot px ON px.id=receipt.extraction_snapshot_id
                  AND px.id=op.extraction_snapshot_id AND px.artifact_id=prior.id
                  AND px.extracted_sha256=x.extracted_sha256 AND px.extracted_size_bytes=x.extracted_size_bytes
                  AND px.normalized_content_sha256=x.normalized_content_sha256 AND px.normalizer_version=x.normalizer_version
                  AND px.extractor_name=x.extractor_name AND px.extractor_version=x.extractor_version
                  AND px.warnings_json=x.warnings_json
                JOIN authored_wiki_snapshot s ON s.id=receipt.requested_authored_snapshot_id
                JOIN publication_authoring_run r ON r.id=s.run_id
                WHERE receipt.publication_operation_id=? AND receipt.extraction_snapshot_id=?
                  AND receipt.requested_authored_snapshot_id=?
                  AND r.preview_id=? AND r.source_sha256=x.extracted_sha256
                  AND r.actor_user_id=p.actor_user_id AND r.artifact_id=p.artifact_id
                  AND r.extraction_snapshot_id=p.extraction_snapshot_id AND r.status='ready'
                  AND NOT EXISTS(SELECT 1 FROM publication_authored_selection selected
                    WHERE selected.publication_operation_id IN (p.id,op.id))
                  AND EXISTS(SELECT 1 FROM publication_target ot
                    JOIN publication_target nt ON nt.publication_operation_id=p.id
                      AND nt.target_space_id=ot.target_space_id AND nt.item_id=ot.item_id
                    JOIN knowledge_revision v ON v.id=ot.resolved_revision_id AND v.item_id=ot.item_id
                    WHERE ot.publication_operation_id=op.id AND ot.outcome<>'update_proposed'
                      AND v.raw_content_sha256=px.extracted_sha256 AND v.content_size_bytes=px.extracted_size_bytes
                      AND v.normalized_content_sha256=px.normalized_content_sha256 AND v.normalizer_version=px.normalizer_version)""",
                (canonical_operation_id, reused_extraction_snapshot_id, requested_authored_snapshot_id, preview)).fetchone()
            if selected is None:
                raise PublicationCommitIntegrityError("The committed extraction reuse is unavailable or inconsistent.")
            self._verify_selected_bodies(connection, canonical_operation_id, *selected, retained=False)
        if authored_snapshot_id is not None:
            selected = connection.execute("""SELECT s.content_sha256,s.content_size_bytes
                FROM publication_authored_selection a JOIN authored_wiki_snapshot s ON s.id=a.authored_snapshot_id
                WHERE a.publication_operation_id=? AND a.authored_snapshot_id=?""",
                (canonical_operation_id, authored_snapshot_id)).fetchone()
            if selected is None:
                raise PublicationCommitIntegrityError("The committed authored selection is unavailable.")
            self._verify_selected_bodies(connection, canonical_operation_id, *selected, retained=False)
        row = connection.execute(
            """SELECT p.artifact_id,p.extraction_snapshot_id,p.target_mode,
                      p.lineage_group_id,p.explicit_authority_id,
                      d.status,d.result_json
               FROM publication_operation AS p
               JOIN operation_dedup AS d ON d.id=p.dedup_id
               WHERE p.id=? AND p.dedup_id=? AND p.actor_user_id=?
                 AND p.operation_id=? AND p.request_sha256=?
                 AND d.status='committed'""",
            (canonical_operation_id, dedup, actor, operation, request_sha),
        ).fetchone()
        if row is None:
            raise PublicationCommitIntegrityError(
                "The committed publication operation is missing or inconsistent."
            )
        preview_row = connection.execute(
            """SELECT artifact_id,extraction_snapshot_id,status
               FROM publication_preview
               WHERE id=? AND actor_user_id=?""",
            (preview, actor),
        ).fetchone()
        if (
            preview_row is None
            or _value(preview_row, "status") != "consumed"
            or _value(preview_row, "artifact_id") != _value(row, "artifact_id")
            or _value(preview_row, "extraction_snapshot_id")
            != _value(row, "extraction_snapshot_id")
        ):
            raise PublicationCommitIntegrityError(
                "The publication preview consumption binding is invalid."
            )
        target_rows = connection.execute(
            """SELECT target.target_space_id,target.outcome,target.item_id,
                      target.resolved_revision_id,target.created_revision_id,
                      target.curation_finding_id,target.response_json,
                      (SELECT proposal.id FROM change_proposal AS proposal
                       WHERE proposal.finding_id=target.curation_finding_id)
                        AS change_proposal_id,
                      space.space_kind
               FROM publication_target AS target
               JOIN knowledge_space AS space ON space.id=target.target_space_id
               WHERE target.publication_operation_id=?
               ORDER BY CASE space.space_kind WHEN 'personal' THEN 0 ELSE 1 END,
                        target.target_space_id""",
            (canonical_operation_id,),
        ).fetchall()
        target_mode = _value(row, "target_mode")
        expected_count = 2 if target_mode == "both" else 1
        if len(target_rows) != expected_count:
            raise PublicationCommitIntegrityError(
                "The committed publication target set is incomplete."
            )
        results: list[PublicationTargetResult] = []
        for target in target_rows:
            outcome = _value(target, "outcome")
            if outcome not in {
                "created",
                "provenance_added",
                "update_proposed",
                "no_op",
            }:
                raise PublicationCommitIntegrityError(
                    "The committed publication outcome is invalid."
                )
            result = PublicationTargetResult(
                str(_value(target, "target_space_id")),
                outcome,  # type: ignore[arg-type]
                str(_value(target, "item_id")),
                str(_value(target, "resolved_revision_id")),
                (
                    None
                    if _value(target, "created_revision_id") is None
                    else str(_value(target, "created_revision_id"))
                ),
                str(_value(row, "artifact_id")),
                (
                    None
                    if _value(target, "curation_finding_id") is None
                    else str(_value(target, "curation_finding_id"))
                ),
                (
                    None
                    if _value(target, "change_proposal_id") is None
                    else str(_value(target, "change_proposal_id"))
                ),
            )
            if outcome == "update_proposed":
                if (
                    result.curation_finding_id is None
                    or result.change_proposal_id is None
                ):
                    raise PublicationCommitIntegrityError(
                        "The update proposal finding is missing."
                    )
                proposal = connection.execute(
                    """SELECT 1
                       FROM curation_finding AS finding
                       JOIN change_proposal AS proposal
                         ON proposal.finding_id=finding.id
                       WHERE finding.id=? AND proposal.id=? AND finding.space_id=?
                         AND finding.finding_kind='update'
                         AND finding.source_item_id=?
                         AND finding.source_revision_id=?
                         AND finding.target_item_id IS NULL
                         AND finding.target_revision_id IS NULL
                         AND proposal.proposal_kind='stable_source_update'
                         AND proposal.base_item_id=?
                         AND proposal.base_revision_id=?
                         AND proposal.target_item_id=?
                         AND proposal.target_revision_id=?
                         AND proposal.source_artifact_id=?
                         AND proposal.extraction_snapshot_id=?""",
                    (
                        result.curation_finding_id,
                        result.change_proposal_id,
                        result.target_space_id,
                        result.item_id,
                        result.resolved_revision_id,
                        result.item_id,
                        result.resolved_revision_id,
                        result.item_id,
                        result.resolved_revision_id,
                        _value(row, "artifact_id"),
                        _value(row, "extraction_snapshot_id"),
                    ),
                ).fetchone()
                if proposal is None:
                    raise PublicationCommitIntegrityError(
                        "The update proposal binding is incomplete."
                    )
            elif (
                result.curation_finding_id is not None
                or result.change_proposal_id is not None
            ):
                raise PublicationCommitIntegrityError(
                    "A non-proposal target has a curation finding."
                )
            try:
                stored = json.loads(str(_value(target, "response_json")))
            except json.JSONDecodeError as exc:
                raise PublicationCommitIntegrityError(
                    "A publication target response is invalid."
                ) from exc
            if stored != result.to_mapping() or _canonical(stored) != _value(
                target, "response_json"
            ):
                raise PublicationCommitIntegrityError(
                    "A publication target response is not exact."
                )
            results.append(result)
        lineage = _value(row, "lineage_group_id")
        if (target_mode == "both") != (lineage is not None):
            raise PublicationCommitIntegrityError(
                "The publication lineage binding is invalid."
            )
        explicit_authority_id = _value(row, "explicit_authority_id")
        if target_mode == "explicit":
            authority = self._authority.get_operation(
                connection, canonical_operation_id
            )
            if (
                authority is None
                or authority.authority_id != explicit_authority_id
                or authority.preview_id != preview
                or len(results) != 1
                or results[0].target_space_id != authority.target_space_id
            ):
                raise PublicationCommitIntegrityError(
                    "The explicit publication replay authority is invalid."
                )
        elif explicit_authority_id is not None:
            raise PublicationCommitIntegrityError(
                "A legacy publication unexpectedly carries explicit authority."
            )
        return PublicationCommitResult(
            canonical_operation_id,
            None if lineage is None else str(lineage),
            preview,
            str(_value(row, "artifact_id")),
            target_mode,  # type: ignore[arg-type]
            tuple(results),
            authored_snapshot_id,
            reused_extraction_snapshot_id,
        )


__all__ = [
    "FrozenPublicationSource",
    "PublicationCommitConflictError",
    "PublicationCommitDraft",
    "PublicationCommitIntegrityError",
    "PublicationCommitRepository",
    "PublicationCommitRepositoryError",
    "PublicationCommitResult",
    "PublicationCommitTarget",
    "PublicationOutcome",
    "PublicationTargetKind",
    "PublicationTargetMode",
    "PublicationTargetResult",
    "PublicationUpdateProposalRequiredError",
    "publication_lineage_group_id",
    "publication_operation_id",
]

"""Canonical deterministic finding and proposal persistence.

This repository owns the P4.4 stable-Notion-source update boundary.  It accepts
only body-free provenance plus a source artifact/extraction pair whose proposed
body already exists in the canonical database.  It performs no authorization,
filesystem, source-system, model, transaction, audit, outbox, or commit work.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
from typing import Mapping
from uuid import UUID, uuid5

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


STABLE_SOURCE_UPDATE_RULE_VERSION = "p4.4-stable-source-update-v1"

_FINDING_NAMESPACE = UUID("93d16aba-7893-4ba0-a2d4-ad017823491f")
_PROPOSAL_NAMESPACE = UUID("f1b2da9d-b512-4ac1-9d74-62ca11caacfa")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_MAX_INTEGER = 9_223_372_036_854_775_807


class CurationRepositoryError(KnowledgeCoreDatabaseError):
    code = "CURATION_REPOSITORY_ERROR"


class CurationConflictError(CurationRepositoryError):
    code = "CURATION_CONFLICT"


class CurationIntegrityError(CurationRepositoryError):
    code = "CURATION_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class StableSourceUpdateDraft:
    publication_operation_id: str
    actor_user_id: str
    target_space_id: str
    target_item_id: str
    target_revision_id: str
    source_artifact_id: str
    extraction_snapshot_id: str
    extraction_snapshot_sha256: str
    preview_id: str
    captured_part_id: str | None
    publisher_user_id: str
    notion_owner_user_id: str
    notion_page_id: str
    notion_revision: int | None
    notion_updated_at: int | None
    notion_asset_manifest_sha256: str
    notion_source_snapshot_sha256: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class StableSourceUpdateResult:
    finding_id: str
    proposal_id: str
    preview_sha256: str
    target_item_id: str
    target_revision_id: str


@dataclass(frozen=True, slots=True)
class _SourceProposalBody:
    content_md: str | None
    content_blob_hash: str | None
    content_size_bytes: int
    content_sha256: str
    normalized_content_sha256: str


def _uuid(value: object, *, field: str) -> str:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{field} must be a canonical lowercase UUID.") from None
    canonical = str(parsed)
    if not isinstance(value, str) or value != canonical:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return canonical


def _optional_uuid(value: object, *, field: str) -> str | None:
    return None if value is None else _uuid(value, field=field)


def _user(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid.")
    return value


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 value.")
    return value


def _integer(value: object, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_INTEGER
    ):
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


def _optional_integer(value: object, *, field: str) -> int | None:
    return None if value is None else _integer(value, field=field)


def _text(value: object, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or "\x00" in value
        or any(ord(char) < 0x20 for char in value)
    ):
        raise ValueError(f"{field} is invalid.")
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
        raise ValueError("Curation evidence must be finite JSON.") from exc
    if type(json.loads(encoded)) is not dict:
        raise ValueError("Curation evidence must be a JSON object.")
    return encoded


def _sha256_canonical(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, TypeError, IndexError) as exc:
        raise CurationIntegrityError(
            "A canonical curation row cannot be mapped."
        ) from exc


def _require_write(connection: object) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("CurationRepository requires a ManagedConnection.")
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Curation writes require an existing managed transaction."
        )
    return connection


def stable_source_update_ids(
    publication_operation_id: str, target_space_id: str
) -> tuple[str, str]:
    operation = _uuid(publication_operation_id, field="publication_operation_id")
    space = _uuid(target_space_id, field="target_space_id")
    identity = f"{operation}:{space}"
    return (
        str(uuid5(_FINDING_NAMESPACE, identity)),
        str(uuid5(_PROPOSAL_NAMESPACE, identity)),
    )


def _prepare(value: StableSourceUpdateDraft) -> StableSourceUpdateDraft:
    if not isinstance(value, StableSourceUpdateDraft):
        raise TypeError("A StableSourceUpdateDraft is required.")
    operation = _uuid(
        value.publication_operation_id, field="publication_operation_id"
    )
    actor = _user(value.actor_user_id, field="actor_user_id")
    space = _uuid(value.target_space_id, field="target_space_id")
    item = _uuid(value.target_item_id, field="target_item_id")
    revision = _uuid(value.target_revision_id, field="target_revision_id")
    artifact = _uuid(value.source_artifact_id, field="source_artifact_id")
    extraction = _uuid(
        value.extraction_snapshot_id, field="extraction_snapshot_id"
    )
    extraction_sha = _sha(
        value.extraction_snapshot_sha256,
        field="extraction_snapshot_sha256",
    )
    preview = _uuid(value.preview_id, field="preview_id")
    captured_part = _optional_uuid(value.captured_part_id, field="captured_part_id")
    publisher = _user(value.publisher_user_id, field="publisher_user_id")
    owner = _user(value.notion_owner_user_id, field="notion_owner_user_id")
    page = _text(value.notion_page_id, field="notion_page_id", maximum=1000)
    notion_revision = _optional_integer(
        value.notion_revision, field="notion_revision"
    )
    notion_updated_at = _optional_integer(
        value.notion_updated_at, field="notion_updated_at"
    )
    asset_sha = _sha(
        value.notion_asset_manifest_sha256,
        field="notion_asset_manifest_sha256",
    )
    notion_snapshot_sha = _sha(
        value.notion_source_snapshot_sha256,
        field="notion_source_snapshot_sha256",
    )
    created_at = _integer(value.created_at_ms, field="created_at_ms")
    if actor != publisher:
        raise ValueError("The Notion publisher must be the publication actor.")
    return StableSourceUpdateDraft(
        operation,
        actor,
        space,
        item,
        revision,
        artifact,
        extraction,
        extraction_sha,
        preview,
        captured_part,
        publisher,
        owner,
        page,
        notion_revision,
        notion_updated_at,
        asset_sha,
        notion_snapshot_sha,
        created_at,
    )


def _proposal_preview_payload(
    draft: StableSourceUpdateDraft,
    *,
    content_sha256: str,
) -> dict[str, object]:
    return {
        "base_item_id": draft.target_item_id,
        "base_revision_id": draft.target_revision_id,
        "extraction_snapshot_id": draft.extraction_snapshot_id,
        "extraction_snapshot_sha256": draft.extraction_snapshot_sha256,
        "finding_input_row_version": 1,
        "proposal_kind": "stable_source_update",
        "proposal_version": 1,
        "proposed_content_sha256": content_sha256,
        "rule_version": STABLE_SOURCE_UPDATE_RULE_VERSION,
        "source_artifact_id": draft.source_artifact_id,
        "source_item_id": None,
        "source_revision_id": None,
        "target_item_id": draft.target_item_id,
        "target_revision_id": draft.target_revision_id,
    }


class CurationRepository:
    """Insert one fixed stable-source update finding/proposal pair."""

    __slots__ = ()

    @staticmethod
    def _verify_operation_and_preview(
        connection: ManagedConnection, draft: StableSourceUpdateDraft
    ) -> None:
        row = connection.execute(
            """SELECT operation.actor_user_id,operation.artifact_id,
                      operation.extraction_snapshot_id,operation.created_at,
                      preview.actor_user_id AS preview_actor,
                      preview.artifact_id AS preview_artifact,
                      preview.extraction_snapshot_id AS preview_extraction,
                      preview.source_snapshot_sha256,preview.captured_part_id,
                      preview.status AS preview_status
               FROM publication_operation AS operation
               JOIN publication_preview AS preview ON preview.id=?
               WHERE operation.id=?""",
            (draft.preview_id, draft.publication_operation_id),
        ).fetchone()
        if row is None or tuple(
            _value(row, key)
            for key in (
                "actor_user_id",
                "artifact_id",
                "extraction_snapshot_id",
                "created_at",
                "preview_actor",
                "preview_artifact",
                "preview_extraction",
                "source_snapshot_sha256",
                "captured_part_id",
                "preview_status",
            )
        ) != (
            draft.actor_user_id,
            draft.source_artifact_id,
            draft.extraction_snapshot_id,
            draft.created_at_ms,
            draft.actor_user_id,
            draft.source_artifact_id,
            draft.extraction_snapshot_id,
            draft.extraction_snapshot_sha256,
            draft.captured_part_id,
            "ready",
        ):
            raise CurationIntegrityError(
                "The stable-source proposal operation binding is invalid."
            )

    @staticmethod
    def _verify_target(
        connection: ManagedConnection, draft: StableSourceUpdateDraft
    ) -> None:
        row = connection.execute(
            """SELECT current_revision_id,lifecycle_status
               FROM knowledge_item WHERE id=? AND space_id=?""",
            (draft.target_item_id, draft.target_space_id),
        ).fetchone()
        if (
            row is None
            or _value(row, "current_revision_id") != draft.target_revision_id
            or _value(row, "lifecycle_status") != "active"
        ):
            raise CurationConflictError(
                "The stable-source proposal target is no longer current."
            )

    @staticmethod
    def _source_body(
        connection: ManagedConnection, draft: StableSourceUpdateDraft
    ) -> _SourceProposalBody:
        row = connection.execute(
            """SELECT artifact.origin_type,artifact.origin_key,
                      artifact.origin_revision_key,artifact.publisher_user_id,
                      artifact.metadata_json,artifact.body_retention_status,
                      snapshot.status,snapshot.extracted_sha256,
                      snapshot.normalized_content_sha256,
                      snapshot.extracted_size_bytes,snapshot.normalizer_version,
                      snapshot.body_retention_status AS snapshot_retention,
                      body.content_md,body.content_blob_hash,
                      body.content_size_bytes,body.content_sha256
               FROM source_artifact AS artifact
               JOIN extraction_snapshot AS snapshot
                 ON snapshot.artifact_id=artifact.id AND snapshot.id=?
               JOIN extraction_snapshot_body AS body
                 ON body.snapshot_id=snapshot.id AND body.artifact_id=artifact.id
               WHERE artifact.id=?""",
            (draft.extraction_snapshot_id, draft.source_artifact_id),
        ).fetchone()
        if row is None:
            raise CurationIntegrityError(
                "The stable-source proposal body is unavailable."
            )
        try:
            metadata = json.loads(str(_value(row, "metadata_json")))
        except (json.JSONDecodeError, RecursionError) as exc:
            raise CurationIntegrityError(
                "The Notion source provenance is invalid."
            ) from exc
        provenance = (
            metadata.get("notion_provenance")
            if isinstance(metadata, dict)
            else None
        )
        expected_provenance = {
            "asset_manifest_sha256": draft.notion_asset_manifest_sha256,
            "owner_user_id": draft.notion_owner_user_id,
            "page_id": draft.notion_page_id,
            "revision": draft.notion_revision,
            "source_snapshot_sha256": draft.notion_source_snapshot_sha256,
            "updated_at": draft.notion_updated_at,
        }
        content_md = _value(row, "content_md")
        content_blob = _value(row, "content_blob_hash")
        content_size = _value(row, "content_size_bytes")
        content_sha = _value(row, "content_sha256")
        normalized_sha = _value(row, "normalized_content_sha256")
        if (
            _value(row, "origin_type") != "notion"
            or _value(row, "origin_key")
            != f"notion:{draft.notion_owner_user_id}:{draft.notion_page_id}"
            or _value(row, "origin_revision_key")
            != draft.notion_source_snapshot_sha256
            or _value(row, "publisher_user_id") != draft.publisher_user_id
            or provenance != expected_provenance
            or _canonical(metadata) != _value(row, "metadata_json")
            or _value(row, "body_retention_status") != "present"
            or _value(row, "snapshot_retention") != "present"
            or _value(row, "status") != "ready"
            or _value(row, "extracted_sha256")
            != draft.extraction_snapshot_sha256
            or content_sha != draft.extraction_snapshot_sha256
            or _value(row, "extracted_size_bytes") != content_size
            or _value(row, "normalizer_version") != "markdown_lf_v1"
            or type(content_size) is not int
            or content_size < 0
            or not isinstance(content_sha, str)
            or _SHA_RE.fullmatch(content_sha) is None
            or not isinstance(normalized_sha, str)
            or _SHA_RE.fullmatch(normalized_sha) is None
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
            raise CurationIntegrityError(
                "The stable-source proposal evidence is inconsistent."
            )
        if content_md is not None and (
            hashlib.sha256(content_md.encode("utf-8")).hexdigest() != content_sha
            or len(content_md.encode("utf-8")) != content_size
        ):
            raise CurationIntegrityError(
                "The stable-source proposed Markdown body is inconsistent."
            )
        return _SourceProposalBody(
            content_md if isinstance(content_md, str) else None,
            content_blob if isinstance(content_blob, str) else None,
            content_size,
            content_sha,
            normalized_sha,
        )

    def put_stable_source_update(
        self,
        connection: ManagedConnection,
        draft: StableSourceUpdateDraft,
    ) -> StableSourceUpdateResult:
        connection = _require_write(connection)
        draft = _prepare(draft)
        self._verify_operation_and_preview(connection, draft)
        self._verify_target(connection, draft)
        body = self._source_body(connection, draft)
        finding_id, proposal_id = stable_source_update_ids(
            draft.publication_operation_id, draft.target_space_id
        )
        collisions = connection.execute(
            """SELECT
                 EXISTS(SELECT 1 FROM curation_finding WHERE id=?) AS finding_exists,
                 EXISTS(SELECT 1 FROM change_proposal WHERE id=?) AS proposal_exists""",
            (finding_id, proposal_id),
        ).fetchone()
        if collisions is None or any(
            _value(collisions, key) != 0
            for key in ("finding_exists", "proposal_exists")
        ):
            raise CurationConflictError(
                "The deterministic stable-source proposal identity already exists."
            )
        preview_payload = _proposal_preview_payload(
            draft, content_sha256=body.content_sha256
        )
        preview_sha256 = _sha256_canonical(preview_payload)
        evidence_json = _canonical(
            {
                "captured_part_id": draft.captured_part_id,
                "extraction_snapshot_id": draft.extraction_snapshot_id,
                "extraction_snapshot_sha256": draft.extraction_snapshot_sha256,
                "notion_asset_manifest_sha256": (
                    draft.notion_asset_manifest_sha256
                ),
                "notion_owner_user_id": draft.notion_owner_user_id,
                "notion_page_id": draft.notion_page_id,
                "notion_revision": draft.notion_revision,
                "notion_source_snapshot_sha256": (
                    draft.notion_source_snapshot_sha256
                ),
                "notion_updated_at": draft.notion_updated_at,
                "preview_id": draft.preview_id,
                "proposed_content_sha256": body.content_sha256,
                "proposed_normalized_content_sha256": (
                    body.normalized_content_sha256
                ),
                "publication_operation_id": draft.publication_operation_id,
                "publisher_user_id": draft.publisher_user_id,
                "schema_version": 1,
                "source_artifact_id": draft.source_artifact_id,
                "target_item_id": draft.target_item_id,
                "target_revision_id": draft.target_revision_id,
            }
        )
        action_json = _canonical(
            {
                "preview_sha256": preview_sha256,
                "proposal_id": proposal_id,
                "proposal_kind": "stable_source_update",
                "proposal_version": 1,
                "schema_version": 1,
            }
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
                     created_at,updated_at,row_version
                   ) VALUES (
                     ?,?,'update','T2','info','pending','human','space_editor',
                     'agent_hidden','update',?,?,NULL,NULL,
                     'stable-source-publication','1',?,NULL,NULL,1.0,?,?,
                     NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
                     'user',?,?,?,1
                   )""",
                (
                    finding_id,
                    draft.target_space_id,
                    draft.target_item_id,
                    draft.target_revision_id,
                    STABLE_SOURCE_UPDATE_RULE_VERSION,
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
                     proposal_series_id,proposal_version,
                     supersedes_proposal_id,lineage_group_id,base_item_id,
                     base_revision_id,source_item_id,source_revision_id,
                     target_item_id,target_revision_id,source_artifact_id,
                     extraction_snapshot_id,proposed_content_md,
                     proposed_content_blob_hash,proposed_content_size_bytes,
                     proposed_content_sha256,preview_sha256,state,
                     applied_operation_id,applied_dedup_id,
                     last_application_error_json,created_by,updated_by,
                     created_at,updated_at,row_version
                   ) VALUES (
                     ?,?,1,'stable_source_update',?,1,NULL,NULL,?,?,NULL,NULL,
                     ?,?,?,?,?,?,?,?,?,'pending_review',NULL,NULL,NULL,?,?,?,?,1
                   )""",
                (
                    proposal_id,
                    finding_id,
                    proposal_id,
                    draft.target_item_id,
                    draft.target_revision_id,
                    draft.target_item_id,
                    draft.target_revision_id,
                    draft.source_artifact_id,
                    draft.extraction_snapshot_id,
                    body.content_md,
                    body.content_blob_hash,
                    body.content_size_bytes,
                    body.content_sha256,
                    preview_sha256,
                    draft.actor_user_id,
                    draft.actor_user_id,
                    draft.created_at_ms,
                    draft.created_at_ms,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise CurationConflictError(
                "The stable-source proposal violates canonical constraints."
            ) from exc
        return StableSourceUpdateResult(
            finding_id,
            proposal_id,
            preview_sha256,
            draft.target_item_id,
            draft.target_revision_id,
        )


__all__ = [
    "CurationConflictError",
    "CurationIntegrityError",
    "CurationRepository",
    "CurationRepositoryError",
    "STABLE_SOURCE_UPDATE_RULE_VERSION",
    "StableSourceUpdateDraft",
    "StableSourceUpdateResult",
    "stable_source_update_ids",
]

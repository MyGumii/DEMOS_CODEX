"""Read-only provenance for original files attached to canonical Wiki items.

The caller owns the connection, transaction, and authorization decision.  This
repository deliberately accepts a resolved item authorization so a client can
never substitute a physical source space or revision.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from ..db import ManagedConnection
from ..errors import KnowledgeCoreError
from .management_reads import ManagementItemAuthorization


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class SourceArtifactRepositoryIntegrityError(KnowledgeCoreError):
    code = "INTERNAL_ERROR"
    http_status = 500
    retryable = False


@dataclass(frozen=True, slots=True)
class ItemSourceArtifactRecord:
    artifact_id: str
    item_id: str
    source_revision_id: str
    origin_type: str
    original_filename: str
    media_type: str
    size_bytes: int
    content_sha256: str
    created_at_ms: int
    protected_source_kind: str | None = None


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise SourceArtifactRepositoryIntegrityError(f"Stored {field} is invalid.")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise SourceArtifactRepositoryIntegrityError(f"Stored {field} is invalid.")
    return value


def _text(value: object, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise SourceArtifactRepositoryIntegrityError(f"Stored {field} is invalid.")
    return value.strip()


def _filename(original: object, logical: object, artifact_id: str) -> str:
    candidate = original if isinstance(original, str) and original.strip() else logical
    if not isinstance(candidate, str):
        candidate = f"source-{artifact_id}"
    # Origin metadata may contain a browser path.  A download name is always a
    # single component and never carries control characters into HTTP headers.
    candidate = re.split(r"[\\/]", candidate.strip())[-1]
    candidate = "".join(
        character
        for character in candidate
        if ord(character) >= 32 and ord(character) != 127
    ).strip(" .")
    if not candidate:
        candidate = f"source-{artifact_id}"
    if len(candidate) > 240:
        stem, separator, extension = candidate.rpartition(".")
        candidate = (
            f"{stem[: max(1, 239 - len(extension))]}.{extension}"
            if separator and len(extension) <= 24
            else candidate[:240]
        )
    return candidate


class SourceArtifactRepository:
    """Return only retained, verified raw artifacts for an authorized item."""

    def list_for_authorized_item(
        self,
        connection: ManagedConnection,
        *,
        item_id: str,
        authorization: ManagementItemAuthorization,
        as_of_revision_id: str | None = None,
    ) -> tuple[ItemSourceArtifactRecord, ...]:
        if not isinstance(connection, ManagedConnection) or not connection.in_transaction:
            raise TypeError("Source artifact reads require an active transaction.")
        item = _uuid(item_id, "item ID")
        if not isinstance(authorization, ManagementItemAuthorization):
            raise TypeError("A resolved item authorization is required.")
        target_revision = _uuid(
            as_of_revision_id or authorization.source_revision_id,
            "target revision ID",
        )
        if (
            authorization.origin == "overlay"
            and target_revision != authorization.source_revision_id
        ):
            return ()
        rows = connection.execute(
            """SELECT source.artifact_id,source.item_id,source.revision_id,
                      artifact.origin_type,artifact.original_filename,
                      artifact.media_type AS artifact_media_type,
                      artifact.raw_sha256,artifact.raw_size_bytes,
                      artifact.created_at,raw.logical_name,
                      raw.media_type AS raw_media_type,raw.content_sha256,
                      raw.size_bytes,blob.sha256 AS blob_sha256,
                      blob.size_bytes AS blob_size_bytes,
                      blob.media_type AS blob_media_type
                 FROM item_source AS source
                 JOIN source_artifact AS artifact
                   ON artifact.id=source.artifact_id
                 JOIN artifact_blob AS raw
                   ON raw.artifact_id=artifact.id
                  AND raw.role='raw' AND raw.ordinal=0
                 JOIN artifact_blob_body AS body
                   ON body.artifact_blob_id=raw.id
                  AND body.content_sha256=raw.content_sha256
                 JOIN blob_object AS blob
                   ON blob.sha256=body.content_sha256
                 JOIN knowledge_revision AS source_revision
                   ON source_revision.id=source.revision_id
                  AND source_revision.item_id=source.item_id
                 JOIN knowledge_revision AS target_revision
                   ON target_revision.id=?
                  AND target_revision.item_id=source.item_id
                WHERE source.item_id=?
                  AND source_revision.revision_no<=target_revision.revision_no
                  AND artifact.body_retention_status='present'
                  AND raw.body_retention_status='present'
                  AND blob.verification_status='verified'
                ORDER BY source.created_at,source.id,artifact.id""",
            (target_revision, item),
        ).fetchall()
        has_protected_provenance = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='protected_extraction_provenance'"
        ).fetchone() is not None
        result: list[ItemSourceArtifactRecord] = []
        seen: set[str] = set()
        for row in rows:
            artifact_id = _uuid(row["artifact_id"], "artifact ID")
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            content_sha256 = _sha(row["content_sha256"], "blob digest")
            raw_sha256 = _sha(row["raw_sha256"], "raw digest")
            blob_sha256 = _sha(row["blob_sha256"], "blob-object digest")
            size = row["size_bytes"]
            if (
                content_sha256 != raw_sha256
                or content_sha256 != blob_sha256
                or isinstance(size, bool)
                or not isinstance(size, int)
                or size < 0
                or size != row["raw_size_bytes"]
                or size != row["blob_size_bytes"]
            ):
                raise SourceArtifactRepositoryIntegrityError(
                    "Stored raw artifact evidence is inconsistent."
                )
            media_type = _text(row["raw_media_type"], "media type", 255)
            if (
                media_type != _text(row["artifact_media_type"], "media type", 255)
            ):
                raise SourceArtifactRepositoryIntegrityError(
                    "Stored raw artifact media types are inconsistent."
                )
            # blob_object is a content-addressed storage object and older
            # imports intentionally record its transport-neutral type as
            # application/octet-stream.  The source/raw artifact pair owns the
            # user-facing media type; the blob value still must be well formed.
            _text(row["blob_media_type"], "blob media type", 255)
            created_at = row["created_at"]
            if isinstance(created_at, bool) or not isinstance(created_at, int) or created_at < 0:
                raise SourceArtifactRepositoryIntegrityError(
                    "Stored artifact creation time is invalid."
                )
            protected_kind = None
            if has_protected_provenance:
                provenance = connection.execute(
                    """SELECT DISTINCT source_kind,original_sha256
                         FROM protected_extraction_provenance
                        WHERE artifact_id=?""",
                    (artifact_id,),
                ).fetchall()
                if provenance:
                    if (
                        len(provenance) != 1
                        or provenance[0]["original_sha256"] != content_sha256
                        or provenance[0]["source_kind"] not in (
                            "pdf_standard_encryption",
                            "office_encrypted_package_candidate",
                            "company_policy_protected",
                        )
                    ):
                        raise SourceArtifactRepositoryIntegrityError(
                            "Stored protected source evidence is inconsistent."
                        )
                    protected_kind = provenance[0]["source_kind"]
            result.append(
                ItemSourceArtifactRecord(
                    artifact_id=artifact_id,
                    item_id=_uuid(row["item_id"], "item ID"),
                    source_revision_id=_uuid(
                        row["revision_id"], "source revision ID"
                    ),
                    origin_type=_text(row["origin_type"], "origin type", 20),
                    original_filename=_filename(
                        row["original_filename"], row["logical_name"], artifact_id
                    ),
                    media_type=media_type,
                    size_bytes=size,
                    content_sha256=content_sha256,
                    created_at_ms=created_at,
                    protected_source_kind=protected_kind,
                )
            )
        return tuple(result)


__all__ = [
    "ItemSourceArtifactRecord",
    "SourceArtifactRepository",
    "SourceArtifactRepositoryIntegrityError",
]

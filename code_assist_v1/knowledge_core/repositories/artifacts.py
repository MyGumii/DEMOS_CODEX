"""Connection-scoped source-artifact and immutable-blob persistence.

This module contains the P2.3 legacy and P4.1 general storage boundaries used
after a source has already been read, hashed, normalized (for Markdown), and
staged by an adapter outside SQLite.  It performs no filesystem I/O and never
opens, commits, or rolls back the caller-owned transaction.

Bundle methods deliberately accept only deterministic identities.  They
preflight every schema-visible identity, unique key, and dependency before the
first write, then either insert the complete bundle or verify an exact replay.
Unexpected SQLite failures still have to escape the service-owned transaction;
repositories cannot make a sequence of dependent INSERT statements atomic when
a caller catches an exception and commits the surrounding transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
from typing import Literal, Mapping, Sequence
from uuid import UUID, uuid5

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_RECORD_RE = re.compile(r"^rec_[0-9a-f]{24}$", re.ASCII)
_RELATIONSHIP_RE = re.compile(r"^rel_[0-9a-f]{24}$", re.ASCII)
_WARNING_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$", re.ASCII)
_PHYSICAL_LOCATOR_RE = re.compile(
    r"^p2v1/[a-z0-9][a-z0-9._-]{0,127}/[0-9a-f]{64}/physical_file/0$",
    re.ASCII,
)
_ARTIFACT_NAMESPACE = UUID("6993332b-f4ea-49aa-9bd2-63536f247978")
_RAW_MANIFEST_NAMESPACE = UUID("d785947c-c80a-4b04-b55b-47d2da1a2171")
_EXTRACTION_NAMESPACE = UUID("b8398747-cbb8-4973-8657-868c04ab4ded")
_EMBEDDED_MANIFEST_NAMESPACE = UUID("5079ba99-2d34-44a5-8139-75e4219e61b8")
_ORPHAN_GRACE_MS = 2_592_000_000
_SQLITE_INTEGER_MAX = 9_223_372_036_854_775_807
CAS_BLOB_MEDIA_TYPE = "application/octet-stream"


class ArtifactRepositoryError(KnowledgeCoreDatabaseError):
    code = "ARTIFACT_REPOSITORY_ERROR"


class ArtifactBundleConflictError(ArtifactRepositoryError):
    code = "ARTIFACT_BUNDLE_CONFLICT"


class ArtifactBundleIntegrityError(ArtifactRepositoryError):
    code = "ARTIFACT_BUNDLE_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class LegacyArtifactIds:
    artifact_id: str
    raw_manifest_id: str
    extraction_snapshot_id: str


@dataclass(frozen=True, slots=True)
class StagedVerifiedBlob:
    """Physical blob evidence produced before entering the DB transaction."""

    sha256: str
    size_bytes: int
    media_type: str
    relative_path: str
    created_at_ms: int
    verified_at_ms: int


@dataclass(frozen=True, slots=True)
class PreparedMarkdownBody:
    """Already verified Markdown body; exactly one storage form is selected.

    An upstream adapter is responsible for UTF-8 encoding, exact hashing, and
    normalization before constructing this value.  The repository binds those
    observations to canonical metadata and stored rows without re-reading a
    file or normalizing content inside the transaction.
    """

    content_md: str | None
    content_blob_hash: str | None
    content_size_bytes: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class LegacyExtractionSpec:
    normalized_content_sha256: str
    normalizer_version: str
    extractor_name: str
    extractor_version: str
    warnings_json: str
    body: PreparedMarkdownBody
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class LegacyArtifactBundle:
    plan_sha256: str
    inventory_record_id: str
    ids: LegacyArtifactIds
    owner_user_id: str
    origin_key: str
    raw_blob: StagedVerifiedBlob
    logical_name: str
    original_filename: str | None
    source_owner_snapshot_json: str
    metadata_json: str
    created_at_ms: int
    extraction: LegacyExtractionSpec | None = None


@dataclass(frozen=True, slots=True)
class LegacyArtifactBundleResult:
    ids: LegacyArtifactIds
    inserted: bool


@dataclass(frozen=True, slots=True)
class LegacyEmbeddedAssetLink:
    plan_sha256: str
    relationship_id: str
    manifest_id: str
    source_artifact_id: str
    target_asset_artifact_id: str
    target_asset_raw_manifest_id: str
    target_blob: StagedVerifiedBlob
    logical_name: str
    logical_name_sha256: str
    ordinal: int
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class LegacyEmbeddedAssetLinkResult:
    manifest_id: str
    inserted: bool


@dataclass(frozen=True, slots=True)
class ArtifactIds:
    """Caller-fixed canonical identities for one immutable source snapshot."""

    artifact_id: str
    raw_manifest_id: str
    extraction_snapshot_id: str


@dataclass(frozen=True, slots=True)
class ArtifactExtractionSpec:
    """One immutable ready or failed extraction result.

    Extraction and normalization are completed before the repository's write
    transaction.  Ready snapshots carry an exact Markdown body; failed
    snapshots deliberately carry no body or derived hashes.
    """

    status: Literal["ready", "failed"]
    extracted_sha256: str | None
    normalized_content_sha256: str | None
    extracted_size_bytes: int | None
    normalizer_version: str | None
    extractor_name: str
    extractor_version: str
    warnings_json: str
    body: PreparedMarkdownBody | None
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class CanonicalEmbeddedAssetSpec:
    """One verified embedded child produced by canonical rich extraction."""

    manifest_id: str
    blob: StagedVerifiedBlob
    logical_name: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class SourceArtifactBundle:
    """Prepared non-legacy source evidence for one DB commit.

    Synchronous sources include their immutable extraction.  Asynchronous
    sources deliberately persist only the raw artifact first; a durable worker
    later adds the one exact extraction through ``put_source_extraction``.
    """

    ids: ArtifactIds
    origin_type: Literal["upload", "notion", "chat", "manual"]
    origin_key: str | None
    origin_revision_key: str | None
    raw_blob: StagedVerifiedBlob
    logical_name: str
    original_filename: str | None
    publisher_user_id: str
    source_owner_snapshot_json: str
    metadata_json: str
    metadata_schema_version: int
    created_at_ms: int
    extraction: ArtifactExtractionSpec | None


@dataclass(frozen=True, slots=True)
class SourceArtifactBundleResult:
    ids: ArtifactIds
    inserted: bool


@dataclass(frozen=True, slots=True)
class SourceExtractionResult:
    extraction_snapshot_id: str
    inserted: bool


@dataclass(frozen=True, slots=True)
class OrphanBodyCleanupResult:
    artifact_id: str
    changed: bool
    deleted_artifact_body_count: int
    deleted_extraction_body_count: int


def _uuid(value: object, *, field: str) -> str:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{field} must be a canonical lowercase UUID.") from None
    canonical = str(parsed)
    if not isinstance(value, str) or value != canonical:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return canonical


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 value.")
    return value


def _record_id(value: object) -> str:
    if not isinstance(value, str) or _RECORD_RE.fullmatch(value) is None:
        raise ValueError("inventory_record_id is invalid.")
    return value


def _relationship_id(value: object) -> str:
    if not isinstance(value, str) or _RELATIONSHIP_RE.fullmatch(value) is None:
        raise ValueError("relationship_id is invalid.")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("owner_user_id is invalid.")
    return value


def _text(value: object, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.strip()) <= maximum
        or "\x00" in value
    ):
        raise ValueError(f"{field} is invalid.")
    return value


def _utc_ms(value: object, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _SQLITE_INTEGER_MAX
    ):
        raise ValueError(f"{field} must be a non-negative UTC millisecond integer.")
    return value


def _size(value: object, *, field: str = "size_bytes") -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _SQLITE_INTEGER_MAX
    ):
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


def _canonical_json(value: object, *, field: str, array: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be canonical JSON.")
    try:
        parsed = json.loads(value)
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
        raise ValueError(f"{field} must be canonical JSON.") from None
    expected = list if array else dict
    if type(parsed) is not expected or canonical != value:
        raise ValueError(f"{field} must be canonical JSON.")
    return value


def _warning_codes_json(value: object) -> str:
    canonical = _canonical_json(value, field="warnings_json", array=True)
    parsed = json.loads(canonical)
    if (
        len(parsed) > 100
        or any(
            not isinstance(code, str) or _WARNING_CODE_RE.fullmatch(code) is None
            for code in parsed
        )
        or parsed != sorted(set(parsed))
    ):
        raise ValueError("warnings_json must be a sorted unique safe-code array.")
    return canonical


def _connection(connection: ManagedConnection, *, write: bool) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("ArtifactRepository requires a ManagedConnection.")
    if write and not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Artifact repository writes require an existing managed transaction."
        )
    return connection


def _id_for(namespace: UUID, *, plan_sha256: str, source_id: str) -> str:
    plan_sha256 = _sha(plan_sha256, field="plan_sha256")
    return str(uuid5(namespace, f"p2.3:{plan_sha256}:{source_id}"))


def legacy_artifact_ids_for_record(
    plan_sha256: str, inventory_record_id: str
) -> LegacyArtifactIds:
    """Return every deterministic artifact-side ID for one reviewed record."""

    record_id = _record_id(inventory_record_id)
    return LegacyArtifactIds(
        artifact_id=_id_for(
            _ARTIFACT_NAMESPACE, plan_sha256=plan_sha256, source_id=record_id
        ),
        raw_manifest_id=_id_for(
            _RAW_MANIFEST_NAMESPACE, plan_sha256=plan_sha256, source_id=record_id
        ),
        extraction_snapshot_id=_id_for(
            _EXTRACTION_NAMESPACE, plan_sha256=plan_sha256, source_id=record_id
        ),
    )


def legacy_embedded_manifest_id_for_relationship(
    plan_sha256: str, relationship_id: str
) -> str:
    relationship_id = _relationship_id(relationship_id)
    return _id_for(
        _EMBEDDED_MANIFEST_NAMESPACE,
        plan_sha256=plan_sha256,
        source_id=relationship_id,
    )


def _prepare_blob(value: StagedVerifiedBlob) -> StagedVerifiedBlob:
    if not isinstance(value, StagedVerifiedBlob):
        raise TypeError("A StagedVerifiedBlob is required.")
    sha256 = _sha(value.sha256, field="blob_sha256")
    size_bytes = _size(value.size_bytes)
    media_type = _text(value.media_type, field="media_type", maximum=255)
    relative_path = _text(value.relative_path, field="relative_path", maximum=500)
    expected_path = f"{sha256[:2]}/{sha256[2:4]}/{sha256}"
    if relative_path != expected_path:
        raise ValueError("relative_path is not the canonical content-addressed path.")
    created_at_ms = _utc_ms(value.created_at_ms, field="created_at_ms")
    verified_at_ms = _utc_ms(value.verified_at_ms, field="verified_at_ms")
    if verified_at_ms < created_at_ms:
        raise ValueError("verified_at_ms cannot precede created_at_ms.")
    return StagedVerifiedBlob(
        sha256,
        size_bytes,
        media_type,
        relative_path,
        created_at_ms,
        verified_at_ms,
    )


def _prepare_body(value: PreparedMarkdownBody) -> PreparedMarkdownBody:
    if not isinstance(value, PreparedMarkdownBody):
        raise TypeError("A PreparedMarkdownBody is required.")
    if (value.content_md is None) == (value.content_blob_hash is None):
        raise ValueError("A Markdown body requires exactly one storage form.")
    if value.content_md is not None:
        if not isinstance(value.content_md, str) or "\x00" in value.content_md:
            raise ValueError("The prepared inline Markdown body is invalid.")
        content_md = value.content_md
        content_blob_hash = None
    else:
        content_md = None
        content_blob_hash = _sha(
            value.content_blob_hash, field="content_blob_hash"  # type: ignore[arg-type]
        )
    prepared = PreparedMarkdownBody(
        content_md=content_md,
        content_blob_hash=content_blob_hash,
        content_size_bytes=_size(
            value.content_size_bytes, field="content_size_bytes"
        ),
        content_sha256=_sha(value.content_sha256, field="content_sha256"),
    )
    if prepared.content_md is not None:
        encoded = prepared.content_md.encode("utf-8")
        if (
            len(encoded) != prepared.content_size_bytes
            or hashlib.sha256(encoded).hexdigest() != prepared.content_sha256
        ):
            raise ValueError(
                "The inline Markdown body does not match its exact size and hash."
            )
    return prepared


def _positive_int(value: object, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= _SQLITE_INTEGER_MAX
    ):
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _optional_text(value: object, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field=field, maximum=maximum)


def _artifact_ids(value: object) -> ArtifactIds:
    if not isinstance(value, ArtifactIds):
        raise TypeError("ArtifactIds are required.")
    artifact_id = _uuid(value.artifact_id, field="artifact_id")
    raw_manifest_id = _uuid(value.raw_manifest_id, field="raw_manifest_id")
    extraction_id = _uuid(
        value.extraction_snapshot_id, field="extraction_snapshot_id"
    )
    if len({artifact_id, raw_manifest_id, extraction_id}) != 3:
        raise ValueError("Artifact bundle IDs must be distinct.")
    return ArtifactIds(artifact_id, raw_manifest_id, extraction_id)


def _prepare_extraction(
    value: object, *, artifact_created_at_ms: int
) -> ArtifactExtractionSpec:
    if not isinstance(value, ArtifactExtractionSpec):
        raise TypeError("An ArtifactExtractionSpec is required.")
    if value.status not in {"ready", "failed"}:
        raise ValueError("Extraction status is invalid.")
    extractor_name = _text(
        value.extractor_name, field="extractor_name", maximum=120
    )
    extractor_version = _text(
        value.extractor_version, field="extractor_version", maximum=120
    )
    warnings_json = _warning_codes_json(value.warnings_json)
    created_at_ms = _utc_ms(
        value.created_at_ms, field="extraction.created_at_ms"
    )
    if created_at_ms < artifact_created_at_ms:
        raise ValueError("Extraction time cannot precede artifact creation.")

    if value.status == "failed":
        if any(
            field is not None
            for field in (
                value.extracted_sha256,
                value.normalized_content_sha256,
                value.extracted_size_bytes,
                value.normalizer_version,
                value.body,
            )
        ):
            raise ValueError("A failed extraction cannot claim body or hash evidence.")
        return ArtifactExtractionSpec(
            "failed",
            None,
            None,
            None,
            None,
            extractor_name,
            extractor_version,
            warnings_json,
            None,
            created_at_ms,
        )

    extracted_sha256 = _sha(value.extracted_sha256, field="extracted_sha256")
    normalized_sha256 = _sha(
        value.normalized_content_sha256,
        field="normalized_content_sha256",
    )
    extracted_size = _size(
        value.extracted_size_bytes, field="extracted_size_bytes"
    )
    normalizer = _text(
        value.normalizer_version, field="normalizer_version", maximum=80
    )
    if normalizer != "markdown_lf_v1":
        raise ValueError("Ready Markdown requires the frozen normalizer.")
    body = _prepare_body(value.body)
    if (
        body.content_sha256 != extracted_sha256
        or body.content_size_bytes != extracted_size
    ):
        raise ValueError("Extraction metadata does not match its exact body.")
    if body.content_md is not None:
        normalized = body.content_md.replace("\r\n", "\n").replace("\r", "\n")
        observed_normalized = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if observed_normalized != normalized_sha256:
            raise ValueError("The normalized Markdown hash is inconsistent.")
    return ArtifactExtractionSpec(
        "ready",
        extracted_sha256,
        normalized_sha256,
        extracted_size,
        normalizer,
        extractor_name,
        extractor_version,
        warnings_json,
        body,
        created_at_ms,
    )


def _prepare_embedded_assets(
    value: object, *, extraction_created_at_ms: int
) -> tuple[CanonicalEmbeddedAssetSpec, ...]:
    if not isinstance(value, tuple):
        raise TypeError("embedded_assets must be a tuple.")
    if len(value) > 64:
        raise ValueError("At most 64 embedded assets may be attached.")
    prepared: list[CanonicalEmbeddedAssetSpec] = []
    manifest_ids: set[str] = set()
    for expected_ordinal, candidate in enumerate(value, start=1):
        if not isinstance(candidate, CanonicalEmbeddedAssetSpec):
            raise TypeError("Each embedded asset must be canonical evidence.")
        manifest_id = _uuid(candidate.manifest_id, field="embedded_manifest_id")
        blob = _prepare_blob(candidate.blob)
        logical_name = _text(
            candidate.logical_name, field="embedded_logical_name", maximum=500
        )
        ordinal = _positive_int(candidate.ordinal, field="embedded_ordinal")
        if ordinal != expected_ordinal or ordinal > 64:
            raise ValueError("Embedded asset ordinals must be contiguous and ordered.")
        if blob.created_at_ms != extraction_created_at_ms:
            raise ValueError("Embedded asset time must match its extraction.")
        if manifest_id in manifest_ids:
            raise ValueError("Embedded asset manifest IDs must be distinct.")
        manifest_ids.add(manifest_id)
        prepared.append(
            CanonicalEmbeddedAssetSpec(manifest_id, blob, logical_name, ordinal)
        )
    return tuple(prepared)


def _prepare_source_owner_snapshot(value: object, *, publisher: str) -> str:
    canonical = _canonical_json(
        value, field="source_owner_snapshot_json"
    )
    parsed = json.loads(canonical)
    if set(parsed) != {"publisher_user_id", "schema_version"} or parsed != {
        "publisher_user_id": publisher,
        "schema_version": 1,
    }:
        raise ValueError("source_owner_snapshot_json is invalid.")
    return canonical


def _prepare_source_metadata(
    value: object,
    *,
    blob: StagedVerifiedBlob,
    extraction: ArtifactExtractionSpec | None,
    origin_type: str,
    origin_key: str | None,
    origin_revision_key: str | None,
) -> str:
    canonical = _canonical_json(value, field="metadata_json")
    parsed = json.loads(canonical)
    if extraction is None:
        if parsed != {
            "ingestion_state": "pending_extraction",
            "raw_sha256": blob.sha256,
            "raw_size_bytes": blob.size_bytes,
            "schema_version": 1,
        }:
            raise ValueError(
                "metadata_json is not the pending source-evidence shape."
            )
        return canonical
    expected_keys = {
        "lint_findings",
        "preparation_sha256",
        "publishable",
        "schema_version",
    }
    if origin_type == "notion":
        expected_keys.add("notion_provenance")
    if set(parsed) != expected_keys or parsed.get("schema_version") != 1:
        raise ValueError("metadata_json is not the P4.1 source-evidence shape.")
    if origin_type == "notion":
        provenance = parsed.get("notion_provenance")
        if not isinstance(provenance, dict) or set(provenance) != {
            "asset_manifest_sha256",
            "owner_user_id",
            "page_id",
            "revision",
            "source_snapshot_sha256",
            "updated_at",
        }:
            raise ValueError("metadata_json Notion provenance is invalid.")
        owner = _user(provenance.get("owner_user_id"))
        page_id = _text(
            provenance.get("page_id"), field="notion_page_id", maximum=1000
        )
        asset_manifest_sha = _sha(
            provenance.get("asset_manifest_sha256"),
            field="asset_manifest_sha256",
        )
        source_snapshot_sha = _sha(
            provenance.get("source_snapshot_sha256"),
            field="source_snapshot_sha256",
        )
        revision = provenance.get("revision")
        updated_at = provenance.get("updated_at")
        if revision is not None:
            _size(revision, field="notion_revision")
        if updated_at is not None:
            _utc_ms(updated_at, field="notion_updated_at")
        if (
            not asset_manifest_sha
            or origin_key != f"notion:{owner}:{page_id}"
            or origin_revision_key != source_snapshot_sha
        ):
            raise ValueError("metadata_json Notion provenance is inconsistent.")
    publishable = parsed.get("publishable")
    findings = parsed.get("lint_findings")
    preparation_sha = parsed.get("preparation_sha256")
    if type(publishable) is not bool or not isinstance(findings, list):
        raise ValueError("metadata_json source evidence is invalid.")
    _sha(preparation_sha, field="preparation_sha256")

    normalized_findings: list[dict[str, object]] = []
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {
            "code",
            "column",
            "line",
            "severity",
        }:
            raise ValueError("metadata_json lint evidence is invalid.")
        code = finding.get("code")
        severity = finding.get("severity")
        line = finding.get("line")
        column = finding.get("column")
        if (
            not isinstance(code, str)
            or _WARNING_CODE_RE.fullmatch(code) is None
            or severity not in {"blocker", "error", "warning", "info"}
            or (
                line is not None
                and (
                    isinstance(line, bool)
                    or not isinstance(line, int)
                    or line < 1
                )
            )
            or (
                column is not None
                and (
                    isinstance(column, bool)
                    or not isinstance(column, int)
                    or column < 1
                )
            )
            or (line is None) != (column is None)
        ):
            raise ValueError("metadata_json lint evidence is invalid.")
        normalized_findings.append(
            {"code": code, "column": column, "line": line, "severity": severity}
        )
    ordered = sorted(
        normalized_findings,
        key=lambda finding: (
            finding["line"] is None,
            finding["line"] or 0,
            finding["column"] or 0,
            finding["code"],
        ),
    )
    if normalized_findings != ordered or len(
        {
            (
                finding["code"],
                finding["severity"],
                finding["line"],
                finding["column"],
            )
            for finding in normalized_findings
        }
    ) != len(normalized_findings):
        raise ValueError("metadata_json lint evidence is not canonical.")
    expected_publishable = not any(
        finding["severity"] in {"blocker", "error"}
        for finding in normalized_findings
    )
    if publishable is not expected_publishable:
        raise ValueError("metadata_json publishability is inconsistent.")

    body = extraction.body
    preparation_payload = {
        "extraction": {
            "body": None
            if body is None
            else {
                "content_blob_hash": body.content_blob_hash,
                "content_sha256": body.content_sha256,
                "content_size_bytes": body.content_size_bytes,
            },
            "created_at_ms": extraction.created_at_ms,
            "extracted_sha256": extraction.extracted_sha256,
            "extracted_size_bytes": extraction.extracted_size_bytes,
            "extractor_name": extraction.extractor_name,
            "extractor_version": extraction.extractor_version,
            "normalized_content_sha256": extraction.normalized_content_sha256,
            "normalizer_version": extraction.normalizer_version,
            "status": extraction.status,
            "warnings": json.loads(extraction.warnings_json),
        },
        "lint_findings": normalized_findings,
        "raw_sha256": blob.sha256,
        "raw_size_bytes": blob.size_bytes,
        "schema_version": 1,
    }
    observed = hashlib.sha256(
        json.dumps(
            preparation_payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if preparation_sha != observed:
        raise ValueError("metadata_json preparation hash is inconsistent.")
    return canonical


def _prepare_source_bundle(value: object) -> SourceArtifactBundle:
    if not isinstance(value, SourceArtifactBundle):
        raise TypeError("A SourceArtifactBundle is required.")
    ids = _artifact_ids(value.ids)
    if value.origin_type not in {"upload", "notion", "chat", "manual"}:
        raise ValueError("origin_type is invalid.")
    origin_key = _optional_text(value.origin_key, field="origin_key", maximum=1000)
    origin_revision_key = _optional_text(
        value.origin_revision_key, field="origin_revision_key", maximum=500
    )
    blob = _prepare_blob(value.raw_blob)
    logical_name = _text(value.logical_name, field="logical_name", maximum=500)
    original_filename = _optional_text(
        value.original_filename, field="original_filename", maximum=500
    )
    publisher = _user(value.publisher_user_id)
    metadata_version = _positive_int(
        value.metadata_schema_version, field="metadata_schema_version"
    )
    created_at_ms = _utc_ms(value.created_at_ms, field="created_at_ms")
    if created_at_ms > _SQLITE_INTEGER_MAX - _ORPHAN_GRACE_MS:
        raise ValueError("created_at_ms exceeds the artifact retention domain.")
    extraction = (
        None
        if value.extraction is None
        else _prepare_extraction(
            value.extraction, artifact_created_at_ms=created_at_ms
        )
    )
    owner_json = _prepare_source_owner_snapshot(
        value.source_owner_snapshot_json, publisher=publisher
    )
    metadata_json = _prepare_source_metadata(
        value.metadata_json,
        blob=blob,
        extraction=extraction,
        origin_type=value.origin_type,
        origin_key=origin_key,
        origin_revision_key=origin_revision_key,
    )
    return SourceArtifactBundle(
        ids,
        value.origin_type,
        origin_key,
        origin_revision_key,
        blob,
        logical_name,
        original_filename,
        publisher,
        owner_json,
        metadata_json,
        metadata_version,
        created_at_ms,
        extraction,
    )


def _prepare_bundle(value: LegacyArtifactBundle) -> LegacyArtifactBundle:
    if not isinstance(value, LegacyArtifactBundle):
        raise TypeError("A LegacyArtifactBundle is required.")
    plan_sha256 = _sha(value.plan_sha256, field="plan_sha256")
    record_id = _record_id(value.inventory_record_id)
    expected_ids = legacy_artifact_ids_for_record(plan_sha256, record_id)
    if value.ids != expected_ids:
        raise ValueError("Artifact IDs do not match the reviewed record.")
    owner = _user(value.owner_user_id)
    origin_key = _text(value.origin_key, field="origin_key", maximum=1000)
    if _PHYSICAL_LOCATOR_RE.fullmatch(origin_key) is None:
        raise ValueError("origin_key is not a canonical P2.3 physical locator.")
    blob = _prepare_blob(value.raw_blob)
    logical_name = _text(value.logical_name, field="logical_name", maximum=500)
    original_filename = value.original_filename
    if original_filename is not None:
        original_filename = _text(
            original_filename, field="original_filename", maximum=500
        )
    owner_json = _canonical_json(
        value.source_owner_snapshot_json,
        field="source_owner_snapshot_json",
    )
    metadata_json = _canonical_json(value.metadata_json, field="metadata_json")
    created_at = _utc_ms(value.created_at_ms, field="created_at_ms")
    if created_at > _SQLITE_INTEGER_MAX - _ORPHAN_GRACE_MS:
        raise ValueError("created_at_ms exceeds the artifact retention domain.")
    extraction = value.extraction
    # P2.3 native Markdown and the frozen legacy text/JSON adapters both
    # produce a ready canonical Markdown body.  Assets deliberately omit an
    # extraction regardless of their filename/media type, so media type alone
    # cannot decide whether extraction evidence is legal.
    if extraction is not None:
        if not isinstance(extraction, LegacyExtractionSpec):
            raise TypeError("A LegacyExtractionSpec is required.")
        normalized = _sha(
            extraction.normalized_content_sha256,
            field="normalized_content_sha256",
        )
        normalizer = _text(
            extraction.normalizer_version,
            field="normalizer_version",
            maximum=80,
        )
        if normalizer != "markdown_lf_v1":
            raise ValueError("Legacy personal content requires the frozen normalizer.")
        extractor_name = _text(
            extraction.extractor_name, field="extractor_name", maximum=120
        )
        extractor_version = _text(
            extraction.extractor_version,
            field="extractor_version",
            maximum=120,
        )
        warnings = _canonical_json(
            extraction.warnings_json, field="warnings_json", array=True
        )
        body = _prepare_body(extraction.body)
        extraction_created_at = _utc_ms(
            extraction.created_at_ms, field="extraction.created_at_ms"
        )
        if (
            body.content_sha256 != blob.sha256
            or body.content_size_bytes != blob.size_bytes
            or (
                body.content_blob_hash is not None
                and body.content_blob_hash != blob.sha256
            )
        ):
            raise ValueError("Extraction body evidence does not match the raw source.")
        extraction = LegacyExtractionSpec(
            normalized,
            normalizer,
            extractor_name,
            extractor_version,
            warnings,
            body,
            extraction_created_at,
        )
    return LegacyArtifactBundle(
        plan_sha256,
        record_id,
        expected_ids,
        owner,
        origin_key,
        blob,
        logical_name,
        original_filename,
        owner_json,
        metadata_json,
        created_at,
        extraction,
    )


def _row_tuple(row: object | None, fields: Sequence[str]) -> tuple[object, ...] | None:
    if row is None:
        return None
    try:
        return tuple(row[field] for field in fields)  # type: ignore[index]
    except (KeyError, TypeError, IndexError) as exc:
        raise ArtifactBundleIntegrityError(
            "A canonical artifact row cannot be mapped."
        ) from exc


def _body_identity(body: PreparedMarkdownBody) -> tuple[object, ...]:
    return (
        body.content_md,
        body.content_blob_hash,
        body.content_size_bytes,
        body.content_sha256,
    )


class ArtifactRepository:
    """Exact insert-or-verify bundles for immutable source evidence."""

    __slots__ = ()

    @staticmethod
    def _preflight_blob(
        connection: ManagedConnection, blob: StagedVerifiedBlob
    ) -> bool:
        row = connection.execute(
            """SELECT sha256,size_bytes,media_type,relative_path,
                      verification_status,created_at,verified_at
               FROM blob_object WHERE sha256=?""",
            (blob.sha256,),
        ).fetchone()
        path_row = connection.execute(
            "SELECT sha256 FROM blob_object WHERE relative_path=?",
            (blob.relative_path,),
        ).fetchone()
        if row is None:
            if path_row is not None:
                raise ArtifactBundleConflictError(
                    "The content-addressed blob path is already bound incompatibly."
                )
            return True
        identity = _row_tuple(
            row,
            (
                "sha256",
                "size_bytes",
                "media_type",
                "relative_path",
                "verification_status",
            ),
        )
        if identity != (
            blob.sha256,
            blob.size_bytes,
            CAS_BLOB_MEDIA_TYPE,
            blob.relative_path,
            "verified",
        ) or type(row["created_at"]) is not int or type(row["verified_at"]) is not int:  # type: ignore[index]
            raise ArtifactBundleConflictError(
                "The content-addressed blob is already bound incompatibly."
            )
        if path_row is None or path_row["sha256"] != blob.sha256:  # type: ignore[index]
            raise ArtifactBundleIntegrityError(
                "The canonical blob path index is inconsistent."
            )
        return False

    @staticmethod
    def _require_body_blob(
        connection: ManagedConnection, body: PreparedMarkdownBody
    ) -> None:
        if body.content_blob_hash is None:
            return
        row = connection.execute(
            """SELECT size_bytes,verification_status,verified_at
               FROM blob_object WHERE sha256=?""",
            (body.content_blob_hash,),
        ).fetchone()
        if (
            row is None
            or row["size_bytes"] != body.content_size_bytes  # type: ignore[index]
            or row["verification_status"] != "verified"  # type: ignore[index]
            or type(row["verified_at"]) is not int  # type: ignore[index]
        ):
            raise ArtifactBundleConflictError(
                "The prepared Markdown body blob is not verified exactly."
            )

    @classmethod
    def _preflight_artifact_rows(
        cls, connection: ManagedConnection, bundle: LegacyArtifactBundle
    ) -> Literal["insert", "replay"]:
        ids = bundle.ids
        artifact = connection.execute(
            """SELECT id,origin_type,origin_key,origin_revision_key,raw_sha256,
                      raw_size_bytes,media_type,original_filename,
                      body_retention_status,body_retention_reason,body_purged_at,
                      orphan_cleanup_eligible_at,publisher_user_id,
                      source_owner_snapshot_json,metadata_json,
                      metadata_schema_version,created_at
               FROM source_artifact WHERE id=?""",
            (ids.artifact_id,),
        ).fetchone()
        raw_by_id = connection.execute(
            """SELECT id,artifact_id,content_sha256,size_bytes,role,logical_name,
                      media_type,ordinal,body_retention_status,created_at
               FROM artifact_blob WHERE id=?""",
            (ids.raw_manifest_id,),
        ).fetchone()
        raw_by_identity = connection.execute(
            """SELECT id FROM artifact_blob
               WHERE artifact_id=? AND role='raw' AND ordinal=0""",
            (ids.artifact_id,),
        ).fetchone()
        raw_body = connection.execute(
            """SELECT artifact_blob_id,content_sha256,created_at
               FROM artifact_blob_body WHERE artifact_blob_id=?""",
            (ids.raw_manifest_id,),
        ).fetchone()
        extraction = connection.execute(
            """SELECT id,artifact_id,status,extracted_sha256,
                      normalized_content_sha256,extracted_size_bytes,
                      normalizer_version,extractor_name,extractor_version,
                      warnings_json,body_retention_status,created_at
               FROM extraction_snapshot WHERE id=?""",
            (ids.extraction_snapshot_id,),
        ).fetchone()
        extraction_body = connection.execute(
            """SELECT snapshot_id,artifact_id,content_md,content_blob_hash,
                      content_size_bytes,content_sha256,created_at
               FROM extraction_snapshot_body
               WHERE snapshot_id=? AND artifact_id=?""",
            (ids.extraction_snapshot_id, ids.artifact_id),
        ).fetchone()

        expected_extraction = bundle.extraction is not None
        presence = (
            artifact is not None,
            raw_by_id is not None,
            raw_body is not None,
            extraction is not None,
            extraction_body is not None,
        )
        expected_presence = (True, True, True, expected_extraction, expected_extraction)
        if not any(presence):
            if raw_by_identity is not None:
                raise ArtifactBundleConflictError(
                    "The raw artifact identity is already bound incompatibly."
                )
            return "insert"
        if presence != expected_presence:
            raise ArtifactBundleConflictError(
                "The legacy artifact bundle is only partially present."
            )
        if raw_by_identity is None or raw_by_identity["id"] != ids.raw_manifest_id:  # type: ignore[index]
            raise ArtifactBundleConflictError(
                "The raw artifact identity is already bound incompatibly."
            )

        blob = bundle.raw_blob
        artifact_expected = (
            ids.artifact_id,
            "legacy",
            bundle.origin_key,
            bundle.inventory_record_id,
            blob.sha256,
            blob.size_bytes,
            blob.media_type,
            bundle.original_filename,
            "present",
            None,
            None,
            bundle.created_at_ms + _ORPHAN_GRACE_MS,
            bundle.owner_user_id,
            bundle.source_owner_snapshot_json,
            bundle.metadata_json,
            1,
            bundle.created_at_ms,
        )
        if _row_tuple(
            artifact,
            (
                "id",
                "origin_type",
                "origin_key",
                "origin_revision_key",
                "raw_sha256",
                "raw_size_bytes",
                "media_type",
                "original_filename",
                "body_retention_status",
                "body_retention_reason",
                "body_purged_at",
                "orphan_cleanup_eligible_at",
                "publisher_user_id",
                "source_owner_snapshot_json",
                "metadata_json",
                "metadata_schema_version",
                "created_at",
            ),
        ) != artifact_expected:
            raise ArtifactBundleConflictError(
                "The source artifact is already bound incompatibly."
            )
        if _row_tuple(
            raw_by_id,
            (
                "id",
                "artifact_id",
                "content_sha256",
                "size_bytes",
                "role",
                "logical_name",
                "media_type",
                "ordinal",
                "body_retention_status",
                "created_at",
            ),
        ) != (
            ids.raw_manifest_id,
            ids.artifact_id,
            blob.sha256,
            blob.size_bytes,
            "raw",
            bundle.logical_name,
            blob.media_type,
            0,
            "present",
            bundle.created_at_ms,
        ) or _row_tuple(
            raw_body, ("artifact_blob_id", "content_sha256", "created_at")
        ) != (ids.raw_manifest_id, blob.sha256, bundle.created_at_ms):
            raise ArtifactBundleConflictError(
                "The raw artifact manifest is already bound incompatibly."
            )

        if bundle.extraction is not None:
            spec = bundle.extraction
            if _row_tuple(
                extraction,
                (
                    "id",
                    "artifact_id",
                    "status",
                    "extracted_sha256",
                    "normalized_content_sha256",
                    "extracted_size_bytes",
                    "normalizer_version",
                    "extractor_name",
                    "extractor_version",
                    "warnings_json",
                    "body_retention_status",
                    "created_at",
                ),
            ) != (
                ids.extraction_snapshot_id,
                ids.artifact_id,
                "ready",
                blob.sha256,
                spec.normalized_content_sha256,
                blob.size_bytes,
                spec.normalizer_version,
                spec.extractor_name,
                spec.extractor_version,
                spec.warnings_json,
                "present",
                spec.created_at_ms,
            ) or _row_tuple(
                extraction_body,
                (
                    "snapshot_id",
                    "artifact_id",
                    "content_md",
                    "content_blob_hash",
                    "content_size_bytes",
                    "content_sha256",
                    "created_at",
                ),
            ) != (
                ids.extraction_snapshot_id,
                ids.artifact_id,
                *_body_identity(spec.body),
                spec.created_at_ms,
                ):
                raise ArtifactBundleConflictError(
                    "The extraction snapshot is already bound incompatibly."
                )
        return "replay"

    @classmethod
    def _preflight_source_rows(
        cls, connection: ManagedConnection, bundle: SourceArtifactBundle
    ) -> Literal["insert", "replay"]:
        del cls
        ids = bundle.ids
        artifact = connection.execute(
            """SELECT id,origin_type,origin_key,origin_revision_key,raw_sha256,
                      raw_size_bytes,media_type,original_filename,
                      body_retention_status,body_retention_reason,body_purged_at,
                      orphan_cleanup_eligible_at,publisher_user_id,
                      source_owner_snapshot_json,metadata_json,
                      metadata_schema_version,created_at
               FROM source_artifact WHERE id=?""",
            (ids.artifact_id,),
        ).fetchone()
        raw_by_id = connection.execute(
            """SELECT id,artifact_id,content_sha256,size_bytes,role,logical_name,
                      media_type,ordinal,body_retention_status,created_at
               FROM artifact_blob WHERE id=?""",
            (ids.raw_manifest_id,),
        ).fetchone()
        raw_by_identity = connection.execute(
            """SELECT id FROM artifact_blob
               WHERE artifact_id=? AND role='raw' AND ordinal=0""",
            (ids.artifact_id,),
        ).fetchone()
        raw_body = connection.execute(
            """SELECT artifact_blob_id,content_sha256,created_at
               FROM artifact_blob_body WHERE artifact_blob_id=?""",
            (ids.raw_manifest_id,),
        ).fetchone()
        extraction = connection.execute(
            """SELECT id,artifact_id,status,extracted_sha256,
                      normalized_content_sha256,extracted_size_bytes,
                      normalizer_version,extractor_name,extractor_version,
                      warnings_json,body_retention_status,created_at
               FROM extraction_snapshot WHERE id=?""",
            (ids.extraction_snapshot_id,),
        ).fetchone()
        extraction_body = connection.execute(
            """SELECT snapshot_id,artifact_id,content_md,content_blob_hash,
                      content_size_bytes,content_sha256,created_at
               FROM extraction_snapshot_body
               WHERE snapshot_id=? AND artifact_id=?""",
            (ids.extraction_snapshot_id, ids.artifact_id),
        ).fetchone()

        spec = bundle.extraction
        ready = spec is not None and spec.status == "ready"
        has_extraction = spec is not None
        presence = (
            artifact is not None,
            raw_by_id is not None,
            raw_body is not None,
            extraction is not None,
            extraction_body is not None,
        )
        expected_presence = (True, True, True, has_extraction, ready)
        if not any(presence):
            if raw_by_identity is not None:
                raise ArtifactBundleConflictError(
                    "The raw artifact identity is already bound incompatibly."
                )
            return "insert"
        if presence != expected_presence:
            raise ArtifactBundleConflictError(
                "The source artifact bundle is only partially present."
            )
        if raw_by_identity is None or raw_by_identity["id"] != ids.raw_manifest_id:  # type: ignore[index]
            raise ArtifactBundleConflictError(
                "The raw artifact identity is already bound incompatibly."
            )

        blob = bundle.raw_blob
        if _row_tuple(
            artifact,
            (
                "id",
                "origin_type",
                "origin_key",
                "origin_revision_key",
                "raw_sha256",
                "raw_size_bytes",
                "media_type",
                "original_filename",
                "body_retention_status",
                "body_retention_reason",
                "body_purged_at",
                "orphan_cleanup_eligible_at",
                "publisher_user_id",
                "source_owner_snapshot_json",
                "metadata_json",
                "metadata_schema_version",
                "created_at",
            ),
        ) != (
            ids.artifact_id,
            bundle.origin_type,
            bundle.origin_key,
            bundle.origin_revision_key,
            blob.sha256,
            blob.size_bytes,
            blob.media_type,
            bundle.original_filename,
            "present",
            None,
            None,
            bundle.created_at_ms + _ORPHAN_GRACE_MS,
            bundle.publisher_user_id,
            bundle.source_owner_snapshot_json,
            bundle.metadata_json,
            bundle.metadata_schema_version,
            bundle.created_at_ms,
        ):
            raise ArtifactBundleConflictError(
                "The source artifact is already bound incompatibly."
            )
        if _row_tuple(
            raw_by_id,
            (
                "id",
                "artifact_id",
                "content_sha256",
                "size_bytes",
                "role",
                "logical_name",
                "media_type",
                "ordinal",
                "body_retention_status",
                "created_at",
            ),
        ) != (
            ids.raw_manifest_id,
            ids.artifact_id,
            blob.sha256,
            blob.size_bytes,
            "raw",
            bundle.logical_name,
            blob.media_type,
            0,
            "present",
            bundle.created_at_ms,
        ) or _row_tuple(
            raw_body, ("artifact_blob_id", "content_sha256", "created_at")
        ) != (ids.raw_manifest_id, blob.sha256, bundle.created_at_ms):
            raise ArtifactBundleConflictError(
                "The raw artifact manifest is already bound incompatibly."
            )

        if spec is None:
            expected_extraction = None
            expected_body = None
        elif spec.status == "ready":
            assert spec.body is not None
            expected_extraction = (
                ids.extraction_snapshot_id,
                ids.artifact_id,
                "ready",
                spec.extracted_sha256,
                spec.normalized_content_sha256,
                spec.extracted_size_bytes,
                spec.normalizer_version,
                spec.extractor_name,
                spec.extractor_version,
                spec.warnings_json,
                "present",
                spec.created_at_ms,
            )
            expected_body = (
                ids.extraction_snapshot_id,
                ids.artifact_id,
                *_body_identity(spec.body),
                spec.created_at_ms,
            )
        else:
            expected_extraction = (
                ids.extraction_snapshot_id,
                ids.artifact_id,
                "failed",
                None,
                None,
                None,
                None,
                spec.extractor_name,
                spec.extractor_version,
                spec.warnings_json,
                "none",
                spec.created_at_ms,
            )
            expected_body = None
        if (
            None
            if extraction is None
            else _row_tuple(
                extraction,
                (
                    "id",
                    "artifact_id",
                    "status",
                    "extracted_sha256",
                    "normalized_content_sha256",
                    "extracted_size_bytes",
                    "normalizer_version",
                    "extractor_name",
                    "extractor_version",
                    "warnings_json",
                    "body_retention_status",
                    "created_at",
                ),
            )
        ) != expected_extraction or (
            ready
            and _row_tuple(
                extraction_body,
                (
                    "snapshot_id",
                    "artifact_id",
                    "content_md",
                    "content_blob_hash",
                    "content_size_bytes",
                    "content_sha256",
                    "created_at",
                ),
            )
            != expected_body
        ):
            raise ArtifactBundleConflictError(
                "The extraction snapshot is already bound incompatibly."
            )
        return "replay"

    def put_source_bundle(
        self,
        connection: ManagedConnection,
        bundle: SourceArtifactBundle,
    ) -> SourceArtifactBundleResult:
        """Insert or exactly replay one non-legacy artifact/extraction bundle."""

        connection = _connection(connection, write=True)
        bundle = _prepare_source_bundle(bundle)
        owner = connection.execute(
            "SELECT 1 FROM app_user WHERE id=?", (bundle.publisher_user_id,)
        ).fetchone()
        if owner is None:
            raise ArtifactBundleConflictError("The artifact publisher does not exist.")

        blob_insert = self._preflight_blob(connection, bundle.raw_blob)
        spec = bundle.extraction
        if spec is not None and spec.status == "ready":
            assert spec.body is not None
            self._require_body_blob(connection, spec.body)
        action = self._preflight_source_rows(connection, bundle)
        if action == "replay":
            return SourceArtifactBundleResult(bundle.ids, False)

        blob = bundle.raw_blob
        try:
            if blob_insert:
                connection.execute(
                    """INSERT INTO blob_object(
                         sha256,size_bytes,media_type,relative_path,
                         verification_status,created_at,verified_at
                       ) VALUES (?,?,?,?,'verified',?,?)""",
                    (
                        blob.sha256,
                        blob.size_bytes,
                        CAS_BLOB_MEDIA_TYPE,
                        blob.relative_path,
                        blob.created_at_ms,
                        blob.verified_at_ms,
                    ),
                )
            connection.execute(
                """INSERT INTO source_artifact(
                     id,origin_type,origin_key,origin_revision_key,raw_sha256,
                     raw_size_bytes,media_type,original_filename,
                     body_retention_status,body_retention_reason,body_purged_at,
                     orphan_cleanup_eligible_at,publisher_user_id,
                     source_owner_snapshot_json,metadata_json,
                     metadata_schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,'present',NULL,NULL,?,?,?,?,?,?)""",
                (
                    bundle.ids.artifact_id,
                    bundle.origin_type,
                    bundle.origin_key,
                    bundle.origin_revision_key,
                    blob.sha256,
                    blob.size_bytes,
                    blob.media_type,
                    bundle.original_filename,
                    bundle.created_at_ms + _ORPHAN_GRACE_MS,
                    bundle.publisher_user_id,
                    bundle.source_owner_snapshot_json,
                    bundle.metadata_json,
                    bundle.metadata_schema_version,
                    bundle.created_at_ms,
                ),
            )
            connection.execute(
                """INSERT INTO artifact_blob(
                     id,artifact_id,content_sha256,size_bytes,role,logical_name,
                     media_type,ordinal,body_retention_status,created_at
                   ) VALUES (?,?,?,?,'raw',?,?,0,'present',?)""",
                (
                    bundle.ids.raw_manifest_id,
                    bundle.ids.artifact_id,
                    blob.sha256,
                    blob.size_bytes,
                    bundle.logical_name,
                    blob.media_type,
                    bundle.created_at_ms,
                ),
            )
            connection.execute(
                """INSERT INTO artifact_blob_body(
                     artifact_blob_id,content_sha256,created_at
                   ) VALUES (?,?,?)""",
                (
                    bundle.ids.raw_manifest_id,
                    blob.sha256,
                    bundle.created_at_ms,
                ),
            )
            if spec is None:
                pass
            elif spec.status == "ready":
                assert spec.body is not None
                connection.execute(
                    """INSERT INTO extraction_snapshot(
                         id,artifact_id,status,extracted_sha256,
                         normalized_content_sha256,extracted_size_bytes,
                         normalizer_version,extractor_name,extractor_version,
                         warnings_json,body_retention_status,created_at
                       ) VALUES (?,?,'ready',?,?,?,?,?,?,?,'present',?)""",
                    (
                        bundle.ids.extraction_snapshot_id,
                        bundle.ids.artifact_id,
                        spec.extracted_sha256,
                        spec.normalized_content_sha256,
                        spec.extracted_size_bytes,
                        spec.normalizer_version,
                        spec.extractor_name,
                        spec.extractor_version,
                        spec.warnings_json,
                        spec.created_at_ms,
                    ),
                )
                connection.execute(
                    """INSERT INTO extraction_snapshot_body(
                         snapshot_id,artifact_id,content_md,content_blob_hash,
                         content_size_bytes,content_sha256,created_at
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (
                        bundle.ids.extraction_snapshot_id,
                        bundle.ids.artifact_id,
                        spec.body.content_md,
                        spec.body.content_blob_hash,
                        spec.body.content_size_bytes,
                        spec.body.content_sha256,
                        spec.created_at_ms,
                    ),
                )
            else:
                connection.execute(
                    """INSERT INTO extraction_snapshot(
                         id,artifact_id,status,extracted_sha256,
                         normalized_content_sha256,extracted_size_bytes,
                         normalizer_version,extractor_name,extractor_version,
                         warnings_json,body_retention_status,created_at
                       ) VALUES (?,?,'failed',NULL,NULL,NULL,NULL,?,?,?,'none',?)""",
                    (
                        bundle.ids.extraction_snapshot_id,
                        bundle.ids.artifact_id,
                        spec.extractor_name,
                        spec.extractor_version,
                        spec.warnings_json,
                        spec.created_at_ms,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ArtifactBundleConflictError(
                "The source artifact bundle violates canonical constraints."
            ) from exc
        return SourceArtifactBundleResult(bundle.ids, True)

    def put_source_extraction(
        self,
        connection: ManagedConnection,
        *,
        artifact_id: str,
        extraction_snapshot_id: str,
        extraction: ArtifactExtractionSpec,
        embedded_assets: tuple[CanonicalEmbeddedAssetSpec, ...] = (),
    ) -> SourceExtractionResult:
        """Attach or exactly replay the sole durable-worker extraction.

        The raw artifact must already be the exact pending-extraction shape.
        Extraction and body preparation remain outside the caller-owned write
        transaction; this method only binds their immutable evidence.
        """

        connection = _connection(connection, write=True)
        artifact_id = _uuid(artifact_id, field="artifact_id")
        extraction_snapshot_id = _uuid(
            extraction_snapshot_id, field="extraction_snapshot_id"
        )
        artifact = connection.execute(
            """SELECT raw_sha256,raw_size_bytes,metadata_json,created_at,
                      body_retention_status
                 FROM source_artifact WHERE id=?""",
            (artifact_id,),
        ).fetchone()
        if artifact is None or artifact["body_retention_status"] != "present":
            raise ArtifactBundleConflictError(
                "The pending source artifact is unavailable."
            )
        try:
            metadata = json.loads(str(artifact["metadata_json"]))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ArtifactBundleIntegrityError(
                "The pending source metadata is invalid."
            ) from exc
        expected_metadata = {
            "ingestion_state": "pending_extraction",
            "raw_sha256": str(artifact["raw_sha256"]),
            "raw_size_bytes": int(artifact["raw_size_bytes"]),
            "schema_version": 1,
        }
        canonical_metadata = json.dumps(
            metadata,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if metadata != expected_metadata or canonical_metadata != str(
            artifact["metadata_json"]
        ):
            raise ArtifactBundleConflictError(
                "The source artifact is not pending durable extraction."
            )
        raw = connection.execute(
            """SELECT b.id,bb.content_sha256
                 FROM artifact_blob b
                 JOIN artifact_blob_body bb ON bb.artifact_blob_id=b.id
                WHERE b.artifact_id=? AND b.role='raw' AND b.ordinal=0
                  AND b.body_retention_status='present'""",
            (artifact_id,),
        ).fetchone()
        if raw is None or raw["content_sha256"] != artifact["raw_sha256"]:
            raise ArtifactBundleIntegrityError(
                "The pending raw artifact body is unavailable."
            )
        spec = _prepare_extraction(
            extraction,
            artifact_created_at_ms=int(artifact["created_at"]),
        )
        assets = _prepare_embedded_assets(
            embedded_assets, extraction_created_at_ms=spec.created_at_ms
        )
        if spec.status == "failed" and assets:
            raise ValueError("A failed extraction cannot have embedded assets.")
        if spec.status == "ready":
            assert spec.body is not None
            self._require_body_blob(connection, spec.body)
        asset_blob_inserts: dict[str, StagedVerifiedBlob] = {}
        for asset in assets:
            if self._preflight_blob(connection, asset.blob):
                asset_blob_inserts.setdefault(asset.blob.sha256, asset.blob)
            identity = connection.execute(
                "SELECT artifact_id,role,ordinal FROM artifact_blob WHERE id=?",
                (asset.manifest_id,),
            ).fetchone()
            if identity is not None and (
                identity["artifact_id"] != artifact_id
                or identity["role"] != "embedded_asset"
                or identity["ordinal"] != asset.ordinal
            ):
                raise ArtifactBundleConflictError(
                    "An embedded asset identity is already bound incompatibly."
                )

        existing = connection.execute(
            """SELECT id,artifact_id,status,extracted_sha256,
                      normalized_content_sha256,extracted_size_bytes,
                      normalizer_version,extractor_name,extractor_version,
                      warnings_json,body_retention_status,created_at
                 FROM extraction_snapshot WHERE id=?""",
            (extraction_snapshot_id,),
        ).fetchone()
        other = connection.execute(
            """SELECT id FROM extraction_snapshot
                WHERE artifact_id=? ORDER BY created_at,id LIMIT 1""",
            (artifact_id,),
        ).fetchone()
        body = connection.execute(
            """SELECT snapshot_id,artifact_id,content_md,content_blob_hash,
                      content_size_bytes,content_sha256,created_at
                 FROM extraction_snapshot_body
                WHERE snapshot_id=? AND artifact_id=?""",
            (extraction_snapshot_id, artifact_id),
        ).fetchone()
        asset_rows = connection.execute(
            """SELECT b.id,b.artifact_id,b.content_sha256,b.size_bytes,b.role,
                      b.logical_name,b.media_type,b.ordinal,
                      b.body_retention_status,b.created_at,
                      bb.artifact_blob_id,bb.content_sha256 AS body_sha256,
                      bb.created_at AS body_created_at
                 FROM artifact_blob b
                 LEFT JOIN artifact_blob_body bb ON bb.artifact_blob_id=b.id
                WHERE b.artifact_id=? AND b.role='embedded_asset'
                ORDER BY b.ordinal,b.id""",
            (artifact_id,),
        ).fetchall()
        expected_assets = tuple(
            (
                asset.manifest_id,
                artifact_id,
                asset.blob.sha256,
                asset.blob.size_bytes,
                "embedded_asset",
                asset.logical_name,
                asset.blob.media_type,
                asset.ordinal,
                "present",
                spec.created_at_ms,
                asset.manifest_id,
                asset.blob.sha256,
                spec.created_at_ms,
            )
            for asset in assets
        )
        actual_assets = tuple(
            _row_tuple(
                row,
                (
                    "id",
                    "artifact_id",
                    "content_sha256",
                    "size_bytes",
                    "role",
                    "logical_name",
                    "media_type",
                    "ordinal",
                    "body_retention_status",
                    "created_at",
                    "artifact_blob_id",
                    "body_sha256",
                    "body_created_at",
                ),
            )
            for row in asset_rows
        )
        if existing is not None:
            if other is None or other["id"] != extraction_snapshot_id:
                raise ArtifactBundleConflictError(
                    "The source artifact has multiple extraction identities."
                )
            if spec.status == "ready":
                assert spec.body is not None
                expected_row = (
                    extraction_snapshot_id,
                    artifact_id,
                    "ready",
                    spec.extracted_sha256,
                    spec.normalized_content_sha256,
                    spec.extracted_size_bytes,
                    spec.normalizer_version,
                    spec.extractor_name,
                    spec.extractor_version,
                    spec.warnings_json,
                    "present",
                    spec.created_at_ms,
                )
                expected_body = (
                    extraction_snapshot_id,
                    artifact_id,
                    *_body_identity(spec.body),
                    spec.created_at_ms,
                )
            else:
                expected_row = (
                    extraction_snapshot_id,
                    artifact_id,
                    "failed",
                    None,
                    None,
                    None,
                    None,
                    spec.extractor_name,
                    spec.extractor_version,
                    spec.warnings_json,
                    "none",
                    spec.created_at_ms,
                )
                expected_body = None
            actual_row = _row_tuple(
                existing,
                (
                    "id",
                    "artifact_id",
                    "status",
                    "extracted_sha256",
                    "normalized_content_sha256",
                    "extracted_size_bytes",
                    "normalizer_version",
                    "extractor_name",
                    "extractor_version",
                    "warnings_json",
                    "body_retention_status",
                    "created_at",
                ),
            )
            actual_body = None if body is None else _row_tuple(
                body,
                (
                    "snapshot_id",
                    "artifact_id",
                    "content_md",
                    "content_blob_hash",
                    "content_size_bytes",
                    "content_sha256",
                    "created_at",
                ),
            )
            if (
                actual_row != expected_row
                or actual_body != expected_body
                or actual_assets != expected_assets
            ):
                raise ArtifactBundleConflictError(
                    "The extraction identity is already bound incompatibly."
                )
            return SourceExtractionResult(extraction_snapshot_id, False)
        if other is not None or body is not None or asset_rows:
            raise ArtifactBundleConflictError(
                "The source artifact extraction is partially or incompatibly present."
            )

        try:
            for blob in asset_blob_inserts.values():
                connection.execute(
                    """INSERT INTO blob_object(
                         sha256,size_bytes,media_type,relative_path,
                         verification_status,created_at,verified_at
                       ) VALUES (?,?,?,?,'verified',?,?)""",
                    (
                        blob.sha256,
                        blob.size_bytes,
                        CAS_BLOB_MEDIA_TYPE,
                        blob.relative_path,
                        blob.created_at_ms,
                        blob.verified_at_ms,
                    ),
                )
            for asset in assets:
                connection.execute(
                    """INSERT INTO artifact_blob(
                         id,artifact_id,content_sha256,size_bytes,role,
                         logical_name,media_type,ordinal,
                         body_retention_status,created_at
                       ) VALUES (?,?,?,?,'embedded_asset',?,?,?,'present',?)""",
                    (
                        asset.manifest_id,
                        artifact_id,
                        asset.blob.sha256,
                        asset.blob.size_bytes,
                        asset.logical_name,
                        asset.blob.media_type,
                        asset.ordinal,
                        spec.created_at_ms,
                    ),
                )
                connection.execute(
                    """INSERT INTO artifact_blob_body(
                         artifact_blob_id,content_sha256,created_at
                       ) VALUES (?,?,?)""",
                    (
                        asset.manifest_id,
                        asset.blob.sha256,
                        spec.created_at_ms,
                    ),
                )
            if spec.status == "ready":
                assert spec.body is not None
                connection.execute(
                    """INSERT INTO extraction_snapshot(
                         id,artifact_id,status,extracted_sha256,
                         normalized_content_sha256,extracted_size_bytes,
                         normalizer_version,extractor_name,extractor_version,
                         warnings_json,body_retention_status,created_at
                       ) VALUES (?,?,'ready',?,?,?,?,?,?,?,'present',?)""",
                    (
                        extraction_snapshot_id,
                        artifact_id,
                        spec.extracted_sha256,
                        spec.normalized_content_sha256,
                        spec.extracted_size_bytes,
                        spec.normalizer_version,
                        spec.extractor_name,
                        spec.extractor_version,
                        spec.warnings_json,
                        spec.created_at_ms,
                    ),
                )
                connection.execute(
                    """INSERT INTO extraction_snapshot_body(
                         snapshot_id,artifact_id,content_md,content_blob_hash,
                         content_size_bytes,content_sha256,created_at
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (
                        extraction_snapshot_id,
                        artifact_id,
                        spec.body.content_md,
                        spec.body.content_blob_hash,
                        spec.body.content_size_bytes,
                        spec.body.content_sha256,
                        spec.created_at_ms,
                    ),
                )
            else:
                connection.execute(
                    """INSERT INTO extraction_snapshot(
                         id,artifact_id,status,extracted_sha256,
                         normalized_content_sha256,extracted_size_bytes,
                         normalizer_version,extractor_name,extractor_version,
                         warnings_json,body_retention_status,created_at
                       ) VALUES (?,?,'failed',NULL,NULL,NULL,NULL,?,?,?,'none',?)""",
                    (
                        extraction_snapshot_id,
                        artifact_id,
                        spec.extractor_name,
                        spec.extractor_version,
                        spec.warnings_json,
                        spec.created_at_ms,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ArtifactBundleConflictError(
                "The source extraction violates canonical constraints."
            ) from exc
        return SourceExtractionResult(extraction_snapshot_id, True)

    def cleanup_orphan_bodies(
        self,
        connection: ManagedConnection,
        *,
        artifact_id: str,
        purged_at_ms: int,
    ) -> OrphanBodyCleanupResult:
        """Remove only body links for one terminal, unreferenced 30-day source.

        Metadata, hashes, manifests, extraction metadata, and the CAS object row
        remain.  Physical unreferenced-blob collection is a separate idempotent
        storage operation; this transaction records the canonical eligibility
        and retention effect only.
        """

        connection = _connection(connection, write=True)
        artifact_id = _uuid(artifact_id, field="artifact_id")
        purged_at = _utc_ms(purged_at_ms, field="purged_at_ms")
        artifact = connection.execute(
            """SELECT body_retention_status,body_retention_reason,
                      body_purged_at,orphan_cleanup_eligible_at
                 FROM source_artifact WHERE id=?""",
            (artifact_id,),
        ).fetchone()
        if artifact is None:
            raise ArtifactBundleConflictError("The source artifact does not exist.")
        if artifact["body_retention_status"] == "purged":
            if (
                artifact["body_retention_reason"] != "orphan_ingestion_cleanup"
                or artifact["body_purged_at"] is None
                or connection.execute(
                    """SELECT 1 FROM artifact_blob_body bb
                         JOIN artifact_blob b ON b.id=bb.artifact_blob_id
                        WHERE b.artifact_id=? LIMIT 1""",
                    (artifact_id,),
                ).fetchone()
                is not None
                or connection.execute(
                    """SELECT 1 FROM extraction_snapshot_body
                        WHERE artifact_id=? LIMIT 1""",
                    (artifact_id,),
                ).fetchone()
                is not None
            ):
                raise ArtifactBundleIntegrityError(
                    "The stored orphan cleanup state is inconsistent."
                )
            return OrphanBodyCleanupResult(artifact_id, False, 0, 0)
        if (
            artifact["body_retention_status"] != "present"
            or purged_at < int(artifact["orphan_cleanup_eligible_at"])
        ):
            raise ArtifactBundleConflictError(
                "The source artifact is not orphan-cleanup eligible."
            )
        blocker = connection.execute(
            """SELECT
                 EXISTS(SELECT 1 FROM publication_preview p
                          WHERE p.artifact_id=?
                            AND p.status IN ('queued','extracting','ready'))
                 OR EXISTS(SELECT 1 FROM item_source s WHERE s.artifact_id=?)
                 OR EXISTS(SELECT 1 FROM lineage_group g
                            WHERE g.initial_artifact_id=? OR g.base_artifact_id=?)
                 OR EXISTS(SELECT 1 FROM conversation_context_binding b
                            WHERE b.source_artifact_id=?
                              AND b.binding_mode='snapshot'
                              AND b.status<>'detached')
                 OR EXISTS(SELECT 1 FROM change_proposal p
                            WHERE p.source_artifact_id=?
                              AND p.state IN ('pending_review','approved','apply_failed'))
                 OR EXISTS(SELECT 1 FROM backup_run r
                            WHERE r.state IN ('preparing','copying'))""",
            (
                artifact_id,
                artifact_id,
                artifact_id,
                artifact_id,
                artifact_id,
                artifact_id,
            ),
        ).fetchone()
        if blocker is None or int(blocker[0]) != 0:
            raise ArtifactBundleConflictError(
                "The source artifact still has a retention reference."
            )
        try:
            extraction_delete = connection.execute(
                "DELETE FROM extraction_snapshot_body WHERE artifact_id=?",
                (artifact_id,),
            ).rowcount
            connection.execute(
                """UPDATE extraction_snapshot SET body_retention_status='purged'
                    WHERE artifact_id=? AND status='ready'
                      AND body_retention_status='present'""",
                (artifact_id,),
            )
            artifact_delete = connection.execute(
                """DELETE FROM artifact_blob_body
                    WHERE artifact_blob_id IN (
                      SELECT id FROM artifact_blob WHERE artifact_id=?
                    )""",
                (artifact_id,),
            ).rowcount
            connection.execute(
                """UPDATE artifact_blob SET body_retention_status='purged'
                    WHERE artifact_id=? AND body_retention_status='present'""",
                (artifact_id,),
            )
            updated = connection.execute(
                """UPDATE source_artifact
                      SET body_retention_status='purged',
                          body_retention_reason='orphan_ingestion_cleanup',
                          body_purged_at=?
                    WHERE id=? AND body_retention_status='present'""",
                (purged_at, artifact_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ArtifactBundleConflictError(
                "The source artifact failed the canonical orphan cleanup gate."
            ) from exc
        if updated.rowcount != 1:
            raise ArtifactBundleConflictError(
                "The source artifact cleanup lost its state fence."
            )
        remaining = connection.execute(
            """SELECT
                 (SELECT count(*) FROM extraction_snapshot_body
                   WHERE artifact_id=?)
                 +(SELECT count(*) FROM artifact_blob_body bb
                    JOIN artifact_blob b ON b.id=bb.artifact_blob_id
                   WHERE b.artifact_id=?)""",
            (artifact_id, artifact_id),
        ).fetchone()
        if remaining is None or int(remaining[0]) != 0:
            raise ArtifactBundleIntegrityError(
                "The source artifact body links survived cleanup."
            )
        return OrphanBodyCleanupResult(
            artifact_id,
            True,
            int(artifact_delete),
            int(extraction_delete),
        )

    def put_legacy_bundle(
        self,
        connection: ManagedConnection,
        bundle: LegacyArtifactBundle,
    ) -> LegacyArtifactBundleResult:
        connection = _connection(connection, write=True)
        bundle = _prepare_bundle(bundle)
        owner = connection.execute(
            "SELECT 1 FROM app_user WHERE id=?", (bundle.owner_user_id,)
        ).fetchone()
        if owner is None:
            raise ArtifactBundleConflictError(
                "The legacy artifact owner does not exist."
            )

        blob_insert = self._preflight_blob(connection, bundle.raw_blob)
        if (
            bundle.extraction is not None
            and bundle.extraction.body.content_blob_hash
            != bundle.raw_blob.sha256
        ):
            self._require_body_blob(connection, bundle.extraction.body)
        action = self._preflight_artifact_rows(connection, bundle)
        if action == "replay":
            return LegacyArtifactBundleResult(bundle.ids, False)

        blob = bundle.raw_blob
        try:
            if blob_insert:
                connection.execute(
                    """INSERT INTO blob_object(
                         sha256,size_bytes,media_type,relative_path,
                         verification_status,created_at,verified_at
                       ) VALUES (?,?,?,?,'verified',?,?)""",
                    (
                        blob.sha256,
                        blob.size_bytes,
                        CAS_BLOB_MEDIA_TYPE,
                        blob.relative_path,
                        blob.created_at_ms,
                        blob.verified_at_ms,
                    ),
                )
            connection.execute(
                """INSERT INTO source_artifact(
                     id,origin_type,origin_key,origin_revision_key,raw_sha256,
                     raw_size_bytes,media_type,original_filename,
                     body_retention_status,body_retention_reason,body_purged_at,
                     orphan_cleanup_eligible_at,publisher_user_id,
                     source_owner_snapshot_json,metadata_json,
                     metadata_schema_version,created_at
                   ) VALUES (?,'legacy',?,?,?,?,?,?,'present',NULL,NULL,?,?,?, ?,1,?)""",
                (
                    bundle.ids.artifact_id,
                    bundle.origin_key,
                    bundle.inventory_record_id,
                    blob.sha256,
                    blob.size_bytes,
                    blob.media_type,
                    bundle.original_filename,
                    bundle.created_at_ms + _ORPHAN_GRACE_MS,
                    bundle.owner_user_id,
                    bundle.source_owner_snapshot_json,
                    bundle.metadata_json,
                    bundle.created_at_ms,
                ),
            )
            connection.execute(
                """INSERT INTO artifact_blob(
                     id,artifact_id,content_sha256,size_bytes,role,logical_name,
                     media_type,ordinal,body_retention_status,created_at
                   ) VALUES (?,?,?,?,'raw',?,?,0,'present',?)""",
                (
                    bundle.ids.raw_manifest_id,
                    bundle.ids.artifact_id,
                    blob.sha256,
                    blob.size_bytes,
                    bundle.logical_name,
                    blob.media_type,
                    bundle.created_at_ms,
                ),
            )
            connection.execute(
                """INSERT INTO artifact_blob_body(
                     artifact_blob_id,content_sha256,created_at
                   ) VALUES (?,?,?)""",
                (
                    bundle.ids.raw_manifest_id,
                    blob.sha256,
                    bundle.created_at_ms,
                ),
            )
            if bundle.extraction is not None:
                spec = bundle.extraction
                connection.execute(
                    """INSERT INTO extraction_snapshot(
                         id,artifact_id,status,extracted_sha256,
                         normalized_content_sha256,extracted_size_bytes,
                         normalizer_version,extractor_name,extractor_version,
                         warnings_json,body_retention_status,created_at
                       ) VALUES (?,?,'ready',?,?,?,?,?,?,?,'present',?)""",
                    (
                        bundle.ids.extraction_snapshot_id,
                        bundle.ids.artifact_id,
                        blob.sha256,
                        spec.normalized_content_sha256,
                        blob.size_bytes,
                        spec.normalizer_version,
                        spec.extractor_name,
                        spec.extractor_version,
                        spec.warnings_json,
                        spec.created_at_ms,
                    ),
                )
                connection.execute(
                    """INSERT INTO extraction_snapshot_body(
                         snapshot_id,artifact_id,content_md,content_blob_hash,
                         content_size_bytes,content_sha256,created_at
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (
                        bundle.ids.extraction_snapshot_id,
                        bundle.ids.artifact_id,
                        spec.body.content_md,
                        spec.body.content_blob_hash,
                        spec.body.content_size_bytes,
                        spec.body.content_sha256,
                        spec.created_at_ms,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ArtifactBundleConflictError(
                "The legacy artifact bundle violates canonical constraints."
            ) from exc
        return LegacyArtifactBundleResult(bundle.ids, True)

    def put_embedded_asset_link(
        self,
        connection: ManagedConnection,
        link: LegacyEmbeddedAssetLink,
    ) -> LegacyEmbeddedAssetLinkResult:
        connection = _connection(connection, write=True)
        if not isinstance(link, LegacyEmbeddedAssetLink):
            raise TypeError("A LegacyEmbeddedAssetLink is required.")
        plan_sha = _sha(link.plan_sha256, field="plan_sha256")
        relationship_id = _relationship_id(link.relationship_id)
        expected_id = legacy_embedded_manifest_id_for_relationship(
            plan_sha, relationship_id
        )
        if _uuid(link.manifest_id, field="manifest_id") != expected_id:
            raise ValueError("The embedded manifest ID is not deterministic.")
        source_artifact_id = _uuid(
            link.source_artifact_id, field="source_artifact_id"
        )
        target_asset_artifact_id = _uuid(
            link.target_asset_artifact_id, field="target_asset_artifact_id"
        )
        target_asset_raw_manifest_id = _uuid(
            link.target_asset_raw_manifest_id,
            field="target_asset_raw_manifest_id",
        )
        blob = _prepare_blob(link.target_blob)
        logical_name = _text(link.logical_name, field="logical_name", maximum=500)
        logical_name_sha = _sha(
            link.logical_name_sha256, field="logical_name_sha256"
        )
        if (
            isinstance(link.ordinal, bool)
            or not isinstance(link.ordinal, int)
            or not 0 <= link.ordinal <= _SQLITE_INTEGER_MAX
        ):
            raise ValueError("ordinal must be a non-negative integer.")
        created_at = _utc_ms(link.created_at_ms, field="created_at_ms")

        # The upstream relationship adapter binds logical_name_sha256 to the
        # exact reviewed relationship.  Hashing the logical name itself remains
        # outside this repository/transaction.
        source = connection.execute(
            """SELECT artifact.publisher_user_id
               FROM source_artifact AS artifact
               WHERE artifact.id=? AND artifact.origin_type='legacy'
                 AND artifact.media_type='text/markdown'
                 AND artifact.body_retention_status='present'
                 AND EXISTS (
                   SELECT 1 FROM extraction_snapshot AS snapshot
                   JOIN extraction_snapshot_body AS body
                     ON body.snapshot_id=snapshot.id
                    AND body.artifact_id=snapshot.artifact_id
                   WHERE snapshot.artifact_id=artifact.id
                     AND snapshot.status='ready'
                     AND snapshot.body_retention_status='present'
                 )""",
            (source_artifact_id,),
        ).fetchone()
        if source is None:
            raise ArtifactBundleConflictError(
                "The embedded asset source artifact is unavailable."
            )
        if self._preflight_blob(connection, blob):
            raise ArtifactBundleConflictError(
                "The embedded asset target blob is not canonical yet."
            )
        target = connection.execute(
            """SELECT artifact.publisher_user_id,artifact.raw_sha256,
                      artifact.raw_size_bytes,artifact.media_type,
                      artifact.body_retention_status,
                      manifest.artifact_id,manifest.content_sha256,
                      manifest.size_bytes,manifest.media_type AS manifest_media_type,
                      manifest.role,manifest.ordinal,
                      manifest.body_retention_status AS manifest_retention,
                      body.content_sha256 AS body_sha256
               FROM source_artifact AS artifact
               JOIN artifact_blob AS manifest
                 ON manifest.id=? AND manifest.artifact_id=artifact.id
               JOIN artifact_blob_body AS body
                 ON body.artifact_blob_id=manifest.id
               WHERE artifact.id=? AND artifact.origin_type='legacy'""",
            (target_asset_raw_manifest_id, target_asset_artifact_id),
        ).fetchone()
        if (
            target is None
            or target["publisher_user_id"] != source["publisher_user_id"]  # type: ignore[index]
            or target["raw_sha256"] != blob.sha256  # type: ignore[index]
            or target["raw_size_bytes"] != blob.size_bytes  # type: ignore[index]
            or target["media_type"] != blob.media_type  # type: ignore[index]
            or target["body_retention_status"] != "present"  # type: ignore[index]
            or target["artifact_id"] != target_asset_artifact_id  # type: ignore[index]
            or target["content_sha256"] != blob.sha256  # type: ignore[index]
            or target["size_bytes"] != blob.size_bytes  # type: ignore[index]
            or target["manifest_media_type"] != blob.media_type  # type: ignore[index]
            or target["role"] != "raw"  # type: ignore[index]
            or target["ordinal"] != 0  # type: ignore[index]
            or target["manifest_retention"] != "present"  # type: ignore[index]
            or target["body_sha256"] != blob.sha256  # type: ignore[index]
        ):
            raise ArtifactBundleConflictError(
                "The embedded asset target artifact is not exact."
            )
        manifest = connection.execute(
            """SELECT id,artifact_id,content_sha256,size_bytes,role,logical_name,
                      media_type,ordinal,body_retention_status,created_at
               FROM artifact_blob WHERE id=?""",
            (expected_id,),
        ).fetchone()
        identity = connection.execute(
            """SELECT id FROM artifact_blob
               WHERE artifact_id=? AND role='embedded_asset' AND ordinal=?""",
            (source_artifact_id, link.ordinal),
        ).fetchone()
        body = connection.execute(
            """SELECT artifact_blob_id,content_sha256,created_at
               FROM artifact_blob_body WHERE artifact_blob_id=?""",
            (expected_id,),
        ).fetchone()
        if manifest is None and body is None:
            if identity is not None:
                raise ArtifactBundleConflictError(
                    "The embedded asset ordinal is already bound incompatibly."
                )
            try:
                connection.execute(
                    """INSERT INTO artifact_blob(
                         id,artifact_id,content_sha256,size_bytes,role,logical_name,
                         media_type,ordinal,body_retention_status,created_at
                       ) VALUES (?,?,?,?,'embedded_asset',?,?,?,'present',?)""",
                    (
                        expected_id,
                        source_artifact_id,
                        blob.sha256,
                        blob.size_bytes,
                        logical_name,
                        blob.media_type,
                        link.ordinal,
                        created_at,
                    ),
                )
                connection.execute(
                    """INSERT INTO artifact_blob_body(
                         artifact_blob_id,content_sha256,created_at
                       ) VALUES (?,?,?)""",
                    (expected_id, blob.sha256, created_at),
                )
            except sqlite3.IntegrityError as exc:
                raise ArtifactBundleConflictError(
                    "The embedded asset link violates canonical constraints."
                ) from exc
            return LegacyEmbeddedAssetLinkResult(expected_id, True)
        if manifest is None or body is None or identity is None:
            raise ArtifactBundleConflictError(
                "The embedded asset link is only partially present."
            )
        if identity["id"] != expected_id or _row_tuple(  # type: ignore[index]
            manifest,
            (
                "id",
                "artifact_id",
                "content_sha256",
                "size_bytes",
                "role",
                "logical_name",
                "media_type",
                "ordinal",
                "body_retention_status",
                "created_at",
            ),
        ) != (
            expected_id,
            source_artifact_id,
            blob.sha256,
            blob.size_bytes,
            "embedded_asset",
            logical_name,
            blob.media_type,
            link.ordinal,
            "present",
            created_at,
        ) or _row_tuple(
            body, ("artifact_blob_id", "content_sha256", "created_at")
        ) != (expected_id, blob.sha256, created_at):
            raise ArtifactBundleConflictError(
                "The embedded asset link is already bound incompatibly."
            )
        del logical_name_sha  # reviewed evidence is persisted by the relationship map
        return LegacyEmbeddedAssetLinkResult(expected_id, False)


__all__ = [
    "ArtifactExtractionSpec",
    "ArtifactIds",
    "ArtifactBundleConflictError",
    "ArtifactBundleIntegrityError",
    "ArtifactRepository",
    "CAS_BLOB_MEDIA_TYPE",
    "CanonicalEmbeddedAssetSpec",
    "ArtifactRepositoryError",
    "LegacyArtifactBundle",
    "LegacyArtifactBundleResult",
    "LegacyArtifactIds",
    "LegacyEmbeddedAssetLink",
    "LegacyEmbeddedAssetLinkResult",
    "LegacyExtractionSpec",
    "OrphanBodyCleanupResult",
    "PreparedMarkdownBody",
    "SourceArtifactBundle",
    "SourceArtifactBundleResult",
    "SourceExtractionResult",
    "StagedVerifiedBlob",
    "legacy_artifact_ids_for_record",
    "legacy_embedded_manifest_id_for_relationship",
]

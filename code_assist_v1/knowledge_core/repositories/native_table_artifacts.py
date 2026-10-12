"""Opt-in native-table extraction bundle; no route/handler activation.

The worker must obtain the authorized retained original before preparation.
OOXML parsing occurs outside its write transaction. The process-local seal binds
that preparation to all fields consumed here; the repository then rechecks the
raw identity against canonical storage. The seal is not user authorization and
is not a durable receipt. Restarted workers prepare again from retained bytes.

As with ArtifactRepository, all unexpected failures must escape the caller's
transaction. This repository never owns a transaction or performs filesystem I/O.
"""

from dataclasses import asdict, dataclass, replace
import hashlib
import hmac
import json
import secrets
import sqlite3

from ...document_source_tables import (
    NativeTable, NativeTableDocument, extract_native_table_document,
)
from .artifacts import (
    ArtifactBundleConflictError, ArtifactBundleIntegrityError,
    ArtifactExtractionSpec, ArtifactRepository, CanonicalEmbeddedAssetSpec,
    SourceExtractionResult, OrphanBodyCleanupResult, _connection, _prepare_embedded_assets,
    _prepare_extraction, _uuid,
)


NATIVE_EXTRACTOR_VERSION = "rich-source-v4-native-tables"
NATIVE_RENDERER_VERSION = "native-source-table-html-v1"
_PREPARATION_KEY = secrets.token_bytes(32)
_DESCRIPTOR_FIELDS = (
    "snapshot_id", "artifact_id", "structure_version", "renderer_version",
    "source_format", "raw_source_sha256", "raw_size_bytes",
    "extracted_markdown_sha256", "structure_sha256", "structure_size_bytes",
    "table_count", "coverage", "created_at",
)
_BODY_FIELDS = (
    "snapshot_id", "artifact_id", "structure_json", "structure_sha256",
    "structure_size_bytes", "created_at",
)


@dataclass(frozen=True, slots=True)
class PreparedNativeTableExtraction:
    extraction: ArtifactExtractionSpec
    embedded_assets: tuple[CanonicalEmbeddedAssetSpec, ...]
    raw_sha256: str
    raw_size_bytes: int
    structure_json: str
    structure_sha256: str
    structure_size_bytes: int
    structure_version: str
    source_format: str
    coverage: str
    table_count: int  # Includes nested tables, not just standalone render roots.
    renderer_version: str
    seal: str


@dataclass(frozen=True, slots=True)
class NativeOrphanBodyCleanupResult:
    legacy: OrphanBodyCleanupResult
    deleted_native_body_count: int
    deleted_native_body_bytes: int


def _seal(prepared: PreparedNativeTableExtraction) -> str:
    value = asdict(prepared)
    value.pop("seal")
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":")).encode("utf-8")
    return hmac.new(_PREPARATION_KEY, b"native-table-extraction-v1\0" + payload,
        hashlib.sha256).hexdigest()


def _count_tables(document: NativeTableDocument) -> int:
    count, pending = 0, list(document.blocks)
    while pending:
        block = pending.pop()
        if isinstance(block, NativeTable):
            count += 1
            pending.extend(child for cell in block.cells for child in cell.blocks)
    return count


def prepare_native_table_extraction(
    *, filename: str, raw_content: bytes, extraction: ArtifactExtractionSpec,
    embedded_assets: tuple[CanonicalEmbeddedAssetSpec, ...] = (),
) -> PreparedNativeTableExtraction:
    """Reconstruct from exact original bytes, never a client/model sidecar.

This is the CPU-only preparation boundary. The caller must run it outside a
write transaction after authorized CAS retrieval; no raw path is accepted.
"""
    if type(raw_content) is not bytes:
        raise TypeError("Native preparation requires retained original bytes.")
    spec = _prepare_extraction(extraction, artifact_created_at_ms=0)
    if spec.status != "ready" or spec.extractor_version != NATIVE_EXTRACTOR_VERSION:
        raise ValueError("Native structures require a ready successor extraction.")
    assets = _prepare_embedded_assets(embedded_assets,
        extraction_created_at_ms=spec.created_at_ms)
    document = extract_native_table_document(filename, raw_content)
    canonical = document.canonical_json()
    encoded = canonical.encode("utf-8")
    prepared = PreparedNativeTableExtraction(
        spec, assets, document.source_sha256, len(raw_content), canonical,
        hashlib.sha256(encoded).hexdigest(), len(encoded), document.version,
        document.format, document.coverage, _count_tables(document),
        NATIVE_RENDERER_VERSION, "",
    )
    return replace(prepared, seal=_seal(prepared))


def _verify_prepared(prepared: PreparedNativeTableExtraction) -> None:
    if type(prepared) is not PreparedNativeTableExtraction:
        raise TypeError("A sealed native extraction preparation is required.")
    try:
        valid = type(prepared.seal) is str and hmac.compare_digest(prepared.seal, _seal(prepared))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        valid = False
    if not valid:
        raise ArtifactBundleIntegrityError("Native preparation changed or belongs to another worker process.")


def _tuple(row, fields):
    return None if row is None else tuple(row[field] for field in fields)


class NativeTableArtifactRepository:
    def cleanup_orphan_bodies(self, connection, *, artifact_id: str, purged_at_ms: int) -> NativeOrphanBodyCleanupResult:
        """Versioned accounting; do not reinterpret old extraction-body counts.

        The legacy eligibility/authorization boundary still owns the effect.
        Native rows are counted before its trigger deletes them, then checked
        absent. This method does not replace the permanent-purge preview API.
        """
        connection = _connection(connection, write=True)
        artifact_id = _uuid(artifact_id, field="artifact_id")
        invalid = connection.execute(
            """SELECT 1 FROM extraction_snapshot x
                LEFT JOIN extraction_native_table_structure d ON d.snapshot_id=x.id AND d.artifact_id=x.artifact_id
                LEFT JOIN extraction_native_table_structure_body b ON b.snapshot_id=x.id AND b.artifact_id=x.artifact_id
                WHERE x.artifact_id=? AND x.extractor_version=? AND x.status='ready' AND (
                    d.snapshot_id IS NULL
                    OR (x.body_retention_status='present' AND b.snapshot_id IS NULL)
                    OR (x.body_retention_status<>'present' AND b.snapshot_id IS NOT NULL)) LIMIT 1""",
            (artifact_id, NATIVE_EXTRACTOR_VERSION),
        ).fetchone()
        if invalid is not None:
            raise ArtifactBundleIntegrityError("Native orphan retention state is incomplete.")
        count, size = connection.execute(
            "SELECT count(*),coalesce(sum(structure_size_bytes),0) FROM extraction_native_table_structure_body WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        result = ArtifactRepository().cleanup_orphan_bodies(connection,
            artifact_id=artifact_id, purged_at_ms=purged_at_ms)
        remaining = connection.execute(
            "SELECT count(*) FROM extraction_native_table_structure_body WHERE artifact_id=?", (artifact_id,),
        ).fetchone()[0]
        if remaining or (not result.changed and count):
            raise ArtifactBundleIntegrityError("Native bodies survived orphan cleanup.")
        return NativeOrphanBodyCleanupResult(result, count, size)

    def put_source_extraction(
        self, connection, *, artifact_id: str, extraction_snapshot_id: str,
        prepared: PreparedNativeTableExtraction,
    ) -> SourceExtractionResult:
        connection = _connection(connection, write=True)
        artifact_id = _uuid(artifact_id, field="artifact_id")
        snapshot_id = _uuid(extraction_snapshot_id, field="extraction_snapshot_id")
        _verify_prepared(prepared)
        artifact = connection.execute(
            "SELECT raw_sha256,raw_size_bytes,body_retention_status,original_filename FROM source_artifact WHERE id=?",
            (artifact_id,),
        ).fetchone()
        if artifact is None or tuple(artifact)[:3] != (
            prepared.raw_sha256, prepared.raw_size_bytes, "present",
        ) or not isinstance(artifact["original_filename"], str) or not artifact["original_filename"].lower().endswith("." + prepared.source_format):
            raise ArtifactBundleConflictError("Native preparation does not match the retained raw artifact.")

        expected = (
            snapshot_id, artifact_id, prepared.structure_version, prepared.renderer_version,
            prepared.source_format, prepared.raw_sha256, prepared.raw_size_bytes,
            prepared.extraction.extracted_sha256, prepared.structure_sha256,
            prepared.structure_size_bytes, prepared.table_count, prepared.coverage,
            prepared.extraction.created_at_ms,
        )
        expected_body = (
            snapshot_id, artifact_id, prepared.structure_json, prepared.structure_sha256,
            prepared.structure_size_bytes, prepared.extraction.created_at_ms,
        )
        descriptor = connection.execute(
            "SELECT * FROM extraction_native_table_structure WHERE snapshot_id=?", (snapshot_id,),
        ).fetchone()
        body = connection.execute(
            "SELECT * FROM extraction_native_table_structure_body WHERE snapshot_id=?", (snapshot_id,),
        ).fetchone()
        snapshot = connection.execute("SELECT id FROM extraction_snapshot WHERE id=?", (snapshot_id,)).fetchone()
        if snapshot is None:
            if descriptor is not None or body is not None:
                raise ArtifactBundleConflictError("Native extraction bundle is partially present.")
        elif _tuple(descriptor, _DESCRIPTOR_FIELDS) != expected or _tuple(body, _BODY_FIELDS) != expected_body:
            raise ArtifactBundleConflictError("Native extraction replay is incomplete or different.")

        # All sidecar conflict checks precede legacy writes. Legacy preflight
        # independently checks source/body/asset identities before its first write.
        result = ArtifactRepository().put_source_extraction(
            connection, artifact_id=artifact_id, extraction_snapshot_id=snapshot_id,
            extraction=prepared.extraction, embedded_assets=prepared.embedded_assets,
        )
        if not result.inserted:
            return result
        try:
            connection.execute(
                "INSERT INTO extraction_native_table_structure(" + ",".join(_DESCRIPTOR_FIELDS)
                + ") VALUES (" + ",".join("?" for _ in _DESCRIPTOR_FIELDS) + ")", expected,
            )
            connection.execute(
                "INSERT INTO extraction_native_table_structure_body(" + ",".join(_BODY_FIELDS)
                + ") VALUES (" + ",".join("?" for _ in _BODY_FIELDS) + ")", expected_body,
            )
        except sqlite3.IntegrityError as exc:
            raise ArtifactBundleConflictError("Native extraction violates canonical storage constraints.") from exc
        return result


__all__ = [
    "NATIVE_EXTRACTOR_VERSION", "NATIVE_RENDERER_VERSION",
    "NativeTableArtifactRepository", "PreparedNativeTableExtraction",
    "NativeOrphanBodyCleanupResult",
    "prepare_native_table_extraction",
]

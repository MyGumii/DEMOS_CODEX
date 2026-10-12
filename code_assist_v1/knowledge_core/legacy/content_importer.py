"""Bounded P2.3 personal-content and asset import composition.

The public boundary accepts the exact reviewed-plan bytes plus the exact source
bytes selected for one bounded batch.  A trusted blob stager performs physical
CAS writes and observations before SQLite is opened.  The importer then owns
one transaction that composes artifact, item, durable mapping, relationship,
audit, and outbox repositories.

This module deliberately does not discover source paths or read the frozen
snapshot by pathname.  The offline apply adapter owns that root-bound read and
passes bytes here; this service proves that those bytes match the reviewed
record before any canonical write.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import mimetypes
import ntpath
import os
from pathlib import PurePosixPath
import re
from typing import Protocol
from uuid import UUID, uuid5

from ..db import ConnectionFactory, ManagedConnection, transaction
from ..errors import KnowledgeCoreError
from ..repositories.artifacts import (
    ArtifactRepository,
    LegacyArtifactBundle,
    LegacyEmbeddedAssetLink,
    LegacyExtractionSpec,
    PreparedMarkdownBody,
    StagedVerifiedBlob,
    legacy_artifact_ids_for_record,
    legacy_embedded_manifest_id_for_relationship,
)
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.items import (
    ItemRepository,
    LegacyMarkdownImportBundle,
    legacy_item_ids_for_record,
)
from ..repositories.migration import (
    LegacyMigrationDetails,
    LegacyMigrationKey,
    LegacyMigrationRecord,
    LegacyMigrationRelationshipDetails,
    LegacyMigrationRelationshipRecord,
    LegacyMigrationRelationshipRepository,
    LegacyMigrationRelationshipTarget,
    LegacyMigrationRepository,
    LegacyMigrationTarget,
    VerifiedBlobEvidence,
    migration_mapping_id_for_record,
    migration_relationship_mapping_id_for_plan,
    migration_run_id_for_plan,
    migration_source_locator,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from .importer import _derived_uuid, _mapping, _record_source, _reviewed_plan, _utc_ms
from .plan_contract import sha256_canonical


MAX_CONTENT_EFFECTS_PER_TRANSACTION = 100
MAX_CONTENT_SOURCE_BYTES = 256 * 1024 * 1024
MAX_INLINE_MARKDOWN_BYTES = 256 * 1024
MARKDOWN_MEDIA_TYPE = "text/markdown"
MARKDOWN_NORMALIZER_VERSION = "markdown_lf_v1"

_NATIVE_MARKDOWN_CATEGORIES = frozenset(
    {
        "knowledge.legacy_flat_markdown",
        "knowledge.wiki_hidden_pages",
        "knowledge.wiki_sources",
    }
)
_LEGACY_ADAPTER_SUFFIXES = {
    "knowledge.legacy_thoughts": frozenset({".md", ".markdown"}),
    "knowledge.legacy_inbox": frozenset({".json"}),
    "knowledge.legacy_raw": frozenset(
        {".adoc", ".markdown", ".md", ".rst", ".text", ".txt"}
    ),
    "knowledge.legacy_raw_chunks": frozenset(
        {
            ".adoc",
            ".json",
            ".jsonl",
            ".markdown",
            ".md",
            ".rst",
            ".text",
            ".txt",
        }
    ),
}
_TEXT_MEDIA_TYPES = {
    ".adoc": "text/asciidoc",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".markdown": MARKDOWN_MEDIA_TYPE,
    ".md": MARKDOWN_MEDIA_TYPE,
    ".rst": "text/x-rst",
    ".text": "text/plain",
    ".txt": "text/plain",
}

_RECORD_RE = re.compile(r"^rec_[0-9a-f]{24}$", re.ASCII)
_RELATIONSHIP_RE = re.compile(r"^rel_[0-9a-f]{24}$", re.ASCII)
_REQUEST_NAMESPACE = UUID("a428cf23-5a44-487c-8382-5d3231c123e8")
_OPERATION_NAMESPACE = UUID("cad6671f-f39b-4f6d-81d3-0f5b20251716")
_AUDIT_NAMESPACE = UUID("5c83f0c2-8880-45bd-97ca-669c6486cc6f")
_RELATIONSHIP_AUDIT_NAMESPACE = UUID("f84af105-8f9b-4b0f-9155-6f4bc09a0f7c")


class LegacyContentImportError(KnowledgeCoreError):
    code = "LEGACY_CONTENT_IMPORT_ERROR"


class LegacyContentPreparationError(LegacyContentImportError):
    code = "LEGACY_CONTENT_PREPARATION_INVALID"


class LegacyContentBatchLimitError(LegacyContentPreparationError):
    code = "LEGACY_CONTENT_BATCH_LIMIT"


class LegacyContentStateConflictError(LegacyContentImportError):
    code = "LEGACY_CONTENT_STATE_CONFLICT"


class LegacyBlobLease(Protocol):
    """A retained read handle that blocks blob write/delete races."""

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class StagedBlobObservation:
    """Trusted physical result returned by a configured blob stager."""

    blob: StagedVerifiedBlob
    evidence: VerifiedBlobEvidence
    lease: LegacyBlobLease


class LegacyBlobStager(Protocol):
    """Stage and stream-verify one immutable blob outside SQLite."""

    @property
    def resolved_root(self) -> str: ...

    def stage_and_verify(
        self,
        content: bytes,
        *,
        expected_sha256: str,
        media_type: str,
        effective_at_ms: int,
        normalized_content_sha256: str | None,
        normalizer_version: str | None,
    ) -> StagedBlobObservation: ...


@dataclass(frozen=True, slots=True)
class _PreparedContentRecord:
    plan_row: Mapping[str, object]
    source: Mapping[str, object]
    raw: bytes
    observation: StagedBlobObservation
    artifact_bundle: LegacyArtifactBundle
    item_bundle: LegacyMarkdownImportBundle | None
    mapping_id: str
    migration_key: LegacyMigrationKey
    owner_user_id: str
    expected_system_role: str
    expected_account_status: str
    expected_part_id: str | None
    target_space_id: str
    path_key: str

    @property
    def record_id(self) -> str:
        return str(self.plan_row["record_id"])

    @property
    def import_mode(self) -> str:
        return str(self.plan_row["import_mode"])


@dataclass(frozen=True, slots=True)
class _PreparedAssetRelationship:
    plan_row: Mapping[str, object]
    source_relationship: Mapping[str, object]
    source: _PreparedContentRecord
    target: _PreparedContentRecord
    ordinal: int
    logical_name: str
    logical_name_sha256: str
    embedded_manifest_id: str

    @property
    def relationship_id(self) -> str:
        return str(self.plan_row["relationship_id"])


@dataclass(frozen=True, slots=True)
class _PreparedContentBatch:
    plan_sha256: str
    migration_run_id: str
    request_id: str
    operation_id: str
    effective_at_ms: int
    inventory_manifest_sha256: str
    root_binding_sha256: str
    reviewer_id: str
    records: tuple[_PreparedContentRecord, ...]
    relationships: tuple[_PreparedAssetRelationship, ...]


@dataclass(frozen=True, slots=True)
class LegacyContentRecordOutcome:
    record_id: str
    mapping_id: str
    import_mode: str
    result_disposition: str
    artifact_id: str
    item_id: str | None
    revision_id: str | None
    mapping_inserted: bool


@dataclass(frozen=True, slots=True)
class LegacyContentRelationshipOutcome:
    relationship_id: str
    relationship_mapping_id: str
    embedded_manifest_id: str
    mapping_inserted: bool


@dataclass(frozen=True, slots=True)
class LegacyContentImportResult:
    plan_sha256: str
    migration_run_id: str
    request_id: str
    operation_id: str
    records: tuple[LegacyContentRecordOutcome, ...]
    relationships: tuple[LegacyContentRelationshipOutcome, ...]

    @property
    def inserted_mapping_count(self) -> int:
        return sum(value.mapping_inserted for value in self.records) + sum(
            value.mapping_inserted for value in self.relationships
        )


def legacy_content_request_id_for_plan(plan_sha256: str) -> str:
    return _derived_uuid(_REQUEST_NAMESPACE, plan_sha256)


def legacy_content_operation_id_for_plan(plan_sha256: str) -> str:
    return _derived_uuid(_OPERATION_NAMESPACE, plan_sha256)


def _canonical_object(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError):
        raise LegacyContentPreparationError(
            "Content metadata cannot be represented canonically."
        ) from None


def _record_id(value: object) -> str:
    if not isinstance(value, str) or _RECORD_RE.fullmatch(value) is None:
        raise LegacyContentPreparationError("A selected inventory record ID is invalid.")
    return value


def _relationship_id(value: object) -> str:
    if not isinstance(value, str) or _RELATIONSHIP_RE.fullmatch(value) is None:
        raise LegacyContentPreparationError(
            "A selected inventory relationship ID is invalid."
        )
    return value


def _selected_ids(
    values: Sequence[str], *, relationship: bool
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("Selected IDs must be a sequence.")
    validator = _relationship_id if relationship else _record_id
    result = tuple(validator(value) for value in values)
    if len(result) != len(set(result)) or result != tuple(sorted(result)):
        raise LegacyContentPreparationError(
            "Selected IDs must be unique and sorted canonically."
        )
    return result


def _source_bytes(
    payloads: Mapping[str, bytes], required_ids: set[str]
) -> dict[str, bytes]:
    if not isinstance(payloads, Mapping):
        raise TypeError("source_payloads must be a mapping.")
    if set(payloads) != required_ids:
        raise LegacyContentPreparationError(
            "Source payloads must cover exactly the selected records and dependencies."
        )
    result: dict[str, bytes] = {}
    total = 0
    for record_id in sorted(required_ids):
        _record_id(record_id)
        raw = payloads[record_id]
        if not isinstance(raw, bytes):
            raise TypeError("Every source payload must be bytes.")
        total += len(raw)
        if total > MAX_CONTENT_SOURCE_BYTES:
            raise LegacyContentBatchLimitError(
                "A content batch exceeds the source-byte limit."
            )
        result[record_id] = raw
    return result


def _assert_blob_root_outside_sources(
    stager: LegacyBlobStager, draft: Mapping[str, object]
) -> None:
    root = getattr(stager, "resolved_root", None)
    snapshot = draft.get("source_snapshot")
    roots = snapshot.get("roots") if isinstance(snapshot, Mapping) else None
    if not isinstance(root, str) or not os.path.isabs(root) or not isinstance(roots, list):
        raise LegacyContentPreparationError(
            "The blob stager does not expose a valid bound storage root."
        )
    target = ntpath.normcase(ntpath.normpath(root.replace("/", "\\")))
    for row in roots:
        source = row.get("resolved_path") if isinstance(row, Mapping) else None
        if not isinstance(source, str):
            raise LegacyContentPreparationError(
                "The reviewed source-root binding is invalid."
            )
        source = ntpath.normcase(ntpath.normpath(source.replace("/", "\\")))
        try:
            common = ntpath.commonpath((source, target))
        except ValueError:
            continue
        if common in {source, target}:
            raise LegacyContentPreparationError(
                "The canonical blob root overlaps a reviewed source root."
            )


def _markdown_normalized(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _logical_name(source: Mapping[str, object]) -> str:
    relative = source.get("relative_path")
    if not isinstance(relative, str) or not relative:
        raise LegacyContentPreparationError("Reviewed source path is invalid.")
    name = PurePosixPath(relative).name
    if not name or name in {".", ".."} or "\x00" in name:
        raise LegacyContentPreparationError("Reviewed source filename is invalid.")
    return name


def _title(source: Mapping[str, object]) -> str:
    name = _logical_name(source)
    stem = PurePosixPath(name).stem.strip()
    return stem or name


def _personal_item_adapter(source: Mapping[str, object]) -> tuple[str, str]:
    category = source.get("category")
    relative = source.get("relative_path")
    if not isinstance(category, str) or not isinstance(relative, str):
        raise LegacyContentPreparationError(
            "Reviewed personal-content adapter facts are invalid."
        )
    suffix = PurePosixPath(relative).suffix.casefold()
    if category in _NATIVE_MARKDOWN_CATEGORIES:
        if suffix != ".md":
            raise LegacyContentPreparationError(
                "Reviewed native Markdown has an unsupported extension."
            )
        return MARKDOWN_MEDIA_TYPE, "legacy-markdown"
    allowed = _LEGACY_ADAPTER_SUFFIXES.get(category)
    if allowed is None or suffix not in allowed:
        raise LegacyContentPreparationError(
            "Reviewed legacy personal content has no frozen adapter."
        )
    parse = source.get("parse")
    if suffix in {".json", ".jsonl"} and (
        not isinstance(parse, Mapping) or parse.get("status") != "valid"
    ):
        raise LegacyContentPreparationError(
            "Reviewed structured legacy content lacks valid parse evidence."
        )
    adapter = "legacy-json-text" if suffix in {".json", ".jsonl"} else "legacy-text"
    return _TEXT_MEDIA_TYPES[suffix], adapter


def _media_type(source: Mapping[str, object], mode: str) -> str:
    if mode == "personal_item":
        return _personal_item_adapter(source)[0]
    relative = source.get("relative_path")
    guessed, _ = mimetypes.guess_type(str(relative), strict=False)
    return guessed or "application/octet-stream"


def _validate_observation(
    value: object,
    *,
    expected_sha256: str,
    expected_size: int,
    media_type: str,
    normalized_sha256: str | None,
    normalizer_version: str | None,
) -> StagedBlobObservation:
    if not isinstance(value, StagedBlobObservation):
        raise LegacyContentPreparationError(
            "The configured blob stager returned invalid evidence."
        )
    blob = value.blob
    evidence = value.evidence
    expected_path = (
        f"{expected_sha256[:2]}/{expected_sha256[2:4]}/{expected_sha256}"
    )
    if (
        not isinstance(blob, StagedVerifiedBlob)
        or not isinstance(evidence, VerifiedBlobEvidence)
        or blob.sha256 != expected_sha256
        or blob.size_bytes != expected_size
        or blob.media_type != media_type
        or blob.relative_path != expected_path
        or evidence.raw_sha256 != expected_sha256
        or evidence.size_bytes != expected_size
        or evidence.media_type != media_type
        or evidence.normalized_content_sha256 != normalized_sha256
        or evidence.normalizer_version != normalizer_version
        or not callable(getattr(value.lease, "close", None))
    ):
        raise LegacyContentPreparationError(
            "Physical blob evidence does not match the reviewed source bytes."
        )
    return value


def _prepare_record(
    *,
    row: Mapping[str, object],
    raw: bytes,
    stager: LegacyBlobStager,
    plan_sha256: str,
    effective_at_ms: int,
    owner_mapping: Mapping[str, object],
) -> _PreparedContentRecord:
    if (
        row.get("action") != "import"
        or row.get("execution_phase") != "P2.3"
        or row.get("import_mode") not in {"personal_item", "personal_asset"}
        or row.get("target_space_kind") != "personal"
        or not isinstance(row.get("resolved_owner_user_id"), str)
    ):
        raise LegacyContentPreparationError(
            "A selected record is not an imported P2.3 personal content record."
        )
    source = _record_source(row)
    if source.get("record_kind") != "physical_file":
        raise LegacyContentPreparationError(
            "P2.3 content batches require physical source records."
        )
    record_id = _record_id(row.get("record_id"))
    if (
        owner_mapping.get("mapping_id") != row.get("owner_mapping_id")
        or owner_mapping.get("decision") != "personal"
        or owner_mapping.get("user_id") != row.get("resolved_owner_user_id")
        or owner_mapping.get("system_role") not in {"admin", "user"}
        or owner_mapping.get("account_status") not in {"active", "disabled"}
    ):
        raise LegacyContentPreparationError(
            "A selected record lacks an exact reviewed owner mapping."
        )
    expected_sha = source.get("sha256")
    expected_size = source.get("size_bytes")
    if (
        not isinstance(expected_sha, str)
        or len(expected_sha) != 64
        or hashlib.sha256(raw).hexdigest() != expected_sha
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size != len(raw)
    ):
        raise LegacyContentPreparationError(
            "Source bytes do not match their reviewed inventory record."
        )
    if row.get("inventory_record_sha256") != sha256_canonical(source):
        raise LegacyContentPreparationError(
            "The reviewed inventory record binding changed."
        )
    mode = str(row["import_mode"])
    media_type = _media_type(source, mode)
    normalized_sha = None
    normalizer = None
    body = None
    extraction = None
    if mode == "personal_item":
        media_type, adapter_name = _personal_item_adapter(source)
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise LegacyContentPreparationError(
                "Reviewed personal Markdown is not valid UTF-8."
            ) from None
        normalized_sha = hashlib.sha256(_markdown_normalized(raw)).hexdigest()
        normalizer = MARKDOWN_NORMALIZER_VERSION
        body = PreparedMarkdownBody(
            content_md=decoded if len(raw) <= MAX_INLINE_MARKDOWN_BYTES else None,
            content_blob_hash=(expected_sha if len(raw) > MAX_INLINE_MARKDOWN_BYTES else None),
            content_size_bytes=len(raw),
            content_sha256=expected_sha,
        )
        extraction = LegacyExtractionSpec(
            normalized_content_sha256=normalized_sha,
            normalizer_version=normalizer,
            extractor_name=adapter_name,
            extractor_version="p2.3-v1",
            warnings_json="[]",
            body=body,
            created_at_ms=effective_at_ms,
        )
    observed = stager.stage_and_verify(
        raw,
        expected_sha256=expected_sha,
        media_type=media_type,
        effective_at_ms=effective_at_ms,
        normalized_content_sha256=normalized_sha,
        normalizer_version=normalizer,
    )
    observed = _validate_observation(
        observed,
        expected_sha256=expected_sha,
        expected_size=len(raw),
        media_type=media_type,
        normalized_sha256=normalized_sha,
        normalizer_version=normalizer,
    )
    try:
        root_id = source.get("root_id")
        path_key = source.get("path_key")
        if not isinstance(root_id, str) or not isinstance(path_key, str):
            raise LegacyContentPreparationError("Reviewed source identity is invalid.")
        origin_key = migration_source_locator(
            root_id=root_id,
            path_key=path_key,
            record_kind="physical_file",
            source_ordinal=0,
        )
        ids = legacy_artifact_ids_for_record(plan_sha256, record_id)
        owner = str(row["resolved_owner_user_id"])
        artifact = LegacyArtifactBundle(
        plan_sha256=plan_sha256,
        inventory_record_id=record_id,
        ids=ids,
        owner_user_id=owner,
        origin_key=origin_key,
        raw_blob=observed.blob,
        logical_name=_logical_name(source),
        original_filename=_logical_name(source),
        source_owner_snapshot_json=_canonical_object(
            {"owner": source.get("owner"), "schema_version": 1}
        ),
        metadata_json=_canonical_object(
            {
                "category": source.get("category"),
                "content_adapter": (
                    _personal_item_adapter(source)[1]
                    if mode == "personal_item"
                    else "legacy-asset"
                ),
                "inventory_record_id": record_id,
                "relative_path_sha256": hashlib.sha256(
                    str(source.get("relative_path")).encode("utf-8")
                ).hexdigest(),
                "schema_version": 1,
            }
        ),
        created_at_ms=effective_at_ms,
        extraction=extraction,
        )
        from ..services.spaces import space_id_for_owner

        target_space_id = space_id_for_owner("personal", owner)
        item = None
        if mode == "personal_item":
            assert extraction is not None and body is not None
            item = LegacyMarkdownImportBundle(
            plan_sha256=plan_sha256,
            inventory_record_id=record_id,
            ids=legacy_item_ids_for_record(plan_sha256, record_id),
            owner_user_id=owner,
            personal_space_id=target_space_id,
            artifact_id=ids.artifact_id,
            extraction_snapshot_id=ids.extraction_snapshot_id,
            title=_title(source),
            raw_content_sha256=expected_sha,
            normalized_content_sha256=normalized_sha,  # type: ignore[arg-type]
            content_size_bytes=len(raw),
            normalizer_version=normalizer,  # type: ignore[arg-type]
            metadata_json=_canonical_object(
                {"inventory_record_id": record_id, "migration": True}
            ),
            change_reason="reviewed P2.3 legacy personal-content import",
            body=body,
            created_at_ms=effective_at_ms,
            )
        return _PreparedContentRecord(
            plan_row=row,
            source=source,
            raw=raw,
            observation=observed,
            artifact_bundle=artifact,
            item_bundle=item,
            mapping_id=migration_mapping_id_for_record(plan_sha256, record_id),
            migration_key=LegacyMigrationKey(
                legacy_kind=str(source["category"]),
                normalized_path=origin_key,
                source_sha256=expected_sha,
            ),
            owner_user_id=owner,
            expected_system_role=str(owner_mapping["system_role"]),
            expected_account_status=str(owner_mapping["account_status"]),
            expected_part_id=(
                None
                if owner_mapping.get("part_id") is None
                else str(owner_mapping["part_id"])
            ),
            target_space_id=target_space_id,
            path_key=path_key,
        )
    except Exception:
        observed.lease.close()
        raise


def _prepare_batch(
    reviewed_plan_bytes: bytes,
    source_payloads: Mapping[str, bytes],
    *,
    selected_record_ids: Sequence[str],
    selected_relationship_ids: Sequence[str],
    expected_plan_sha256: str,
    stager: LegacyBlobStager,
) -> _PreparedContentBatch:
    if not isinstance(reviewed_plan_bytes, bytes):
        raise TypeError("reviewed_plan_bytes must be bytes.")
    plan = _reviewed_plan(
        reviewed_plan_bytes, expected_plan_sha256=expected_plan_sha256
    )
    record_ids = _selected_ids(selected_record_ids, relationship=False)
    relationship_ids = _selected_ids(selected_relationship_ids, relationship=True)
    if not record_ids and not relationship_ids:
        raise LegacyContentPreparationError("A content batch cannot be empty.")
    if len(record_ids) + len(relationship_ids) > MAX_CONTENT_EFFECTS_PER_TRANSACTION:
        raise LegacyContentBatchLimitError(
            "A content transaction is limited to "
            f"{MAX_CONTENT_EFFECTS_PER_TRANSACTION} effects."
        )
    plan_sha256 = str(plan["plan_sha256"])
    draft = _mapping(plan.get("draft"), field="plan draft")
    _assert_blob_root_outside_sources(stager, draft)
    rows_value = draft.get("records")
    relationships_value = draft.get("relationships")
    if not isinstance(rows_value, list) or not isinstance(relationships_value, list):
        raise LegacyContentPreparationError("Reviewed content ledgers are invalid.")
    rows = {
        str(row.get("record_id")): row
        for row in rows_value
        if isinstance(row, Mapping)
    }
    relationships = {
        str(row.get("relationship_id")): row
        for row in relationships_value
        if isinstance(row, Mapping)
    }
    owner_mappings_value = draft.get("owner_mappings")
    if not isinstance(owner_mappings_value, list):
        raise LegacyContentPreparationError("Reviewed owner mappings are invalid.")
    owner_mappings = {
        str(value.get("mapping_id")): value
        for value in owner_mappings_value
        if isinstance(value, Mapping)
    }
    try:
        selected_relationships = [relationships[value] for value in relationship_ids]
        for value in record_ids:
            rows[value]
    except KeyError:
        raise LegacyContentPreparationError(
            "A selected effect is absent from the reviewed plan."
        ) from None
    dependency_ids = set(record_ids)
    for row in selected_relationships:
        if (
            row.get("action") != "import"
            or row.get("execution_phase") != "P2.3"
            or row.get("import_mode") != "personal_asset"
        ):
            raise LegacyContentPreparationError(
                "A selected relationship is not an imported P2.3 asset reference."
            )
        source = row.get("source_relationship")
        if not isinstance(source, Mapping) or source.get("relationship_type") != "asset_reference":
            raise LegacyContentPreparationError(
                "A selected relationship is not a reviewed asset reference."
            )
        dependency_ids.add(_record_id(source.get("from_record_id")))
        dependency_ids.add(_record_id(source.get("to_record_id")))
    payloads = _source_bytes(source_payloads, dependency_ids)
    metadata = _mapping(draft.get("source_inventory"), field="source inventory")
    metadata = _mapping(metadata.get("metadata"), field="source metadata")
    run = _mapping(metadata.get("run"), field="inventory run")
    effective_at_ms = _utc_ms(run.get("started_at_utc"))
    prepared_by_id: dict[str, _PreparedContentRecord] = {}
    try:
        for record_id in sorted(dependency_ids):
            row = rows.get(record_id)
            if not isinstance(row, Mapping):
                raise LegacyContentPreparationError(
                    "A relationship endpoint is absent from the reviewed record ledger."
                )
            prepared_by_id[record_id] = _prepare_record(
                row=row,
                raw=payloads[record_id],
                stager=stager,
                plan_sha256=plan_sha256,
                effective_at_ms=effective_at_ms,
                owner_mapping=owner_mappings.get(str(row.get("owner_mapping_id")), {}),
            )
        prepared_relationships: list[_PreparedAssetRelationship] = []
        imported_asset_relationships = [
        row
        for row in relationships.values()
        if row.get("action") == "import"
        and row.get("execution_phase") == "P2.3"
        and row.get("import_mode") == "personal_asset"
        and isinstance(row.get("source_relationship"), Mapping)
        and row["source_relationship"].get("relationship_type") == "asset_reference"
    ]
        by_source: dict[str, list[str]] = {}
        for row in imported_asset_relationships:
            source = row["source_relationship"]
            by_source.setdefault(str(source.get("from_record_id")), []).append(
                str(row.get("relationship_id"))
            )
        for values in by_source.values():
            values.sort()
        for row in selected_relationships:
            source_relationship = row["source_relationship"]
            from_id = str(source_relationship["from_record_id"])
            to_id = str(source_relationship["to_record_id"])
            source = prepared_by_id[from_id]
            target = prepared_by_id[to_id]
            if source.import_mode != "personal_item" or target.import_mode != "personal_asset":
                raise LegacyContentPreparationError(
                    "An asset reference does not bind item and asset endpoint modes."
                )
            if (
                source.owner_user_id != target.owner_user_id
                or source.target_space_id != target.target_space_id
            ):
                raise LegacyContentPreparationError(
                    "An asset reference crosses a reviewed personal-space boundary."
                )
            relationship_id = str(row["relationship_id"])
            logical_name = _logical_name(target.source)
            prepared_relationships.append(
                _PreparedAssetRelationship(
                plan_row=row,
                source_relationship=source_relationship,
                source=source,
                target=target,
                ordinal=by_source[from_id].index(relationship_id) + 1,
                logical_name=logical_name,
                logical_name_sha256=hashlib.sha256(
                    logical_name.encode("utf-8")
                ).hexdigest(),
                embedded_manifest_id=legacy_embedded_manifest_id_for_relationship(
                    plan_sha256, relationship_id
                ),
                )
            )
        inputs = _mapping(draft.get("inputs"), field="plan inputs")
        inventory_binding = _mapping(inputs.get("inventory"), field="inventory binding")
        review = _mapping(plan.get("review"), field="review")
        return _PreparedContentBatch(
        plan_sha256=plan_sha256,
        migration_run_id=migration_run_id_for_plan(plan_sha256),
        request_id=legacy_content_request_id_for_plan(plan_sha256),
        operation_id=legacy_content_operation_id_for_plan(plan_sha256),
        effective_at_ms=effective_at_ms,
        inventory_manifest_sha256=str(inventory_binding["manifest_sha256"]),
        root_binding_sha256=str(inventory_binding["root_binding_sha256"]),
        reviewer_id=str(review["reviewer_id"]),
        records=tuple(prepared_by_id[value] for value in record_ids),
        relationships=tuple(
            sorted(prepared_relationships, key=lambda value: value.relationship_id)
        ),
        )
    except Exception:
        for value in prepared_by_id.values():
            value.observation.lease.close()
        raise


def _close_batch_leases(batch: _PreparedContentBatch) -> None:
    seen: set[int] = set()
    for value in (*batch.records, *(row.source for row in batch.relationships), *(row.target for row in batch.relationships)):
        lease = value.observation.lease
        identity = id(lease)
        if identity in seen:
            continue
        seen.add(identity)
        lease.close()


class LegacyPersonalContentImporter:
    """Apply one exact, bounded P2.3 content/asset effect batch."""

    __slots__ = (
        "_artifacts",
        "_audit",
        "_factory",
        "_failpoint",
        "_items",
        "_migration",
        "_outbox",
        "_relationships",
        "_stager",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        stager: LegacyBlobStager,
        *,
        artifacts: ArtifactRepository | None = None,
        items: ItemRepository | None = None,
        migration: LegacyMigrationRepository | None = None,
        relationships: LegacyMigrationRelationshipRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("LegacyPersonalContentImporter requires a ConnectionFactory.")
        if not hasattr(stager, "stage_and_verify") or not isinstance(
            getattr(stager, "resolved_root", None), str
        ):
            raise TypeError("A trusted LegacyBlobStager is required.")
        self._factory = factory
        self._stager = stager
        self._artifacts = artifacts or ArtifactRepository()
        self._items = items or ItemRepository()
        self._migration = migration or LegacyMigrationRepository()
        self._relationships = relationships or LegacyMigrationRelationshipRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    def _append_record_events(
        self,
        connection: ManagedConnection,
        *,
        batch: _PreparedContentBatch,
        record: LegacyMigrationRecord,
    ) -> None:
        details = {
            "import_mode": record.details.import_mode,
            "inventory_record_id": record.details.inventory_record_id,
            "mapping_id": record.id,
            "migration_run_id": record.migration_run_id,
            "plan_sha256": batch.plan_sha256,
            "result_disposition": record.details.result_disposition,
            "schema_version": 1,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_AUDIT_NAMESPACE, record.id),
                occurred_at_ms=batch.effective_at_ms,
                request_id=batch.request_id,
                operation_id=batch.operation_id,
                action=f"legacy_migration.{record.details.import_mode}",
                outcome=record.details.result_disposition,
                actor_kind="system",
                actor_user_id=None,
                actor_display_name="P2.3 legacy migration",
                actor_role_snapshot="system",
                space_id=record.details.target_space_id,
                item_id=record.item_id,
                new_revision_id=record.revision_id,
                reason="reviewed P2.3 legacy personal-content import",
                details=details,
                request_meta={"reviewer_id": batch.reviewer_id},
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"legacy-migration:{record.id}:imported:v1",
                space_id=record.details.target_space_id,
                aggregate_type="legacy_migration_map",
                aggregate_id=record.id,
                aggregate_version=1,
                event_type=f"legacy_migration.{record.details.import_mode}.imported",
                payload={
                    **details,
                    "operation_id": batch.operation_id,
                    "resolved_owner_user_id": record.resolved_owner_user_id,
                },
                available_at_ms=batch.effective_at_ms,
            ),
        )

    def _append_relationship_events(
        self,
        connection: ManagedConnection,
        *,
        batch: _PreparedContentBatch,
        record: LegacyMigrationRelationshipRecord,
    ) -> None:
        details = {
            "from_mapping_id": record.from_mapping_id,
            "migration_run_id": record.migration_run_id,
            "plan_sha256": batch.plan_sha256,
            "relationship_id": record.relationship_id,
            "relationship_mapping_id": record.id,
            "schema_version": 1,
            "to_mapping_id": record.to_mapping_id,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_RELATIONSHIP_AUDIT_NAMESPACE, record.id),
                occurred_at_ms=batch.effective_at_ms,
                request_id=batch.request_id,
                operation_id=batch.operation_id,
                action="legacy_migration.asset_reference",
                outcome="imported",
                actor_kind="system",
                actor_user_id=None,
                actor_display_name="P2.3 legacy migration",
                actor_role_snapshot="system",
                space_id=record.target_space_id,
                item_id=record.source_item_id,
                new_revision_id=record.source_revision_id,
                reason="reviewed P2.3 legacy asset-reference import",
                details=details,
                request_meta={"reviewer_id": batch.reviewer_id},
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"legacy-migration-relationship:{record.id}:imported:v1",
                space_id=record.target_space_id,
                aggregate_type="legacy_migration_relationship_map",
                aggregate_id=record.id,
                aggregate_version=1,
                event_type="legacy_migration.asset_reference.imported",
                payload={**details, "operation_id": batch.operation_id},
                available_at_ms=batch.effective_at_ms,
            ),
        )

    @staticmethod
    def _assert_current_owner(
        connection: ManagedConnection, prepared: _PreparedContentRecord
    ) -> None:
        user = connection.execute(
            """SELECT system_role,account_status,current_part_id
               FROM app_user WHERE id=?""",
            (prepared.owner_user_id,),
        ).fetchone()
        space = connection.execute(
            """SELECT 1 FROM knowledge_space
               WHERE id=? AND space_kind='personal' AND owner_user_id=?""",
            (prepared.target_space_id, prepared.owner_user_id),
        ).fetchone()
        if (
            user is None
            or space is None
            or user["system_role"] != prepared.expected_system_role
            or user["account_status"] != prepared.expected_account_status
            or user["current_part_id"] != prepared.expected_part_id
        ):
            raise LegacyContentStateConflictError(
                "The reviewed canonical owner or personal space changed before commit."
            )
        membership = connection.execute(
            """SELECT part_id FROM org_membership
               WHERE user_id=? AND valid_to IS NULL""",
            (prepared.owner_user_id,),
        ).fetchall()
        if prepared.expected_part_id is None:
            if membership:
                raise LegacyContentStateConflictError(
                    "The reviewed unassigned owner gained an active membership."
                )
        elif (
            len(membership) != 1
            or membership[0]["part_id"] != prepared.expected_part_id
        ):
            raise LegacyContentStateConflictError(
                "The reviewed owner membership changed before commit."
            )

    def apply_batch(
        self,
        reviewed_plan_bytes: bytes,
        source_payloads: Mapping[str, bytes],
        *,
        selected_record_ids: Sequence[str],
        selected_relationship_ids: Sequence[str] = (),
        expected_plan_sha256: str,
    ) -> LegacyContentImportResult:
        batch = _prepare_batch(
            reviewed_plan_bytes,
            source_payloads,
            selected_record_ids=selected_record_ids,
            selected_relationship_ids=selected_relationship_ids,
            expected_plan_sha256=expected_plan_sha256,
            stager=self._stager,
        )
        connection: ManagedConnection | None = None
        try:
            connection = self._factory.open()
            with transaction(connection):
                return self._apply_in_transaction(connection, batch)
        finally:
            if connection is not None:
                connection.close()
            _close_batch_leases(batch)

    def _apply_in_transaction(
        self,
        connection: ManagedConnection,
        batch: _PreparedContentBatch,
    ) -> LegacyContentImportResult:
        outcomes: list[LegacyContentRecordOutcome] = []
        for prepared in batch.records:
            self._assert_current_owner(connection, prepared)
            existing_mapping = self._migration.get_by_id(
                connection, prepared.mapping_id
            )
            artifact_result = self._artifacts.put_legacy_bundle(
                connection, prepared.artifact_bundle
            )
            self._hit("legacy_content.after_artifact")
            item_result = None
            if prepared.item_bundle is not None:
                item_result = self._items.put_legacy_markdown_import(
                    connection, prepared.item_bundle
                )
                self._hit("legacy_content.after_item")
                disposition = item_result.disposition
                item_id = item_result.item_id
                revision_id = item_result.revision_id
                item_source_id = item_result.item_source_id
            else:
                disposition = "created" if artifact_result.inserted else "existing_exact"
                item_id = revision_id = item_source_id = None
            # The append-only mapping records the original import outcome.
            # Repository replay may naturally report ``no_op`` or
            # ``existing_exact`` after that first commit; preserve the durable
            # historical disposition and exact target IDs on every replay.
            if existing_mapping is not None:
                disposition = existing_mapping.details.result_disposition
                item_id = existing_mapping.item_id
                revision_id = existing_mapping.revision_id
                item_source_id = existing_mapping.details.item_source_id
            source = prepared.source
            details = LegacyMigrationDetails(
                plan_sha256=batch.plan_sha256,
                inventory_manifest_sha256=batch.inventory_manifest_sha256,
                root_binding_sha256=batch.root_binding_sha256,
                inventory_record_id=prepared.record_id,
                inventory_record_sha256=str(
                    prepared.plan_row["inventory_record_sha256"]
                ),
                root_id=str(source["root_id"]),
                path_key_sha256=hashlib.sha256(
                    prepared.path_key.encode("utf-8")
                ).hexdigest(),
                category=str(source["category"]),
                record_kind="physical_file",
                source_ordinal=0,
                import_mode=prepared.import_mode,  # type: ignore[arg-type]
                result_disposition=disposition,  # type: ignore[arg-type]
                target_space_id=prepared.target_space_id,
                blob_sha256=prepared.observation.blob.sha256,
                artifact_blob_id=prepared.artifact_bundle.ids.raw_manifest_id,
                extraction_snapshot_id=(
                    prepared.artifact_bundle.ids.extraction_snapshot_id
                    if prepared.item_bundle is not None
                    else None
                ),
                item_source_id=item_source_id,
                normalized_content_sha256=(
                    prepared.observation.evidence.normalized_content_sha256
                ),
                normalizer_version=prepared.observation.evidence.normalizer_version,
                source_size_bytes=prepared.observation.blob.size_bytes,
                source_media_type=prepared.observation.blob.media_type,
                source_record_sha256=prepared.observation.blob.sha256,
            )
            self._hit("migration.before_checkpoint")
            mapped = self._migration.put_imported(
                connection,
                mapping_id=prepared.mapping_id,
                migration_run_id=batch.migration_run_id,
                source_path_key=prepared.path_key,
                key=prepared.migration_key,
                target=LegacyMigrationTarget(
                    resolved_owner_user_id=prepared.owner_user_id,
                    artifact_id=prepared.artifact_bundle.ids.artifact_id,
                    item_id=item_id,
                    revision_id=revision_id,
                    edge_id=None,
                    details=details,
                    source_path_key=prepared.path_key,
                ),
                verified_blob_evidence=prepared.observation.evidence,
                now_ms=batch.effective_at_ms,
            )
            self._hit("legacy_content.after_record_mapping")
            self._append_record_events(connection, batch=batch, record=mapped.record)
            outcomes.append(
                LegacyContentRecordOutcome(
                    record_id=prepared.record_id,
                    mapping_id=mapped.record.id,
                    import_mode=prepared.import_mode,
                    result_disposition=mapped.record.details.result_disposition,
                    artifact_id=str(mapped.record.artifact_id),
                    item_id=mapped.record.item_id,
                    revision_id=mapped.record.revision_id,
                    mapping_inserted=mapped.inserted,
                )
            )
        relationship_outcomes: list[LegacyContentRelationshipOutcome] = []
        for prepared in batch.relationships:
            self._assert_current_owner(connection, prepared.source)
            self._assert_current_owner(connection, prepared.target)
            source_mapping = self._migration.get_by_id(
                connection, prepared.source.mapping_id
            )
            target_mapping = self._migration.get_by_id(
                connection, prepared.target.mapping_id
            )
            if (
                source_mapping is None
                or target_mapping is None
                or source_mapping.item_id is None
                or source_mapping.revision_id is None
                or source_mapping.artifact_id is None
                or target_mapping.artifact_id is None
                or target_mapping.details.artifact_blob_id is None
            ):
                raise LegacyContentStateConflictError(
                    "An asset relationship endpoint has not been imported exactly."
                )
            embedded = self._artifacts.put_embedded_asset_link(
                connection,
                LegacyEmbeddedAssetLink(
                    plan_sha256=batch.plan_sha256,
                    relationship_id=prepared.relationship_id,
                    manifest_id=prepared.embedded_manifest_id,
                    source_artifact_id=source_mapping.artifact_id,
                    target_asset_artifact_id=target_mapping.artifact_id,
                    target_asset_raw_manifest_id=target_mapping.details.artifact_blob_id,
                    target_blob=prepared.target.observation.blob,
                    logical_name=prepared.logical_name,
                    logical_name_sha256=prepared.logical_name_sha256,
                    ordinal=prepared.ordinal,
                    created_at_ms=batch.effective_at_ms,
                ),
            )
            self._hit("legacy_content.after_embedded_asset")
            target = LegacyMigrationRelationshipTarget(
                from_mapping_id=source_mapping.id,
                to_mapping_id=target_mapping.id,
                resolved_owner_user_id=prepared.source.owner_user_id,
                target_space_id=prepared.source.target_space_id,
                source_artifact_id=source_mapping.artifact_id,
                source_item_id=source_mapping.item_id,
                source_revision_id=source_mapping.revision_id,
                target_asset_artifact_id=target_mapping.artifact_id,
                target_asset_blob_id=target_mapping.details.artifact_blob_id,
                embedded_asset_blob_id=embedded.manifest_id,
                embedded_ordinal=prepared.ordinal,
                embedded_logical_name_sha256=prepared.logical_name_sha256,
                details=LegacyMigrationRelationshipDetails(
                    plan_sha256=batch.plan_sha256,
                    inventory_manifest_sha256=batch.inventory_manifest_sha256,
                    root_binding_sha256=batch.root_binding_sha256,
                    relationship_id=prepared.relationship_id,
                    inventory_relationship_sha256=str(
                        prepared.plan_row["inventory_relationship_sha256"]
                    ),
                    from_inventory_record_id=prepared.source.record_id,
                    to_inventory_record_id=prepared.target.record_id,
                ),
            )
            relationship_mapping_id = migration_relationship_mapping_id_for_plan(
                batch.plan_sha256, prepared.relationship_id
            )
            self._hit("migration.before_checkpoint")
            mapped = self._relationships.put_imported(
                connection,
                relationship_mapping_id=relationship_mapping_id,
                migration_run_id=batch.migration_run_id,
                relationship_id=prepared.relationship_id,
                relationship_sha256=str(
                    prepared.plan_row["inventory_relationship_sha256"]
                ),
                target=target,
                source_verified_blob_evidence=prepared.source.observation.evidence,
                target_verified_blob_evidence=prepared.target.observation.evidence,
                now_ms=batch.effective_at_ms,
            )
            self._hit("legacy_content.after_relationship_mapping")
            self._append_relationship_events(
                connection, batch=batch, record=mapped.record
            )
            relationship_outcomes.append(
                LegacyContentRelationshipOutcome(
                    relationship_id=prepared.relationship_id,
                    relationship_mapping_id=mapped.record.id,
                    embedded_manifest_id=embedded.manifest_id,
                    mapping_inserted=mapped.inserted,
                )
            )
        return LegacyContentImportResult(
            plan_sha256=batch.plan_sha256,
            migration_run_id=batch.migration_run_id,
            request_id=batch.request_id,
            operation_id=batch.operation_id,
            records=tuple(outcomes),
            relationships=tuple(relationship_outcomes),
        )


__all__ = [
    "LegacyBlobStager",
    "LegacyBlobLease",
    "LegacyContentBatchLimitError",
    "LegacyContentImportError",
    "LegacyContentImportResult",
    "LegacyContentPreparationError",
    "LegacyContentRecordOutcome",
    "LegacyContentRelationshipOutcome",
    "LegacyContentStateConflictError",
    "LegacyPersonalContentImporter",
    "MARKDOWN_MEDIA_TYPE",
    "MARKDOWN_NORMALIZER_VERSION",
    "MAX_CONTENT_EFFECTS_PER_TRANSACTION",
    "StagedBlobObservation",
    "legacy_content_operation_id_for_plan",
    "legacy_content_request_id_for_plan",
]

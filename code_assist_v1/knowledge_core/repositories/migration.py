"""Durable, connection-scoped checkpoints for reviewed legacy imports.

P2.3 writes only successfully imported records here.  The reviewed plan remains
the authority for the five terminal classifications; this table is the
transactional replay ledger for canonical effects, not a replacement plan or
an external cursor.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
import unicodedata
from typing import Iterable, Literal, Mapping
from uuid import UUID, uuid5

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError
from .artifacts import CAS_BLOB_MEDIA_TYPE


MigrationImportMode = Literal[
    "source_container", "user_account", "personal_item", "personal_asset"
]
MigrationRecordKind = Literal["physical_file", "logical_record"]
MigrationResultDisposition = Literal[
    "created", "existing_exact", "provenance_added", "no_op", "dependency_closed"
]
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$", re.ASCII)
_RECORD_ID_RE = re.compile(r"^rec_[0-9a-f]{24}$", re.ASCII)
_RELATIONSHIP_ID_RE = re.compile(r"^rel_[0-9a-f]{24}$", re.ASCII)
_RUN_NAMESPACE = UUID("877d62a2-e1de-42c2-987f-a1b7d66fcd93")
_MAPPING_NAMESPACE = UUID("fb14492f-09a1-4fb4-bb89-f44f12475f61")
_RELATIONSHIP_MAPPING_NAMESPACE = UUID("87dfe36b-d937-4453-a88b-3d03cbefcb14")

P2_3_IMPORT_CATEGORIES = frozenset(
    {
        "identity.users_registry",
        "knowledge.legacy_flat_markdown",
        "knowledge.legacy_inbox",
        "knowledge.legacy_raw",
        "knowledge.legacy_raw_chunks",
        "knowledge.legacy_thoughts",
        "knowledge.wiki_assets",
        "knowledge.wiki_hidden_pages",
        "knowledge.wiki_sources",
    }
)
_PERSONAL_ITEM_CATEGORIES = P2_3_IMPORT_CATEGORIES - {
    "identity.users_registry",
    "knowledge.wiki_assets",
}

_DETAIL_KEYS = frozenset(
    {
        "artifact_blob_id",
        "account_projection_schema",
        "account_projection_sha256",
        "assigned_part_id",
        "blob_sha256",
        "category",
        "execution_phase",
        "extraction_snapshot_id",
        "final_terminal_classification",
        "import_mode",
        "inventory_manifest_sha256",
        "inventory_record_id",
        "inventory_record_sha256",
        "item_source_id",
        "leader_assignment_id",
        "membership_id",
        "normalizer_version",
        "normalized_content_sha256",
        "parent_inventory_record_id",
        "path_key_sha256",
        "plan_sha256",
        "planned_import_child_count",
        "record_kind",
        "result_disposition",
        "root_binding_sha256",
        "root_id",
        "schema_version",
        "source_media_type",
        "source_ordinal",
        "source_record_sha256",
        "source_size_bytes",
        "target_space_id",
        "imported_child_bindings_sha256",
    }
)

_COLUMNS = """
id,migration_run_id,legacy_kind,normalized_path,source_sha256,
resolved_owner_user_id,artifact_id,item_id,revision_id,edge_id,status,
details_json,created_at,updated_at
""".strip()

_RELATIONSHIP_COLUMNS = """
id,migration_run_id,relationship_id,relationship_sha256,relationship_type,
from_inventory_record_id,to_inventory_record_id,from_mapping_id,to_mapping_id,
resolved_owner_user_id,target_space_id,source_artifact_id,source_item_id,
source_revision_id,target_asset_artifact_id,target_asset_blob_id,
embedded_asset_blob_id,embedded_ordinal,embedded_logical_name_sha256,status,
details_json,created_at,updated_at
""".strip()


class LegacyMigrationRepositoryError(KnowledgeCoreDatabaseError):
    code = "MIGRATION_REPOSITORY_ERROR"


class LegacyMigrationNotFoundError(LegacyMigrationRepositoryError):
    code = "MIGRATION_MAPPING_NOT_FOUND"


class LegacyMigrationConflictError(LegacyMigrationRepositoryError):
    code = "MIGRATION_MAPPING_CONFLICT"


class LegacyMigrationIntegrityError(LegacyMigrationRepositoryError):
    code = "MIGRATION_TARGET_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class LegacyMigrationKey:
    legacy_kind: str
    normalized_path: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class LegacyMigrationDetails:
    plan_sha256: str
    inventory_manifest_sha256: str
    root_binding_sha256: str
    inventory_record_id: str
    inventory_record_sha256: str
    root_id: str
    path_key_sha256: str
    category: str
    record_kind: MigrationRecordKind
    source_ordinal: int
    import_mode: MigrationImportMode
    result_disposition: MigrationResultDisposition
    target_space_id: str | None = None
    blob_sha256: str | None = None
    artifact_blob_id: str | None = None
    extraction_snapshot_id: str | None = None
    item_source_id: str | None = None
    normalized_content_sha256: str | None = None
    normalizer_version: str | None = None
    source_size_bytes: int | None = None
    source_media_type: str | None = None
    source_record_sha256: str | None = None
    account_projection_schema: str | None = None
    account_projection_sha256: str | None = None
    assigned_part_id: str | None = None
    membership_id: str | None = None
    leader_assignment_id: str | None = None
    parent_inventory_record_id: str | None = None
    planned_import_child_count: int | None = None
    imported_child_bindings_sha256: str | None = None

    def to_mapping(self) -> dict[str, object]:
        return {
            "account_projection_schema": self.account_projection_schema,
            "account_projection_sha256": self.account_projection_sha256,
            "assigned_part_id": self.assigned_part_id,
            "artifact_blob_id": self.artifact_blob_id,
            "blob_sha256": self.blob_sha256,
            "category": self.category,
            "execution_phase": "P2.3",
            "extraction_snapshot_id": self.extraction_snapshot_id,
            "final_terminal_classification": "imported",
            "import_mode": self.import_mode,
            "inventory_manifest_sha256": self.inventory_manifest_sha256,
            "inventory_record_id": self.inventory_record_id,
            "inventory_record_sha256": self.inventory_record_sha256,
            "item_source_id": self.item_source_id,
            "leader_assignment_id": self.leader_assignment_id,
            "membership_id": self.membership_id,
            "normalizer_version": self.normalizer_version,
            "normalized_content_sha256": self.normalized_content_sha256,
            "parent_inventory_record_id": self.parent_inventory_record_id,
            "path_key_sha256": self.path_key_sha256,
            "plan_sha256": self.plan_sha256,
            "planned_import_child_count": self.planned_import_child_count,
            "record_kind": self.record_kind,
            "result_disposition": self.result_disposition,
            "root_binding_sha256": self.root_binding_sha256,
            "root_id": self.root_id,
            "schema_version": 1,
            "source_media_type": self.source_media_type,
            "source_ordinal": self.source_ordinal,
            "source_record_sha256": self.source_record_sha256,
            "source_size_bytes": self.source_size_bytes,
            "target_space_id": self.target_space_id,
            "imported_child_bindings_sha256": self.imported_child_bindings_sha256,
        }


@dataclass(frozen=True, slots=True)
class LegacyMigrationTarget:
    resolved_owner_user_id: str | None
    artifact_id: str | None
    item_id: str | None
    revision_id: str | None
    edge_id: str | None
    details: LegacyMigrationDetails
    source_path_key: str | None = None


@dataclass(frozen=True, slots=True)
class LegacyMigrationRecord:
    id: str
    migration_run_id: str
    key: LegacyMigrationKey
    resolved_owner_user_id: str | None
    artifact_id: str | None
    item_id: str | None
    revision_id: str | None
    edge_id: str | None
    status: str
    details_json: str
    details: LegacyMigrationDetails
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class LegacyMigrationInsertResult:
    record: LegacyMigrationRecord
    inserted: bool


@dataclass(frozen=True, slots=True)
class VerifiedBlobEvidence:
    """Content observations produced before a database write transaction.

    The artifact-store boundary streams a no-follow, root-bound file and
    constructs this value.  This repository never opens a file while holding
    the caller's SQLite transaction; it only compares the already-observed
    values with reviewed details and canonical rows.
    """

    raw_sha256: str
    size_bytes: int
    media_type: str
    normalized_content_sha256: str | None = None
    normalizer_version: str | None = None


@dataclass(frozen=True, slots=True)
class LegacyMigrationRelationshipDetails:
    plan_sha256: str
    inventory_manifest_sha256: str
    root_binding_sha256: str
    relationship_id: str
    inventory_relationship_sha256: str
    from_inventory_record_id: str
    to_inventory_record_id: str


@dataclass(frozen=True, slots=True)
class LegacyMigrationRelationshipTarget:
    from_mapping_id: str
    to_mapping_id: str
    resolved_owner_user_id: str
    target_space_id: str
    source_artifact_id: str
    source_item_id: str
    source_revision_id: str
    target_asset_artifact_id: str
    target_asset_blob_id: str
    embedded_asset_blob_id: str
    embedded_ordinal: int
    embedded_logical_name_sha256: str
    details: LegacyMigrationRelationshipDetails


@dataclass(frozen=True, slots=True)
class LegacyMigrationRelationshipRecord:
    id: str
    migration_run_id: str
    relationship_id: str
    relationship_sha256: str
    relationship_type: str
    from_inventory_record_id: str
    to_inventory_record_id: str
    from_mapping_id: str
    to_mapping_id: str
    resolved_owner_user_id: str
    target_space_id: str
    source_artifact_id: str
    source_item_id: str
    source_revision_id: str
    target_asset_artifact_id: str
    target_asset_blob_id: str
    embedded_asset_blob_id: str
    embedded_ordinal: int
    embedded_logical_name_sha256: str
    status: str
    details_json: str
    details: LegacyMigrationRelationshipDetails
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class LegacyMigrationRelationshipInsertResult:
    record: LegacyMigrationRelationshipRecord
    inserted: bool


def _uuid(value: object, *, field: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise ValueError(f"{field} must be a canonical lowercase UUID.") from None
    if str(parsed) != value:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _sha(value: object, *, field: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")
    return value


def _text(value: object, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or character == "\x7f" for character in value)
    ):
        raise ValueError(f"{field} must contain 1..{maximum} safe characters.")
    return value


def _utc_ms(value: object, *, field: str = "now_ms") -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer millisecond value.")
    return value


def _nonnegative_integer(
    value: object, *, field: str, nullable: bool = False
) -> int | None:
    if value is None and nullable:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


def _user(value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("resolved_owner_user_id is invalid.")
    return value


def migration_run_id_for_plan(plan_sha256: str) -> str:
    return str(uuid5(_RUN_NAMESPACE, str(_sha(plan_sha256, field="plan_sha256"))))


def legacy_user_account_projection_sha256(record: object) -> str:
    """Hash the normalized initial account row without exposing credentials."""

    field_names = (
        "id",
        "display_name",
        "password_hash",
        "system_role",
        "account_status",
        "current_part_id",
        "scope_epoch",
        "must_change_password",
        "password_changed_at_ms",
        "created_at_ms",
        "updated_at_ms",
        "disabled_at_ms",
    )
    try:
        payload = {field: getattr(record, field) for field in field_names}
        payload["row_version"] = getattr(record, "row_version", 1)
    except (AttributeError, TypeError):
        raise TypeError("A normalized legacy or canonical user record is required.") from None
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ValueError("The account projection is not canonical JSON.") from None
    return hashlib.sha256(encoded).hexdigest()


def migration_child_bindings_sha256(
    bindings: Iterable[tuple[str, str, str]],
) -> str:
    """Hash the exact imported child set that closes a source container."""

    if isinstance(bindings, (str, bytes, bytearray, Mapping)):
        raise TypeError("Child bindings must be an iterable of three-tuples.")
    prepared: list[tuple[str, str, str]] = []
    mapping_ids: set[str] = set()
    record_ids: set[str] = set()
    try:
        iterator = iter(bindings)
    except TypeError:
        raise TypeError("Child bindings must be iterable.") from None
    for binding in iterator:
        if not isinstance(binding, tuple) or len(binding) != 3:
            raise ValueError("Each child binding must be an exact three-tuple.")
        mapping_id = str(_uuid(binding[0], field="child_mapping_id"))
        record_id = binding[1]
        if not isinstance(record_id, str) or _RECORD_ID_RE.fullmatch(record_id) is None:
            raise ValueError("child_inventory_record_id is invalid.")
        source_sha256 = str(_sha(binding[2], field="child_source_record_sha256"))
        if mapping_id in mapping_ids or record_id in record_ids:
            raise ValueError("Child bindings must have unique mapping and record IDs.")
        mapping_ids.add(mapping_id)
        record_ids.add(record_id)
        prepared.append((mapping_id, record_id, source_sha256))
    prepared.sort()
    encoded = json.dumps(
        prepared,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def migration_mapping_id_for_record(plan_sha256: str, record_id: str) -> str:
    plan = str(_sha(plan_sha256, field="plan_sha256"))
    if not isinstance(record_id, str) or _RECORD_ID_RE.fullmatch(record_id) is None:
        raise ValueError("record_id must be a bound inventory record ID.")
    return str(uuid5(_MAPPING_NAMESPACE, f"{plan}:{record_id}"))


def migration_relationship_mapping_id_for_plan(
    plan_sha256: str, relationship_id: str
) -> str:
    plan = str(_sha(plan_sha256, field="plan_sha256"))
    if (
        not isinstance(relationship_id, str)
        or _RELATIONSHIP_ID_RE.fullmatch(relationship_id) is None
    ):
        raise ValueError("relationship_id must be a bound inventory relationship ID.")
    return str(uuid5(_RELATIONSHIP_MAPPING_NAMESPACE, f"{plan}:{relationship_id}"))


def migration_source_locator(
    *,
    root_id: str,
    path_key: str,
    record_kind: MigrationRecordKind,
    source_ordinal: int,
) -> str:
    """Return a bounded, non-secret identity for the v1 mapping path column."""

    if not isinstance(root_id, str) or _IDENTIFIER_RE.fullmatch(root_id) is None:
        raise ValueError("root_id is invalid.")
    if not isinstance(path_key, str) or not path_key or len(path_key) > 32768:
        raise ValueError("path_key is invalid.")
    path_parts = path_key.split("/")
    if (
        path_key != unicodedata.normalize("NFKC", path_key).casefold()
        or path_key.startswith(("/", "\\"))
        or "\\" in path_key
        or re.match(r"^[a-z]:", path_key, re.ASCII) is not None
        or any(part in {"", ".", ".."} for part in path_parts)
    ):
        raise ValueError("path_key must be a canonical relative inventory key.")
    if record_kind not in ("physical_file", "logical_record"):
        raise ValueError("record_kind is invalid.")
    if (
        isinstance(source_ordinal, bool)
        or not isinstance(source_ordinal, int)
        or source_ordinal < 0
        or (record_kind == "physical_file" and source_ordinal != 0)
        or (record_kind == "logical_record" and source_ordinal < 1)
    ):
        raise ValueError("source_ordinal is invalid.")
    path_digest = hashlib.sha256(path_key.encode("utf-8")).hexdigest()
    return f"p2v1/{root_id}/{path_digest}/{record_kind}/{source_ordinal}"


def _prepare_key(value: LegacyMigrationKey) -> LegacyMigrationKey:
    if not isinstance(value, LegacyMigrationKey):
        raise TypeError("A LegacyMigrationKey is required.")
    return LegacyMigrationKey(
        legacy_kind=_text(value.legacy_kind, field="legacy_kind", maximum=80),
        normalized_path=_text(
            value.normalized_path, field="normalized_path", maximum=1000
        ),
        source_sha256=str(_sha(value.source_sha256, field="source_sha256")),
    )


def _prepare_details(value: LegacyMigrationDetails) -> tuple[LegacyMigrationDetails, str]:
    if not isinstance(value, LegacyMigrationDetails):
        raise TypeError("Legacy migration details are required.")
    for field in (
        "plan_sha256",
        "inventory_manifest_sha256",
        "root_binding_sha256",
        "inventory_record_sha256",
        "path_key_sha256",
    ):
        _sha(getattr(value, field), field=field)
    if _RECORD_ID_RE.fullmatch(value.inventory_record_id) is None:
        raise ValueError("inventory_record_id is invalid.")
    if _IDENTIFIER_RE.fullmatch(value.root_id) is None:
        raise ValueError("root_id is invalid.")
    if value.category not in P2_3_IMPORT_CATEGORIES:
        raise ValueError("category is not owned by P2.3.")
    if value.record_kind not in ("physical_file", "logical_record"):
        raise ValueError("record_kind is invalid.")
    if (
        isinstance(value.source_ordinal, bool)
        or not isinstance(value.source_ordinal, int)
        or value.source_ordinal < 0
        or (value.record_kind == "physical_file" and value.source_ordinal != 0)
        or (value.record_kind == "logical_record" and value.source_ordinal < 1)
    ):
        raise ValueError("source_ordinal is invalid.")
    if value.import_mode not in (
        "source_container",
        "user_account",
        "personal_item",
        "personal_asset",
    ):
        raise ValueError("import_mode is invalid.")
    if value.result_disposition not in (
        "created",
        "existing_exact",
        "provenance_added",
        "no_op",
        "dependency_closed",
    ):
        raise ValueError("result_disposition is invalid.")
    for field in (
        "target_space_id",
        "artifact_blob_id",
        "extraction_snapshot_id",
        "item_source_id",
        "assigned_part_id",
        "membership_id",
        "leader_assignment_id",
    ):
        _uuid(getattr(value, field), field=field, nullable=True)
    _sha(value.blob_sha256, field="blob_sha256", nullable=True)
    _sha(
        value.normalized_content_sha256,
        field="normalized_content_sha256",
        nullable=True,
    )
    _sha(
        value.source_record_sha256,
        field="source_record_sha256",
        nullable=False,
    )
    _sha(
        value.account_projection_sha256,
        field="account_projection_sha256",
        nullable=True,
    )
    _sha(
        value.imported_child_bindings_sha256,
        field="imported_child_bindings_sha256",
        nullable=True,
    )
    if value.account_projection_schema is not None:
        _text(
            value.account_projection_schema,
            field="account_projection_schema",
            maximum=80,
        )
    _nonnegative_integer(
        value.source_size_bytes, field="source_size_bytes", nullable=True
    )
    _nonnegative_integer(
        value.planned_import_child_count,
        field="planned_import_child_count",
        nullable=True,
    )
    if value.parent_inventory_record_id is not None and (
        not isinstance(value.parent_inventory_record_id, str)
        or _RECORD_ID_RE.fullmatch(value.parent_inventory_record_id) is None
    ):
        raise ValueError("parent_inventory_record_id is invalid.")
    if value.source_media_type is not None:
        _text(value.source_media_type, field="source_media_type", maximum=255)
    if value.import_mode != "source_container" and value.target_space_id is None:
        raise ValueError("A canonical personal target space is required.")
    if value.import_mode == "source_container" and (
        value.category != "identity.users_registry"
        or value.record_kind != "physical_file"
        or value.result_disposition != "dependency_closed"
        or value.target_space_id is not None
        or value.blob_sha256 is not None
        or value.artifact_blob_id is not None
        or value.extraction_snapshot_id is not None
        or value.item_source_id is not None
        or value.normalized_content_sha256 is not None
        or value.normalizer_version is not None
        or value.parent_inventory_record_id is not None
        or value.planned_import_child_count is None
        or value.imported_child_bindings_sha256 is None
    ):
        raise ValueError("source-container detail shape is invalid.")
    if value.import_mode == "user_account" and (
        value.category != "identity.users_registry" or value.record_kind != "logical_record"
    ):
        raise ValueError("user-account detail shape is invalid.")
    if value.import_mode == "user_account" and (
        value.account_projection_schema != "legacy_user_import_v1"
        or value.account_projection_sha256 is None
        or value.parent_inventory_record_id is None
        or value.parent_inventory_record_id == value.inventory_record_id
        or value.planned_import_child_count is not None
        or value.imported_child_bindings_sha256 is not None
        or (value.assigned_part_id is None) != (value.membership_id is None)
        or (value.leader_assignment_id is not None and value.assigned_part_id is None)
    ):
        raise ValueError("user-account projection evidence is invalid.")
    if value.import_mode == "personal_asset" and (
        value.category != "knowledge.wiki_assets" or value.record_kind != "physical_file"
    ):
        raise ValueError("personal-asset detail shape is invalid.")
    if value.import_mode == "personal_item" and (
        value.category not in _PERSONAL_ITEM_CATEGORIES
        or value.record_kind != "physical_file"
    ):
        raise ValueError("personal-item detail shape is invalid.")
    if value.import_mode == "personal_item":
        if value.result_disposition not in (
            "created",
            "provenance_added",
            "no_op",
        ):
            raise ValueError("A personal item has an invalid result disposition.")
        if value.normalized_content_sha256 is None or value.normalizer_version is None:
            raise ValueError("A personal item requires normalized content evidence.")
        _text(
            value.normalizer_version,
            field="normalizer_version",
            maximum=80,
        )
        if value.normalizer_version != "markdown_lf_v1":
            raise ValueError("A personal item requires the frozen normalizer.")
    elif value.import_mode != "source_container":
        if value.result_disposition not in ("created", "existing_exact"):
            raise ValueError("This import mode has an invalid result disposition.")
        if (
            value.normalized_content_sha256 is not None
            or value.normalizer_version is not None
        ):
            raise ValueError("Only a personal item has normalized content evidence.")
    if value.import_mode != "user_account" and (
            value.account_projection_schema is not None
            or value.account_projection_sha256 is not None
            or value.assigned_part_id is not None
            or value.membership_id is not None
            or value.leader_assignment_id is not None
    ):
        raise ValueError("Only a user account has account projection evidence.")
    if value.import_mode not in {"source_container", "user_account"} and (
        value.parent_inventory_record_id is not None
        or value.planned_import_child_count is not None
        or value.imported_child_bindings_sha256 is not None
    ):
        raise ValueError("Only registry mappings have dependency evidence.")
    mapping = value.to_mapping()
    encoded = json.dumps(
        mapping,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return value, encoded


def _details_from_json(value: object) -> LegacyMigrationDetails:
    if not isinstance(value, str):
        raise LegacyMigrationIntegrityError("Stored migration details are invalid.")
    try:
        raw = json.loads(value)
    except (json.JSONDecodeError, RecursionError):
        raise LegacyMigrationIntegrityError("Stored migration details are invalid.") from None
    if not isinstance(raw, Mapping) or set(raw) != _DETAIL_KEYS:
        raise LegacyMigrationIntegrityError("Stored migration details are invalid.")
    if (
        raw.get("schema_version") != 1
        or raw.get("execution_phase") != "P2.3"
        or raw.get("final_terminal_classification") != "imported"
    ):
        raise LegacyMigrationIntegrityError("Stored migration details are invalid.")
    try:
        details = LegacyMigrationDetails(
            plan_sha256=raw["plan_sha256"],  # type: ignore[arg-type]
            inventory_manifest_sha256=raw["inventory_manifest_sha256"],  # type: ignore[arg-type]
            root_binding_sha256=raw["root_binding_sha256"],  # type: ignore[arg-type]
            inventory_record_id=raw["inventory_record_id"],  # type: ignore[arg-type]
            inventory_record_sha256=raw["inventory_record_sha256"],  # type: ignore[arg-type]
            root_id=raw["root_id"],  # type: ignore[arg-type]
            path_key_sha256=raw["path_key_sha256"],  # type: ignore[arg-type]
            category=raw["category"],  # type: ignore[arg-type]
            record_kind=raw["record_kind"],  # type: ignore[arg-type]
            source_ordinal=raw["source_ordinal"],  # type: ignore[arg-type]
            import_mode=raw["import_mode"],  # type: ignore[arg-type]
            result_disposition=raw["result_disposition"],  # type: ignore[arg-type]
            target_space_id=raw["target_space_id"],  # type: ignore[arg-type]
            blob_sha256=raw["blob_sha256"],  # type: ignore[arg-type]
            artifact_blob_id=raw["artifact_blob_id"],  # type: ignore[arg-type]
            extraction_snapshot_id=raw["extraction_snapshot_id"],  # type: ignore[arg-type]
            item_source_id=raw["item_source_id"],  # type: ignore[arg-type]
            normalized_content_sha256=raw["normalized_content_sha256"],  # type: ignore[arg-type]
            normalizer_version=raw["normalizer_version"],  # type: ignore[arg-type]
            source_size_bytes=raw["source_size_bytes"],  # type: ignore[arg-type]
            source_media_type=raw["source_media_type"],  # type: ignore[arg-type]
            source_record_sha256=raw["source_record_sha256"],  # type: ignore[arg-type]
            account_projection_schema=raw["account_projection_schema"],  # type: ignore[arg-type]
            account_projection_sha256=raw["account_projection_sha256"],  # type: ignore[arg-type]
            assigned_part_id=raw["assigned_part_id"],  # type: ignore[arg-type]
            membership_id=raw["membership_id"],  # type: ignore[arg-type]
            leader_assignment_id=raw["leader_assignment_id"],  # type: ignore[arg-type]
            parent_inventory_record_id=raw["parent_inventory_record_id"],  # type: ignore[arg-type]
            planned_import_child_count=raw["planned_import_child_count"],  # type: ignore[arg-type]
            imported_child_bindings_sha256=raw["imported_child_bindings_sha256"],  # type: ignore[arg-type]
        )
        prepared, encoded = _prepare_details(details)
    except (TypeError, ValueError, KeyError):
        raise LegacyMigrationIntegrityError("Stored migration details are invalid.") from None
    if encoded != value:
        raise LegacyMigrationIntegrityError("Stored migration details are not canonical.")
    return prepared


def _prepare_target(value: LegacyMigrationTarget) -> tuple[LegacyMigrationTarget, str]:
    if not isinstance(value, LegacyMigrationTarget):
        raise TypeError("A LegacyMigrationTarget is required.")
    details, details_json = _prepare_details(value.details)
    owner = _user(value.resolved_owner_user_id, nullable=True)
    artifact_id = _uuid(value.artifact_id, field="artifact_id", nullable=True)
    item_id = _uuid(value.item_id, field="item_id", nullable=True)
    revision_id = _uuid(value.revision_id, field="revision_id", nullable=True)
    edge_id = _uuid(value.edge_id, field="edge_id", nullable=True)
    mode = details.import_mode
    if edge_id is not None:
        raise ValueError("P2.3 cannot import graph edges.")
    if mode != "source_container" and owner is None:
        raise ValueError("A personal import requires a resolved owner.")
    if mode == "source_container" and any(
        item is not None
        for item in (owner, artifact_id, item_id, revision_id, edge_id)
    ):
        raise ValueError("A source-container mapping cannot claim canonical targets.")
    if mode == "user_account" and any(
        value is not None for value in (artifact_id, item_id, revision_id)
    ):
        raise ValueError("A user-account mapping cannot claim content targets.")
    if mode in {"source_container", "user_account"} and any(
        value is not None
        for value in (
            details.blob_sha256,
            details.artifact_blob_id,
            details.extraction_snapshot_id,
            details.item_source_id,
        )
    ):
        raise ValueError("A registry mapping cannot claim canonical content evidence.")
    if mode == "user_account" and any(
        value is not None
        for value in (details.source_size_bytes, details.source_media_type)
    ):
        raise ValueError("A user-account mapping cannot claim source-file evidence.")
    if mode == "source_container" and (
        details.source_size_bytes is None or details.source_media_type is None
    ):
        raise ValueError("A source container requires source observation evidence.")
    if mode == "personal_asset" and (
        artifact_id is None
        or item_id is not None
        or revision_id is not None
        or details.blob_sha256 is None
        or details.artifact_blob_id is None
        or details.extraction_snapshot_id is not None
        or details.item_source_id is not None
        or details.source_size_bytes is None
        or details.source_media_type is None
    ):
        raise ValueError("A personal-asset mapping has an invalid target shape.")
    if mode == "personal_item" and (
        artifact_id is None
        or item_id is None
        or revision_id is None
        or details.blob_sha256 is None
        or details.artifact_blob_id is None
        or details.extraction_snapshot_id is None
        or details.item_source_id is None
        or details.normalized_content_sha256 is None
        or details.normalizer_version is None
        or details.source_size_bytes is None
        or details.source_media_type is None
    ):
        raise ValueError("A personal-item mapping has an invalid target shape.")
    source_path_key = value.source_path_key
    if source_path_key is not None:
        migration_source_locator(
            root_id=details.root_id,
            path_key=source_path_key,
            record_kind=details.record_kind,
            source_ordinal=details.source_ordinal,
        )
    return (
        LegacyMigrationTarget(
            owner,
            artifact_id,
            item_id,
            revision_id,
            edge_id,
            details,
            source_path_key,
        ),
        details_json,
    )


def _prepare_verified_blob_evidence(
    value: VerifiedBlobEvidence | None,
    *,
    details: LegacyMigrationDetails,
) -> VerifiedBlobEvidence | None:
    if details.import_mode == "user_account":
        if value is not None:
            raise LegacyMigrationIntegrityError(
                "A user-account mapping cannot consume blob evidence."
            )
        return None
    if not isinstance(value, VerifiedBlobEvidence):
        raise LegacyMigrationIntegrityError(
            "A physical import requires pre-transaction blob evidence."
        )
    try:
        raw_sha256 = str(_sha(value.raw_sha256, field="raw_sha256"))
        size_bytes = int(
            _nonnegative_integer(value.size_bytes, field="size_bytes")
        )
        media_type = _text(value.media_type, field="media_type", maximum=255)
        normalized_sha256 = _sha(
            value.normalized_content_sha256,
            field="normalized_content_sha256",
            nullable=True,
        )
        normalizer_version = value.normalizer_version
        if normalizer_version is not None:
            normalizer_version = _text(
                normalizer_version, field="normalizer_version", maximum=80
            )
    except (TypeError, ValueError):
        raise LegacyMigrationIntegrityError(
            "The pre-transaction blob evidence is invalid."
        ) from None
    expected_normalized = (
        details.normalized_content_sha256
        if details.import_mode == "personal_item"
        else None
    )
    expected_normalizer = (
        details.normalizer_version
        if details.import_mode == "personal_item"
        else None
    )
    if (
        raw_sha256 != details.source_record_sha256
        or (
            details.import_mode != "source_container"
            and raw_sha256 != details.blob_sha256
        )
        or size_bytes != details.source_size_bytes
        or media_type != details.source_media_type
        or normalized_sha256 != expected_normalized
        or normalizer_version != expected_normalizer
    ):
        raise LegacyMigrationIntegrityError(
            "The pre-transaction blob evidence does not match the reviewed source."
        )
    return VerifiedBlobEvidence(
        raw_sha256=raw_sha256,
        size_bytes=size_bytes,
        media_type=media_type,
        normalized_content_sha256=(
            None if normalized_sha256 is None else str(normalized_sha256)
        ),
        normalizer_version=normalizer_version,
    )


def _prepare_relationship_target(
    value: LegacyMigrationRelationshipTarget,
) -> tuple[LegacyMigrationRelationshipTarget, str]:
    if not isinstance(value, LegacyMigrationRelationshipTarget):
        raise TypeError("A LegacyMigrationRelationshipTarget is required.")
    details = value.details
    if not isinstance(details, LegacyMigrationRelationshipDetails):
        raise TypeError("Legacy migration relationship details are required.")
    for field in (
        "plan_sha256",
        "inventory_manifest_sha256",
        "root_binding_sha256",
        "inventory_relationship_sha256",
    ):
        _sha(getattr(details, field), field=field)
    if (
        not isinstance(details.relationship_id, str)
        or _RELATIONSHIP_ID_RE.fullmatch(details.relationship_id) is None
    ):
        raise ValueError("relationship_id is invalid.")
    for field in ("from_inventory_record_id", "to_inventory_record_id"):
        field_value = getattr(details, field)
        if not isinstance(field_value, str) or _RECORD_ID_RE.fullmatch(field_value) is None:
            raise ValueError(f"{field} is invalid.")
    if details.from_inventory_record_id == details.to_inventory_record_id:
        raise ValueError("An asset reference requires distinct endpoint records.")
    prepared = LegacyMigrationRelationshipTarget(
        from_mapping_id=str(_uuid(value.from_mapping_id, field="from_mapping_id")),
        to_mapping_id=str(_uuid(value.to_mapping_id, field="to_mapping_id")),
        resolved_owner_user_id=str(_user(value.resolved_owner_user_id)),
        target_space_id=str(_uuid(value.target_space_id, field="target_space_id")),
        source_artifact_id=str(_uuid(value.source_artifact_id, field="source_artifact_id")),
        source_item_id=str(_uuid(value.source_item_id, field="source_item_id")),
        source_revision_id=str(_uuid(value.source_revision_id, field="source_revision_id")),
        target_asset_artifact_id=str(
            _uuid(value.target_asset_artifact_id, field="target_asset_artifact_id")
        ),
        target_asset_blob_id=str(
            _uuid(value.target_asset_blob_id, field="target_asset_blob_id")
        ),
        embedded_asset_blob_id=str(
            _uuid(value.embedded_asset_blob_id, field="embedded_asset_blob_id")
        ),
        embedded_ordinal=int(
            _nonnegative_integer(value.embedded_ordinal, field="embedded_ordinal")
        ),
        embedded_logical_name_sha256=str(
            _sha(
                value.embedded_logical_name_sha256,
                field="embedded_logical_name_sha256",
            )
        ),
        details=details,
    )
    if (
        prepared.from_mapping_id == prepared.to_mapping_id
        or prepared.target_asset_blob_id == prepared.embedded_asset_blob_id
    ):
        raise ValueError("Asset-reference endpoint and blob identities must be distinct.")
    mapping = {
        "embedded_asset_blob_id": prepared.embedded_asset_blob_id,
        "embedded_logical_name_sha256": prepared.embedded_logical_name_sha256,
        "embedded_ordinal": prepared.embedded_ordinal,
        "execution_phase": "P2.3",
        "final_terminal_classification": "imported",
        "from_inventory_record_id": details.from_inventory_record_id,
        "from_mapping_id": prepared.from_mapping_id,
        "import_mode": "personal_asset",
        "inventory_manifest_sha256": details.inventory_manifest_sha256,
        "inventory_relationship_sha256": details.inventory_relationship_sha256,
        "plan_sha256": details.plan_sha256,
        "relationship_id": details.relationship_id,
        "relationship_type": "asset_reference",
        "resolved_owner_user_id": prepared.resolved_owner_user_id,
        "root_binding_sha256": details.root_binding_sha256,
        "schema_version": 1,
        "source_artifact_id": prepared.source_artifact_id,
        "source_item_id": prepared.source_item_id,
        "source_revision_id": prepared.source_revision_id,
        "status": "imported",
        "target_asset_artifact_id": prepared.target_asset_artifact_id,
        "target_asset_blob_id": prepared.target_asset_blob_id,
        "target_space_id": prepared.target_space_id,
        "to_inventory_record_id": details.to_inventory_record_id,
        "to_mapping_id": prepared.to_mapping_id,
    }
    details_json = json.dumps(
        mapping,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return prepared, details_json


def _relationship_target_from_json(value: object) -> LegacyMigrationRelationshipTarget:
    if not isinstance(value, str):
        raise LegacyMigrationIntegrityError("Stored relationship details are invalid.")
    try:
        raw = json.loads(value)
    except (json.JSONDecodeError, RecursionError):
        raise LegacyMigrationIntegrityError("Stored relationship details are invalid.") from None
    expected_keys = {
        "embedded_asset_blob_id", "embedded_logical_name_sha256", "embedded_ordinal",
        "execution_phase", "final_terminal_classification", "from_inventory_record_id",
        "from_mapping_id", "import_mode", "inventory_manifest_sha256",
        "inventory_relationship_sha256", "plan_sha256", "relationship_id",
        "relationship_type", "resolved_owner_user_id", "root_binding_sha256",
        "schema_version", "source_artifact_id", "source_item_id", "source_revision_id",
        "status", "target_asset_artifact_id", "target_asset_blob_id", "target_space_id",
        "to_inventory_record_id", "to_mapping_id",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected_keys or (
        raw.get("schema_version") != 1
        or raw.get("execution_phase") != "P2.3"
        or raw.get("final_terminal_classification") != "imported"
        or raw.get("import_mode") != "personal_asset"
        or raw.get("relationship_type") != "asset_reference"
        or raw.get("status") != "imported"
    ):
        raise LegacyMigrationIntegrityError("Stored relationship details are invalid.")
    try:
        target = LegacyMigrationRelationshipTarget(
            from_mapping_id=raw["from_mapping_id"],  # type: ignore[arg-type]
            to_mapping_id=raw["to_mapping_id"],  # type: ignore[arg-type]
            resolved_owner_user_id=raw["resolved_owner_user_id"],  # type: ignore[arg-type]
            target_space_id=raw["target_space_id"],  # type: ignore[arg-type]
            source_artifact_id=raw["source_artifact_id"],  # type: ignore[arg-type]
            source_item_id=raw["source_item_id"],  # type: ignore[arg-type]
            source_revision_id=raw["source_revision_id"],  # type: ignore[arg-type]
            target_asset_artifact_id=raw["target_asset_artifact_id"],  # type: ignore[arg-type]
            target_asset_blob_id=raw["target_asset_blob_id"],  # type: ignore[arg-type]
            embedded_asset_blob_id=raw["embedded_asset_blob_id"],  # type: ignore[arg-type]
            embedded_ordinal=raw["embedded_ordinal"],  # type: ignore[arg-type]
            embedded_logical_name_sha256=raw["embedded_logical_name_sha256"],  # type: ignore[arg-type]
            details=LegacyMigrationRelationshipDetails(
                plan_sha256=raw["plan_sha256"],  # type: ignore[arg-type]
                inventory_manifest_sha256=raw["inventory_manifest_sha256"],  # type: ignore[arg-type]
                root_binding_sha256=raw["root_binding_sha256"],  # type: ignore[arg-type]
                relationship_id=raw["relationship_id"],  # type: ignore[arg-type]
                inventory_relationship_sha256=raw["inventory_relationship_sha256"],  # type: ignore[arg-type]
                from_inventory_record_id=raw["from_inventory_record_id"],  # type: ignore[arg-type]
                to_inventory_record_id=raw["to_inventory_record_id"],  # type: ignore[arg-type]
            ),
        )
        prepared, canonical = _prepare_relationship_target(target)
    except (TypeError, ValueError, KeyError):
        raise LegacyMigrationIntegrityError("Stored relationship details are invalid.") from None
    if canonical != value:
        raise LegacyMigrationIntegrityError("Stored relationship details are not canonical.")
    return prepared


def _row_value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, TypeError, IndexError):
        raise LegacyMigrationRepositoryError("Migration row mapping failed.") from None


def _row_to_record(row: object) -> LegacyMigrationRecord:
    details_json = _row_value(row, "details_json")
    details = _details_from_json(details_json)
    try:
        created = _row_value(row, "created_at")
        updated = _row_value(row, "updated_at")
        if (
            type(created) is not int
            or created < 0
            or type(updated) is not int
            or updated != created
            or _row_value(row, "status") != "imported"
        ):
            raise ValueError("stored state is invalid")
        record = LegacyMigrationRecord(
            id=str(_uuid(_row_value(row, "id"), field="mapping_id")),
            migration_run_id=str(
                _uuid(_row_value(row, "migration_run_id"), field="migration_run_id")
            ),
            key=_prepare_key(
                LegacyMigrationKey(
                    legacy_kind=_row_value(row, "legacy_kind"),  # type: ignore[arg-type]
                    normalized_path=_row_value(row, "normalized_path"),  # type: ignore[arg-type]
                    source_sha256=_row_value(row, "source_sha256"),  # type: ignore[arg-type]
                )
            ),
            resolved_owner_user_id=_user(
                _row_value(row, "resolved_owner_user_id"), nullable=True
            ),
            artifact_id=_uuid(_row_value(row, "artifact_id"), field="artifact_id", nullable=True),
            item_id=_uuid(_row_value(row, "item_id"), field="item_id", nullable=True),
            revision_id=_uuid(_row_value(row, "revision_id"), field="revision_id", nullable=True),
            edge_id=_uuid(_row_value(row, "edge_id"), field="edge_id", nullable=True),
            status="imported",
            details_json=str(details_json),
            details=details,
            created_at_ms=created,
            updated_at_ms=updated,
        )
        _validate_record_binding(record)
        return record
    except (TypeError, ValueError):
        raise LegacyMigrationIntegrityError("Stored migration row is invalid.") from None


def _record_identity(record: LegacyMigrationRecord) -> tuple[object, ...]:
    return (
        record.id,
        record.migration_run_id,
        record.key,
        record.resolved_owner_user_id,
        record.artifact_id,
        record.item_id,
        record.revision_id,
        record.edge_id,
        record.status,
        record.details_json,
    )


def _relationship_row_to_record(row: object) -> LegacyMigrationRelationshipRecord:
    details_json = _row_value(row, "details_json")
    target = _relationship_target_from_json(details_json)
    details = target.details
    try:
        created = _row_value(row, "created_at")
        updated = _row_value(row, "updated_at")
        relationship_id = _row_value(row, "relationship_id")
        from_record_id = _row_value(row, "from_inventory_record_id")
        to_record_id = _row_value(row, "to_inventory_record_id")
        if (
            type(created) is not int
            or created < 0
            or updated != created
            or _row_value(row, "relationship_type") != "asset_reference"
            or _row_value(row, "status") != "imported"
            or not isinstance(relationship_id, str)
            or _RELATIONSHIP_ID_RE.fullmatch(relationship_id) is None
            or not isinstance(from_record_id, str)
            or _RECORD_ID_RE.fullmatch(from_record_id) is None
            or not isinstance(to_record_id, str)
            or _RECORD_ID_RE.fullmatch(to_record_id) is None
        ):
            raise ValueError("stored relationship state is invalid")
        record = LegacyMigrationRelationshipRecord(
            id=str(_uuid(_row_value(row, "id"), field="relationship_mapping_id")),
            migration_run_id=str(
                _uuid(_row_value(row, "migration_run_id"), field="migration_run_id")
            ),
            relationship_id=relationship_id,
            relationship_sha256=str(
                _sha(_row_value(row, "relationship_sha256"), field="relationship_sha256")
            ),
            relationship_type="asset_reference",
            from_inventory_record_id=from_record_id,
            to_inventory_record_id=to_record_id,
            from_mapping_id=str(
                _uuid(_row_value(row, "from_mapping_id"), field="from_mapping_id")
            ),
            to_mapping_id=str(
                _uuid(_row_value(row, "to_mapping_id"), field="to_mapping_id")
            ),
            resolved_owner_user_id=str(
                _user(_row_value(row, "resolved_owner_user_id"))
            ),
            target_space_id=str(
                _uuid(_row_value(row, "target_space_id"), field="target_space_id")
            ),
            source_artifact_id=str(
                _uuid(_row_value(row, "source_artifact_id"), field="source_artifact_id")
            ),
            source_item_id=str(
                _uuid(_row_value(row, "source_item_id"), field="source_item_id")
            ),
            source_revision_id=str(
                _uuid(_row_value(row, "source_revision_id"), field="source_revision_id")
            ),
            target_asset_artifact_id=str(
                _uuid(
                    _row_value(row, "target_asset_artifact_id"),
                    field="target_asset_artifact_id",
                )
            ),
            target_asset_blob_id=str(
                _uuid(_row_value(row, "target_asset_blob_id"), field="target_asset_blob_id")
            ),
            embedded_asset_blob_id=str(
                _uuid(
                    _row_value(row, "embedded_asset_blob_id"),
                    field="embedded_asset_blob_id",
                )
            ),
            embedded_ordinal=int(
                _nonnegative_integer(
                    _row_value(row, "embedded_ordinal"), field="embedded_ordinal"
                )
            ),
            embedded_logical_name_sha256=str(
                _sha(
                    _row_value(row, "embedded_logical_name_sha256"),
                    field="embedded_logical_name_sha256",
                )
            ),
            status="imported",
            details_json=str(details_json),
            details=details,
            created_at_ms=created,
            updated_at_ms=updated,
        )
    except (TypeError, ValueError):
        raise LegacyMigrationIntegrityError(
            "Stored migration relationship row is invalid."
        ) from None
    expected_target, canonical = _prepare_relationship_target(
        LegacyMigrationRelationshipTarget(
            from_mapping_id=record.from_mapping_id,
            to_mapping_id=record.to_mapping_id,
            resolved_owner_user_id=record.resolved_owner_user_id,
            target_space_id=record.target_space_id,
            source_artifact_id=record.source_artifact_id,
            source_item_id=record.source_item_id,
            source_revision_id=record.source_revision_id,
            target_asset_artifact_id=record.target_asset_artifact_id,
            target_asset_blob_id=record.target_asset_blob_id,
            embedded_asset_blob_id=record.embedded_asset_blob_id,
            embedded_ordinal=record.embedded_ordinal,
            embedded_logical_name_sha256=record.embedded_logical_name_sha256,
            details=record.details,
        )
    )
    if (
        canonical != record.details_json
        or expected_target.details != details
        or record.relationship_id != details.relationship_id
        or record.relationship_sha256 != details.inventory_relationship_sha256
        or record.from_inventory_record_id != details.from_inventory_record_id
        or record.to_inventory_record_id != details.to_inventory_record_id
        or record.id
        != migration_relationship_mapping_id_for_plan(
            details.plan_sha256, details.relationship_id
        )
        or record.migration_run_id != migration_run_id_for_plan(details.plan_sha256)
    ):
        raise LegacyMigrationIntegrityError(
            "The migration relationship is not bound to its reviewed identity."
        )
    return record


def _relationship_record_identity(
    record: LegacyMigrationRelationshipRecord,
) -> tuple[object, ...]:
    return (
        record.id,
        record.migration_run_id,
        record.relationship_id,
        record.relationship_sha256,
        record.from_inventory_record_id,
        record.to_inventory_record_id,
        record.from_mapping_id,
        record.to_mapping_id,
        record.resolved_owner_user_id,
        record.target_space_id,
        record.source_artifact_id,
        record.source_item_id,
        record.source_revision_id,
        record.target_asset_artifact_id,
        record.target_asset_blob_id,
        record.embedded_asset_blob_id,
        record.embedded_ordinal,
        record.embedded_logical_name_sha256,
        record.details_json,
    )


def _validate_record_binding(record: LegacyMigrationRecord) -> None:
    details = record.details
    expected_locator = (
        f"p2v1/{details.root_id}/{details.path_key_sha256}/"
        f"{details.record_kind}/{details.source_ordinal}"
    )
    try:
        prepared_target, canonical_details = _prepare_target(
            LegacyMigrationTarget(
                resolved_owner_user_id=record.resolved_owner_user_id,
                artifact_id=record.artifact_id,
                item_id=record.item_id,
                revision_id=record.revision_id,
                edge_id=record.edge_id,
                details=record.details,
            )
        )
    except (TypeError, ValueError) as exc:
        raise LegacyMigrationIntegrityError(
            "The stored migration target shape is invalid."
        ) from exc
    if canonical_details != record.details_json or prepared_target.details != details:
        raise LegacyMigrationIntegrityError(
            "The stored migration details are not bound canonically."
        )
    if (
        record.key.legacy_kind != details.category
        or record.key.normalized_path != expected_locator
        or details.source_record_sha256 != record.key.source_sha256
        or (
            details.import_mode in {"personal_asset", "personal_item"}
            and details.blob_sha256 != record.key.source_sha256
        )
        or record.id
        != migration_mapping_id_for_record(
            details.plan_sha256, details.inventory_record_id
        )
        or record.migration_run_id != migration_run_id_for_plan(details.plan_sha256)
    ):
        raise LegacyMigrationIntegrityError(
            "The migration mapping is not bound to its reviewed source identity."
        )


def _markdown_body_matches(
    row: object,
    *,
    raw_sha256: str,
    normalized_sha256: str,
    size_bytes: int,
) -> bool:
    try:
        content = row["content_md"]  # type: ignore[index]
        content_blob_hash = row["content_blob_hash"]  # type: ignore[index]
        stored_size = row["content_size_bytes"]  # type: ignore[index]
        stored_sha256 = row["content_sha256"]  # type: ignore[index]
    except (KeyError, TypeError, IndexError):
        return False
    if content is None:
        return (
            content_blob_hash == raw_sha256
            and stored_size == size_bytes
            and stored_sha256 == raw_sha256
        )
    if not isinstance(content, str) or content_blob_hash is not None:
        return False
    try:
        raw = content.encode("utf-8")
        normalized = content.replace("\r\n", "\n").replace("\r", "\n").encode(
            "utf-8"
        )
    except UnicodeEncodeError:
        return False
    return (
        stored_size == size_bytes
        and stored_sha256 == raw_sha256
        and len(raw) == size_bytes
        and hashlib.sha256(raw).hexdigest() == raw_sha256
        and hashlib.sha256(normalized).hexdigest() == normalized_sha256
    )


class LegacyMigrationRepository:
    """Insert-once canonical effect mappings for reviewed P2.3 batches."""

    __slots__ = ()

    @staticmethod
    def _connection(connection: ManagedConnection) -> ManagedConnection:
        if not isinstance(connection, ManagedConnection):
            raise TypeError("LegacyMigrationRepository requires a ManagedConnection.")
        return connection

    @classmethod
    def _write_connection(cls, connection: ManagedConnection) -> ManagedConnection:
        connection = cls._connection(connection)
        if not connection.in_transaction:
            raise KnowledgeCoreTransactionError(
                "Migration mapping writes require an existing managed transaction."
            )
        return connection

    def get_by_id(
        self, connection: ManagedConnection, mapping_id: str
    ) -> LegacyMigrationRecord | None:
        connection = self._connection(connection)
        mapping_id = str(_uuid(mapping_id, field="mapping_id"))
        row = connection.execute(
            f"SELECT {_COLUMNS} FROM legacy_migration_map WHERE id=?",
            (mapping_id,),
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def get_by_key(
        self, connection: ManagedConnection, key: LegacyMigrationKey
    ) -> LegacyMigrationRecord | None:
        connection = self._connection(connection)
        key = _prepare_key(key)
        row = connection.execute(
            f"""SELECT {_COLUMNS} FROM legacy_migration_map
                WHERE legacy_kind=? AND normalized_path=? AND source_sha256=?""",
            (key.legacy_kind, key.normalized_path, key.source_sha256),
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def get_by_locator(
        self, connection: ManagedConnection, normalized_path: str
    ) -> LegacyMigrationRecord | None:
        """Return the sole mapping for a stable source locator, if one exists.

        The v1 DDL unique key also contains category and source SHA.  P2.3 is
        intentionally stricter: a reviewed update cannot reinterpret the same
        stable source locator or changed bytes as a second canonical effect.
        """

        connection = self._connection(connection)
        normalized_path = _text(
            normalized_path, field="normalized_path", maximum=1000
        )
        rows = connection.execute(
            f"""SELECT {_COLUMNS} FROM legacy_migration_map
                WHERE normalized_path=? ORDER BY id""",
            (normalized_path,),
        ).fetchall()
        if len(rows) > 1:
            raise LegacyMigrationIntegrityError(
                "More than one mapping exists for the stable source locator."
            )
        return None if not rows else _row_to_record(rows[0])

    def require_by_key(
        self, connection: ManagedConnection, key: LegacyMigrationKey
    ) -> LegacyMigrationRecord:
        record = self.get_by_key(connection, key)
        if record is None:
            raise LegacyMigrationNotFoundError("The migration mapping does not exist.")
        return record

    def put_imported(
        self,
        connection: ManagedConnection,
        *,
        mapping_id: str,
        migration_run_id: str,
        source_path_key: str | None = None,
        key: LegacyMigrationKey,
        target: LegacyMigrationTarget,
        verified_blob_evidence: VerifiedBlobEvidence | None = None,
        now_ms: int,
    ) -> LegacyMigrationInsertResult:
        connection = self._write_connection(connection)
        mapping_id = str(_uuid(mapping_id, field="mapping_id"))
        migration_run_id = str(_uuid(migration_run_id, field="migration_run_id"))
        key = _prepare_key(key)
        target, details_json = _prepare_target(target)
        verified_blob_evidence = _prepare_verified_blob_evidence(
            verified_blob_evidence,
            details=target.details,
        )
        now_ms = _utc_ms(now_ms)
        effective_path_key = (
            source_path_key if source_path_key is not None else target.source_path_key
        )
        if effective_path_key is None:
            raise ValueError("The canonical reviewed path key is required.")
        reviewed_locator = migration_source_locator(
            root_id=target.details.root_id,
            path_key=effective_path_key,
            record_kind=target.details.record_kind,
            source_ordinal=target.details.source_ordinal,
        )
        reviewed_path_sha256 = hashlib.sha256(
            effective_path_key.encode("utf-8")
        ).hexdigest()
        if (
            key.normalized_path != reviewed_locator
            or target.details.path_key_sha256 != reviewed_path_sha256
        ):
            raise ValueError(
                "The migration locator must match the canonical reviewed path key."
            )
        expected = (
            mapping_id,
            migration_run_id,
            key,
            target.resolved_owner_user_id,
            target.artifact_id,
            target.item_id,
            target.revision_id,
            target.edge_id,
            "imported",
            details_json,
        )
        existing = self.get_by_locator(connection, key.normalized_path)
        if existing is not None:
            if existing.key != key or _record_identity(existing) != expected:
                raise LegacyMigrationConflictError(
                    "The source identity is already mapped to a different effect."
                )
            self.verify_target_integrity(
                connection,
                existing,
                verified_blob_evidence=verified_blob_evidence,
            )
            return LegacyMigrationInsertResult(existing, False)

        if (
            key.legacy_kind != target.details.category
            or key.normalized_path != reviewed_locator
            or target.details.source_record_sha256 != key.source_sha256
            or (
                target.details.import_mode in {"personal_asset", "personal_item"}
                and target.details.blob_sha256 != key.source_sha256
            )
            or mapping_id
            != migration_mapping_id_for_record(
                target.details.plan_sha256,
                target.details.inventory_record_id,
            )
            or migration_run_id
            != migration_run_id_for_plan(target.details.plan_sha256)
        ):
            raise ValueError(
                "The migration key and deterministic IDs must match the reviewed record."
            )

        if target.details.import_mode == "user_account":
            closed_parent = connection.execute(
                """SELECT 1 FROM legacy_migration_map
                   WHERE migration_run_id=?
                     AND legacy_kind='identity.users_registry'
                     AND status='imported'
                     AND json_extract(details_json,'$.import_mode')='source_container'
                     AND json_extract(details_json,'$.plan_sha256')=?
                     AND json_extract(details_json,'$.inventory_record_id')=?
                   LIMIT 1""",
                (
                    migration_run_id,
                    target.details.plan_sha256,
                    target.details.parent_inventory_record_id,
                ),
            ).fetchone()
            if closed_parent is not None:
                raise LegacyMigrationConflictError(
                    "The registry source container is already dependency-closed."
                )

        # The mapping row becomes immutable in physical schema version 3.
        # Prove the complete canonical effect before inserting it so a caller
        # that handles the exception inside its outer transaction cannot
        # accidentally commit an unverifiable checkpoint.
        transient = LegacyMigrationRecord(
            id=mapping_id,
            migration_run_id=migration_run_id,
            key=key,
            resolved_owner_user_id=target.resolved_owner_user_id,
            artifact_id=target.artifact_id,
            item_id=target.item_id,
            revision_id=target.revision_id,
            edge_id=target.edge_id,
            status="imported",
            details_json=details_json,
            details=target.details,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        self._verify_effect_rows(
            connection,
            transient,
            verified_blob_evidence=verified_blob_evidence,
        )

        try:
            cursor = connection.execute(
                """INSERT INTO legacy_migration_map(
                     id,migration_run_id,legacy_kind,normalized_path,source_sha256,
                     resolved_owner_user_id,artifact_id,item_id,revision_id,edge_id,
                     status,details_json,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,'imported',?,?,?)
                   ON CONFLICT DO NOTHING""",
                (
                    mapping_id,
                    migration_run_id,
                    key.legacy_kind,
                    key.normalized_path,
                    key.source_sha256,
                    target.resolved_owner_user_id,
                    target.artifact_id,
                    target.item_id,
                    target.revision_id,
                    target.edge_id,
                    details_json,
                    now_ms,
                    now_ms,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyMigrationConflictError(
                "The imported mapping violates canonical target constraints."
            ) from exc
        if cursor.rowcount != 1:
            raise LegacyMigrationConflictError(
                "The mapping ID or source identity is already bound incompatibly."
            )

        # The complete target effect was verified immediately before the
        # insert in this same caller-owned write transaction.  Re-running an
        # external physical verifier after the immutable row exists would
        # create a catch-and-commit window if that second observation failed.
        return LegacyMigrationInsertResult(transient, True)

    def list_for_run(
        self, connection: ManagedConnection, migration_run_id: str
    ) -> tuple[LegacyMigrationRecord, ...]:
        connection = self._connection(connection)
        migration_run_id = str(_uuid(migration_run_id, field="migration_run_id"))
        rows = connection.execute(
            f"""SELECT {_COLUMNS} FROM legacy_migration_map
                WHERE migration_run_id=? AND status='imported'
                ORDER BY normalized_path,source_sha256,id""",
            (migration_run_id,),
        ).fetchall()
        return tuple(_row_to_record(row) for row in rows)

    def verify_target_integrity(
        self,
        connection: ManagedConnection,
        record: LegacyMigrationRecord,
        *,
        verified_blob_evidence: VerifiedBlobEvidence | None = None,
    ) -> LegacyMigrationRecord:
        connection = self._connection(connection)
        if not isinstance(record, LegacyMigrationRecord):
            raise TypeError("A LegacyMigrationRecord is required.")
        mode = record.details.import_mode
        _validate_record_binding(record)
        stored = self.get_by_id(connection, record.id)
        if stored is None or stored != record:
            raise LegacyMigrationIntegrityError(
                "The supplied migration record is not the exact durable mapping."
            )
        verified_blob_evidence = _prepare_verified_blob_evidence(
            verified_blob_evidence,
            details=record.details,
        )
        self._verify_effect_rows(
            connection,
            record,
            verified_blob_evidence=verified_blob_evidence,
        )
        return record

    def _verify_effect_rows(
        self,
        connection: ManagedConnection,
        record: LegacyMigrationRecord,
        *,
        verified_blob_evidence: VerifiedBlobEvidence | None,
    ) -> None:
        mode = record.details.import_mode
        if mode == "source_container":
            rows = connection.execute(
                f"""SELECT {_COLUMNS} FROM legacy_migration_map
                    WHERE migration_run_id=?
                      AND legacy_kind='identity.users_registry'
                      AND status='imported'
                      AND json_extract(details_json,'$.import_mode')='user_account'
                      AND json_extract(details_json,'$.plan_sha256')=?
                      AND json_extract(details_json,'$.parent_inventory_record_id')=?
                    ORDER BY id""",
                (
                    record.migration_run_id,
                    record.details.plan_sha256,
                    record.details.inventory_record_id,
                ),
            ).fetchall()
            child_rows = tuple(_row_to_record(row) for row in rows)
            bindings = tuple(
                (
                    child.id,
                    child.details.inventory_record_id,
                    str(child.details.source_record_sha256),
                )
                for child in child_rows
            )
            if (
                len(child_rows) != record.details.planned_import_child_count
                or migration_child_bindings_sha256(bindings)
                != record.details.imported_child_bindings_sha256
            ):
                raise LegacyMigrationIntegrityError(
                    "The source container does not have its exact imported child set."
                )
            for child in child_rows:
                self._verify_effect_rows(
                    connection,
                    child,
                    verified_blob_evidence=None,
                )
            return
        owner = record.resolved_owner_user_id
        if owner is None or connection.execute(
            "SELECT 1 FROM app_user WHERE id=?", (owner,)
        ).fetchone() is None:
            raise LegacyMigrationIntegrityError("The mapped canonical owner is missing.")
        space = connection.execute(
            """SELECT id FROM knowledge_space
               WHERE id=? AND space_kind='personal' AND owner_user_id=?""",
            (record.details.target_space_id, owner),
        ).fetchone()
        if space is None:
            raise LegacyMigrationIntegrityError("The mapped personal space is invalid.")
        if mode == "user_account":
            # Before the append-only mapping is inserted, the current row must
            # still be the exact normalized legacy import projection.  On
            # replay, that projection is historical evidence: later account
            # password/role/status changes must not invalidate provenance.
            if self.get_by_id(connection, record.id) is None:
                user_row = connection.execute(
                    """SELECT id,display_name,password_hash,system_role,
                              account_status,current_part_id,scope_epoch,
                              must_change_password,password_changed_at,
                              created_at,updated_at,disabled_at,row_version
                       FROM app_user WHERE id=?""",
                    (owner,),
                ).fetchone()
                if user_row is None:
                    raise LegacyMigrationIntegrityError(
                        "The mapped canonical account is missing."
                    )
                projection_payload = {
                    "account_status": user_row["account_status"],
                    "created_at_ms": user_row["created_at"],
                    "current_part_id": user_row["current_part_id"],
                    "disabled_at_ms": user_row["disabled_at"],
                    "display_name": user_row["display_name"],
                    "id": user_row["id"],
                    "must_change_password": bool(user_row["must_change_password"]),
                    "password_changed_at_ms": user_row["password_changed_at"],
                    "password_hash": user_row["password_hash"],
                    "row_version": user_row["row_version"],
                    "scope_epoch": user_row["scope_epoch"],
                    "system_role": user_row["system_role"],
                    "updated_at_ms": user_row["updated_at"],
                }
                encoded = json.dumps(
                    projection_payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                if (
                    record.details.account_projection_schema
                    != "legacy_user_import_v1"
                    or hashlib.sha256(encoded).hexdigest()
                    != record.details.account_projection_sha256
                ):
                    raise LegacyMigrationIntegrityError(
                        "The canonical account does not match its reviewed import projection."
                    )
                membership = connection.execute(
                    """SELECT id,part_id FROM org_membership
                       WHERE user_id=? AND valid_to IS NULL""",
                    (owner,),
                ).fetchone()
                if record.details.assigned_part_id is None:
                    if membership is not None:
                        raise LegacyMigrationIntegrityError(
                            "The imported account has an unexpected active membership."
                        )
                elif (
                    membership is None
                    or membership["id"] != record.details.membership_id
                    or membership["part_id"] != record.details.assigned_part_id
                ):
                    raise LegacyMigrationIntegrityError(
                        "The imported account membership is not exact."
                    )
                leader = connection.execute(
                    """SELECT id,org_unit_id FROM org_role_assignment
                       WHERE user_id=? AND role='part_leader' AND valid_to IS NULL""",
                    (owner,),
                ).fetchone()
                if record.details.leader_assignment_id is None:
                    if leader is not None:
                        raise LegacyMigrationIntegrityError(
                            "The imported account has an unexpected leader assignment."
                        )
                elif (
                    leader is None
                    or leader["id"] != record.details.leader_assignment_id
                    or leader["org_unit_id"] != record.details.assigned_part_id
                ):
                    raise LegacyMigrationIntegrityError(
                        "The imported account leader assignment is not exact."
                    )
            return
        artifact = connection.execute(
            """SELECT origin_key,origin_revision_key,raw_size_bytes,media_type,
                      body_retention_status
               FROM source_artifact
               WHERE id=? AND origin_type='legacy' AND publisher_user_id=?
                 AND raw_sha256=?""",
            (record.artifact_id, owner, record.key.source_sha256),
        ).fetchone()
        blob = connection.execute(
            """SELECT manifest.size_bytes AS manifest_size_bytes,
                      manifest.media_type AS manifest_media_type,
                      manifest.body_retention_status AS manifest_retention,
                      body.content_sha256 AS body_sha256,
                      blob.size_bytes AS blob_size_bytes,
                      blob.media_type AS blob_media_type,
                      blob.relative_path AS blob_relative_path,
                      blob.verification_status AS blob_status,
                      blob.verified_at AS blob_verified_at
               FROM artifact_blob AS manifest
               JOIN artifact_blob_body AS body ON body.artifact_blob_id=manifest.id
               JOIN blob_object AS blob ON blob.sha256=body.content_sha256
               WHERE manifest.id=? AND manifest.artifact_id=?
                 AND manifest.role='raw' AND manifest.ordinal=0
                 AND manifest.content_sha256=? AND body.content_sha256=?
                 AND blob.verification_status='verified'""",
            (
                record.details.artifact_blob_id,
                record.artifact_id,
                record.key.source_sha256,
                record.key.source_sha256,
            ),
        ).fetchone()
        expected_size = record.details.source_size_bytes
        expected_media_type = record.details.source_media_type
        expected_blob_path = (
            f"{record.key.source_sha256[:2]}/{record.key.source_sha256[2:4]}/"
            f"{record.key.source_sha256}"
        )
        if (
            artifact is None
            or blob is None
            or expected_size is None
            or expected_media_type is None
            or artifact["origin_key"] != record.key.normalized_path
            or artifact["origin_revision_key"] != record.details.inventory_record_id
            or artifact["raw_size_bytes"] != expected_size
            or artifact["media_type"] != expected_media_type
            or artifact["body_retention_status"] != "present"
            or blob["manifest_size_bytes"] != expected_size
            or blob["manifest_media_type"] != expected_media_type
            or blob["manifest_retention"] != "present"
            or blob["body_sha256"] != record.key.source_sha256
            or blob["blob_size_bytes"] != expected_size
            or blob["blob_media_type"] != CAS_BLOB_MEDIA_TYPE
            or blob["blob_relative_path"] != expected_blob_path
            or blob["blob_status"] != "verified"
            or type(blob["blob_verified_at"]) is not int
        ):
            raise LegacyMigrationIntegrityError("The mapped source artifact is invalid.")
        if verified_blob_evidence is None:
            raise LegacyMigrationIntegrityError(
                "The mapped source blob lacks pre-transaction physical evidence."
            )
        if mode == "personal_asset":
            return
        item = connection.execute(
            """SELECT 1 FROM knowledge_item
               WHERE id=? AND space_id=?""",
            (record.item_id, record.details.target_space_id),
        ).fetchone()
        revision = connection.execute(
            """SELECT revision.change_kind,revision.created_by,
                      body.content_md,body.content_blob_hash,
                      body.content_size_bytes,body.content_sha256
               FROM knowledge_revision AS revision
               JOIN knowledge_revision_body AS body
                 ON body.revision_id=revision.id AND body.item_id=revision.item_id
               WHERE revision.id=? AND revision.item_id=?
                 AND revision.raw_content_sha256=?
                 AND revision.normalized_content_sha256=?
                 AND revision.normalizer_version=?
                 AND body.content_sha256=revision.raw_content_sha256
                 AND body.content_size_bytes=revision.content_size_bytes""",
            (
                record.revision_id,
                record.item_id,
                record.key.source_sha256,
                record.details.normalized_content_sha256,
                record.details.normalizer_version,
            ),
        ).fetchone()
        extraction = connection.execute(
            """SELECT body.content_md,body.content_blob_hash,
                      body.content_size_bytes,body.content_sha256
               FROM extraction_snapshot AS snapshot
               JOIN extraction_snapshot_body AS body
                 ON body.snapshot_id=snapshot.id AND body.artifact_id=snapshot.artifact_id
               WHERE snapshot.id=? AND snapshot.artifact_id=? AND snapshot.status='ready'
                 AND snapshot.extracted_sha256=?
                 AND snapshot.normalized_content_sha256=?
                 AND snapshot.normalizer_version=?
                 AND body.content_sha256=snapshot.extracted_sha256
                 AND body.content_size_bytes=snapshot.extracted_size_bytes""",
            (
                record.details.extraction_snapshot_id,
                record.artifact_id,
                record.key.source_sha256,
                record.details.normalized_content_sha256,
                record.details.normalizer_version,
            ),
        ).fetchone()
        source = connection.execute(
            """SELECT 1 FROM item_source
               WHERE id=? AND item_id=? AND revision_id=? AND artifact_id=?
                 AND extraction_snapshot_id=? AND role='migrated'
                 AND created_by=?""",
            (
                record.details.item_source_id,
                record.item_id,
                record.revision_id,
                record.artifact_id,
                record.details.extraction_snapshot_id,
                owner,
            ),
        ).fetchone()
        if (
            item is None
            or revision is None
            or extraction is None
            or source is None
            or (
                record.details.result_disposition == "created"
                and (
                    revision["change_kind"] != "import"
                    or revision["created_by"] != owner
                )
            )
            or not _markdown_body_matches(
                revision,
                raw_sha256=record.key.source_sha256,
                normalized_sha256=str(record.details.normalized_content_sha256),
                size_bytes=expected_size,
            )
            or not _markdown_body_matches(
                extraction,
                raw_sha256=record.key.source_sha256,
                normalized_sha256=str(record.details.normalized_content_sha256),
                size_bytes=expected_size,
            )
        ):
            raise LegacyMigrationIntegrityError("The mapped personal item is invalid.")


class LegacyMigrationRelationshipRepository:
    """Immutable checkpoints for reviewed P2.3 asset-reference effects."""

    __slots__ = ()

    @staticmethod
    def _connection(connection: ManagedConnection) -> ManagedConnection:
        if not isinstance(connection, ManagedConnection):
            raise TypeError(
                "LegacyMigrationRelationshipRepository requires a ManagedConnection."
            )
        return connection

    @classmethod
    def _write_connection(cls, connection: ManagedConnection) -> ManagedConnection:
        connection = cls._connection(connection)
        if not connection.in_transaction:
            raise KnowledgeCoreTransactionError(
                "Migration relationship writes require an existing managed transaction."
            )
        return connection

    def get_by_id(
        self, connection: ManagedConnection, relationship_mapping_id: str
    ) -> LegacyMigrationRelationshipRecord | None:
        connection = self._connection(connection)
        relationship_mapping_id = str(
            _uuid(relationship_mapping_id, field="relationship_mapping_id")
        )
        row = connection.execute(
            f"SELECT {_RELATIONSHIP_COLUMNS} FROM legacy_migration_relationship_map WHERE id=?",
            (relationship_mapping_id,),
        ).fetchone()
        return None if row is None else _relationship_row_to_record(row)

    def get_by_relationship_id(
        self, connection: ManagedConnection, relationship_id: str
    ) -> LegacyMigrationRelationshipRecord | None:
        connection = self._connection(connection)
        if (
            not isinstance(relationship_id, str)
            or _RELATIONSHIP_ID_RE.fullmatch(relationship_id) is None
        ):
            raise ValueError("relationship_id is invalid.")
        row = connection.execute(
            f"""SELECT {_RELATIONSHIP_COLUMNS}
                FROM legacy_migration_relationship_map WHERE relationship_id=?""",
            (relationship_id,),
        ).fetchone()
        return None if row is None else _relationship_row_to_record(row)

    def list_for_run(
        self, connection: ManagedConnection, migration_run_id: str
    ) -> tuple[LegacyMigrationRelationshipRecord, ...]:
        connection = self._connection(connection)
        migration_run_id = str(_uuid(migration_run_id, field="migration_run_id"))
        rows = connection.execute(
            f"""SELECT {_RELATIONSHIP_COLUMNS}
                FROM legacy_migration_relationship_map
                WHERE migration_run_id=? AND status='imported'
                ORDER BY relationship_id,id""",
            (migration_run_id,),
        ).fetchall()
        return tuple(_relationship_row_to_record(row) for row in rows)

    @staticmethod
    def _endpoint_records(
        connection: ManagedConnection,
        target: LegacyMigrationRelationshipTarget,
    ) -> tuple[LegacyMigrationRecord, LegacyMigrationRecord]:
        records = LegacyMigrationRepository()
        source = records.get_by_id(connection, target.from_mapping_id)
        asset = records.get_by_id(connection, target.to_mapping_id)
        if source is None or asset is None:
            raise LegacyMigrationIntegrityError(
                "An asset-reference endpoint mapping is missing."
            )
        return source, asset

    def _verify_effect_rows(
        self,
        connection: ManagedConnection,
        record: LegacyMigrationRelationshipRecord,
        *,
        source_verified_blob_evidence: VerifiedBlobEvidence,
        target_verified_blob_evidence: VerifiedBlobEvidence,
    ) -> None:
        target = LegacyMigrationRelationshipTarget(
            from_mapping_id=record.from_mapping_id,
            to_mapping_id=record.to_mapping_id,
            resolved_owner_user_id=record.resolved_owner_user_id,
            target_space_id=record.target_space_id,
            source_artifact_id=record.source_artifact_id,
            source_item_id=record.source_item_id,
            source_revision_id=record.source_revision_id,
            target_asset_artifact_id=record.target_asset_artifact_id,
            target_asset_blob_id=record.target_asset_blob_id,
            embedded_asset_blob_id=record.embedded_asset_blob_id,
            embedded_ordinal=record.embedded_ordinal,
            embedded_logical_name_sha256=record.embedded_logical_name_sha256,
            details=record.details,
        )
        source, asset = self._endpoint_records(connection, target)
        if (
            source.migration_run_id != record.migration_run_id
            or asset.migration_run_id != record.migration_run_id
            or source.details.plan_sha256 != record.details.plan_sha256
            or asset.details.plan_sha256 != record.details.plan_sha256
            or source.details.inventory_manifest_sha256
            != record.details.inventory_manifest_sha256
            or asset.details.inventory_manifest_sha256
            != record.details.inventory_manifest_sha256
            or source.details.root_binding_sha256
            != record.details.root_binding_sha256
            or asset.details.root_binding_sha256
            != record.details.root_binding_sha256
            or source.details.inventory_record_id
            != record.from_inventory_record_id
            or asset.details.inventory_record_id != record.to_inventory_record_id
            or source.details.import_mode != "personal_item"
            or asset.details.import_mode != "personal_asset"
            or source.resolved_owner_user_id != record.resolved_owner_user_id
            or asset.resolved_owner_user_id != record.resolved_owner_user_id
            or source.details.target_space_id != record.target_space_id
            or asset.details.target_space_id != record.target_space_id
            or source.artifact_id != record.source_artifact_id
            or source.item_id != record.source_item_id
            or source.revision_id != record.source_revision_id
            or asset.artifact_id != record.target_asset_artifact_id
            or asset.details.artifact_blob_id != record.target_asset_blob_id
        ):
            raise LegacyMigrationIntegrityError(
                "The asset-reference endpoint mappings are not exact."
            )
        source_evidence = _prepare_verified_blob_evidence(
            source_verified_blob_evidence, details=source.details
        )
        asset_evidence = _prepare_verified_blob_evidence(
            target_verified_blob_evidence, details=asset.details
        )
        records = LegacyMigrationRepository()
        records._verify_effect_rows(
            connection,
            source,
            verified_blob_evidence=source_evidence,
        )
        records._verify_effect_rows(
            connection,
            asset,
            verified_blob_evidence=asset_evidence,
        )
        embedded = connection.execute(
            """SELECT manifest.artifact_id,manifest.content_sha256,
                      manifest.size_bytes,manifest.media_type,manifest.role,
                      manifest.logical_name,manifest.ordinal,
                      manifest.body_retention_status,
                      body.content_sha256 AS body_sha256,
                      blob.size_bytes AS blob_size_bytes,
                      blob.media_type AS blob_media_type,
                      blob.relative_path AS blob_relative_path,
                      blob.verification_status AS blob_status,
                      blob.verified_at AS blob_verified_at
               FROM artifact_blob AS manifest
               JOIN artifact_blob_body AS body
                 ON body.artifact_blob_id=manifest.id
               JOIN blob_object AS blob ON blob.sha256=body.content_sha256
               WHERE manifest.id=?""",
            (record.embedded_asset_blob_id,),
        ).fetchone()
        asset_sha = asset.key.source_sha256
        expected_path = f"{asset_sha[:2]}/{asset_sha[2:4]}/{asset_sha}"
        try:
            logical_name_sha256 = (
                hashlib.sha256(embedded["logical_name"].encode("utf-8")).hexdigest()
                if embedded is not None
                and isinstance(embedded["logical_name"], str)
                else None
            )
        except UnicodeEncodeError:
            logical_name_sha256 = None
        if (
            embedded is None
            or embedded["artifact_id"] != record.source_artifact_id
            or embedded["content_sha256"] != asset_sha
            or embedded["size_bytes"] != asset.details.source_size_bytes
            or embedded["media_type"] != asset.details.source_media_type
            or embedded["role"] != "embedded_asset"
            or embedded["ordinal"] != record.embedded_ordinal
            or embedded["body_retention_status"] != "present"
            or embedded["body_sha256"] != asset_sha
            or embedded["blob_size_bytes"] != asset.details.source_size_bytes
            or embedded["blob_media_type"] != CAS_BLOB_MEDIA_TYPE
            or embedded["blob_relative_path"] != expected_path
            or embedded["blob_status"] != "verified"
            or type(embedded["blob_verified_at"]) is not int
            or logical_name_sha256 != record.embedded_logical_name_sha256
        ):
            raise LegacyMigrationIntegrityError(
                "The embedded asset effect is not bound to the reviewed relationship."
            )

    def verify_target_integrity(
        self,
        connection: ManagedConnection,
        record: LegacyMigrationRelationshipRecord,
        *,
        source_verified_blob_evidence: VerifiedBlobEvidence,
        target_verified_blob_evidence: VerifiedBlobEvidence,
    ) -> LegacyMigrationRelationshipRecord:
        connection = self._connection(connection)
        if not isinstance(record, LegacyMigrationRelationshipRecord):
            raise TypeError("A LegacyMigrationRelationshipRecord is required.")
        stored = self.get_by_id(connection, record.id)
        if stored is None or stored != record:
            raise LegacyMigrationIntegrityError(
                "The supplied relationship is not the exact durable mapping."
            )
        self._verify_effect_rows(
            connection,
            record,
            source_verified_blob_evidence=source_verified_blob_evidence,
            target_verified_blob_evidence=target_verified_blob_evidence,
        )
        return record

    def put_imported(
        self,
        connection: ManagedConnection,
        *,
        relationship_mapping_id: str,
        migration_run_id: str,
        relationship_id: str,
        relationship_sha256: str,
        target: LegacyMigrationRelationshipTarget,
        source_verified_blob_evidence: VerifiedBlobEvidence,
        target_verified_blob_evidence: VerifiedBlobEvidence,
        now_ms: int,
    ) -> LegacyMigrationRelationshipInsertResult:
        connection = self._write_connection(connection)
        relationship_mapping_id = str(
            _uuid(relationship_mapping_id, field="relationship_mapping_id")
        )
        migration_run_id = str(_uuid(migration_run_id, field="migration_run_id"))
        if (
            not isinstance(relationship_id, str)
            or _RELATIONSHIP_ID_RE.fullmatch(relationship_id) is None
        ):
            raise ValueError("relationship_id is invalid.")
        relationship_sha256 = str(
            _sha(relationship_sha256, field="relationship_sha256")
        )
        target, details_json = _prepare_relationship_target(target)
        now_ms = _utc_ms(now_ms)
        if (
            relationship_id != target.details.relationship_id
            or relationship_sha256
            != target.details.inventory_relationship_sha256
            or relationship_mapping_id
            != migration_relationship_mapping_id_for_plan(
                target.details.plan_sha256, relationship_id
            )
            or migration_run_id
            != migration_run_id_for_plan(target.details.plan_sha256)
        ):
            raise ValueError(
                "The relationship IDs and hashes must match the reviewed relationship."
            )
        expected = (
            relationship_mapping_id,
            migration_run_id,
            relationship_id,
            relationship_sha256,
            target.details.from_inventory_record_id,
            target.details.to_inventory_record_id,
            target.from_mapping_id,
            target.to_mapping_id,
            target.resolved_owner_user_id,
            target.target_space_id,
            target.source_artifact_id,
            target.source_item_id,
            target.source_revision_id,
            target.target_asset_artifact_id,
            target.target_asset_blob_id,
            target.embedded_asset_blob_id,
            target.embedded_ordinal,
            target.embedded_logical_name_sha256,
            details_json,
        )
        existing = self.get_by_relationship_id(connection, relationship_id)
        if existing is not None:
            if _relationship_record_identity(existing) != expected:
                raise LegacyMigrationConflictError(
                    "The reviewed relationship is already mapped incompatibly."
                )
            self.verify_target_integrity(
                connection,
                existing,
                source_verified_blob_evidence=source_verified_blob_evidence,
                target_verified_blob_evidence=target_verified_blob_evidence,
            )
            return LegacyMigrationRelationshipInsertResult(existing, False)
        transient = LegacyMigrationRelationshipRecord(
            id=relationship_mapping_id,
            migration_run_id=migration_run_id,
            relationship_id=relationship_id,
            relationship_sha256=relationship_sha256,
            relationship_type="asset_reference",
            from_inventory_record_id=target.details.from_inventory_record_id,
            to_inventory_record_id=target.details.to_inventory_record_id,
            from_mapping_id=target.from_mapping_id,
            to_mapping_id=target.to_mapping_id,
            resolved_owner_user_id=target.resolved_owner_user_id,
            target_space_id=target.target_space_id,
            source_artifact_id=target.source_artifact_id,
            source_item_id=target.source_item_id,
            source_revision_id=target.source_revision_id,
            target_asset_artifact_id=target.target_asset_artifact_id,
            target_asset_blob_id=target.target_asset_blob_id,
            embedded_asset_blob_id=target.embedded_asset_blob_id,
            embedded_ordinal=target.embedded_ordinal,
            embedded_logical_name_sha256=target.embedded_logical_name_sha256,
            status="imported",
            details_json=details_json,
            details=target.details,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        self._verify_effect_rows(
            connection,
            transient,
            source_verified_blob_evidence=source_verified_blob_evidence,
            target_verified_blob_evidence=target_verified_blob_evidence,
        )
        try:
            cursor = connection.execute(
                """INSERT INTO legacy_migration_relationship_map(
                     id,migration_run_id,relationship_id,relationship_sha256,
                     relationship_type,from_inventory_record_id,to_inventory_record_id,
                     from_mapping_id,to_mapping_id,resolved_owner_user_id,target_space_id,
                     source_artifact_id,source_item_id,source_revision_id,
                     target_asset_artifact_id,target_asset_blob_id,embedded_asset_blob_id,
                     embedded_ordinal,embedded_logical_name_sha256,status,details_json,
                     created_at,updated_at
                   ) VALUES (?,?,?,?,'asset_reference',?,?,?,?,?,?,?,?,?,?,?,?,?,?,'imported',?,?,?)
                   ON CONFLICT DO NOTHING""",
                (
                    relationship_mapping_id,
                    migration_run_id,
                    relationship_id,
                    relationship_sha256,
                    target.details.from_inventory_record_id,
                    target.details.to_inventory_record_id,
                    target.from_mapping_id,
                    target.to_mapping_id,
                    target.resolved_owner_user_id,
                    target.target_space_id,
                    target.source_artifact_id,
                    target.source_item_id,
                    target.source_revision_id,
                    target.target_asset_artifact_id,
                    target.target_asset_blob_id,
                    target.embedded_asset_blob_id,
                    target.embedded_ordinal,
                    target.embedded_logical_name_sha256,
                    details_json,
                    now_ms,
                    now_ms,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyMigrationConflictError(
                "The imported relationship violates canonical target constraints."
            ) from exc
        if cursor.rowcount != 1:
            raise LegacyMigrationConflictError(
                "The relationship identity is already bound incompatibly."
            )
        return LegacyMigrationRelationshipInsertResult(transient, True)


__all__ = [
    "LegacyMigrationConflictError",
    "LegacyMigrationDetails",
    "LegacyMigrationInsertResult",
    "LegacyMigrationIntegrityError",
    "LegacyMigrationKey",
    "LegacyMigrationNotFoundError",
    "LegacyMigrationRecord",
    "LegacyMigrationRelationshipDetails",
    "LegacyMigrationRelationshipInsertResult",
    "LegacyMigrationRelationshipRecord",
    "LegacyMigrationRelationshipRepository",
    "LegacyMigrationRelationshipTarget",
    "LegacyMigrationRepository",
    "LegacyMigrationRepositoryError",
    "LegacyMigrationTarget",
    "MigrationImportMode",
    "MigrationRecordKind",
    "MigrationResultDisposition",
    "P2_3_IMPORT_CATEGORIES",
    "VerifiedBlobEvidence",
    "legacy_user_account_projection_sha256",
    "migration_mapping_id_for_record",
    "migration_relationship_mapping_id_for_plan",
    "migration_run_id_for_plan",
    "migration_source_locator",
]

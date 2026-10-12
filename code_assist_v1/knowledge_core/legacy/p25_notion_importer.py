"""Frozen SQLite adapter for reviewed P2.5 Notion session links.

The retained external Notion database is never imported as a canonical source
container.  Exact main/sidecar bytes are copied to migration-owned scratch and
revalidated against the P2.1 table/schema ledger before SQLite is opened.  The
adapter then creates only reviewed, resolved session-page bindings plus their
immutable record and relationship checkpoints in one service-owned transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import ntpath
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
from typing import Any
import unicodedata
from uuid import UUID

from ..db import ConnectionFactory, ManagedConnection, transaction
from ..errors import KnowledgeCoreError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.conversations import (
    ConversationRepository,
    LegacyNotionContextBinding,
    legacy_conversation_id,
    legacy_notion_context_binding_id,
)
from ..repositories.migration import (
    migration_mapping_id_for_record,
    migration_run_id_for_plan,
)
from ..repositories.migration_p25 import (
    LegacyP25MappingRepository,
    LegacyP25NotionDetails,
    LegacyP25NotionTarget,
    LegacyP25RelationshipDetails,
    LegacyP25RelationshipRepository,
    LegacyP25RelationshipTarget,
    p25_child_bindings_sha256,
    p25_relationship_bindings_sha256,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from .importer import _derived_uuid, _mapping, _reviewed_plan, _utc_ms
from .p25_importer import (
    MAX_P25_EFFECTS_PER_TRANSACTION,
    MAX_P25_SOURCE_BYTES,
    LegacyP25RecordOutcome,
    LegacyP25StateConflictError,
    _OwnerAssertion,
    _canonical_object,
    _owner_assertion,
    _physical_bytes,
    _record_id,
)
from .plan_contract import sha256_canonical


_REQUEST_NAMESPACE = UUID("ce5dc845-1bbc-46db-bfa4-e876ae2eff80")
_OPERATION_NAMESPACE = UUID("b1184566-40b2-4082-934f-b0f44b2bfb28")
_AUDIT_NAMESPACE = UUID("a176be91-247a-4807-b12e-0e9dd24b2f13")

_COMPONENT_SUFFIXES = ("", "-wal", "-shm", "-journal")
_NOTION_TABLES = frozenset(
    {
        "blocks",
        "comments",
        "files",
        "notion_schema_migrations",
        "page_save_operations",
        "page_shares",
        "page_versions",
        "pages",
        "public_links",
        "session_pages",
        "teamspace_memberships",
        "teamspaces",
        "workspace_audit_events",
        "workspace_file_sources",
        "workspace_group_members",
        "workspace_groups",
        "workspace_guests",
        "workspace_memberships",
        "workspace_operations",
        "workspace_outbox",
        "workspace_outbox_deliveries",
        "workspace_page_source_projections",
        "workspace_page_sources",
        "workspace_public_sources",
        "workspace_schema_migrations",
        "workspaces",
    }
)


class LegacyP25NotionImportError(KnowledgeCoreError):
    code = "LEGACY_P25_NOTION_IMPORT_FAILED"


class LegacyP25NotionPreparationError(LegacyP25NotionImportError):
    code = "LEGACY_P25_NOTION_PLAN_INVALID"


class LegacyP25NotionBatchLimitError(LegacyP25NotionPreparationError):
    code = "LEGACY_P25_NOTION_BATCH_LIMIT"


@dataclass(frozen=True, slots=True)
class _PreparedLink:
    plan_row: Mapping[str, object]
    source: Mapping[str, object]
    relationship_row: Mapping[str, object]
    source_relationship: Mapping[str, object]
    owner: _OwnerAssertion
    parent_record_id: str
    source_path_key: str
    source_ordinal: int
    session_record_id: str
    conversation_id: str
    relationship_id: str
    page_id: str
    page_identity_sha256: str
    notion_row_sha256: str

    @property
    def record_id(self) -> str:
        return str(self.plan_row["record_id"])


@dataclass(frozen=True, slots=True)
class _PreparedBatch:
    plan_sha256: str
    migration_run_id: str
    inventory_manifest_sha256: str
    root_binding_sha256: str
    reviewer_id: str
    effective_at_ms: int
    request_id: str
    operation_id: str
    links: tuple[_PreparedLink, ...]


@dataclass(frozen=True, slots=True)
class LegacyP25NotionOutcome:
    record: LegacyP25RecordOutcome
    relationship_id: str
    relationship_mapping_id: str
    context_binding_id: str
    relationship_inserted: bool


@dataclass(frozen=True, slots=True)
class LegacyP25NotionImportResult:
    plan_sha256: str
    migration_run_id: str
    request_id: str
    operation_id: str
    outcomes: tuple[LegacyP25NotionOutcome, ...]

    @property
    def records(self) -> tuple[LegacyP25RecordOutcome, ...]:
        return tuple(outcome.record for outcome in self.outcomes)

    @property
    def relationships(self) -> tuple[LegacyP25NotionOutcome, ...]:
        return self.outcomes

    @property
    def inserted_mapping_count(self) -> int:
        return sum(
            outcome.record.mapping_inserted + outcome.relationship_inserted
            for outcome in self.outcomes
        )


def _selected(values: Sequence[str], *, prefix: str, field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError(f"{field} must be a sequence.")
    prepared = tuple(values)
    if (
        not all(
            isinstance(value, str)
            and len(value) == 28
            and value.startswith(prefix)
            and all(character in "0123456789abcdef" for character in value[4:])
            for value in prepared
        )
        or prepared != tuple(sorted(set(prepared)))
    ):
        raise LegacyP25NotionPreparationError(
            f"{field} must be sorted, unique reviewed identities."
        )
    return prepared


def _source(row: Mapping[str, object]) -> Mapping[str, object]:
    value = row.get("source_record")
    if not isinstance(value, Mapping):
        raise LegacyP25NotionPreparationError("Reviewed source record is invalid.")
    return value


def _cell_fact(value: object) -> object:
    if value is None or isinstance(value, (str, int, float)):
        return value
    if isinstance(value, bytes):
        return {
            "blob_bytes": len(value),
            "blob_sha256": hashlib.sha256(value).hexdigest(),
        }
    raise LegacyP25NotionPreparationError("Notion SQLite contains an unsupported value.")


def _quoted(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_shape(
    connection: sqlite3.Connection, table: str
) -> dict[str, dict[str, object]]:
    return {
        str(row[1]): {
            "declared_type": str(row[2] or "").upper(),
            "not_null": int(row[3]),
            "primary_key_position": int(row[5]),
        }
        for row in connection.execute(f"PRAGMA table_info({_quoted(table)})")
    }


def _anchor_reasons(connection: sqlite3.Connection, names: set[str]) -> tuple[str, ...]:
    if {"pages", "session_pages"} - names:
        return ("missing_required_notion_table",)
    reasons: list[str] = []
    pages = _table_shape(connection, "pages")
    links = _table_shape(connection, "session_pages")
    for column in ("id", "owner_user_id", "created_at"):
        if column not in pages:
            reasons.append(f"pages_missing_{column}")
    if "id" in pages and (
        pages["id"]["declared_type"] != "TEXT"
        or pages["id"]["primary_key_position"] != 1
    ):
        reasons.append("pages_id_not_text_primary_key")
    if "owner_user_id" in pages and (
        pages["owner_user_id"]["declared_type"] != "TEXT"
        or pages["owner_user_id"]["not_null"] != 1
    ):
        reasons.append("pages_owner_not_required_text")
    if "created_at" in pages and (
        pages["created_at"]["declared_type"] != "INTEGER"
        or pages["created_at"]["not_null"] != 1
    ):
        reasons.append("pages_created_at_not_required_integer")
    required = {
        "session_id": ("TEXT", 1),
        "page_id": ("TEXT", 2),
        "owner_user_id": ("TEXT", 0),
        "created_at": ("INTEGER", 0),
    }
    for column, (declared, primary) in required.items():
        shape = links.get(column)
        if shape is None:
            reasons.append(f"session_pages_missing_{column}")
            continue
        if shape["declared_type"] != declared or shape["not_null"] != 1:
            reasons.append(f"session_pages_{column}_shape_invalid")
        if shape["primary_key_position"] != primary:
            reasons.append(f"session_pages_{column}_key_invalid")
    return tuple(sorted(set(reasons)))


def _snapshot_rows(
    parent_source: Mapping[str, object],
    component_rows: Mapping[str, Mapping[str, object]],
    component_payloads: Mapping[str, bytes],
    *,
    scratch_root: Path,
) -> tuple[
    list[tuple[object, ...]],
    tuple[str, ...],
    dict[str, str],
]:
    relative = str(parent_source["relative_path"])
    main_name = PurePosixPath(relative).name
    names: dict[str, bytes] = {}
    for record_id, row in component_rows.items():
        source = _source(row)
        raw = component_payloads[record_id]
        try:
            _physical_bytes(source, raw, label="Notion SQLite component")
        except KnowledgeCoreError:
            raise LegacyP25NotionPreparationError(
                "Notion SQLite component bytes differ from the reviewed source."
            ) from None
        name = PurePosixPath(str(source["relative_path"])).name
        if name not in {main_name + suffix for suffix in _COMPONENT_SUFFIXES}:
            raise LegacyP25NotionPreparationError(
                "Notion SQLite component name is outside the reviewed snapshot."
            )
        if name in names:
            raise LegacyP25NotionPreparationError("Notion SQLite component is duplicated.")
        names[name] = raw
    if main_name not in names:
        raise LegacyP25NotionPreparationError("Notion SQLite main database is missing.")
    if (main_name + "-journal" in names and any(
        main_name + suffix in names for suffix in ("-wal", "-shm")
    )) or (main_name + "-shm" in names and main_name + "-wal" not in names):
        raise LegacyP25NotionPreparationError("Notion SQLite sidecars conflict.")

    try:
        with tempfile.TemporaryDirectory(prefix=".p25-notion-", dir=scratch_root) as temp:
            directory = Path(temp)
            for name, raw in names.items():
                (directory / name).write_bytes(raw)
            database = (directory / main_name).resolve(strict=True)
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            try:
                connection.execute("PRAGMA query_only=ON")
                if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
                    raise LegacyP25NotionPreparationError(
                        "Notion SQLite query-only mode was not established."
                    )
                names_in_db = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                unknown = sorted(
                    name
                    for name in names_in_db
                    if not name.startswith("sqlite_") and name not in _NOTION_TABLES
                )
                quick = connection.execute("PRAGMA quick_check").fetchall()
                foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
                if (
                    unknown
                    or _anchor_reasons(connection, names_in_db)
                    or len(quick) != 1
                    or str(quick[0][0]).casefold() != "ok"
                    or foreign_keys
                ):
                    raise LegacyP25NotionPreparationError(
                        "Notion SQLite schema or integrity differs from the reviewed snapshot."
                    )
                schema_rows = [
                    [str(row[0]), str(row[1]), str(row[2] or "")]
                    for row in connection.execute(
                        "SELECT type,name,sql FROM sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
                    )
                ]
                counts: dict[str, int] = {}
                table_hashes: dict[str, str] = {}
                columns: dict[str, tuple[str, ...]] = {}
                for table in sorted(_NOTION_TABLES & names_in_db):
                    quoted = _quoted(table)
                    table_columns = tuple(
                        str(row[1])
                        for row in connection.execute(f"PRAGMA table_info({quoted})")
                    )
                    rows = connection.execute(f"SELECT * FROM {quoted}").fetchall()
                    columns[table] = table_columns
                    counts[table] = len(rows)
                    table_hashes[table] = sha256_canonical(
                        sorted(
                            sha256_canonical([_cell_fact(value) for value in row])
                            for row in rows
                        )
                    )
                parse = parent_source.get("parse")
                schema_sha = sha256_canonical(schema_rows)
                logical_sha = sha256_canonical(
                    {
                        "schema_sha256": schema_sha,
                        "table_counts": counts,
                        "table_sha256": table_hashes,
                    }
                )
                if (
                    not isinstance(parse, Mapping)
                    or parse.get("status") != "valid"
                    or parse.get("schema_version")
                    != int(connection.execute("PRAGMA user_version").fetchone()[0])
                    or parse.get("schema_sha256") != schema_sha
                    or parse.get("table_counts") != counts
                    or parse.get("table_sha256") != table_hashes
                    or parse.get("logical_sha256") != logical_sha
                    or parse.get("quick_check_ok") is not True
                    or parse.get("foreign_key_violation_count") != 0
                    or parse.get("unknown_table_count") != 0
                    or parse.get("record_count") != sum(counts.values())
                    or parse.get("table_row_count") != sum(counts.values())
                    or parse.get("child_count") != counts.get("session_pages", 0)
                ):
                    raise LegacyP25NotionPreparationError(
                        "Notion SQLite facts differ from P2.1 evidence."
                    )
                link_columns = columns["session_pages"]
                raw_rows = connection.execute('SELECT * FROM "session_pages"').fetchall()
                indexes = {
                    name: link_columns.index(name)
                    for name in ("session_id", "page_id", "owner_user_id")
                }
                page_columns = columns["pages"]
                page_id_index = page_columns.index("id")
                page_owner_index = page_columns.index("owner_user_id")
                page_owners = {
                    str(row[page_id_index]): str(row[page_owner_index])
                    for row in connection.execute('SELECT * FROM "pages"').fetchall()
                }
                return (
                    sorted(
                        (tuple(row) for row in raw_rows),
                        key=lambda row: (
                            str(row[indexes["session_id"]]),
                            str(row[indexes["page_id"]]),
                            str(row[indexes["owner_user_id"]]),
                        ),
                    ),
                    link_columns,
                    page_owners,
                )
            finally:
                connection.close()
    except LegacyP25NotionPreparationError:
        raise
    except (OSError, sqlite3.Error, ValueError):
        raise LegacyP25NotionPreparationError(
            "Notion SQLite snapshot could not be read safely."
        ) from None


def _norm(value: str | Path) -> str:
    return ntpath.normcase(ntpath.normpath(str(value).replace("/", "\\")))


def _overlap(left: str, right: str) -> bool:
    try:
        common = ntpath.commonpath((_norm(left), _norm(right)))
    except ValueError:
        return False
    return common in {_norm(left), _norm(right)}


def _prepare_batch(
    reviewed_plan: bytes | Mapping[str, object],
    component_payloads: Mapping[str, bytes],
    *,
    selected_record_ids: Sequence[str],
    selected_relationship_ids: Sequence[str],
    expected_plan_sha256: str,
    scratch_root: Path,
) -> _PreparedBatch:
    selected_records = _selected(
        selected_record_ids, prefix="rec_", field="selected_record_ids"
    )
    selected_relationships = _selected(
        selected_relationship_ids,
        prefix="rel_",
        field="selected_relationship_ids",
    )
    if (
        not selected_records
        or len(selected_records) != len(selected_relationships)
        or len(selected_records) + len(selected_relationships)
        > MAX_P25_EFFECTS_PER_TRANSACTION
    ):
        raise LegacyP25NotionBatchLimitError(
            "A P2.5 Notion batch requires 1..50 exact record/relationship pairs."
        )
    plan = _reviewed_plan(reviewed_plan, expected_plan_sha256=expected_plan_sha256)
    draft = _mapping(plan.get("draft"), field="plan draft")
    records_value = draft.get("records")
    relationships_value = draft.get("relationships")
    mappings_value = draft.get("owner_mappings")
    if (
        not isinstance(records_value, list)
        or not all(isinstance(row, Mapping) for row in records_value)
        or not isinstance(relationships_value, list)
        or not all(isinstance(row, Mapping) for row in relationships_value)
        or not isinstance(mappings_value, list)
        or not all(isinstance(row, Mapping) for row in mappings_value)
    ):
        raise LegacyP25NotionPreparationError("Reviewed Notion plan rows are invalid.")
    records: tuple[Mapping[str, object], ...] = tuple(records_value)  # type: ignore[assignment]
    relationships: tuple[Mapping[str, object], ...] = tuple(relationships_value)  # type: ignore[assignment]
    records_by_id = {str(row.get("record_id")): row for row in records}
    relationships_by_id = {
        str(row.get("relationship_id")): row for row in relationships
    }
    owner_mappings = {
        str(row.get("mapping_id")): row for row in mappings_value  # type: ignore[union-attr]
    }
    if (
        len(records_by_id) != len(records)
        or len(relationships_by_id) != len(relationships)
        or any(value not in records_by_id for value in selected_records)
        or any(value not in relationships_by_id for value in selected_relationships)
    ):
        raise LegacyP25NotionPreparationError("Selected Notion plan rows are absent.")
    selected_rows = tuple(records_by_id[value] for value in selected_records)
    parent_ids = {
        str(_source(row).get("parent_record_id")) for row in selected_rows
    }
    if len(parent_ids) != 1:
        raise LegacyP25NotionPreparationError(
            "A Notion batch must bind exactly one retained database snapshot."
        )
    parent_id = _record_id(next(iter(parent_ids)), "parent_record_id")
    parent_row = records_by_id.get(parent_id)
    if parent_row is None:
        raise LegacyP25NotionPreparationError("Notion database parent is absent.")
    parent_source = _source(parent_row)
    if (
        parent_row.get("action") != "retain_external"
        or parent_row.get("execution_phase") != "none"
        or parent_source.get("category") != "external.notion_database"
        or parent_source.get("record_kind") != "physical_file"
        or parent_row.get("inventory_record_sha256")
        != sha256_canonical(parent_source)
    ):
        raise LegacyP25NotionPreparationError(
            "Notion database must remain an exact retained-external source."
        )
    main_relative = str(parent_source.get("relative_path"))
    component_rows = {
        str(row["record_id"]): row
        for row in records
        if _source(row).get("record_kind") == "physical_file"
        and _source(row).get("root_id") == parent_source.get("root_id")
        and str(_source(row).get("relative_path"))
        in {main_relative + suffix for suffix in _COMPONENT_SUFFIXES}
    }
    if not isinstance(component_payloads, Mapping) or set(component_payloads) != set(
        component_rows
    ):
        raise LegacyP25NotionPreparationError(
            "Notion component payloads must cover the exact reviewed snapshot."
        )
    if any(not isinstance(raw, bytes) for raw in component_payloads.values()):
        raise TypeError("Notion component payloads must be bytes.")
    if sum(len(raw) for raw in component_payloads.values()) > MAX_P25_SOURCE_BYTES:
        raise LegacyP25NotionBatchLimitError(
            "A Notion snapshot exceeds the P2.5 source-byte limit."
        )
    snapshot = draft.get("source_snapshot")
    roots = snapshot.get("roots") if isinstance(snapshot, Mapping) else None
    if not isinstance(roots, list) or any(
        not isinstance(root, Mapping)
        or not isinstance(root.get("resolved_path"), str)
        or _overlap(str(root["resolved_path"]), str(scratch_root))
        for root in roots
    ):
        raise LegacyP25NotionPreparationError(
            "Notion scratch storage overlaps or lacks reviewed source roots."
        )
    raw_rows, link_columns, page_owners = _snapshot_rows(
        parent_source,
        component_rows,
        component_payloads,
        scratch_root=scratch_root,
    )
    all_children = tuple(
        sorted(
            (
                row
                for row in records
                if _source(row).get("category") == "external.notion_session_links"
                and _source(row).get("parent_record_id") == parent_id
            ),
            key=lambda row: int(_source(row).get("source_ordinal", 0)),
        )
    )
    if len(raw_rows) != len(all_children):
        raise LegacyP25NotionPreparationError(
            "Notion row count differs from reviewed logical children."
        )
    indexes = {
        name: link_columns.index(name)
        for name in ("session_id", "page_id", "owner_user_id")
    }
    links: list[_PreparedLink] = []
    selected_relationship_set = set(selected_relationships)
    relationship_for_source = {
        str(row.get("source_relationship", {}).get("source_record_id")): row
        for row in relationships
        if isinstance(row.get("source_relationship"), Mapping)
        and row.get("action") == "import"
        and row.get("execution_phase") == "P2.5"
        and row.get("import_mode") == "notion_session_link"
    }
    for ordinal, (row, plan_row) in enumerate(zip(raw_rows, all_children, strict=True), 1):
        session_id = str(row[indexes["session_id"]])
        page_id = str(row[indexes["page_id"]])
        owner_id = str(row[indexes["owner_user_id"]])
        shape = {
            "owner_sha256": hashlib.sha256(owner_id.encode("utf-8")).hexdigest(),
            "page_sha256": hashlib.sha256(page_id.encode("utf-8")).hexdigest(),
            "row_sha256": sha256_canonical([_cell_fact(value) for value in row]),
            "session_sha256": hashlib.sha256(session_id.encode("utf-8")).hexdigest(),
        }
        source = _source(plan_row)
        expected_child_sha = sha256_canonical(
            {
                "ordinal": ordinal,
                "shape": shape,
                "source_sha256": parent_source.get("sha256"),
            }
        )
        if (
            source.get("source_ordinal") != ordinal
            or source.get("sha256") != expected_child_sha
            or source.get("record_kind") != "logical_record"
            or source.get("path_key") != parent_source.get("path_key")
            or source.get("root_id") != parent_source.get("root_id")
        ):
            raise LegacyP25NotionPreparationError(
                "A Notion row differs from its reviewed logical record."
            )
        record_id = str(plan_row["record_id"])
        if record_id not in selected_records:
            continue
        relationship_row = relationship_for_source.get(record_id)
        resolution = source.get("link_resolution")
        relationship_source = (
            relationship_row.get("source_relationship")
            if isinstance(relationship_row, Mapping)
            else None
        )
        owner = _owner_assertion(plan_row, owner_mappings)
        normalized_owner = unicodedata.normalize("NFKC", owner_id).casefold()
        normalized_page_owner = unicodedata.normalize(
            "NFKC", page_owners.get(page_id, "")
        ).casefold()
        if (
            plan_row.get("action") != "import"
            or plan_row.get("execution_phase") != "P2.5"
            or plan_row.get("import_mode") != "notion_session_link"
            or plan_row.get("inventory_record_sha256") != sha256_canonical(source)
            or not isinstance(resolution, Mapping)
            or resolution.get("status") != "resolved"
            or resolution.get("page_exists") is not True
            or resolution.get("page_identity_sha256") != shape["page_sha256"]
            or not isinstance(resolution.get("session_record_id"), str)
            or not isinstance(relationship_row, Mapping)
            or not isinstance(relationship_source, Mapping)
            or normalized_owner != owner.user_id
            or normalized_page_owner != owner.user_id
        ):
            raise LegacyP25NotionPreparationError(
                "A selected Notion link is not an exact resolved P2.5 effect."
            )
        relationship_id = str(relationship_row["relationship_id"])
        session_record_id = str(resolution["session_record_id"])
        session_row = records_by_id.get(session_record_id)
        if session_row is None:
            raise LegacyP25NotionPreparationError("Resolved Notion session is absent.")
        session_owner = _owner_assertion(session_row, owner_mappings)
        if (
            owner != session_owner
            or relationship_id not in selected_relationship_set
            or relationship_row.get("inventory_relationship_sha256")
            != sha256_canonical(relationship_source)
            or relationship_source.get("relationship_id") != relationship_id
            or relationship_source.get("relationship_type") != "notion_session_page"
            or relationship_source.get("status") != "valid"
            or relationship_source.get("source_record_id") != record_id
            or relationship_source.get("from_record_id") != session_record_id
            or relationship_source.get("to_record_id") != record_id
            or relationship_source.get("from_ref_sha256") is not None
            or relationship_source.get("to_ref_sha256") is not None
        ):
            raise LegacyP25NotionPreparationError(
                "Notion relationship evidence differs from the resolved row."
            )
        links.append(
            _PreparedLink(
                plan_row=plan_row,
                source=source,
                relationship_row=relationship_row,
                source_relationship=relationship_source,
                owner=owner,
                parent_record_id=parent_id,
                source_path_key=str(parent_source["path_key"]),
                source_ordinal=ordinal,
                session_record_id=session_record_id,
                conversation_id=legacy_conversation_id(
                    expected_plan_sha256, session_record_id
                ),
                relationship_id=relationship_id,
                page_id=page_id,
                page_identity_sha256=shape["page_sha256"],
                notion_row_sha256=shape["row_sha256"],
            )
        )
    if (
        {link.record_id for link in links} != set(selected_records)
        or {link.relationship_id for link in links} != selected_relationship_set
    ):
        raise LegacyP25NotionPreparationError(
            "Selected Notion records and relationships are not exactly paired."
        )
    source_inventory = _mapping(draft.get("source_inventory"), field="source inventory")
    metadata = _mapping(source_inventory.get("metadata"), field="source metadata")
    run = _mapping(metadata.get("run"), field="inventory run")
    inputs = _mapping(draft.get("inputs"), field="plan inputs")
    inventory = _mapping(inputs.get("inventory"), field="inventory binding")
    review = _mapping(plan.get("review"), field="review")
    return _PreparedBatch(
        plan_sha256=expected_plan_sha256,
        migration_run_id=migration_run_id_for_plan(expected_plan_sha256),
        inventory_manifest_sha256=str(inventory["manifest_sha256"]),
        root_binding_sha256=str(inventory["root_binding_sha256"]),
        reviewer_id=str(review["reviewer_id"]),
        effective_at_ms=_utc_ms(run.get("started_at_utc")),
        request_id=_derived_uuid(_REQUEST_NAMESPACE, expected_plan_sha256),
        operation_id=_derived_uuid(_OPERATION_NAMESPACE, expected_plan_sha256),
        links=tuple(sorted(links, key=lambda link: link.record_id)),
    )


class LegacyP25NotionImporter:
    """Apply exact reviewed Notion link pairs in one bounded transaction."""

    __slots__ = (
        "_audit",
        "_conversations",
        "_factory",
        "_failpoint",
        "_mapping",
        "_outbox",
        "_relationships",
        "_scratch_root",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        scratch_root: str | Path,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("LegacyP25NotionImporter requires a ConnectionFactory.")
        root = Path(scratch_root)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("Notion scratch_root must be an existing absolute directory.")
        self._factory = factory
        self._scratch_root = root.resolve(strict=True)
        self._mapping = LegacyP25MappingRepository()
        self._relationships = LegacyP25RelationshipRepository()
        self._conversations = ConversationRepository()
        self._audit = AuditRepository()
        self._outbox = OutboxRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    @staticmethod
    def _assert_owner(connection: ManagedConnection, owner: _OwnerAssertion) -> None:
        user = connection.execute(
            """SELECT system_role,account_status,current_part_id
               FROM app_user WHERE id=?""",
            (owner.user_id,),
        ).fetchone()
        space = connection.execute(
            """SELECT space_kind,owner_user_id,status FROM knowledge_space WHERE id=?""",
            (owner.target_space_id,),
        ).fetchone()
        memberships = connection.execute(
            """SELECT part_id FROM org_membership
               WHERE user_id=? AND valid_to IS NULL""",
            (owner.user_id,),
        ).fetchall()
        if (
            user is None
            or space is None
            or user["system_role"] != owner.system_role
            or user["account_status"] != owner.account_status
            or user["current_part_id"] != owner.part_id
            or space["space_kind"] != "personal"
            or space["owner_user_id"] != owner.user_id
            or space["status"] != "active"
            or (owner.part_id is None and memberships)
            or (
                owner.part_id is not None
                and (
                    len(memberships) != 1
                    or memberships[0]["part_id"] != owner.part_id
                )
            )
        ):
            raise LegacyP25StateConflictError(
                "The reviewed Notion owner, membership, or space drifted."
            )

    def apply_batch(
        self,
        reviewed_plan: bytes | Mapping[str, object],
        component_payloads: Mapping[str, bytes],
        *,
        selected_record_ids: Sequence[str],
        selected_relationship_ids: Sequence[str],
        expected_plan_sha256: str,
    ) -> LegacyP25NotionImportResult:
        batch = _prepare_batch(
            reviewed_plan,
            component_payloads,
            selected_record_ids=selected_record_ids,
            selected_relationship_ids=selected_relationship_ids,
            expected_plan_sha256=expected_plan_sha256,
            scratch_root=self._scratch_root,
        )
        connection = self._factory.open()
        try:
            with transaction(connection):
                outcomes = tuple(self._apply_link(connection, batch, link) for link in batch.links)
                return LegacyP25NotionImportResult(
                    batch.plan_sha256,
                    batch.migration_run_id,
                    batch.request_id,
                    batch.operation_id,
                    outcomes,
                )
        finally:
            connection.close()

    def _apply_link(
        self,
        connection: ManagedConnection,
        batch: _PreparedBatch,
        link: _PreparedLink,
    ) -> LegacyP25NotionOutcome:
        self._assert_owner(connection, link.owner)
        context_binding_id = legacy_notion_context_binding_id(
            batch.plan_sha256, link.relationship_id
        )
        metadata_json = _canonical_object(
            {
                "legacy_p2_5": {
                    "notion_row_sha256": link.notion_row_sha256,
                    "page_identity_sha256": link.page_identity_sha256,
                    "relationship_id": link.relationship_id,
                    "schema_version": 1,
                }
            }
        )
        self._conversations.put_legacy_notion_binding(
            connection,
            LegacyNotionContextBinding(
                plan_sha256=batch.plan_sha256,
                relationship_id=link.relationship_id,
                context_binding_id=context_binding_id,
                conversation_id=link.conversation_id,
                owner_user_id=link.owner.user_id,
                personal_space_id=link.owner.target_space_id,
                external_page_key=link.page_id,
                display_metadata_json=metadata_json,
                created_at_ms=batch.effective_at_ms + link.source_ordinal,
            ),
        )
        self._hit("p25_notion.after_binding")
        source = link.source
        self._hit("migration.before_checkpoint")
        record, record_inserted = self._mapping.put_notion_imported(
            connection,
            target=LegacyP25NotionTarget(
                resolved_owner_user_id=link.owner.user_id,
                conversation_id=link.conversation_id,
                context_binding_id=context_binding_id,
                source_path_key=link.source_path_key,
                details=LegacyP25NotionDetails(
                    plan_sha256=batch.plan_sha256,
                    inventory_manifest_sha256=batch.inventory_manifest_sha256,
                    root_binding_sha256=batch.root_binding_sha256,
                    inventory_record_id=link.record_id,
                    inventory_record_sha256=str(
                        link.plan_row["inventory_record_sha256"]
                    ),
                    parent_inventory_record_id=link.parent_record_id,
                    root_id=str(source["root_id"]),
                    path_key_sha256=hashlib.sha256(
                        link.source_path_key.encode("utf-8")
                    ).hexdigest(),
                    record_kind="logical_record",
                    source_ordinal=link.source_ordinal,
                    import_mode="notion_session_link",
                    result_disposition="binding_created",
                    target_space_id=link.owner.target_space_id,
                    source_record_sha256=str(source["sha256"]),
                    conversation_id=link.conversation_id,
                    relationship_id=link.relationship_id,
                    inventory_relationship_sha256=str(
                        link.relationship_row["inventory_relationship_sha256"]
                    ),
                    page_identity_sha256=link.page_identity_sha256,
                    notion_row_sha256=link.notion_row_sha256,
                    planned_import_child_record_ids=(),
                    planned_import_relationship_ids=(),
                    imported_child_bindings_sha256=p25_child_bindings_sha256(()),
                    imported_relationship_bindings_sha256=(
                        p25_relationship_bindings_sha256(())
                    ),
                ),
            ),
            now_ms=batch.effective_at_ms,
        )
        self._hit("p25_notion.after_record_mapping")
        self._hit("migration.before_checkpoint")
        relationship, relationship_inserted = self._relationships.put_imported(
            connection,
            target=LegacyP25RelationshipTarget(
                source_mapping_id=record.id,
                from_mapping_id=migration_mapping_id_for_record(
                    batch.plan_sha256, link.session_record_id
                ),
                conversation_id=link.conversation_id,
                context_binding_id=context_binding_id,
                resolved_owner_user_id=link.owner.user_id,
                target_space_id=link.owner.target_space_id,
                details=LegacyP25RelationshipDetails(
                    plan_sha256=batch.plan_sha256,
                    relationship_id=link.relationship_id,
                    inventory_manifest_sha256=batch.inventory_manifest_sha256,
                    root_binding_sha256=batch.root_binding_sha256,
                    inventory_relationship_sha256=str(
                        link.relationship_row["inventory_relationship_sha256"]
                    ),
                    source_inventory_record_id=link.record_id,
                    from_inventory_record_id=link.session_record_id,
                    page_identity_sha256=link.page_identity_sha256,
                    notion_row_sha256=link.notion_row_sha256,
                ),
            ),
            now_ms=batch.effective_at_ms,
        )
        self._hit("p25_notion.after_relationship_mapping")
        event_details = {
            "context_binding_id": context_binding_id,
            "inventory_record_id": link.record_id,
            "mapping_id": record.id,
            "notion_row_sha256": link.notion_row_sha256,
            "page_identity_sha256": link.page_identity_sha256,
            "plan_sha256": batch.plan_sha256,
            "relationship_id": link.relationship_id,
            "relationship_mapping_id": relationship.id,
            "schema_version": 1,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(
                    _AUDIT_NAMESPACE, batch.plan_sha256, link.relationship_id
                ),
                occurred_at_ms=batch.effective_at_ms,
                request_id=batch.request_id,
                operation_id=batch.operation_id,
                action="legacy_migration.notion_session_link",
                outcome="binding_created",
                actor_kind="system",
                actor_user_id=None,
                actor_display_name="P2.5 legacy migration",
                actor_role_snapshot="system",
                space_id=link.owner.target_space_id,
                reason="reviewed P2.5 Notion session binding",
                details=event_details,
                request_meta={"reviewer_id": batch.reviewer_id},
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"legacy-migration-p25-notion:{relationship.id}:imported:v1",
                space_id=link.owner.target_space_id,
                aggregate_type="legacy_migration_p25_relationship_map",
                aggregate_id=relationship.id,
                aggregate_version=1,
                event_type="legacy_migration.notion_session_link.imported",
                payload={
                    **event_details,
                    "operation_id": batch.operation_id,
                    "resolved_owner_user_id": link.owner.user_id,
                },
                available_at_ms=batch.effective_at_ms,
            ),
        )
        return LegacyP25NotionOutcome(
            record=LegacyP25RecordOutcome(
                link.record_id,
                record.id,
                "notion_session_link",
                "binding_created",
                link.conversation_id,
                None,
                record_inserted,
            ),
            relationship_id=link.relationship_id,
            relationship_mapping_id=relationship.id,
            context_binding_id=context_binding_id,
            relationship_inserted=relationship_inserted,
        )


__all__ = [
    "LegacyP25NotionBatchLimitError",
    "LegacyP25NotionImportError",
    "LegacyP25NotionImporter",
    "LegacyP25NotionImportResult",
    "LegacyP25NotionOutcome",
    "LegacyP25NotionPreparationError",
]

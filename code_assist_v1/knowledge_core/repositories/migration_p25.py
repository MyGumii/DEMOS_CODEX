"""Immutable P2.5 session and Notion record/relationship checkpoints.

Canonical conversation effects are written first.  This repository verifies
those effects and then appends one immutable migration checkpoint in the same
caller-owned transaction.  Session logical children are checkpointed before
their imported session source container; that container closes the exact
reviewed child set and permanently rejects late additions.  The retained
external Notion database is deliberately not represented by an imported
container checkpoint; only reviewed logical session-page links are mapped.

No source file, SQLite backup, blob, or network access occurs here.  The P2.5
adapter must perform those observations before opening the write transaction.
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
from ..errors import KnowledgeCoreError, KnowledgeCoreTransactionError
from .migration import (
    migration_mapping_id_for_record,
    migration_run_id_for_plan,
    migration_source_locator,
)


P25SessionChildKind = Literal[
    "session_core_metadata", "messages", "metadata", "summaries", "uploads",
    "bindings",
]

_P25_RELATIONSHIP_NAMESPACE = UUID("ec5591d4-2538-40d2-84db-c1a929a511ea")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_RECORD_RE = re.compile(r"^rec_[0-9a-f]{24}$", re.ASCII)
_RELATIONSHIP_RE = re.compile(r"^rel_[0-9a-f]{24}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$", re.ASCII)
_SQLITE_INTEGER_MAX = 9_223_372_036_854_775_807
_SESSION_CATEGORIES = frozenset(
    {"conversation.user_sessions", "conversation.global_sessions"}
)
_SESSION_CHILD_KINDS = frozenset(
    {
        "session_core_metadata", "messages", "metadata", "summaries",
        "uploads", "bindings",
    }
)

_RECORD_COLUMNS = (
    "id", "migration_run_id", "legacy_kind", "normalized_path",
    "source_sha256", "resolved_owner_user_id", "artifact_id", "item_id",
    "revision_id", "edge_id", "status", "details_json", "created_at",
    "updated_at", "finding_id", "audit_event_id", "conversation_id",
    "message_id", "summary_id", "context_binding_id",
)
_RELATIONSHIP_COLUMNS = (
    "id", "migration_run_id", "relationship_id", "relationship_sha256",
    "relationship_type", "source_mapping_id", "from_mapping_id",
    "conversation_id", "context_binding_id", "resolved_owner_user_id",
    "target_space_id", "page_identity_sha256", "notion_row_sha256", "status",
    "details_json", "created_at", "updated_at",
)


class LegacyP25MappingError(KnowledgeCoreError):
    code = "LEGACY_P25_MAPPING_ERROR"


class LegacyP25MappingConflict(LegacyP25MappingError):
    code = "LEGACY_P25_MAPPING_CONFLICT"


class LegacyP25MappingIntegrityError(LegacyP25MappingError):
    code = "LEGACY_P25_MAPPING_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class LegacyP25SessionDetails:
    plan_sha256: str
    inventory_manifest_sha256: str
    root_binding_sha256: str
    inventory_record_id: str
    inventory_record_sha256: str
    parent_inventory_record_id: str | None
    root_id: str
    path_key_sha256: str
    category: str
    record_kind: str
    source_ordinal: int
    import_mode: Literal["source_container", "conversation_child"]
    result_disposition: str
    target_space_id: str
    source_record_sha256: str
    conversation_id: str
    session_id_sha256: str
    child_kind: P25SessionChildKind | None
    payload_sha256: str | None
    message_sequence_no: int | None
    planned_import_child_record_ids: tuple[str, ...]
    imported_child_bindings_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "child_kind": self.child_kind,
            "conversation_id": self.conversation_id,
            "execution_phase": "P2.5",
            "final_terminal_classification": "imported",
            "import_mode": self.import_mode,
            "imported_child_bindings_sha256": self.imported_child_bindings_sha256,
            "inventory_manifest_sha256": self.inventory_manifest_sha256,
            "inventory_record_id": self.inventory_record_id,
            "inventory_record_sha256": self.inventory_record_sha256,
            "message_sequence_no": self.message_sequence_no,
            "parent_inventory_record_id": self.parent_inventory_record_id,
            "path_key_sha256": self.path_key_sha256,
            "payload_sha256": self.payload_sha256,
            "plan_sha256": self.plan_sha256,
            "planned_import_child_record_ids": list(
                self.planned_import_child_record_ids
            ),
            "record_kind": self.record_kind,
            "result_disposition": self.result_disposition,
            "root_binding_sha256": self.root_binding_sha256,
            "root_id": self.root_id,
            "schema_version": 5,
            "session_id_sha256": self.session_id_sha256,
            "source_ordinal": self.source_ordinal,
            "source_record_sha256": self.source_record_sha256,
            "target_space_id": self.target_space_id,
        }


@dataclass(frozen=True, slots=True)
class LegacyP25SessionTarget:
    resolved_owner_user_id: str
    conversation_id: str
    message_id: str | None
    summary_id: str | None
    details: LegacyP25SessionDetails
    source_path_key: str


@dataclass(frozen=True, slots=True)
class LegacyP25NotionDetails:
    plan_sha256: str
    inventory_manifest_sha256: str
    root_binding_sha256: str
    inventory_record_id: str
    inventory_record_sha256: str
    parent_inventory_record_id: str | None
    root_id: str
    path_key_sha256: str
    record_kind: str
    source_ordinal: int
    import_mode: Literal["source_container", "notion_session_link"]
    result_disposition: str
    target_space_id: str | None
    source_record_sha256: str
    conversation_id: str | None
    relationship_id: str | None
    inventory_relationship_sha256: str | None
    page_identity_sha256: str | None
    notion_row_sha256: str | None
    planned_import_child_record_ids: tuple[str, ...]
    planned_import_relationship_ids: tuple[str, ...]
    imported_child_bindings_sha256: str
    imported_relationship_bindings_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "category": "external.notion_session_links",
            "conversation_id": self.conversation_id,
            "execution_phase": "P2.5",
            "final_terminal_classification": "imported",
            "import_mode": self.import_mode,
            "imported_child_bindings_sha256": self.imported_child_bindings_sha256,
            "imported_relationship_bindings_sha256": self.imported_relationship_bindings_sha256,
            "inventory_manifest_sha256": self.inventory_manifest_sha256,
            "inventory_record_id": self.inventory_record_id,
            "inventory_record_sha256": self.inventory_record_sha256,
            "inventory_relationship_sha256": self.inventory_relationship_sha256,
            "notion_row_sha256": self.notion_row_sha256,
            "page_identity_sha256": self.page_identity_sha256,
            "parent_inventory_record_id": self.parent_inventory_record_id,
            "path_key_sha256": self.path_key_sha256,
            "plan_sha256": self.plan_sha256,
            "planned_import_child_record_ids": list(
                self.planned_import_child_record_ids
            ),
            "planned_import_relationship_ids": list(
                self.planned_import_relationship_ids
            ),
            "record_kind": self.record_kind,
            "relationship_id": self.relationship_id,
            "result_disposition": self.result_disposition,
            "root_binding_sha256": self.root_binding_sha256,
            "root_id": self.root_id,
            "schema_version": 6,
            "source_ordinal": self.source_ordinal,
            "source_record_sha256": self.source_record_sha256,
            "target_space_id": self.target_space_id,
        }


@dataclass(frozen=True, slots=True)
class LegacyP25NotionTarget:
    resolved_owner_user_id: str
    conversation_id: str | None
    context_binding_id: str | None
    details: LegacyP25NotionDetails
    source_path_key: str


@dataclass(frozen=True, slots=True)
class LegacyP25JobDetails:
    plan_sha256: str
    inventory_manifest_sha256: str
    root_binding_sha256: str
    inventory_record_id: str
    inventory_record_sha256: str
    root_id: str
    path_key_sha256: str
    source_record_sha256: str
    target_space_id: str
    import_mode: Literal["job_history", "abandoned_legacy_job"]
    result_disposition: Literal[
        "terminal_history_recorded", "abandoned_historical"
    ]
    source_status: Literal[
        "cancelled", "failed", "queued", "running", "succeeded", "unknown"
    ]
    job_identity_sha256: str
    job_evidence_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "category": "operations.jobs",
            "execution_phase": "P2.5",
            "final_terminal_classification": "imported",
            "import_mode": self.import_mode,
            "inventory_manifest_sha256": self.inventory_manifest_sha256,
            "inventory_record_id": self.inventory_record_id,
            "inventory_record_sha256": self.inventory_record_sha256,
            "job_evidence_sha256": self.job_evidence_sha256,
            "job_identity_sha256": self.job_identity_sha256,
            "path_key_sha256": self.path_key_sha256,
            "plan_sha256": self.plan_sha256,
            "record_kind": "physical_file",
            "result_disposition": self.result_disposition,
            "root_binding_sha256": self.root_binding_sha256,
            "root_id": self.root_id,
            "schema_version": 7,
            "source_ordinal": 0,
            "source_record_sha256": self.source_record_sha256,
            "source_status": self.source_status,
            "target_space_id": self.target_space_id,
        }


@dataclass(frozen=True, slots=True)
class LegacyP25JobTarget:
    resolved_owner_user_id: str
    artifact_id: str
    audit_event_id: int
    audit_event_uuid: str
    details: LegacyP25JobDetails
    source_path_key: str
    job_evidence_json: str


@dataclass(frozen=True, slots=True)
class LegacyP25Record:
    id: str
    migration_run_id: str
    details_json: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class LegacyP25RelationshipDetails:
    plan_sha256: str
    relationship_id: str
    inventory_manifest_sha256: str
    root_binding_sha256: str
    inventory_relationship_sha256: str
    source_inventory_record_id: str
    from_inventory_record_id: str
    page_identity_sha256: str
    notion_row_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "from_inventory_record_id": self.from_inventory_record_id,
            "inventory_manifest_sha256": self.inventory_manifest_sha256,
            "inventory_relationship_sha256": self.inventory_relationship_sha256,
            "notion_row_sha256": self.notion_row_sha256,
            "page_identity_sha256": self.page_identity_sha256,
            "plan_sha256": self.plan_sha256,
            "relationship_id": self.relationship_id,
            "root_binding_sha256": self.root_binding_sha256,
            "schema_version": 1,
            "source_inventory_record_id": self.source_inventory_record_id,
        }


@dataclass(frozen=True, slots=True)
class LegacyP25RelationshipTarget:
    source_mapping_id: str
    from_mapping_id: str
    conversation_id: str
    context_binding_id: str
    resolved_owner_user_id: str
    target_space_id: str
    details: LegacyP25RelationshipDetails


@dataclass(frozen=True, slots=True)
class LegacyP25RelationshipRecord:
    id: str
    migration_run_id: str
    details_json: str
    created_at_ms: int


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 value.")
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _optional_uuid(value: object, field: str) -> str | None:
    return None if value is None else _uuid(value, field)


def _record(value: object, field: str = "inventory_record_id") -> str:
    if not isinstance(value, str) or _RECORD_RE.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid.")
    return value


def _relationship(value: object) -> str:
    if not isinstance(value, str) or _RELATIONSHIP_RE.fullmatch(value) is None:
        raise ValueError("relationship_id is invalid.")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("resolved_owner_user_id is invalid.")
    return value


def _ordinal(value: object, *, physical: bool) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or (physical and value != 0)
        or (not physical and value < 1)
        or value > _SQLITE_INTEGER_MAX
    ):
        raise ValueError("source_ordinal is invalid.")
    return value


def _positive(value: object, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= _SQLITE_INTEGER_MAX
    ):
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _audit_id(value: object) -> int:
    return _positive(value, "audit_event_id")


def _now(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _SQLITE_INTEGER_MAX
    ):
        raise ValueError("now_ms is invalid.")
    return value


def _canonical(value: Mapping[str, object] | str, field: str) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, RecursionError, ValueError):
            raise ValueError(f"{field} is invalid.") from None
    else:
        parsed = dict(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{field} must be a JSON object.")
    try:
        encoded = json.dumps(
            parsed,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (RecursionError, TypeError, ValueError):
        raise ValueError(f"{field} is invalid.") from None
    if isinstance(value, str) and value != encoded:
        raise ValueError(f"{field} must be canonical JSON.")
    return encoded


def _sorted_records(values: Sequence[str], field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError(f"{field} must be a sequence of record IDs.")
    prepared = tuple(_record(value, field) for value in values)
    if prepared != tuple(sorted(set(prepared))):
        raise ValueError(f"{field} must be sorted and unique.")
    return prepared


def _sorted_relationships(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError("Relationship IDs must be a sequence.")
    prepared = tuple(_relationship(value) for value in values)
    if prepared != tuple(sorted(set(prepared))):
        raise ValueError("Relationship IDs must be sorted and unique.")
    return prepared


def _binding_sha(rows: Sequence[tuple[str, str, str]]) -> str:
    prepared = [list(row) for row in sorted(rows)]
    return hashlib.sha256(
        json.dumps(
            prepared,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def p25_child_bindings_sha256(
    rows: Sequence[tuple[str, str, str]],
) -> str:
    checked: list[tuple[str, str, str]] = []
    for mapping_id, record_id, source_sha256 in rows:
        checked.append(
            (
                _uuid(mapping_id, "child_mapping_id"),
                _record(record_id, "child_inventory_record_id"),
                _sha(source_sha256, "child_source_record_sha256"),
            )
        )
    if len({row[0] for row in checked}) != len(checked) or len(
        {row[1] for row in checked}
    ) != len(checked):
        raise ValueError("Child bindings must have unique identities.")
    return _binding_sha(checked)


def p25_relationship_bindings_sha256(
    rows: Sequence[tuple[str, str, str]],
) -> str:
    checked: list[tuple[str, str, str]] = []
    for mapping_id, relationship_id, source_sha256 in rows:
        checked.append(
            (
                _uuid(mapping_id, "relationship_mapping_id"),
                _relationship(relationship_id),
                _sha(source_sha256, "inventory_relationship_sha256"),
            )
        )
    if len({row[0] for row in checked}) != len(checked) or len(
        {row[1] for row in checked}
    ) != len(checked):
        raise ValueError("Relationship bindings must have unique identities.")
    return _binding_sha(checked)


def p25_relationship_mapping_id(plan_sha256: str, relationship_id: str) -> str:
    return str(
        uuid5(
            _P25_RELATIONSHIP_NAMESPACE,
            f"p2.5:{_sha(plan_sha256, 'plan_sha256')}:{_relationship(relationship_id)}",
        )
    )


def _connection(value: ManagedConnection) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError("A ManagedConnection is required.")
    if not value.in_transaction:
        raise KnowledgeCoreTransactionError(
            "P2.5 mapping writes require an existing managed transaction."
        )
    return value


def _tuple(row: object, columns: tuple[str, ...]) -> tuple[object, ...]:
    return tuple(row[name] for name in columns)  # type: ignore[index]


def _path_locator(
    *,
    root_id: str,
    path_key_sha256: str,
    path_key: str,
    record_kind: str,
    source_ordinal: int,
) -> str:
    if not isinstance(root_id, str) or _IDENTIFIER_RE.fullmatch(root_id) is None:
        raise ValueError("root_id is invalid.")
    if hashlib.sha256(path_key.encode("utf-8")).hexdigest() != path_key_sha256:
        raise ValueError("P2.5 source path binding is invalid.")
    return migration_source_locator(
        root_id=root_id,
        path_key=path_key,
        record_kind=record_kind,  # type: ignore[arg-type]
        source_ordinal=source_ordinal,
    )


def _conversation_row(
    connection: ManagedConnection,
    *,
    conversation_id: str,
    owner: str,
    space: str,
):
    row = connection.execute(
        """SELECT c.id,c.owner_user_id,c.space_id,c.status,c.metadata_json,
                  c.captured_scope_epoch,u.scope_epoch,s.space_kind,
                  s.owner_user_id AS space_owner,s.status AS space_status
           FROM conversation AS c
           JOIN app_user AS u ON u.id=c.owner_user_id
           JOIN knowledge_space AS s ON s.id=c.space_id
           WHERE c.id=?""",
        (conversation_id,),
    ).fetchone()
    if (
        row is None
        or row["owner_user_id"] != owner
        or row["space_id"] != space
        or row["status"] != "archived"
        or row["captured_scope_epoch"] != row["scope_epoch"]
        or row["space_kind"] != "personal"
        or row["space_owner"] != owner
        or row["space_status"] != "active"
    ):
        raise LegacyP25MappingIntegrityError(
            "The canonical conversation target is not owner-bound and exact."
        )
    return row


def _legacy_session_metadata(row: object) -> Mapping[str, object]:
    try:
        metadata = json.loads(str(row["metadata_json"]))  # type: ignore[index]
        legacy = metadata["legacy_p2_5"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise LegacyP25MappingIntegrityError(
            "The conversation lacks exact legacy session evidence."
        ) from None
    if not isinstance(legacy, Mapping) or legacy.get("schema_version") != 1:
        raise LegacyP25MappingIntegrityError(
            "The conversation legacy session evidence is invalid."
        )
    return legacy


def _session_evidence(
    legacy: Mapping[str, object],
    *,
    plan: str,
    parent_record_id: str,
    session_id_sha256: str,
) -> list[Mapping[str, object]]:
    children = legacy.get("child_evidence")
    if (
        legacy.get("plan_sha256") != plan
        or legacy.get("inventory_record_id") != parent_record_id
        or legacy.get("session_id_sha256") != session_id_sha256
        or not isinstance(children, list)
        or not all(isinstance(item, Mapping) for item in children)
    ):
        raise LegacyP25MappingIntegrityError(
            "The conversation session evidence does not match the reviewed source."
        )
    return children  # type: ignore[return-value]


class LegacyP25MappingRepository:
    __slots__ = ()

    @staticmethod
    def _prepare_session_details(
        value: LegacyP25SessionDetails,
    ) -> tuple[LegacyP25SessionDetails, str]:
        if not isinstance(value, LegacyP25SessionDetails):
            raise TypeError("LegacyP25SessionDetails is required.")
        _sha(value.plan_sha256, "plan_sha256")
        _sha(value.inventory_manifest_sha256, "inventory_manifest_sha256")
        _sha(value.root_binding_sha256, "root_binding_sha256")
        _record(value.inventory_record_id)
        _sha(value.inventory_record_sha256, "inventory_record_sha256")
        _sha(value.path_key_sha256, "path_key_sha256")
        _sha(value.source_record_sha256, "source_record_sha256")
        _sha(value.session_id_sha256, "session_id_sha256")
        _uuid(value.target_space_id, "target_space_id")
        _uuid(value.conversation_id, "conversation_id")
        if value.category not in _SESSION_CATEGORIES:
            raise ValueError("P2.5 session category is invalid.")
        children = _sorted_records(
            value.planned_import_child_record_ids,
            "planned_import_child_record_id",
        )
        _sha(
            value.imported_child_bindings_sha256,
            "imported_child_bindings_sha256",
        )
        if value.import_mode == "source_container":
            if (
                value.record_kind != "physical_file"
                or value.parent_inventory_record_id is not None
                or _ordinal(value.source_ordinal, physical=True) != 0
                or value.result_disposition != "dependency_closed"
                or value.child_kind is not None
                or value.payload_sha256 is not None
                or value.message_sequence_no is not None
            ):
                raise ValueError("P2.5 session-container detail shape is invalid.")
        elif value.import_mode == "conversation_child":
            if (
                value.record_kind != "logical_record"
                or value.parent_inventory_record_id is None
                or value.parent_inventory_record_id == value.inventory_record_id
                or children
                or value.imported_child_bindings_sha256
                != p25_child_bindings_sha256(())
                or value.child_kind not in _SESSION_CHILD_KINDS
                or value.payload_sha256 is None
            ):
                raise ValueError("P2.5 session-child detail shape is invalid.")
            _record(value.parent_inventory_record_id, "parent_inventory_record_id")
            _ordinal(value.source_ordinal, physical=False)
            _sha(value.payload_sha256, "payload_sha256")
            expected_dispositions = {
                "messages": {"message_created"},
                "summaries": {"summary_created", "preserved_in_metadata"},
                "session_core_metadata": {"preserved_in_metadata"},
                "metadata": {"preserved_in_metadata"},
                "uploads": {"preserved_in_metadata"},
                "bindings": {"preserved_in_metadata"},
            }
            if value.result_disposition not in expected_dispositions[value.child_kind]:
                raise ValueError("P2.5 session-child disposition is invalid.")
            if value.child_kind == "messages":
                _positive(value.message_sequence_no, "message_sequence_no")
            elif value.message_sequence_no is not None:
                raise ValueError("Only a message child has a sequence number.")
        else:
            raise ValueError("P2.5 session import_mode is invalid.")
        return value, _canonical(value.as_dict(), "session_details")

    @staticmethod
    def _prepare_notion_details(
        value: LegacyP25NotionDetails,
    ) -> tuple[LegacyP25NotionDetails, str]:
        if not isinstance(value, LegacyP25NotionDetails):
            raise TypeError("LegacyP25NotionDetails is required.")
        _sha(value.plan_sha256, "plan_sha256")
        _sha(value.inventory_manifest_sha256, "inventory_manifest_sha256")
        _sha(value.root_binding_sha256, "root_binding_sha256")
        _record(value.inventory_record_id)
        _sha(value.inventory_record_sha256, "inventory_record_sha256")
        _sha(value.path_key_sha256, "path_key_sha256")
        _sha(value.source_record_sha256, "source_record_sha256")
        children = _sorted_records(
            value.planned_import_child_record_ids,
            "planned_import_child_record_id",
        )
        relationships = _sorted_relationships(
            value.planned_import_relationship_ids
        )
        _sha(value.imported_child_bindings_sha256, "imported_child_bindings_sha256")
        _sha(
            value.imported_relationship_bindings_sha256,
            "imported_relationship_bindings_sha256",
        )
        if value.import_mode == "source_container":
            if (
                value.record_kind != "physical_file"
                or value.parent_inventory_record_id is not None
                or _ordinal(value.source_ordinal, physical=True) != 0
                or value.result_disposition != "dependency_closed"
                or value.target_space_id is not None
                or value.conversation_id is not None
                or value.relationship_id is not None
                or value.inventory_relationship_sha256 is not None
                or value.page_identity_sha256 is not None
                or value.notion_row_sha256 is not None
            ):
                raise ValueError("P2.5 Notion-container detail shape is invalid.")
        elif value.import_mode == "notion_session_link":
            if (
                value.record_kind != "logical_record"
                or value.parent_inventory_record_id is None
                or value.parent_inventory_record_id == value.inventory_record_id
                or _ordinal(value.source_ordinal, physical=False) < 1
                or value.result_disposition != "binding_created"
                or value.target_space_id is None
                or value.conversation_id is None
                or value.relationship_id is None
                or value.inventory_relationship_sha256 is None
                or value.page_identity_sha256 is None
                or value.notion_row_sha256 is None
                or children
                or relationships
                or value.imported_child_bindings_sha256
                != p25_child_bindings_sha256(())
                or value.imported_relationship_bindings_sha256
                != p25_relationship_bindings_sha256(())
            ):
                raise ValueError("P2.5 Notion-link detail shape is invalid.")
            _record(value.parent_inventory_record_id, "parent_inventory_record_id")
            _uuid(value.target_space_id, "target_space_id")
            _uuid(value.conversation_id, "conversation_id")
            _relationship(value.relationship_id)
            _sha(value.inventory_relationship_sha256, "inventory_relationship_sha256")
            _sha(value.page_identity_sha256, "page_identity_sha256")
            _sha(value.notion_row_sha256, "notion_row_sha256")
        else:
            raise ValueError("P2.5 Notion import_mode is invalid.")
        return value, _canonical(value.as_dict(), "notion_details")

    @staticmethod
    def _prepare_job_details(
        value: LegacyP25JobDetails,
    ) -> tuple[LegacyP25JobDetails, str]:
        if not isinstance(value, LegacyP25JobDetails):
            raise TypeError("LegacyP25JobDetails is required.")
        for field in (
            "plan_sha256",
            "inventory_manifest_sha256",
            "root_binding_sha256",
            "inventory_record_sha256",
            "path_key_sha256",
            "source_record_sha256",
            "job_identity_sha256",
            "job_evidence_sha256",
        ):
            _sha(getattr(value, field), field)
        _record(value.inventory_record_id)
        if not isinstance(value.root_id, str) or _IDENTIFIER_RE.fullmatch(value.root_id) is None:
            raise ValueError("root_id is invalid.")
        _uuid(value.target_space_id, "target_space_id")
        terminal = {"cancelled", "failed", "succeeded"}
        abandoned = {"queued", "running", "unknown"}
        if value.source_status in terminal:
            expected = ("job_history", "terminal_history_recorded")
        elif value.source_status in abandoned:
            expected = ("abandoned_legacy_job", "abandoned_historical")
        else:
            raise ValueError("P2.5 legacy job status is invalid.")
        if (value.import_mode, value.result_disposition) != expected:
            raise ValueError("P2.5 legacy job policy is invalid.")
        return value, _canonical(value.as_dict(), "job_details")

    @staticmethod
    def _prepare_job_evidence(
        details: LegacyP25JobDetails,
        value: str,
    ) -> str:
        encoded = _canonical(value, "job_evidence_json")
        parsed = json.loads(encoded)
        required = {
            "authority",
            "category",
            "execution_policy",
            "inventory_record_id",
            "job_identity_sha256",
            "media_type",
            "raw_sha256",
            "schema_version",
            "size_bytes",
            "source_status",
        }
        valid = (
            set(parsed) == required
            and parsed.get("authority") == "historical_only"
            and parsed.get("category") == "operations.jobs"
            and parsed.get("execution_policy") == "never_replay"
            and parsed.get("inventory_record_id") == details.inventory_record_id
            and parsed.get("job_identity_sha256") == details.job_identity_sha256
            and parsed.get("media_type") == "application/json"
            and parsed.get("raw_sha256") == details.source_record_sha256
            and parsed.get("schema_version") == 1
            and type(parsed.get("size_bytes")) is int
            and 0 <= int(parsed["size_bytes"]) <= _SQLITE_INTEGER_MAX
            and parsed.get("source_status") == details.source_status
        )
        if not valid:
            raise ValueError("P2.5 legacy job evidence shape is invalid.")
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != details.job_evidence_sha256:
            raise ValueError("P2.5 legacy job evidence digest differs.")
        return encoded

    @staticmethod
    def _verify_session_effect(
        connection: ManagedConnection,
        target: LegacyP25SessionTarget,
    ) -> None:
        details = target.details
        owner = _user(target.resolved_owner_user_id)
        conversation = _uuid(target.conversation_id, "conversation_id")
        if conversation != details.conversation_id:
            raise LegacyP25MappingIntegrityError(
                "The session checkpoint conversation differs from its details."
            )
        message_id = _optional_uuid(target.message_id, "message_id")
        summary_id = _optional_uuid(target.summary_id, "summary_id")
        row = _conversation_row(
            connection,
            conversation_id=conversation,
            owner=owner,
            space=details.target_space_id,
        )
        legacy = _legacy_session_metadata(row)
        parent_id = (
            details.inventory_record_id
            if details.import_mode == "source_container"
            else str(details.parent_inventory_record_id)
        )
        evidence = _session_evidence(
            legacy,
            plan=details.plan_sha256,
            parent_record_id=parent_id,
            session_id_sha256=details.session_id_sha256,
        )
        if details.import_mode == "source_container":
            evidence_ids = tuple(
                sorted(
                    str(item.get("inventory_record_id"))
                    for item in evidence
                    if isinstance(item.get("inventory_record_id"), str)
                )
            )
            if (
                message_id is not None
                or summary_id is not None
                or evidence_ids != details.planned_import_child_record_ids
            ):
                raise LegacyP25MappingIntegrityError(
                    "The session container evidence does not match its children."
                )
            return
        matches = [
            item
            for item in evidence
            if item.get("inventory_record_id") == details.inventory_record_id
        ]
        if len(matches) != 1 or matches[0] != {
            "child_kind": details.child_kind,
            "inventory_record_id": details.inventory_record_id,
            "payload_sha256": details.payload_sha256,
            "source_ordinal": details.source_ordinal,
        }:
            raise LegacyP25MappingIntegrityError(
                "The session child is not bound to exact conversation evidence."
            )
        if details.child_kind == "messages":
            if message_id is None or summary_id is not None:
                raise LegacyP25MappingIntegrityError(
                    "A message child requires exactly one message target."
                )
            message = connection.execute(
                """SELECT conversation_id,sequence_no,content_json
                   FROM conversation_message WHERE id=?""",
                (message_id,),
            ).fetchone()
            if message is None:
                raise LegacyP25MappingIntegrityError(
                    "The canonical legacy message is missing."
                )
            try:
                payload = json.loads(str(message["content_json"]))
                payload_sha = hashlib.sha256(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            except (TypeError, ValueError, json.JSONDecodeError):
                raise LegacyP25MappingIntegrityError(
                    "The canonical legacy message payload is invalid."
                ) from None
            if (
                message["conversation_id"] != conversation
                or message["sequence_no"] != details.message_sequence_no
                or payload_sha != details.payload_sha256
            ):
                raise LegacyP25MappingIntegrityError(
                    "The canonical legacy message differs from source evidence."
                )
        elif details.result_disposition == "summary_created":
            if summary_id is None or message_id is not None:
                raise LegacyP25MappingIntegrityError(
                    "A created summary requires exactly one summary target."
                )
            summary = connection.execute(
                """SELECT conversation_id,status FROM conversation_summary
                   WHERE id=?""",
                (summary_id,),
            ).fetchone()
            if (
                summary is None
                or summary["conversation_id"] != conversation
                or summary["status"] != "active"
            ):
                raise LegacyP25MappingIntegrityError(
                    "The canonical legacy summary is missing or incompatible."
                )
        elif message_id is not None or summary_id is not None:
            raise LegacyP25MappingIntegrityError(
                "Metadata-only session children cannot claim child table targets."
            )

    @staticmethod
    def _verify_notion_effect(
        connection: ManagedConnection,
        target: LegacyP25NotionTarget,
    ) -> None:
        details = target.details
        owner = _user(target.resolved_owner_user_id)
        conversation = _optional_uuid(target.conversation_id, "conversation_id")
        binding_id = _optional_uuid(target.context_binding_id, "context_binding_id")
        if details.import_mode == "source_container":
            if conversation is not None or binding_id is not None:
                raise LegacyP25MappingIntegrityError(
                    "A Notion source container cannot claim a canonical binding."
                )
            return
        if conversation != details.conversation_id or conversation is None or binding_id is None:
            raise LegacyP25MappingIntegrityError(
                "The Notion link canonical targets differ from its details."
            )
        _conversation_row(
            connection,
            conversation_id=conversation,
            owner=owner,
            space=str(details.target_space_id),
        )
        row = connection.execute(
            """SELECT conversation_id,binding_type,binding_mode,external_key,
                       display_metadata_json,status,created_by
               FROM conversation_context_binding WHERE id=?""",
            (binding_id,),
        ).fetchone()
        if row is None:
            raise LegacyP25MappingIntegrityError(
                "The canonical Notion context binding is missing."
            )
        try:
            metadata = json.loads(str(row["display_metadata_json"]))
            evidence = metadata["legacy_p2_5"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise LegacyP25MappingIntegrityError(
                "The Notion context binding evidence is invalid."
            ) from None
        expected_evidence = {
            "notion_row_sha256": details.notion_row_sha256,
            "page_identity_sha256": details.page_identity_sha256,
            "relationship_id": details.relationship_id,
            "schema_version": 1,
        }
        if (
            row["conversation_id"] != conversation
            or row["binding_type"] != "notion_page"
            or row["binding_mode"] != "live"
            or hashlib.sha256(str(row["external_key"]).encode("utf-8")).hexdigest()
            != details.page_identity_sha256
            or row["status"] != "active"
            or row["created_by"] != owner
            or evidence != expected_evidence
        ):
            raise LegacyP25MappingIntegrityError(
                "The canonical Notion context binding differs from source evidence."
            )

    @staticmethod
    def _verify_job_effect(
        connection: ManagedConnection,
        target: LegacyP25JobTarget,
        *,
        locator: str,
        evidence_json: str,
    ) -> None:
        details = target.details
        owner = _user(target.resolved_owner_user_id)
        artifact_id = _uuid(target.artifact_id, "artifact_id")
        audit_event_id = _audit_id(target.audit_event_id)
        audit_event_uuid = _uuid(target.audit_event_uuid, "audit_event_uuid")
        personal = connection.execute(
            """SELECT 1 FROM knowledge_space
               WHERE id=? AND space_kind='personal' AND owner_user_id=?
                 AND status='active'""",
            (details.target_space_id, owner),
        ).fetchone()
        artifact_rows = connection.execute(
            """SELECT artifact.publisher_user_id,artifact.origin_type,
                      artifact.origin_key,artifact.origin_revision_key,
                      artifact.raw_sha256,artifact.raw_size_bytes,
                      artifact.media_type,artifact.body_retention_status,
                      manifest.content_sha256,manifest.size_bytes,manifest.role,
                      manifest.ordinal,manifest.media_type,body.content_sha256
               FROM source_artifact AS artifact
               JOIN artifact_blob AS manifest
                 ON manifest.artifact_id=artifact.id AND manifest.role='raw'
               JOIN artifact_blob_body AS body
                 ON body.artifact_blob_id=manifest.id
               WHERE artifact.id=?""",
            (artifact_id,),
        ).fetchall()
        evidence = json.loads(evidence_json)
        expected_artifact = (
            owner,
            "legacy",
            locator,
            details.inventory_record_id,
            details.source_record_sha256,
            evidence["size_bytes"],
            "application/json",
            "present",
            details.source_record_sha256,
            evidence["size_bytes"],
            "raw",
            0,
            "application/json",
            details.source_record_sha256,
        )
        audit = connection.execute(
            """SELECT event_uuid,action,outcome,actor_kind,actor_user_id,
                      space_id,details_json,schema_version
               FROM audit_event WHERE id=?""",
            (audit_event_id,),
        ).fetchone()
        if details.import_mode == "abandoned_legacy_job":
            expected_action = "legacy_job.abandoned_history"
            expected_outcome = "abandoned_historical"
        else:
            expected_action = "legacy_job.terminal_history"
            expected_outcome = "terminal_history_recorded"
        expected_audit = (
            audit_event_uuid,
            expected_action,
            expected_outcome,
            "system",
            None,
            details.target_space_id,
            evidence_json,
            1,
        )
        if (
            personal is None
            or len(artifact_rows) != 1
            or tuple(artifact_rows[0]) != expected_artifact
            or audit is None
            or tuple(audit) != expected_audit
        ):
            raise LegacyP25MappingIntegrityError(
                "The canonical legacy job history effect is not exact."
            )

    @staticmethod
    def _record_expected(
        *,
        mapping_id: str,
        run_id: str,
        legacy_kind: str,
        locator: str,
        source_sha256: str,
        owner: str,
        details_json: str,
        now: int,
        conversation_id: str | None,
        message_id: str | None,
        summary_id: str | None,
        context_binding_id: str | None,
        artifact_id: str | None = None,
        audit_event_id: int | None = None,
    ) -> tuple[object, ...]:
        return (
            mapping_id, run_id, legacy_kind, locator, source_sha256, owner,
            artifact_id, None, None, None, "imported", details_json, now, now,
            None, audit_event_id, conversation_id, message_id, summary_id,
            context_binding_id,
        )

    @staticmethod
    def _insert_record(
        connection: ManagedConnection,
        *,
        expected: tuple[object, ...],
    ) -> None:
        try:
            connection.execute(
                """INSERT INTO legacy_migration_map(
                     id,migration_run_id,legacy_kind,normalized_path,source_sha256,
                     resolved_owner_user_id,artifact_id,item_id,revision_id,edge_id,
                     status,details_json,created_at,updated_at,finding_id,
                     audit_event_id,conversation_id,message_id,summary_id,
                     context_binding_id
                   ) VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,'imported',?,?,?,
                             NULL,?,?,?,?,?)""",
                (
                    expected[0], expected[1], expected[2], expected[3], expected[4],
                    expected[5], expected[6], expected[11], expected[12], expected[13],
                    expected[15],
                    expected[16], expected[17], expected[18], expected[19],
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyP25MappingConflict(
                "The P2.5 record checkpoint violates durable constraints."
            ) from exc

    @staticmethod
    def _preflight_identity(
        connection: ManagedConnection,
        *,
        mapping_id: str,
        locator: str,
        expected: tuple[object, ...],
    ) -> bool:
        existing = connection.execute(
            f"SELECT {','.join(_RECORD_COLUMNS)} FROM legacy_migration_map WHERE id=?",
            (mapping_id,),
        ).fetchone()
        occupied = connection.execute(
            "SELECT id FROM legacy_migration_map WHERE normalized_path=?",
            (locator,),
        ).fetchone()
        if existing is not None:
            if _tuple(existing, _RECORD_COLUMNS) != expected:
                raise LegacyP25MappingConflict("The P2.5 record checkpoint differs.")
            return False
        if occupied is not None:
            raise LegacyP25MappingConflict(
                "The stable P2.5 source locator is already occupied."
            )
        return True

    def put_session_imported(
        self,
        connection: ManagedConnection,
        *,
        target: LegacyP25SessionTarget,
        now_ms: int,
    ) -> tuple[LegacyP25Record, bool]:
        connection = _connection(connection)
        if not isinstance(target, LegacyP25SessionTarget):
            raise TypeError("LegacyP25SessionTarget is required.")
        details, details_json = self._prepare_session_details(target.details)
        owner = _user(target.resolved_owner_user_id)
        conversation = _uuid(target.conversation_id, "conversation_id")
        message_id = _optional_uuid(target.message_id, "message_id")
        summary_id = _optional_uuid(target.summary_id, "summary_id")
        locator = _path_locator(
            root_id=details.root_id,
            path_key_sha256=details.path_key_sha256,
            path_key=target.source_path_key,
            record_kind=details.record_kind,
            source_ordinal=details.source_ordinal,
        )
        mapping_id = migration_mapping_id_for_record(
            details.plan_sha256, details.inventory_record_id
        )
        run_id = migration_run_id_for_plan(details.plan_sha256)
        if details.import_mode == "source_container":
            rows = connection.execute(
                """SELECT id,source_sha256,
                          json_extract(details_json,'$.inventory_record_id') AS record_id
                   FROM legacy_migration_map
                   WHERE json_extract(details_json,'$.schema_version')=5
                     AND json_extract(details_json,'$.plan_sha256')=?
                     AND json_extract(details_json,'$.parent_inventory_record_id')=?
                     AND json_extract(details_json,'$.import_mode')='conversation_child'
                   ORDER BY record_id""",
                (details.plan_sha256, details.inventory_record_id),
            ).fetchall()
            record_ids = tuple(str(row["record_id"]) for row in rows)
            bindings = [
                (str(row["id"]), str(row["record_id"]), str(row["source_sha256"]))
                for row in rows
            ]
            if (
                record_ids != details.planned_import_child_record_ids
                or p25_child_bindings_sha256(bindings)
                != details.imported_child_bindings_sha256
            ):
                raise LegacyP25MappingIntegrityError(
                    "The P2.5 session child closure is incomplete."
                )
        else:
            closed = connection.execute(
                """SELECT id FROM legacy_migration_map
                   WHERE json_extract(details_json,'$.schema_version')=5
                     AND json_extract(details_json,'$.plan_sha256')=?
                     AND json_extract(details_json,'$.inventory_record_id')=?
                     AND json_extract(details_json,'$.import_mode')='source_container'""",
                (details.plan_sha256, details.parent_inventory_record_id),
            ).fetchone()
            existing = connection.execute(
                "SELECT id FROM legacy_migration_map WHERE id=?", (mapping_id,)
            ).fetchone()
            if closed is not None and existing is None:
                raise LegacyP25MappingConflict(
                    "The P2.5 session parent is already dependency-closed."
                )
        self._verify_session_effect(connection, target)
        now = _now(now_ms)
        expected = self._record_expected(
            mapping_id=mapping_id,
            run_id=run_id,
            legacy_kind=details.category,
            locator=locator,
            source_sha256=details.source_record_sha256,
            owner=owner,
            details_json=details_json,
            now=now,
            conversation_id=conversation,
            message_id=message_id,
            summary_id=summary_id,
            context_binding_id=None,
        )
        inserted = self._preflight_identity(
            connection,
            mapping_id=mapping_id,
            locator=locator,
            expected=expected,
        )
        if inserted:
            self._insert_record(connection, expected=expected)
        return LegacyP25Record(mapping_id, run_id, details_json, now), inserted

    def put_notion_imported(
        self,
        connection: ManagedConnection,
        *,
        target: LegacyP25NotionTarget,
        now_ms: int,
    ) -> tuple[LegacyP25Record, bool]:
        connection = _connection(connection)
        if not isinstance(target, LegacyP25NotionTarget):
            raise TypeError("LegacyP25NotionTarget is required.")
        details, details_json = self._prepare_notion_details(target.details)
        owner = _user(target.resolved_owner_user_id)
        conversation = _optional_uuid(target.conversation_id, "conversation_id")
        context_binding = _optional_uuid(
            target.context_binding_id, "context_binding_id"
        )
        locator = _path_locator(
            root_id=details.root_id,
            path_key_sha256=details.path_key_sha256,
            path_key=target.source_path_key,
            record_kind=details.record_kind,
            source_ordinal=details.source_ordinal,
        )
        mapping_id = migration_mapping_id_for_record(
            details.plan_sha256, details.inventory_record_id
        )
        run_id = migration_run_id_for_plan(details.plan_sha256)
        if details.import_mode == "source_container":
            children = connection.execute(
                """SELECT id,source_sha256,
                          json_extract(details_json,'$.inventory_record_id') AS record_id
                   FROM legacy_migration_map
                   WHERE json_extract(details_json,'$.schema_version')=6
                     AND json_extract(details_json,'$.plan_sha256')=?
                     AND json_extract(details_json,'$.parent_inventory_record_id')=?
                     AND json_extract(details_json,'$.import_mode')='notion_session_link'
                   ORDER BY record_id""",
                (details.plan_sha256, details.inventory_record_id),
            ).fetchall()
            relationships = connection.execute(
                """SELECT id,relationship_id,relationship_sha256
                   FROM legacy_migration_p25_relationship_map
                   WHERE migration_run_id=?
                     AND json_extract(details_json,'$.source_inventory_record_id')
                         IN (
                           SELECT json_extract(details_json,'$.inventory_record_id')
                           FROM legacy_migration_map
                           WHERE json_extract(details_json,'$.schema_version')=6
                             AND json_extract(details_json,'$.plan_sha256')=?
                             AND json_extract(details_json,'$.parent_inventory_record_id')=?
                         )
                   ORDER BY relationship_id""",
                (run_id, details.plan_sha256, details.inventory_record_id),
            ).fetchall()
            child_ids = tuple(str(row["record_id"]) for row in children)
            relationship_ids = tuple(
                str(row["relationship_id"]) for row in relationships
            )
            child_bindings = [
                (str(row["id"]), str(row["record_id"]), str(row["source_sha256"]))
                for row in children
            ]
            relationship_bindings = [
                (
                    str(row["id"]),
                    str(row["relationship_id"]),
                    str(row["relationship_sha256"]),
                )
                for row in relationships
            ]
            if (
                child_ids != details.planned_import_child_record_ids
                or relationship_ids != details.planned_import_relationship_ids
                or p25_child_bindings_sha256(child_bindings)
                != details.imported_child_bindings_sha256
                or p25_relationship_bindings_sha256(relationship_bindings)
                != details.imported_relationship_bindings_sha256
            ):
                raise LegacyP25MappingIntegrityError(
                    "The P2.5 Notion source closure is incomplete."
                )
        else:
            closed = connection.execute(
                """SELECT id FROM legacy_migration_map
                   WHERE json_extract(details_json,'$.schema_version')=6
                     AND json_extract(details_json,'$.plan_sha256')=?
                     AND json_extract(details_json,'$.inventory_record_id')=?
                     AND json_extract(details_json,'$.import_mode')='source_container'""",
                (details.plan_sha256, details.parent_inventory_record_id),
            ).fetchone()
            existing = connection.execute(
                "SELECT id FROM legacy_migration_map WHERE id=?", (mapping_id,)
            ).fetchone()
            if closed is not None and existing is None:
                raise LegacyP25MappingConflict(
                    "The P2.5 Notion parent is already dependency-closed."
                )
        self._verify_notion_effect(connection, target)
        now = _now(now_ms)
        expected = self._record_expected(
            mapping_id=mapping_id,
            run_id=run_id,
            legacy_kind="external.notion_session_links",
            locator=locator,
            source_sha256=details.source_record_sha256,
            owner=owner,
            details_json=details_json,
            now=now,
            conversation_id=conversation,
            message_id=None,
            summary_id=None,
            context_binding_id=context_binding,
        )
        inserted = self._preflight_identity(
            connection,
            mapping_id=mapping_id,
            locator=locator,
            expected=expected,
        )
        if inserted:
            self._insert_record(connection, expected=expected)
        return LegacyP25Record(mapping_id, run_id, details_json, now), inserted

    def put_job_imported(
        self,
        connection: ManagedConnection,
        *,
        target: LegacyP25JobTarget,
        now_ms: int,
    ) -> tuple[LegacyP25Record, bool]:
        connection = _connection(connection)
        if not isinstance(target, LegacyP25JobTarget):
            raise TypeError("LegacyP25JobTarget is required.")
        details, details_json = self._prepare_job_details(target.details)
        evidence_json = self._prepare_job_evidence(
            details, target.job_evidence_json
        )
        owner = _user(target.resolved_owner_user_id)
        artifact_id = _uuid(target.artifact_id, "artifact_id")
        audit_event_id = _audit_id(target.audit_event_id)
        _uuid(target.audit_event_uuid, "audit_event_uuid")
        locator = _path_locator(
            root_id=details.root_id,
            path_key_sha256=details.path_key_sha256,
            path_key=target.source_path_key,
            record_kind="physical_file",
            source_ordinal=0,
        )
        self._verify_job_effect(
            connection,
            target,
            locator=locator,
            evidence_json=evidence_json,
        )
        mapping_id = migration_mapping_id_for_record(
            details.plan_sha256, details.inventory_record_id
        )
        run_id = migration_run_id_for_plan(details.plan_sha256)
        now = _now(now_ms)
        expected = self._record_expected(
            mapping_id=mapping_id,
            run_id=run_id,
            legacy_kind="operations.jobs",
            locator=locator,
            source_sha256=details.source_record_sha256,
            owner=owner,
            details_json=details_json,
            now=now,
            conversation_id=None,
            message_id=None,
            summary_id=None,
            context_binding_id=None,
            artifact_id=artifact_id,
            audit_event_id=audit_event_id,
        )
        inserted = self._preflight_identity(
            connection,
            mapping_id=mapping_id,
            locator=locator,
            expected=expected,
        )
        if inserted:
            self._insert_record(connection, expected=expected)
        return LegacyP25Record(mapping_id, run_id, details_json, now), inserted


class LegacyP25RelationshipRepository:
    __slots__ = ()

    @staticmethod
    def _prepare_details(
        value: LegacyP25RelationshipDetails,
    ) -> tuple[LegacyP25RelationshipDetails, str]:
        if not isinstance(value, LegacyP25RelationshipDetails):
            raise TypeError("LegacyP25RelationshipDetails is required.")
        _sha(value.plan_sha256, "plan_sha256")
        _relationship(value.relationship_id)
        _sha(value.inventory_manifest_sha256, "inventory_manifest_sha256")
        _sha(value.root_binding_sha256, "root_binding_sha256")
        _sha(value.inventory_relationship_sha256, "inventory_relationship_sha256")
        _record(value.source_inventory_record_id, "source_inventory_record_id")
        _record(value.from_inventory_record_id, "from_inventory_record_id")
        if value.source_inventory_record_id == value.from_inventory_record_id:
            raise ValueError("P2.5 relationship endpoints must be distinct.")
        _sha(value.page_identity_sha256, "page_identity_sha256")
        _sha(value.notion_row_sha256, "notion_row_sha256")
        return value, _canonical(value.as_dict(), "relationship_details")

    @staticmethod
    def _mapping(
        connection: ManagedConnection,
        *,
        plan: str,
        record_id: str,
    ):
        rows = connection.execute(
            f"""SELECT {','.join(_RECORD_COLUMNS)} FROM legacy_migration_map
                WHERE json_extract(details_json,'$.plan_sha256')=?
                  AND json_extract(details_json,'$.inventory_record_id')=?""",
            (plan, record_id),
        ).fetchall()
        if len(rows) != 1:
            raise LegacyP25MappingIntegrityError(
                "A P2.5 relationship endpoint mapping is missing or ambiguous."
            )
        return rows[0]

    def put_imported(
        self,
        connection: ManagedConnection,
        *,
        target: LegacyP25RelationshipTarget,
        now_ms: int,
    ) -> tuple[LegacyP25RelationshipRecord, bool]:
        connection = _connection(connection)
        if not isinstance(target, LegacyP25RelationshipTarget):
            raise TypeError("LegacyP25RelationshipTarget is required.")
        details, details_json = self._prepare_details(target.details)
        source_mapping = _uuid(target.source_mapping_id, "source_mapping_id")
        from_mapping = _uuid(target.from_mapping_id, "from_mapping_id")
        if source_mapping == from_mapping:
            raise ValueError("P2.5 relationship mapping endpoints must be distinct.")
        conversation = _uuid(target.conversation_id, "conversation_id")
        context_binding = _uuid(target.context_binding_id, "context_binding_id")
        owner = _user(target.resolved_owner_user_id)
        space = _uuid(target.target_space_id, "target_space_id")
        source = self._mapping(
            connection,
            plan=details.plan_sha256,
            record_id=details.source_inventory_record_id,
        )
        origin = self._mapping(
            connection,
            plan=details.plan_sha256,
            record_id=details.from_inventory_record_id,
        )
        source_details = json.loads(str(source["details_json"]))
        origin_details = json.loads(str(origin["details_json"]))
        if (
            source["id"] != source_mapping
            or source_details.get("schema_version") != 6
            or source_details.get("import_mode") != "notion_session_link"
            or source_details.get("relationship_id") != details.relationship_id
            or source_details.get("inventory_relationship_sha256")
            != details.inventory_relationship_sha256
            or source_details.get("page_identity_sha256")
            != details.page_identity_sha256
            or source_details.get("notion_row_sha256") != details.notion_row_sha256
            or source["conversation_id"] != conversation
            or source["context_binding_id"] != context_binding
            or source["resolved_owner_user_id"] != owner
            or origin["id"] != from_mapping
            or origin_details.get("schema_version") != 5
            or origin_details.get("import_mode") != "source_container"
            or origin["conversation_id"] != conversation
            or origin["resolved_owner_user_id"] != owner
            or source_details.get("inventory_manifest_sha256")
            != details.inventory_manifest_sha256
            or origin_details.get("inventory_manifest_sha256")
            != details.inventory_manifest_sha256
            or source_details.get("root_binding_sha256") != details.root_binding_sha256
            or origin_details.get("root_binding_sha256") != details.root_binding_sha256
            or source_details.get("target_space_id") != space
            or origin_details.get("target_space_id") != space
        ):
            raise LegacyP25MappingIntegrityError(
                "The P2.5 Notion relationship endpoints are not exact."
            )
        binding = connection.execute(
            """SELECT conversation_id,binding_type,binding_mode,external_key,status,created_by,
                      display_metadata_json
               FROM conversation_context_binding WHERE id=?""",
            (context_binding,),
        ).fetchone()
        if binding is None:
            raise LegacyP25MappingIntegrityError(
                "The P2.5 Notion relationship binding is missing."
            )
        try:
            evidence = json.loads(str(binding["display_metadata_json"]))[
                "legacy_p2_5"
            ]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise LegacyP25MappingIntegrityError(
                "The P2.5 Notion relationship evidence is invalid."
            ) from None
        if (
            binding["conversation_id"] != conversation
            or binding["binding_type"] != "notion_page"
            or binding["binding_mode"] != "live"
            or hashlib.sha256(
                str(binding["external_key"]).encode("utf-8")
            ).hexdigest()
            != details.page_identity_sha256
            or binding["status"] != "active"
            or binding["created_by"] != owner
            or evidence
            != {
                "notion_row_sha256": details.notion_row_sha256,
                "page_identity_sha256": details.page_identity_sha256,
                "relationship_id": details.relationship_id,
                "schema_version": 1,
            }
        ):
            raise LegacyP25MappingIntegrityError(
                "The P2.5 Notion relationship canonical effect differs."
            )
        closed_parent = connection.execute(
            """SELECT id FROM legacy_migration_map
               WHERE json_extract(details_json,'$.schema_version')=6
                 AND json_extract(details_json,'$.plan_sha256')=?
                 AND json_extract(details_json,'$.inventory_record_id')=?
                 AND json_extract(details_json,'$.import_mode')='source_container'""",
            (details.plan_sha256, source_details.get("parent_inventory_record_id")),
        ).fetchone()
        mapping_id = p25_relationship_mapping_id(
            details.plan_sha256, details.relationship_id
        )
        existing = connection.execute(
            f"""SELECT {','.join(_RELATIONSHIP_COLUMNS)}
                FROM legacy_migration_p25_relationship_map WHERE id=?""",
            (mapping_id,),
        ).fetchone()
        if closed_parent is not None and existing is None:
            raise LegacyP25MappingConflict(
                "The P2.5 Notion parent is already relationship-closed."
            )
        run_id = migration_run_id_for_plan(details.plan_sha256)
        now = _now(now_ms)
        expected = (
            mapping_id,
            run_id,
            details.relationship_id,
            details.inventory_relationship_sha256,
            "notion_session_page",
            source_mapping,
            from_mapping,
            conversation,
            context_binding,
            owner,
            space,
            details.page_identity_sha256,
            details.notion_row_sha256,
            "imported",
            details_json,
            now,
            now,
        )
        identity = connection.execute(
            """SELECT id FROM legacy_migration_p25_relationship_map
               WHERE relationship_id=?""",
            (details.relationship_id,),
        ).fetchone()
        if existing is not None:
            if (
                _tuple(existing, _RELATIONSHIP_COLUMNS) != expected
                or identity is None
                or identity["id"] != mapping_id
            ):
                raise LegacyP25MappingConflict(
                    "The P2.5 relationship checkpoint differs."
                )
            return LegacyP25RelationshipRecord(
                mapping_id, run_id, details_json, now
            ), False
        if identity is not None:
            raise LegacyP25MappingConflict(
                "The P2.5 relationship identity is already occupied."
            )
        try:
            connection.execute(
                """INSERT INTO legacy_migration_p25_relationship_map(
                     id,migration_run_id,relationship_id,relationship_sha256,
                     relationship_type,source_mapping_id,from_mapping_id,
                     conversation_id,context_binding_id,resolved_owner_user_id,
                     target_space_id,page_identity_sha256,notion_row_sha256,
                     status,details_json,created_at,updated_at
                   ) VALUES (?,?,?,?,'notion_session_page',?,?,?,?,?,?,?,?,
                             'imported',?,?,?)""",
                (
                    mapping_id,
                    run_id,
                    details.relationship_id,
                    details.inventory_relationship_sha256,
                    source_mapping,
                    from_mapping,
                    conversation,
                    context_binding,
                    owner,
                    space,
                    details.page_identity_sha256,
                    details.notion_row_sha256,
                    details_json,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyP25MappingConflict(
                "The P2.5 relationship checkpoint violates durable constraints."
            ) from exc
        return LegacyP25RelationshipRecord(mapping_id, run_id, details_json, now), True


__all__ = [
    "LegacyP25MappingConflict",
    "LegacyP25MappingError",
    "LegacyP25MappingIntegrityError",
    "LegacyP25MappingRepository",
    "LegacyP25JobDetails",
    "LegacyP25JobTarget",
    "LegacyP25NotionDetails",
    "LegacyP25NotionTarget",
    "LegacyP25Record",
    "LegacyP25RelationshipDetails",
    "LegacyP25RelationshipRecord",
    "LegacyP25RelationshipRepository",
    "LegacyP25RelationshipTarget",
    "LegacyP25SessionChildKind",
    "LegacyP25SessionDetails",
    "LegacyP25SessionTarget",
    "p25_child_bindings_sha256",
    "p25_relationship_bindings_sha256",
    "p25_relationship_mapping_id",
]

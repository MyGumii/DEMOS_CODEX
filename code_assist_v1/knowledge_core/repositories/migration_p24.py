"""Immutable P2.4 graph/finding record and relationship checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
from typing import Literal, Mapping
from uuid import UUID, uuid5

from ..db import ManagedConnection
from ..errors import KnowledgeCoreError, KnowledgeCoreTransactionError
from .migration import (
    migration_mapping_id_for_record,
    migration_run_id_for_plan,
    migration_source_locator,
)


P24GraphMode = Literal["graph_node", "graph_edge", "finding"]
P24HistoryMode = Literal["legacy_history", "source_container"]
P24RelationshipType = Literal[
    "candidate_source", "candidate_target", "graph_node_source",
    "contradicts", "extends", "related", "supports",
    "exact_content_duplicate",
]

_RELATIONSHIP_NAMESPACE = UUID("c30607d7-b908-4802-a30d-0d2915234398")
_UUID_RE = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.ASCII)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RECORD_RE = re.compile(r"^rec_[0-9a-f]{24}$", re.ASCII)
_RELATIONSHIP_RE = re.compile(r"^rel_[0-9a-f]{24}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_SQLITE_INTEGER_MAX = 9_223_372_036_854_775_807
_HISTORY_ENUM_VALUES = frozenset(
    {
        "active", "admin", "administrator", "applied", "archived",
        "cancelled", "candidate", "child", "contradicts", "conversation",
        "decision", "deleted", "depends_on", "derived_from", "done",
        "duplicate", "example_of", "extends", "failed", "graph", "import",
        "lesson", "member", "memory", "merge", "note", "parent", "part_of",
        "pending", "personal", "preference", "project", "queued", "reference",
        "refine", "rejected", "related", "replace", "replaces", "restore",
        "references", "requires", "running", "similar", "skipped", "source",
        "source-of", "supports", "supersedes", "system", "thought", "unknown",
        "user",
    }
)
_HISTORY_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "application/octet-stream",
        "application/x-ndjson",
        "text/markdown",
        "text/plain",
    }
)

_RECORD_COLUMNS = (
    "id", "migration_run_id", "legacy_kind", "normalized_path", "source_sha256",
    "resolved_owner_user_id", "artifact_id", "item_id", "revision_id", "edge_id",
    "status", "details_json", "created_at", "updated_at", "finding_id", "audit_event_id",
)
_REL_COLUMNS = (
    "id", "migration_run_id", "relationship_id", "relationship_sha256",
    "relationship_type", "source_mapping_id", "from_mapping_id", "to_mapping_id",
    "resolved_owner_user_id", "target_space_id", "effect_kind", "edge_id",
    "finding_id", "item_id", "status", "details_json", "created_at", "updated_at",
)


class LegacyP24MappingError(KnowledgeCoreError):
    code = "LEGACY_P24_MAPPING_ERROR"


class LegacyP24MappingConflict(LegacyP24MappingError):
    code = "LEGACY_P24_MAPPING_CONFLICT"


class LegacyP24MappingIntegrityError(LegacyP24MappingError):
    code = "LEGACY_P24_MAPPING_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class LegacyP24GraphDetails:
    plan_sha256: str
    inventory_manifest_sha256: str
    root_binding_sha256: str
    inventory_record_id: str
    inventory_record_sha256: str
    parent_inventory_record_id: str
    root_id: str
    path_key_sha256: str
    category: str
    record_kind: str
    source_ordinal: int
    import_mode: P24GraphMode
    result_disposition: str
    target_space_id: str
    source_record_sha256: str
    endpoint_inventory_record_ids: tuple[str, ...]
    relationship_ids: tuple[str, ...]
    effect_evidence_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "effect_evidence_sha256": self.effect_evidence_sha256,
            "endpoint_inventory_record_ids": list(self.endpoint_inventory_record_ids),
            "import_mode": self.import_mode,
            "inventory_manifest_sha256": self.inventory_manifest_sha256,
            "inventory_record_id": self.inventory_record_id,
            "inventory_record_sha256": self.inventory_record_sha256,
            "parent_inventory_record_id": self.parent_inventory_record_id,
            "path_key_sha256": self.path_key_sha256,
            "plan_sha256": self.plan_sha256,
            "record_kind": self.record_kind,
            "relationship_ids": list(self.relationship_ids),
            "result_disposition": self.result_disposition,
            "root_binding_sha256": self.root_binding_sha256,
            "root_id": self.root_id,
            "schema_version": 2,
            "source_ordinal": self.source_ordinal,
            "source_record_sha256": self.source_record_sha256,
            "target_space_id": self.target_space_id,
        }


@dataclass(frozen=True, slots=True)
class LegacyP24GraphTarget:
    resolved_owner_user_id: str
    item_id: str | None
    revision_id: str | None
    edge_id: str | None
    finding_id: str | None
    details: LegacyP24GraphDetails
    source_path_key: str
    effect_evidence_json: str


@dataclass(frozen=True, slots=True)
class LegacyP24GraphRecord:
    id: str
    migration_run_id: str
    legacy_kind: str
    normalized_path: str
    source_sha256: str
    target: LegacyP24GraphTarget
    details_json: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class LegacyP24ContainerDetails:
    plan_sha256: str
    inventory_manifest_sha256: str
    root_binding_sha256: str
    inventory_record_id: str
    inventory_record_sha256: str
    root_id: str
    path_key_sha256: str
    category: str
    source_record_sha256: str
    target_space_id: str
    planned_import_child_record_ids: tuple[str, ...]
    planned_import_relationship_ids: tuple[str, ...]
    imported_child_bindings_sha256: str
    imported_relationship_bindings_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "execution_phase": "P2.4",
            "final_terminal_classification": "imported",
            "import_mode": "source_container",
            "imported_child_bindings_sha256": self.imported_child_bindings_sha256,
            "imported_relationship_bindings_sha256": self.imported_relationship_bindings_sha256,
            "inventory_manifest_sha256": self.inventory_manifest_sha256,
            "inventory_record_id": self.inventory_record_id,
            "inventory_record_sha256": self.inventory_record_sha256,
            "path_key_sha256": self.path_key_sha256,
            "plan_sha256": self.plan_sha256,
            "planned_import_child_record_ids": list(
                self.planned_import_child_record_ids
            ),
            "planned_import_relationship_ids": list(
                self.planned_import_relationship_ids
            ),
            "record_kind": "physical_file",
            "result_disposition": "dependency_closed",
            "root_binding_sha256": self.root_binding_sha256,
            "root_id": self.root_id,
            "schema_version": 3,
            "source_ordinal": 0,
            "source_record_sha256": self.source_record_sha256,
            "target_space_id": self.target_space_id,
        }


@dataclass(frozen=True, slots=True)
class LegacyP24ContainerTarget:
    resolved_owner_user_id: str
    details: LegacyP24ContainerDetails
    source_path_key: str


@dataclass(frozen=True, slots=True)
class LegacyP24ContainerRecord:
    id: str
    migration_run_id: str
    target: LegacyP24ContainerTarget
    details_json: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class LegacyP24HistoryDetails:
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
    import_mode: P24HistoryMode
    result_disposition: str
    target_space_id: str | None
    source_record_sha256: str
    history_evidence_sha256: str
    planned_import_child_record_ids: tuple[str, ...]
    imported_child_bindings_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "history_evidence_sha256": self.history_evidence_sha256,
            "import_mode": self.import_mode,
            "imported_child_bindings_sha256": self.imported_child_bindings_sha256,
            "inventory_manifest_sha256": self.inventory_manifest_sha256,
            "inventory_record_id": self.inventory_record_id,
            "inventory_record_sha256": self.inventory_record_sha256,
            "parent_inventory_record_id": self.parent_inventory_record_id,
            "path_key_sha256": self.path_key_sha256,
            "plan_sha256": self.plan_sha256,
            "planned_import_child_record_ids": list(
                self.planned_import_child_record_ids
            ),
            "record_kind": self.record_kind,
            "result_disposition": self.result_disposition,
            "root_binding_sha256": self.root_binding_sha256,
            "root_id": self.root_id,
            "schema_version": 4,
            "source_ordinal": self.source_ordinal,
            "source_record_sha256": self.source_record_sha256,
            "target_space_id": self.target_space_id,
        }


@dataclass(frozen=True, slots=True)
class LegacyP24HistoryTarget:
    resolved_owner_user_id: str | None
    artifact_id: str | None
    audit_event_id: int | None
    audit_event_uuid: str | None
    details: LegacyP24HistoryDetails
    source_path_key: str
    history_evidence_json: str


@dataclass(frozen=True, slots=True)
class LegacyP24HistoryRecord:
    id: str
    migration_run_id: str
    target: LegacyP24HistoryTarget
    details_json: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class LegacyP24RelationshipDetails:
    plan_sha256: str
    relationship_id: str
    inventory_manifest_sha256: str
    root_binding_sha256: str
    inventory_relationship_sha256: str
    source_inventory_record_id: str | None
    from_inventory_record_id: str | None
    to_inventory_record_id: str
    effect_mapping_id: str

    def as_dict(self) -> dict[str, object]:
        return {
            "effect_mapping_id": self.effect_mapping_id,
            "from_inventory_record_id": self.from_inventory_record_id,
            "inventory_manifest_sha256": self.inventory_manifest_sha256,
            "inventory_relationship_sha256": self.inventory_relationship_sha256,
            "plan_sha256": self.plan_sha256,
            "relationship_id": self.relationship_id,
            "root_binding_sha256": self.root_binding_sha256,
            "schema_version": 1,
            "source_inventory_record_id": self.source_inventory_record_id,
            "to_inventory_record_id": self.to_inventory_record_id,
        }


@dataclass(frozen=True, slots=True)
class LegacyP24RelationshipTarget:
    relationship_type: P24RelationshipType
    source_mapping_id: str | None
    from_mapping_id: str | None
    to_mapping_id: str
    resolved_owner_user_id: str
    target_space_id: str
    effect_kind: Literal["dependency", "edge", "finding", "provenance"]
    edge_id: str | None
    finding_id: str | None
    item_id: str | None
    details: LegacyP24RelationshipDetails


@dataclass(frozen=True, slots=True)
class LegacyP24RelationshipRecord:
    id: str
    target: LegacyP24RelationshipTarget
    migration_run_id: str
    created_at_ms: int


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 value.")
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _record_id(value: object, field: str = "inventory_record_id") -> str:
    if not isinstance(value, str) or _RECORD_RE.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid.")
    return value


def _relationship_id(value: object) -> str:
    if not isinstance(value, str) or _RELATIONSHIP_RE.fullmatch(value) is None:
        raise ValueError("relationship_id is invalid.")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("resolved_owner_user_id is invalid.")
    return value


def _optional_user(value: object) -> str | None:
    return None if value is None else _user(value)


def _optional_uuid(value: object, field: str) -> str | None:
    return None if value is None else _uuid(value, field)


def _audit_id(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= _SQLITE_INTEGER_MAX
    ):
        raise ValueError("audit_event_id must be a positive integer.")
    return value


def _child_bindings_sha256(
    rows: tuple[tuple[str, str, str], ...] | list[tuple[str, str, str]],
) -> str:
    return hashlib.sha256(
        json.dumps(
            [list(row) for row in rows],
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _ordinal(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _SQLITE_INTEGER_MAX:
        raise ValueError("source_ordinal must be a positive integer.")
    return value


def _now(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _SQLITE_INTEGER_MAX:
        raise ValueError("now_ms is invalid.")
    return value


def _canonical(value: Mapping[str, object] | str, field: str) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, json.JSONDecodeError):
            raise ValueError(f"{field} is invalid.") from None
    else:
        parsed = dict(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{field} is invalid.")
    encoded = json.dumps(parsed, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if isinstance(value, str) and encoded != value:
        raise ValueError(f"{field} must be canonical JSON.")
    return encoded


def p24_relationship_mapping_id(plan_sha256: str, relationship_id: str) -> str:
    return str(uuid5(_RELATIONSHIP_NAMESPACE, f"p2.4:{_sha(plan_sha256, 'plan_sha256')}:{_relationship_id(relationship_id)}"))


def _connection(value: ManagedConnection) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError("A ManagedConnection is required.")
    if not value.in_transaction:
        raise KnowledgeCoreTransactionError("P2.4 mapping writes require an existing managed transaction.")
    return value


def _tuple(row: object, names: tuple[str, ...]) -> tuple[object, ...]:
    return tuple(row[name] for name in names)  # type: ignore[index]


def _dependency(connection: ManagedConnection, *, plan: str, record_id: str):
    row = connection.execute(
        """SELECT id,migration_run_id,resolved_owner_user_id,item_id,revision_id,
                  edge_id,finding_id,json_extract(details_json,'$.plan_sha256') AS plan,
                  json_extract(details_json,'$.target_space_id') AS space,
                  json_extract(details_json,'$.inventory_record_id') AS record_id,
                  json_extract(details_json,'$.import_mode') AS import_mode,
                  json_extract(details_json,'$.inventory_manifest_sha256') AS manifest,
                  json_extract(details_json,'$.root_binding_sha256') AS root_binding,
                  details_json
           FROM legacy_migration_map
           WHERE json_extract(details_json,'$.inventory_record_id')=?
             AND json_extract(details_json,'$.plan_sha256')=?""",
        (record_id, plan),
    ).fetchall()
    if len(row) != 1:
        raise LegacyP24MappingIntegrityError("A reviewed endpoint mapping is missing or ambiguous.")
    return row[0]


class LegacyP24MappingRepository:
    __slots__ = ()

    @staticmethod
    def _prepare_details(value: LegacyP24GraphDetails) -> tuple[LegacyP24GraphDetails, str]:
        if not isinstance(value, LegacyP24GraphDetails):
            raise TypeError("LegacyP24GraphDetails is required.")
        plan = _sha(value.plan_sha256, "plan_sha256")
        _sha(value.inventory_manifest_sha256, "inventory_manifest_sha256")
        _sha(value.root_binding_sha256, "root_binding_sha256")
        _record_id(value.inventory_record_id)
        _sha(value.inventory_record_sha256, "inventory_record_sha256")
        _record_id(value.parent_inventory_record_id, "parent_inventory_record_id")
        _sha(value.path_key_sha256, "path_key_sha256")
        _sha(value.source_record_sha256, "source_record_sha256")
        _sha(value.effect_evidence_sha256, "effect_evidence_sha256")
        _uuid(value.target_space_id, "target_space_id")
        ordinal = _ordinal(value.source_ordinal)
        if value.record_kind != "logical_record" or value.import_mode not in {"graph_node", "graph_edge", "finding"}:
            raise ValueError("P2.4 graph mappings require one supported logical record.")
        expected_category = {
            "graph_node": "knowledge.wiki_graph_nodes",
            "graph_edge": "knowledge.wiki_graph_edges",
            "finding": "knowledge.wiki_candidates",
        }[value.import_mode]
        if value.category != expected_category or value.result_disposition not in {"created", "existing_exact"}:
            raise ValueError("P2.4 graph mapping category/disposition is invalid.")
        endpoints = tuple(_record_id(item, "endpoint_inventory_record_id") for item in value.endpoint_inventory_record_ids)
        if len(endpoints) != (1 if value.import_mode == "graph_node" else 2) or len(set(endpoints)) != len(endpoints):
            raise ValueError("P2.4 endpoint bindings are invalid.")
        relationships = tuple(_relationship_id(item) for item in value.relationship_ids)
        if relationships != tuple(sorted(set(relationships))) or not relationships:
            raise ValueError("P2.4 relationship bindings are invalid.")
        del ordinal, plan
        return value, _canonical(value.as_dict(), "details")

    @staticmethod
    def _verify_effect(connection: ManagedConnection, target: LegacyP24GraphTarget) -> None:
        details = target.details
        dependencies = tuple(
            _dependency(connection, plan=details.plan_sha256, record_id=item)
            for item in details.endpoint_inventory_record_ids
        )
        owner = target.resolved_owner_user_id
        if any(row["resolved_owner_user_id"] != owner or row["space"] != details.target_space_id for row in dependencies):
            raise LegacyP24MappingIntegrityError("P2.4 endpoint ownership/space differs.")
        if details.import_mode == "graph_node":
            endpoint = dependencies[0]
            if target.item_id != endpoint["item_id"] or target.revision_id != endpoint["revision_id"] or target.edge_id is not None or target.finding_id is not None:
                raise LegacyP24MappingIntegrityError("The graph-node target is not its exact imported source item.")
            row = connection.execute(
                """SELECT item.space_id,item.lifecycle_status,revision.item_id
                   FROM knowledge_item AS item
                   JOIN knowledge_revision AS revision ON revision.id=?
                   WHERE item.id=?""",
                (target.revision_id, target.item_id),
            ).fetchone()
            if row is None or tuple(row) != (
                details.target_space_id,
                "active",
                target.item_id,
            ):
                raise LegacyP24MappingIntegrityError(
                    "The graph-node source item/revision is invalid."
                )
            return
        source, destination = dependencies
        if source["item_id"] is None or destination["item_id"] is None or source["revision_id"] is None or destination["revision_id"] is None:
            raise LegacyP24MappingIntegrityError("P2.4 endpoint canonical items are incomplete.")
        if details.import_mode == "graph_edge":
            if target.edge_id is None or any(value is not None for value in (target.item_id, target.revision_id, target.finding_id)):
                raise LegacyP24MappingIntegrityError("The graph-edge target shape is invalid.")
            row = connection.execute(
                """SELECT space_id,source_item_id,target_item_id,
                          source_evidence_revision_id,target_evidence_revision_id,
                          edge_status,origin,evidence_json
                   FROM knowledge_edge WHERE id=?""",
                (target.edge_id,),
            ).fetchone()
            expected = (
                details.target_space_id,
                source["item_id"],
                destination["item_id"],
                source["revision_id"],
                destination["revision_id"],
                "active",
                "manual",
                target.effect_evidence_json,
            )
            if row is None or tuple(row) != expected:
                raise LegacyP24MappingIntegrityError("The graph-edge canonical effect is invalid.")
            return
        if target.finding_id is None or any(value is not None for value in (target.item_id, target.revision_id, target.edge_id)):
            raise LegacyP24MappingIntegrityError("The finding target shape is invalid.")
        row = connection.execute(
            """SELECT space_id,source_item_id,source_revision_id,target_item_id,
                      target_revision_id,status,decision_mode,created_actor_kind,
                      evidence_json
               FROM curation_finding WHERE id=?""",
            (target.finding_id,),
        ).fetchone()
        expected = (
            details.target_space_id,
            source["item_id"],
            source["revision_id"],
            destination["item_id"],
            destination["revision_id"],
            "pending",
            "human",
            "system",
            target.effect_evidence_json,
        )
        if row is None or tuple(row) != expected:
            raise LegacyP24MappingIntegrityError("The candidate finding canonical effect is invalid.")

    def put_graph_imported(
        self,
        connection: ManagedConnection,
        *,
        mapping_id: str,
        migration_run_id: str,
        legacy_kind: str,
        normalized_path: str,
        source_sha256: str,
        target: LegacyP24GraphTarget,
        now_ms: int,
    ) -> tuple[LegacyP24GraphRecord, bool]:
        connection = _connection(connection)
        if not isinstance(target, LegacyP24GraphTarget):
            raise TypeError("LegacyP24GraphTarget is required.")
        details, details_json = self._prepare_details(target.details)
        effect_evidence_json = _canonical(
            target.effect_evidence_json, "effect_evidence_json"
        )
        if (
            hashlib.sha256(effect_evidence_json.encode("utf-8")).hexdigest()
            != details.effect_evidence_sha256
        ):
            raise ValueError(
                "P2.4 effect evidence does not match its reviewed digest."
            )
        expected_id = migration_mapping_id_for_record(details.plan_sha256, details.inventory_record_id)
        expected_run = migration_run_id_for_plan(details.plan_sha256)
        if _uuid(mapping_id, "mapping_id") != expected_id or _uuid(migration_run_id, "migration_run_id") != expected_run:
            raise ValueError("P2.4 mapping/run IDs do not match the reviewed plan.")
        owner = _user(target.resolved_owner_user_id)
        source_sha = _sha(source_sha256, "source_sha256")
        if legacy_kind != details.category or source_sha != details.source_record_sha256:
            raise ValueError("P2.4 source key does not match its details.")
        if not isinstance(target.source_path_key, str) or hashlib.sha256(target.source_path_key.encode("utf-8")).hexdigest() != details.path_key_sha256:
            raise ValueError("P2.4 source path key does not match its reviewed hash.")
        expected_locator = migration_source_locator(root_id=details.root_id, path_key=target.source_path_key, record_kind="logical_record", source_ordinal=details.source_ordinal)
        if normalized_path != expected_locator:
            raise ValueError("P2.4 normalized_path is not its stable source locator.")
        self._verify_effect(connection, target)
        item_id = _uuid(target.item_id, "item_id") if target.item_id is not None else None
        revision_id = _uuid(target.revision_id, "revision_id") if target.revision_id is not None else None
        edge_id = _uuid(target.edge_id, "edge_id") if target.edge_id is not None else None
        finding_id = _uuid(target.finding_id, "finding_id") if target.finding_id is not None else None
        now = _now(now_ms)
        expected = (expected_id, expected_run, legacy_kind, normalized_path, source_sha, owner, None, item_id, revision_id, edge_id, "imported", details_json, now, now, finding_id, None)
        existing = connection.execute(f"SELECT {','.join(_RECORD_COLUMNS)} FROM legacy_migration_map WHERE id=?", (expected_id,)).fetchone()
        closed_parent = connection.execute(
            """SELECT id FROM legacy_migration_map
               WHERE json_extract(details_json,'$.schema_version')=3
                 AND json_extract(details_json,'$.plan_sha256')=?
                 AND json_extract(details_json,'$.inventory_record_id')=?""",
            (details.plan_sha256, details.parent_inventory_record_id),
        ).fetchone()
        if existing is None and closed_parent is not None:
            raise LegacyP24MappingConflict(
                "The P2.4 source container is already dependency-closed."
            )
        by_locator = connection.execute("SELECT id FROM legacy_migration_map WHERE normalized_path=?", (normalized_path,)).fetchone()
        if existing is not None:
            if _tuple(existing, _RECORD_COLUMNS) != expected or by_locator is None or by_locator["id"] != expected_id:
                raise LegacyP24MappingConflict("The P2.4 record mapping is already bound incompatibly.")
            return LegacyP24GraphRecord(expected_id, expected_run, legacy_kind, normalized_path, source_sha, target, details_json, now), False
        if by_locator is not None:
            raise LegacyP24MappingConflict("The stable P2.4 source locator is already occupied.")
        try:
            connection.execute(
                """INSERT INTO legacy_migration_map(
                     id,migration_run_id,legacy_kind,normalized_path,source_sha256,
                     resolved_owner_user_id,artifact_id,item_id,revision_id,edge_id,
                     status,details_json,created_at,updated_at,finding_id,audit_event_id
                   ) VALUES (?,?,?,?,?,?,NULL,?,?,?,'imported',?,?,?,?,NULL)""",
                (
                    expected_id,
                    expected_run,
                    legacy_kind,
                    normalized_path,
                    source_sha,
                    owner,
                    item_id,
                    revision_id,
                    edge_id,
                    details_json,
                    now,
                    now,
                    finding_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyP24MappingConflict("The P2.4 record mapping violates durable constraints.") from exc
        return LegacyP24GraphRecord(expected_id, expected_run, legacy_kind, normalized_path, source_sha, target, details_json, now), True

    def put_container_imported(
        self,
        connection: ManagedConnection,
        *,
        target: LegacyP24ContainerTarget,
        now_ms: int,
    ) -> tuple[LegacyP24ContainerRecord, bool]:
        connection = _connection(connection)
        if not isinstance(target, LegacyP24ContainerTarget):
            raise TypeError("LegacyP24ContainerTarget is required.")
        details = target.details
        if not isinstance(details, LegacyP24ContainerDetails):
            raise TypeError("LegacyP24ContainerDetails is required.")
        plan = _sha(details.plan_sha256, "plan_sha256")
        _sha(details.inventory_manifest_sha256, "inventory_manifest_sha256")
        _sha(details.root_binding_sha256, "root_binding_sha256")
        record_id = _record_id(details.inventory_record_id)
        _sha(details.inventory_record_sha256, "inventory_record_sha256")
        source_sha = _sha(details.source_record_sha256, "source_record_sha256")
        _sha(details.path_key_sha256, "path_key_sha256")
        _sha(details.imported_child_bindings_sha256, "imported_child_bindings_sha256")
        _sha(
            details.imported_relationship_bindings_sha256,
            "imported_relationship_bindings_sha256",
        )
        if details.category not in {
            "knowledge.wiki_graph_edges",
            "knowledge.wiki_candidates",
            "knowledge.legacy_edges",
        }:
            raise ValueError("P2.4 source-container category is invalid.")
        owner = _user(target.resolved_owner_user_id)
        space = _uuid(details.target_space_id, "target_space_id")
        child_ids = tuple(
            _record_id(value, "planned_import_child_record_id")
            for value in details.planned_import_child_record_ids
        )
        relationship_ids = tuple(
            _relationship_id(value)
            for value in details.planned_import_relationship_ids
        )
        if child_ids != tuple(sorted(set(child_ids))) or relationship_ids != tuple(
            sorted(set(relationship_ids))
        ):
            raise ValueError("P2.4 source-container dependencies are not canonical.")
        if (
            not isinstance(target.source_path_key, str)
            or hashlib.sha256(target.source_path_key.encode("utf-8")).hexdigest()
            != details.path_key_sha256
        ):
            raise ValueError("P2.4 source-container path binding is invalid.")
        locator = migration_source_locator(
            root_id=details.root_id,
            path_key=target.source_path_key,
            record_kind="physical_file",
            source_ordinal=0,
        )
        mapping_id = migration_mapping_id_for_record(plan, record_id)
        run_id = migration_run_id_for_plan(plan)
        child_rows = connection.execute(
            """SELECT id,json_extract(details_json,'$.inventory_record_id') AS record_id,
                      source_sha256,resolved_owner_user_id,
                      json_extract(details_json,'$.target_space_id') AS space_id
               FROM legacy_migration_map
               WHERE json_extract(details_json,'$.schema_version')=2
                 AND json_extract(details_json,'$.plan_sha256')=?
                 AND json_extract(details_json,'$.parent_inventory_record_id')=?
               ORDER BY record_id""",
            (plan, record_id),
        ).fetchall()
        observed_child_ids = tuple(str(row["record_id"]) for row in child_rows)
        child_bindings = [
            [str(row["id"]), str(row["record_id"]), str(row["source_sha256"])]
            for row in child_rows
        ]
        child_digest = hashlib.sha256(
            json.dumps(
                child_bindings,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if (
            observed_child_ids != child_ids
            or child_digest != details.imported_child_bindings_sha256
            or any(
                row["resolved_owner_user_id"] != owner or row["space_id"] != space
                for row in child_rows
            )
        ):
            raise LegacyP24MappingIntegrityError(
                "P2.4 source-container child closure is invalid."
            )
        relationship_rows = connection.execute(
            """SELECT rel.id,rel.relationship_id,rel.relationship_sha256
               FROM legacy_migration_p24_relationship_map AS rel
               WHERE rel.migration_run_id=?
                 AND (rel.source_mapping_id IN (
                        SELECT value FROM json_each(?)
                      ) OR rel.from_mapping_id IN (
                        SELECT value FROM json_each(?)
                      ))
               ORDER BY rel.relationship_id""",
            (
                run_id,
                json.dumps([str(row["id"]) for row in child_rows]),
                json.dumps([str(row["id"]) for row in child_rows]),
            ),
        ).fetchall()
        observed_relationship_ids = tuple(
            str(row["relationship_id"]) for row in relationship_rows
        )
        relationship_bindings = [
            [
                str(row["id"]),
                str(row["relationship_id"]),
                str(row["relationship_sha256"]),
            ]
            for row in relationship_rows
        ]
        relationship_digest = hashlib.sha256(
            json.dumps(
                relationship_bindings,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if (
            observed_relationship_ids != relationship_ids
            or relationship_digest
            != details.imported_relationship_bindings_sha256
        ):
            raise LegacyP24MappingIntegrityError(
                "P2.4 source-container relationship closure is invalid."
            )
        personal = connection.execute(
            """SELECT 1 FROM knowledge_space
               WHERE id=? AND space_kind='personal' AND owner_user_id=?""",
            (space, owner),
        ).fetchone()
        if personal is None:
            raise LegacyP24MappingIntegrityError(
                "P2.4 source-container personal space is invalid."
            )
        details_json = _canonical(details.as_dict(), "container_details")
        now = _now(now_ms)
        expected = (
            mapping_id,
            run_id,
            details.category,
            locator,
            source_sha,
            owner,
            None,
            None,
            None,
            None,
            "imported",
            details_json,
            now,
            now,
            None,
            None,
        )
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
                raise LegacyP24MappingConflict(
                    "The P2.4 source-container checkpoint differs."
                )
            return LegacyP24ContainerRecord(
                mapping_id, run_id, target, details_json, now
            ), False
        if occupied is not None:
            raise LegacyP24MappingConflict(
                "The P2.4 source-container locator is already occupied."
            )
        try:
            connection.execute(
                """INSERT INTO legacy_migration_map(
                     id,migration_run_id,legacy_kind,normalized_path,source_sha256,
                     resolved_owner_user_id,artifact_id,item_id,revision_id,edge_id,
                     status,details_json,created_at,updated_at,finding_id,audit_event_id
                   ) VALUES (?,?,?,?,?,?,NULL,NULL,NULL,NULL,'imported',?,?,?,NULL,NULL)""",
                (
                    mapping_id,
                    run_id,
                    details.category,
                    locator,
                    source_sha,
                    owner,
                    details_json,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyP24MappingConflict(
                "The P2.4 source-container checkpoint violates durable constraints."
            ) from exc
        return LegacyP24ContainerRecord(
            mapping_id, run_id, target, details_json, now
        ), True

    @staticmethod
    def _prepare_history_details(
        value: LegacyP24HistoryDetails,
    ) -> tuple[LegacyP24HistoryDetails, str]:
        if not isinstance(value, LegacyP24HistoryDetails):
            raise TypeError("LegacyP24HistoryDetails is required.")
        _sha(value.plan_sha256, "plan_sha256")
        _sha(value.inventory_manifest_sha256, "inventory_manifest_sha256")
        _sha(value.root_binding_sha256, "root_binding_sha256")
        _record_id(value.inventory_record_id)
        _sha(value.inventory_record_sha256, "inventory_record_sha256")
        _sha(value.path_key_sha256, "path_key_sha256")
        _sha(value.source_record_sha256, "source_record_sha256")
        _sha(value.history_evidence_sha256, "history_evidence_sha256")
        _sha(value.imported_child_bindings_sha256, "imported_child_bindings_sha256")
        if value.category not in {
            "identity.auth_audit",
            "knowledge.wiki_log",
            "knowledge.tombstones",
        }:
            raise ValueError("P2.4 history category is invalid.")
        if value.record_kind not in {"physical_file", "logical_record"}:
            raise ValueError("P2.4 history record_kind is invalid.")
        if value.record_kind == "physical_file":
            if value.source_ordinal != 0 or value.parent_inventory_record_id is not None:
                raise ValueError("Physical history source identity is invalid.")
        else:
            _ordinal(value.source_ordinal)
            if value.parent_inventory_record_id is None:
                raise ValueError("Logical history requires one physical parent.")
            _record_id(
                value.parent_inventory_record_id,
                "parent_inventory_record_id",
            )
        if value.category == "identity.auth_audit":
            expected_mode = (
                "source_container"
                if value.record_kind == "physical_file"
                else "legacy_history"
            )
        else:
            expected_mode = "legacy_history"
        if value.import_mode != expected_mode:
            raise ValueError("P2.4 history import_mode is invalid.")
        expected_disposition = {
            ("physical_file", "identity.auth_audit"): "dependency_closed",
            ("physical_file", "knowledge.wiki_log"): "artifact_retained",
            ("physical_file", "knowledge.tombstones"): "artifact_retained",
            ("logical_record", "identity.auth_audit"): "historical_event",
            ("logical_record", "knowledge.tombstones"): "historical_event",
        }.get((value.record_kind, value.category))
        if value.result_disposition != expected_disposition:
            raise ValueError("P2.4 history disposition is invalid.")
        if value.category == "identity.auth_audit":
            if value.target_space_id is not None:
                raise ValueError("Global legacy audit history cannot target a space.")
        else:
            _uuid(value.target_space_id, "target_space_id")
        child_ids = tuple(
            _record_id(item, "planned_import_child_record_id")
            for item in value.planned_import_child_record_ids
        )
        if child_ids != tuple(sorted(set(child_ids))):
            raise ValueError("P2.4 history child identities are not canonical.")
        if value.record_kind == "logical_record" and child_ids:
            raise ValueError("Logical history cannot own child records.")
        if (
            value.record_kind == "logical_record"
            and value.imported_child_bindings_sha256
            != _child_bindings_sha256([])
        ):
            raise ValueError("Logical history must bind the empty child set.")
        return value, _canonical(value.as_dict(), "history_details")

    @staticmethod
    def _prepare_history_evidence(
        details: LegacyP24HistoryDetails, value: str
    ) -> str:
        encoded = _canonical(value, "history_evidence_json")
        parsed = json.loads(encoded)
        if details.result_disposition == "artifact_retained":
            required = {
                "authority",
                "category",
                "inventory_record_id",
                "media_type",
                "raw_sha256",
                "schema_version",
                "size_bytes",
            }
            valid = (
                set(parsed) == required
                and parsed.get("authority") == "historical_only"
                and parsed.get("category") == details.category
                and parsed.get("inventory_record_id") == details.inventory_record_id
                and parsed.get("media_type") in _HISTORY_MEDIA_TYPES
                and parsed.get("raw_sha256") == details.source_record_sha256
                and parsed.get("schema_version") == 1
                and type(parsed.get("size_bytes")) is int
                and 0 <= int(parsed["size_bytes"]) <= _SQLITE_INTEGER_MAX
            )
        elif details.result_disposition == "historical_event":
            required = {
                "authority",
                "category",
                "inventory_record_id",
                "kind",
                "parent_inventory_record_id",
                "payload_sha256",
                "schema_version",
                "source_ordinal",
                "status",
            }
            valid = (
                set(parsed) == required
                and parsed.get("authority") == "historical_only"
                and parsed.get("category") == details.category
                and parsed.get("inventory_record_id") == details.inventory_record_id
                and parsed.get("parent_inventory_record_id")
                == details.parent_inventory_record_id
                and parsed.get("source_ordinal") == details.source_ordinal
                and parsed.get("schema_version") == 1
                and parsed.get("kind") in _HISTORY_ENUM_VALUES
                and parsed.get("status") in _HISTORY_ENUM_VALUES
                and isinstance(parsed.get("payload_sha256"), str)
                and _SHA_RE.fullmatch(str(parsed.get("payload_sha256"))) is not None
            )
        else:
            required = {
                "authority",
                "category",
                "inventory_record_id",
                "planned_import_child_count",
                "raw_sha256",
                "schema_version",
            }
            valid = (
                set(parsed) == required
                and parsed.get("authority") == "historical_only"
                and parsed.get("category") == details.category
                and parsed.get("inventory_record_id") == details.inventory_record_id
                and parsed.get("planned_import_child_count")
                == len(details.planned_import_child_record_ids)
                and parsed.get("raw_sha256") == details.source_record_sha256
                and parsed.get("schema_version") == 1
            )
        if not valid:
            raise ValueError("P2.4 history evidence shape is invalid.")
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != details.history_evidence_sha256:
            raise ValueError("P2.4 history evidence digest differs.")
        return encoded

    @staticmethod
    def _verify_history_effect(
        connection: ManagedConnection,
        target: LegacyP24HistoryTarget,
        *,
        locator: str,
        evidence_json: str,
    ) -> None:
        details = target.details
        owner = _optional_user(target.resolved_owner_user_id)
        artifact_id = _optional_uuid(target.artifact_id, "artifact_id")
        audit_event_id = (
            None if target.audit_event_id is None else _audit_id(target.audit_event_id)
        )
        audit_event_uuid = _optional_uuid(
            target.audit_event_uuid, "audit_event_uuid"
        )
        if details.result_disposition == "artifact_retained":
            if owner is None or artifact_id is None or audit_event_id is not None or audit_event_uuid is not None:
                raise LegacyP24MappingIntegrityError(
                    "Historical artifacts require exactly one personal artifact target."
                )
            personal = connection.execute(
                """SELECT 1 FROM knowledge_space
                   WHERE id=? AND space_kind='personal' AND owner_user_id=?""",
                (details.target_space_id, owner),
            ).fetchone()
            row = connection.execute(
                """SELECT artifact.publisher_user_id,artifact.origin_type,
                          artifact.origin_key,artifact.origin_revision_key,
                          artifact.raw_sha256,artifact.raw_size_bytes,
                          artifact.media_type,manifest.content_sha256,
                          manifest.size_bytes,manifest.role,manifest.ordinal,
                          manifest.media_type,body.content_sha256
                   FROM source_artifact AS artifact
                   JOIN artifact_blob AS manifest
                     ON manifest.artifact_id=artifact.id AND manifest.role='raw'
                   JOIN artifact_blob_body AS body
                     ON body.artifact_blob_id=manifest.id
                   WHERE artifact.id=?""",
                (artifact_id,),
            ).fetchall()
            evidence = json.loads(evidence_json)
            expected = (
                owner,
                "legacy",
                locator,
                details.inventory_record_id,
                details.source_record_sha256,
                evidence["size_bytes"],
                evidence["media_type"],
                details.source_record_sha256,
                evidence["size_bytes"],
                "raw",
                0,
                evidence["media_type"],
                details.source_record_sha256,
            )
            if personal is None or len(row) != 1 or tuple(row[0]) != expected:
                raise LegacyP24MappingIntegrityError(
                    "The labelled historical artifact is not exact."
                )
            return
        if artifact_id is not None or audit_event_id is None or audit_event_uuid is None:
            raise LegacyP24MappingIntegrityError(
                "Historical events require exactly one canonical audit target."
            )
        if details.category == "identity.auth_audit":
            if owner is not None or details.target_space_id is not None:
                raise LegacyP24MappingIntegrityError(
                    "Global legacy audit history cannot acquire a personal owner."
                )
        else:
            if owner is None:
                raise LegacyP24MappingIntegrityError(
                    "Personal tombstone history requires one owner."
                )
            personal = connection.execute(
                """SELECT 1 FROM knowledge_space
                   WHERE id=? AND space_kind='personal' AND owner_user_id=?""",
                (details.target_space_id, owner),
            ).fetchone()
            if personal is None:
                raise LegacyP24MappingIntegrityError(
                    "Personal tombstone history targets the wrong space."
                )
        row = connection.execute(
            """SELECT event_uuid,action,outcome,actor_kind,actor_user_id,
                      space_id,details_json,schema_version
               FROM audit_event WHERE id=?""",
            (audit_event_id,),
        ).fetchone()
        expected = (
            audit_event_uuid,
            f"legacy_history.{details.category}",
            "historical",
            "system",
            None,
            details.target_space_id,
            evidence_json,
            1,
        )
        if row is None or tuple(row) != expected:
            raise LegacyP24MappingIntegrityError(
                "The canonical legacy-history audit event is not exact."
            )

    def put_history_imported(
        self,
        connection: ManagedConnection,
        *,
        target: LegacyP24HistoryTarget,
        now_ms: int,
    ) -> tuple[LegacyP24HistoryRecord, bool]:
        connection = _connection(connection)
        if not isinstance(target, LegacyP24HistoryTarget):
            raise TypeError("LegacyP24HistoryTarget is required.")
        details, details_json = self._prepare_history_details(target.details)
        evidence_json = self._prepare_history_evidence(
            details, target.history_evidence_json
        )
        owner = _optional_user(target.resolved_owner_user_id)
        artifact_id = _optional_uuid(target.artifact_id, "artifact_id")
        audit_event_id = (
            None if target.audit_event_id is None else _audit_id(target.audit_event_id)
        )
        path_key = target.source_path_key
        if (
            not isinstance(path_key, str)
            or hashlib.sha256(path_key.encode("utf-8")).hexdigest()
            != details.path_key_sha256
        ):
            raise ValueError("P2.4 history path binding is invalid.")
        locator = migration_source_locator(
            root_id=details.root_id,
            path_key=path_key,
            record_kind=details.record_kind,  # type: ignore[arg-type]
            source_ordinal=details.source_ordinal,
        )
        mapping_id = migration_mapping_id_for_record(
            details.plan_sha256, details.inventory_record_id
        )
        run_id = migration_run_id_for_plan(details.plan_sha256)
        if details.record_kind == "physical_file":
            child_rows = connection.execute(
                """SELECT id,json_extract(details_json,'$.inventory_record_id') AS record_id,
                          source_sha256
                   FROM legacy_migration_map
                   WHERE json_extract(details_json,'$.schema_version')=4
                     AND json_extract(details_json,'$.plan_sha256')=?
                     AND json_extract(details_json,'$.parent_inventory_record_id')=?
                   ORDER BY record_id""",
                (details.plan_sha256, details.inventory_record_id),
            ).fetchall()
            observed = tuple(str(row["record_id"]) for row in child_rows)
            bindings = [
                (str(row["id"]), str(row["record_id"]), str(row["source_sha256"]))
                for row in child_rows
            ]
            if (
                observed != details.planned_import_child_record_ids
                or _child_bindings_sha256(bindings)
                != details.imported_child_bindings_sha256
            ):
                raise LegacyP24MappingIntegrityError(
                    "P2.4 history parent child closure is invalid."
                )
        else:
            closed_parent = connection.execute(
                """SELECT id FROM legacy_migration_map
                   WHERE json_extract(details_json,'$.schema_version')=4
                     AND json_extract(details_json,'$.plan_sha256')=?
                     AND json_extract(details_json,'$.inventory_record_id')=?""",
                (details.plan_sha256, details.parent_inventory_record_id),
            ).fetchone()
            if closed_parent is not None:
                existing = connection.execute(
                    "SELECT id FROM legacy_migration_map WHERE id=?",
                    (mapping_id,),
                ).fetchone()
                if existing is None:
                    raise LegacyP24MappingConflict(
                        "The P2.4 history parent is already dependency-closed."
                    )
        self._verify_history_effect(
            connection, target, locator=locator, evidence_json=evidence_json
        )
        now = _now(now_ms)
        expected = (
            mapping_id,
            run_id,
            details.category,
            locator,
            details.source_record_sha256,
            owner,
            artifact_id,
            None,
            None,
            None,
            "imported",
            details_json,
            now,
            now,
            None,
            audit_event_id,
        )
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
                raise LegacyP24MappingConflict(
                    "The P2.4 history checkpoint differs."
                )
            return LegacyP24HistoryRecord(
                mapping_id, run_id, target, details_json, now
            ), False
        if occupied is not None:
            raise LegacyP24MappingConflict(
                "The stable P2.4 history locator is already occupied."
            )
        try:
            connection.execute(
                """INSERT INTO legacy_migration_map(
                     id,migration_run_id,legacy_kind,normalized_path,source_sha256,
                     resolved_owner_user_id,artifact_id,item_id,revision_id,edge_id,
                     status,details_json,created_at,updated_at,finding_id,audit_event_id
                   ) VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,'imported',?,?,?,NULL,?)""",
                (
                    mapping_id,
                    run_id,
                    details.category,
                    locator,
                    details.source_record_sha256,
                    owner,
                    artifact_id,
                    details_json,
                    now,
                    now,
                    audit_event_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyP24MappingConflict(
                "The P2.4 history checkpoint violates durable constraints."
            ) from exc
        return LegacyP24HistoryRecord(
            mapping_id, run_id, target, details_json, now
        ), True

    def get_history_by_record(
        self,
        connection: ManagedConnection,
        *,
        plan_sha256: str,
        inventory_record_id: str,
    ):
        plan = _sha(plan_sha256, "plan_sha256")
        record = _record_id(inventory_record_id)
        rows = connection.execute(
            f"""SELECT {','.join(_RECORD_COLUMNS)} FROM legacy_migration_map
                WHERE json_extract(details_json,'$.schema_version')=4
                  AND json_extract(details_json,'$.plan_sha256')=?
                  AND json_extract(details_json,'$.inventory_record_id')=?""",
            (plan, record),
        ).fetchall()
        if len(rows) > 1:
            raise LegacyP24MappingIntegrityError(
                "P2.4 history record identity is duplicated."
            )
        return rows[0] if rows else None

    def get_graph_by_record(self, connection: ManagedConnection, *, plan_sha256: str, inventory_record_id: str):
        plan = _sha(plan_sha256, "plan_sha256")
        record = _record_id(inventory_record_id)
        rows = connection.execute(
            f"""SELECT {','.join(_RECORD_COLUMNS)} FROM legacy_migration_map
                WHERE json_extract(details_json,'$.schema_version')=2
                  AND json_extract(details_json,'$.plan_sha256')=?
                  AND json_extract(details_json,'$.inventory_record_id')=?""",
            (plan, record),
        ).fetchall()
        if len(rows) > 1:
            raise LegacyP24MappingIntegrityError("P2.4 record identity is duplicated.")
        return rows[0] if rows else None


class LegacyP24RelationshipRepository:
    __slots__ = ()

    @staticmethod
    def _prepare_details(
        value: LegacyP24RelationshipDetails,
    ) -> tuple[LegacyP24RelationshipDetails, str]:
        if not isinstance(value, LegacyP24RelationshipDetails):
            raise TypeError("LegacyP24RelationshipDetails is required.")
        _sha(value.plan_sha256, "plan_sha256")
        _relationship_id(value.relationship_id)
        _sha(value.inventory_manifest_sha256, "inventory_manifest_sha256")
        _sha(value.root_binding_sha256, "root_binding_sha256")
        _sha(
            value.inventory_relationship_sha256,
            "inventory_relationship_sha256",
        )
        if value.source_inventory_record_id is not None:
            _record_id(
                value.source_inventory_record_id,
                "source_inventory_record_id",
            )
        if value.from_inventory_record_id is not None:
            _record_id(value.from_inventory_record_id, "from_inventory_record_id")
        _record_id(value.to_inventory_record_id, "to_inventory_record_id")
        _uuid(value.effect_mapping_id, "effect_mapping_id")
        return value, _canonical(value.as_dict(), "relationship_details")

    @staticmethod
    def _verify(connection: ManagedConnection, target: LegacyP24RelationshipTarget) -> None:
        details = target.details
        plan = details.plan_sha256
        to_row = _dependency(
            connection, plan=plan, record_id=details.to_inventory_record_id
        )
        from_row = (
            _dependency(
                connection,
                plan=plan,
                record_id=details.from_inventory_record_id,
            )
            if details.from_inventory_record_id
            else None
        )
        source_row = (
            _dependency(
                connection,
                plan=plan,
                record_id=details.source_inventory_record_id,
            )
            if details.source_inventory_record_id
            else None
        )
        for expected_id, row in ((target.to_mapping_id, to_row), (target.from_mapping_id, from_row), (target.source_mapping_id, source_row)):
            if expected_id is not None and (row is None or row["id"] != expected_id):
                raise LegacyP24MappingIntegrityError("P2.4 relationship endpoint mapping differs.")
        rows = [row for row in (to_row, from_row, source_row) if row is not None]
        if any(row["resolved_owner_user_id"] != target.resolved_owner_user_id or row["space"] != target.target_space_id for row in rows):
            raise LegacyP24MappingIntegrityError("P2.4 relationship crosses owner/space.")
        if any(
            row["manifest"] != details.inventory_manifest_sha256
            or row["root_binding"] != details.root_binding_sha256
            for row in rows
        ):
            raise LegacyP24MappingIntegrityError(
                "P2.4 relationship endpoint source bindings differ."
            )
        kind = target.relationship_type
        if kind == "graph_node_source":
            effect = json.loads(str(from_row["details_json"])) if from_row is not None else {}
            if (
                target.effect_kind != "dependency"
                or from_row is None
                or from_row["import_mode"] != "graph_node"
                or source_row is not None
                or details.effect_mapping_id != from_row["id"]
                or effect.get("endpoint_inventory_record_ids")
                != [details.to_inventory_record_id]
                or details.relationship_id not in effect.get("relationship_ids", [])
            ):
                raise LegacyP24MappingIntegrityError("Graph-node dependency shape is invalid.")
        elif kind in {"candidate_source", "candidate_target"}:
            effect = json.loads(str(from_row["details_json"])) if from_row is not None else {}
            endpoint_index = 0 if kind == "candidate_source" else 1
            endpoints = effect.get("endpoint_inventory_record_ids")
            if (
                target.effect_kind != "finding"
                or from_row is None
                or from_row["import_mode"] != "finding"
                or target.finding_id != from_row["finding_id"]
                or source_row is not None
                or details.effect_mapping_id != from_row["id"]
                or not isinstance(endpoints, list)
                or len(endpoints) != 2
                or endpoints[endpoint_index] != details.to_inventory_record_id
                or details.relationship_id not in effect.get("relationship_ids", [])
            ):
                raise LegacyP24MappingIntegrityError("Candidate dependency shape is invalid.")
        elif kind == "exact_content_duplicate":
            if (
                target.effect_kind != "provenance"
                or source_row is not None
                or from_row is None
                or from_row["item_id"] is None
                or from_row["item_id"] != to_row["item_id"]
                or target.item_id != from_row["item_id"]
                or target.edge_id is not None
                or target.finding_id is not None
                or details.effect_mapping_id != from_row["id"]
            ):
                raise LegacyP24MappingIntegrityError(
                    "Exact-content provenance shape is invalid."
                )
        else:
            effect = json.loads(str(source_row["details_json"])) if source_row is not None else {}
            if (
                target.effect_kind != "edge"
                or source_row is None
                or source_row["import_mode"] != "graph_edge"
                or target.edge_id != source_row["edge_id"]
                or from_row is None
                or details.effect_mapping_id != source_row["id"]
                or effect.get("endpoint_inventory_record_ids")
                != [
                    details.from_inventory_record_id,
                    details.to_inventory_record_id,
                ]
                or details.relationship_id not in effect.get("relationship_ids", [])
            ):
                raise LegacyP24MappingIntegrityError("Graph-edge relationship shape is invalid.")

    def put_imported(self, connection: ManagedConnection, target: LegacyP24RelationshipTarget, *, now_ms: int) -> tuple[LegacyP24RelationshipRecord, bool]:
        connection = _connection(connection)
        if not isinstance(target, LegacyP24RelationshipTarget):
            raise TypeError("LegacyP24RelationshipTarget is required.")
        prepared_details, details = self._prepare_details(target.details)
        plan = prepared_details.plan_sha256
        relationship = prepared_details.relationship_id
        relationship_sha = prepared_details.inventory_relationship_sha256
        if target.relationship_type not in {"candidate_source", "candidate_target", "graph_node_source", "contradicts", "extends", "related", "supports", "exact_content_duplicate"}:
            raise ValueError("P2.4 relationship type is invalid.")
        source_map = _uuid(target.source_mapping_id, "source_mapping_id") if target.source_mapping_id else None
        from_map = _uuid(target.from_mapping_id, "from_mapping_id") if target.from_mapping_id else None
        to_map = _uuid(target.to_mapping_id, "to_mapping_id")
        owner = _user(target.resolved_owner_user_id)
        space = _uuid(target.target_space_id, "target_space_id")
        edge = _uuid(target.edge_id, "edge_id") if target.edge_id else None
        finding = _uuid(target.finding_id, "finding_id") if target.finding_id else None
        item = _uuid(target.item_id, "item_id") if target.item_id else None
        mapping_id = p24_relationship_mapping_id(plan, relationship)
        run_id = migration_run_id_for_plan(plan)
        now = _now(now_ms)
        normalized_target = LegacyP24RelationshipTarget(
            target.relationship_type,
            source_map,
            from_map,
            to_map,
            owner,
            space,
            target.effect_kind,
            edge,
            finding,
            item,
            prepared_details,
        )
        self._verify(connection, normalized_target)
        expected = (mapping_id, run_id, relationship, relationship_sha, target.relationship_type, source_map, from_map, to_map, owner, space, target.effect_kind, edge, finding, item, "imported", details, now, now)
        existing = connection.execute(f"SELECT {','.join(_REL_COLUMNS)} FROM legacy_migration_p24_relationship_map WHERE id=?", (mapping_id,)).fetchone()
        identity = connection.execute("SELECT id FROM legacy_migration_p24_relationship_map WHERE relationship_id=?", (relationship,)).fetchone()
        if existing is not None:
            if _tuple(existing, _REL_COLUMNS) != expected or identity is None or identity["id"] != mapping_id:
                raise LegacyP24MappingConflict("The P2.4 relationship checkpoint differs.")
            return LegacyP24RelationshipRecord(mapping_id, normalized_target, run_id, now), False
        if identity is not None:
            raise LegacyP24MappingConflict("The reviewed P2.4 relationship is already checkpointed differently.")
        effect = connection.execute(
            """SELECT json_extract(details_json,'$.parent_inventory_record_id') AS parent_id
               FROM legacy_migration_map WHERE id=?""",
            (prepared_details.effect_mapping_id,),
        ).fetchone()
        parent_id = effect["parent_id"] if effect is not None else None
        if parent_id is not None:
            closed = connection.execute(
                """SELECT id FROM legacy_migration_map
                   WHERE json_extract(details_json,'$.schema_version')=3
                     AND json_extract(details_json,'$.plan_sha256')=?
                     AND json_extract(details_json,'$.inventory_record_id')=?""",
                (plan, parent_id),
            ).fetchone()
            if closed is not None:
                raise LegacyP24MappingConflict(
                    "The P2.4 source container is already dependency-closed."
                )
        try:
            connection.execute(
                """INSERT INTO legacy_migration_p24_relationship_map(
                     id,migration_run_id,relationship_id,relationship_sha256,
                     relationship_type,source_mapping_id,from_mapping_id,to_mapping_id,
                     resolved_owner_user_id,target_space_id,effect_kind,edge_id,
                     finding_id,item_id,status,details_json,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'imported',?,?,?)""",
                (
                    mapping_id,
                    run_id,
                    relationship,
                    relationship_sha,
                    target.relationship_type,
                    source_map,
                    from_map,
                    to_map,
                    owner,
                    space,
                    target.effect_kind,
                    edge,
                    finding,
                    item,
                    details,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyP24MappingConflict("The P2.4 relationship checkpoint violates durable constraints.") from exc
        return LegacyP24RelationshipRecord(mapping_id, normalized_target, run_id, now), True


__all__ = [
    "LegacyP24ContainerDetails", "LegacyP24ContainerRecord",
    "LegacyP24ContainerTarget",
    "LegacyP24GraphDetails", "LegacyP24GraphRecord", "LegacyP24GraphTarget",
    "LegacyP24HistoryDetails", "LegacyP24HistoryRecord",
    "LegacyP24HistoryTarget",
    "LegacyP24MappingConflict", "LegacyP24MappingIntegrityError",
    "LegacyP24MappingRepository", "LegacyP24RelationshipRecord",
    "LegacyP24RelationshipDetails", "LegacyP24RelationshipRepository",
    "LegacyP24RelationshipTarget",
    "p24_relationship_mapping_id",
]

"""Bounded reviewed P2.4 graph-node, edge, and candidate import service.

Only the sanitized logical evidence frozen in a reviewed P2.2 plan is used.
The service never reads legacy source files and never interprets a projection as
authority.  Every canonical effect, immutable record/relationship checkpoint,
audit event, and outbox event is committed in one service-owned transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Literal
from uuid import UUID

from ..db import ConnectionFactory, ManagedConnection, transaction
from ..errors import KnowledgeCoreError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.graph import (
    GraphRepository,
    LegacyCandidateFinding,
    LegacyGraphEdge,
    legacy_candidate_finding_id,
    legacy_graph_edge_id,
)
from ..repositories.migration import (
    migration_mapping_id_for_record,
    migration_run_id_for_plan,
    migration_source_locator,
)
from ..repositories.migration_p24 import (
    LegacyP24ContainerDetails,
    LegacyP24ContainerTarget,
    LegacyP24GraphDetails,
    LegacyP24GraphTarget,
    LegacyP24MappingRepository,
    LegacyP24RelationshipDetails,
    LegacyP24RelationshipRepository,
    LegacyP24RelationshipTarget,
    p24_relationship_mapping_id,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..services.spaces import space_id_for_owner
from .importer import _derived_uuid, _mapping, _reviewed_plan, _utc_ms


MAX_P24_EFFECTS_PER_TRANSACTION = 100

_REQUEST_NAMESPACE = UUID("0bbc60fb-d603-496b-9b63-dc6ee6921982")
_OPERATION_NAMESPACE = UUID("1b349d20-327a-45f2-a745-6069095714e3")
_AUDIT_NAMESPACE = UUID("3e218df7-e485-4d59-b10d-00235208a521")
_REL_AUDIT_NAMESPACE = UUID("86875d3c-12a6-4385-a1ab-51ece68e65ed")


class LegacyP24ImportError(KnowledgeCoreError):
    code = "LEGACY_P24_IMPORT_FAILED"


class LegacyP24PreparationError(LegacyP24ImportError):
    code = "LEGACY_P24_PLAN_INVALID"


class LegacyP24StateConflictError(LegacyP24ImportError):
    code = "LEGACY_P24_STATE_CONFLICT"


P24Mode = Literal[
    "graph_node", "graph_edge", "finding", "legacy_history", "source_container"
]


@dataclass(frozen=True, slots=True)
class _OwnerAssertion:
    user_id: str
    system_role: str
    account_status: str
    part_id: str | None
    target_space_id: str


@dataclass(frozen=True, slots=True)
class _PreparedRecord:
    plan_row: Mapping[str, object]
    source: Mapping[str, object]
    record_id: str
    mode: P24Mode
    owner_user_id: str
    target_space_id: str
    endpoint_record_ids: tuple[str, ...]
    relationship_ids: tuple[str, ...]
    effect_evidence_json: str
    source_record_sha256: str
    path_key: str


@dataclass(frozen=True, slots=True)
class _PreparedRelationship:
    plan_row: Mapping[str, object]
    source: Mapping[str, object]
    relationship_id: str
    relationship_type: str
    source_record_id: str | None
    from_record_id: str | None
    to_record_id: str
    mode: P24Mode


@dataclass(frozen=True, slots=True)
class _PreparedContainer:
    plan_row: Mapping[str, object]
    source: Mapping[str, object]
    record_id: str
    owner_user_id: str
    target_space_id: str
    path_key: str
    source_record_sha256: str
    child_record_ids: tuple[str, ...]
    relationship_ids: tuple[str, ...]
    child_bindings_sha256: str
    relationship_bindings_sha256: str


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
    owner_assertions: tuple[_OwnerAssertion, ...]
    records: tuple[_PreparedRecord, ...]
    relationships: tuple[_PreparedRelationship, ...]
    containers: tuple[_PreparedContainer, ...]


@dataclass(frozen=True, slots=True)
class LegacyP24RecordOutcome:
    record_id: str
    mapping_id: str
    import_mode: P24Mode
    effect_id: str
    mapping_inserted: bool


@dataclass(frozen=True, slots=True)
class LegacyP24RelationshipOutcome:
    relationship_id: str
    relationship_mapping_id: str
    mapping_inserted: bool


@dataclass(frozen=True, slots=True)
class LegacyP24ImportResult:
    plan_sha256: str
    migration_run_id: str
    records: tuple[LegacyP24RecordOutcome, ...]
    relationships: tuple[LegacyP24RelationshipOutcome, ...]

    @property
    def inserted_mapping_count(self) -> int:
        return sum(row.mapping_inserted for row in self.records) + sum(
            row.mapping_inserted for row in self.relationships
        )


def _canonical(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError):
        raise LegacyP24PreparationError(
            "P2.4 evidence cannot be represented canonically."
        ) from None
    if not isinstance(json.loads(encoded), dict):
        raise LegacyP24PreparationError("P2.4 evidence must be an object.")
    return encoded


def _record_id(value: object, field: str = "record_id") -> str:
    if (
        not isinstance(value, str)
        or len(value) != 28
        or not value.startswith("rec_")
        or any(character not in "0123456789abcdef" for character in value[4:])
    ):
        raise LegacyP24PreparationError(f"{field} is invalid.")
    return value


def _relationship_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 28
        or not value.startswith("rel_")
        or any(character not in "0123456789abcdef" for character in value[4:])
    ):
        raise LegacyP24PreparationError("relationship_id is invalid.")
    return value


def _source(row: Mapping[str, object]) -> Mapping[str, object]:
    value = row.get("source_record")
    if not isinstance(value, Mapping):
        raise LegacyP24PreparationError("P2.4 source record is invalid.")
    return value


def _prepare_record(row: Mapping[str, object]) -> _PreparedRecord:
    record_id = _record_id(row.get("record_id"))
    mode = row.get("import_mode")
    if (
        row.get("action") != "import"
        or row.get("execution_phase") != "P2.4"
        or mode not in {"graph_node", "graph_edge", "finding"}
    ):
        raise LegacyP24PreparationError(
            "A selected record is not a supported imported P2.4 graph effect."
        )
    source = _source(row)
    if source.get("record_kind") != "logical_record":
        raise LegacyP24PreparationError(
            "P2.4 graph effects require logical source records."
        )
    owner = row.get("resolved_owner_user_id")
    if not isinstance(owner, str) or not owner:
        raise LegacyP24PreparationError("P2.4 graph effects require one owner.")
    space = space_id_for_owner("personal", owner)
    relationships_value = row.get("relationship_ids")
    if not isinstance(relationships_value, list) or not relationships_value:
        raise LegacyP24PreparationError(
            "P2.4 graph effects require relationship evidence."
        )
    relationship_ids = tuple(
        _relationship_id(value) for value in relationships_value
    )
    if relationship_ids != tuple(sorted(set(relationship_ids))):
        raise LegacyP24PreparationError(
            "P2.4 relationship evidence is not canonical."
        )
    if mode == "graph_node":
        resolution = source.get("graph_node_resolution")
        if not isinstance(resolution, Mapping) or resolution.get("status") != "resolved":
            raise LegacyP24PreparationError("Graph-node resolution is invalid.")
        endpoints = (_record_id(resolution.get("source_record_id")),)
        evidence = {
            "graph_node_resolution": dict(resolution),
            "inventory_record_sha256": row.get("inventory_record_sha256"),
        }
    elif mode == "graph_edge":
        edge = source.get("graph_edge")
        resolution = source.get("graph_edge_resolution")
        if (
            not isinstance(edge, Mapping)
            or edge.get("relation_type")
            not in {"contradicts", "extends", "related", "supports"}
            or not isinstance(resolution, Mapping)
            or resolution.get("status") != "valid"
        ):
            raise LegacyP24PreparationError("Graph-edge evidence is invalid.")
        endpoints = (
            _record_id(resolution.get("from_record_id"), "from_record_id"),
            _record_id(resolution.get("to_record_id"), "to_record_id"),
        )
        evidence = {
            "graph_edge": dict(edge),
            "graph_edge_resolution": dict(resolution),
            "inventory_record_sha256": row.get("inventory_record_sha256"),
        }
    else:
        candidate = source.get("candidate")
        resolution = candidate.get("resolution") if isinstance(candidate, Mapping) else None
        if (
            not isinstance(candidate, Mapping)
            or (candidate.get("kind"), candidate.get("action"))
            not in {("graph_candidate", "add_edge"), ("wiki_diff", "create")}
            or candidate.get("status") != "pending"
            or not isinstance(resolution, Mapping)
            or resolution.get("status") != "resolved"
        ):
            raise LegacyP24PreparationError("Candidate evidence is invalid.")
        endpoints = (
            _record_id(resolution.get("source_record_id"), "source_record_id"),
            _record_id(resolution.get("target_record_id"), "target_record_id"),
        )
        evidence = {
            "candidate": dict(candidate),
            "inventory_record_sha256": row.get("inventory_record_sha256"),
        }
    if len(set(endpoints)) != len(endpoints):
        raise LegacyP24PreparationError("P2.4 endpoints are not distinct.")
    source_sha = source.get("sha256")
    path_key = source.get("path_key")
    if not isinstance(source_sha, str) or len(source_sha) != 64:
        raise LegacyP24PreparationError("P2.4 logical source hash is invalid.")
    if not isinstance(path_key, str) or not path_key:
        raise LegacyP24PreparationError("P2.4 source path key is invalid.")
    return _PreparedRecord(
        plan_row=row,
        source=source,
        record_id=record_id,
        mode=mode,  # type: ignore[arg-type]
        owner_user_id=owner,
        target_space_id=space,
        endpoint_record_ids=endpoints,
        relationship_ids=relationship_ids,
        effect_evidence_json=_canonical(evidence),
        source_record_sha256=source_sha,
        path_key=path_key,
    )


def _prepare_relationship(row: Mapping[str, object]) -> _PreparedRelationship:
    relationship_id = _relationship_id(row.get("relationship_id"))
    mode = row.get("import_mode")
    source = row.get("source_relationship")
    if (
        row.get("action") != "import"
        or row.get("execution_phase") != "P2.4"
        or mode not in {"graph_node", "graph_edge", "finding", "legacy_history"}
        or not isinstance(source, Mapping)
    ):
        raise LegacyP24PreparationError(
            "A selected relationship is not a supported imported P2.4 effect."
        )
    relationship_type = source.get("relationship_type")
    allowed = {
        "graph_node": {"graph_node_source"},
        "graph_edge": {"contradicts", "extends", "related", "supports"},
        "finding": {"candidate_source", "candidate_target"},
        "legacy_history": {"exact_content_duplicate"},
    }[str(mode)]
    if relationship_type not in allowed or source.get("status") != "valid":
        raise LegacyP24PreparationError("P2.4 relationship evidence is invalid.")
    source_id = (
        _record_id(source.get("source_record_id"), "source_record_id")
        if source.get("source_record_id") is not None
        else None
    )
    from_id = (
        _record_id(source.get("from_record_id"), "from_record_id")
        if source.get("from_record_id") is not None
        else None
    )
    to_id = _record_id(source.get("to_record_id"), "to_record_id")
    if mode == "graph_edge" and (source_id is None or from_id is None):
        raise LegacyP24PreparationError("Graph-edge relationship shape is invalid.")
    if mode in {"graph_node", "finding", "legacy_history"} and (
        source_id is not None or from_id is None
    ):
        raise LegacyP24PreparationError("Dependency relationship shape is invalid.")
    return _PreparedRelationship(
        plan_row=row,
        source=source,
        relationship_id=relationship_id,
        relationship_type=str(relationship_type),
        source_record_id=source_id,
        from_record_id=from_id,
        to_record_id=to_id,
        mode=mode,  # type: ignore[arg-type]
    )


def _prepare_container(
    row: Mapping[str, object],
    *,
    all_records: Sequence[Mapping[str, object]],
    all_relationships: Sequence[Mapping[str, object]],
    plan_sha256: str,
) -> _PreparedContainer:
    record_id = _record_id(row.get("record_id"))
    source = _source(row)
    if (
        row.get("action") != "import"
        or row.get("execution_phase") != "P2.4"
        or row.get("import_mode") != "source_container"
        or source.get("record_kind") != "physical_file"
        or source.get("category")
        not in {
            "knowledge.wiki_graph_edges",
            "knowledge.wiki_candidates",
            "knowledge.legacy_edges",
        }
    ):
        raise LegacyP24PreparationError(
            "A selected P2.4 source container is invalid."
        )
    owner = row.get("resolved_owner_user_id")
    if not isinstance(owner, str) or not owner:
        raise LegacyP24PreparationError(
            "P2.4 source containers require one personal owner."
        )
    children = tuple(
        sorted(
            (
                child
                for child in all_records
                if child.get("action") == "import"
                and child.get("execution_phase") == "P2.4"
                and child.get("import_mode")
                in {"graph_node", "graph_edge", "finding"}
                and isinstance(child.get("source_record"), Mapping)
                and child["source_record"].get("parent_record_id") == record_id
            ),
            key=lambda value: str(value.get("record_id")),
        )
    )
    child_ids = tuple(_record_id(value.get("record_id")) for value in children)
    child_id_set = set(child_ids)
    relationships = tuple(
        sorted(
            (
                relationship
                for relationship in all_relationships
                if relationship.get("action") == "import"
                and relationship.get("execution_phase") == "P2.4"
                and isinstance(relationship.get("source_relationship"), Mapping)
                and any(
                    relationship["source_relationship"].get(field)
                    in child_id_set
                    for field in ("source_record_id", "from_record_id")
                )
            ),
            key=lambda value: str(value.get("relationship_id")),
        )
    )
    relationship_ids = tuple(
        _relationship_id(value.get("relationship_id")) for value in relationships
    )
    child_bindings = [
        [
            migration_mapping_id_for_record(plan_sha256, child_id),
            child_id,
            str(_source(child)["sha256"]),
        ]
        for child_id, child in zip(child_ids, children, strict=True)
    ]
    relationship_bindings = [
        [
            p24_relationship_mapping_id(plan_sha256, relationship_id),
            relationship_id,
            str(relationship["inventory_relationship_sha256"]),
        ]
        for relationship_id, relationship in zip(
            relationship_ids, relationships, strict=True
        )
    ]
    path_key = source.get("path_key")
    source_sha = source.get("sha256")
    if not isinstance(path_key, str) or not isinstance(source_sha, str):
        raise LegacyP24PreparationError(
            "P2.4 source-container identity is invalid."
        )
    return _PreparedContainer(
        plan_row=row,
        source=source,
        record_id=record_id,
        owner_user_id=owner,
        target_space_id=space_id_for_owner("personal", owner),
        path_key=path_key,
        source_record_sha256=source_sha,
        child_record_ids=child_ids,
        relationship_ids=relationship_ids,
        child_bindings_sha256=hashlib.sha256(
            json.dumps(
                child_bindings,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        relationship_bindings_sha256=hashlib.sha256(
            json.dumps(
                relationship_bindings,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    )


def _prepare_batch(
    reviewed_plan_bytes: bytes,
    *,
    selected_record_ids: Sequence[str],
    selected_relationship_ids: Sequence[str],
    expected_plan_sha256: str,
) -> _PreparedBatch:
    if len(selected_record_ids) + len(selected_relationship_ids) == 0:
        raise LegacyP24PreparationError("A P2.4 batch cannot be empty.")
    if (
        len(selected_record_ids) + len(selected_relationship_ids)
        > MAX_P24_EFFECTS_PER_TRANSACTION
    ):
        raise LegacyP24PreparationError("A P2.4 transaction is limited to 100 effects.")
    plan = _reviewed_plan(
        reviewed_plan_bytes, expected_plan_sha256=expected_plan_sha256
    )
    draft = _mapping(plan.get("draft"), field="plan draft")
    rows_value = draft.get("records")
    relationships_value = draft.get("relationships")
    if not isinstance(rows_value, list) or not isinstance(relationships_value, list):
        raise LegacyP24PreparationError("Reviewed P2.4 ledgers are invalid.")
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
    try:
        selected_rows = tuple(
            rows[_record_id(value)] for value in sorted(set(selected_record_ids))
        )
        prepared_records = tuple(
            _prepare_record(value)
            for value in selected_rows
            if not (
                value.get("import_mode") == "source_container"
                and _source(value).get("record_kind") == "physical_file"
            )
        )
        prepared_containers = tuple(
            _prepare_container(
                value,
                all_records=tuple(
                    row for row in rows_value if isinstance(row, Mapping)
                ),
                all_relationships=tuple(
                    row for row in relationships_value if isinstance(row, Mapping)
                ),
                plan_sha256=str(plan["plan_sha256"]),
            )
            for value in selected_rows
            if value.get("import_mode") == "source_container"
            and _source(value).get("record_kind") == "physical_file"
        )
        prepared_relationships = tuple(
            _prepare_relationship(relationships[_relationship_id(value)])
            for value in sorted(set(selected_relationship_ids))
        )
    except KeyError:
        raise LegacyP24PreparationError(
            "A selected P2.4 effect is absent from the reviewed plan."
        ) from None
    owner_mappings_value = draft.get("owner_mappings")
    if not isinstance(owner_mappings_value, list):
        raise LegacyP24PreparationError("Reviewed owner mappings are invalid.")
    owner_mappings = {
        str(value.get("mapping_id")): value
        for value in owner_mappings_value
        if isinstance(value, Mapping)
    }
    dependency_record_ids: set[str] = {
        prepared.record_id for prepared in prepared_records
    } | {prepared.record_id for prepared in prepared_containers}
    for prepared in prepared_relationships:
        dependency_record_ids.update(
            value
            for value in (
                prepared.source_record_id,
                prepared.from_record_id,
                prepared.to_record_id,
            )
            if value is not None
        )
    owner_assertions: dict[str, _OwnerAssertion] = {}
    for record_id in sorted(dependency_record_ids):
        record = rows.get(record_id)
        if record is None:
            raise LegacyP24PreparationError(
                "A P2.4 owner dependency is absent from the reviewed plan."
            )
        user_id = record.get("resolved_owner_user_id")
        if not isinstance(user_id, str) or record.get("target_space_kind") != "personal":
            raise LegacyP24PreparationError(
                "P2.4 graph/finding dependencies require personal ownership."
            )
        mapping = owner_mappings.get(str(record.get("owner_mapping_id")))
        if (
            mapping is None
            or mapping.get("decision") != "personal"
            or mapping.get("user_id") != user_id
            or mapping.get("system_role") not in {"admin", "user"}
            or mapping.get("account_status") not in {"active", "disabled"}
        ):
            raise LegacyP24PreparationError(
                "P2.4 owner mapping is not exact."
            )
        part_id = mapping.get("part_id")
        if part_id is not None and not isinstance(part_id, str):
            raise LegacyP24PreparationError(
                "P2.4 reviewed part assertion is invalid."
            )
        assertion = _OwnerAssertion(
            user_id=user_id,
            system_role=str(mapping["system_role"]),
            account_status=str(mapping["account_status"]),
            part_id=part_id,
            target_space_id=space_id_for_owner("personal", user_id),
        )
        existing_assertion = owner_assertions.get(user_id)
        if existing_assertion is not None and existing_assertion != assertion:
            raise LegacyP24PreparationError(
                "P2.4 reviewed owner assertions conflict."
            )
        owner_assertions[user_id] = assertion
    source_inventory = _mapping(
        draft.get("source_inventory"), field="source inventory"
    )
    metadata = _mapping(source_inventory.get("metadata"), field="source metadata")
    run = _mapping(metadata.get("run"), field="inventory run")
    inputs = _mapping(draft.get("inputs"), field="plan inputs")
    inventory = _mapping(inputs.get("inventory"), field="inventory binding")
    review = _mapping(plan.get("review"), field="review")
    plan_sha = str(plan["plan_sha256"])
    return _PreparedBatch(
        plan_sha256=plan_sha,
        migration_run_id=migration_run_id_for_plan(plan_sha),
        inventory_manifest_sha256=str(inventory["manifest_sha256"]),
        root_binding_sha256=str(inventory["root_binding_sha256"]),
        reviewer_id=str(review["reviewer_id"]),
        effective_at_ms=_utc_ms(run.get("started_at_utc")),
        request_id=_derived_uuid(_REQUEST_NAMESPACE, plan_sha),
        operation_id=_derived_uuid(_OPERATION_NAMESPACE, plan_sha),
        owner_assertions=tuple(
            owner_assertions[user_id] for user_id in sorted(owner_assertions)
        ),
        records=prepared_records,
        relationships=prepared_relationships,
        containers=prepared_containers,
    )


def _dependency(
    connection: ManagedConnection, *, plan_sha256: str, record_id: str
) -> Mapping[str, object]:
    rows = connection.execute(
        """SELECT id,item_id,revision_id,edge_id,finding_id,
                  resolved_owner_user_id,
                  json_extract(details_json,'$.target_space_id') AS space_id
           FROM legacy_migration_map
           WHERE json_extract(details_json,'$.plan_sha256')=?
             AND json_extract(details_json,'$.inventory_record_id')=?""",
        (plan_sha256, record_id),
    ).fetchall()
    if len(rows) != 1:
        raise LegacyP24StateConflictError(
            "A P2.4 endpoint mapping is missing or ambiguous."
        )
    return dict(rows[0])


class LegacyP24Importer:
    """Apply one exact graph/finding batch in a service-owned transaction."""

    __slots__ = (
        "_audit",
        "_factory",
        "_failpoint",
        "_graph",
        "_mapping",
        "_outbox",
        "_relationships",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("LegacyP24Importer requires a ConnectionFactory.")
        self._factory = factory
        self._graph = GraphRepository()
        self._mapping = LegacyP24MappingRepository()
        self._relationships = LegacyP24RelationshipRepository()
        self._audit = AuditRepository()
        self._outbox = OutboxRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    @staticmethod
    def _assert_owner(
        connection: ManagedConnection, assertion: _OwnerAssertion
    ) -> None:
        row = connection.execute(
            """SELECT system_role,account_status,current_part_id
               FROM app_user WHERE id=?""",
            (assertion.user_id,),
        ).fetchone()
        if row is None or tuple(row) != (
            assertion.system_role,
            assertion.account_status,
            assertion.part_id,
        ):
            raise LegacyP24StateConflictError(
                "The reviewed P2.4 owner assertion drifted."
            )
        space = connection.execute(
            """SELECT space_kind,owner_user_id,status
               FROM knowledge_space WHERE id=?""",
            (assertion.target_space_id,),
        ).fetchone()
        if space is None or tuple(space) != (
            "personal",
            assertion.user_id,
            "active",
        ):
            raise LegacyP24StateConflictError(
                "The reviewed P2.4 personal-space assertion drifted."
            )

    def apply_batch(
        self,
        reviewed_plan_bytes: bytes,
        *,
        selected_record_ids: Sequence[str] = (),
        selected_relationship_ids: Sequence[str] = (),
        expected_plan_sha256: str,
    ) -> LegacyP24ImportResult:
        batch = _prepare_batch(
            reviewed_plan_bytes,
            selected_record_ids=selected_record_ids,
            selected_relationship_ids=selected_relationship_ids,
            expected_plan_sha256=expected_plan_sha256,
        )
        connection = self._factory.open()
        try:
            with transaction(connection):
                return self._apply(connection, batch)
        finally:
            connection.close()

    def _record_event(
        self,
        connection: ManagedConnection,
        batch: _PreparedBatch,
        *,
        mapping_id: str,
        prepared: _PreparedRecord,
        effect_id: str,
        disposition: str,
    ) -> None:
        details = {
            "effect_id": effect_id,
            "import_mode": prepared.mode,
            "inventory_record_id": prepared.record_id,
            "mapping_id": mapping_id,
            "migration_run_id": batch.migration_run_id,
            "plan_sha256": batch.plan_sha256,
            "schema_version": 1,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_AUDIT_NAMESPACE, mapping_id),
                occurred_at_ms=batch.effective_at_ms,
                request_id=batch.request_id,
                operation_id=batch.operation_id,
                action=f"legacy_migration.{prepared.mode}",
                outcome=disposition,
                actor_kind="system",
                actor_user_id=None,
                actor_display_name="P2.4 legacy migration",
                actor_role_snapshot="system",
                space_id=prepared.target_space_id,
                item_id=(effect_id if prepared.mode == "graph_node" else None),
                reason="reviewed P2.4 graph/finding import",
                details=details,
                request_meta={"reviewer_id": batch.reviewer_id},
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"legacy-migration-p24:{mapping_id}:imported:v1",
                space_id=prepared.target_space_id,
                aggregate_type="legacy_migration_map",
                aggregate_id=mapping_id,
                aggregate_version=1,
                event_type=f"legacy_migration.{prepared.mode}.imported",
                payload={**details, "operation_id": batch.operation_id},
                available_at_ms=batch.effective_at_ms,
            ),
        )

    def _apply_record(
        self, connection: ManagedConnection, batch: _PreparedBatch, prepared: _PreparedRecord
    ) -> LegacyP24RecordOutcome:
        dependencies = tuple(
            _dependency(
                connection,
                plan_sha256=batch.plan_sha256,
                record_id=record_id,
            )
            for record_id in prepared.endpoint_record_ids
        )
        if any(
            row["resolved_owner_user_id"] != prepared.owner_user_id
            or row["space_id"] != prepared.target_space_id
            or row["item_id"] is None
            or row["revision_id"] is None
            for row in dependencies
        ):
            raise LegacyP24StateConflictError(
                "P2.4 endpoints are not imported in one reviewed personal space."
            )
        existing = self._mapping.get_graph_by_record(
            connection,
            plan_sha256=batch.plan_sha256,
            inventory_record_id=prepared.record_id,
        )
        if existing is not None:
            disposition = str(json.loads(existing["details_json"])["result_disposition"])
        else:
            disposition = "created"
        edge_id = finding_id = item_id = revision_id = None
        if prepared.mode == "graph_node":
            item_id = str(dependencies[0]["item_id"])
            revision_id = str(dependencies[0]["revision_id"])
            effect_id = item_id
        elif prepared.mode == "graph_edge":
            source, target = dependencies
            edge_id = legacy_graph_edge_id(batch.plan_sha256, prepared.record_id)
            effect = self._graph.put_legacy_edge(
                connection,
                LegacyGraphEdge(
                    plan_sha256=batch.plan_sha256,
                    inventory_record_id=prepared.record_id,
                    edge_id=edge_id,
                    owner_user_id=prepared.owner_user_id,
                    space_id=prepared.target_space_id,
                    source_item_id=str(source["item_id"]),
                    source_revision_id=str(source["revision_id"]),
                    target_item_id=str(target["item_id"]),
                    target_revision_id=str(target["revision_id"]),
                    edge_type=str(prepared.source["graph_edge"]["relation_type"]),  # type: ignore[index,arg-type]
                    evidence_json=prepared.effect_evidence_json,
                    created_at_ms=batch.effective_at_ms,
                ),
            )
            if existing is None and not effect.inserted:
                disposition = "existing_exact"
            effect_id = edge_id
        else:
            source, target = dependencies
            finding_id = legacy_candidate_finding_id(
                batch.plan_sha256, prepared.record_id
            )
            candidate = prepared.source["candidate"]
            assert isinstance(candidate, Mapping)
            effect = self._graph.put_legacy_candidate(
                connection,
                LegacyCandidateFinding(
                    plan_sha256=batch.plan_sha256,
                    inventory_record_id=prepared.record_id,
                    finding_id=finding_id,
                    owner_user_id=prepared.owner_user_id,
                    space_id=prepared.target_space_id,
                    source_item_id=str(source["item_id"]),
                    source_revision_id=str(source["revision_id"]),
                    target_item_id=str(target["item_id"]),
                    target_revision_id=str(target["revision_id"]),
                    candidate_kind=str(candidate["kind"]),  # type: ignore[arg-type]
                    evidence_json=prepared.effect_evidence_json,
                    proposed_action_json=_canonical(
                        {
                            "legacy_action": candidate["action"],
                            "legacy_status": candidate["status"],
                        }
                    ),
                    created_at_ms=batch.effective_at_ms,
                ),
            )
            if existing is None and not effect.inserted:
                disposition = "existing_exact"
            effect_id = finding_id
        self._hit("p24.after_effect")
        source = prepared.source
        details = LegacyP24GraphDetails(
            plan_sha256=batch.plan_sha256,
            inventory_manifest_sha256=batch.inventory_manifest_sha256,
            root_binding_sha256=batch.root_binding_sha256,
            inventory_record_id=prepared.record_id,
            inventory_record_sha256=str(
                prepared.plan_row["inventory_record_sha256"]
            ),
            parent_inventory_record_id=_record_id(
                source.get("parent_record_id"), "parent_record_id"
            ),
            root_id=str(source["root_id"]),
            path_key_sha256=hashlib.sha256(
                prepared.path_key.encode("utf-8")
            ).hexdigest(),
            category=str(source["category"]),
            record_kind="logical_record",
            source_ordinal=int(source["source_ordinal"]),
            import_mode=prepared.mode,
            result_disposition=disposition,
            target_space_id=prepared.target_space_id,
            source_record_sha256=prepared.source_record_sha256,
            endpoint_inventory_record_ids=prepared.endpoint_record_ids,
            relationship_ids=prepared.relationship_ids,
            effect_evidence_sha256=hashlib.sha256(
                prepared.effect_evidence_json.encode("utf-8")
            ).hexdigest(),
        )
        self._hit("migration.before_checkpoint")
        mapped, inserted = self._mapping.put_graph_imported(
            connection,
            mapping_id=migration_mapping_id_for_record(
                batch.plan_sha256, prepared.record_id
            ),
            migration_run_id=batch.migration_run_id,
            legacy_kind=str(source["category"]),
            normalized_path=migration_source_locator(
                root_id=str(source["root_id"]),
                path_key=prepared.path_key,
                record_kind="logical_record",
                source_ordinal=int(source["source_ordinal"]),
            ),
            source_sha256=prepared.source_record_sha256,
            target=LegacyP24GraphTarget(
                resolved_owner_user_id=prepared.owner_user_id,
                item_id=item_id,
                revision_id=revision_id,
                edge_id=edge_id,
                finding_id=finding_id,
                details=details,
                source_path_key=prepared.path_key,
                effect_evidence_json=prepared.effect_evidence_json,
            ),
            now_ms=batch.effective_at_ms,
        )
        self._hit("p24.after_mapping")
        self._record_event(
            connection,
            batch,
            mapping_id=mapped.id,
            prepared=prepared,
            effect_id=effect_id,
            disposition=disposition,
        )
        return LegacyP24RecordOutcome(
            prepared.record_id,
            mapped.id,
            prepared.mode,
            effect_id,
            inserted,
        )

    def _apply_relationship(
        self,
        connection: ManagedConnection,
        batch: _PreparedBatch,
        prepared: _PreparedRelationship,
    ) -> LegacyP24RelationshipOutcome:
        endpoint_ids = {
            value
            for value in (
                prepared.source_record_id,
                prepared.from_record_id,
                prepared.to_record_id,
            )
            if value is not None
        }
        dependencies = {
            record_id: _dependency(
                connection,
                plan_sha256=batch.plan_sha256,
                record_id=record_id,
            )
            for record_id in endpoint_ids
        }
        to_row = dependencies[prepared.to_record_id]
        from_row = (
            dependencies[prepared.from_record_id]
            if prepared.from_record_id is not None
            else None
        )
        source_row = (
            dependencies[prepared.source_record_id]
            if prepared.source_record_id is not None
            else None
        )
        effect_row = source_row if prepared.mode == "graph_edge" else from_row
        if effect_row is None:
            raise LegacyP24StateConflictError(
                "P2.4 relationship effect mapping is missing."
            )
        edge_id = (
            str(effect_row["edge_id"])
            if prepared.mode == "graph_edge"
            else None
        )
        finding_id = (
            str(effect_row["finding_id"])
            if prepared.mode == "finding"
            else None
        )
        item_id = (
            str(from_row["item_id"])
            if prepared.mode == "legacy_history"
            and from_row is not None
            and from_row["item_id"] is not None
            else None
        )
        owner = str(to_row["resolved_owner_user_id"])
        space = str(to_row["space_id"])
        details = LegacyP24RelationshipDetails(
            plan_sha256=batch.plan_sha256,
            relationship_id=prepared.relationship_id,
            inventory_manifest_sha256=batch.inventory_manifest_sha256,
            root_binding_sha256=batch.root_binding_sha256,
            inventory_relationship_sha256=str(
                prepared.plan_row["inventory_relationship_sha256"]
            ),
            source_inventory_record_id=prepared.source_record_id,
            from_inventory_record_id=prepared.from_record_id,
            to_inventory_record_id=prepared.to_record_id,
            effect_mapping_id=str(effect_row["id"]),
        )
        self._hit("migration.before_checkpoint")
        mapped, inserted = self._relationships.put_imported(
            connection,
            LegacyP24RelationshipTarget(
                relationship_type=prepared.relationship_type,  # type: ignore[arg-type]
                source_mapping_id=(
                    str(source_row["id"]) if source_row is not None else None
                ),
                from_mapping_id=(
                    str(from_row["id"]) if from_row is not None else None
                ),
                to_mapping_id=str(to_row["id"]),
                resolved_owner_user_id=owner,
                target_space_id=space,
                effect_kind={
                    "graph_node": "dependency",
                    "graph_edge": "edge",
                    "finding": "finding",
                    "legacy_history": "provenance",
                }[prepared.mode],  # type: ignore[arg-type]
                edge_id=edge_id,
                finding_id=finding_id,
                item_id=item_id,
                details=details,
            ),
            now_ms=batch.effective_at_ms,
        )
        self._hit("p24.after_relationship_mapping")
        audit_details = {
            "effect_mapping_id": details.effect_mapping_id,
            "plan_sha256": batch.plan_sha256,
            "relationship_id": prepared.relationship_id,
            "relationship_mapping_id": mapped.id,
            "schema_version": 1,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_REL_AUDIT_NAMESPACE, mapped.id),
                occurred_at_ms=batch.effective_at_ms,
                request_id=batch.request_id,
                operation_id=batch.operation_id,
                action=f"legacy_migration.{prepared.relationship_type}",
                outcome="imported",
                actor_kind="system",
                actor_user_id=None,
                actor_display_name="P2.4 legacy migration",
                actor_role_snapshot="system",
                space_id=space,
                item_id=(
                    str(to_row["item_id"])
                    if to_row.get("item_id") is not None
                    else None
                ),
                reason="reviewed P2.4 relationship import",
                details=audit_details,
                request_meta={"reviewer_id": batch.reviewer_id},
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"legacy-migration-p24-relationship:{mapped.id}:imported:v1",
                space_id=space,
                aggregate_type="legacy_migration_p24_relationship_map",
                aggregate_id=mapped.id,
                aggregate_version=1,
                event_type=f"legacy_migration.{prepared.relationship_type}.imported",
                payload={**audit_details, "operation_id": batch.operation_id},
                available_at_ms=batch.effective_at_ms,
            ),
        )
        return LegacyP24RelationshipOutcome(
            prepared.relationship_id, mapped.id, inserted
        )

    def _apply_container(
        self,
        connection: ManagedConnection,
        batch: _PreparedBatch,
        prepared: _PreparedContainer,
    ) -> LegacyP24RecordOutcome:
        source = prepared.source
        details = LegacyP24ContainerDetails(
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
            source_record_sha256=prepared.source_record_sha256,
            target_space_id=prepared.target_space_id,
            planned_import_child_record_ids=prepared.child_record_ids,
            planned_import_relationship_ids=prepared.relationship_ids,
            imported_child_bindings_sha256=prepared.child_bindings_sha256,
            imported_relationship_bindings_sha256=(
                prepared.relationship_bindings_sha256
            ),
        )
        self._hit("migration.before_checkpoint")
        mapped, inserted = self._mapping.put_container_imported(
            connection,
            target=LegacyP24ContainerTarget(
                resolved_owner_user_id=prepared.owner_user_id,
                details=details,
                source_path_key=prepared.path_key,
            ),
            now_ms=batch.effective_at_ms,
        )
        self._hit("p24.after_container_mapping")
        event_details = {
            "import_mode": "source_container",
            "inventory_record_id": prepared.record_id,
            "mapping_id": mapped.id,
            "migration_run_id": batch.migration_run_id,
            "plan_sha256": batch.plan_sha256,
            "result_disposition": "dependency_closed",
            "schema_version": 1,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_AUDIT_NAMESPACE, mapped.id),
                occurred_at_ms=batch.effective_at_ms,
                request_id=batch.request_id,
                operation_id=batch.operation_id,
                action="legacy_migration.source_container",
                outcome="dependency_closed",
                actor_kind="system",
                actor_user_id=None,
                actor_display_name="P2.4 legacy migration",
                actor_role_snapshot="system",
                space_id=prepared.target_space_id,
                item_id=None,
                reason="reviewed P2.4 dependency-closed source container",
                details=event_details,
                request_meta={"reviewer_id": batch.reviewer_id},
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"legacy-migration-p24:{mapped.id}:imported:v1",
                space_id=prepared.target_space_id,
                aggregate_type="legacy_migration_map",
                aggregate_id=mapped.id,
                aggregate_version=1,
                event_type="legacy_migration.source_container.imported",
                payload={**event_details, "operation_id": batch.operation_id},
                available_at_ms=batch.effective_at_ms,
            ),
        )
        return LegacyP24RecordOutcome(
            prepared.record_id,
            mapped.id,
            "source_container",
            mapped.id,
            inserted,
        )

    def _apply(
        self, connection: ManagedConnection, batch: _PreparedBatch
    ) -> LegacyP24ImportResult:
        for assertion in batch.owner_assertions:
            self._assert_owner(connection, assertion)
        record_outcomes = tuple(
            self._apply_record(connection, batch, prepared)
            for prepared in batch.records
        )
        relationship_outcomes = tuple(
            self._apply_relationship(connection, batch, prepared)
            for prepared in batch.relationships
        )
        container_outcomes = tuple(
            self._apply_container(connection, batch, prepared)
            for prepared in batch.containers
        )
        return LegacyP24ImportResult(
            batch.plan_sha256,
            batch.migration_run_id,
            record_outcomes + container_outcomes,
            relationship_outcomes,
        )


__all__ = [
    "LegacyP24ImportError",
    "LegacyP24ImportResult",
    "LegacyP24Importer",
    "LegacyP24PreparationError",
    "LegacyP24RecordOutcome",
    "LegacyP24RelationshipOutcome",
    "LegacyP24StateConflictError",
    "MAX_P24_EFFECTS_PER_TRANSACTION",
]

"""Transaction-scoped aggregate graph and generation-vector primitives.

The repository is deliberately policy-free: callers own authentication,
authorization, transactions, audit, and outbox.  It does own the canonical LCA
calculation and exact replay checks so every writer and reader uses one
definition of aggregate-edge home.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from hashlib import sha256
import json
import re
import sqlite3
from typing import Literal

from ..db import ManagedConnection
from ..domain import AggregateEdgeStatus, SemanticEdgeDirection, SemanticRelationType
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError
from .semantic_graph_contracts import (
    AggregateGenerationVectorRecord,
    AggregateKnowledgeEdgeRecord,
)


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MAX_SOURCES = 4096
_MAX_INVALIDATION_TARGETS = 64
_EMPTY_VECTOR_JSON = "[]"
_EMPTY_VECTOR_SHA256 = sha256(_EMPTY_VECTOR_JSON.encode("ascii")).hexdigest()


class AggregateGraphRepositoryError(KnowledgeCoreDatabaseError):
    code = "AGGREGATE_GRAPH_REPOSITORY_ERROR"


class AggregateGraphConflictError(AggregateGraphRepositoryError):
    code = "AGGREGATE_GRAPH_CONFLICT"


@dataclass(frozen=True, slots=True)
class AggregateGraphPlacement:
    kind: Literal["local", "aggregate"]
    source_space_id: str
    target_space_id: str
    home_space_id: str
    home_org_unit_id: str | None
    home_kind: Literal["part", "team", "responsibility"]


@dataclass(frozen=True, slots=True)
class AggregateEndpointSnapshot:
    space_id: str
    space_kind: Literal["part", "team", "responsibility"]
    owner_org_unit_id: str
    item_id: str
    revision_id: str
    item_row_version: int


def _connection(
    connection: ManagedConnection, *, write: bool = False
) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("AggregateGraphRepository requires a ManagedConnection.")
    if write and not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Aggregate graph mutation requires a caller-owned transaction."
        )
    return connection


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")
    return value


def _canonical_json(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Aggregate graph data must be canonical JSON.") from exc
    json.loads(encoded)
    return encoded


def _record(record_type, row: object):
    try:
        values = {field.name: row[field.name] for field in fields(record_type)}
        if record_type is AggregateKnowledgeEdgeRecord:
            values["edge_type"] = SemanticRelationType(values["edge_type"])
            values["direction"] = SemanticEdgeDirection(values["direction"])
            values["edge_status"] = AggregateEdgeStatus(values["edge_status"])
        return record_type(**values)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise AggregateGraphRepositoryError(
            "Stored aggregate graph state is malformed."
        ) from exc


class AggregateGraphRepository:
    """Canonical LCA, edge replay, and bounded vector operations."""

    __slots__ = ()

    def resolve_placement(
        self,
        connection: ManagedConnection,
        *,
        source_space_id: str,
        target_space_id: str,
    ) -> AggregateGraphPlacement:
        connection = _connection(connection)
        source = _uuid(source_space_id, "source_space_id")
        target = _uuid(target_space_id, "target_space_id")
        spaces = connection.execute(
            """SELECT id,space_kind,owner_org_unit_id,status
                 FROM knowledge_space WHERE id IN (?,?) ORDER BY id""",
            (source, target),
        ).fetchall()
        by_id = {str(row["id"]): row for row in spaces}
        if set(by_id) != {source, target}:
            raise AggregateGraphConflictError("An endpoint space is unavailable.")
        for row in by_id.values():
            if row["status"] != "active" or row["space_kind"] == "personal":
                raise AggregateGraphConflictError(
                    "Aggregate endpoints require active organizational spaces."
                )
        if source == target:
            row = by_id[source]
            kind = str(row["space_kind"])
            if kind not in {"part", "team", "responsibility"}:
                raise AggregateGraphConflictError("The local graph home is invalid.")
            return AggregateGraphPlacement(
                kind="local",
                source_space_id=source,
                target_space_id=target,
                home_space_id=source,
                home_org_unit_id=str(row["owner_org_unit_id"]),
                home_kind=kind,  # type: ignore[arg-type]
            )
        row = connection.execute(
            """WITH RECURSIVE
                 source_ancestors(id,depth) AS (
                   SELECT owner_org_unit_id,0 FROM knowledge_space WHERE id=?
                   UNION ALL
                   SELECT unit.parent_id,source_ancestors.depth+1
                     FROM org_unit AS unit JOIN source_ancestors
                       ON unit.id=source_ancestors.id
                    WHERE unit.parent_id IS NOT NULL
                 ),
                 target_ancestors(id,depth) AS (
                   SELECT owner_org_unit_id,0 FROM knowledge_space WHERE id=?
                   UNION ALL
                   SELECT unit.parent_id,target_ancestors.depth+1
                     FROM org_unit AS unit JOIN target_ancestors
                       ON unit.id=target_ancestors.id
                    WHERE unit.parent_id IS NOT NULL
                 )
                 SELECT home.id AS home_space_id,unit.id AS home_org_unit_id,
                        unit.kind AS home_kind
                   FROM source_ancestors AS source
                   JOIN target_ancestors AS target ON target.id=source.id
                   JOIN org_unit AS unit ON unit.id=source.id
                   JOIN knowledge_space AS home
                     ON home.owner_org_unit_id=unit.id
                    AND home.space_kind=unit.kind
                  WHERE unit.status='active'
                    AND unit.kind IN ('team','responsibility')
                    AND home.status='active'
                  ORDER BY source.depth+target.depth,source.id
                  LIMIT 1""",
            (source, target),
        ).fetchone()
        if row is None:
            raise AggregateGraphConflictError(
                "The endpoint pair has no active Team/Responsibility LCA."
            )
        return AggregateGraphPlacement(
            kind="aggregate",
            source_space_id=source,
            target_space_id=target,
            home_space_id=str(row["home_space_id"]),
            home_org_unit_id=str(row["home_org_unit_id"]),
            home_kind=str(row["home_kind"]),  # type: ignore[arg-type]
        )

    def require_current_endpoint(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        item_id: str,
        revision_id: str,
        item_row_version: int,
    ) -> AggregateEndpointSnapshot:
        connection = _connection(connection)
        space = _uuid(space_id, "space_id")
        item = _uuid(item_id, "item_id")
        revision = _uuid(revision_id, "revision_id")
        if (
            isinstance(item_row_version, bool)
            or not isinstance(item_row_version, int)
            or item_row_version < 1
        ):
            raise ValueError("item_row_version must be positive.")
        row = connection.execute(
            """SELECT item.space_id,space.space_kind,space.owner_org_unit_id,
                      item.id AS item_id,item.current_revision_id AS revision_id,
                      item.row_version AS item_row_version
                 FROM knowledge_item AS item
                 JOIN knowledge_space AS space ON space.id=item.space_id
                WHERE item.id=? AND item.space_id=?
                  AND item.current_revision_id=? AND item.row_version=?
                  AND item.lifecycle_status='active'
                  AND item.curation_status='published'
                  AND space.status='active'
                  AND space.space_kind IN ('part','team','responsibility')""",
            (item, space, revision, item_row_version),
        ).fetchone()
        if row is None:
            raise AggregateGraphConflictError(
                "An aggregate endpoint is stale or not currently readable."
            )
        return AggregateEndpointSnapshot(
            space_id=str(row["space_id"]),
            space_kind=str(row["space_kind"]),  # type: ignore[arg-type]
            owner_org_unit_id=str(row["owner_org_unit_id"]),
            item_id=str(row["item_id"]),
            revision_id=str(row["revision_id"]),
            item_row_version=int(row["item_row_version"]),
        )

    def append_edge(
        self,
        connection: ManagedConnection,
        record: AggregateKnowledgeEdgeRecord,
    ) -> AggregateKnowledgeEdgeRecord:
        connection = _connection(connection, write=True)
        if not isinstance(record, AggregateKnowledgeEdgeRecord):
            raise TypeError("An AggregateKnowledgeEdgeRecord is required.")
        columns = ",".join(field.name for field in fields(record))
        placeholders = ",".join("?" for _ in fields(record))
        values = tuple(
            value.value if hasattr(value, "value") else value
            for value in (getattr(record, field.name) for field in fields(record))
        )
        try:
            cursor = connection.execute(
                f"INSERT INTO aggregate_knowledge_edge({columns}) "
                f"VALUES ({placeholders}) ON CONFLICT(effect_key) DO NOTHING",
                values,
            )
        except sqlite3.IntegrityError as exc:
            raise AggregateGraphConflictError(
                "The aggregate edge violates its canonical storage contract."
            ) from exc
        row = connection.execute(
            f"SELECT {columns} FROM aggregate_knowledge_edge WHERE effect_key=?",
            (record.effect_key,),
        ).fetchone()
        stored = None if row is None else _record(AggregateKnowledgeEdgeRecord, row)
        if stored != record:
            raise AggregateGraphConflictError(
                "The aggregate-edge effect key is bound incompatibly."
            )
        if cursor.rowcount not in (0, 1):
            raise AggregateGraphRepositoryError(
                "The aggregate-edge insert returned an invalid row count."
            )
        return stored

    def capture_source_vector(
        self,
        connection: ManagedConnection,
        *,
        selected_space_id: str,
    ) -> tuple[str, str, int]:
        connection = _connection(connection)
        selected = _uuid(selected_space_id, "selected_space_id")
        selected_row = connection.execute(
            """SELECT space.owner_org_unit_id,space.space_kind,space.status,
                      unit.row_version AS organization_row_version
                 FROM knowledge_space AS space JOIN org_unit AS unit
                   ON unit.id=space.owner_org_unit_id
                WHERE space.id=?""",
            (selected,),
        ).fetchone()
        if (
            selected_row is None
            or selected_row["status"] != "active"
            or selected_row["space_kind"] not in {"team", "responsibility"}
        ):
            raise AggregateGraphConflictError(
                "Generation vectors require an active higher space."
            )
        rows = connection.execute(
            """WITH RECURSIVE descendants(id) AS (
                   SELECT ?
                   UNION ALL
                   SELECT unit.id FROM org_unit AS unit JOIN descendants
                     ON unit.parent_id=descendants.id
                    WHERE unit.status='active'
                 )
                 SELECT space.id AS source_space_id,
                        space.generation AS content_generation,
                        space.generation + CASE
                          WHEN space.id=? THEN 0
                          ELSE coalesce(graph.home_edge_generation,0)
                        END AS graph_generation,
                        source_unit.row_version AS organization_row_version,
                        space.temporal_generation AS temporal_generation
                   FROM knowledge_space AS space JOIN descendants
                     ON descendants.id=space.owner_org_unit_id
                   JOIN org_unit AS source_unit
                     ON source_unit.id=space.owner_org_unit_id
                   LEFT JOIN aggregate_generation_vector AS graph
                     ON graph.selected_space_id=space.id
                  WHERE space.status='active'
                  ORDER BY space.id LIMIT ?""",
            (
                str(selected_row["owner_org_unit_id"]),
                selected,
                _MAX_SOURCES + 1,
            ),
        ).fetchall()
        if len(rows) > _MAX_SOURCES:
            raise AggregateGraphConflictError(
                "The aggregate source vector exceeds its bounded size."
            )
        vector = [
            {
                "content_generation": int(row["content_generation"]),
                "graph_generation": int(row["graph_generation"]),
                "organization_row_version": int(
                    row["organization_row_version"]
                ),
                "source_space_id": str(row["source_space_id"]),
                "temporal_generation": int(row["temporal_generation"]),
            }
            for row in rows
        ]
        vector_json = _canonical_json(vector)
        return (
            vector_json,
            sha256(vector_json.encode("utf-8")).hexdigest(),
            int(selected_row["organization_row_version"]),
        )

    def ensure_generation_vector(
        self,
        connection: ManagedConnection,
        *,
        selected_space_id: str,
        closure_proof_sha256: str,
        now_ms: int,
        increment_home_edge: bool = False,
        advance_desired_on_change: bool = True,
    ) -> AggregateGenerationVectorRecord:
        connection = _connection(connection, write=True)
        selected = _uuid(selected_space_id, "selected_space_id")
        closure = _sha(closure_proof_sha256, "closure_proof_sha256")
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("now_ms must be non-negative.")
        if not isinstance(increment_home_edge, bool):
            raise TypeError("increment_home_edge must be Boolean.")
        if not isinstance(advance_desired_on_change, bool):
            raise TypeError("advance_desired_on_change must be Boolean.")
        vector_json, vector_sha, org_version = self.capture_source_vector(
            connection, selected_space_id=selected
        )
        existing = connection.execute(
            "SELECT * FROM aggregate_generation_vector WHERE selected_space_id=?",
            (selected,),
        ).fetchone()
        if existing is None:
            desired = 1 if increment_home_edge else 0
            connection.execute(
                """INSERT INTO aggregate_generation_vector(
                     selected_space_id,source_vector_json,source_vector_sha256,
                     home_edge_generation,desired_generation,built_generation,
                     manifest_sha256,closure_proof_sha256,
                     organization_row_version,status,last_error_json,
                     created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,?,0,NULL,?,?,'idle',NULL,?,?,1)""",
                (
                    selected,
                    vector_json,
                    vector_sha,
                    int(increment_home_edge),
                    desired,
                    closure,
                    org_version,
                    now_ms,
                    now_ms,
                ),
            )
        else:
            changed = (
                str(existing["source_vector_sha256"]) != vector_sha
                or str(existing["closure_proof_sha256"]) != closure
                or int(existing["organization_row_version"]) != org_version
            )
            if changed or increment_home_edge:
                desired_increment = int(
                    increment_home_edge
                    or (changed and advance_desired_on_change)
                )
                connection.execute(
                    """UPDATE aggregate_generation_vector
                          SET source_vector_json=?,source_vector_sha256=?,
                              home_edge_generation=home_edge_generation+?,
                              desired_generation=desired_generation+?,
                              manifest_sha256=NULL,closure_proof_sha256=?,
                              organization_row_version=?,status='idle',
                              last_error_json=NULL,updated_at=?,row_version=row_version+1
                        WHERE selected_space_id=?""",
                    (
                        vector_json,
                        vector_sha,
                        int(increment_home_edge),
                        desired_increment,
                        closure,
                        org_version,
                        now_ms,
                        selected,
                    ),
                )
        row = connection.execute(
            "SELECT * FROM aggregate_generation_vector WHERE selected_space_id=?",
            (selected,),
        ).fetchone()
        if row is None:
            raise AggregateGraphRepositoryError(
                "The aggregate generation vector could not be read back."
            )
        return _record(AggregateGenerationVectorRecord, row)

    def invalidate_generation_targets(
        self,
        connection: ManagedConnection,
        *,
        selected_space_ids: tuple[str, ...],
        now_ms: int,
        create_missing: bool = False,
    ) -> tuple[str, ...]:
        """Mark exact higher targets dirty without scanning their descendants.

        When ``create_missing`` is explicitly enabled, a missing target receives
        a pending empty-vector sentinel.  The graph projector replaces that
        sentinel with one bounded canonical source vector before reading or
        publishing the target.  Ordinary pre-activation mutations only touch
        vectors already owned by this feature, preserving the default-off write
        boundary.  Mutation latency is proportional to the organization
        ancestor chain, never the descendant corpus size.
        """

        connection = _connection(connection, write=True)
        if not isinstance(selected_space_ids, tuple):
            raise TypeError("selected_space_ids must be a tuple.")
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("now_ms must be non-negative.")
        if not isinstance(create_missing, bool):
            raise TypeError("create_missing must be Boolean.")
        target_list: list[str] = []
        seen_targets: set[str] = set()
        for value in selected_space_ids:
            target = _uuid(value, "selected_space_id")
            if target not in seen_targets:
                seen_targets.add(target)
                target_list.append(target)
        targets = tuple(target_list)
        if len(targets) > _MAX_INVALIDATION_TARGETS:
            raise AggregateGraphConflictError(
                "The aggregate invalidation target set exceeds its bound."
            )
        if not targets:
            return ()
        placeholders = ",".join("?" for _ in targets)
        rows = connection.execute(
            f"""SELECT space.id,unit.row_version AS organization_row_version
                   FROM knowledge_space AS space JOIN org_unit AS unit
                     ON unit.id=space.owner_org_unit_id
                  WHERE space.id IN ({placeholders})
                    AND space.space_kind IN ('team','responsibility')
                    AND space.status='active' AND unit.status='active'
                  ORDER BY space.id""",
            targets,
        ).fetchall()
        by_id = {str(row["id"]): row for row in rows}
        if set(by_id) != set(targets):
            raise AggregateGraphConflictError(
                "An aggregate invalidation target is not an active higher space."
            )
        invalidated: list[str] = []
        for target in targets:
            org_version = int(by_id[target]["organization_row_version"])
            existing = connection.execute(
                "SELECT 1 FROM aggregate_generation_vector WHERE selected_space_id=?",
                (target,),
            ).fetchone()
            if existing is None:
                if not create_missing:
                    continue
                connection.execute(
                    """INSERT INTO aggregate_generation_vector(
                         selected_space_id,source_vector_json,source_vector_sha256,
                         home_edge_generation,desired_generation,built_generation,
                         manifest_sha256,closure_proof_sha256,
                         organization_row_version,status,last_error_json,
                         created_at,updated_at,row_version
                       ) VALUES (?,?,?,0,1,0,NULL,?,?,'idle',NULL,?,?,1)""",
                    (
                        target,
                        _EMPTY_VECTOR_JSON,
                        _EMPTY_VECTOR_SHA256,
                        _EMPTY_VECTOR_SHA256,
                        org_version,
                        now_ms,
                        now_ms,
                    ),
                )
                invalidated.append(target)
                continue
            connection.execute(
                """UPDATE aggregate_generation_vector
                      SET desired_generation=desired_generation+1,
                          manifest_sha256=NULL,organization_row_version=?,status='idle',
                          last_error_json=NULL,updated_at=?,row_version=row_version+1
                    WHERE selected_space_id=?""",
                (org_version, now_ms, target),
            )
            invalidated.append(target)
        return tuple(invalidated)

    def invalidate_current_ancestors(
        self,
        connection: ManagedConnection,
        *,
        source_space_id: str,
        now_ms: int,
        include_self: bool = True,
        create_missing: bool = False,
    ) -> tuple[str, ...]:
        connection = _connection(connection, write=True)
        source = _uuid(source_space_id, "source_space_id")
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("now_ms must be non-negative.")
        if not isinstance(include_self, bool):
            raise TypeError("include_self must be Boolean.")
        if not isinstance(create_missing, bool):
            raise TypeError("create_missing must be Boolean.")
        rows = connection.execute(
            """WITH RECURSIVE ancestors(id,depth) AS (
                   SELECT owner_org_unit_id,0 FROM knowledge_space WHERE id=?
                   UNION ALL
                   SELECT unit.parent_id,ancestors.depth+1
                     FROM org_unit AS unit JOIN ancestors
                     ON unit.id=ancestors.id WHERE unit.parent_id IS NOT NULL
                 )
                 SELECT space.id,ancestors.depth
                   FROM knowledge_space AS space JOIN ancestors
                     ON ancestors.id=space.owner_org_unit_id
                  WHERE space.space_kind IN ('team','responsibility')
                    AND space.status='active'
                  ORDER BY ancestors.depth,space.id""",
            (source,),
        ).fetchall()
        targets = tuple(
            str(row["id"])
            for row in rows
            if include_self or str(row["id"]) != source
        )
        return self.invalidate_generation_targets(
            connection,
            selected_space_ids=targets,
            now_ms=now_ms,
            create_missing=create_missing,
        )


__all__ = [
    "AggregateEndpointSnapshot",
    "AggregateGraphConflictError",
    "AggregateGraphPlacement",
    "AggregateGraphRepository",
    "AggregateGraphRepositoryError",
]

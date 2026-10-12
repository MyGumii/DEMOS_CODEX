"""Bounded repository primitives for SBE automatic semantic-edge work.

This module contains only transaction-scoped SQL and immutable snapshots.  It
does not call a model, open a transaction, infer user authority, or activate an
edge.  The seed/materializer/handler services compose these primitives.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Literal

from ..db import ManagedConnection
from ..domain import SemanticIntentTargetKind
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError
from .semantic_graph_contracts import (
    SEMANTIC_INTENT_BATCH_MAX_SIZE,
    SemanticEdgeIntentRecord,
    SemanticEdgeIntentSeedRunRecord,
    SemanticLifecycleVector,
)


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
SEMANTIC_LEXICAL_CANDIDATE_LIMIT = 20
SEMANTIC_GRAPH_NEIGHBOR_LIMIT = 20
SEMANTIC_MODEL_CANDIDATE_LIMIT = 12
SEMANTIC_SEED_BATCH_LIMIT = 200


class AutomaticSemanticEdgeRepositoryError(KnowledgeCoreDatabaseError):
    code = "AUTOMATIC_SEMANTIC_EDGE_REPOSITORY_ERROR"


@dataclass(frozen=True, slots=True)
class SemanticSeedSource:
    updated_at: int
    source_space_id: str
    source_item_id: str
    source_revision_id: str
    source_body_sha256: str
    source_space_generation: int


@dataclass(frozen=True, slots=True)
class SemanticIntentTarget:
    target_kind: SemanticIntentTargetKind
    target_space_id: str
    target_org_unit_id: str | None
    target_space_generation: int
    organization_generation: int


@dataclass(frozen=True, slots=True)
class SemanticSourceDocument:
    space_id: str
    item_id: str
    revision_id: str
    item_row_version: int
    title: str
    content_md: str
    normalized_content_sha256: str


@dataclass(frozen=True, slots=True)
class SemanticCandidateDocument:
    space_id: str
    item_id: str
    revision_id: str
    item_row_version: int
    title: str
    content_md: str
    normalized_content_sha256: str
    lexical_ordinal: int | None
    graph_neighbor: bool


def _connection(value: object, *, write: bool = False) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError("AutomaticSemanticEdgeRepository requires a ManagedConnection.")
    if write and not value.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Automatic semantic-edge writes require a caller-owned transaction."
        )
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _limit(value: object, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError("limit is outside the bounded semantic-edge range.")
    return value


class AutomaticSemanticEdgeRepository:
    """Bounded seed, materialization, and candidate snapshot reads."""

    __slots__ = ()

    @staticmethod
    def supports_native_aggregate_targets(connection: ManagedConnection) -> bool:
        # Validated factory/catalog owns schema identity. Never emit a v48
        # intent into an older database whose insert guard cannot accept it.
        connection = _connection(connection)
        from ..migrations.native_semantic_targets_catalog import SCHEMA_V48_MIGRATION
        return connection.execute(
            "SELECT 1 FROM schema_migration WHERE version=48 AND name=? AND checksum_sha256=?",
            (SCHEMA_V48_MIGRATION.name, SCHEMA_V48_MIGRATION.checksum_sha256),
        ).fetchone() is not None

    def snapshot_high_water(
        self,
        connection: ManagedConnection,
        *,
        scope_kind: str,
        scope_id: str | None,
    ) -> tuple[int, int]:
        connection = _connection(connection)
        if scope_kind not in {"global", "org_unit", "item", "wiki"}:
            raise ValueError("scope_kind is invalid.")
        if (scope_kind == "global") != (scope_id is None):
            raise ValueError("scope_id does not match scope_kind.")
        if scope_id is not None:
            scope_id = _uuid(scope_id, "scope_id")
        clause, parameters = self._scope_clause(scope_kind, scope_id)
        row = connection.execute(
            f"""SELECT coalesce(max(item.updated_at),0) AS domain_high_water
                    FROM knowledge_item AS item
                    JOIN knowledge_space AS space ON space.id=item.space_id
                   WHERE item.lifecycle_status='active'
                     AND item.curation_status='published'
                     AND space.status='active' {clause}""",
            parameters,
        ).fetchone()
        audit = connection.execute(
            "SELECT coalesce(max(id),0) AS audit_high_water FROM audit_event"
        ).fetchone()
        if row is None or audit is None:
            raise AutomaticSemanticEdgeRepositoryError(
                "The semantic seed high-water marks are unavailable."
            )
        return int(row["domain_high_water"]), int(audit["audit_high_water"])

    @staticmethod
    def _scope_clause(
        scope_kind: str, scope_id: str | None
    ) -> tuple[str, tuple[object, ...]]:
        if scope_kind == "global":
            return "", ()
        if scope_kind == "item":
            return "AND item.id=?", (scope_id,)
        if scope_kind == "wiki":
            return "AND item.space_id=?", (scope_id,)
        return (
            """AND space.owner_org_unit_id IN (
                   WITH RECURSIVE descendants(id) AS (
                     SELECT ? UNION ALL
                     SELECT child.id FROM org_unit AS child JOIN descendants
                       ON child.parent_id=descendants.id
                      WHERE child.status='active'
                   ) SELECT id FROM descendants
                 )""",
            (scope_id,),
        )

    def list_seed_sources(
        self,
        connection: ManagedConnection,
        run: SemanticEdgeIntentSeedRunRecord,
        *,
        limit: int = SEMANTIC_SEED_BATCH_LIMIT,
    ) -> tuple[SemanticSeedSource, ...]:
        connection = _connection(connection)
        if not isinstance(run, SemanticEdgeIntentSeedRunRecord) or run.state != "running":
            raise ValueError("A running semantic seed record is required.")
        limit = _limit(limit, SEMANTIC_SEED_BATCH_LIMIT)
        after_revision = run.cursor_revision_id or "00000000-0000-0000-0000-000000000000"
        clause, scope_parameters = self._scope_clause(run.scope_kind, run.scope_id)
        rows = connection.execute(
            f"""SELECT item.updated_at,space.id AS source_space_id,
                        item.id AS source_item_id,
                        revision.id AS source_revision_id,
                        revision.normalized_content_sha256 AS source_body_sha256,
                        space.generation AS source_space_generation
                   FROM knowledge_item AS item
                   JOIN knowledge_space AS space ON space.id=item.space_id
                   JOIN knowledge_revision AS revision
                     ON revision.id=item.current_revision_id
                    AND revision.item_id=item.id
                  WHERE item.lifecycle_status='active'
                    AND item.curation_status='published'
                    AND space.status='active'
                    AND item.updated_at<=?
                    AND (item.updated_at,revision.id)>(?,?)
                    {clause}
                  ORDER BY item.updated_at,revision.id LIMIT ?""",
            (
                run.snapshot_domain_high_water,
                run.cursor_updated_at,
                after_revision,
                *scope_parameters,
                limit,
            ),
        ).fetchall()
        return tuple(
            SemanticSeedSource(
                updated_at=int(row["updated_at"]),
                source_space_id=str(row["source_space_id"]),
                source_item_id=str(row["source_item_id"]),
                source_revision_id=str(row["source_revision_id"]),
                source_body_sha256=str(row["source_body_sha256"]),
                source_space_generation=int(row["source_space_generation"]),
            )
            for row in rows
        )

    def list_targets(
        self,
        connection: ManagedConnection,
        *,
        source_space_id: str,
    ) -> tuple[SemanticIntentTarget, ...]:
        """Local plus eligible aggregate homes, including a native higher home.

        A Team/Responsibility source needs two distinct jobs at its own space:
        local compares native peers; aggregate compares strict descendants.
        Part/personal sources still have no aggregate home at depth zero.
        """
        connection = _connection(connection)
        source = _uuid(source_space_id, "source_space_id")
        local = connection.execute(
            """SELECT space.id,space.generation,space.owner_org_unit_id,
                      unit.row_version AS organization_generation
                 FROM knowledge_space AS space LEFT JOIN org_unit AS unit
                   ON unit.id=space.owner_org_unit_id
                WHERE space.id=? AND space.status='active'""",
            (source,),
        ).fetchone()
        if local is None:
            raise AutomaticSemanticEdgeRepositoryError(
                "The semantic source space is unavailable."
            )
        values = [
            SemanticIntentTarget(
                SemanticIntentTargetKind.LOCAL,
                source,
                None if local["owner_org_unit_id"] is None else str(local["owner_org_unit_id"]),
                int(local["generation"]),
                0 if local["organization_generation"] is None else int(local["organization_generation"]),
            )
        ]
        rows = connection.execute(
            """WITH RECURSIVE ancestors(id,depth) AS (
                   SELECT owner_org_unit_id,0 FROM knowledge_space WHERE id=?
                   UNION ALL
                   SELECT unit.parent_id,ancestors.depth+1
                     FROM org_unit AS unit JOIN ancestors ON unit.id=ancestors.id
                    WHERE unit.parent_id IS NOT NULL
                 )
                 SELECT space.id AS target_space_id,
                        space.generation AS target_space_generation,
                        unit.id AS target_org_unit_id,
                        unit.row_version AS organization_generation,
                        ancestors.depth
                   FROM ancestors JOIN org_unit AS unit ON unit.id=ancestors.id
                   JOIN knowledge_space AS space
                     ON space.owner_org_unit_id=unit.id
                    AND space.space_kind=unit.kind
                  WHERE (ancestors.depth>0 OR ?=1) AND unit.status='active'
                    AND unit.kind IN ('team','responsibility')
                    AND space.status='active'
                  ORDER BY ancestors.depth,space.id LIMIT 2""",
            (source, int(self.supports_native_aggregate_targets(connection))),
        ).fetchall()
        values.extend(
            SemanticIntentTarget(
                SemanticIntentTargetKind.AGGREGATE,
                str(row["target_space_id"]),
                str(row["target_org_unit_id"]),
                int(row["target_space_generation"]),
                int(row["organization_generation"]),
            )
            for row in rows
        )
        return tuple(values)

    def find_generation_intent(
        self,
        connection: ManagedConnection,
        *,
        source_revision_id: str,
        target_kind: SemanticIntentTargetKind,
        target_space_id: str,
        detector_generation: int,
        organization_generation: int,
        item_lifecycle_generation: int,
        wiki_lifecycle_generation: int,
    ) -> SemanticEdgeIntentRecord | None:
        connection = _connection(connection)
        source_revision_id = _uuid(source_revision_id, "source_revision_id")
        target_space_id = _uuid(target_space_id, "target_space_id")
        row = connection.execute(
            """SELECT id FROM semantic_edge_intent
                WHERE source_revision_id=? AND target_kind=? AND target_space_id=?
                  AND detector_generation=? AND organization_generation=?
                  AND item_lifecycle_generation=? AND wiki_lifecycle_generation=?""",
            (
                source_revision_id,
                target_kind.value,
                target_space_id,
                detector_generation,
                organization_generation,
                item_lifecycle_generation,
                wiki_lifecycle_generation,
            ),
        ).fetchone()
        if row is None:
            return None
        from .semantic_graph_contracts import SemanticGraphContractRepository

        return SemanticGraphContractRepository().get_intent(connection, str(row["id"]))

    def has_missing_native_target_intents(
        self, connection: ManagedConnection, *, detector_generation: int,
    ) -> bool:
        """Attest v48 adoption for every current published native revision.

        This is a backfill coverage gate, not ongoing generation/job health.
        The existing local check and lifecycle/retarget workers keep their own
        contracts. Requiring current organization generations here would block
        startup after renames (which do not schedule semantic reseeding).
        """
        connection = _connection(connection)
        return connection.execute(
            """SELECT 1 FROM knowledge_item i
                JOIN knowledge_space s ON s.id=i.space_id
                JOIN org_unit o ON o.id=s.owner_org_unit_id
               WHERE i.lifecycle_status='active' AND i.curation_status='published'
                 AND s.status='active' AND o.status='active'
                 AND s.space_kind IN ('team','responsibility')
                 AND NOT EXISTS (
                   SELECT 1 FROM semantic_edge_intent intent
                    WHERE intent.source_revision_id=i.current_revision_id
                      AND intent.source_space_id=i.space_id AND intent.source_item_id=i.id
                      AND intent.target_kind='aggregate' AND intent.target_space_id=i.space_id
                      AND intent.target_org_unit_id=s.owner_org_unit_id
                      AND intent.detector_generation=?
                 ) LIMIT 1""",
            (detector_generation,),
        ).fetchone() is not None

    def list_unjoined_intents(
        self,
        connection: ManagedConnection,
        *,
        local_enabled: bool,
        aggregate_enabled: bool,
        limit: int = SEMANTIC_INTENT_BATCH_MAX_SIZE,
    ) -> tuple[SemanticEdgeIntentRecord, ...]:
        connection = _connection(connection)
        if not isinstance(local_enabled, bool) or not isinstance(aggregate_enabled, bool):
            raise TypeError("semantic materializer flags must be Boolean.")
        limit = _limit(limit, SEMANTIC_INTENT_BATCH_MAX_SIZE)
        if not local_enabled:
            return ()
        kinds = ("local", "aggregate") if aggregate_enabled else ("local",)
        placeholders = ",".join("?" for _ in kinds)
        rows = connection.execute(
            f"""SELECT intent.id
                   FROM semantic_edge_intent AS intent
                  WHERE intent.target_kind IN ({placeholders})
                    AND NOT EXISTS (
                      SELECT 1 FROM durable_job AS job
                       WHERE job.effect_key=intent.effect_key
                    )
                  ORDER BY intent.created_at,intent.id LIMIT ?""",
            (*kinds, limit),
        ).fetchall()
        from .semantic_graph_contracts import SemanticGraphContractRepository

        repository = SemanticGraphContractRepository()
        values = []
        for row in rows:
            value = repository.get_intent(connection, str(row["id"]))
            if value is None:
                raise AutomaticSemanticEdgeRepositoryError(
                    "A selected semantic intent disappeared."
                )
            values.append(value)
        return tuple(values)

    def load_source_document(
        self,
        connection: ManagedConnection,
        intent: SemanticEdgeIntentRecord,
    ) -> SemanticSourceDocument | None:
        connection = _connection(connection)
        if not isinstance(intent, SemanticEdgeIntentRecord):
            raise TypeError("A semantic intent is required.")
        row = connection.execute(
            """SELECT item.space_id,item.id AS item_id,
                      item.current_revision_id AS revision_id,item.row_version,
                      revision.title,revision.normalized_content_sha256,
                      search.content_md
                 FROM knowledge_item AS item
                 JOIN knowledge_space AS space ON space.id=item.space_id
                 JOIN knowledge_revision AS revision
                   ON revision.id=item.current_revision_id
                  AND revision.item_id=item.id
                 JOIN search_document AS search
                   ON search.item_id=item.id AND search.space_id=item.space_id
                  AND search.revision_id=revision.id
                WHERE item.id=? AND item.space_id=?
                  AND item.current_revision_id=?
                  AND item.lifecycle_status='active'
                  AND item.curation_status='published'
                  AND space.status='active'
                  AND revision.normalized_content_sha256=?""",
            (
                intent.source_item_id,
                intent.source_space_id,
                intent.source_revision_id,
                intent.source_body_sha256,
            ),
        ).fetchone()
        if row is None:
            return None
        return SemanticSourceDocument(
            space_id=str(row["space_id"]),
            item_id=str(row["item_id"]),
            revision_id=str(row["revision_id"]),
            item_row_version=int(row["row_version"]),
            title=str(row["title"]),
            content_md=str(row["content_md"]),
            normalized_content_sha256=str(row["normalized_content_sha256"]),
        )

    def retrieve_candidates(
        self,
        connection: ManagedConnection,
        *,
        intent: SemanticEdgeIntentRecord,
        fts_query: str,
    ) -> tuple[SemanticCandidateDocument, ...]:
        """Return at most 20 lexical plus 20 one-hop candidates.

        Exact local/LCA eligibility is applied before both limits, then
        independently rechecked by the service and immediately before commit.
        """

        connection = _connection(connection)
        if not isinstance(intent, SemanticEdgeIntentRecord):
            raise TypeError("A semantic intent is required.")
        if not isinstance(fts_query, str) or not fts_query:
            raise ValueError("fts_query must be nonblank text.")
        if intent.target_kind is SemanticIntentTargetKind.LOCAL:
            scope_sql = "candidate.space_id=? AND candidate.space_id=?"
            scope_parameters: tuple[object, ...] = (intent.source_space_id, intent.target_space_id)
        else:
            # Compute eligible SPACE ids, not a correlated LCA for each matching
            # document. Preserve the old active-descendant boundary, and mirror
            # resolve_placement's nearest active native home within that scope.
            # Ancestor traversal must not prune inactive intermediate units.
            scope_sql = """candidate.space_id IN (
              WITH RECURSIVE
              descendants(id) AS (
                SELECT owner_org_unit_id FROM knowledge_space WHERE id=?
                UNION ALL
                SELECT child.id FROM org_unit AS child JOIN descendants
                  ON child.parent_id=descendants.id WHERE child.status='active'
              ),
              source_space(id,org_id) AS (
                SELECT id,owner_org_unit_id FROM knowledge_space
                 WHERE id=? AND status='active' AND space_kind<>'personal'
              ),
              source_ancestors(id,depth) AS (
                SELECT org_id,0 FROM source_space
                UNION ALL
                SELECT unit.parent_id,source_ancestors.depth+1
                  FROM org_unit AS unit JOIN source_ancestors ON unit.id=source_ancestors.id
                 WHERE unit.parent_id IS NOT NULL
              ),
              candidate_ancestors(space_id,id,depth) AS (
                SELECT space.id,space.owner_org_unit_id,0
                  FROM knowledge_space AS space JOIN descendants
                    ON descendants.id=space.owner_org_unit_id
                  CROSS JOIN source_space
                 WHERE space.status='active' AND space.space_kind<>'personal'
                   AND space.id<>source_space.id
                UNION ALL
                SELECT ancestor.space_id,unit.parent_id,ancestor.depth+1
                  FROM org_unit AS unit JOIN candidate_ancestors AS ancestor ON unit.id=ancestor.id
                 WHERE unit.parent_id IS NOT NULL
              ),
              homes(space_id,home_id,priority) AS (
                SELECT target.space_id,home.id,
                       row_number() OVER (PARTITION BY target.space_id
                         ORDER BY source.depth+target.depth,source.id)
                  FROM source_ancestors AS source JOIN candidate_ancestors AS target ON target.id=source.id
                  JOIN org_unit AS unit ON unit.id=source.id
                  JOIN knowledge_space AS home
                    ON home.owner_org_unit_id=unit.id AND home.space_kind=unit.kind
                 WHERE unit.status='active' AND unit.kind IN ('team','responsibility')
                   AND home.status='active'
              )
              SELECT space_id FROM homes WHERE priority=1 AND home_id=?
            )"""
            scope_parameters = (intent.target_space_id, intent.source_space_id, intent.target_space_id)
        lexical = connection.execute(
            f"""SELECT candidate.space_id,candidate.item_id,
                        candidate.revision_id,item.row_version,candidate.title,
                        candidate.content_md,revision.normalized_content_sha256,
                        bm25(knowledge_fts,6.0,1.0) AS score
                   FROM knowledge_fts
                   JOIN search_document AS candidate
                     ON candidate.rowid=knowledge_fts.rowid
                   JOIN knowledge_item AS item
                     ON item.id=candidate.item_id AND item.space_id=candidate.space_id
                   JOIN knowledge_revision AS revision
                     ON revision.id=candidate.revision_id
                    AND revision.item_id=candidate.item_id
                   JOIN knowledge_space AS space ON space.id=candidate.space_id
                  WHERE knowledge_fts MATCH ? AND {scope_sql}
                    AND candidate.item_id<>?
                    AND item.current_revision_id=candidate.revision_id
                    AND item.lifecycle_status='active'
                    AND item.curation_status='published'
                    AND space.status='active'
                  ORDER BY score,candidate.item_id LIMIT ?""",
            (fts_query, *scope_parameters, intent.source_item_id, SEMANTIC_LEXICAL_CANDIDATE_LIMIT),
        ).fetchall()
        by_item: dict[str, SemanticCandidateDocument] = {}
        for ordinal, row in enumerate(lexical, start=1):
            value = SemanticCandidateDocument(
                space_id=str(row["space_id"]),
                item_id=str(row["item_id"]),
                revision_id=str(row["revision_id"]),
                item_row_version=int(row["row_version"]),
                title=str(row["title"]),
                content_md=str(row["content_md"]),
                normalized_content_sha256=str(row["normalized_content_sha256"]),
                lexical_ordinal=ordinal,
                graph_neighbor=False,
            )
            by_item[value.item_id] = value
        seed_ids = tuple(value.item_id for value in tuple(by_item.values())[:6])
        if seed_ids:
            seed_json = json.dumps(seed_ids, separators=(",", ":"))
            neighbor_rows = connection.execute(
                f"""WITH seed(id) AS (SELECT value FROM json_each(?)),
                    pairs(space_id,item_id) AS (
                      SELECT edge.space_id,
                             CASE WHEN edge.source_item_id=seed.id
                                  THEN edge.target_item_id ELSE edge.source_item_id END
                        FROM knowledge_edge AS edge JOIN seed
                          ON seed.id IN (edge.source_item_id,edge.target_item_id)
                       WHERE edge.edge_status='active' AND edge.needs_revalidation=0
                      UNION
                      SELECT CASE WHEN edge.source_item_id=seed.id
                                  THEN edge.target_space_id ELSE edge.source_space_id END,
                             CASE WHEN edge.source_item_id=seed.id
                                  THEN edge.target_item_id ELSE edge.source_item_id END
                        FROM aggregate_knowledge_edge AS edge JOIN seed
                          ON seed.id IN (edge.source_item_id,edge.target_item_id)
                       WHERE edge.edge_status='active'
                    )
                    SELECT candidate.space_id,candidate.item_id,candidate.revision_id,
                           item.row_version,candidate.title,candidate.content_md,
                           revision.normalized_content_sha256
                      FROM pairs JOIN search_document AS candidate
                        ON candidate.space_id=pairs.space_id
                       AND candidate.item_id=pairs.item_id
                      JOIN knowledge_item AS item
                        ON item.id=candidate.item_id AND item.space_id=candidate.space_id
                      JOIN knowledge_revision AS revision
                        ON revision.id=candidate.revision_id
                       AND revision.item_id=candidate.item_id
                      JOIN knowledge_space AS space ON space.id=candidate.space_id
                     WHERE {scope_sql} AND candidate.item_id<>?
                       AND item.current_revision_id=candidate.revision_id
                       AND item.lifecycle_status='active'
                       AND item.curation_status='published' AND space.status='active'
                     ORDER BY candidate.item_id LIMIT ?""",
                (seed_json, *scope_parameters, intent.source_item_id, SEMANTIC_GRAPH_NEIGHBOR_LIMIT),
            ).fetchall()
            for row in neighbor_rows:
                item_id = str(row["item_id"])
                existing = by_item.get(item_id)
                if existing is not None:
                    by_item[item_id] = SemanticCandidateDocument(
                        existing.space_id,
                        existing.item_id,
                        existing.revision_id,
                        existing.item_row_version,
                        existing.title,
                        existing.content_md,
                        existing.normalized_content_sha256,
                        existing.lexical_ordinal,
                        True,
                    )
                    continue
                by_item[item_id] = SemanticCandidateDocument(
                    space_id=str(row["space_id"]),
                    item_id=item_id,
                    revision_id=str(row["revision_id"]),
                    item_row_version=int(row["row_version"]),
                    title=str(row["title"]),
                    content_md=str(row["content_md"]),
                    normalized_content_sha256=str(row["normalized_content_sha256"]),
                    lexical_ordinal=None,
                    graph_neighbor=True,
                )
        return tuple(by_item.values())


__all__ = [
    "AutomaticSemanticEdgeRepository",
    "AutomaticSemanticEdgeRepositoryError",
    "SEMANTIC_GRAPH_NEIGHBOR_LIMIT",
    "SEMANTIC_LEXICAL_CANDIDATE_LIMIT",
    "SEMANTIC_MODEL_CANDIDATE_LIMIT",
    "SEMANTIC_SEED_BATCH_LIMIT",
    "SemanticCandidateDocument",
    "SemanticIntentTarget",
    "SemanticSeedSource",
    "SemanticSourceDocument",
]

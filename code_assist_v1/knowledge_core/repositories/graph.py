"""Exact P2.4 legacy graph-edge and pending-finding persistence.

The repository accepts only already reviewed, hash-only evidence.  It performs
no source I/O and owns no transaction; callers must compose the domain row,
immutable migration checkpoint, audit, and outbox in one managed transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from typing import Literal
from uuid import UUID, uuid5

from ..db import ManagedConnection
from ..errors import KnowledgeCoreError, KnowledgeCoreTransactionError


LegacyCanonicalEdgeType = Literal["contradicts", "extends", "related", "supports"]
LegacyCandidateKind = Literal["graph_candidate", "wiki_diff"]

_EDGE_NAMESPACE = UUID("e8f0791a-4754-40da-830d-2a3d7852b4df")
_FINDING_NAMESPACE = UUID("e448c08a-a731-49ea-a193-dc5bd37eae65")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RECORD_RE = re.compile(r"^rec_[0-9a-f]{24}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_SQLITE_INTEGER_MAX = 9_223_372_036_854_775_807


class LegacyGraphRepositoryError(KnowledgeCoreError):
    code = "LEGACY_GRAPH_REPOSITORY_ERROR"


class LegacyGraphConflictError(LegacyGraphRepositoryError):
    code = "LEGACY_GRAPH_STATE_CONFLICT"


@dataclass(frozen=True, slots=True)
class LegacyGraphEdge:
    plan_sha256: str
    inventory_record_id: str
    edge_id: str
    owner_user_id: str
    space_id: str
    source_item_id: str
    source_revision_id: str
    target_item_id: str
    target_revision_id: str
    edge_type: LegacyCanonicalEdgeType
    evidence_json: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class LegacyGraphEdgeResult:
    edge_id: str
    inserted: bool


@dataclass(frozen=True, slots=True)
class LegacyCandidateFinding:
    plan_sha256: str
    inventory_record_id: str
    finding_id: str
    owner_user_id: str
    space_id: str
    source_item_id: str
    source_revision_id: str
    target_item_id: str
    target_revision_id: str
    candidate_kind: LegacyCandidateKind
    evidence_json: str
    proposed_action_json: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class LegacyCandidateFindingResult:
    finding_id: str
    inserted: bool


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 value.")
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _record(value: object) -> str:
    if not isinstance(value, str) or _RECORD_RE.fullmatch(value) is None:
        raise ValueError("inventory_record_id is invalid.")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("owner_user_id is invalid.")
    return value


def _utc_ms(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _SQLITE_INTEGER_MAX:
        raise ValueError("created_at_ms is invalid.")
    return value


def _canonical_object(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be canonical JSON.")
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        raise ValueError(f"{field} must be canonical JSON.") from None
    if not isinstance(parsed, dict):
        raise ValueError(f"{field} must be a JSON object.")
    canonical = json.dumps(
        parsed,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if value != canonical:
        raise ValueError(f"{field} must be canonical JSON.")
    return value


def legacy_graph_edge_id(plan_sha256: str, inventory_record_id: str) -> str:
    return str(uuid5(_EDGE_NAMESPACE, f"p2.4:{_sha(plan_sha256, 'plan_sha256')}:{_record(inventory_record_id)}"))


def legacy_candidate_finding_id(plan_sha256: str, inventory_record_id: str) -> str:
    return str(uuid5(_FINDING_NAMESPACE, f"p2.4:{_sha(plan_sha256, 'plan_sha256')}:{_record(inventory_record_id)}"))


def _row_tuple(row: object, names: tuple[str, ...]) -> tuple[object, ...]:
    return tuple(row[name] for name in names)  # type: ignore[index]


class GraphRepository:
    """Insert-or-exact-verify reviewed legacy graph and finding effects."""

    __slots__ = ()

    @staticmethod
    def _connection(connection: ManagedConnection) -> ManagedConnection:
        if not isinstance(connection, ManagedConnection):
            raise TypeError("GraphRepository requires a ManagedConnection.")
        if not connection.in_transaction:
            raise KnowledgeCoreTransactionError(
                "Graph repository writes require an existing managed transaction."
            )
        return connection

    @staticmethod
    def _verify_endpoints(
        connection: ManagedConnection,
        *,
        owner: str,
        space_id: str,
        source_item_id: str,
        source_revision_id: str,
        target_item_id: str,
        target_revision_id: str,
    ) -> None:
        personal = connection.execute(
            """SELECT 1 FROM knowledge_space
               WHERE id=? AND space_kind='personal' AND owner_user_id=?""",
            (space_id, owner),
        ).fetchone()
        endpoints = connection.execute(
            """SELECT item.id,item.space_id,item.lifecycle_status,revision.id AS revision_id
               FROM knowledge_item AS item
               JOIN knowledge_revision AS revision ON revision.item_id=item.id
               WHERE (item.id=? AND revision.id=?) OR (item.id=? AND revision.id=?)
               ORDER BY item.id,revision.id""",
            (
                source_item_id,
                source_revision_id,
                target_item_id,
                target_revision_id,
            ),
        ).fetchall()
        observed = {(row["id"], row["revision_id"], row["space_id"], row["lifecycle_status"]) for row in endpoints}
        expected = {
            (source_item_id, source_revision_id, space_id, "active"),
            (target_item_id, target_revision_id, space_id, "active"),
        }
        if personal is None or source_item_id == target_item_id or observed != expected:
            raise LegacyGraphConflictError(
                "Legacy graph endpoints are not two active items in one personal space."
            )

    def put_legacy_edge(
        self, connection: ManagedConnection, value: LegacyGraphEdge
    ) -> LegacyGraphEdgeResult:
        connection = self._connection(connection)
        if not isinstance(value, LegacyGraphEdge):
            raise TypeError("A LegacyGraphEdge is required.")
        plan = _sha(value.plan_sha256, "plan_sha256")
        record_id = _record(value.inventory_record_id)
        expected_id = legacy_graph_edge_id(plan, record_id)
        if _uuid(value.edge_id, "edge_id") != expected_id:
            raise ValueError("edge_id does not match the reviewed record.")
        owner = _user(value.owner_user_id)
        space = _uuid(value.space_id, "space_id")
        source_item = _uuid(value.source_item_id, "source_item_id")
        source_revision = _uuid(value.source_revision_id, "source_revision_id")
        target_item = _uuid(value.target_item_id, "target_item_id")
        target_revision = _uuid(value.target_revision_id, "target_revision_id")
        if value.edge_type not in {"contradicts", "extends", "related", "supports"}:
            raise ValueError("edge_type is not a canonical reviewed legacy relation.")
        evidence = _canonical_object(value.evidence_json, "evidence_json")
        created = _utc_ms(value.created_at_ms)
        self._verify_endpoints(
            connection,
            owner=owner,
            space_id=space,
            source_item_id=source_item,
            source_revision_id=source_revision,
            target_item_id=target_item,
            target_revision_id=target_revision,
        )
        names = (
            "id", "space_id", "source_item_id", "target_item_id", "edge_type",
            "edge_status", "origin", "source_evidence_revision_id",
            "target_evidence_revision_id", "approval_finding_id", "evidence_json",
            "needs_revalidation", "created_by", "updated_by", "created_at",
            "updated_at", "row_version",
        )
        expected = (
            expected_id, space, source_item, target_item, value.edge_type, "active",
            "manual", source_revision, target_revision, None, evidence, 0, owner,
            owner, created, created, 1,
        )
        by_id = connection.execute(
            f"SELECT {','.join(names)} FROM knowledge_edge WHERE id=?", (expected_id,)
        ).fetchone()
        by_identity = connection.execute(
            """SELECT id FROM knowledge_edge
               WHERE space_id=? AND source_item_id=? AND target_item_id=?
                 AND edge_type=? AND edge_status='active'""",
            (space, source_item, target_item, value.edge_type),
        ).fetchone()
        if by_id is not None:
            if _row_tuple(by_id, names) != expected or by_identity is None or by_identity["id"] != expected_id:
                raise LegacyGraphConflictError("The legacy graph edge is already bound incompatibly.")
            return LegacyGraphEdgeResult(expected_id, False)
        if by_identity is not None:
            raise LegacyGraphConflictError("The active graph-edge identity is already occupied.")
        try:
            connection.execute(
                """INSERT INTO knowledge_edge(
                     id,space_id,source_item_id,target_item_id,edge_type,edge_status,
                     origin,source_evidence_revision_id,target_evidence_revision_id,
                     approval_finding_id,evidence_json,needs_revalidation,
                     created_by,updated_by,created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,?,'active','manual',?,?,NULL,?,0,?,?,?,?,1)""",
                (
                    expected_id, space, source_item, target_item, value.edge_type,
                    source_revision, target_revision, evidence, owner, owner, created,
                    created,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyGraphConflictError("The legacy graph edge violates canonical constraints.") from exc
        return LegacyGraphEdgeResult(expected_id, True)

    def put_legacy_candidate(
        self, connection: ManagedConnection, value: LegacyCandidateFinding
    ) -> LegacyCandidateFindingResult:
        connection = self._connection(connection)
        if not isinstance(value, LegacyCandidateFinding):
            raise TypeError("A LegacyCandidateFinding is required.")
        plan = _sha(value.plan_sha256, "plan_sha256")
        record_id = _record(value.inventory_record_id)
        expected_id = legacy_candidate_finding_id(plan, record_id)
        if _uuid(value.finding_id, "finding_id") != expected_id:
            raise ValueError("finding_id does not match the reviewed record.")
        owner = _user(value.owner_user_id)
        space = _uuid(value.space_id, "space_id")
        source_item = _uuid(value.source_item_id, "source_item_id")
        source_revision = _uuid(value.source_revision_id, "source_revision_id")
        target_item = _uuid(value.target_item_id, "target_item_id")
        target_revision = _uuid(value.target_revision_id, "target_revision_id")
        if value.candidate_kind == "graph_candidate":
            policy = ("semantic_edge", "info", "none", "relation")
        elif value.candidate_kind == "wiki_diff":
            policy = ("near_duplicate", "warning", "agent_hidden", "review_duplicate")
        else:
            raise ValueError("candidate_kind is not a frozen pending-candidate kind.")
        evidence = _canonical_object(value.evidence_json, "evidence_json")
        proposed = _canonical_object(value.proposed_action_json, "proposed_action_json")
        created = _utc_ms(value.created_at_ms)
        self._verify_endpoints(
            connection,
            owner=owner,
            space_id=space,
            source_item_id=source_item,
            source_revision_id=source_revision,
            target_item_id=target_item,
            target_revision_id=target_revision,
        )
        kind, severity, visibility, action = policy
        names = (
            "id", "space_id", "finding_kind", "risk_tier", "severity", "status",
            "decision_mode", "required_reviewer", "visibility_effect",
            "proposed_action", "source_item_id", "source_revision_id",
            "target_item_id", "target_revision_id", "detector_name",
            "detector_version", "rule_version", "model_id", "prompt_version",
            "confidence", "evidence_json", "proposed_action_json", "reviewed_by",
            "reviewed_at", "review_reason", "applied_operation_id",
            "applied_dedup_id", "applied_job_id", "applied_by", "applied_at",
            "last_application_error_json", "created_actor_kind", "created_by",
            "created_at", "updated_at", "row_version",
        )
        expected = (
            expected_id, space, kind, "T2", severity, "pending", "human",
            "space_editor", visibility, action, source_item, source_revision,
            target_item, target_revision, "legacy_migration", "p2.4-v1", "p2.4-v1",
            None, None, None, evidence, proposed, None, None, None, None, None,
            None, None, None, None, "system", None, created, created, 1,
        )
        by_id = connection.execute(
            f"SELECT {','.join(names)} FROM curation_finding WHERE id=?", (expected_id,)
        ).fetchone()
        if by_id is not None:
            if _row_tuple(by_id, names) != expected:
                raise LegacyGraphConflictError("The legacy candidate finding is already bound incompatibly.")
            return LegacyCandidateFindingResult(expected_id, False)
        try:
            connection.execute(
                """INSERT INTO curation_finding(
                     id,space_id,finding_kind,risk_tier,severity,status,decision_mode,
                     required_reviewer,visibility_effect,proposed_action,
                     source_item_id,source_revision_id,target_item_id,target_revision_id,
                     detector_name,detector_version,rule_version,model_id,prompt_version,
                     confidence,evidence_json,proposed_action_json,reviewed_by,
                     reviewed_at,review_reason,applied_operation_id,applied_dedup_id,
                     applied_job_id,applied_by,applied_at,last_application_error_json,
                     created_actor_kind,created_by,created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,?,'pending','human','space_editor',?,?,?,?,?,?,
                     'legacy_migration','p2.4-v1','p2.4-v1',NULL,NULL,NULL,?,?,NULL,
                     NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'system',NULL,?,?,1)""",
                (
                    expected_id, space, kind, "T2", severity, visibility, action,
                    source_item, source_revision, target_item, target_revision,
                    evidence, proposed, created, created,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyGraphConflictError("The legacy candidate violates canonical constraints.") from exc
        return LegacyCandidateFindingResult(expected_id, True)


__all__ = [
    "GraphRepository",
    "LegacyCandidateFinding",
    "LegacyCandidateFindingResult",
    "LegacyGraphConflictError",
    "LegacyGraphEdge",
    "LegacyGraphEdgeResult",
    "LegacyGraphRepositoryError",
    "legacy_candidate_finding_id",
    "legacy_graph_edge_id",
]

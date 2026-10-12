"""Reviewed P6.4 semantic-edge application primitives.

The repository owns no transaction and performs no model work.  It consumes one
already-approved, immutable semantic finding plus the caller operation reserved
by the service, revalidates exact current published endpoints, and writes the
finding application proof and one semantic edge in the same caller transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
from typing import Literal
from uuid import UUID, uuid5

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


SemanticEdgeType = Literal[
    "related", "supports", "contradicts", "depends_on", "extends", "example_of"
]
SemanticEdgeApplyOutcome = Literal["applied", "obsolete"]

SEMANTIC_EDGE_EVIDENCE_SCHEMA_VERSION = 1
SEMANTIC_EDGE_RULE_VERSION = "p6.4-reviewed-semantic-edge-v1"

_EDGE_NAMESPACE = UUID("48351588-58a8-4680-8f1a-79df2b836441")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_EDGE_TYPES = {
    "related",
    "supports",
    "contradicts",
    "depends_on",
    "extends",
    "example_of",
}
_MAX_INTEGER = 9_223_372_036_854_775_807


class SemanticEdgeRepositoryError(KnowledgeCoreDatabaseError):
    code = "SEMANTIC_EDGE_REPOSITORY_ERROR"


class SemanticEdgeConflictError(SemanticEdgeRepositoryError):
    code = "SEMANTIC_EDGE_CONFLICT"


class SemanticEdgeIntegrityError(SemanticEdgeRepositoryError):
    code = "SEMANTIC_EDGE_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class SemanticEdgeRecord:
    id: str
    space_id: str
    source_item_id: str
    target_item_id: str
    edge_type: SemanticEdgeType
    edge_status: str
    origin: str
    source_evidence_revision_id: str
    target_evidence_revision_id: str
    approval_finding_id: str
    evidence_json: str
    needs_revalidation: bool
    created_by: str
    updated_by: str
    created_at_ms: int
    updated_at_ms: int
    row_version: int

    @property
    def evidence(self) -> dict[str, object]:
        return _canonical_object(self.evidence_json, "stored edge evidence")


@dataclass(frozen=True, slots=True)
class SemanticEdgeApplyDraft:
    finding_id: str
    expected_finding_row_version: int
    space_id: str
    source_revision_id: str
    target_revision_id: str
    evidence_sha256: str
    actor_user_id: str
    operation_id: str
    dedup_id: str
    reason: str
    now_ms: int


@dataclass(frozen=True, slots=True)
class SemanticEdgeApplyWriteResult:
    outcome: SemanticEdgeApplyOutcome
    finding_id: str
    finding_row_version: int
    edge: SemanticEdgeRecord | None
    space_generation: int


def semantic_edge_id_for_finding(finding_id: str) -> str:
    return str(uuid5(_EDGE_NAMESPACE, _uuid(finding_id, "finding_id")))


def _transaction(value: object) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError("SemanticEdgeRepository requires a ManagedConnection.")
    if not value.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Semantic edge writes require one caller-owned transaction."
        )
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 value.")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("actor_user_id is invalid.")
    return value


def _positive(value: object, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= _MAX_INTEGER
    ):
        raise ValueError(f"{field} is invalid.")
    return value


def _timestamp(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_INTEGER
    ):
        raise ValueError("now_ms is invalid.")
    return value


def _reason(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not 1 <= len(value) <= 2_000:
        raise ValueError("reason is invalid.")
    return value


def _canonical_object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, str):
        raise SemanticEdgeIntegrityError(f"{field} is not canonical JSON.")
    try:
        parsed = json.loads(value)
        canonical = json.dumps(
            parsed,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
        raise SemanticEdgeIntegrityError(f"{field} is not canonical JSON.") from exc
    if type(parsed) is not dict or canonical != value:
        raise SemanticEdgeIntegrityError(f"{field} is not a canonical object.")
    return parsed


def _canonical_json(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _row_value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise SemanticEdgeIntegrityError("A semantic edge row cannot be mapped.") from exc


_EDGE_COLUMNS = """id,space_id,source_item_id,target_item_id,edge_type,edge_status,origin,
source_evidence_revision_id,target_evidence_revision_id,approval_finding_id,
evidence_json,needs_revalidation,created_by,updated_by,created_at,updated_at,row_version"""


def _edge_record(row: object) -> SemanticEdgeRecord:
    edge_type = str(_row_value(row, "edge_type"))
    if edge_type not in _EDGE_TYPES:
        raise SemanticEdgeIntegrityError("Stored semantic edge type is invalid.")
    record = SemanticEdgeRecord(
        id=_uuid(_row_value(row, "id"), "stored edge id"),
        space_id=_uuid(_row_value(row, "space_id"), "stored edge space"),
        source_item_id=_uuid(_row_value(row, "source_item_id"), "stored source item"),
        target_item_id=_uuid(_row_value(row, "target_item_id"), "stored target item"),
        edge_type=edge_type,  # type: ignore[arg-type]
        edge_status=str(_row_value(row, "edge_status")),
        origin=str(_row_value(row, "origin")),
        source_evidence_revision_id=_uuid(
            _row_value(row, "source_evidence_revision_id"), "stored source revision"
        ),
        target_evidence_revision_id=_uuid(
            _row_value(row, "target_evidence_revision_id"), "stored target revision"
        ),
        approval_finding_id=_uuid(
            _row_value(row, "approval_finding_id"), "stored approval finding"
        ),
        evidence_json=str(_row_value(row, "evidence_json")),
        needs_revalidation=bool(_row_value(row, "needs_revalidation")),
        created_by=_user(_row_value(row, "created_by")),
        updated_by=_user(_row_value(row, "updated_by")),
        created_at_ms=int(_row_value(row, "created_at")),
        updated_at_ms=int(_row_value(row, "updated_at")),
        row_version=int(_row_value(row, "row_version")),
    )
    evidence = _canonical_object(record.evidence_json, "stored edge evidence")
    if set(evidence) != {
        "finding_evidence",
        "finding_evidence_sha256",
        "finding_id",
        "finding_input_row_version",
        "rule_version",
        "schema_version",
    }:
        raise SemanticEdgeIntegrityError("Stored semantic edge evidence shape is invalid.")
    finding_evidence = evidence.get("finding_evidence")
    finding_evidence_sha = evidence.get("finding_evidence_sha256")
    if (
        type(finding_evidence) is not dict
        or not isinstance(finding_evidence_sha, str)
        or _SHA_RE.fullmatch(finding_evidence_sha) is None
        or hashlib.sha256(_canonical_json(finding_evidence).encode("ascii")).hexdigest()
        != finding_evidence_sha
        or evidence.get("finding_id") != record.approval_finding_id
        or evidence.get("schema_version") != SEMANTIC_EDGE_EVIDENCE_SCHEMA_VERSION
        or evidence.get("rule_version") != SEMANTIC_EDGE_RULE_VERSION
        or isinstance(evidence.get("finding_input_row_version"), bool)
        or not isinstance(evidence.get("finding_input_row_version"), int)
        or int(evidence["finding_input_row_version"]) < 1
    ):
        raise SemanticEdgeIntegrityError("Stored semantic edge evidence is invalid.")
    if (
        record.edge_status not in {"active", "inactive"}
        or record.origin != "semantic_approved"
        or type(_row_value(row, "needs_revalidation")) is not int
        or _row_value(row, "needs_revalidation") not in {0, 1}
        or record.created_at_ms < 0
        or record.updated_at_ms < 0
        or record.row_version < 1
    ):
        raise SemanticEdgeIntegrityError("Stored semantic edge shape is invalid.")
    return record


class SemanticEdgeRepository:
    __slots__ = ()

    def get(self, connection: ManagedConnection, edge_id: str) -> SemanticEdgeRecord | None:
        if not isinstance(connection, ManagedConnection):
            raise TypeError("SemanticEdgeRepository requires a ManagedConnection.")
        edge_id = _uuid(edge_id, "edge_id")
        row = connection.execute(
            f"SELECT {_EDGE_COLUMNS} FROM knowledge_edge WHERE id=?", (edge_id,)
        ).fetchone()
        return None if row is None else _edge_record(row)

    def apply_approved(
        self,
        connection: ManagedConnection,
        draft: SemanticEdgeApplyDraft,
        *,
        failpoint=None,
    ) -> SemanticEdgeApplyWriteResult:
        connection = _transaction(connection)
        if not isinstance(draft, SemanticEdgeApplyDraft):
            raise TypeError("A SemanticEdgeApplyDraft is required.")
        finding_id = _uuid(draft.finding_id, "finding_id")
        expected_version = _positive(
            draft.expected_finding_row_version, "expected finding row version"
        )
        space_id = _uuid(draft.space_id, "space_id")
        source_revision_id = _uuid(draft.source_revision_id, "source_revision_id")
        target_revision_id = _uuid(draft.target_revision_id, "target_revision_id")
        evidence_sha = _sha(draft.evidence_sha256, "evidence_sha256")
        actor = _user(draft.actor_user_id)
        operation_id = _uuid(draft.operation_id, "operation_id")
        dedup_id = _uuid(draft.dedup_id, "dedup_id")
        reason = _reason(draft.reason)
        now_ms = _timestamp(draft.now_ms)

        finding = connection.execute(
            """SELECT * FROM curation_finding WHERE id=?""", (finding_id,)
        ).fetchone()
        if finding is None:
            raise SemanticEdgeConflictError("The semantic finding does not exist.")
        if (
            _row_value(finding, "finding_kind") != "semantic_edge"
            or _row_value(finding, "risk_tier") != "T2"
            or _row_value(finding, "severity") != "info"
            or _row_value(finding, "decision_mode") != "human"
            or _row_value(finding, "required_reviewer") != "space_editor"
            or _row_value(finding, "visibility_effect") != "none"
            or _row_value(finding, "proposed_action") != "relation"
            or _row_value(finding, "status") != "approved"
            or int(_row_value(finding, "row_version")) != expected_version
            or _row_value(finding, "space_id") != space_id
            or _row_value(finding, "source_revision_id") != source_revision_id
            or _row_value(finding, "target_revision_id") != target_revision_id
            or _row_value(finding, "reviewed_by") != actor
            or _row_value(finding, "reviewed_at") is None
            or _row_value(finding, "review_reason") is None
        ):
            raise SemanticEdgeConflictError(
                "The approved semantic finding changed or is not applicable."
            )
        source_item_id = _uuid(_row_value(finding, "source_item_id"), "source item")
        target_item_id = _uuid(_row_value(finding, "target_item_id"), "target item")
        evidence_json = str(_row_value(finding, "evidence_json"))
        evidence = _canonical_object(evidence_json, "finding evidence")
        if hashlib.sha256(evidence_json.encode("ascii")).hexdigest() != evidence_sha:
            raise SemanticEdgeConflictError("The approved evidence hash changed.")
        action = _canonical_object(
            str(_row_value(finding, "proposed_action_json")), "finding action"
        )
        if set(action) != {
            "action",
            "relation_type",
            "schema_version",
            "source_revision_id",
            "target_revision_id",
        } or (
            action.get("action") != "relation"
            or action.get("schema_version") != 1
            or action.get("source_revision_id") != source_revision_id
            or action.get("target_revision_id") != target_revision_id
            or action.get("relation_type") not in _EDGE_TYPES
        ):
            raise SemanticEdgeIntegrityError(
                "The semantic finding action evidence is invalid."
            )
        edge_type = str(action["relation_type"])
        operation = connection.execute(
            """SELECT actor_user_id,operation_type,operation_id,status
                 FROM operation_dedup WHERE id=?""",
            (dedup_id,),
        ).fetchone()
        if operation is None or tuple(operation) != (
            actor,
            "semantic_edge",
            operation_id,
            "in_progress",
        ):
            raise SemanticEdgeIntegrityError(
                "The semantic-edge operation reservation is invalid."
            )
        items = connection.execute(
            """SELECT id,space_id,current_revision_id,lifecycle_status,curation_status
                 FROM knowledge_item WHERE id IN (?,?) ORDER BY id""",
            (source_item_id, target_item_id),
        ).fetchall()
        states = {
            str(_row_value(row, "id")): (
                str(_row_value(row, "space_id")),
                str(_row_value(row, "current_revision_id")),
                str(_row_value(row, "lifecycle_status")),
                str(_row_value(row, "curation_status")),
            )
            for row in items
        }
        expected_states = {
            source_item_id: (space_id, source_revision_id, "active", "published"),
            target_item_id: (space_id, target_revision_id, "active", "published"),
        }
        generation_row = connection.execute(
            """SELECT generation FROM knowledge_space
                 WHERE id=? AND status='active' AND write_enabled=1""",
            (space_id,),
        ).fetchone()
        if generation_row is None:
            raise SemanticEdgeConflictError("The semantic-edge space is not writable.")
        generation = int(generation_row[0])
        if states != expected_states:
            cursor = connection.execute(
                """UPDATE curation_finding
                      SET status='obsolete',updated_at=?,row_version=row_version+1
                    WHERE id=? AND status='approved' AND row_version=?""",
                (now_ms, finding_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise SemanticEdgeConflictError(
                    "The semantic finding changed before obsoletion."
                )
            if failpoint is not None:
                failpoint("semantic_edge.after_obsolete")
            return SemanticEdgeApplyWriteResult(
                "obsolete", finding_id, expected_version + 1, None, generation
            )

        edge_id = semantic_edge_id_for_finding(finding_id)
        collision = connection.execute(
            """SELECT id FROM knowledge_edge
                 WHERE space_id=? AND source_item_id=? AND target_item_id=?
                   AND edge_type=? AND edge_status='active'""",
            (space_id, source_item_id, target_item_id, edge_type),
        ).fetchone()
        if collision is not None or self.get(connection, edge_id) is not None:
            raise SemanticEdgeConflictError(
                "The active semantic-edge identity is already occupied."
            )
        edge_evidence_json = _canonical_json(
            {
                "finding_evidence": evidence,
                "finding_evidence_sha256": evidence_sha,
                "finding_id": finding_id,
                "finding_input_row_version": expected_version,
                "rule_version": SEMANTIC_EDGE_RULE_VERSION,
                "schema_version": SEMANTIC_EDGE_EVIDENCE_SCHEMA_VERSION,
            }
        )
        try:
            cursor = connection.execute(
                """UPDATE curation_finding
                      SET status='applied',applied_operation_id=?,applied_dedup_id=?,
                          applied_job_id=NULL,applied_by=?,applied_at=?,
                          last_application_error_json=NULL,updated_at=?,
                          row_version=row_version+1
                    WHERE id=? AND status='approved' AND row_version=?""",
                (
                    operation_id,
                    dedup_id,
                    actor,
                    now_ms,
                    now_ms,
                    finding_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise SemanticEdgeConflictError(
                    "The semantic finding changed before application."
                )
            if failpoint is not None:
                failpoint("semantic_edge.after_finding_apply")
            connection.execute(
                """INSERT INTO knowledge_edge(
                     id,space_id,source_item_id,target_item_id,edge_type,edge_status,
                     origin,source_evidence_revision_id,target_evidence_revision_id,
                     approval_finding_id,evidence_json,needs_revalidation,created_by,
                     updated_by,created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,?,'active','semantic_approved',?,?,?,?,0,?,?,?,?,1)""",
                (
                    edge_id,
                    space_id,
                    source_item_id,
                    target_item_id,
                    edge_type,
                    source_revision_id,
                    target_revision_id,
                    finding_id,
                    edge_evidence_json,
                    actor,
                    actor,
                    now_ms,
                    now_ms,
                ),
            )
            if failpoint is not None:
                failpoint("semantic_edge.after_edge_insert")
            generation_cursor = connection.execute(
                """UPDATE knowledge_space
                      SET generation=generation+1,updated_at=?,row_version=row_version+1
                    WHERE id=? AND status='active' AND write_enabled=1
                      AND generation=?""",
                (now_ms, space_id, generation),
            )
            if generation_cursor.rowcount != 1:
                raise SemanticEdgeConflictError(
                    "The semantic-edge space changed before commit."
                )
        except sqlite3.IntegrityError as exc:
            raise SemanticEdgeIntegrityError(
                "The reviewed semantic edge violates canonical constraints."
            ) from exc
        edge = self.get(connection, edge_id)
        applied = connection.execute(
            """SELECT status,row_version FROM curation_finding WHERE id=?""",
            (finding_id,),
        ).fetchone()
        if (
            edge is None
            or edge.edge_status != "active"
            or edge.approval_finding_id != finding_id
            or edge.source_evidence_revision_id != source_revision_id
            or edge.target_evidence_revision_id != target_revision_id
            or tuple(applied or ()) != ("applied", expected_version + 1)
        ):
            raise SemanticEdgeIntegrityError(
                "The semantic-edge application postcondition failed."
            )
        return SemanticEdgeApplyWriteResult(
            "applied", finding_id, expected_version + 1, edge, generation + 1
        )


__all__ = [
    "SEMANTIC_EDGE_EVIDENCE_SCHEMA_VERSION",
    "SEMANTIC_EDGE_RULE_VERSION",
    "SemanticEdgeApplyDraft",
    "SemanticEdgeApplyWriteResult",
    "SemanticEdgeConflictError",
    "SemanticEdgeIntegrityError",
    "SemanticEdgeRecord",
    "SemanticEdgeRepository",
    "SemanticEdgeRepositoryError",
    "semantic_edge_id_for_finding",
]

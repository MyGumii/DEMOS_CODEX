"""Versioned, policy-derived curation finding persistence for P6.3.

Persisted evidence is bounded and body-free.  Transient Markdown supplied for
span verification is rebound to the canonical revision hashes and is never
serialized.  Risk tier, review authority, visibility, action, and decision mode
are derived from the frozen v1 policy table below and are never accepted from a
detector or caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from typing import Literal
from uuid import UUID, uuid5

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError
from .candidate_retrieval import (
    CandidateRetrievalIntegrityError,
    IncrementalCandidateSnapshot,
    validate_incremental_candidate_snapshot,
)


FindingKind = Literal[
    "lint",
    "exact_repeat",
    "curation_clear",
    "weak_complementary",
    "near_duplicate",
    "update",
    "potential_conflict",
    "semantic_edge",
    "replace",
    "merge",
    "curation_release",
    "curation_quarantine",
    "deactivate",
    "purge",
]
FindingSeverity = Literal["blocker", "error", "warning", "info"]
FindingRiskTier = Literal["T0", "T1", "T2", "T3"]
FindingDecisionMode = Literal["automatic", "human"]
FindingReviewer = Literal["none", "space_editor", "part_leader", "admin"]
FindingVisibility = Literal[
    "none", "badge", "agent_hidden", "publication_blocked"
]
FindingAction = Literal[
    "none",
    "provenance",
    "review_duplicate",
    "update",
    "review_conflict",
    "relation",
    "merge",
    "replace",
    "publish",
    "quarantine",
    "deactivate",
    "purge",
]
FindingStatus = Literal[
    "pending", "approved", "rejected", "ignored", "obsolete", "applied", "apply_failed"
]
FindingReviewDecision = Literal["approve", "reject", "ignore"]
EvidenceSide = Literal["source", "target"]

FINDING_EVIDENCE_SCHEMA_VERSION = 1
FINDING_POLICY_VERSION = "p6.3-finding-policy-v1"

_FINDING_NAMESPACE = UUID("710ab9e0-1636-4476-aae0-c73d319ab3b2")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9._-]{0,119}$", re.ASCII)
_EVIDENCE_CODE_RE = re.compile(r"^[a-z][a-z0-9._-]{0,79}$", re.ASCII)
_MAX_INTEGER = 9_223_372_036_854_775_807
_MAX_MARKDOWN_BYTES = 2_000_000


class FindingRepositoryError(KnowledgeCoreDatabaseError):
    code = "FINDING_REPOSITORY_ERROR"


class FindingConflictError(FindingRepositoryError):
    code = "FINDING_CONFLICT"


class FindingIntegrityError(FindingRepositoryError):
    code = "FINDING_INTEGRITY_ERROR"


class FindingPolicyError(ValueError):
    code = "FINDING_POLICY_INVALID"


@dataclass(frozen=True, slots=True)
class FindingPolicy:
    risk_tier: FindingRiskTier
    decision_mode: FindingDecisionMode
    required_reviewer: FindingReviewer
    visibility_effect: FindingVisibility
    proposed_action: FindingAction


_POLICY: dict[tuple[str, str, str | None], FindingPolicy] = {
    ("lint", "blocker", None): FindingPolicy("T0", "automatic", "none", "publication_blocked", "quarantine"),
    ("lint", "error", None): FindingPolicy("T0", "automatic", "none", "publication_blocked", "quarantine"),
    ("lint", "warning", None): FindingPolicy("T0", "automatic", "none", "badge", "none"),
    ("lint", "info", None): FindingPolicy("T0", "automatic", "none", "none", "none"),
    ("exact_repeat", "info", "no_op"): FindingPolicy("T0", "automatic", "none", "none", "none"),
    ("exact_repeat", "info", "provenance"): FindingPolicy("T1", "automatic", "none", "none", "provenance"),
    ("curation_clear", "info", None): FindingPolicy("T1", "automatic", "none", "none", "publish"),
    ("weak_complementary", "warning", None): FindingPolicy("T2", "human", "space_editor", "badge", "none"),
    ("near_duplicate", "warning", None): FindingPolicy("T2", "human", "space_editor", "agent_hidden", "review_duplicate"),
    ("update", "info", None): FindingPolicy("T2", "human", "space_editor", "agent_hidden", "update"),
    ("potential_conflict", "error", None): FindingPolicy("T2", "human", "space_editor", "agent_hidden", "review_conflict"),
    ("semantic_edge", "info", None): FindingPolicy("T2", "human", "space_editor", "none", "relation"),
    ("replace", "warning", None): FindingPolicy("T2", "human", "space_editor", "agent_hidden", "replace"),
    ("merge", "warning", None): FindingPolicy("T2", "human", "space_editor", "agent_hidden", "merge"),
    ("curation_release", "info", None): FindingPolicy("T2", "human", "space_editor", "agent_hidden", "publish"),
    ("curation_quarantine", "error", None): FindingPolicy("T2", "human", "space_editor", "agent_hidden", "quarantine"),
    ("deactivate", "warning", None): FindingPolicy("T3", "human", "space_editor", "none", "deactivate"),
    ("purge", "warning", None): FindingPolicy("T3", "human", "admin", "none", "purge"),
}

_PAIR_REQUIRED = {
    "exact_repeat",
    "weak_complementary",
    "near_duplicate",
    "potential_conflict",
    "semantic_edge",
    "replace",
    "merge",
}
_PAIR_FORBIDDEN = {
    "lint",
    "curation_clear",
    "curation_release",
    "curation_quarantine",
    "deactivate",
    "purge",
}
_CANDIDATE_EVIDENCE_REQUIRED = {
    "weak_complementary",
    "near_duplicate",
    "potential_conflict",
    "semantic_edge",
    "replace",
    "merge",
}
_SPANS_REQUIRED = {
    "weak_complementary",
    "near_duplicate",
    "update",
    "potential_conflict",
    "semantic_edge",
    "replace",
    "merge",
}
_EDGE_TYPES = {
    "related",
    "supports",
    "contradicts",
    "depends_on",
    "extends",
    "example_of",
}


@dataclass(frozen=True, slots=True)
class FindingEvidenceSpan:
    side: EvidenceSide
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    excerpt_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "end_column": self.end_column,
            "end_line": self.end_line,
            "excerpt_sha256": self.excerpt_sha256,
            "side": self.side,
            "start_column": self.start_column,
            "start_line": self.start_line,
        }


@dataclass(frozen=True, slots=True)
class FindingDetection:
    space_id: str
    finding_kind: FindingKind
    severity: FindingSeverity
    policy_variant: str | None
    source_item_id: str
    source_revision_id: str
    target_item_id: str | None
    target_revision_id: str | None
    detector_name: str
    detector_version: str
    rule_version: str
    model_id: str | None
    prompt_version: str | None
    confidence: float | None
    detector_evidence_sha256: str
    candidate_retrieval_evidence_sha256: str | None
    evidence_codes: tuple[str, ...]
    evidence_spans: tuple[FindingEvidenceSpan, ...]
    candidate_snapshot: IncrementalCandidateSnapshot | None = field(repr=False)
    source_markdown: str | None = field(repr=False)
    target_markdown: str | None = field(repr=False)
    proposed_relation_type: str | None = None


@dataclass(frozen=True, slots=True)
class FindingRecord:
    id: str
    space_id: str
    finding_kind: str
    risk_tier: str
    severity: str
    status: str
    decision_mode: str
    required_reviewer: str
    visibility_effect: str
    proposed_action: str
    source_item_id: str
    source_revision_id: str
    target_item_id: str | None
    target_revision_id: str | None
    detector_name: str
    detector_version: str
    rule_version: str
    model_id: str | None
    prompt_version: str | None
    confidence: float | None
    evidence_json: str
    proposed_action_json: str
    reviewed_by: str | None
    reviewed_at_ms: int | None
    review_reason: str | None
    created_at_ms: int
    updated_at_ms: int
    row_version: int
    source_space_id: str | None = None
    selected_view_space_id: str | None = None
    inherited: bool = False
    origin: Literal["native", "inherited", "overlay"] | None = None
    overlay_grant_id: str | None = None
    capabilities: tuple[str, ...] = ()
    corpus_closure_sha256: str | None = None

    @property
    def evidence(self) -> dict[str, object]:
        return _decode_object(self.evidence_json, "finding evidence")

    @property
    def proposed_action_evidence(self) -> dict[str, object]:
        return _decode_object(self.proposed_action_json, "finding action evidence")


@dataclass(frozen=True, slots=True)
class FindingPutResult:
    finding: FindingRecord
    inserted: bool
    suppressed_by_ignored_exact_input: bool


@dataclass(frozen=True, slots=True)
class FindingReviewWriteResult:
    finding: FindingRecord
    outcome: Literal["approved", "rejected", "ignored", "obsolete"]


@dataclass(frozen=True, slots=True)
class _Endpoint:
    item_id: str
    revision_id: str
    space_id: str
    current_revision_id: str
    lifecycle_status: str
    curation_status: str
    title: str
    raw_content_sha256: str
    normalized_content_sha256: str
    content_size_bytes: int
    normalizer_version: str
    content_md: str | None
    content_blob_hash: str | None
    body_sha256: str
    body_size_bytes: int


@dataclass(frozen=True, slots=True)
class _ReviewEndpointState:
    current_revision_id: str
    lifecycle_status: str
    curation_status: str


@dataclass(frozen=True, slots=True)
class _PreparedDetection:
    detection: FindingDetection
    policy: FindingPolicy
    finding_id: str
    evidence_json: str
    action_json: str
    source: _Endpoint
    target: _Endpoint | None


_COLUMNS = """id,space_id,finding_kind,risk_tier,severity,status,decision_mode,
required_reviewer,visibility_effect,proposed_action,source_item_id,
source_revision_id,target_item_id,target_revision_id,detector_name,
detector_version,rule_version,model_id,prompt_version,confidence,evidence_json,
proposed_action_json,reviewed_by,reviewed_at,review_reason,created_at,updated_at,
row_version"""


def finding_policy(
    finding_kind: object, severity: object, policy_variant: object = None
) -> FindingPolicy:
    if not isinstance(finding_kind, str) or not isinstance(severity, str):
        raise FindingPolicyError("Finding kind and severity must be text.")
    if policy_variant is not None and not isinstance(policy_variant, str):
        raise FindingPolicyError("Finding policy variant must be text or null.")
    policy = _POLICY.get((finding_kind, severity, policy_variant))
    if policy is None:
        raise FindingPolicyError(
            "Finding kind, severity, and variant do not match the frozen policy."
        )
    return policy


def _require_transaction(connection: object) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("FindingRepository requires a ManagedConnection.")
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Finding writes require one caller-owned managed transaction."
        )
    return connection


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise FindingPolicyError(f"{field} must be a canonical lowercase UUID.")
    return value


def _optional_uuid(value: object, field: str) -> str | None:
    return None if value is None else _uuid(value, field)


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise FindingPolicyError(f"{field} must be a lowercase SHA-256 value.")
    return value


def _optional_sha(value: object, field: str) -> str | None:
    return None if value is None else _sha(value, field)


def _integer(value: object, field: str, *, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= _MAX_INTEGER
    ):
        raise FindingPolicyError(f"{field} is invalid.")
    return value


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise FindingPolicyError(f"{field} is invalid.")
    return value


def _optional_text(value: object, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or value != unicodedata.normalize("NFKC", value)
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise FindingPolicyError(f"{field} is invalid.")
    return value


def _canonical(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise FindingPolicyError("Finding evidence must be finite JSON.") from exc
    return encoded


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("ascii")).hexdigest()


def _decode_object(value: str, field: str) -> dict[str, object]:
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise FindingIntegrityError(f"Stored {field} is invalid.") from exc
    if type(decoded) is not dict or _canonical(decoded) != value:
        raise FindingIntegrityError(f"Stored {field} is not canonical.")
    return decoded


def _row_value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, TypeError, IndexError) as exc:
        raise FindingIntegrityError("A finding row cannot be mapped.") from exc


def _record(row: object) -> FindingRecord:
    confidence = _row_value(row, "confidence")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0.0 <= float(confidence) <= 1.0
    ):
        raise FindingIntegrityError("Stored finding confidence is invalid.")
    record = FindingRecord(
        id=str(_row_value(row, "id")),
        space_id=str(_row_value(row, "space_id")),
        finding_kind=str(_row_value(row, "finding_kind")),
        risk_tier=str(_row_value(row, "risk_tier")),
        severity=str(_row_value(row, "severity")),
        status=str(_row_value(row, "status")),
        decision_mode=str(_row_value(row, "decision_mode")),
        required_reviewer=str(_row_value(row, "required_reviewer")),
        visibility_effect=str(_row_value(row, "visibility_effect")),
        proposed_action=str(_row_value(row, "proposed_action")),
        source_item_id=str(_row_value(row, "source_item_id")),
        source_revision_id=str(_row_value(row, "source_revision_id")),
        target_item_id=(None if _row_value(row, "target_item_id") is None else str(_row_value(row, "target_item_id"))),
        target_revision_id=(None if _row_value(row, "target_revision_id") is None else str(_row_value(row, "target_revision_id"))),
        detector_name=str(_row_value(row, "detector_name")),
        detector_version=str(_row_value(row, "detector_version")),
        rule_version=str(_row_value(row, "rule_version")),
        model_id=None if _row_value(row, "model_id") is None else str(_row_value(row, "model_id")),
        prompt_version=None if _row_value(row, "prompt_version") is None else str(_row_value(row, "prompt_version")),
        confidence=None if confidence is None else float(confidence),
        evidence_json=str(_row_value(row, "evidence_json")),
        proposed_action_json=str(_row_value(row, "proposed_action_json")),
        reviewed_by=None if _row_value(row, "reviewed_by") is None else str(_row_value(row, "reviewed_by")),
        reviewed_at_ms=None if _row_value(row, "reviewed_at") is None else int(_row_value(row, "reviewed_at")),
        review_reason=None if _row_value(row, "review_reason") is None else str(_row_value(row, "review_reason")),
        created_at_ms=int(_row_value(row, "created_at")),
        updated_at_ms=int(_row_value(row, "updated_at")),
        row_version=int(_row_value(row, "row_version")),
    )
    _uuid(record.id, "finding id")
    _uuid(record.space_id, "finding space id")
    _uuid(record.source_item_id, "finding source item id")
    _uuid(record.source_revision_id, "finding source revision id")
    _optional_uuid(record.target_item_id, "finding target item id")
    _optional_uuid(record.target_revision_id, "finding target revision id")
    _decode_object(record.evidence_json, "finding evidence")
    _decode_object(record.proposed_action_json, "finding action evidence")
    _integer(record.created_at_ms, "finding created timestamp")
    _integer(record.updated_at_ms, "finding updated timestamp")
    _integer(record.row_version, "finding row version", positive=True)
    return record


def _load_endpoint(
    connection: ManagedConnection, item_id: str, revision_id: str
) -> _Endpoint:
    row = connection.execute(
        """SELECT item.id AS item_id,item.space_id,item.current_revision_id,
                  item.lifecycle_status,item.curation_status,
                  revision.id AS revision_id,revision.title,
                  revision.raw_content_sha256,
                  revision.normalized_content_sha256,revision.content_size_bytes,
                  revision.normalizer_version,body.content_md,
                  body.content_blob_hash,body.content_sha256 AS body_sha256,
                  body.content_size_bytes AS body_size_bytes
             FROM knowledge_item AS item
             JOIN knowledge_revision AS revision
               ON revision.item_id=item.id AND revision.id=?
             JOIN knowledge_revision_body AS body
               ON body.item_id=item.id AND body.revision_id=revision.id
            WHERE item.id=?""",
        (revision_id, item_id),
    ).fetchone()
    if row is None:
        raise FindingConflictError("A compared item revision is unavailable.")
    endpoint = _Endpoint(
        item_id=_uuid(_row_value(row, "item_id"), "endpoint item id"),
        revision_id=_uuid(_row_value(row, "revision_id"), "endpoint revision id"),
        space_id=_uuid(_row_value(row, "space_id"), "endpoint space id"),
        current_revision_id=_uuid(_row_value(row, "current_revision_id"), "endpoint current revision id"),
        lifecycle_status=str(_row_value(row, "lifecycle_status")),
        curation_status=str(_row_value(row, "curation_status")),
        title=str(_row_value(row, "title")),
        raw_content_sha256=_sha(_row_value(row, "raw_content_sha256"), "endpoint raw SHA"),
        normalized_content_sha256=_sha(_row_value(row, "normalized_content_sha256"), "endpoint normalized SHA"),
        content_size_bytes=_integer(_row_value(row, "content_size_bytes"), "endpoint size"),
        normalizer_version=_identifier(_row_value(row, "normalizer_version"), "endpoint normalizer"),
        content_md=None if _row_value(row, "content_md") is None else str(_row_value(row, "content_md")),
        content_blob_hash=_optional_sha(_row_value(row, "content_blob_hash"), "endpoint blob hash"),
        body_sha256=_sha(_row_value(row, "body_sha256"), "endpoint body SHA"),
        body_size_bytes=_integer(_row_value(row, "body_size_bytes"), "endpoint body size"),
    )
    if (
        endpoint.lifecycle_status != "active"
        or endpoint.curation_status not in {"published", "provisional", "quarantined"}
        or endpoint.body_sha256 != endpoint.raw_content_sha256
        or endpoint.body_size_bytes != endpoint.content_size_bytes
        or ((endpoint.content_md is None) == (endpoint.content_blob_hash is None))
        or (
            endpoint.content_md is not None
            and (
                hashlib.sha256(endpoint.content_md.encode("utf-8")).hexdigest()
                != endpoint.raw_content_sha256
                or len(endpoint.content_md.encode("utf-8"))
                != endpoint.content_size_bytes
            )
        )
    ):
        raise FindingIntegrityError("Compared revision body evidence is inconsistent.")
    return endpoint


def _load_review_endpoint_state(
    connection: ManagedConnection, item_id: str
) -> _ReviewEndpointState:
    row = connection.execute(
        """SELECT current_revision_id,lifecycle_status,curation_status
             FROM knowledge_item WHERE id=?""",
        (item_id,),
    ).fetchone()
    if row is None:
        raise FindingIntegrityError("A finding endpoint item disappeared.")
    return _ReviewEndpointState(
        current_revision_id=_uuid(
            _row_value(row, "current_revision_id"), "current endpoint revision id"
        ),
        lifecycle_status=str(_row_value(row, "lifecycle_status")),
        curation_status=str(_row_value(row, "curation_status")),
    )


def _span(value: FindingEvidenceSpan, *, target_exists: bool) -> FindingEvidenceSpan:
    if not isinstance(value, FindingEvidenceSpan):
        raise TypeError("FindingEvidenceSpan values are required.")
    if value.side not in {"source", "target"} or (
        value.side == "target" and not target_exists
    ):
        raise FindingPolicyError("Evidence span side is invalid for the finding.")
    start_line = _integer(value.start_line, "span start line", positive=True)
    start_column = _integer(value.start_column, "span start column", positive=True)
    end_line = _integer(value.end_line, "span end line", positive=True)
    end_column = _integer(value.end_column, "span end column", positive=True)
    if (end_line, end_column) <= (start_line, start_column):
        raise FindingPolicyError("Evidence span must contain a nonempty range.")
    return FindingEvidenceSpan(
        side=value.side,
        start_line=start_line,
        start_column=start_column,
        end_line=end_line,
        end_column=end_column,
        excerpt_sha256=_sha(value.excerpt_sha256, "span excerpt SHA"),
    )


def _normalized_markdown(value: str, field: str) -> tuple[bytes, str]:
    if not isinstance(value, str) or "\x00" in value:
        raise FindingPolicyError(f"{field} is invalid.")
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise FindingPolicyError(f"{field} is not valid UTF-8 text.") from exc
    if len(raw) > _MAX_MARKDOWN_BYTES:
        raise FindingPolicyError(f"{field} exceeds the bounded evidence limit.")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return raw, normalized


def _verified_markdown(
    endpoint: _Endpoint,
    value: str | None,
    *,
    required: bool,
    field: str,
) -> str | None:
    if value is None:
        if required:
            raise FindingPolicyError(f"{field} is required to verify evidence spans.")
        return None
    raw, normalized = _normalized_markdown(value, field)
    if (
        hashlib.sha256(raw).hexdigest() != endpoint.raw_content_sha256
        or len(raw) != endpoint.content_size_bytes
        or hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        != endpoint.normalized_content_sha256
        or endpoint.normalizer_version != "markdown_lf_v1"
        or (endpoint.content_md is not None and endpoint.content_md != value)
    ):
        raise FindingConflictError(
            f"{field} no longer matches the compared canonical revision."
        )
    return normalized


def _position_offset(markdown: str, line: int, column: int) -> int:
    lines = markdown.split("\n")
    if line > len(lines) or column > len(lines[line - 1]) + 1:
        raise FindingPolicyError("Evidence span coordinates exceed the revision body.")
    return sum(len(part) + 1 for part in lines[: line - 1]) + column - 1


def _verify_spans(
    spans: tuple[FindingEvidenceSpan, ...],
    *,
    source_markdown: str | None,
    target_markdown: str | None,
) -> None:
    for span in spans:
        markdown = source_markdown if span.side == "source" else target_markdown
        if markdown is None:
            raise FindingPolicyError("Evidence span body verification is unavailable.")
        start = _position_offset(markdown, span.start_line, span.start_column)
        end = _position_offset(markdown, span.end_line, span.end_column)
        if end <= start:
            raise FindingPolicyError("Evidence span must contain a nonempty range.")
        observed = hashlib.sha256(markdown[start:end].encode("utf-8")).hexdigest()
        if observed != span.excerpt_sha256:
            raise FindingPolicyError("Evidence span excerpt hash does not match.")


def _prepare_detection(
    connection: ManagedConnection, value: FindingDetection
) -> _PreparedDetection:
    if not isinstance(value, FindingDetection):
        raise TypeError("A FindingDetection is required.")
    policy = finding_policy(value.finding_kind, value.severity, value.policy_variant)
    space_id = _uuid(value.space_id, "space_id")
    source_item_id = _uuid(value.source_item_id, "source_item_id")
    source_revision_id = _uuid(value.source_revision_id, "source_revision_id")
    target_item_id = _optional_uuid(value.target_item_id, "target_item_id")
    target_revision_id = _optional_uuid(value.target_revision_id, "target_revision_id")
    target_exists = target_item_id is not None and target_revision_id is not None
    if (target_item_id is None) != (target_revision_id is None):
        raise FindingPolicyError("Finding target item and revision must form a pair.")
    if value.finding_kind in _PAIR_REQUIRED and not target_exists:
        raise FindingPolicyError("This finding kind requires a compared target.")
    if value.finding_kind in _PAIR_FORBIDDEN and target_exists:
        raise FindingPolicyError("This finding kind cannot have a compared target.")
    if target_item_id == source_item_id:
        raise FindingPolicyError("A finding cannot compare an item to itself.")
    detector_name = _identifier(value.detector_name, "detector_name")
    detector_version = _identifier(value.detector_version, "detector_version")
    rule_version = _identifier(value.rule_version, "rule_version")
    model_id = _optional_text(value.model_id, "model_id", 200)
    prompt_version = _optional_text(value.prompt_version, "prompt_version", 200)
    if (model_id is None) != (prompt_version is None):
        raise FindingPolicyError("Model and prompt versions must form a pair.")
    if policy.risk_tier in {"T0", "T1"} and model_id is not None:
        raise FindingPolicyError("Deterministic T0/T1 findings cannot claim model evidence.")
    confidence = value.confidence
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0.0 <= float(confidence) <= 1.0
    ):
        raise FindingPolicyError("Finding confidence must be finite from zero to one.")
    confidence = None if confidence is None else float(confidence)
    detector_evidence_sha = _sha(
        value.detector_evidence_sha256, "detector evidence SHA"
    )
    candidate_sha = _optional_sha(
        value.candidate_retrieval_evidence_sha256,
        "candidate retrieval evidence SHA",
    )
    if value.finding_kind in _CANDIDATE_EVIDENCE_REQUIRED and candidate_sha is None:
        raise FindingPolicyError("This finding requires candidate retrieval evidence.")
    if value.finding_kind not in _CANDIDATE_EVIDENCE_REQUIRED and (
        candidate_sha is not None or value.candidate_snapshot is not None
    ):
        raise FindingPolicyError(
            "This finding kind cannot claim candidate retrieval evidence."
        )
    codes = tuple(value.evidence_codes)
    if (
        not codes
        or any(not isinstance(code, str) or _EVIDENCE_CODE_RE.fullmatch(code) is None for code in codes)
        or codes != tuple(sorted(set(codes)))
    ):
        raise FindingPolicyError("Evidence codes must be sorted, unique identifiers.")
    spans = tuple(_span(span, target_exists=target_exists) for span in value.evidence_spans)
    canonical_spans = tuple(
        sorted(
            set(spans),
            key=lambda span: (
                span.side,
                span.start_line,
                span.start_column,
                span.end_line,
                span.end_column,
                span.excerpt_sha256,
            ),
        )
    )
    if spans != canonical_spans:
        raise FindingPolicyError("Evidence spans must be canonical and unique.")
    if value.finding_kind in _SPANS_REQUIRED:
        if not spans or (target_exists and {span.side for span in spans} != {"source", "target"}):
            raise FindingPolicyError("This finding requires source/target evidence spans.")
    relation_type = value.proposed_relation_type
    if value.finding_kind == "semantic_edge":
        if relation_type not in _EDGE_TYPES:
            raise FindingPolicyError("Semantic edge relation type is invalid.")
    elif relation_type is not None:
        raise FindingPolicyError("Only semantic-edge findings may name a relation type.")

    source = _load_endpoint(connection, source_item_id, source_revision_id)
    target = None if not target_exists else _load_endpoint(
        connection, target_item_id, target_revision_id  # type: ignore[arg-type]
    )
    if source.space_id != space_id or (target is not None and target.space_id != space_id):
        raise FindingPolicyError("Finding endpoints must belong to one exact space.")
    if target is not None and target.curation_status != "published":
        raise FindingConflictError("A candidate target must be currently published.")
    if value.finding_kind in {"near_duplicate", "potential_conflict"} and source.curation_status != "provisional":
        raise FindingConflictError("A strong risky source must remain provisional.")
    if value.finding_kind == "weak_complementary" and source.curation_status != "published":
        raise FindingConflictError("A weak-complementary source must remain published.")
    if value.finding_kind == "exact_repeat" and target is not None and (
        source.raw_content_sha256 != target.raw_content_sha256
        or source.normalized_content_sha256 != target.normalized_content_sha256
        or source.content_size_bytes != target.content_size_bytes
    ):
        raise FindingPolicyError("Exact-repeat evidence is not byte-exact.")
    if value.finding_kind in _CANDIDATE_EVIDENCE_REQUIRED:
        if not isinstance(value.candidate_snapshot, IncrementalCandidateSnapshot):
            raise FindingPolicyError(
                "This finding requires the complete body-free candidate snapshot."
            )
        try:
            snapshot = validate_incremental_candidate_snapshot(value.candidate_snapshot)
        except (CandidateRetrievalIntegrityError, TypeError, ValueError) as exc:
            raise FindingPolicyError("Candidate retrieval evidence is invalid.") from exc
        if (
            snapshot.evidence_sha256 != candidate_sha
            or snapshot.space_id != space_id
            or snapshot.source_item_id != source_item_id
            or snapshot.source_revision_id != source_revision_id
            or snapshot.source_raw_content_sha256 != source.raw_content_sha256
            or snapshot.source_normalized_content_sha256
            != source.normalized_content_sha256
            or snapshot.source_content_size_bytes != source.content_size_bytes
        ):
            raise FindingPolicyError(
                "Candidate retrieval evidence is not bound to the source revision."
            )
        candidate = next(
            (
                item
                for item in snapshot.candidates
                if item.item_id == target_item_id
                and item.revision_id == target_revision_id
            ),
            None,
        )
        if target is None or candidate is None or (
            candidate.raw_content_sha256 != target.raw_content_sha256
            or candidate.normalized_content_sha256
            != target.normalized_content_sha256
            or candidate.content_size_bytes != target.content_size_bytes
            or candidate.title_sha256
            != hashlib.sha256(target.title.encode("utf-8")).hexdigest()
        ):
            raise FindingPolicyError(
                "Candidate retrieval evidence is not bound to the target revision."
            )
    source_markdown = _verified_markdown(
        source,
        value.source_markdown,
        required=any(span.side == "source" for span in spans),
        field="source_markdown",
    )
    target_markdown = None
    if target is not None:
        target_markdown = _verified_markdown(
            target,
            value.target_markdown,
            required=any(span.side == "target" for span in spans),
            field="target_markdown",
        )
    elif value.target_markdown is not None:
        raise FindingPolicyError("Target Markdown requires a target revision.")
    _verify_spans(
        spans,
        source_markdown=source_markdown,
        target_markdown=target_markdown,
    )

    evidence = {
        "candidate_retrieval_evidence_sha256": candidate_sha,
        "detector_evidence_sha256": detector_evidence_sha,
        "evidence_codes": list(codes),
        "evidence_spans": [span.as_dict() for span in spans],
        "finding_policy_version": FINDING_POLICY_VERSION,
        "schema_version": FINDING_EVIDENCE_SCHEMA_VERSION,
        "source_content_sha256": source.raw_content_sha256,
        "source_normalized_content_sha256": source.normalized_content_sha256,
        "target_content_sha256": None if target is None else target.raw_content_sha256,
        "target_normalized_content_sha256": None if target is None else target.normalized_content_sha256,
    }
    action = {
        "action": policy.proposed_action,
        "relation_type": relation_type,
        "schema_version": FINDING_EVIDENCE_SCHEMA_VERSION,
        "source_revision_id": source_revision_id,
        "target_revision_id": target_revision_id,
    }
    identity = {
        "action": action,
        "confidence": confidence,
        "detector_name": detector_name,
        "detector_version": detector_version,
        "evidence": evidence,
        "finding_kind": value.finding_kind,
        "model_id": model_id,
        "policy": {
            "decision_mode": policy.decision_mode,
            "required_reviewer": policy.required_reviewer,
            "risk_tier": policy.risk_tier,
            "severity": value.severity,
            "visibility_effect": policy.visibility_effect,
        },
        "prompt_version": prompt_version,
        "rule_version": rule_version,
        "source_item_id": source_item_id,
        "space_id": space_id,
        "target_item_id": target_item_id,
    }
    finding_id = str(uuid5(_FINDING_NAMESPACE, _canonical_sha256(identity)))
    normalized = FindingDetection(
        space_id=space_id,
        finding_kind=value.finding_kind,
        severity=value.severity,
        policy_variant=value.policy_variant,
        source_item_id=source_item_id,
        source_revision_id=source_revision_id,
        target_item_id=target_item_id,
        target_revision_id=target_revision_id,
        detector_name=detector_name,
        detector_version=detector_version,
        rule_version=rule_version,
        model_id=model_id,
        prompt_version=prompt_version,
        confidence=confidence,
        detector_evidence_sha256=detector_evidence_sha,
        candidate_retrieval_evidence_sha256=candidate_sha,
        evidence_codes=codes,
        evidence_spans=spans,
        candidate_snapshot=value.candidate_snapshot,
        source_markdown=value.source_markdown,
        target_markdown=value.target_markdown,
        proposed_relation_type=relation_type,
    )
    return _PreparedDetection(
        normalized,
        policy,
        finding_id,
        _canonical(evidence),
        _canonical(action),
        source,
        target,
    )


def _immutable_identity(record: FindingRecord) -> tuple[object, ...]:
    return (
        record.space_id,
        record.finding_kind,
        record.risk_tier,
        record.severity,
        record.decision_mode,
        record.required_reviewer,
        record.visibility_effect,
        record.proposed_action,
        record.source_item_id,
        record.source_revision_id,
        record.target_item_id,
        record.target_revision_id,
        record.detector_name,
        record.detector_version,
        record.rule_version,
        record.model_id,
        record.prompt_version,
        record.confidence,
        record.evidence_json,
        record.proposed_action_json,
    )


def _prepared_identity(value: _PreparedDetection) -> tuple[object, ...]:
    detection = value.detection
    policy = value.policy
    return (
        detection.space_id,
        detection.finding_kind,
        policy.risk_tier,
        detection.severity,
        policy.decision_mode,
        policy.required_reviewer,
        policy.visibility_effect,
        policy.proposed_action,
        detection.source_item_id,
        detection.source_revision_id,
        detection.target_item_id,
        detection.target_revision_id,
        detection.detector_name,
        detection.detector_version,
        detection.rule_version,
        detection.model_id,
        detection.prompt_version,
        detection.confidence,
        value.evidence_json,
        value.action_json,
    )


class FindingRepository:
    __slots__ = ()

    def get(self, connection: ManagedConnection, finding_id: str) -> FindingRecord | None:
        if not isinstance(connection, ManagedConnection):
            raise TypeError("FindingRepository requires a ManagedConnection.")
        finding_id = _uuid(finding_id, "finding_id")
        row = connection.execute(
            f"SELECT {_COLUMNS} FROM curation_finding WHERE id=?", (finding_id,)
        ).fetchone()
        return None if row is None else _record(row)

    def put_detection(
        self,
        connection: ManagedConnection,
        detection: FindingDetection,
        *,
        now_ms: int,
    ) -> FindingPutResult:
        connection = _require_transaction(connection)
        now_ms = _integer(now_ms, "finding timestamp")
        prepared = _prepare_detection(connection, detection)
        existing = self.get(connection, prepared.finding_id)
        if existing is not None:
            if _immutable_identity(existing) != _prepared_identity(prepared):
                raise FindingIntegrityError(
                    "The deterministic finding ID names different evidence."
                )
            return FindingPutResult(existing, False, existing.status == "ignored")
        if (
            prepared.source.current_revision_id != prepared.detection.source_revision_id
            or (
                prepared.target is not None
                and prepared.target.current_revision_id
                != prepared.detection.target_revision_id
            )
        ):
            raise FindingConflictError(
                "A compared revision changed before finding persistence."
            )
        value = prepared.detection
        policy = prepared.policy
        try:
            cursor = connection.execute(
                """INSERT INTO curation_finding(
                     id,space_id,finding_kind,risk_tier,severity,status,
                     decision_mode,required_reviewer,visibility_effect,
                     proposed_action,source_item_id,source_revision_id,
                     target_item_id,target_revision_id,detector_name,
                     detector_version,rule_version,model_id,prompt_version,
                     confidence,evidence_json,proposed_action_json,reviewed_by,
                     reviewed_at,review_reason,applied_operation_id,
                     applied_dedup_id,applied_job_id,applied_by,applied_at,
                     last_application_error_json,created_actor_kind,created_by,
                     created_at,updated_at,row_version
                   ) VALUES (
                     :id,:space_id,:finding_kind,:risk_tier,:severity,'pending',
                     :decision_mode,:required_reviewer,:visibility_effect,
                     :proposed_action,:source_item_id,:source_revision_id,
                     :target_item_id,:target_revision_id,:detector_name,
                     :detector_version,:rule_version,:model_id,:prompt_version,
                     :confidence,:evidence_json,:proposed_action_json,
                     NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'system',NULL,
                     :created_at,:updated_at,1
                   )""",
                {
                    "id": prepared.finding_id,
                    "space_id": value.space_id,
                    "finding_kind": value.finding_kind,
                    "risk_tier": policy.risk_tier,
                    "severity": value.severity,
                    "decision_mode": policy.decision_mode,
                    "required_reviewer": policy.required_reviewer,
                    "visibility_effect": policy.visibility_effect,
                    "proposed_action": policy.proposed_action,
                    "source_item_id": value.source_item_id,
                    "source_revision_id": value.source_revision_id,
                    "target_item_id": value.target_item_id,
                    "target_revision_id": value.target_revision_id,
                    "detector_name": value.detector_name,
                    "detector_version": value.detector_version,
                    "rule_version": value.rule_version,
                    "model_id": value.model_id,
                    "prompt_version": value.prompt_version,
                    "confidence": value.confidence,
                    "evidence_json": prepared.evidence_json,
                    "proposed_action_json": prepared.action_json,
                    "created_at": now_ms,
                    "updated_at": now_ms,
                },
            )
        except sqlite3.IntegrityError as exc:
            raise FindingIntegrityError(
                "The versioned finding violates canonical policy constraints."
            ) from exc
        if cursor.rowcount != 1:
            raise FindingIntegrityError("The versioned finding was not inserted.")
        record = self.get(connection, prepared.finding_id)
        if record is None or _immutable_identity(record) != _prepared_identity(prepared):
            raise FindingIntegrityError("The inserted finding failed its postcondition.")
        return FindingPutResult(record, True, False)

    def review(
        self,
        connection: ManagedConnection,
        *,
        finding_id: str,
        actor_user_id: str,
        decision: FindingReviewDecision,
        reason: str,
        expected_row_version: int,
        now_ms: int,
    ) -> FindingReviewWriteResult:
        connection = _require_transaction(connection)
        finding_id = _uuid(finding_id, "finding_id")
        if not isinstance(actor_user_id, str) or _USER_RE.fullmatch(actor_user_id) is None:
            raise FindingPolicyError("review actor user ID is invalid.")
        if decision not in {"approve", "reject", "ignore"}:
            raise FindingPolicyError("review decision is invalid.")
        reason = _optional_text(reason, "review reason", 2000)  # type: ignore[assignment]
        if reason is None:
            raise FindingPolicyError("review reason is required.")
        expected_row_version = _integer(
            expected_row_version, "expected finding row version", positive=True
        )
        now_ms = _integer(now_ms, "review timestamp")
        current = self.get(connection, finding_id)
        if current is None:
            raise FindingConflictError("The finding does not exist.")
        if current.row_version != expected_row_version or current.status not in {
            "pending",
            "apply_failed",
        }:
            raise FindingConflictError(
                "The finding is no longer reviewable at that version."
            )
        if current.status == "apply_failed" and decision == "ignore":
            raise FindingPolicyError(
                "An apply-failed finding may only be approved again or rejected."
            )
        if current.decision_mode != "human" or current.required_reviewer == "none":
            raise FindingPolicyError("Automatic findings do not accept human review.")
        proposal = connection.execute(
            """SELECT id,state,row_version FROM change_proposal
                WHERE finding_id=?""",
            (finding_id,),
        ).fetchone()
        expected_proposal_state = (
            "pending_review" if current.status == "pending" else "apply_failed"
        )
        if proposal is not None and (
            current.finding_kind != "update"
            or current.proposed_action != "update"
            or proposal["state"] != expected_proposal_state
            or not isinstance(proposal["row_version"], int)
            or isinstance(proposal["row_version"], bool)
            or int(proposal["row_version"]) < 1
        ):
            raise FindingIntegrityError(
                "The finding and its change proposal are not review-compatible."
            )
        source = _load_review_endpoint_state(connection, current.source_item_id)
        target = (
            None
            if current.target_item_id is None
            else _load_review_endpoint_state(connection, current.target_item_id)
        )
        stale = (
            source.current_revision_id != current.source_revision_id
            or source.lifecycle_status != "active"
            or (
                current.finding_kind in {"near_duplicate", "potential_conflict"}
                and source.curation_status != "provisional"
            )
            or (
                current.finding_kind == "weak_complementary"
                and source.curation_status != "published"
            )
            or (
                target is not None
                and (
                    target.current_revision_id != current.target_revision_id
                    or target.lifecycle_status != "active"
                    or target.curation_status != "published"
                )
            )
        )
        if stale:
            cursor = connection.execute(
                """UPDATE curation_finding
                      SET status='obsolete',applied_operation_id=NULL,
                          applied_dedup_id=NULL,applied_job_id=NULL,applied_by=NULL,
                          applied_at=NULL,last_application_error_json=NULL,
                          updated_at=?,row_version=row_version+1
                    WHERE id=? AND status=? AND row_version=?""",
                (now_ms, finding_id, current.status, expected_row_version),
            )
            outcome: Literal["approved", "rejected", "ignored", "obsolete"] = "obsolete"
        else:
            status = {"approve": "approved", "reject": "rejected", "ignore": "ignored"}[decision]
            cursor = connection.execute(
                """UPDATE curation_finding
                      SET status=?,reviewed_by=?,reviewed_at=?,review_reason=?,
                          applied_operation_id=NULL,applied_dedup_id=NULL,
                          applied_job_id=NULL,applied_by=NULL,applied_at=NULL,
                          last_application_error_json=NULL,
                          updated_at=?,row_version=row_version+1
                    WHERE id=? AND status=? AND row_version=?""",
                (
                    status,
                    actor_user_id,
                    now_ms,
                    reason,
                    now_ms,
                    finding_id,
                    current.status,
                    expected_row_version,
                ),
            )
            outcome = status  # type: ignore[assignment]
        if cursor.rowcount != 1:
            raise FindingConflictError("The finding changed before review commit.")
        if proposal is not None:
            proposal_state = "approved" if outcome == "approved" else "obsolete"
            proposal_cursor = connection.execute(
                """UPDATE change_proposal
                      SET state=?,applied_operation_id=NULL,applied_dedup_id=NULL,
                          last_application_error_json=NULL,updated_by=?,updated_at=?,
                          row_version=row_version+1
                    WHERE id=? AND finding_id=? AND state=?
                      AND row_version=?""",
                (
                    proposal_state,
                    actor_user_id,
                    now_ms,
                    proposal["id"],
                    finding_id,
                    expected_proposal_state,
                    proposal["row_version"],
                ),
            )
            if proposal_cursor.rowcount != 1:
                raise FindingConflictError(
                    "The change proposal changed before review commit."
                )
        record = self.get(connection, finding_id)
        if record is None or record.status != outcome or record.row_version != expected_row_version + 1:
            raise FindingIntegrityError("The finding review postcondition failed.")
        if proposal is not None:
            current_proposal = connection.execute(
                "SELECT state,row_version FROM change_proposal WHERE id=?",
                (proposal["id"],),
            ).fetchone()
            if (
                current_proposal is None
                or current_proposal["state"]
                != ("approved" if outcome == "approved" else "obsolete")
                or int(current_proposal["row_version"])
                != int(proposal["row_version"]) + 1
            ):
                raise FindingIntegrityError(
                    "The finding/proposal review postcondition failed."
                )
        return FindingReviewWriteResult(record, outcome)


__all__ = [
    "FINDING_EVIDENCE_SCHEMA_VERSION",
    "FINDING_POLICY_VERSION",
    "FindingConflictError",
    "FindingDetection",
    "FindingEvidenceSpan",
    "FindingIntegrityError",
    "FindingPolicy",
    "FindingPolicyError",
    "FindingPutResult",
    "FindingRecord",
    "FindingRepository",
    "FindingRepositoryError",
    "FindingReviewWriteResult",
    "finding_policy",
]

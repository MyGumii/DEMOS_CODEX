"""Bounded canonical management reads for an authorized corpus closure.

The repository owns no connection or transaction and performs no writes.  A
service must resolve the actor, selected view, physical source spaces, and
item-scoped overlays in the same read snapshot before calling these methods.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Literal, Mapping

from ..db import ManagedConnection
from ..errors import KnowledgeCoreError


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_MAX_INT = 9_223_372_036_854_775_807
_MAX_CLOSURE_SOURCES = 4096
_MAX_CLOSURE_OVERLAYS = 4096
_EDGE_TYPES = frozenset(
    {"related", "supports", "contradicts", "depends_on", "extends", "example_of"}
)
_EDGE_ORIGINS = frozenset({"manual", "semantic_approved", "semantic_auto"})
_FINDING_KINDS = frozenset(
    {
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
    }
)
_RISK_TIERS = frozenset({"T0", "T1", "T2", "T3"})
_SEVERITIES = frozenset({"blocker", "error", "warning", "info"})
_FINDING_STATUSES = frozenset(
    {"pending", "approved", "rejected", "ignored", "obsolete", "applied", "apply_failed"}
)
_REVIEWERS = frozenset({"none", "space_editor", "part_leader", "admin"})
_VISIBILITY_EFFECTS = frozenset(
    {"none", "badge", "agent_hidden", "publication_blocked"}
)
_PROPOSED_ACTIONS = frozenset(
    {
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
    }
)
_GRAPH_LEVEL_ACTIVITY_ACTIONS = frozenset(
    {
        "aggregate_edge.create",
        "semantic_edge.automatic_activate",
    }
)


class ManagementReadIntegrityError(KnowledgeCoreError):
    code = "INTERNAL_ERROR"
    http_status = 500
    retryable = False


@dataclass(frozen=True, slots=True)
class ManagementItemRecord:
    id: str
    space_id: str
    title: str
    lifecycle_status: Literal["active"]
    curation_status: Literal["published"]
    current_revision_id: str
    row_version: int
    updated_at_ms: int
    warning_badges: tuple[str, ...] = ()
    selected_view_space_id: str | None = None
    inherited: bool = False
    origin: Literal["native", "inherited", "overlay"] = "native"
    overlay_grant_id: str | None = None
    capabilities: tuple[str, ...] = ("read",)


@dataclass(frozen=True, slots=True)
class ManagementItemAuthorization:
    source_space_id: str
    selected_view_space_id: str
    source_revision_id: str
    inherited: bool
    origin: Literal["native", "inherited", "overlay"]
    overlay_grant_id: str | None
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ManagementGraphNode:
    id: str
    current_revision_id: str
    title: str
    lifecycle_status: Literal["active"]
    curation_status: Literal["published"]
    row_version: int
    space_id: str | None = None
    selected_view_space_id: str | None = None
    inherited: bool = False
    origin: Literal["native", "inherited", "overlay"] = "native"
    overlay_grant_id: str | None = None
    capabilities: tuple[str, ...] = ("read",)


@dataclass(frozen=True, slots=True)
class ManagementGraphEdge:
    id: str
    source_item_id: str
    target_item_id: str
    edge_type: str
    origin: str
    source_evidence_revision_id: str | None
    target_evidence_revision_id: str | None
    row_version: int
    space_id: str | None = None
    selected_view_space_id: str | None = None
    inherited: bool = False
    capabilities: tuple[str, ...] = ("read",)
    edge_scope: Literal["local", "aggregate"] = "local"
    home_space_id: str | None = None
    source_space_id: str | None = None
    target_space_id: str | None = None
    direction: str | None = None
    edge_status: str = "active"
    confidence_ppm: int | None = None
    source_evidence_span_sha256: str | None = None
    target_evidence_span_sha256: str | None = None
    source_evidence_location: Mapping[str, object] | None = None
    target_evidence_location: Mapping[str, object] | None = None
    detector_generation: int | None = None
    model_provider: str | None = None
    model_name: str | None = None
    prompt_schema_version: str | None = None
    scoring_version: str | None = None
    semantic_evidence_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ManagementGraph:
    space_id: str
    generation: int
    nodes: tuple[ManagementGraphNode, ...]
    edges: tuple[ManagementGraphEdge, ...]
    corpus_closure_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ManagementProjectionStatus:
    space_id: str
    projection_name: Literal["markdown"]
    desired_generation: int
    built_generation: int
    status: Literal["idle", "building", "ready", "error"]
    checksum_sha256: str | None
    last_error: Mapping[str, object] | None
    updated_at_ms: int
    built_at_ms: int | None


@dataclass(frozen=True, slots=True)
class ManagementFindingRecord:
    id: str
    space_id: str
    finding_kind: str
    risk_tier: str
    severity: str
    status: str
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
    evidence: Mapping[str, object]
    row_version: int
    created_at_ms: int
    updated_at_ms: int
    selected_view_space_id: str | None = None
    inherited: bool = False
    origin: Literal["native", "inherited", "overlay"] = "native"
    overlay_grant_id: str | None = None
    capabilities: tuple[str, ...] = ("read",)


@dataclass(frozen=True, slots=True)
class ManagementActivityRecord:
    event_uuid: str
    space_id: str
    occurred_at_ms: int
    action: str
    outcome: str
    actor_kind: Literal["user", "system"]
    actor_user_id: str | None
    actor_display_name: str
    actor_role_snapshot: str
    item_id: str | None
    base_revision_id: str | None
    new_revision_id: str | None
    reason: str | None
    selected_view_space_id: str | None = None
    inherited: bool = False
    origin: Literal["native", "inherited", "overlay"] = "native"
    overlay_grant_id: str | None = None
    capabilities: tuple[str, ...] = ("read",)


def _connection(value: object) -> ManagedConnection:
    if not isinstance(value, ManagedConnection) or not value.in_transaction:
        raise TypeError("An active ManagedConnection transaction is required.")
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ManagementReadIntegrityError(f"Stored {field} is invalid.")
    return value


def _integer(value: object, field: str, *, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= _MAX_INT
    ):
        raise ManagementReadIntegrityError(f"Stored {field} is invalid.")
    return value


def _text(value: object, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ManagementReadIntegrityError(f"Stored {field} is invalid.")
    return value


def _optional_text(value: object, field: str, maximum: int) -> str | None:
    return None if value is None else _text(value, field, maximum)


def _enum(value: object, field: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ManagementReadIntegrityError(f"Stored {field} is invalid.")
    return value


def _confidence(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManagementReadIntegrityError("Stored finding confidence is invalid.")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ManagementReadIntegrityError("Stored finding confidence is invalid.")
    return result


def _optional_uuid(value: object, field: str) -> str | None:
    return None if value is None else _uuid(value, field)


def _activity_text(value: object, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(
            (ord(character) < 32 and character not in {"\t", "\n", "\r"})
            or ord(character) == 127
            for character in value
        )
    ):
        raise ManagementReadIntegrityError(f"Stored {field} is invalid.")
    return value


def _optional_activity_text(value: object, field: str, maximum: int) -> str | None:
    return None if value is None else _activity_text(value, field, maximum)


def _json_object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, str):
        raise ManagementReadIntegrityError(f"Stored {field} is invalid.")

    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = item
        return result

    try:
        decoded = json.loads(value, object_pairs_hook=reject_duplicate)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ManagementReadIntegrityError(f"Stored {field} is invalid.") from exc
    if not isinstance(decoded, dict):
        raise ManagementReadIntegrityError(f"Stored {field} is invalid.")
    return decoded


def _graph_provenance(row: object) -> dict[str, object]:
    def value(name: str) -> object:
        try:
            return row[name]  # type: ignore[index]
        except (IndexError, KeyError, TypeError) as exc:
            raise ManagementReadIntegrityError(
                f"Stored graph {name} is unavailable."
            ) from exc

    return {
        "confidence_ppm": (
            None
            if value("confidence_ppm") is None
            else _integer(value("confidence_ppm"), "graph confidence")
        ),
        "detector_generation": (
            None
            if value("detector_generation") is None
            else _integer(
                value("detector_generation"),
                "graph detector generation",
                positive=True,
            )
        ),
        "model_name": _optional_text(value("model_name"), "graph model name", 160),
        "model_provider": _optional_text(
            value("model_provider"), "graph model provider", 120
        ),
        "prompt_schema_version": _optional_text(
            value("prompt_schema_version"), "graph prompt schema version", 80
        ),
        "scoring_version": _optional_text(
            value("scoring_version"), "graph scoring version", 80
        ),
        "semantic_evidence_sha256": _optional_text(
            value("semantic_evidence_sha256"), "graph semantic evidence hash", 64
        ),
        "source_evidence_location": (
            None
            if value("source_evidence_location_json") is None
            else _json_object(
                value("source_evidence_location_json"),
                "graph source evidence location",
            )
        ),
        "source_evidence_span_sha256": _optional_text(
            value("source_evidence_span_sha256"),
            "graph source evidence hash",
            64,
        ),
        "target_evidence_location": (
            None
            if value("target_evidence_location_json") is None
            else _json_object(
                value("target_evidence_location_json"),
                "graph target evidence location",
            )
        ),
        "target_evidence_span_sha256": _optional_text(
            value("target_evidence_span_sha256"),
            "graph target evidence hash",
            64,
        ),
    }


def _page_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 101:
        raise ValueError("limit must be between 1 and 101.")
    return value


def _closure_json(
    *,
    selected_space_id: object,
    source_space_ids: object,
    overlays: object,
) -> tuple[str, tuple[str, ...], str, tuple[dict[str, str], ...]]:
    """Validate a server-resolved closure and encode it for bounded JSON1 SQL."""

    selected = _uuid(selected_space_id, "selected_space_id")
    if (
        not isinstance(source_space_ids, tuple)
        or not 1 <= len(source_space_ids) <= _MAX_CLOSURE_SOURCES
    ):
        raise ValueError("The corpus source closure is invalid.")
    sources = tuple(
        dict.fromkeys(_uuid(value, "source_space_id") for value in source_space_ids)
    )
    if len(sources) != len(source_space_ids) or selected not in sources:
        raise ValueError("The corpus source closure is incoherent.")
    if not isinstance(overlays, tuple) or len(overlays) > _MAX_CLOSURE_OVERLAYS:
        raise ValueError("The distribution overlay closure is invalid.")
    normalized_overlays: list[dict[str, str]] = []
    for value in overlays:
        if not isinstance(value, dict) or set(value) != {
            "grant_id",
            "source_item_id",
            "source_revision_id",
            "source_space_id",
        }:
            raise ValueError("A distribution overlay is invalid.")
        normalized_overlays.append(
            {
                key: _uuid(value[key], key)
                for key in (
                    "grant_id",
                    "source_item_id",
                    "source_revision_id",
                    "source_space_id",
                )
            }
        )
    overlay_keys = {
        (
            value["grant_id"],
            value["source_space_id"],
            value["source_item_id"],
            value["source_revision_id"],
        )
        for value in normalized_overlays
    }
    if len(overlay_keys) != len(normalized_overlays):
        raise ValueError("The distribution overlay closure contains duplicates.")
    return (
        selected,
        sources,
        json.dumps(normalized_overlays, separators=(",", ":")),
        tuple(normalized_overlays),
    )


class ManagementReadRepository:
    """Read bounded management views from a server-authorized corpus closure."""

    def list_items(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        before_updated_at_ms: int | None = None,
        before_item_id: str | None = None,
        limit: int = 51,
    ) -> tuple[ManagementItemRecord, ...]:
        connection = _connection(connection)
        space = _uuid(space_id, "space_id")
        limit = _page_limit(limit)
        if (before_updated_at_ms is None) != (before_item_id is None):
            raise ValueError("Both item cursor fields must be supplied together.")
        clauses = [
            "i.space_id=?",
            "i.lifecycle_status='active'",
            "i.curation_status='published'",
            "i.current_revision_id IS NOT NULL",
        ]
        parameters: list[object] = [space]
        if before_updated_at_ms is not None:
            before_time = _integer(before_updated_at_ms, "item cursor timestamp")
            before_id = _uuid(before_item_id, "item cursor ID")
            clauses.append("(i.updated_at<? OR (i.updated_at=? AND i.id<?))")
            parameters.extend((before_time, before_time, before_id))
        parameters.append(limit)
        rows = connection.execute(
            f"""SELECT i.id,i.space_id,r.title,i.lifecycle_status,
                       i.curation_status,i.current_revision_id,i.row_version,
                       i.updated_at
                  FROM knowledge_item AS i
                  JOIN knowledge_revision AS r
                    ON r.id=i.current_revision_id AND r.item_id=i.id
                 WHERE {' AND '.join(clauses)}
                 ORDER BY i.updated_at DESC,i.id DESC LIMIT ?""",
            tuple(parameters),
        ).fetchall()
        result: list[ManagementItemRecord] = []
        for row in rows:
            if (
                row["space_id"] != space
                or row["lifecycle_status"] != "active"
                or row["curation_status"] != "published"
            ):
                raise ManagementReadIntegrityError("Stored item visibility is invalid.")
            result.append(
                ManagementItemRecord(
                    id=_uuid(row["id"], "item ID"),
                    space_id=_uuid(row["space_id"], "item space ID"),
                    title=_text(row["title"], "item title", 500),
                    lifecycle_status="active",
                    curation_status="published",
                    current_revision_id=_uuid(
                        row["current_revision_id"], "current revision ID"
                    ),
                    row_version=_integer(row["row_version"], "item row version", positive=True),
                    updated_at_ms=_integer(row["updated_at"], "item updated timestamp"),
                )
            )
        return tuple(result)

    def list_items_closure(
        self,
        connection: ManagedConnection,
        *,
        selected_space_id: str,
        source_space_ids: tuple[str, ...],
        overlays: tuple[dict[str, str], ...],
        before_updated_at_ms: int | None = None,
        before_item_id: str | None = None,
        limit: int = 51,
    ) -> tuple[ManagementItemRecord, ...]:
        connection = _connection(connection)
        selected, sources, overlay_json, _ = _closure_json(
            selected_space_id=selected_space_id,
            source_space_ids=source_space_ids,
            overlays=overlays,
        )
        limit = _page_limit(limit)
        if (before_updated_at_ms is None) != (before_item_id is None):
            raise ValueError("Both item cursor fields must be supplied together.")
        cursor_sql = ""
        parameters: list[object] = [
            json.dumps(sources, separators=(",", ":")),
            overlay_json,
        ]
        if before_updated_at_ms is not None:
            before_time = _integer(before_updated_at_ms, "item cursor timestamp")
            before_id = _uuid(before_item_id, "item cursor ID")
            cursor_sql = "AND (i.updated_at<? OR (i.updated_at=? AND i.id<?))"
            parameters.extend((before_time, before_time, before_id))
        parameters.append(limit)
        rows = connection.execute(
            f"""WITH authorized_sources(space_id) AS (
                   SELECT value FROM json_each(?)
                 ), authorized_overlays(
                   grant_id,source_space_id,source_item_id,source_revision_id
                 ) AS (
                   SELECT json_extract(value,'$.grant_id'),
                          json_extract(value,'$.source_space_id'),
                          json_extract(value,'$.source_item_id'),
                          json_extract(value,'$.source_revision_id')
                     FROM json_each(?)
                 )
                 SELECT i.id,i.space_id,r.title,i.lifecycle_status,
                        i.curation_status,i.current_revision_id,i.row_version,
                        i.updated_at,
                        (SELECT overlay.grant_id
                           FROM authorized_overlays AS overlay
                          WHERE overlay.source_space_id=i.space_id
                            AND overlay.source_item_id=i.id
                            AND overlay.source_revision_id=i.current_revision_id
                          ORDER BY overlay.grant_id LIMIT 1) AS overlay_grant_id
                   FROM knowledge_item AS i
                   JOIN knowledge_revision AS r
                     ON r.id=i.current_revision_id AND r.item_id=i.id
                  WHERE i.lifecycle_status='active'
                    AND i.curation_status='published'
                    AND i.current_revision_id IS NOT NULL
                    AND (
                      EXISTS (SELECT 1 FROM authorized_sources AS source
                               WHERE source.space_id=i.space_id)
                      OR EXISTS (
                        SELECT 1 FROM authorized_overlays AS overlay
                         WHERE overlay.source_space_id=i.space_id
                           AND overlay.source_item_id=i.id
                           AND overlay.source_revision_id=i.current_revision_id
                      )
                    )
                    {cursor_sql}
                  ORDER BY i.updated_at DESC,i.id DESC LIMIT ?""",
            tuple(parameters),
        ).fetchall()
        source_set = frozenset(sources)
        result: list[ManagementItemRecord] = []
        for row in rows:
            source_space = _uuid(row["space_id"], "item space ID")
            overlay_grant = _optional_uuid(
                row["overlay_grant_id"], "overlay grant ID"
            )
            if source_space not in source_set and overlay_grant is None:
                raise ManagementReadIntegrityError(
                    "Stored item escaped the authorized corpus closure."
                )
            origin: Literal["native", "inherited", "overlay"] = (
                "native"
                if source_space == selected
                else "inherited"
                if source_space in source_set
                else "overlay"
            )
            result.append(
                ManagementItemRecord(
                    id=_uuid(row["id"], "item ID"),
                    space_id=source_space,
                    title=_text(row["title"], "item title", 500),
                    lifecycle_status="active",
                    curation_status="published",
                    current_revision_id=_uuid(
                        row["current_revision_id"], "current revision ID"
                    ),
                    row_version=_integer(
                        row["row_version"], "item row version", positive=True
                    ),
                    updated_at_ms=_integer(
                        row["updated_at"], "item updated timestamp"
                    ),
                    selected_view_space_id=selected,
                    inherited=source_space != selected,
                    origin=origin,
                    overlay_grant_id=(
                        overlay_grant if origin == "overlay" else None
                    ),
                )
            )
        return tuple(result)

    def resolve_item_authorization(
        self,
        connection: ManagedConnection,
        *,
        selected_space_id: str,
        item_id: str,
        source_space_ids: tuple[str, ...],
        overlays: tuple[dict[str, str], ...],
    ) -> ManagementItemAuthorization | None:
        """Resolve one item to its physical source without revealing foreign IDs."""

        connection = _connection(connection)
        selected, sources, overlay_json, _ = _closure_json(
            selected_space_id=selected_space_id,
            source_space_ids=source_space_ids,
            overlays=overlays,
        )
        item = _uuid(item_id, "item_id")
        row = connection.execute(
            """WITH authorized_sources(space_id) AS (
                 SELECT value FROM json_each(?)
               ), authorized_overlays(
                 grant_id,source_space_id,source_item_id,source_revision_id
               ) AS (
                 SELECT json_extract(value,'$.grant_id'),
                        json_extract(value,'$.source_space_id'),
                        json_extract(value,'$.source_item_id'),
                        json_extract(value,'$.source_revision_id')
                   FROM json_each(?)
               )
               SELECT i.space_id,i.current_revision_id,i.lifecycle_status,
                      i.curation_status,
                      (SELECT overlay.grant_id
                         FROM authorized_overlays AS overlay
                        WHERE overlay.source_space_id=i.space_id
                          AND overlay.source_item_id=i.id
                          AND overlay.source_revision_id=i.current_revision_id
                        ORDER BY overlay.grant_id LIMIT 1) AS overlay_grant_id,
                      EXISTS(SELECT 1 FROM authorized_sources AS source
                              WHERE source.space_id=i.space_id) AS is_source
                 FROM knowledge_item AS i
                WHERE i.id=?
                  AND (
                    (i.space_id=? )
                    OR (
                      i.lifecycle_status='active'
                      AND i.curation_status='published'
                      AND i.current_revision_id IS NOT NULL
                      AND (
                        EXISTS(SELECT 1 FROM authorized_sources AS source
                                WHERE source.space_id=i.space_id)
                        OR EXISTS(
                          SELECT 1 FROM authorized_overlays AS overlay
                           WHERE overlay.source_space_id=i.space_id
                             AND overlay.source_item_id=i.id
                             AND overlay.source_revision_id=i.current_revision_id
                        )
                      )
                    )
                  )
                LIMIT 1""",
            (
                json.dumps(sources, separators=(",", ":")),
                overlay_json,
                item,
                selected,
            ),
        ).fetchone()
        if row is None:
            return None
        source_space = _uuid(row["space_id"], "item source space ID")
        source_revision = _uuid(
            row["current_revision_id"], "item current revision ID"
        )
        overlay_grant = _optional_uuid(row["overlay_grant_id"], "overlay grant ID")
        is_source = row["is_source"] == 1
        if source_space == selected:
            origin: Literal["native", "inherited", "overlay"] = "native"
        elif is_source:
            origin = "inherited"
        elif overlay_grant is not None:
            origin = "overlay"
        else:
            raise ManagementReadIntegrityError(
                "Stored item escaped the authorized corpus closure."
            )
        return ManagementItemAuthorization(
            source_space_id=source_space,
            selected_view_space_id=selected,
            source_revision_id=source_revision,
            inherited=source_space != selected,
            origin=origin,
            overlay_grant_id=overlay_grant if origin == "overlay" else None,
            capabilities=("read",),
        )

    def graph(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        node_limit: int = 10_001,
        edge_limit: int = 20_001,
    ) -> ManagementGraph:
        connection = _connection(connection)
        space = _uuid(space_id, "space_id")
        if not 1 <= node_limit <= 10_001 or not 1 <= edge_limit <= 20_001:
            raise ValueError("graph limits are invalid.")
        generation_row = connection.execute(
            "SELECT generation FROM knowledge_space WHERE id=?", (space,)
        ).fetchone()
        if generation_row is None:
            raise ManagementReadIntegrityError("The authorized space is absent.")
        node_rows = connection.execute(
            """SELECT i.id,i.current_revision_id,r.title,i.lifecycle_status,
                      i.curation_status,i.row_version
                 FROM knowledge_item AS i
                 JOIN knowledge_revision AS r
                   ON r.id=i.current_revision_id AND r.item_id=i.id
                WHERE i.space_id=? AND i.lifecycle_status='active'
                  AND i.curation_status='published'
                  AND i.current_revision_id IS NOT NULL
                ORDER BY i.id LIMIT ?""",
            (space, node_limit),
        ).fetchall()
        edge_rows = connection.execute(
            """SELECT e.id,e.source_item_id,e.target_item_id,e.edge_type,
                      e.origin,e.source_evidence_revision_id,
                      e.target_evidence_revision_id,e.row_version,
                      e.needs_revalidation,e.confidence_ppm,
                      e.source_evidence_span_sha256,
                      e.target_evidence_span_sha256,
                      e.source_evidence_location_json,
                      e.target_evidence_location_json,
                      e.detector_generation,e.model_provider,e.model_name,
                      e.prompt_schema_version,e.scoring_version,
                      e.semantic_evidence_sha256
                 FROM knowledge_edge AS e
                 JOIN knowledge_item AS source
                   ON source.id=e.source_item_id AND source.space_id=e.space_id
                 JOIN knowledge_item AS target
                   ON target.id=e.target_item_id AND target.space_id=e.space_id
                WHERE e.space_id=? AND e.edge_status='active'
                  AND source.lifecycle_status='active'
                  AND source.curation_status='published'
                  AND target.lifecycle_status='active'
                  AND target.curation_status='published'
                ORDER BY e.id LIMIT ?""",
            (space, edge_limit),
        ).fetchall()
        if len(node_rows) == node_limit or len(edge_rows) == edge_limit:
            raise ManagementReadIntegrityError("The graph exceeds the bounded v1 response.")
        nodes = tuple(
            ManagementGraphNode(
                id=_uuid(row["id"], "graph node ID"),
                current_revision_id=_uuid(
                    row["current_revision_id"], "graph node revision ID"
                ),
                title=_text(row["title"], "graph node title", 500),
                lifecycle_status="active",
                curation_status="published",
                row_version=_integer(row["row_version"], "graph node row version", positive=True),
            )
            for row in node_rows
        )
        edges = tuple(
            ManagementGraphEdge(
                id=_uuid(row["id"], "graph edge ID"),
                source_item_id=_uuid(row["source_item_id"], "graph source item ID"),
                target_item_id=_uuid(row["target_item_id"], "graph target item ID"),
                edge_type=_enum(row["edge_type"], "graph edge type", _EDGE_TYPES),
                origin=_enum(row["origin"], "graph edge origin", _EDGE_ORIGINS),
                source_evidence_revision_id=_optional_uuid(
                    row["source_evidence_revision_id"], "graph source evidence revision ID"
                ),
                target_evidence_revision_id=_optional_uuid(
                    row["target_evidence_revision_id"], "graph target evidence revision ID"
                ),
                row_version=_integer(row["row_version"], "graph edge row version", positive=True),
                edge_status=(
                    "needs_revalidation"
                    if _integer(row["needs_revalidation"], "graph revalidation marker") == 1
                    else "active"
                ),
                **_graph_provenance(row),
            )
            for row in edge_rows
        )
        node_ids = {node.id for node in nodes}
        if any(
            edge.source_item_id not in node_ids or edge.target_item_id not in node_ids
            for edge in edges
        ):
            raise ManagementReadIntegrityError("The effective graph is not endpoint closed.")
        return ManagementGraph(
            space_id=space,
            generation=_integer(generation_row["generation"], "space generation"),
            nodes=nodes,
            edges=edges,
        )

    def graph_closure(
        self,
        connection: ManagedConnection,
        *,
        selected_space_id: str,
        source_space_ids: tuple[str, ...],
        overlays: tuple[dict[str, str], ...],
        closure_generation: int,
        corpus_closure_sha256: str,
        include_aggregate_edges: bool = False,
        node_limit: int = 10_001,
        edge_limit: int = 20_001,
    ) -> ManagementGraph:
        """Compose authorized local clusters and eligible LCA-home edges."""

        connection = _connection(connection)
        selected, sources, overlay_json, _ = _closure_json(
            selected_space_id=selected_space_id,
            source_space_ids=source_space_ids,
            overlays=overlays,
        )
        generation = _integer(closure_generation, "closure generation")
        if (
            not isinstance(corpus_closure_sha256, str)
            or _SHA_RE.fullmatch(corpus_closure_sha256) is None
        ):
            raise ValueError("corpus_closure_sha256 is invalid.")
        if not 1 <= node_limit <= 10_001 or not 1 <= edge_limit <= 20_001:
            raise ValueError("graph limits are invalid.")
        if not isinstance(include_aggregate_edges, bool):
            raise ValueError("include_aggregate_edges must be Boolean.")
        source_json = json.dumps(sources, separators=(",", ":"))
        node_rows = connection.execute(
            """WITH authorized_sources(space_id) AS (
                 SELECT value FROM json_each(?)
               ), authorized_overlays(
                 grant_id,source_space_id,source_item_id,source_revision_id
               ) AS (
                 SELECT json_extract(value,'$.grant_id'),
                        json_extract(value,'$.source_space_id'),
                        json_extract(value,'$.source_item_id'),
                        json_extract(value,'$.source_revision_id')
                   FROM json_each(?)
               )
               SELECT i.id,i.space_id,i.current_revision_id,r.title,
                      i.lifecycle_status,i.curation_status,i.row_version,
                      (SELECT overlay.grant_id
                         FROM authorized_overlays AS overlay
                        WHERE overlay.source_space_id=i.space_id
                          AND overlay.source_item_id=i.id
                          AND overlay.source_revision_id=i.current_revision_id
                        ORDER BY overlay.grant_id LIMIT 1) AS overlay_grant_id
                 FROM knowledge_item AS i
                 JOIN knowledge_revision AS r
                   ON r.id=i.current_revision_id AND r.item_id=i.id
                WHERE i.lifecycle_status='active'
                  AND i.curation_status='published'
                  AND i.current_revision_id IS NOT NULL
                  AND (
                    EXISTS(SELECT 1 FROM authorized_sources AS source
                            WHERE source.space_id=i.space_id)
                    OR EXISTS(
                      SELECT 1 FROM authorized_overlays AS overlay
                       WHERE overlay.source_space_id=i.space_id
                         AND overlay.source_item_id=i.id
                         AND overlay.source_revision_id=i.current_revision_id
                    )
                  )
                ORDER BY i.space_id,i.id LIMIT ?""",
            (source_json, overlay_json, node_limit),
        ).fetchall()
        edge_rows = connection.execute(
            """WITH authorized_sources(space_id) AS (
                 SELECT value FROM json_each(?)
               )
               SELECT e.id,e.space_id,e.source_item_id,e.target_item_id,
                      e.edge_type,e.origin,e.source_evidence_revision_id,
                      e.target_evidence_revision_id,e.row_version,
                      e.needs_revalidation,e.confidence_ppm,
                      e.source_evidence_span_sha256,
                      e.target_evidence_span_sha256,
                      e.source_evidence_location_json,
                      e.target_evidence_location_json,
                      e.detector_generation,e.model_provider,e.model_name,
                      e.prompt_schema_version,e.scoring_version,
                      e.semantic_evidence_sha256
                 FROM knowledge_edge AS e
                 JOIN knowledge_item AS source
                   ON source.id=e.source_item_id AND source.space_id=e.space_id
                 JOIN knowledge_item AS target
                   ON target.id=e.target_item_id AND target.space_id=e.space_id
                WHERE EXISTS(SELECT 1 FROM authorized_sources AS allowed
                              WHERE allowed.space_id=e.space_id)
                  AND e.edge_status='active'
                  AND source.lifecycle_status='active'
                  AND source.curation_status='published'
                  AND target.lifecycle_status='active'
                  AND target.curation_status='published'
                ORDER BY e.space_id,e.id LIMIT ?""",
            (source_json, edge_limit),
        ).fetchall()
        if len(node_rows) == node_limit or len(edge_rows) == edge_limit:
            raise ManagementReadIntegrityError(
                "The aggregate local graph exceeds the bounded response."
            )
        aggregate_rows: tuple[object, ...] | list[object] = ()
        selected_kind_row = connection.execute(
            "SELECT space_kind FROM knowledge_space WHERE id=?",
            (selected,),
        ).fetchone()
        if selected_kind_row is None:
            raise ManagementReadIntegrityError("The selected graph space is absent.")
        selected_kind = str(selected_kind_row["space_kind"])
        if include_aggregate_edges and selected_kind in {"team", "responsibility"}:
            remaining = edge_limit - len(edge_rows)
            aggregate_rows = connection.execute(
                """WITH authorized_sources(space_id) AS (
                     SELECT value FROM json_each(?)
                   )
                   SELECT edge.id,edge.home_space_id,edge.source_space_id,
                          edge.target_space_id,edge.source_item_id,
                          edge.target_item_id,edge.edge_type,edge.direction,
                          edge.edge_status,edge.origin,edge.source_revision_id,
                          edge.target_revision_id,edge.confidence_ppm,
                          edge.source_evidence_span_sha256,
                          edge.target_evidence_span_sha256,
                          edge.source_evidence_location_json,
                          edge.target_evidence_location_json,edge.row_version,
                          edge.detector_generation,edge.model_provider,
                          edge.model_name,edge.prompt_schema_version,
                          edge.scoring_version,NULL AS semantic_evidence_sha256
                     FROM aggregate_knowledge_edge AS edge
                     JOIN knowledge_space AS home ON home.id=edge.home_space_id
                     JOIN knowledge_item AS source
                       ON source.id=edge.source_item_id
                      AND source.space_id=edge.source_space_id
                     JOIN knowledge_item AS target
                       ON target.id=edge.target_item_id
                      AND target.space_id=edge.target_space_id
                    WHERE edge.edge_status IN ('active','needs_revalidation')
                      AND home.status='active'
                      AND home.space_kind IN ('team','responsibility')
                      AND EXISTS(
                        SELECT 1 FROM authorized_sources AS allowed
                         WHERE allowed.space_id=edge.home_space_id
                      )
                      AND EXISTS(
                        SELECT 1 FROM authorized_sources AS allowed
                         WHERE allowed.space_id=edge.source_space_id
                      )
                      AND EXISTS(
                        SELECT 1 FROM authorized_sources AS allowed
                         WHERE allowed.space_id=edge.target_space_id
                      )
                      AND source.lifecycle_status='active'
                      AND source.curation_status='published'
                      AND (edge.edge_status='needs_revalidation' OR (
                        source.current_revision_id=edge.source_revision_id
                        AND source.row_version=edge.source_item_row_version
                      ))
                      AND target.lifecycle_status='active'
                      AND target.curation_status='published'
                      AND (edge.edge_status='needs_revalidation' OR (
                        target.current_revision_id=edge.target_revision_id
                        AND target.row_version=edge.target_item_row_version
                      ))
                      AND edge.home_org_unit_id IS (
                        WITH RECURSIVE
                        source_ancestors(id,depth) AS (
                          SELECT owner_org_unit_id,0 FROM knowledge_space
                           WHERE id=edge.source_space_id
                          UNION ALL
                          SELECT unit.parent_id,source_ancestors.depth+1
                            FROM org_unit AS unit JOIN source_ancestors
                              ON unit.id=source_ancestors.id
                           WHERE unit.parent_id IS NOT NULL
                        ),
                        target_ancestors(id,depth) AS (
                          SELECT owner_org_unit_id,0 FROM knowledge_space
                           WHERE id=edge.target_space_id
                          UNION ALL
                          SELECT unit.parent_id,target_ancestors.depth+1
                            FROM org_unit AS unit JOIN target_ancestors
                              ON unit.id=target_ancestors.id
                           WHERE unit.parent_id IS NOT NULL
                        )
                        SELECT source_ancestors.id
                          FROM source_ancestors JOIN target_ancestors
                            ON target_ancestors.id=source_ancestors.id
                          JOIN org_unit AS unit ON unit.id=source_ancestors.id
                         WHERE unit.status='active'
                           AND unit.kind IN ('team','responsibility')
                         ORDER BY source_ancestors.depth+target_ancestors.depth,
                                  source_ancestors.id
                         LIMIT 1
                      )
                    ORDER BY edge.home_space_id,edge.id LIMIT ?""",
                (source_json, remaining),
            ).fetchall()
            if len(aggregate_rows) == remaining:
                raise ManagementReadIntegrityError(
                    "The aggregate graph exceeds the bounded response."
                )
        source_set = frozenset(sources)
        nodes: list[ManagementGraphNode] = []
        for row in node_rows:
            source_space = _uuid(row["space_id"], "graph node source space ID")
            overlay_grant = _optional_uuid(
                row["overlay_grant_id"], "graph overlay grant ID"
            )
            if source_space == selected:
                item_origin: Literal["native", "inherited", "overlay"] = "native"
            elif source_space in source_set:
                item_origin = "inherited"
            elif overlay_grant is not None:
                item_origin = "overlay"
            else:
                raise ManagementReadIntegrityError(
                    "A graph node escaped the authorized corpus closure."
                )
            nodes.append(
                ManagementGraphNode(
                    id=_uuid(row["id"], "graph node ID"),
                    current_revision_id=_uuid(
                        row["current_revision_id"], "graph node revision ID"
                    ),
                    title=_text(row["title"], "graph node title", 500),
                    lifecycle_status="active",
                    curation_status="published",
                    row_version=_integer(
                        row["row_version"], "graph node row version", positive=True
                    ),
                    space_id=source_space,
                    selected_view_space_id=selected,
                    inherited=source_space != selected,
                    origin=item_origin,
                    overlay_grant_id=(
                        overlay_grant if item_origin == "overlay" else None
                    ),
                )
            )
        node_ids = {node.id for node in nodes if node.origin != "overlay"}
        edges: list[ManagementGraphEdge] = []
        for row in edge_rows:
            source_space = _uuid(row["space_id"], "graph edge source space ID")
            if source_space not in source_set:
                raise ManagementReadIntegrityError(
                    "A graph edge escaped the authorized source closure."
                )
            source_item = _uuid(row["source_item_id"], "graph source item ID")
            target_item = _uuid(row["target_item_id"], "graph target item ID")
            if source_item not in node_ids or target_item not in node_ids:
                raise ManagementReadIntegrityError(
                    "The effective closure graph is not endpoint closed."
                )
            edges.append(
                ManagementGraphEdge(
                    id=_uuid(row["id"], "graph edge ID"),
                    source_item_id=source_item,
                    target_item_id=target_item,
                    edge_type=_enum(row["edge_type"], "graph edge type", _EDGE_TYPES),
                    origin=_enum(row["origin"], "graph edge origin", _EDGE_ORIGINS),
                    source_evidence_revision_id=_optional_uuid(
                        row["source_evidence_revision_id"],
                        "graph source evidence revision ID",
                    ),
                    target_evidence_revision_id=_optional_uuid(
                        row["target_evidence_revision_id"],
                        "graph target evidence revision ID",
                    ),
                    row_version=_integer(
                        row["row_version"], "graph edge row version", positive=True
                    ),
                    space_id=source_space,
                    selected_view_space_id=selected,
                    inherited=source_space != selected,
                    edge_scope="local",
                    home_space_id=source_space,
                    source_space_id=source_space,
                    target_space_id=source_space,
                    edge_status=(
                        "needs_revalidation"
                        if _integer(row["needs_revalidation"], "graph revalidation marker") == 1
                        else "active"
                    ),
                    **_graph_provenance(row),
                )
            )
        for row in aggregate_rows:
            source_space = _uuid(
                row["source_space_id"], "aggregate source space ID"
            )
            target_space = _uuid(
                row["target_space_id"], "aggregate target space ID"
            )
            home_space = _uuid(row["home_space_id"], "aggregate home space ID")
            if not {source_space, target_space, home_space}.issubset(source_set):
                raise ManagementReadIntegrityError(
                    "An aggregate edge escaped the authorized source closure."
                )
            source_item = _uuid(
                row["source_item_id"], "aggregate source item ID"
            )
            target_item = _uuid(
                row["target_item_id"], "aggregate target item ID"
            )
            if source_item not in node_ids or target_item not in node_ids:
                raise ManagementReadIntegrityError(
                    "The aggregate closure graph is not endpoint closed."
                )
            edges.append(
                ManagementGraphEdge(
                    id=_uuid(row["id"], "aggregate edge ID"),
                    source_item_id=source_item,
                    target_item_id=target_item,
                    edge_type=_enum(
                        row["edge_type"], "aggregate edge type", _EDGE_TYPES
                    ),
                    origin=_enum(
                        row["origin"], "aggregate edge origin", _EDGE_ORIGINS
                    ),
                    source_evidence_revision_id=_uuid(
                        row["source_revision_id"],
                        "aggregate source evidence revision ID",
                    ),
                    target_evidence_revision_id=_uuid(
                        row["target_revision_id"],
                        "aggregate target evidence revision ID",
                    ),
                    row_version=_integer(
                        row["row_version"],
                        "aggregate edge row version",
                        positive=True,
                    ),
                    space_id=home_space,
                    selected_view_space_id=selected,
                    inherited=home_space != selected,
                    edge_scope="aggregate",
                    home_space_id=home_space,
                    source_space_id=source_space,
                    target_space_id=target_space,
                    direction=_enum(
                        row["direction"],
                        "aggregate edge direction",
                        frozenset(
                            {"source_to_target", "target_to_source", "symmetric"}
                        ),
                    ),
                    edge_status=_enum(
                        row["edge_status"],
                        "aggregate edge status",
                        frozenset({"active", "needs_revalidation"}),
                    ),
                    **_graph_provenance(row),
                )
            )
        return ManagementGraph(
            space_id=selected,
            generation=generation,
            nodes=tuple(nodes),
            edges=tuple(edges),
            corpus_closure_sha256=corpus_closure_sha256,
        )

    def projection_status(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
    ) -> ManagementProjectionStatus:
        connection = _connection(connection)
        space = _uuid(space_id, "space_id")
        space_row = connection.execute(
            "SELECT generation,updated_at FROM knowledge_space WHERE id=?", (space,)
        ).fetchone()
        if space_row is None:
            raise ManagementReadIntegrityError("The authorized space is absent.")
        row = connection.execute(
            """SELECT desired_generation,built_generation,checksum_sha256,
                      status,last_error_json,updated_at,built_at
                 FROM projection_state
                WHERE projection_name='markdown' AND space_id=?""",
            (space,),
        ).fetchone()
        if row is None:
            return ManagementProjectionStatus(
                space_id=space,
                projection_name="markdown",
                desired_generation=_integer(space_row["generation"], "space generation"),
                built_generation=0,
                status="idle",
                checksum_sha256=None,
                last_error=None,
                updated_at_ms=_integer(space_row["updated_at"], "space updated timestamp"),
                built_at_ms=None,
            )
        status = row["status"]
        if status not in {"idle", "building", "ready", "error"}:
            raise ManagementReadIntegrityError("Stored projection status is invalid.")
        checksum = row["checksum_sha256"]
        if checksum is not None and (
            not isinstance(checksum, str) or _SHA_RE.fullmatch(checksum) is None
        ):
            raise ManagementReadIntegrityError("Stored projection checksum is invalid.")
        desired_generation = _integer(row["desired_generation"], "desired generation")
        built_generation = _integer(row["built_generation"], "built generation")
        if built_generation > desired_generation or desired_generation != _integer(
            space_row["generation"], "space generation"
        ):
            raise ManagementReadIntegrityError("Stored projection generations are invalid.")
        last_error = (
            None
            if row["last_error_json"] is None
            else _json_object(row["last_error_json"], "projection error")
        )
        built_at = (
            None
            if row["built_at"] is None
            else _integer(row["built_at"], "projection built timestamp")
        )
        if (
            (status == "error") != (last_error is not None)
            or (status == "ready" and (built_generation != desired_generation or built_at is None))
        ):
            raise ManagementReadIntegrityError("Stored projection state is incoherent.")
        return ManagementProjectionStatus(
            space_id=space,
            projection_name="markdown",
            desired_generation=desired_generation,
            built_generation=built_generation,
            status=status,
            checksum_sha256=checksum,
            last_error=last_error,
            updated_at_ms=_integer(row["updated_at"], "projection updated timestamp"),
            built_at_ms=built_at,
        )

    def list_findings(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        before_updated_at_ms: int | None = None,
        before_finding_id: str | None = None,
        limit: int = 51,
    ) -> tuple[ManagementFindingRecord, ...]:
        connection = _connection(connection)
        space = _uuid(space_id, "space_id")
        limit = _page_limit(limit)
        if (before_updated_at_ms is None) != (before_finding_id is None):
            raise ValueError("Both finding cursor fields must be supplied together.")
        clauses = ["space_id=?"]
        parameters: list[object] = [space]
        if before_updated_at_ms is not None:
            before_time = _integer(before_updated_at_ms, "finding cursor timestamp")
            before_id = _uuid(before_finding_id, "finding cursor ID")
            clauses.append("(updated_at<? OR (updated_at=? AND id<?))")
            parameters.extend((before_time, before_time, before_id))
        parameters.append(limit)
        rows = connection.execute(
            f"""SELECT id,space_id,finding_kind,risk_tier,severity,status,
                       required_reviewer,visibility_effect,proposed_action,
                       source_item_id,source_revision_id,target_item_id,
                       target_revision_id,detector_name,detector_version,
                       rule_version,model_id,prompt_version,confidence,
                       evidence_json,row_version,created_at,updated_at
                  FROM curation_finding
                 WHERE {' AND '.join(clauses)}
                 ORDER BY updated_at DESC,id DESC LIMIT ?""",
            tuple(parameters),
        ).fetchall()
        return tuple(
            ManagementFindingRecord(
                id=_uuid(row["id"], "finding ID"),
                space_id=_uuid(row["space_id"], "finding space ID"),
                finding_kind=_enum(row["finding_kind"], "finding kind", _FINDING_KINDS),
                risk_tier=_enum(row["risk_tier"], "finding risk tier", _RISK_TIERS),
                severity=_enum(row["severity"], "finding severity", _SEVERITIES),
                status=_enum(row["status"], "finding status", _FINDING_STATUSES),
                required_reviewer=_enum(
                    row["required_reviewer"], "finding reviewer", _REVIEWERS
                ),
                visibility_effect=_enum(
                    row["visibility_effect"], "finding visibility effect", _VISIBILITY_EFFECTS
                ),
                proposed_action=_enum(
                    row["proposed_action"], "finding proposed action", _PROPOSED_ACTIONS
                ),
                source_item_id=_uuid(row["source_item_id"], "finding source item ID"),
                source_revision_id=_uuid(
                    row["source_revision_id"], "finding source revision ID"
                ),
                target_item_id=_optional_uuid(
                    row["target_item_id"], "finding target item ID"
                ),
                target_revision_id=_optional_uuid(
                    row["target_revision_id"], "finding target revision ID"
                ),
                detector_name=_text(row["detector_name"], "detector name", 120),
                detector_version=_text(
                    row["detector_version"], "detector version", 120
                ),
                rule_version=_text(row["rule_version"], "rule version", 120),
                model_id=(
                    None if row["model_id"] is None else _text(row["model_id"], "model ID", 200)
                ),
                prompt_version=(
                    None
                    if row["prompt_version"] is None
                    else _text(row["prompt_version"], "prompt version", 200)
                ),
                confidence=_confidence(row["confidence"]),
                evidence=_json_object(row["evidence_json"], "finding evidence"),
                row_version=_integer(row["row_version"], "finding row version", positive=True),
                created_at_ms=_integer(row["created_at"], "finding created timestamp"),
                updated_at_ms=_integer(row["updated_at"], "finding updated timestamp"),
            )
            for row in rows
        )

    def list_findings_closure(
        self,
        connection: ManagedConnection,
        *,
        selected_space_id: str,
        source_space_ids: tuple[str, ...],
        overlays: tuple[dict[str, str], ...],
        before_updated_at_ms: int | None = None,
        before_finding_id: str | None = None,
        limit: int = 51,
    ) -> tuple[ManagementFindingRecord, ...]:
        connection = _connection(connection)
        selected, sources, overlay_json, _ = _closure_json(
            selected_space_id=selected_space_id,
            source_space_ids=source_space_ids,
            overlays=overlays,
        )
        limit = _page_limit(limit)
        if (before_updated_at_ms is None) != (before_finding_id is None):
            raise ValueError("Both finding cursor fields must be supplied together.")
        cursor_sql = ""
        parameters: list[object] = [
            json.dumps(sources, separators=(",", ":")),
            overlay_json,
            selected,
        ]
        if before_updated_at_ms is not None:
            before_time = _integer(before_updated_at_ms, "finding cursor timestamp")
            before_id = _uuid(before_finding_id, "finding cursor ID")
            cursor_sql = "AND (f.updated_at<? OR (f.updated_at=? AND f.id<?))"
            parameters.extend((before_time, before_time, before_id))
        parameters.append(limit)
        rows = connection.execute(
            f"""WITH authorized_sources(space_id) AS (
                   SELECT value FROM json_each(?)
                 ), authorized_overlays(
                   grant_id,source_space_id,source_item_id,source_revision_id
                 ) AS (
                   SELECT json_extract(value,'$.grant_id'),
                          json_extract(value,'$.source_space_id'),
                          json_extract(value,'$.source_item_id'),
                          json_extract(value,'$.source_revision_id')
                     FROM json_each(?)
                 )
                 SELECT f.id,f.space_id,f.finding_kind,f.risk_tier,f.severity,
                        f.status,f.required_reviewer,f.visibility_effect,
                        f.proposed_action,f.source_item_id,f.source_revision_id,
                        f.target_item_id,f.target_revision_id,f.detector_name,
                        f.detector_version,f.rule_version,f.model_id,
                        f.prompt_version,f.confidence,f.evidence_json,
                        f.row_version,f.created_at,f.updated_at,
                        (SELECT overlay.grant_id
                           FROM authorized_overlays AS overlay
                          WHERE overlay.source_space_id=f.space_id
                            AND overlay.source_item_id=f.source_item_id
                            AND overlay.source_revision_id=f.source_revision_id
                          ORDER BY overlay.grant_id LIMIT 1) AS overlay_grant_id
                   FROM curation_finding AS f
                   JOIN knowledge_item AS source_item
                     ON source_item.id=f.source_item_id
                    AND source_item.space_id=f.space_id
                   LEFT JOIN knowledge_item AS target_item
                     ON target_item.id=f.target_item_id
                    AND target_item.space_id=f.space_id
                  WHERE (
                    f.space_id=?
                    OR (
                      EXISTS(SELECT 1 FROM authorized_sources AS source
                              WHERE source.space_id=f.space_id)
                      AND source_item.lifecycle_status='active'
                      AND source_item.curation_status='published'
                      AND (f.target_item_id IS NULL OR (
                        target_item.lifecycle_status='active'
                        AND target_item.curation_status='published'
                      ))
                    )
                    OR (
                      f.target_item_id IS NULL
                      AND source_item.lifecycle_status='active'
                      AND source_item.curation_status='published'
                      AND source_item.current_revision_id=f.source_revision_id
                      AND EXISTS(
                        SELECT 1 FROM authorized_overlays AS overlay
                         WHERE overlay.source_space_id=f.space_id
                           AND overlay.source_item_id=f.source_item_id
                           AND overlay.source_revision_id=f.source_revision_id
                      )
                    )
                  )
                  {cursor_sql}
                  ORDER BY f.updated_at DESC,f.id DESC LIMIT ?""",
            tuple(parameters),
        ).fetchall()
        source_set = frozenset(sources)
        result: list[ManagementFindingRecord] = []
        for row in rows:
            source_space = _uuid(row["space_id"], "finding space ID")
            overlay_grant = _optional_uuid(
                row["overlay_grant_id"], "finding overlay grant ID"
            )
            origin: Literal["native", "inherited", "overlay"] = (
                "native"
                if source_space == selected
                else "inherited"
                if source_space in source_set
                else "overlay"
            )
            if origin == "overlay" and overlay_grant is None:
                raise ManagementReadIntegrityError(
                    "A finding escaped the authorized corpus closure."
                )
            result.append(
                ManagementFindingRecord(
                    id=_uuid(row["id"], "finding ID"),
                    space_id=source_space,
                    finding_kind=_enum(
                        row["finding_kind"], "finding kind", _FINDING_KINDS
                    ),
                    risk_tier=_enum(
                        row["risk_tier"], "finding risk tier", _RISK_TIERS
                    ),
                    severity=_enum(row["severity"], "finding severity", _SEVERITIES),
                    status=_enum(row["status"], "finding status", _FINDING_STATUSES),
                    required_reviewer=_enum(
                        row["required_reviewer"], "finding reviewer", _REVIEWERS
                    ),
                    visibility_effect=_enum(
                        row["visibility_effect"],
                        "finding visibility effect",
                        _VISIBILITY_EFFECTS,
                    ),
                    proposed_action=_enum(
                        row["proposed_action"],
                        "finding proposed action",
                        _PROPOSED_ACTIONS,
                    ),
                    source_item_id=_uuid(
                        row["source_item_id"], "finding source item ID"
                    ),
                    source_revision_id=_uuid(
                        row["source_revision_id"], "finding source revision ID"
                    ),
                    target_item_id=_optional_uuid(
                        row["target_item_id"], "finding target item ID"
                    ),
                    target_revision_id=_optional_uuid(
                        row["target_revision_id"], "finding target revision ID"
                    ),
                    detector_name=_text(row["detector_name"], "detector name", 120),
                    detector_version=_text(
                        row["detector_version"], "detector version", 120
                    ),
                    rule_version=_text(row["rule_version"], "rule version", 120),
                    model_id=(
                        None
                        if row["model_id"] is None
                        else _text(row["model_id"], "model ID", 200)
                    ),
                    prompt_version=(
                        None
                        if row["prompt_version"] is None
                        else _text(row["prompt_version"], "prompt version", 200)
                    ),
                    confidence=_confidence(row["confidence"]),
                    evidence=_json_object(row["evidence_json"], "finding evidence"),
                    row_version=_integer(
                        row["row_version"], "finding row version", positive=True
                    ),
                    created_at_ms=_integer(
                        row["created_at"], "finding created timestamp"
                    ),
                    updated_at_ms=_integer(
                        row["updated_at"], "finding updated timestamp"
                    ),
                    selected_view_space_id=selected,
                    inherited=source_space != selected,
                    origin=origin,
                    overlay_grant_id=(
                        overlay_grant if origin == "overlay" else None
                    ),
                )
            )
        return tuple(result)

    def list_activity(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        before_occurred_at_ms: int | None = None,
        before_event_uuid: str | None = None,
        limit: int = 51,
    ) -> tuple[ManagementActivityRecord, ...]:
        """Return a sanitized audit projection, never raw audit JSON or request data."""

        connection = _connection(connection)
        space = _uuid(space_id, "space_id")
        limit = _page_limit(limit)
        if (before_occurred_at_ms is None) != (before_event_uuid is None):
            raise ValueError("Both activity cursor fields must be supplied together.")
        clauses = ["a.space_id=?"]
        parameters: list[object] = [space]
        if before_occurred_at_ms is not None:
            before_time = _integer(
                before_occurred_at_ms, "activity cursor timestamp"
            )
            before_uuid = _uuid(before_event_uuid, "activity cursor event UUID")
            clauses.append(
                "(a.occurred_at<? OR (a.occurred_at=? AND a.event_uuid<?))"
            )
            parameters.extend((before_time, before_time, before_uuid))
        parameters.append(limit)
        rows = connection.execute(
            f"""SELECT a.event_uuid,a.space_id,a.occurred_at,a.action,a.outcome,
                       a.actor_kind,a.actor_user_id,a.actor_display_name,
                       a.actor_role_snapshot,a.item_id,a.base_revision_id,
                       a.new_revision_id,a.reason,i.space_id AS item_space_id,
                       br.item_id AS base_revision_item_id,
                       nr.item_id AS new_revision_item_id
                  FROM audit_event AS a
                  LEFT JOIN knowledge_item AS i ON i.id=a.item_id
                  LEFT JOIN knowledge_revision AS br ON br.id=a.base_revision_id
                  LEFT JOIN knowledge_revision AS nr ON nr.id=a.new_revision_id
                 WHERE {' AND '.join(clauses)}
                 ORDER BY a.occurred_at DESC,a.event_uuid DESC LIMIT ?""",
            tuple(parameters),
        ).fetchall()
        result: list[ManagementActivityRecord] = []
        for row in rows:
            event_uuid = _uuid(row["event_uuid"], "activity event UUID")
            row_space = _uuid(row["space_id"], "activity space ID")
            action = _activity_text(row["action"], "activity action", 120)
            item_id = _optional_uuid(row["item_id"], "activity item ID")
            base_revision_id = _optional_uuid(
                row["base_revision_id"], "activity base revision ID"
            )
            new_revision_id = _optional_uuid(
                row["new_revision_id"], "activity new revision ID"
            )
            if row_space != space:
                raise ManagementReadIntegrityError(
                    "Stored activity escaped the authorized space."
                )
            if action in _GRAPH_LEVEL_ACTIVITY_ACTIONS:
                if item_id is not None and row["item_space_id"] is None:
                    raise ManagementReadIntegrityError(
                        "Stored graph activity item does not exist."
                    )
                if (
                    base_revision_id is not None
                    and row["base_revision_item_id"] is None
                ) or (
                    new_revision_id is not None
                    and row["new_revision_item_id"] is None
                ):
                    raise ManagementReadIntegrityError(
                        "Stored graph activity revision evidence does not exist."
                    )
                # Edge events can bind two items and, for aggregate edges, an item
                # outside the graph's home space.  The management activity schema
                # models single-item mutations, so expose these as graph-level
                # events instead of leaking misleading or cross-space item IDs.
                item_id = None
                base_revision_id = None
                new_revision_id = None
            else:
                if item_id is not None and row["item_space_id"] != space:
                    raise ManagementReadIntegrityError(
                        "Stored activity item is outside the authorized space."
                    )
                if (
                    base_revision_id is not None
                    and row["base_revision_item_id"] != item_id
                ) or (
                    new_revision_id is not None
                    and row["new_revision_item_id"] != item_id
                ):
                    raise ManagementReadIntegrityError(
                        "Stored activity revision evidence is not item-bound."
                    )
            actor_kind = _enum(
                row["actor_kind"], "activity actor kind", frozenset({"user", "system"})
            )
            actor_user_id = row["actor_user_id"]
            if actor_user_id is not None and (
                not isinstance(actor_user_id, str)
                or _USER_RE.fullmatch(actor_user_id) is None
            ):
                raise ManagementReadIntegrityError(
                    "Stored activity actor user ID is invalid."
                )
            if (actor_kind == "user") != (actor_user_id is not None):
                raise ManagementReadIntegrityError(
                    "Stored activity actor identity is incoherent."
                )
            result.append(
                ManagementActivityRecord(
                    event_uuid=event_uuid,
                    space_id=row_space,
                    occurred_at_ms=_integer(
                        row["occurred_at"], "activity occurred timestamp"
                    ),
                    action=action,
                    outcome=_activity_text(row["outcome"], "activity outcome", 80),
                    actor_kind=actor_kind,  # type: ignore[arg-type]
                    actor_user_id=actor_user_id,
                    actor_display_name=_activity_text(
                        row["actor_display_name"], "activity actor display name", 200
                    ),
                    actor_role_snapshot=_activity_text(
                        row["actor_role_snapshot"], "activity role snapshot", 200
                    ),
                    item_id=item_id,
                    base_revision_id=base_revision_id,
                    new_revision_id=new_revision_id,
                    reason=_optional_activity_text(
                        row["reason"], "activity reason", 2000
                    ),
                )
            )
        return tuple(result)

    def list_activity_closure(
        self,
        connection: ManagedConnection,
        *,
        selected_space_id: str,
        source_space_ids: tuple[str, ...],
        overlays: tuple[dict[str, str], ...],
        before_occurred_at_ms: int | None = None,
        before_event_uuid: str | None = None,
        limit: int = 51,
    ) -> tuple[ManagementActivityRecord, ...]:
        """Return a sanitized, origin-preserving aggregate audit projection."""

        connection = _connection(connection)
        selected, sources, overlay_json, _ = _closure_json(
            selected_space_id=selected_space_id,
            source_space_ids=source_space_ids,
            overlays=overlays,
        )
        limit = _page_limit(limit)
        if (before_occurred_at_ms is None) != (before_event_uuid is None):
            raise ValueError("Both activity cursor fields must be supplied together.")
        cursor_sql = ""
        parameters: list[object] = [
            json.dumps(sources, separators=(",", ":")),
            overlay_json,
            selected,
        ]
        if before_occurred_at_ms is not None:
            before_time = _integer(before_occurred_at_ms, "activity cursor timestamp")
            before_uuid = _uuid(before_event_uuid, "activity cursor event UUID")
            cursor_sql = (
                "AND (a.occurred_at<? OR "
                "(a.occurred_at=? AND a.event_uuid<?))"
            )
            parameters.extend((before_time, before_time, before_uuid))
        parameters.append(limit)
        rows = connection.execute(
            f"""WITH authorized_sources(space_id) AS (
                   SELECT value FROM json_each(?)
                 ), authorized_overlays(
                   grant_id,source_space_id,source_item_id,source_revision_id
                 ) AS (
                   SELECT json_extract(value,'$.grant_id'),
                          json_extract(value,'$.source_space_id'),
                          json_extract(value,'$.source_item_id'),
                          json_extract(value,'$.source_revision_id')
                     FROM json_each(?)
                 )
                 SELECT a.event_uuid,a.space_id,a.occurred_at,a.action,a.outcome,
                        a.actor_kind,a.actor_user_id,a.actor_display_name,
                        a.actor_role_snapshot,a.item_id,a.base_revision_id,
                        a.new_revision_id,a.reason,i.space_id AS item_space_id,
                        br.item_id AS base_revision_item_id,
                        nr.item_id AS new_revision_item_id,
                        (SELECT overlay.grant_id
                           FROM authorized_overlays AS overlay
                          WHERE overlay.source_space_id=a.space_id
                            AND overlay.source_item_id=a.item_id
                            AND overlay.source_revision_id=i.current_revision_id
                          ORDER BY overlay.grant_id LIMIT 1) AS overlay_grant_id
                   FROM audit_event AS a
                   LEFT JOIN knowledge_item AS i ON i.id=a.item_id
                   LEFT JOIN knowledge_revision AS br ON br.id=a.base_revision_id
                   LEFT JOIN knowledge_revision AS nr ON nr.id=a.new_revision_id
                  WHERE (
                    a.space_id=?
                    OR (
                      a.item_id IS NOT NULL
                      AND i.space_id=a.space_id
                      AND i.lifecycle_status='active'
                      AND i.curation_status='published'
                      AND EXISTS(SELECT 1 FROM authorized_sources AS source
                                  WHERE source.space_id=a.space_id)
                    )
                    OR (
                      a.item_id IS NOT NULL
                      AND i.space_id=a.space_id
                      AND i.lifecycle_status='active'
                      AND i.curation_status='published'
                      AND EXISTS(
                        SELECT 1 FROM authorized_overlays AS overlay
                         WHERE overlay.source_space_id=a.space_id
                           AND overlay.source_item_id=a.item_id
                           AND overlay.source_revision_id=i.current_revision_id
                      )
                    )
                  )
                  {cursor_sql}
                  ORDER BY a.occurred_at DESC,a.event_uuid DESC LIMIT ?""",
            tuple(parameters),
        ).fetchall()
        source_set = frozenset(sources)
        result: list[ManagementActivityRecord] = []
        for row in rows:
            event_uuid = _uuid(row["event_uuid"], "activity event UUID")
            source_space = _uuid(row["space_id"], "activity space ID")
            action = _activity_text(row["action"], "activity action", 120)
            item_id = _optional_uuid(row["item_id"], "activity item ID")
            base_revision_id = _optional_uuid(
                row["base_revision_id"], "activity base revision ID"
            )
            new_revision_id = _optional_uuid(
                row["new_revision_id"], "activity new revision ID"
            )
            if action in _GRAPH_LEVEL_ACTIVITY_ACTIONS:
                if item_id is not None and row["item_space_id"] is None:
                    raise ManagementReadIntegrityError(
                        "Stored graph activity item does not exist."
                    )
                if (
                    base_revision_id is not None
                    and row["base_revision_item_id"] is None
                ) or (
                    new_revision_id is not None
                    and row["new_revision_item_id"] is None
                ):
                    raise ManagementReadIntegrityError(
                        "Stored graph activity revision evidence does not exist."
                    )
                item_id = None
                base_revision_id = None
                new_revision_id = None
            else:
                if item_id is not None and row["item_space_id"] != source_space:
                    raise ManagementReadIntegrityError(
                        "Stored activity item is outside its source space."
                    )
                if (
                    base_revision_id is not None
                    and row["base_revision_item_id"] != item_id
                ) or (
                    new_revision_id is not None
                    and row["new_revision_item_id"] != item_id
                ):
                    raise ManagementReadIntegrityError(
                        "Stored activity revision evidence is not item-bound."
                    )
            overlay_grant = _optional_uuid(
                row["overlay_grant_id"], "activity overlay grant ID"
            )
            origin: Literal["native", "inherited", "overlay"] = (
                "native"
                if source_space == selected
                else "inherited"
                if source_space in source_set
                else "overlay"
            )
            if origin == "overlay" and overlay_grant is None:
                raise ManagementReadIntegrityError(
                    "An activity event escaped the authorized corpus closure."
                )
            actor_kind = _enum(
                row["actor_kind"],
                "activity actor kind",
                frozenset({"user", "system"}),
            )
            actor_user_id = row["actor_user_id"]
            if actor_user_id is not None and (
                not isinstance(actor_user_id, str)
                or _USER_RE.fullmatch(actor_user_id) is None
            ):
                raise ManagementReadIntegrityError(
                    "Stored activity actor user ID is invalid."
                )
            if (actor_kind == "user") != (actor_user_id is not None):
                raise ManagementReadIntegrityError(
                    "Stored activity actor identity is incoherent."
                )
            result.append(
                ManagementActivityRecord(
                    event_uuid=event_uuid,
                    space_id=source_space,
                    occurred_at_ms=_integer(
                        row["occurred_at"], "activity occurred timestamp"
                    ),
                    action=action,
                    outcome=_activity_text(row["outcome"], "activity outcome", 80),
                    actor_kind=actor_kind,  # type: ignore[arg-type]
                    actor_user_id=actor_user_id,
                    actor_display_name=_activity_text(
                        row["actor_display_name"],
                        "activity actor display name",
                        200,
                    ),
                    actor_role_snapshot=_activity_text(
                        row["actor_role_snapshot"],
                        "activity role snapshot",
                        200,
                    ),
                    item_id=item_id,
                    base_revision_id=base_revision_id,
                    new_revision_id=new_revision_id,
                    reason=_optional_activity_text(
                        row["reason"], "activity reason", 2000
                    ),
                    selected_view_space_id=selected,
                    inherited=source_space != selected,
                    origin=origin,
                    overlay_grant_id=(
                        overlay_grant if origin == "overlay" else None
                    ),
                )
            )
        return tuple(result)


__all__ = [
    "ManagementActivityRecord",
    "ManagementFindingRecord",
    "ManagementGraph",
    "ManagementGraphEdge",
    "ManagementGraphNode",
    "ManagementItemAuthorization",
    "ManagementItemRecord",
    "ManagementProjectionStatus",
    "ManagementReadIntegrityError",
    "ManagementReadRepository",
]

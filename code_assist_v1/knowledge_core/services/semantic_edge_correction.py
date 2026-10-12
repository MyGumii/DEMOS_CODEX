"""Exact-authority semantic-edge suppression and suppression-lift commands."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from uuid import UUID, uuid5

from ..db import (
    ConnectionFactory,
    ManagedConnection,
    TransactionMode,
    db_now_ms,
    transaction,
)
from ..errors import KnowledgeCoreError
from ..repositories.aggregate_graph import AggregateGraphRepository
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.users import (
    OperationIdempotencyConflictError,
    OperationInProgressError,
    UserRepository,
)
from ..scope import (
    Capability,
    ScopeAuthenticationRequiredError,
    ScopeForbiddenError,
    ScopeNotFoundError,
    ScopeResolver,
    ScopeStaleError,
    SessionPrincipal,
)


_NAMESPACE = UUID("376cf0c8-042b-4ac4-a7df-f4154e13e474")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_BREAK_GLASS_CONFIRMATION = "SUPPRESS ORGANIZATIONAL SEMANTIC EDGE"
_REQUEST_META_ALLOWLIST = frozenset(
    {"remote_addr", "method", "path", "host", "user_agent"}
)
_SENSITIVE_META_FRAGMENTS = (
    "authorization",
    "cookie",
    "csrf",
    "lease",
    "password",
    "secret",
    "token",
)


class SemanticEdgeCorrectionError(KnowledgeCoreError):
    code = "STATE_CONFLICT"
    http_status = 409
    retryable = False


class SemanticEdgeCorrectionValidationError(SemanticEdgeCorrectionError):
    code = "VALIDATION_ERROR"
    http_status = 400


class SemanticEdgeCorrectionAuthorizationError(SemanticEdgeCorrectionError):
    code = "FORBIDDEN"
    http_status = 403


class SemanticEdgeCorrectionNotFoundError(SemanticEdgeCorrectionError):
    code = "NOT_FOUND"
    http_status = 404


class SemanticEdgeCorrectionScopeStaleError(SemanticEdgeCorrectionError):
    code = "SCOPE_STALE"


class SemanticEdgeCorrectionIdempotencyError(SemanticEdgeCorrectionError):
    code = "IDEMPOTENCY_CONFLICT"


@dataclass(frozen=True, slots=True)
class SuppressSemanticEdgeCommand:
    principal: SessionPrincipal
    selected_space_id: str
    edge_id: str
    edge_kind: str
    reason: str
    operation_id: str
    request_id: str
    break_glass: bool = False
    confirmation: str | None = None
    expected_row_version: int | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class LiftSemanticEdgeSuppressionCommand:
    principal: SessionPrincipal
    selected_space_id: str
    suppression_id: str
    reason: str
    operation_id: str
    request_id: str
    expected_row_version: int | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class LiftSemanticEdgeForEdgeCommand:
    principal: SessionPrincipal
    selected_space_id: str
    edge_id: str
    reason: str
    operation_id: str
    request_id: str
    edge_kind: str = "auto"
    expected_row_version: int | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class SemanticEdgeCorrectionResult:
    operation_id: str
    request_id: str
    edge_id: str
    edge_kind: str
    scope_space_id: str
    suppression_id: str
    suppression_status: str
    edge_status: str
    reevaluation_queued: bool
    row_version: int

    def as_dict(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "edge_kind": self.edge_kind,
            "edge_status": self.edge_status,
            "operation_id": self.operation_id,
            "reevaluation_queued": self.reevaluation_queued,
            "request_id": self.request_id,
            "row_version": self.row_version,
            "scope_space_id": self.scope_space_id,
            "suppression_id": self.suppression_id,
            "suppression_status": self.suppression_status,
        }

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, object], *, request_id: str
    ) -> "SemanticEdgeCorrectionResult":
        try:
            reevaluation_queued = value["reevaluation_queued"]
            if not isinstance(reevaluation_queued, bool):
                raise TypeError("reevaluation_queued must be Boolean.")
            return cls(
                operation_id=str(value["operation_id"]),
                request_id=request_id,
                edge_id=str(value["edge_id"]),
                edge_kind=str(value["edge_kind"]),
                scope_space_id=str(value["scope_space_id"]),
                suppression_id=str(value["suppression_id"]),
                suppression_status=str(value["suppression_status"]),
                edge_status=str(value["edge_status"]),
                reevaluation_queued=reevaluation_queued,
                row_version=int(value["row_version"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticEdgeCorrectionIdempotencyError(
                "The stored correction result is malformed."
            ) from exc


def _id(*parts: object) -> str:
    return str(uuid5(_NAMESPACE, "\x1f".join(str(part) for part in parts)))


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _uuid(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise SemanticEdgeCorrectionValidationError(
            f"{field_name} must be a canonical lowercase UUID."
        )
    return value


def _reason(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not 1 <= len(value) <= 2_000:
        raise SemanticEdgeCorrectionValidationError("reason is invalid.")
    return value


def _safe_request_meta(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise SemanticEdgeCorrectionValidationError(
            "request_meta must be a mapping."
        )
    safe: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip().lower()
        if any(fragment in key for fragment in _SENSITIVE_META_FRAGMENTS):
            raise SemanticEdgeCorrectionValidationError(
                "Sensitive request metadata cannot enter audit storage."
            )
        if key not in _REQUEST_META_ALLOWLIST:
            continue
        if raw_value is not None and len(str(raw_value)) > 500:
            raise SemanticEdgeCorrectionValidationError(
                "Audit request metadata values are too long."
            )
        safe[key] = None if raw_value is None else str(raw_value)
    _canonical(safe)
    return safe


class SemanticEdgeCorrectionService:
    __slots__ = ("_aggregate", "_audit", "_factory", "_outbox", "_scope", "_users")

    def __init__(
        self,
        factory: ConnectionFactory,
        scope_resolver: ScopeResolver,
        *,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        aggregate: AggregateGraphRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("SemanticEdgeCorrectionService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("SemanticEdgeCorrectionService requires a ScopeResolver.")
        self._factory = factory
        self._scope = scope_resolver
        self._users = users or UserRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._aggregate = aggregate or AggregateGraphRepository()

    def suppress(self, command: SuppressSemanticEdgeCommand) -> SemanticEdgeCorrectionResult:
        if not isinstance(command, SuppressSemanticEdgeCommand):
            raise TypeError("suppress requires a SuppressSemanticEdgeCommand.")
        selected = _uuid(command.selected_space_id, "selected_space_id")
        edge_id = _uuid(command.edge_id, "edge_id")
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        reason = _reason(command.reason)
        request_meta = _safe_request_meta(command.request_meta)
        if command.expected_row_version is not None and (
            isinstance(command.expected_row_version, bool)
            or not isinstance(command.expected_row_version, int)
            or command.expected_row_version < 1
        ):
            raise SemanticEdgeCorrectionValidationError(
                "expected_row_version is invalid."
            )
        if command.edge_kind not in {"auto", "local", "aggregate"}:
            raise SemanticEdgeCorrectionValidationError("edge_kind is invalid.")
        if not isinstance(command.break_glass, bool):
            raise SemanticEdgeCorrectionValidationError("break_glass must be Boolean.")
        if not command.break_glass and command.confirmation is not None:
            raise SemanticEdgeCorrectionValidationError(
                "confirmation is valid only for break-glass suppression."
            )
        connection = self._factory.open()
        try:
            with transaction(connection):
                edge_kind, edge = self._resolve_edge(
                    connection, command.edge_kind, edge_id
                )
                if edge is None or edge["scope_space_id"] != selected:
                    raise SemanticEdgeCorrectionNotFoundError("The semantic edge is unavailable.")
                if edge["origin"] != "semantic_auto" or edge["semantic_key"] is None:
                    raise SemanticEdgeCorrectionValidationError(
                        "Only an automatic semantic edge uses this suppression ledger."
                    )
                if command.break_glass and command.expected_row_version is None:
                    raise SemanticEdgeCorrectionValidationError(
                        "Break-glass suppression requires expected_row_version."
                    )
                actor = self._authorize(
                    connection,
                    command.principal,
                    selected,
                    edge_kind,
                    break_glass=command.break_glass,
                    confirmation=command.confirmation,
                )
                request_hash = sha256(
                    _canonical(
                        {
                            "break_glass": command.break_glass,
                            "edge_id": edge_id,
                            "edge_kind": edge_kind,
                            "operation_type": "semantic_edge.suppress",
                            "expected_row_version": command.expected_row_version,
                            "reason": reason,
                            "scope_space_id": selected,
                            "scope_epoch": command.principal.captured_scope_epoch,
                        }
                    ).encode("utf-8")
                ).hexdigest()
                begun = self._begin(
                    connection,
                    actor.id,
                    "semantic_edge.suppress",
                    operation_id,
                    request_hash,
                )
                if begun is not None:
                    return SemanticEdgeCorrectionResult.from_mapping(
                        begun, request_id=request_id
                    )
                if (
                    command.expected_row_version is not None
                    and int(edge["row_version"]) != command.expected_row_version
                ):
                    raise SemanticEdgeCorrectionError(
                        "The semantic edge row version changed."
                    )
                now_ms = db_now_ms(connection)
                suppression_id = _id("suppression", actor.id, operation_id)
                if edge_kind == "local":
                    generation = connection.execute(
                        """UPDATE knowledge_space SET generation=generation+1,
                                  updated_at=?,row_version=row_version+1
                            WHERE id=? AND status='active' RETURNING generation""",
                        (now_ms, selected),
                    ).fetchone()
                    if generation is None:
                        raise SemanticEdgeCorrectionError(
                            "The local graph generation is unavailable."
                        )
                audit = self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=_id("audit", actor.id, operation_id),
                        occurred_at_ms=now_ms,
                        request_id=request_id,
                        operation_id=operation_id,
                        action="semantic_edge.suppress.break_glass" if command.break_glass else "semantic_edge.suppress",
                        outcome="committed",
                        actor_kind="user",
                        actor_user_id=actor.id,
                        actor_display_name=actor.display_name,
                        actor_role_snapshot=actor.system_role,
                        space_id=selected,
                        reason=reason,
                        details={
                            "edge_id": edge_id,
                            "edge_kind": edge_kind,
                            "schema_version": 1,
                            "suppression_id": suppression_id,
                        },
                        request_meta=request_meta,
                    ),
                )
                event = self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=f"semantic-edge:suppress:{suppression_id}",
                        space_id=selected,
                        aggregate_type="semantic_edge_suppression",
                        aggregate_id=suppression_id,
                        aggregate_version=1,
                        event_type="semantic.edge.suppressed",
                        payload={
                            "edge_id": edge_id,
                            "edge_kind": edge_kind,
                            "schema_version": 1,
                            "suppression_id": suppression_id,
                        },
                        available_at_ms=now_ms,
                    ),
                )
                detector_generation = int(edge["detector_generation"])
                connection.execute(
                    """INSERT INTO semantic_edge_suppression(
                         id,semantic_key,scope_kind,scope_space_id,local_edge_id,
                         aggregate_edge_id,reason,detector_generation_floor,
                         detector_generation_ceiling,status,actor_user_id,
                         operation_id,effect_key,audit_event_id,outbox_event_id,
                         superseding_local_edge_id,superseding_aggregate_edge_id,
                         lifted_by_user_id,lifted_at,lift_audit_event_id,
                         lift_outbox_event_id,created_at,updated_at,row_version
                       ) VALUES (?,?,?,?,?,?,?, ?,NULL,'active',?,?,?, ?,?,
                                 NULL,NULL,NULL,NULL,NULL,NULL,?,?,1)""",
                    (
                        suppression_id,
                        edge["semantic_key"],
                        edge_kind,
                        selected,
                        edge_id if edge_kind == "local" else None,
                        edge_id if edge_kind == "aggregate" else None,
                        reason,
                        detector_generation,
                        actor.id,
                        operation_id,
                        f"semantic-suppression:{actor.id}:{operation_id}",
                        audit.id,
                        event.id,
                        now_ms,
                        now_ms,
                    ),
                )
                if edge_kind == "local":
                    updated = connection.execute(
                        """UPDATE knowledge_edge SET edge_status='inactive',
                                  updated_at=?,row_version=row_version+1
                            WHERE id=? AND edge_status='active' AND semantic_key=?""",
                        (now_ms, edge_id, edge["semantic_key"]),
                    )
                    self._aggregate.invalidate_current_ancestors(
                        connection,
                        source_space_id=selected,
                        now_ms=now_ms,
                        include_self=False,
                        create_missing=False,
                    )
                    edge_status = "inactive"
                else:
                    updated = connection.execute(
                        """UPDATE aggregate_knowledge_edge
                              SET edge_status='suppressed',deactivated_at=?,updated_at=?,
                                  row_version=row_version+1
                            WHERE id=? AND edge_status IN ('active','needs_revalidation')
                              AND semantic_key=?""",
                        (now_ms, now_ms, edge_id, edge["semantic_key"]),
                    )
                    self._aggregate.ensure_generation_vector(
                        connection,
                        selected_space_id=selected,
                        closure_proof_sha256=str(edge["closure_proof_sha256"]),
                        now_ms=now_ms,
                        increment_home_edge=True,
                    )
                    self._aggregate.invalidate_current_ancestors(
                        connection,
                        source_space_id=selected,
                        now_ms=now_ms,
                        include_self=False,
                        create_missing=True,
                    )
                    edge_status = "suppressed"
                if updated.rowcount != 1:
                    raise SemanticEdgeCorrectionError("The semantic edge is no longer active.")
                result = SemanticEdgeCorrectionResult(
                    operation_id,
                    request_id,
                    edge_id,
                    edge_kind,
                    selected,
                    suppression_id,
                    "active",
                    edge_status,
                    False,
                    1,
                )
                self._commit(connection, actor.id, operation_id, "semantic_edge.suppress", result)
                return result
        finally:
            connection.close()

    def lift_edge(
        self, command: LiftSemanticEdgeForEdgeCommand
    ) -> SemanticEdgeCorrectionResult:
        """Resolve the contract path edge ID to its latest suppression ledger row."""

        if not isinstance(command, LiftSemanticEdgeForEdgeCommand):
            raise TypeError("lift_edge requires a LiftSemanticEdgeForEdgeCommand.")
        selected = _uuid(command.selected_space_id, "selected_space_id")
        edge_id = _uuid(command.edge_id, "edge_id")
        if command.edge_kind not in {"auto", "local", "aggregate"}:
            raise SemanticEdgeCorrectionValidationError("edge_kind is invalid.")
        connection = self._factory.open(read_only=True)
        try:
            row = None
            edge_kind = command.edge_kind
            with transaction(connection, TransactionMode.DEFERRED):
                edge_kind, edge = self._resolve_edge(connection, edge_kind, edge_id)
                if edge is None or edge["scope_space_id"] != selected:
                    raise SemanticEdgeCorrectionNotFoundError(
                        "The semantic edge is unavailable."
                    )
                column = (
                    "local_edge_id" if edge_kind == "local" else "aggregate_edge_id"
                )
                row = connection.execute(
                    f"""SELECT id FROM semantic_edge_suppression
                          WHERE scope_space_id=? AND scope_kind=? AND {column}=?
                          ORDER BY created_at DESC,id DESC LIMIT 1""",
                    (selected, edge_kind, edge_id),
                ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise SemanticEdgeCorrectionNotFoundError(
                "The semantic edge suppression is unavailable."
            )
        return self.lift(
            LiftSemanticEdgeSuppressionCommand(
                principal=command.principal,
                selected_space_id=selected,
                suppression_id=str(row["id"]),
                reason=command.reason,
                operation_id=command.operation_id,
                request_id=command.request_id,
                expected_row_version=command.expected_row_version,
                request_meta=command.request_meta,
            )
        )

    def lift(
        self, command: LiftSemanticEdgeSuppressionCommand
    ) -> SemanticEdgeCorrectionResult:
        if not isinstance(command, LiftSemanticEdgeSuppressionCommand):
            raise TypeError("lift requires a LiftSemanticEdgeSuppressionCommand.")
        selected = _uuid(command.selected_space_id, "selected_space_id")
        suppression_id = _uuid(command.suppression_id, "suppression_id")
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        reason = _reason(command.reason)
        request_meta = _safe_request_meta(command.request_meta)
        if command.expected_row_version is not None and (
            isinstance(command.expected_row_version, bool)
            or not isinstance(command.expected_row_version, int)
            or command.expected_row_version < 1
        ):
            raise SemanticEdgeCorrectionValidationError(
                "expected_row_version is invalid."
            )
        connection = self._factory.open()
        try:
            with transaction(connection):
                suppression = connection.execute(
                    """SELECT * FROM semantic_edge_suppression
                        WHERE id=? AND scope_space_id=?""",
                    (suppression_id, selected),
                ).fetchone()
                if suppression is None:
                    raise SemanticEdgeCorrectionNotFoundError("The suppression is unavailable.")
                actor = self._authorize(
                    connection,
                    command.principal,
                    selected,
                    str(suppression["scope_kind"]),
                    break_glass=False,
                    confirmation=None,
                )
                request_hash = sha256(
                    _canonical(
                        {
                            "operation_type": "semantic_edge.suppression_lift",
                            "expected_row_version": command.expected_row_version,
                            "reason": reason,
                            "scope_epoch": command.principal.captured_scope_epoch,
                            "scope_space_id": selected,
                            "suppression_id": suppression_id,
                        }
                    ).encode("utf-8")
                ).hexdigest()
                begun = self._begin(
                    connection,
                    actor.id,
                    "semantic_edge.suppression_lift",
                    operation_id,
                    request_hash,
                )
                if begun is not None:
                    return SemanticEdgeCorrectionResult.from_mapping(begun, request_id=request_id)
                if (
                    command.expected_row_version is not None
                    and int(suppression["row_version"]) != command.expected_row_version
                ):
                    raise SemanticEdgeCorrectionError(
                        "The suppression row version changed."
                    )
                if suppression["status"] != "active":
                    raise SemanticEdgeCorrectionError("The suppression is already lifted.")
                now_ms = db_now_ms(connection)
                audit = self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=_id("lift-audit", actor.id, operation_id),
                        occurred_at_ms=now_ms,
                        request_id=request_id,
                        operation_id=operation_id,
                        action="semantic_edge.suppression_lift",
                        outcome="committed",
                        actor_kind="user",
                        actor_user_id=actor.id,
                        actor_display_name=actor.display_name,
                        actor_role_snapshot=actor.system_role,
                        space_id=selected,
                        reason=reason,
                        details={"schema_version": 1, "suppression_id": suppression_id},
                        request_meta=request_meta,
                    ),
                )
                event = self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=f"semantic-edge:suppression-lift:{actor.id}:{operation_id}",
                        space_id=selected,
                        aggregate_type="semantic_edge_suppression",
                        aggregate_id=suppression_id,
                        aggregate_version=int(suppression["row_version"]) + 1,
                        event_type="semantic.edge.suppression_lifted",
                        payload={
                            "detector_generation_floor": int(suppression["detector_generation_floor"]),
                            "edge_id": suppression["local_edge_id"] or suppression["aggregate_edge_id"],
                            "edge_kind": suppression["scope_kind"],
                            "reevaluation_requested": True,
                            "schema_version": 1,
                            "suppression_id": suppression_id,
                        },
                        available_at_ms=now_ms,
                    ),
                )
                updated = connection.execute(
                    """UPDATE semantic_edge_suppression
                          SET status='lifted',lifted_by_user_id=?,lifted_at=?,
                              lift_audit_event_id=?,lift_outbox_event_id=?,updated_at=?,
                              row_version=row_version+1
                        WHERE id=? AND status='active' AND row_version=?""",
                    (
                        actor.id,
                        now_ms,
                        audit.id,
                        event.id,
                        now_ms,
                        suppression_id,
                        suppression["row_version"],
                    ),
                )
                if updated.rowcount != 1:
                    raise SemanticEdgeCorrectionError("The suppression changed before lift.")
                edge_id = str(suppression["local_edge_id"] or suppression["aggregate_edge_id"])
                edge_kind = str(suppression["scope_kind"])
                edge_status = "inactive" if edge_kind == "local" else "suppressed"
                result = SemanticEdgeCorrectionResult(
                    operation_id,
                    request_id,
                    edge_id,
                    edge_kind,
                    selected,
                    suppression_id,
                    "lifted",
                    edge_status,
                    True,
                    int(suppression["row_version"]) + 1,
                )
                self._commit(
                    connection,
                    actor.id,
                    operation_id,
                    "semantic_edge.suppression_lift",
                    result,
                )
                return result
        finally:
            connection.close()

    @staticmethod
    def _edge(connection: ManagedConnection, kind: str, edge_id: str):
        if kind == "local":
            return connection.execute(
                """SELECT id,space_id AS scope_space_id,origin,semantic_key,
                          detector_generation,edge_status,row_version,
                          NULL AS closure_proof_sha256
                     FROM knowledge_edge WHERE id=?""",
                (edge_id,),
            ).fetchone()
        return connection.execute(
            """SELECT id,home_space_id AS scope_space_id,origin,semantic_key,
                      detector_generation,edge_status,row_version,
                      closure_proof_sha256
                 FROM aggregate_knowledge_edge WHERE id=?""",
            (edge_id,),
        ).fetchone()

    @classmethod
    def _resolve_edge(
        cls, connection: ManagedConnection, kind: str, edge_id: str
    ) -> tuple[str, object | None]:
        if kind in {"local", "aggregate"}:
            return kind, cls._edge(connection, kind, edge_id)
        local = cls._edge(connection, "local", edge_id)
        aggregate = cls._edge(connection, "aggregate", edge_id)
        if local is not None and aggregate is not None:
            raise SemanticEdgeCorrectionError(
                "The semantic edge identity is ambiguous."
            )
        if local is not None:
            return "local", local
        if aggregate is not None:
            return "aggregate", aggregate
        return "local", None

    def _authorize(
        self,
        connection: ManagedConnection,
        principal: SessionPrincipal,
        selected_space_id: str,
        edge_kind: str,
        *,
        break_glass: bool,
        confirmation: str | None,
    ):
        actor = self._users.require(connection, principal.user_id)
        if actor.account_status != "active" or actor.scope_epoch != principal.captured_scope_epoch:
            raise SemanticEdgeCorrectionScopeStaleError("The actor scope changed.")
        if break_glass:
            space = connection.execute(
                "SELECT space_kind,status FROM knowledge_space WHERE id=?",
                (selected_space_id,),
            ).fetchone()
            if (
                actor.system_role != "admin"
                or space is None
                or space["status"] != "active"
                or space["space_kind"] == "personal"
                or confirmation != _BREAK_GLASS_CONFIRMATION
            ):
                raise SemanticEdgeCorrectionAuthorizationError(
                    "Organizational administrator break-glass proof is invalid."
                )
            return actor
        capability = (
            Capability.LOCAL_EDGE_CORRECT
            if edge_kind == "local"
            else Capability.AGGREGATE_EDGE_CORRECT
        )
        try:
            resolved = self._scope.resolve_in_transaction(
                connection, principal, selected_space_id, capability
            )
        except ScopeStaleError as exc:
            raise SemanticEdgeCorrectionScopeStaleError(str(exc)) from exc
        except (ScopeAuthenticationRequiredError, ScopeForbiddenError) as exc:
            raise SemanticEdgeCorrectionAuthorizationError(str(exc)) from exc
        except ScopeNotFoundError as exc:
            raise SemanticEdgeCorrectionNotFoundError(str(exc)) from exc
        if resolved.actor_user_id != actor.id:
            raise SemanticEdgeCorrectionAuthorizationError("The correction actor is invalid.")
        return actor

    def _begin(
        self,
        connection: ManagedConnection,
        actor_user_id: str,
        operation_type: str,
        operation_id: str,
        request_hash: str,
    ) -> Mapping[str, object] | None:
        try:
            begun = self._users.begin_operation(
                connection,
                actor_user_id=actor_user_id,
                operation_type=operation_type,
                operation_id=operation_id,
                request_sha256=request_hash,
                dedup_id=_id("dedup", actor_user_id, operation_type, operation_id),
                now_ms=db_now_ms(connection),
            )
        except OperationIdempotencyConflictError as exc:
            raise SemanticEdgeCorrectionIdempotencyError(str(exc)) from exc
        if begun.inserted:
            return None
        if begun.operation.status == "committed" and begun.operation.result is not None:
            return begun.operation.result
        raise SemanticEdgeCorrectionIdempotencyError(
            "The semantic correction operation is already in progress."
        )

    def _commit(
        self,
        connection: ManagedConnection,
        actor_user_id: str,
        operation_id: str,
        operation_type: str,
        result: SemanticEdgeCorrectionResult,
    ) -> None:
        try:
            self._users.commit_operation(
                connection,
                dedup_id=_id("dedup", actor_user_id, operation_type, operation_id),
                result=result.as_dict(),
                http_status=200,
                completed_at_ms=db_now_ms(connection),
            )
        except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
            raise SemanticEdgeCorrectionIdempotencyError(str(exc)) from exc


__all__ = [
    "LiftSemanticEdgeForEdgeCommand",
    "LiftSemanticEdgeSuppressionCommand",
    "SemanticEdgeCorrectionAuthorizationError",
    "SemanticEdgeCorrectionError",
    "SemanticEdgeCorrectionIdempotencyError",
    "SemanticEdgeCorrectionNotFoundError",
    "SemanticEdgeCorrectionResult",
    "SemanticEdgeCorrectionScopeStaleError",
    "SemanticEdgeCorrectionService",
    "SemanticEdgeCorrectionValidationError",
    "SuppressSemanticEdgeCommand",
]

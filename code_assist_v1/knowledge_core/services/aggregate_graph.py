"""Manual aggregate-edge command with exact higher-home authority.

Automatic model decisions intentionally remain SBE-4.2.  This service supplies
the human, transaction-owning primitive and the same LCA/storage postconditions
that the later system handler must reuse.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from uuid import UUID, uuid5

from ..db import ConnectionFactory, ManagedConnection, db_now_ms, transaction
from ..domain import (
    AggregateEdgeStatus,
    SemanticEdgeDirection,
    SemanticRelationType,
)
from ..errors import KnowledgeCoreError
from ..repositories.aggregate_graph import (
    AggregateGraphConflictError,
    AggregateGraphRepository,
    AggregateGraphRepositoryError,
)
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.semantic_graph_contracts import AggregateKnowledgeEdgeRecord
from ..repositories.users import (
    OperationIdempotencyConflictError,
    OperationInProgressError,
    UserRecord,
    UserRepository,
)
from ..scope import (
    Capability,
    ScopeAuthenticationRequiredError,
    ScopeForbiddenError,
    ScopeIntegrityError,
    ScopeNotFoundError,
    ScopeResolver,
    ScopeStaleError,
    ScopeValidationError,
    SessionPrincipal,
)


_DEDUP_NAMESPACE = UUID("2c4be24f-4b32-491b-99e3-27f2553b502f")
_EDGE_NAMESPACE = UUID("794978e3-95da-4b22-928d-f4212ce03490")
_AUDIT_NAMESPACE = UUID("0acdf78e-b3f7-4f84-839c-58d6699af37a")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_META_KEYS = frozenset({"remote_addr", "method", "path", "host", "user_agent"})


class AggregateGraphServiceError(KnowledgeCoreError):
    code = "STATE_CONFLICT"
    http_status = 409
    retryable = False


class AggregateGraphValidationError(AggregateGraphServiceError):
    code = "VALIDATION_ERROR"
    http_status = 400


class AggregateGraphAuthenticationError(AggregateGraphServiceError):
    code = "AUTHENTICATION_REQUIRED"
    http_status = 401


class AggregateGraphAuthorizationError(AggregateGraphServiceError):
    code = "FORBIDDEN"
    http_status = 403


class AggregateGraphNotFoundError(AggregateGraphServiceError):
    code = "NOT_FOUND"
    http_status = 404


class AggregateGraphScopeStaleError(AggregateGraphServiceError):
    code = "SCOPE_STALE"


class AggregateGraphIdempotencyError(AggregateGraphServiceError):
    code = "IDEMPOTENCY_CONFLICT"


class AggregateGraphOperationInProgressError(AggregateGraphServiceError):
    retryable = True


class AggregateGraphIntegrityError(AggregateGraphServiceError):
    code = "INTERNAL_ERROR"
    http_status = 500


class AggregateGraphLocalEdgeRequiredError(AggregateGraphServiceError):
    code = "LOCAL_EDGE_REQUIRED"


@dataclass(frozen=True, slots=True)
class CreateAggregateEdgeCommand:
    principal: SessionPrincipal
    source_space_id: str
    source_item_id: str
    source_revision_id: str
    source_item_row_version: int
    target_space_id: str
    target_item_id: str
    target_revision_id: str
    target_item_row_version: int
    edge_type: SemanticRelationType
    direction: SemanticEdgeDirection
    source_evidence_span_sha256: str
    target_evidence_span_sha256: str
    source_evidence_location: Mapping[str, object]
    target_evidence_location: Mapping[str, object]
    reason: str
    operation_id: str
    request_id: str
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class CreateAggregateEdgeResult:
    request_id: str
    operation_id: str
    edge_id: str
    home_space_id: str
    home_org_unit_id: str
    source_space_id: str
    target_space_id: str
    row_version: int
    home_edge_generation: int
    desired_generation: int

    def as_dict(self) -> dict[str, object]:
        return {
            "desired_generation": self.desired_generation,
            "edge_id": self.edge_id,
            "home_edge_generation": self.home_edge_generation,
            "home_org_unit_id": self.home_org_unit_id,
            "home_space_id": self.home_space_id,
            "operation_id": self.operation_id,
            "request_id": self.request_id,
            "row_version": self.row_version,
            "source_space_id": self.source_space_id,
            "target_space_id": self.target_space_id,
        }

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, object]
    ) -> "CreateAggregateEdgeResult":
        expected = {
            "desired_generation",
            "edge_id",
            "home_edge_generation",
            "home_org_unit_id",
            "home_space_id",
            "operation_id",
            "request_id",
            "row_version",
            "source_space_id",
            "target_space_id",
        }
        if set(value) != expected:
            raise AggregateGraphIdempotencyError(
                "Stored aggregate-edge result is malformed."
            )
        try:
            result = cls(
                request_id=_uuid(value["request_id"], "stored request_id"),
                operation_id=_uuid(value["operation_id"], "stored operation_id"),
                edge_id=_uuid(value["edge_id"], "stored edge_id"),
                home_space_id=_uuid(value["home_space_id"], "stored home_space_id"),
                home_org_unit_id=_uuid(
                    value["home_org_unit_id"], "stored home_org_unit_id"
                ),
                source_space_id=_uuid(
                    value["source_space_id"], "stored source_space_id"
                ),
                target_space_id=_uuid(
                    value["target_space_id"], "stored target_space_id"
                ),
                row_version=_positive(value["row_version"], "stored row_version"),
                home_edge_generation=_positive(
                    value["home_edge_generation"],
                    "stored home_edge_generation",
                ),
                desired_generation=_nonnegative(
                    value["desired_generation"], "stored desired_generation"
                ),
            )
        except AggregateGraphValidationError as exc:
            raise AggregateGraphIdempotencyError(str(exc)) from exc
        return result


def _uuid(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise AggregateGraphValidationError(
            f"{field_name} must be a canonical lowercase UUID."
        )
    return value


def _sha(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise AggregateGraphValidationError(
            f"{field_name} must be a lowercase SHA-256 digest."
        )
    return value


def _positive(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AggregateGraphValidationError(f"{field_name} must be positive.")
    return value


def _nonnegative(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AggregateGraphValidationError(f"{field_name} must be non-negative.")
    return value


def _text(value: object, field_name: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > maximum
    ):
        raise AggregateGraphValidationError(f"{field_name} is invalid.")
    return value


def _canonical_json(value: object, field_name: str) -> str:
    if not isinstance(value, Mapping):
        raise AggregateGraphValidationError(f"{field_name} must be an object.")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise AggregateGraphValidationError(
            f"{field_name} must be finite JSON."
        ) from exc
    if len(encoded.encode("utf-8")) > 16_384:
        raise AggregateGraphValidationError(f"{field_name} is too large.")
    return encoded


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _derived(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, "\x1f".join(parts)))


def _safe_meta(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise AggregateGraphValidationError("request_meta must be an object.")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or key not in _META_KEYS:
            continue
        if isinstance(item, (str, int, bool)) and (
            not isinstance(item, str) or len(item) <= 1_000
        ):
            result[key] = item
    return result


def _semantic_key(
    *,
    source_revision_id: str,
    target_revision_id: str,
    edge_type: SemanticRelationType,
    direction: SemanticEdgeDirection,
) -> str:
    endpoints = [source_revision_id, target_revision_id]
    if direction is SemanticEdgeDirection.SYMMETRIC:
        endpoints.sort()
    return sha256(
        _canonical_bytes(
            {
                "direction": direction.value,
                "edge_type": edge_type.value,
                "endpoints": endpoints,
                "schema_version": 1,
            }
        )
    ).hexdigest()


class AggregateGraphService:
    __slots__ = ("_audit", "_factory", "_graph", "_outbox", "_scope", "_users")

    def __init__(
        self,
        factory: ConnectionFactory,
        scope_resolver: ScopeResolver,
        *,
        graph: AggregateGraphRepository | None = None,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("AggregateGraphService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("AggregateGraphService requires a ScopeResolver.")
        self._factory = factory
        self._scope = scope_resolver
        self._graph = graph or AggregateGraphRepository()
        self._users = users or UserRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()

    def _resolve_home(
        self,
        connection: ManagedConnection,
        principal: SessionPrincipal,
        home_space_id: str,
    ):
        try:
            resolved = self._scope.resolve_in_transaction(
                connection,
                principal,
                home_space_id,
                Capability.AGGREGATE_EDGE_CORRECT,
            )
        except ScopeAuthenticationRequiredError as exc:
            raise AggregateGraphAuthenticationError(str(exc)) from exc
        except ScopeStaleError as exc:
            raise AggregateGraphScopeStaleError(str(exc)) from exc
        except ScopeNotFoundError as exc:
            raise AggregateGraphNotFoundError(str(exc)) from exc
        except ScopeForbiddenError as exc:
            raise AggregateGraphAuthorizationError(str(exc)) from exc
        except ScopeValidationError as exc:
            raise AggregateGraphValidationError(str(exc)) from exc
        except ScopeIntegrityError as exc:
            raise AggregateGraphIntegrityError(str(exc)) from exc
        if resolved.access_plan is None:
            raise AggregateGraphIntegrityError(
                "The canonical higher-space access plan is unavailable."
            )
        actor = self._users.require(connection, resolved.actor_user_id)
        if actor.account_status != "active" or actor.scope_epoch != resolved.scope_epoch:
            raise AggregateGraphScopeStaleError("The canonical actor scope changed.")
        return actor, resolved.access_plan

    def create(self, command: CreateAggregateEdgeCommand) -> CreateAggregateEdgeResult:
        if not isinstance(command, CreateAggregateEdgeCommand):
            raise TypeError("create requires a CreateAggregateEdgeCommand.")
        if not isinstance(command.principal, SessionPrincipal):
            raise AggregateGraphValidationError(
                "A canonical session principal is required."
            )
        source_space = _uuid(command.source_space_id, "source_space_id")
        source_item = _uuid(command.source_item_id, "source_item_id")
        source_revision = _uuid(command.source_revision_id, "source_revision_id")
        source_version = _positive(
            command.source_item_row_version, "source_item_row_version"
        )
        target_space = _uuid(command.target_space_id, "target_space_id")
        target_item = _uuid(command.target_item_id, "target_item_id")
        target_revision = _uuid(command.target_revision_id, "target_revision_id")
        target_version = _positive(
            command.target_item_row_version, "target_item_row_version"
        )
        if source_item == target_item:
            raise AggregateGraphValidationError("A self edge is not permitted.")
        if not isinstance(command.edge_type, SemanticRelationType):
            raise AggregateGraphValidationError("edge_type is invalid.")
        if not isinstance(command.direction, SemanticEdgeDirection):
            raise AggregateGraphValidationError("direction is invalid.")
        source_evidence = _sha(
            command.source_evidence_span_sha256,
            "source_evidence_span_sha256",
        )
        target_evidence = _sha(
            command.target_evidence_span_sha256,
            "target_evidence_span_sha256",
        )
        source_location = _canonical_json(
            command.source_evidence_location,
            "source_evidence_location",
        )
        target_location = _canonical_json(
            command.target_evidence_location,
            "target_evidence_location",
        )
        reason = _text(command.reason, "reason", 2_000)
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        request_meta = _safe_meta(command.request_meta)
        operation_type = "aggregate_edge.create"
        dedup_id = _derived(
            _DEDUP_NAMESPACE,
            command.principal.user_id,
            operation_type,
            operation_id,
        )
        request_hash = sha256(
            _canonical_bytes(
                {
                    "actor_scope_epoch": command.principal.captured_scope_epoch,
                    "actor_user_id": command.principal.user_id,
                    "direction": command.direction.value,
                    "edge_type": command.edge_type.value,
                    "operation_type": operation_type,
                    "reason": reason,
                    "schema_version": 1,
                    "source_evidence_location_json": source_location,
                    "source_evidence_span_sha256": source_evidence,
                    "source_item_id": source_item,
                    "source_item_row_version": source_version,
                    "source_revision_id": source_revision,
                    "source_space_id": source_space,
                    "target_evidence_location_json": target_location,
                    "target_evidence_span_sha256": target_evidence,
                    "target_item_id": target_item,
                    "target_item_row_version": target_version,
                    "target_revision_id": target_revision,
                    "target_space_id": target_space,
                }
            )
        ).hexdigest()
        connection = self._factory.open()
        try:
            with transaction(connection):
                try:
                    placement = self._graph.resolve_placement(
                        connection,
                        source_space_id=source_space,
                        target_space_id=target_space,
                    )
                except AggregateGraphConflictError as exc:
                    raise AggregateGraphServiceError(str(exc)) from exc
                except AggregateGraphRepositoryError as exc:
                    raise AggregateGraphIntegrityError(str(exc)) from exc
                if placement.kind == "local":
                    raise AggregateGraphLocalEdgeRequiredError(
                        "Same-space endpoints must use the local edge ledger."
                    )
                actor, plan = self._resolve_home(
                    connection, command.principal, placement.home_space_id
                )
                authorized_sources = {
                    source.source_space_id for source in plan.corpus_closure.sources
                }
                if not {source_space, target_space}.issubset(authorized_sources):
                    raise AggregateGraphAuthorizationError(
                        "Both endpoints must be physical sources in the LCA closure."
                    )
                try:
                    source = self._graph.require_current_endpoint(
                        connection,
                        space_id=source_space,
                        item_id=source_item,
                        revision_id=source_revision,
                        item_row_version=source_version,
                    )
                    target = self._graph.require_current_endpoint(
                        connection,
                        space_id=target_space,
                        item_id=target_item,
                        revision_id=target_revision,
                        item_row_version=target_version,
                    )
                except AggregateGraphConflictError as exc:
                    raise AggregateGraphServiceError(str(exc)) from exc
                except AggregateGraphRepositoryError as exc:
                    raise AggregateGraphIntegrityError(str(exc)) from exc
                now_ms = db_now_ms(connection)
                try:
                    begun = self._users.begin_operation(
                        connection,
                        actor_user_id=actor.id,
                        operation_type=operation_type,
                        operation_id=operation_id,
                        request_sha256=request_hash,
                        dedup_id=dedup_id,
                        now_ms=now_ms,
                    )
                except OperationIdempotencyConflictError as exc:
                    raise AggregateGraphIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        if begun.operation.result is None:
                            raise AggregateGraphIdempotencyError(
                                "Committed aggregate-edge operation has no result."
                            )
                        replay = CreateAggregateEdgeResult.from_mapping(
                            begun.operation.result
                        )
                        return CreateAggregateEdgeResult(
                            **{
                                **replay.as_dict(),
                                "request_id": request_id,
                            }
                        )
                    raise AggregateGraphOperationInProgressError(
                        "The aggregate-edge operation is already in progress."
                    )
                edge_id = _derived(_EDGE_NAMESPACE, dedup_id, "edge")
                event_uuid = _derived(_AUDIT_NAMESPACE, dedup_id, "create")
                semantic_key = _semantic_key(
                    source_revision_id=source_revision,
                    target_revision_id=target_revision,
                    edge_type=command.edge_type,
                    direction=command.direction,
                )
                effect_key = f"aggregate-edge:manual:{dedup_id}"
                details = {
                    "direction": command.direction.value,
                    "edge_id": edge_id,
                    "edge_type": command.edge_type.value,
                    "home_org_unit_id": placement.home_org_unit_id,
                    "home_space_id": placement.home_space_id,
                    "schema_version": 1,
                    "semantic_key": semantic_key,
                    "source_item_id": source.item_id,
                    "source_revision_id": source.revision_id,
                    "source_space_id": source.space_id,
                    "target_item_id": target.item_id,
                    "target_revision_id": target.revision_id,
                    "target_space_id": target.space_id,
                }
                audit = self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=event_uuid,
                        occurred_at_ms=now_ms,
                        request_id=request_id,
                        operation_id=operation_id,
                        action="aggregate_edge.create",
                        outcome="created",
                        actor_kind="user",
                        actor_user_id=actor.id,
                        actor_display_name=actor.display_name,
                        actor_role_snapshot=actor.system_role,
                        space_id=placement.home_space_id,
                        item_id=source.item_id,
                        base_revision_id=source.revision_id,
                        new_revision_id=source.revision_id,
                        reason=reason,
                        details=details,
                        request_meta=request_meta,
                    ),
                )
                outbox = self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=f"aggregate-edge:create:{dedup_id}",
                        space_id=placement.home_space_id,
                        aggregate_type="aggregate_knowledge_edge",
                        aggregate_id=edge_id,
                        aggregate_version=1,
                        event_type="aggregate_edge.created",
                        payload=details,
                        available_at_ms=now_ms,
                    ),
                )
                try:
                    edge = self._graph.append_edge(
                        connection,
                        AggregateKnowledgeEdgeRecord(
                            id=edge_id,
                            semantic_key=semantic_key,
                            home_space_id=placement.home_space_id,
                            home_org_unit_id=placement.home_org_unit_id or "",
                            source_space_id=source.space_id,
                            source_item_id=source.item_id,
                            source_revision_id=source.revision_id,
                            target_space_id=target.space_id,
                            target_item_id=target.item_id,
                            target_revision_id=target.revision_id,
                            edge_type=command.edge_type,
                            direction=command.direction,
                            edge_status=AggregateEdgeStatus.ACTIVE,
                            origin="manual",
                            actor_kind="user",
                            actor_user_id=actor.id,
                            system_job_id=None,
                            operation_id=operation_id,
                            effect_key=effect_key,
                            detector_generation=None,
                            model_provider=None,
                            model_name=None,
                            prompt_schema_version=None,
                            scoring_version=None,
                            confidence_ppm=None,
                            source_evidence_span_sha256=source_evidence,
                            target_evidence_span_sha256=target_evidence,
                            source_evidence_location_json=source_location,
                            target_evidence_location_json=target_location,
                            closure_proof_sha256=plan.corpus_closure.sha256,
                            source_item_row_version=source.item_row_version,
                            target_item_row_version=target.item_row_version,
                            audit_event_id=audit.id,
                            outbox_event_id=outbox.id,
                            created_at=now_ms,
                            updated_at=now_ms,
                            deactivated_at=None,
                            row_version=1,
                        ),
                    )
                    vector_now_ms = db_now_ms(connection)
                    vector = self._graph.ensure_generation_vector(
                        connection,
                        selected_space_id=placement.home_space_id,
                        closure_proof_sha256=plan.corpus_closure.sha256,
                        now_ms=vector_now_ms,
                        increment_home_edge=True,
                    )
                    # A Team-home edge is also visible in its Responsibility
                    # aggregate.  The home vector above owns the edge counter;
                    # strict ancestors receive only a pending generation mark.
                    self._graph.invalidate_current_ancestors(
                        connection,
                        source_space_id=placement.home_space_id,
                        now_ms=vector_now_ms,
                        include_self=False,
                        create_missing=True,
                    )
                except AggregateGraphConflictError as exc:
                    raise AggregateGraphServiceError(str(exc)) from exc
                except AggregateGraphRepositoryError as exc:
                    raise AggregateGraphIntegrityError(str(exc)) from exc
                result = CreateAggregateEdgeResult(
                    request_id=request_id,
                    operation_id=operation_id,
                    edge_id=edge.id,
                    home_space_id=edge.home_space_id,
                    home_org_unit_id=edge.home_org_unit_id,
                    source_space_id=edge.source_space_id,
                    target_space_id=edge.target_space_id,
                    row_version=edge.row_version,
                    home_edge_generation=vector.home_edge_generation,
                    desired_generation=vector.desired_generation,
                )
                try:
                    self._users.commit_operation(
                        connection,
                        dedup_id=dedup_id,
                        result=result.as_dict(),
                        http_status=201,
                        completed_at_ms=now_ms,
                    )
                except (
                    OperationIdempotencyConflictError,
                    OperationInProgressError,
                ) as exc:
                    raise AggregateGraphIdempotencyError(str(exc)) from exc
                return result
        finally:
            connection.close()


__all__ = [
    "AggregateGraphAuthenticationError",
    "AggregateGraphAuthorizationError",
    "AggregateGraphIdempotencyError",
    "AggregateGraphIntegrityError",
    "AggregateGraphLocalEdgeRequiredError",
    "AggregateGraphNotFoundError",
    "AggregateGraphOperationInProgressError",
    "AggregateGraphScopeStaleError",
    "AggregateGraphService",
    "AggregateGraphServiceError",
    "AggregateGraphValidationError",
    "CreateAggregateEdgeCommand",
    "CreateAggregateEdgeResult",
]

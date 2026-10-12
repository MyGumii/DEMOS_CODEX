"""Explicit human-reviewed P6.4 semantic-edge application service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
import unicodedata
from typing import Literal
from uuid import UUID, uuid5

from ..db import ConnectionFactory, ManagedConnection, db_now_ms, transaction
from ..errors import KnowledgeCoreError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.findings import FindingIntegrityError, FindingRepository
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.semantic_edges import (
    SemanticEdgeApplyDraft,
    SemanticEdgeConflictError,
    SemanticEdgeIntegrityError,
    SemanticEdgeRepository,
)
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


_DEDUP_NAMESPACE = UUID("777e27b3-97a2-499a-a76f-8178f6480849")
_AUDIT_NAMESPACE = UUID("05be4271-a35c-4816-a1ca-278d20bcf308")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_REQUEST_META_ALLOWLIST = frozenset(
    {"remote_addr", "method", "path", "host", "user_agent"}
)
_SENSITIVE_META_FRAGMENTS = (
    "authorization",
    "cookie",
    "csrf",
    "password",
    "secret",
    "token",
)


class SemanticEdgeServiceError(KnowledgeCoreError):
    code = "STATE_CONFLICT"
    http_status = 409
    retryable = False


class SemanticEdgeValidationError(SemanticEdgeServiceError):
    code = "VALIDATION_ERROR"
    http_status = 400


class SemanticEdgeAuthenticationError(SemanticEdgeServiceError):
    code = "AUTHENTICATION_REQUIRED"
    http_status = 401


class SemanticEdgeAuthorizationError(SemanticEdgeServiceError):
    code = "FORBIDDEN"
    http_status = 403


class SemanticEdgeNotFoundError(SemanticEdgeServiceError):
    code = "NOT_FOUND"
    http_status = 404


class SemanticEdgeScopeStaleError(SemanticEdgeServiceError):
    code = "SCOPE_STALE"


class SemanticEdgeIdempotencyError(SemanticEdgeServiceError):
    code = "IDEMPOTENCY_CONFLICT"


class SemanticEdgeOperationInProgressError(SemanticEdgeServiceError):
    retryable = True


class SemanticEdgeServiceIntegrityError(SemanticEdgeServiceError):
    code = "INTERNAL_ERROR"
    http_status = 500


@dataclass(frozen=True, slots=True)
class ApplySemanticEdgeCommand:
    principal: SessionPrincipal
    space_id: str
    finding_id: str
    expected_finding_row_version: int
    source_revision_id: str
    target_revision_id: str
    evidence_sha256: str
    reason: str
    operation_id: str
    request_id: str
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class ApplySemanticEdgeResult:
    request_id: str
    operation_id: str
    finding_id: str
    outcome: Literal["applied", "obsolete"]
    finding_row_version: int
    edge_id: str | None
    edge_row_version: int | None
    space_generation: int

    def as_dict(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "edge_row_version": self.edge_row_version,
            "finding_id": self.finding_id,
            "finding_row_version": self.finding_row_version,
            "operation_id": self.operation_id,
            "outcome": self.outcome,
            "request_id": self.request_id,
            "space_generation": self.space_generation,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ApplySemanticEdgeResult":
        if set(value) != {
            "edge_id",
            "edge_row_version",
            "finding_id",
            "finding_row_version",
            "operation_id",
            "outcome",
            "request_id",
            "space_generation",
        }:
            raise SemanticEdgeIdempotencyError("Stored semantic-edge result is invalid.")
        outcome = value["outcome"]
        edge_id = value["edge_id"]
        edge_version = value["edge_row_version"]
        finding_version = value["finding_row_version"]
        generation = value["space_generation"]
        if outcome not in {"applied", "obsolete"} or (
            (outcome == "applied" and (edge_id is None or edge_version != 1))
            or (outcome == "obsolete" and (edge_id is not None or edge_version is not None))
        ):
            raise SemanticEdgeIdempotencyError("Stored semantic-edge outcome is invalid.")
        try:
            if edge_id is not None:
                edge_id = _uuid(edge_id, "stored edge_id")
            request_id = _uuid(value["request_id"], "stored request_id")
            operation_id = _uuid(value["operation_id"], "stored operation_id")
            finding_id = _uuid(value["finding_id"], "stored finding_id")
        except SemanticEdgeValidationError as exc:
            raise SemanticEdgeIdempotencyError(
                "Stored semantic-edge identity is invalid."
            ) from exc
        for number, field_name, minimum in (
            (finding_version, "stored finding version", 2),
            (generation, "stored space generation", 0),
        ):
            if isinstance(number, bool) or not isinstance(number, int) or number < minimum:
                raise SemanticEdgeIdempotencyError(f"{field_name} is invalid.")
        return cls(
            request_id=request_id,
            operation_id=operation_id,
            finding_id=finding_id,
            outcome=outcome,
            finding_row_version=finding_version,
            edge_id=edge_id,
            edge_row_version=edge_version,
            space_generation=generation,
        )


def _uuid(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise SemanticEdgeValidationError(
            f"{field_name} must be a canonical lowercase UUID."
        )
    return value


def _sha(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise SemanticEdgeValidationError(
            f"{field_name} must be a lowercase SHA-256 value."
        )
    return value


def _reason(value: object) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not 1 <= len(value) <= 2_000
        or value != unicodedata.normalize("NFKC", value)
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise SemanticEdgeValidationError("Semantic-edge reason is invalid.")
    return value


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise SemanticEdgeValidationError("Semantic-edge input is not finite JSON.") from exc


def _derived_uuid(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, "\x00".join(parts)))


def _safe_meta(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise SemanticEdgeValidationError("request_meta must be an object.")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or key not in _REQUEST_META_ALLOWLIST:
            continue
        if isinstance(item, bool):
            result[key] = item
        elif isinstance(item, int) and not isinstance(item, bool):
            result[key] = item
        elif isinstance(item, str) and len(item) <= 1_000:
            folded = item.casefold()
            if not any(fragment in folded for fragment in _SENSITIVE_META_FRAGMENTS):
                result[key] = item
    return result


class ReviewedSemanticEdgeService:
    __slots__ = (
        "_audit",
        "_edges",
        "_factory",
        "_failpoint",
        "_findings",
        "_outbox",
        "_scope",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        scope_resolver: ScopeResolver,
        *,
        edges: SemanticEdgeRepository | None = None,
        findings: FindingRepository | None = None,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("ReviewedSemanticEdgeService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("ReviewedSemanticEdgeService requires a ScopeResolver.")
        self._factory = factory
        self._scope = scope_resolver
        self._edges = edges or SemanticEdgeRepository()
        self._findings = findings or FindingRepository()
        self._users = users or UserRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    def _resolve(
        self,
        connection: ManagedConnection,
        principal: SessionPrincipal,
        space_id: str,
    ) -> UserRecord:
        try:
            resolved = self._scope.resolve_in_transaction(
                connection, principal, space_id, Capability.CURATE
            )
        except ScopeAuthenticationRequiredError as exc:
            raise SemanticEdgeAuthenticationError(str(exc)) from exc
        except ScopeStaleError as exc:
            raise SemanticEdgeScopeStaleError(str(exc)) from exc
        except ScopeNotFoundError as exc:
            raise SemanticEdgeNotFoundError(str(exc)) from exc
        except ScopeForbiddenError as exc:
            raise SemanticEdgeAuthorizationError(str(exc)) from exc
        except ScopeValidationError as exc:
            raise SemanticEdgeValidationError(str(exc)) from exc
        except ScopeIntegrityError as exc:
            raise SemanticEdgeServiceIntegrityError(str(exc)) from exc
        actor = self._users.require(connection, resolved.actor_user_id)
        if actor.account_status != "active" or actor.scope_epoch != resolved.scope_epoch:
            raise SemanticEdgeScopeStaleError("The canonical actor scope changed.")
        return actor

    def apply(self, command: ApplySemanticEdgeCommand) -> ApplySemanticEdgeResult:
        if not isinstance(command, ApplySemanticEdgeCommand):
            raise TypeError("apply requires an ApplySemanticEdgeCommand.")
        if not isinstance(command.principal, SessionPrincipal):
            raise SemanticEdgeValidationError("A canonical session principal is required.")
        space_id = _uuid(command.space_id, "space_id")
        finding_id = _uuid(command.finding_id, "finding_id")
        source_revision_id = _uuid(command.source_revision_id, "source_revision_id")
        target_revision_id = _uuid(command.target_revision_id, "target_revision_id")
        evidence_sha = _sha(command.evidence_sha256, "evidence_sha256")
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        if (
            isinstance(command.expected_finding_row_version, bool)
            or not isinstance(command.expected_finding_row_version, int)
            or command.expected_finding_row_version < 1
        ):
            raise SemanticEdgeValidationError("expected_finding_row_version is invalid.")
        reason = _reason(command.reason)
        request_meta = _safe_meta(command.request_meta)
        operation_type = "semantic_edge"
        dedup_id = _derived_uuid(
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
                    "evidence_sha256": evidence_sha,
                    "expected_finding_row_version": command.expected_finding_row_version,
                    "finding_id": finding_id,
                    "operation_type": operation_type,
                    "reason": reason,
                    "schema_version": 1,
                    "source_revision_id": source_revision_id,
                    "space_id": space_id,
                    "target_revision_id": target_revision_id,
                }
            )
        ).hexdigest()
        connection = self._factory.open()
        try:
            with transaction(connection):
                finding = self._findings.get(connection, finding_id)
                if finding is None or finding.space_id != space_id:
                    raise SemanticEdgeNotFoundError(
                        "The semantic finding was not found in this space."
                    )
                actor = self._resolve(connection, command.principal, space_id)
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
                    raise SemanticEdgeIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored = begun.operation.result
                        if stored is None:
                            raise SemanticEdgeIdempotencyError(
                                "Committed semantic-edge operation has no result."
                            )
                        replay = ApplySemanticEdgeResult.from_mapping(stored)
                        if (
                            replay.operation_id != operation_id
                            or replay.finding_id != finding_id
                        ):
                            raise SemanticEdgeIdempotencyError(
                                "Stored semantic-edge result is not bound to this command."
                            )
                        return ApplySemanticEdgeResult(
                            request_id=request_id,
                            operation_id=replay.operation_id,
                            finding_id=replay.finding_id,
                            outcome=replay.outcome,
                            finding_row_version=replay.finding_row_version,
                            edge_id=replay.edge_id,
                            edge_row_version=replay.edge_row_version,
                            space_generation=replay.space_generation,
                        )
                    raise SemanticEdgeOperationInProgressError(
                        "The semantic-edge operation is already in progress."
                    )
                try:
                    written = self._edges.apply_approved(
                        connection,
                        SemanticEdgeApplyDraft(
                            finding_id=finding_id,
                            expected_finding_row_version=(
                                command.expected_finding_row_version
                            ),
                            space_id=space_id,
                            source_revision_id=source_revision_id,
                            target_revision_id=target_revision_id,
                            evidence_sha256=evidence_sha,
                            actor_user_id=actor.id,
                            operation_id=operation_id,
                            dedup_id=dedup_id,
                            reason=reason,
                            now_ms=now_ms,
                        ),
                        failpoint=self._hit,
                    )
                except (ValueError, SemanticEdgeConflictError) as exc:
                    raise SemanticEdgeServiceError(str(exc)) from exc
                except SemanticEdgeIntegrityError as exc:
                    raise SemanticEdgeServiceIntegrityError(str(exc)) from exc
                edge = written.edge
                result = ApplySemanticEdgeResult(
                    request_id=request_id,
                    operation_id=operation_id,
                    finding_id=finding_id,
                    outcome=written.outcome,
                    finding_row_version=written.finding_row_version,
                    edge_id=None if edge is None else edge.id,
                    edge_row_version=None if edge is None else edge.row_version,
                    space_generation=written.space_generation,
                )
                details = {
                    "edge_id": result.edge_id,
                    "edge_type": None if edge is None else edge.edge_type,
                    "evidence_sha256": evidence_sha,
                    "finding_id": finding_id,
                    "finding_row_version": result.finding_row_version,
                    "outcome": result.outcome,
                    "rule_version": "p6.4-reviewed-semantic-edge-v1",
                    "schema_version": 1,
                    "source_revision_id": source_revision_id,
                    "space_generation": result.space_generation,
                    "target_revision_id": target_revision_id,
                }
                self._hit("semantic_edge.after_effect")
                self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=_derived_uuid(_AUDIT_NAMESPACE, dedup_id, "apply"),
                        occurred_at_ms=now_ms,
                        request_id=request_id,
                        operation_id=operation_id,
                        action="semantic_edge.apply",
                        outcome=result.outcome,
                        actor_kind="user",
                        actor_user_id=actor.id,
                        actor_display_name=actor.display_name,
                        actor_role_snapshot=actor.system_role,
                        space_id=space_id,
                        item_id=finding.source_item_id,
                        base_revision_id=source_revision_id,
                        new_revision_id=source_revision_id,
                        reason=reason,
                        details=details,
                        request_meta=request_meta,
                    ),
                )
                self._hit("semantic_edge.after_audit")
                self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=f"semantic-edge:{result.outcome}:{dedup_id}",
                        space_id=space_id,
                        aggregate_type=(
                            "knowledge_edge" if edge is not None else "curation_finding"
                        ),
                        aggregate_id=edge.id if edge is not None else finding_id,
                        aggregate_version=(
                            edge.row_version if edge is not None else written.finding_row_version
                        ),
                        event_type=f"semantic_edge.{result.outcome}",
                        payload=details,
                        available_at_ms=now_ms,
                    ),
                )
                self._hit("semantic_edge.after_outbox")
                try:
                    self._users.commit_operation(
                        connection,
                        dedup_id=dedup_id,
                        result=result.as_dict(),
                        http_status=200 if result.outcome == "applied" else 409,
                        completed_at_ms=now_ms,
                    )
                except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
                    raise SemanticEdgeIdempotencyError(str(exc)) from exc
                self._hit("semantic_edge.before_commit")
                return result
        except FindingIntegrityError as exc:
            raise SemanticEdgeServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()


__all__ = [
    "ApplySemanticEdgeCommand",
    "ApplySemanticEdgeResult",
    "ReviewedSemanticEdgeService",
    "SemanticEdgeAuthenticationError",
    "SemanticEdgeAuthorizationError",
    "SemanticEdgeIdempotencyError",
    "SemanticEdgeNotFoundError",
    "SemanticEdgeOperationInProgressError",
    "SemanticEdgeScopeStaleError",
    "SemanticEdgeServiceError",
    "SemanticEdgeServiceIntegrityError",
    "SemanticEdgeValidationError",
]

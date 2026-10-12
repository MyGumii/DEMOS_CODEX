"""Transactional P6.3 finding recording and human-review services.

This slice persists version-bound evidence and review decisions only.  It does
not execute a proposed relation, merge, replacement, lifecycle, publication,
quarantine, deactivation, or purge effect; later phase services consume an
approved finding under their own authorization and idempotency contracts.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
import re
import unicodedata
from typing import Literal
from uuid import UUID, uuid5

from ..db import (
    ConnectionFactory,
    ManagedConnection,
    TransactionMode,
    db_now_ms,
    transaction,
)
from ..errors import KnowledgeCoreError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.findings import (
    FindingConflictError,
    FindingDetection,
    FindingIntegrityError,
    FindingPolicyError,
    FindingPutResult,
    FindingRecord,
    FindingRepository,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.users import (
    OperationIdempotencyConflictError,
    OperationInProgressError,
    UserRecord,
    UserRepository,
)
from ..scope import (
    Capability,
    ResolvedScope,
    ScopeAuthenticationRequiredError,
    ScopeForbiddenError,
    ScopeIntegrityError,
    ScopeNotFoundError,
    ScopeResolver,
    ScopeStaleError,
    ScopeValidationError,
    SessionPrincipal,
)


FindingReviewDecision = Literal["approve", "reject", "ignore"]

_DEDUP_NAMESPACE = UUID("65cc126a-f36b-44f3-a439-2bc5bd774235")
_AUDIT_NAMESPACE = UUID("d962bb1d-f071-4a92-a87d-68426924062e")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
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


class FindingServiceError(KnowledgeCoreError):
    code = "STATE_CONFLICT"
    http_status = 409
    retryable = False

    def __init__(self, message: str, *, details: Mapping[str, object] | None = None):
        super().__init__(message)
        self.details = dict(details or {})

    def as_error(self) -> dict[str, object]:
        return {
            "code": self.code,
            "details": self.details,
            "message": str(self),
            "retryable": self.retryable,
        }


class FindingValidationError(FindingServiceError):
    code = "VALIDATION_ERROR"
    http_status = 400


class FindingAuthenticationError(FindingServiceError):
    code = "AUTHENTICATION_REQUIRED"
    http_status = 401


class FindingAuthorizationError(FindingServiceError):
    code = "FORBIDDEN"
    http_status = 403


class FindingNotFoundError(FindingServiceError):
    code = "NOT_FOUND"
    http_status = 404


class FindingScopeStaleError(FindingServiceError):
    code = "SCOPE_STALE"


class FindingIdempotencyError(FindingServiceError):
    code = "IDEMPOTENCY_CONFLICT"


class FindingOperationInProgressError(FindingServiceError):
    retryable = True


class FindingServiceIntegrityError(FindingServiceError):
    code = "INTERNAL_ERROR"
    http_status = 500


@dataclass(frozen=True, slots=True)
class RecordFindingCommand:
    detection: FindingDetection
    request_id: str
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class ReadFindingRequest:
    principal: SessionPrincipal
    space_id: str
    finding_id: str


@dataclass(frozen=True, slots=True)
class ReviewFindingCommand:
    principal: SessionPrincipal
    finding_id: str
    expected_row_version: int
    decision: FindingReviewDecision
    reason: str
    operation_id: str
    request_id: str
    space_id: str | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class ReviewFindingResult:
    request_id: str
    operation_id: str
    finding_id: str
    outcome: Literal["approved", "rejected", "ignored", "obsolete"]
    row_version: int

    def as_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "operation_id": self.operation_id,
            "outcome": self.outcome,
            "request_id": self.request_id,
            "row_version": self.row_version,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ReviewFindingResult":
        if set(value) != {
            "finding_id",
            "operation_id",
            "outcome",
            "request_id",
            "row_version",
        }:
            raise FindingIdempotencyError("Stored finding review result is invalid.")
        finding_id = _uuid(value["finding_id"], "stored finding_id")
        operation_id = _uuid(value["operation_id"], "stored operation_id")
        request_id = _uuid(value["request_id"], "stored request_id")
        outcome = value["outcome"]
        row_version = value["row_version"]
        if outcome not in {"approved", "rejected", "ignored", "obsolete"}:
            raise FindingIdempotencyError("Stored finding review outcome is invalid.")
        if isinstance(row_version, bool) or not isinstance(row_version, int) or row_version < 2:
            raise FindingIdempotencyError("Stored finding review version is invalid.")
        return cls(
            request_id=request_id,
            operation_id=operation_id,
            finding_id=finding_id,
            outcome=outcome,
            row_version=row_version,
        )


def _uuid(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise FindingValidationError(f"{field_name} must be a canonical lowercase UUID.")
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
        raise FindingValidationError("Finding command evidence must be finite JSON.") from exc


def _derived_uuid(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, "\x00".join(parts)))


def _reason(value: object) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not 1 <= len(value) <= 2_000
        or value != unicodedata.normalize("NFKC", value)
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise FindingValidationError("Review reason is invalid.")
    return value


def _safe_request_meta(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise FindingValidationError("request_meta must be an object.")
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


def _finding_details(record: FindingRecord) -> dict[str, object]:
    return {
        "decision_mode": record.decision_mode,
        "finding_id": record.id,
        "finding_kind": record.finding_kind,
        "proposed_action": record.proposed_action,
        "risk_tier": record.risk_tier,
        "row_version": record.row_version,
        "schema_version": 1,
        "severity": record.severity,
        "source_revision_id": record.source_revision_id,
        "target_revision_id": record.target_revision_id,
    }


class VersionedFindingService:
    """Own short transactions for P6.3 finding records and review state."""

    __slots__ = (
        "_audit",
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
        findings: FindingRepository | None = None,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("VersionedFindingService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("VersionedFindingService requires a ScopeResolver.")
        self._factory = factory
        self._scope = scope_resolver
        self._findings = findings or FindingRepository()
        self._users = users or UserRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    def record(self, command: RecordFindingCommand) -> FindingPutResult:
        if not isinstance(command, RecordFindingCommand):
            raise TypeError("record requires a RecordFindingCommand.")
        _uuid(command.request_id, "request_id")
        safe_meta = _safe_request_meta(command.request_meta)
        connection = self._factory.open()
        try:
            with transaction(connection):
                now_ms = db_now_ms(connection)
                try:
                    result = self._findings.put_detection(
                        connection, command.detection, now_ms=now_ms
                    )
                except FindingPolicyError as exc:
                    raise FindingValidationError(str(exc)) from exc
                except FindingConflictError as exc:
                    raise FindingServiceError(str(exc)) from exc
                except FindingIntegrityError as exc:
                    raise FindingServiceIntegrityError(str(exc)) from exc
                if result.inserted:
                    details = _finding_details(result.finding)
                    self._audit.append(
                        connection,
                        AuditEventDraft(
                            event_uuid=_derived_uuid(
                                _AUDIT_NAMESPACE, result.finding.id, "recorded"
                            ),
                            occurred_at_ms=now_ms,
                            request_id=command.request_id,
                            operation_id=None,
                            action="curation_finding.record",
                            outcome="recorded",
                            actor_kind="system",
                            actor_user_id=None,
                            actor_display_name="P6.3 finding detector",
                            actor_role_snapshot="system",
                            space_id=result.finding.space_id,
                            item_id=result.finding.source_item_id,
                            base_revision_id=result.finding.source_revision_id,
                            new_revision_id=result.finding.source_revision_id,
                            reason=None,
                            details=details,
                            request_meta=safe_meta,
                        ),
                    )
                    self._hit("finding.after_record_audit")
                    self._outbox.append(
                        connection,
                        OutboxEventDraft(
                            logical_key=f"curation-finding:recorded:{result.finding.id}",
                            space_id=result.finding.space_id,
                            aggregate_type="curation_finding",
                            aggregate_id=result.finding.id,
                            aggregate_version=result.finding.row_version,
                            event_type="curation_finding.recorded",
                            payload=details,
                            available_at_ms=now_ms,
                        ),
                    )
                    self._hit("finding.after_record_outbox")
                self._hit("finding.before_record_commit")
                return result
        finally:
            connection.close()

    def read(self, request: ReadFindingRequest) -> FindingRecord:
        """Return one finding through the exact selected-view corpus closure."""

        if not isinstance(request, ReadFindingRequest):
            raise TypeError("read requires a ReadFindingRequest.")
        if not isinstance(request.principal, SessionPrincipal):
            raise FindingValidationError("A canonical session principal is required.")
        space_id = _uuid(request.space_id, "space_id")
        finding_id = _uuid(request.finding_id, "finding_id")
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                try:
                    resolved = self._scope.resolve_in_transaction(
                        connection,
                        request.principal,
                        space_id,
                        Capability.READ,
                    )
                except ScopeAuthenticationRequiredError as exc:
                    raise FindingAuthenticationError(str(exc)) from exc
                except ScopeStaleError as exc:
                    raise FindingScopeStaleError(str(exc)) from exc
                except ScopeNotFoundError as exc:
                    raise FindingNotFoundError(str(exc)) from exc
                except ScopeForbiddenError as exc:
                    raise FindingAuthorizationError(str(exc)) from exc
                except ScopeValidationError as exc:
                    raise FindingValidationError(str(exc)) from exc
                except ScopeIntegrityError as exc:
                    raise FindingServiceIntegrityError(str(exc)) from exc
                finding = self._findings.get(connection, finding_id)
                plan = resolved.access_plan
                if finding is None or plan is None:
                    raise FindingNotFoundError("The finding does not exist.")
                source_ids = frozenset(
                    source.source_space_id
                    for source in plan.corpus_closure.sources
                )
                overlay = next(
                    (
                        candidate
                        for candidate in plan.corpus_closure.distribution_overlays
                        if candidate.source_space_id == finding.space_id
                        and candidate.source_item_id == finding.source_item_id
                        and candidate.source_revision_id == finding.source_revision_id
                    ),
                    None,
                )
                if finding.space_id == plan.selected_view.id:
                    origin: Literal["native", "inherited", "overlay"] = "native"
                elif finding.space_id in source_ids:
                    if not self._finding_endpoints_visible(connection, finding):
                        raise FindingNotFoundError("The finding does not exist.")
                    origin = "inherited"
                elif overlay is not None:
                    if (
                        finding.target_item_id is not None
                        or not self._finding_endpoints_visible(
                            connection,
                            finding,
                            require_current_source=True,
                        )
                    ):
                        raise FindingNotFoundError("The finding does not exist.")
                    origin = "overlay"
                else:
                    raise FindingNotFoundError("The finding does not exist.")
                capabilities = (
                    tuple(capability.value for capability in plan.selected_view.capabilities)
                    if origin == "native"
                    else (Capability.READ.value,)
                )
                return replace(
                    finding,
                    source_space_id=finding.space_id,
                    selected_view_space_id=plan.selected_view.id,
                    inherited=origin != "native",
                    origin=origin,
                    overlay_grant_id=(
                        overlay.grant_id if origin == "overlay" and overlay is not None else None
                    ),
                    capabilities=capabilities,
                    corpus_closure_sha256=plan.corpus_closure.sha256,
                )
        finally:
            connection.close()

    @staticmethod
    def _finding_endpoints_visible(
        connection: ManagedConnection,
        finding: FindingRecord,
        *,
        require_current_source: bool = False,
    ) -> bool:
        source = connection.execute(
            """SELECT lifecycle_status,curation_status,current_revision_id
                 FROM knowledge_item WHERE id=? AND space_id=?""",
            (finding.source_item_id, finding.space_id),
        ).fetchone()
        if source is None or tuple(source[:2]) != ("active", "published"):
            return False
        if require_current_source and source[2] != finding.source_revision_id:
            return False
        if finding.target_item_id is None:
            return True
        target = connection.execute(
            """SELECT lifecycle_status,curation_status
                 FROM knowledge_item WHERE id=? AND space_id=?""",
            (finding.target_item_id, finding.space_id),
        ).fetchone()
        return target is not None and tuple(target) == ("active", "published")

    def _resolve_reviewer(
        self,
        connection: ManagedConnection,
        *,
        principal: SessionPrincipal,
        finding: FindingRecord,
    ) -> tuple[ResolvedScope, UserRecord]:
        try:
            resolved = self._scope.resolve_in_transaction(
                connection, principal, finding.space_id, Capability.CURATE
            )
        except ScopeAuthenticationRequiredError as exc:
            raise FindingAuthenticationError(str(exc)) from exc
        except ScopeStaleError as exc:
            raise FindingScopeStaleError(str(exc)) from exc
        except ScopeNotFoundError as exc:
            raise FindingNotFoundError(str(exc)) from exc
        except ScopeForbiddenError as exc:
            raise FindingAuthorizationError(str(exc)) from exc
        except ScopeValidationError as exc:
            raise FindingValidationError(str(exc)) from exc
        except ScopeIntegrityError as exc:
            raise FindingServiceIntegrityError(str(exc)) from exc
        actor = self._users.require(connection, resolved.actor_user_id)
        if actor.account_status != "active" or actor.scope_epoch != resolved.scope_epoch:
            raise FindingScopeStaleError("The canonical reviewer scope changed.")
        if finding.required_reviewer == "admin" and actor.system_role != "admin":
            raise FindingAuthorizationError("This finding requires an administrator.")
        if finding.required_reviewer == "part_leader" and not (
            actor.system_role == "admin" or resolved.is_part_leader
        ):
            raise FindingAuthorizationError("This finding requires a part leader.")
        if finding.required_reviewer not in {"space_editor", "part_leader", "admin"}:
            raise FindingAuthorizationError("This finding is not human-reviewable.")
        return resolved, actor

    def review(self, command: ReviewFindingCommand) -> ReviewFindingResult:
        if not isinstance(command, ReviewFindingCommand):
            raise TypeError("review requires a ReviewFindingCommand.")
        if not isinstance(command.principal, SessionPrincipal):
            raise FindingValidationError("A canonical session principal is required.")
        finding_id = _uuid(command.finding_id, "finding_id")
        selected_space_id = (
            None
            if command.space_id is None
            else _uuid(command.space_id, "space_id")
        )
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        if (
            isinstance(command.expected_row_version, bool)
            or not isinstance(command.expected_row_version, int)
            or command.expected_row_version < 1
        ):
            raise FindingValidationError("expected_row_version is invalid.")
        if command.decision not in {"approve", "reject", "ignore"}:
            raise FindingValidationError("Finding review decision is invalid.")
        reason = _reason(command.reason)
        safe_meta = _safe_request_meta(command.request_meta)
        operation_type = "curation_finding.review"
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
                    "decision": command.decision,
                    "expected_row_version": command.expected_row_version,
                    "finding_id": finding_id,
                    "operation_type": operation_type,
                    "reason": reason,
                    "schema_version": 1,
                    "space_id": selected_space_id,
                }
            )
        ).hexdigest()
        connection = self._factory.open()
        try:
            with transaction(connection):
                finding = self._findings.get(connection, finding_id)
                if finding is None or (
                    selected_space_id is not None
                    and finding.space_id != selected_space_id
                ):
                    raise FindingNotFoundError("The finding does not exist.")
                _, actor = self._resolve_reviewer(
                    connection, principal=command.principal, finding=finding
                )
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
                    raise FindingIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored = begun.operation.result
                        if stored is None:
                            raise FindingIdempotencyError(
                                "Committed finding review has no result."
                            )
                        replay = ReviewFindingResult.from_mapping(stored)
                        if (
                            replay.operation_id != operation_id
                            or replay.finding_id != finding_id
                        ):
                            raise FindingIdempotencyError(
                                "Stored finding review is not bound to this command."
                            )
                        return ReviewFindingResult(
                            request_id=request_id,
                            operation_id=replay.operation_id,
                            finding_id=replay.finding_id,
                            outcome=replay.outcome,
                            row_version=replay.row_version,
                        )
                    raise FindingOperationInProgressError(
                        "The finding review is already in progress."
                    )
                try:
                    written = self._findings.review(
                        connection,
                        finding_id=finding_id,
                        actor_user_id=actor.id,
                        decision=command.decision,
                        reason=reason,
                        expected_row_version=command.expected_row_version,
                        now_ms=now_ms,
                    )
                    self._hit("curation.after_review_state")
                    self._hit("curation.after_visibility_state")
                    self._hit("curation.after_domain_effect")
                except FindingPolicyError as exc:
                    raise FindingValidationError(str(exc)) from exc
                except FindingConflictError as exc:
                    raise FindingServiceError(str(exc)) from exc
                except FindingIntegrityError as exc:
                    raise FindingServiceIntegrityError(str(exc)) from exc
                result = ReviewFindingResult(
                    request_id=request_id,
                    operation_id=operation_id,
                    finding_id=written.finding.id,
                    outcome=written.outcome,
                    row_version=written.finding.row_version,
                )
                details = {
                    **_finding_details(written.finding),
                    "decision": command.decision,
                    "outcome": written.outcome,
                }
                self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=_derived_uuid(_AUDIT_NAMESPACE, dedup_id, "review"),
                        occurred_at_ms=now_ms,
                        request_id=request_id,
                        operation_id=operation_id,
                        action="curation_finding.review",
                        outcome=written.outcome,
                        actor_kind="user",
                        actor_user_id=actor.id,
                        actor_display_name=actor.display_name,
                        actor_role_snapshot=actor.system_role,
                        space_id=written.finding.space_id,
                        item_id=written.finding.source_item_id,
                        base_revision_id=written.finding.source_revision_id,
                        new_revision_id=written.finding.source_revision_id,
                        reason=reason,
                        details=details,
                        request_meta=safe_meta,
                    ),
                )
                self._hit("finding.after_review_audit")
                self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=f"curation-finding:reviewed:{dedup_id}",
                        space_id=written.finding.space_id,
                        aggregate_type="curation_finding",
                        aggregate_id=written.finding.id,
                        aggregate_version=written.finding.row_version,
                        event_type="curation_finding.reviewed",
                        payload=details,
                        available_at_ms=now_ms,
                    ),
                )
                self._hit("finding.after_review_outbox")
                try:
                    self._users.commit_operation(
                        connection,
                        dedup_id=dedup_id,
                        result=result.as_dict(),
                        http_status=200,
                        completed_at_ms=now_ms,
                    )
                except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
                    raise FindingIdempotencyError(str(exc)) from exc
                self._hit("finding.before_review_commit")
                return result
        finally:
            connection.close()


__all__ = [
    "FindingAuthenticationError",
    "FindingAuthorizationError",
    "FindingIdempotencyError",
    "FindingNotFoundError",
    "FindingOperationInProgressError",
    "FindingScopeStaleError",
    "FindingServiceError",
    "FindingServiceIntegrityError",
    "FindingValidationError",
    "RecordFindingCommand",
    "ReadFindingRequest",
    "ReviewFindingCommand",
    "ReviewFindingResult",
    "VersionedFindingService",
]

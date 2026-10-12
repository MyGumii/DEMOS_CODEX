"""Transaction-owning v12 temporal assertion and verification service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from uuid import UUID, uuid5

from ..db import ConnectionFactory, TransactionMode, db_now_ms, transaction
from ..domain import (
    TemporalAssertionKind,
    TemporalAssertionOrigin,
    TemporalPrecision,
)
from ..errors import KnowledgeCoreError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.temporal import (
    TemporalAssertionDraft,
    TemporalAssertionRecord,
    TemporalHeadRecord,
    TemporalItemRecord,
    TemporalItemUnavailableError,
    TemporalRepository,
    TemporalRepositoryError,
    TemporalStateConflictError,
    VerificationDraft,
    VerificationHeadRecord,
    VerificationRecord,
)
from ..repositories.users import (
    OperationIdempotencyConflictError,
    OperationInProgressError,
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


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_DEDUP_NAMESPACE = UUID("9b38cf0f-2e91-4054-bc25-b31905250546")
_ASSERTION_NAMESPACE = UUID("a19993f2-3cde-4e0c-a2b9-7630732568b0")
_VERIFICATION_NAMESPACE = UUID("7b214041-91e2-43cf-91aa-4409942dd00b")
_AUDIT_NAMESPACE = UUID("6e023db6-043f-4fc8-8c88-4a156aac22d2")


class TemporalServiceError(KnowledgeCoreError):
    code = "TEMPORAL_HEAD_CONFLICT"
    http_status = 409
    retryable = False


class TemporalValueInvalidError(TemporalServiceError):
    code = "TEMPORAL_VALUE_INVALID"
    http_status = 422


class TemporalHeadConflictError(TemporalServiceError):
    code = "TEMPORAL_HEAD_CONFLICT"


class VerificationNoncurrentError(TemporalServiceError):
    code = "VERIFICATION_NONCURRENT"


class TemporalAuthorizationError(TemporalServiceError):
    code = "FORBIDDEN"
    http_status = 403


class TemporalNotFoundError(TemporalServiceError):
    code = "NOT_FOUND"
    http_status = 404


class TemporalScopeStaleError(TemporalServiceError):
    code = "SCOPE_STALE"


class TemporalIdempotencyError(TemporalServiceError):
    code = "IDEMPOTENCY_CONFLICT"


class TemporalOperationInProgressError(TemporalServiceError):
    code = "OPERATION_IN_PROGRESS"
    retryable = True


class TemporalServiceIntegrityError(TemporalServiceError):
    code = "INTERNAL_ERROR"
    http_status = 500


@dataclass(frozen=True, slots=True)
class TemporalValue:
    precision: TemporalPrecision
    value: str
    instant_ms: int | None
    source_timezone: str
    source_utc_offset_minutes: int | None

    def tagged(self) -> dict[str, object]:
        result: dict[str, object] = {
            "precision": self.precision.value,
            "value": self.value,
        }
        if self.precision is TemporalPrecision.DATE:
            result["timezone"] = "Asia/Seoul"
        return result


@dataclass(frozen=True, slots=True)
class TemporalAssertionCommand:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    expected_revision_id: str
    assertion_kind: TemporalAssertionKind
    value: Mapping[str, object]
    expected_head_event_id: str | None
    expected_head_generation: int
    origin: TemporalAssertionOrigin
    evidence_sha256: str
    evidence_reference: str
    operation_id: str
    request_id: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TemporalAssertionRevokeCommand:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    expected_revision_id: str
    assertion_id: str
    expected_head_generation: int
    reason: str
    operation_id: str
    request_id: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerificationAttestCommand:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    expected_revision_id: str
    expected_temporal_summary_sha256: str
    expected_head_event_id: str | None
    expected_head_generation: int
    method: str
    evidence_sha256: str
    evidence_reference: str
    note: str | None
    subject_verified_at: str | None
    operation_id: str
    request_id: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerificationRevokeCommand:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    expected_revision_id: str
    attestation_id: str
    expected_head_generation: int
    reason: str
    operation_id: str
    request_id: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TemporalReadRequest:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    after_audit_id: int = 0
    limit: int = 100


def _uuid(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise TemporalValueInvalidError(
            f"{field_name} must be a canonical lowercase UUID."
        )
    return value


def _sha(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise TemporalValueInvalidError(f"{field_name} must be a SHA-256 digest.")
    return value


def _text(value: object, *, field_name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TemporalValueInvalidError(f"{field_name} is required.")
    result = value.strip()
    if not 1 <= len(result) <= maximum or "\x00" in result:
        raise TemporalValueInvalidError(f"{field_name} is outside its bound.")
    return result


def _head_generation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TemporalValueInvalidError(
            "expected_head_generation must be a nonnegative integer."
        )
    return value


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _utc_iso(milliseconds: int) -> str:
    seconds, millis = divmod(milliseconds, 1000)
    value = datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
        microsecond=millis * 1000
    )
    rendered = value.isoformat(timespec="milliseconds")
    return rendered.replace("+00:00", "Z")


def parse_temporal_value(value: Mapping[str, object]) -> TemporalValue:
    if not isinstance(value, Mapping):
        raise TemporalValueInvalidError("A tagged temporal value is required.")
    precision = value.get("precision")
    raw = value.get("value")
    if precision == "date":
        if set(value) != {"precision", "value", "timezone"}:
            raise TemporalValueInvalidError("A date temporal value has invalid fields.")
        if value.get("timezone") != "Asia/Seoul" or not isinstance(raw, str):
            raise TemporalValueInvalidError(
                "A date temporal value requires timezone Asia/Seoul."
            )
        try:
            parsed = date.fromisoformat(raw)
        except ValueError as exc:
            raise TemporalValueInvalidError(
                "The date temporal value is not a real calendar date."
            ) from exc
        if parsed.isoformat() != raw:
            raise TemporalValueInvalidError("The date temporal value is not canonical.")
        return TemporalValue(
            precision=TemporalPrecision.DATE,
            value=raw,
            instant_ms=None,
            source_timezone="Asia/Seoul",
            source_utc_offset_minutes=None,
        )
    if precision != "instant" or set(value) != {"precision", "value"}:
        raise TemporalValueInvalidError("An instant temporal value has invalid fields.")
    if not isinstance(raw, str) or len(raw) > 64:
        raise TemporalValueInvalidError("The instant temporal value is invalid.")
    source = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(source)
    except ValueError as exc:
        raise TemporalValueInvalidError(
            "The instant temporal value is not ISO-8601."
        ) from exc
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None:
        raise TemporalValueInvalidError("The instant temporal value needs an offset.")
    if parsed.second == 60:
        raise TemporalValueInvalidError("Leap-second instants are unsupported.")
    instant_ms = int(parsed.timestamp() * 1000)
    if instant_ms < 0 or parsed.microsecond % 1000 != 0:
        raise TemporalValueInvalidError(
            "Instant precision must be exact nonnegative milliseconds."
        )
    offset_minutes = int(offset.total_seconds() // 60)
    if not -840 <= offset_minutes <= 840:
        raise TemporalValueInvalidError("The instant offset is outside its bound.")
    return TemporalValue(
        precision=TemporalPrecision.INSTANT,
        value=_utc_iso(instant_ms),
        instant_ms=instant_ms,
        source_timezone=("UTC" if offset_minutes == 0 else f"UTC{raw[-6:]}"),
        source_utc_offset_minutes=offset_minutes,
    )


def normalize_notion_source_timestamp(value: object) -> int:
    """Convert one unambiguous 10-digit Notion seconds value to milliseconds."""

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1_000_000_000 <= value <= 9_999_999_999
    ):
        raise TemporalValueInvalidError(
            "A Notion source timestamp must be an exact 10-digit seconds integer."
        )
    return value * 1000


def _event_head(record: TemporalAssertionRecord) -> dict[str, object]:
    if record.event_kind == "revoke":
        return {"event_id": record.id, "state": "revoked", "value": None}
    if record.value_precision is TemporalPrecision.INSTANT:
        tagged: dict[str, object] = {
            "precision": "instant",
            "value": _utc_iso(int(record.instant_value_ms)),
        }
    else:
        tagged = {
            "precision": "date",
            "timezone": "Asia/Seoul",
            "value": record.date_value,
        }
    return {"event_id": record.id, "state": "asserted", "value": tagged}


def temporal_summary_document(
    *,
    revision_id: str,
    source_modified: TemporalAssertionRecord | None,
    information_effective: TemporalAssertionRecord | None,
) -> dict[str, object]:
    return {
        "information_effective_head": (
            None if information_effective is None else _event_head(information_effective)
        ),
        "revision_id": revision_id,
        "schema_version": 1,
        "source_modified_head": (
            None if source_modified is None else _event_head(source_modified)
        ),
    }


def temporal_summary_sha256(
    *,
    revision_id: str,
    source_modified: TemporalAssertionRecord | None,
    information_effective: TemporalAssertionRecord | None,
) -> str:
    return sha256(
        _canonical_bytes(
            temporal_summary_document(
                revision_id=revision_id,
                source_modified=source_modified,
                information_effective=information_effective,
            )
        )
    ).hexdigest()


class TemporalService:
    __slots__ = (
        "_audit",
        "_factory",
        "_outbox",
        "_repository",
        "_scope",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        scope_resolver: ScopeResolver,
        *,
        repository: TemporalRepository | None = None,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("TemporalService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("TemporalService requires a ScopeResolver.")
        self._factory = factory
        self._scope = scope_resolver
        self._repository = repository or TemporalRepository()
        self._users = users or UserRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()

    @staticmethod
    def _dedup_id(actor: str, operation_type: str, operation_id: str) -> str:
        return str(uuid5(_DEDUP_NAMESPACE, f"{actor}:{operation_type}:{operation_id}"))

    @staticmethod
    def _request_hash(operation_type: str, values: Mapping[str, object]) -> str:
        return sha256(
            _canonical_bytes(
                {"operation_type": operation_type, "schema_version": 1, **values}
            )
        ).hexdigest()

    def _resolve(self, connection, command, *, capability: Capability):
        try:
            resolved = self._scope.resolve_in_transaction(
                connection, command.principal, command.space_id, capability
            )
        except ScopeAuthenticationRequiredError as exc:
            raise TemporalAuthorizationError(str(exc)) from exc
        except ScopeForbiddenError as exc:
            raise TemporalAuthorizationError(str(exc)) from exc
        except ScopeStaleError as exc:
            raise TemporalScopeStaleError(str(exc)) from exc
        except ScopeNotFoundError as exc:
            raise TemporalNotFoundError(str(exc)) from exc
        except ScopeValidationError as exc:
            raise TemporalValueInvalidError(str(exc)) from exc
        except ScopeIntegrityError as exc:
            raise TemporalServiceIntegrityError(str(exc)) from exc
        actor = self._users.require(connection, resolved.actor_user_id)
        if actor.account_status != "active" or actor.scope_epoch != resolved.scope_epoch:
            raise TemporalScopeStaleError("The canonical actor scope changed.")
        return resolved, actor

    @staticmethod
    def _head_pair(repository, connection, revision_id, kind):
        pair = repository.get_assertion_head(
            connection, revision_id=revision_id, kind=kind
        )
        return (None, None) if pair is None else pair

    def _summary(self, connection, revision_id: str):
        source_pair = self._repository.get_assertion_head(
            connection,
            revision_id=revision_id,
            kind=TemporalAssertionKind.SOURCE_MODIFIED,
        )
        effective_pair = self._repository.get_assertion_head(
            connection,
            revision_id=revision_id,
            kind=TemporalAssertionKind.INFORMATION_EFFECTIVE,
        )
        source = None if source_pair is None else source_pair[1]
        effective = None if effective_pair is None else effective_pair[1]
        digest = temporal_summary_sha256(
            revision_id=revision_id,
            source_modified=source,
            information_effective=effective,
        )
        return source_pair, effective_pair, digest

    @staticmethod
    def _check_expected_head(
        pair,
        *,
        expected_event_id: str | None,
        expected_generation: int,
    ) -> tuple[str | None, int]:
        expected_generation = _head_generation(expected_generation)
        if expected_event_id is not None:
            expected_event_id = _uuid(expected_event_id, field_name="expected_head_event_id")
        if pair is None:
            if expected_event_id is not None or expected_generation != 0:
                raise TemporalHeadConflictError("The expected temporal head is stale.")
            return None, 1
        head = pair[0]
        if (
            head.head_event_id != expected_event_id
            or head.head_generation != expected_generation
        ):
            raise TemporalHeadConflictError("The expected temporal head is stale.")
        return head.head_event_id, head.head_generation + 1

    def _begin(self, connection, *, actor_id, operation_type, operation_id, request_hash, now_ms):
        dedup_id = self._dedup_id(actor_id, operation_type, operation_id)
        try:
            begun = self._users.begin_operation(
                connection,
                actor_user_id=actor_id,
                operation_type=operation_type,
                operation_id=operation_id,
                request_sha256=request_hash,
                dedup_id=dedup_id,
                now_ms=now_ms,
            )
        except OperationIdempotencyConflictError as exc:
            raise TemporalIdempotencyError(str(exc)) from exc
        except OperationInProgressError as exc:
            raise TemporalOperationInProgressError(str(exc)) from exc
        if not begun.inserted:
            if begun.operation.status == "committed" and begun.operation.result is not None:
                return dedup_id, dict(begun.operation.result)
            if begun.operation.status == "in_progress":
                raise TemporalOperationInProgressError(
                    "The temporal operation is already in progress."
                )
            raise TemporalIdempotencyError(
                "The temporal operation has an incompatible terminal state."
            )
        return dedup_id, None

    def _finish(self, connection, *, dedup_id: str, result: Mapping[str, object], now_ms: int):
        self._users.commit_operation(
            connection,
            dedup_id=dedup_id,
            result=result,
            http_status=200,
            completed_at_ms=now_ms,
        )

    @staticmethod
    def _authority_proof(resolved, item: TemporalItemRecord) -> str:
        plan = resolved.access_plan
        value = {
            "actor_row_version": resolved.actor_row_version,
            "actor_user_id": resolved.actor_user_id,
            "item_id": item.item_id,
            "native_mutation_space_id": (
                None if plan is None else plan.native_mutation_space_id
            ),
            "revision_id": item.revision_id,
            "role_assignment_generation": (
                None if plan is None else plan.role_assignment_generation
            ),
            "role_assignment_id": None if plan is None else plan.role_assignment_id,
            "schema_version": 1,
            "scope_epoch": resolved.scope_epoch,
            "space_id": item.space_id,
            "space_row_version": resolved.space_row_version,
        }
        return sha256(_canonical_bytes(value)).hexdigest()

    def assert_temporal(self, command: TemporalAssertionCommand) -> dict[str, object]:
        if not isinstance(command, TemporalAssertionCommand):
            raise TypeError("assert_temporal requires a TemporalAssertionCommand.")
        value = parse_temporal_value(command.value)
        _uuid(command.space_id, field_name="space_id")
        _uuid(command.item_id, field_name="item_id")
        _uuid(command.expected_revision_id, field_name="expected_revision_id")
        _uuid(command.operation_id, field_name="operation_id")
        _uuid(command.request_id, field_name="request_id")
        if not isinstance(command.assertion_kind, TemporalAssertionKind):
            raise TemporalValueInvalidError("assertion_kind is invalid.")
        if not isinstance(command.origin, TemporalAssertionOrigin):
            raise TemporalValueInvalidError("origin is invalid.")
        if (
            command.origin is TemporalAssertionOrigin.SYSTEM_IMPORT
            and command.assertion_kind is not TemporalAssertionKind.SOURCE_MODIFIED
        ):
            raise TemporalValueInvalidError(
                "system_import may assert source_modified only."
            )
        evidence_sha = _sha(command.evidence_sha256, field_name="evidence_sha256")
        evidence_reference = _text(
            command.evidence_reference, field_name="evidence_reference", maximum=1000
        )
        request_hash = self._request_hash(
            "temporal.assert",
            {
                "actor_scope_epoch": command.principal.captured_scope_epoch,
                "actor_user_id": command.principal.user_id,
                "assertion_kind": command.assertion_kind.value,
                "evidence_reference": evidence_reference,
                "evidence_sha256": evidence_sha,
                "expected_head_event_id": command.expected_head_event_id,
                "expected_head_generation": command.expected_head_generation,
                "expected_revision_id": command.expected_revision_id,
                "item_id": command.item_id,
                "origin": command.origin.value,
                "space_id": command.space_id,
                "value": value.tagged(),
            },
        )
        connection = self._factory.open()
        try:
            with transaction(connection):
                resolved, actor = self._resolve(
                    connection, command, capability=Capability.TEMPORAL_MUTATE
                )
                now_ms = db_now_ms(connection)
                dedup_id, replay = self._begin(
                    connection,
                    actor_id=actor.id,
                    operation_type="temporal.assert",
                    operation_id=command.operation_id,
                    request_hash=request_hash,
                    now_ms=now_ms,
                )
                if replay is not None:
                    replay["request_id"] = command.request_id
                    return replay
                item = self._repository.require_current_item(
                    connection, space_id=command.space_id, item_id=command.item_id
                )
                if item.revision_id != command.expected_revision_id:
                    raise TemporalHeadConflictError("The current revision changed.")
                source_pair, effective_pair, _old_summary = self._summary(
                    connection, item.revision_id
                )
                pair = (
                    source_pair
                    if command.assertion_kind is TemporalAssertionKind.SOURCE_MODIFIED
                    else effective_pair
                )
                predecessor, generation = self._check_expected_head(
                    pair,
                    expected_event_id=command.expected_head_event_id,
                    expected_generation=command.expected_head_generation,
                )
                event_id = str(
                    uuid5(
                        _ASSERTION_NAMESPACE,
                        f"{command.operation_id}:{command.assertion_kind.value}:assert",
                    )
                )
                provisional = TemporalAssertionRecord(
                    event_sequence=0,
                    id=event_id,
                    space_id=item.space_id,
                    item_id=item.item_id,
                    revision_id=item.revision_id,
                    assertion_kind=command.assertion_kind,
                    event_kind="assert",
                    value_precision=value.precision,
                    instant_value_ms=value.instant_ms,
                    date_value=(value.value if value.precision is TemporalPrecision.DATE else None),
                    source_timezone=value.source_timezone,
                    source_utc_offset_minutes=value.source_utc_offset_minutes,
                    origin=command.origin,
                    evidence_sha256=evidence_sha,
                    evidence_reference=evidence_reference,
                    predecessor_event_id=predecessor,
                    head_generation=generation,
                    actor_user_id=actor.id,
                    operation_id=command.operation_id,
                    effect_key=f"temporal:{item.revision_id}:{command.assertion_kind.value}:{generation}",
                    temporal_summary_sha256="0" * 64,
                    audit_event_id=0,
                    outbox_event_id=0,
                    committed_at_ms=now_ms,
                )
                source = provisional if command.assertion_kind is TemporalAssertionKind.SOURCE_MODIFIED else (None if source_pair is None else source_pair[1])
                effective = provisional if command.assertion_kind is TemporalAssertionKind.INFORMATION_EFFECTIVE else (None if effective_pair is None else effective_pair[1])
                summary = temporal_summary_sha256(
                    revision_id=item.revision_id,
                    source_modified=source,
                    information_effective=effective,
                )
                temporal_generation = self._repository.advance_temporal_generation(
                    connection, item=item, now_ms=now_ms
                )
                audit = self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=str(uuid5(_AUDIT_NAMESPACE, f"assert:{event_id}")),
                        occurred_at_ms=now_ms,
                        request_id=command.request_id,
                        operation_id=command.operation_id,
                        action="temporal.assert",
                        outcome=("asserted" if generation == 1 else "superseded"),
                        actor_kind="user",
                        actor_user_id=actor.id,
                        actor_display_name=actor.display_name,
                        actor_role_snapshot=actor.system_role,
                        space_id=item.space_id,
                        item_id=item.item_id,
                        base_revision_id=item.revision_id,
                        new_revision_id=item.revision_id,
                        reason=None,
                        details={
                            "assertion_id": event_id,
                            "assertion_kind": command.assertion_kind.value,
                            "head_generation": generation,
                            "supersedes_assertion_id": predecessor,
                            "schema_version": 1,
                            "temporal_generation": temporal_generation,
                            "temporal_summary_sha256": summary,
                        },
                        request_meta=dict(command.request_meta),
                    ),
                )
                outbox = self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=f"temporal-assert:{event_id}",
                        space_id=item.space_id,
                        aggregate_type="knowledge_item",
                        aggregate_id=item.item_id,
                        aggregate_version=temporal_generation,
                        event_type="knowledge.temporal.asserted",
                        payload={
                            "assertion_id": event_id,
                            "assertion_kind": command.assertion_kind.value,
                            "item_id": item.item_id,
                            "revision_id": item.revision_id,
                            "schema_version": 1,
                            "temporal_generation": temporal_generation,
                            "temporal_summary_sha256": summary,
                            "supersedes_assertion_id": predecessor,
                        },
                        available_at_ms=now_ms,
                    ),
                )
                record = self._repository.append_assertion(
                    connection,
                    TemporalAssertionDraft(
                        id=event_id,
                        space_id=item.space_id,
                        item_id=item.item_id,
                        revision_id=item.revision_id,
                        assertion_kind=command.assertion_kind,
                        event_kind="assert",
                        value_precision=value.precision,
                        instant_value_ms=value.instant_ms,
                        date_value=(value.value if value.precision is TemporalPrecision.DATE else None),
                        source_timezone=value.source_timezone,
                        source_utc_offset_minutes=value.source_utc_offset_minutes,
                        origin=command.origin,
                        evidence_sha256=evidence_sha,
                        evidence_reference=evidence_reference,
                        predecessor_event_id=predecessor,
                        head_generation=generation,
                        actor_user_id=actor.id,
                        operation_id=command.operation_id,
                        effect_key=provisional.effect_key,
                        temporal_summary_sha256=summary,
                        audit_event_id=audit.id,
                        outbox_event_id=outbox.id,
                        committed_at_ms=now_ms,
                    ),
                )
                invalidated = self._repository.invalidate_current_ancestors(
                    connection, space_id=item.space_id, now_ms=now_ms
                )
                result = {
                    "assertion_id": record.id,
                    "assertion_kind": record.assertion_kind.value,
                    "head_generation": record.head_generation,
                    "operation_id": command.operation_id,
                    "request_id": command.request_id,
                    "revision_id": record.revision_id,
                    "temporal_generation": temporal_generation,
                    "temporal_summary_sha256": summary,
                    "value": value.tagged(),
                    "invalidated_ancestor_count": invalidated,
                }
                self._finish(connection, dedup_id=dedup_id, result=result, now_ms=now_ms)
                return result
        except TemporalItemUnavailableError as exc:
            raise TemporalNotFoundError(str(exc)) from exc
        except TemporalStateConflictError as exc:
            raise TemporalHeadConflictError(str(exc)) from exc
        except TemporalRepositoryError as exc:
            raise TemporalServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()

    def revoke_assertion(
        self, command: TemporalAssertionRevokeCommand
    ) -> dict[str, object]:
        if not isinstance(command, TemporalAssertionRevokeCommand):
            raise TypeError("revoke_assertion requires a command.")
        reason = _text(command.reason, field_name="reason", maximum=2000)
        _uuid(command.assertion_id, field_name="assertion_id")
        # Reuse the assertion path's transactional kernel through a private
        # dedicated implementation so a revoke carries no fabricated value.
        return self._revoke_assertion(command, reason=reason)

    def _revoke_assertion(
        self,
        command: TemporalAssertionRevokeCommand,
        *,
        reason: str,
    ) -> dict[str, object]:
        for value, label in (
            (command.space_id, "space_id"),
            (command.item_id, "item_id"),
            (command.expected_revision_id, "expected_revision_id"),
            (command.operation_id, "operation_id"),
            (command.request_id, "request_id"),
        ):
            _uuid(value, field_name=label)
        request_hash = self._request_hash(
            "temporal.revoke",
            {
                "actor_scope_epoch": command.principal.captured_scope_epoch,
                "actor_user_id": command.principal.user_id,
                "assertion_id": command.assertion_id,
                "expected_head_generation": command.expected_head_generation,
                "expected_revision_id": command.expected_revision_id,
                "item_id": command.item_id,
                "reason": reason,
                "space_id": command.space_id,
            },
        )
        connection = self._factory.open()
        try:
            with transaction(connection):
                _resolved, actor = self._resolve(
                    connection, command, capability=Capability.TEMPORAL_MUTATE
                )
                now_ms = db_now_ms(connection)
                dedup_id, replay = self._begin(
                    connection,
                    actor_id=actor.id,
                    operation_type="temporal.revoke",
                    operation_id=command.operation_id,
                    request_hash=request_hash,
                    now_ms=now_ms,
                )
                if replay is not None:
                    replay["request_id"] = command.request_id
                    return replay
                item = self._repository.require_current_item(
                    connection, space_id=command.space_id, item_id=command.item_id
                )
                if item.revision_id != command.expected_revision_id:
                    raise TemporalHeadConflictError("The current revision changed.")
                target = self._repository.get_assertion(
                    connection, command.assertion_id
                )
                if target is None or target.revision_id != item.revision_id:
                    raise TemporalNotFoundError("The temporal assertion is unavailable.")
                pair = self._repository.get_assertion_head(
                    connection,
                    revision_id=item.revision_id,
                    kind=target.assertion_kind,
                )
                if (
                    pair is None
                    or pair[0].head_event_id != target.id
                    or pair[0].head_generation
                    != _head_generation(command.expected_head_generation)
                    or pair[0].state != "asserted"
                ):
                    raise TemporalHeadConflictError(
                        "Only the exact current head can be revoked."
                    )
                event_id = str(
                    uuid5(
                        _ASSERTION_NAMESPACE,
                        f"{command.operation_id}:{target.assertion_kind.value}:revoke",
                    )
                )
                provisional = TemporalAssertionRecord(
                    event_sequence=0,
                    id=event_id,
                    space_id=item.space_id,
                    item_id=item.item_id,
                    revision_id=item.revision_id,
                    assertion_kind=target.assertion_kind,
                    event_kind="revoke",
                    value_precision=None,
                    instant_value_ms=None,
                    date_value=None,
                    source_timezone=None,
                    source_utc_offset_minutes=None,
                    origin=TemporalAssertionOrigin.USER,
                    evidence_sha256=sha256(reason.encode("utf-8")).hexdigest(),
                    evidence_reference="user-revocation",
                    predecessor_event_id=target.id,
                    head_generation=target.head_generation + 1,
                    actor_user_id=actor.id,
                    operation_id=command.operation_id,
                    effect_key=(
                        f"temporal:{item.revision_id}:"
                        f"{target.assertion_kind.value}:"
                        f"{target.head_generation + 1}"
                    ),
                    temporal_summary_sha256="0" * 64,
                    audit_event_id=0,
                    outbox_event_id=0,
                    committed_at_ms=now_ms,
                )
                source_pair, effective_pair, _ = self._summary(
                    connection, item.revision_id
                )
                source = (
                    provisional
                    if target.assertion_kind is TemporalAssertionKind.SOURCE_MODIFIED
                    else None if source_pair is None else source_pair[1]
                )
                effective = (
                    provisional
                    if target.assertion_kind
                    is TemporalAssertionKind.INFORMATION_EFFECTIVE
                    else None if effective_pair is None else effective_pair[1]
                )
                summary = temporal_summary_sha256(
                    revision_id=item.revision_id,
                    source_modified=source,
                    information_effective=effective,
                )
                temporal_generation = (
                    self._repository.advance_temporal_generation(
                        connection, item=item, now_ms=now_ms
                    )
                )
                audit = self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=str(
                            uuid5(_AUDIT_NAMESPACE, f"revoke:{event_id}")
                        ),
                        occurred_at_ms=now_ms,
                        request_id=command.request_id,
                        operation_id=command.operation_id,
                        action="temporal.revoke",
                        outcome="revoked",
                        actor_kind="user",
                        actor_user_id=actor.id,
                        actor_display_name=actor.display_name,
                        actor_role_snapshot=actor.system_role,
                        space_id=item.space_id,
                        item_id=item.item_id,
                        base_revision_id=item.revision_id,
                        new_revision_id=item.revision_id,
                        reason=reason,
                        details={
                            "assertion_id": event_id,
                            "assertion_kind": target.assertion_kind.value,
                            "head_generation": target.head_generation + 1,
                            "schema_version": 1,
                            "temporal_generation": temporal_generation,
                            "temporal_summary_sha256": summary,
                        },
                        request_meta=dict(command.request_meta),
                    ),
                )
                outbox = self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=f"temporal-revoke:{event_id}",
                        space_id=item.space_id,
                        aggregate_type="knowledge_item",
                        aggregate_id=item.item_id,
                        aggregate_version=temporal_generation,
                        event_type="knowledge.temporal.revoked",
                        payload={
                            "assertion_id": event_id,
                            "assertion_kind": target.assertion_kind.value,
                            "item_id": item.item_id,
                            "revision_id": item.revision_id,
                            "schema_version": 1,
                            "temporal_generation": temporal_generation,
                            "temporal_summary_sha256": summary,
                        },
                        available_at_ms=now_ms,
                    ),
                )
                record = self._repository.append_assertion(
                    connection,
                    TemporalAssertionDraft(
                        id=event_id,
                        space_id=item.space_id,
                        item_id=item.item_id,
                        revision_id=item.revision_id,
                        assertion_kind=target.assertion_kind,
                        event_kind="revoke",
                        value_precision=None,
                        instant_value_ms=None,
                        date_value=None,
                        source_timezone=None,
                        source_utc_offset_minutes=None,
                        origin=TemporalAssertionOrigin.USER,
                        evidence_sha256=provisional.evidence_sha256,
                        evidence_reference="user-revocation",
                        predecessor_event_id=target.id,
                        head_generation=target.head_generation + 1,
                        actor_user_id=actor.id,
                        operation_id=command.operation_id,
                        effect_key=provisional.effect_key,
                        temporal_summary_sha256=summary,
                        audit_event_id=audit.id,
                        outbox_event_id=outbox.id,
                        committed_at_ms=now_ms,
                    ),
                )
                invalidated = self._repository.invalidate_current_ancestors(
                    connection, space_id=item.space_id, now_ms=now_ms
                )
                result = {
                    "assertion_id": record.id,
                    "assertion_kind": record.assertion_kind.value,
                    "head_generation": record.head_generation,
                    "operation_id": command.operation_id,
                    "request_id": command.request_id,
                    "revision_id": record.revision_id,
                    "temporal_generation": temporal_generation,
                    "temporal_summary_sha256": summary,
                    "value": None,
                    "invalidated_ancestor_count": invalidated,
                }
                self._finish(
                    connection,
                    dedup_id=dedup_id,
                    result=result,
                    now_ms=now_ms,
                )
                return result
        except TemporalItemUnavailableError as exc:
            raise TemporalNotFoundError(str(exc)) from exc
        except TemporalStateConflictError as exc:
            raise TemporalHeadConflictError(str(exc)) from exc
        except TemporalRepositoryError as exc:
            raise TemporalServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()

    def attest(self, command: VerificationAttestCommand) -> dict[str, object]:
        if not isinstance(command, VerificationAttestCommand):
            raise TypeError("attest requires a VerificationAttestCommand.")
        method = _text(command.method, field_name="method", maximum=120)
        evidence_sha = _sha(
            command.evidence_sha256, field_name="evidence_sha256"
        )
        evidence_ref = _text(
            command.evidence_reference,
            field_name="evidence_reference",
            maximum=1000,
        )
        note = (
            None
            if command.note is None
            else _text(command.note, field_name="note", maximum=2000)
        )
        subject_ms = None
        if command.subject_verified_at is not None:
            subject_ms = parse_temporal_value(
                {
                    "precision": "instant",
                    "value": command.subject_verified_at,
                }
            ).instant_ms
        for value, label in (
            (command.space_id, "space_id"),
            (command.item_id, "item_id"),
            (command.expected_revision_id, "expected_revision_id"),
            (command.operation_id, "operation_id"),
            (command.request_id, "request_id"),
        ):
            _uuid(value, field_name=label)
        expected_summary = _sha(
            command.expected_temporal_summary_sha256,
            field_name="expected_temporal_summary_sha256",
        )
        request_hash = self._request_hash(
            "verification.attest",
            {
                "actor_scope_epoch": command.principal.captured_scope_epoch,
                "actor_user_id": command.principal.user_id,
                "evidence_reference": evidence_ref,
                "evidence_sha256": evidence_sha,
                "expected_head_event_id": command.expected_head_event_id,
                "expected_head_generation": command.expected_head_generation,
                "expected_revision_id": command.expected_revision_id,
                "expected_temporal_summary_sha256": expected_summary,
                "item_id": command.item_id,
                "method": method,
                "note": note,
                "space_id": command.space_id,
                "subject_verified_at_ms": subject_ms,
            },
        )
        connection = self._factory.open()
        try:
            with transaction(connection):
                resolved, actor = self._resolve(
                    connection,
                    command,
                    capability=Capability.TEMPORAL_MUTATE,
                )
                now_ms = db_now_ms(connection)
                dedup_id, replay = self._begin(
                    connection,
                    actor_id=actor.id,
                    operation_type="verification.attest",
                    operation_id=command.operation_id,
                    request_hash=request_hash,
                    now_ms=now_ms,
                )
                if replay is not None:
                    replay["request_id"] = command.request_id
                    return replay
                item = self._repository.require_current_item(
                    connection,
                    space_id=command.space_id,
                    item_id=command.item_id,
                )
                if item.revision_id != command.expected_revision_id:
                    raise VerificationNoncurrentError(
                        "The current revision changed."
                    )
                _source, _effective, current_summary = self._summary(
                    connection, item.revision_id
                )
                if current_summary != expected_summary:
                    raise VerificationNoncurrentError(
                        "The temporal summary is no longer current."
                    )
                pair = self._repository.get_verification_head(
                    connection, revision_id=item.revision_id
                )
                predecessor, generation = self._check_expected_head(
                    pair,
                    expected_event_id=command.expected_head_event_id,
                    expected_generation=command.expected_head_generation,
                )
                event_id = str(
                    uuid5(
                        _VERIFICATION_NAMESPACE,
                        f"{command.operation_id}:attest",
                    )
                )
                authority = self._authority_proof(resolved, item)
                temporal_generation = (
                    self._repository.advance_temporal_generation(
                        connection, item=item, now_ms=now_ms
                    )
                )
                audit = self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=str(
                            uuid5(_AUDIT_NAMESPACE, f"attest:{event_id}")
                        ),
                        occurred_at_ms=now_ms,
                        request_id=command.request_id,
                        operation_id=command.operation_id,
                        action="verification.attest",
                        outcome="attested",
                        actor_kind="user",
                        actor_user_id=actor.id,
                        actor_display_name=actor.display_name,
                        actor_role_snapshot=actor.system_role,
                        space_id=item.space_id,
                        item_id=item.item_id,
                        base_revision_id=item.revision_id,
                        new_revision_id=item.revision_id,
                        reason=None,
                        details={
                            "attestation_id": event_id,
                            "head_generation": generation,
                            "schema_version": 1,
                            "temporal_generation": temporal_generation,
                            "temporal_summary_sha256": current_summary,
                        },
                        request_meta=dict(command.request_meta),
                    ),
                )
                outbox = self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=f"verification-attest:{event_id}",
                        space_id=item.space_id,
                        aggregate_type="knowledge_item",
                        aggregate_id=item.item_id,
                        aggregate_version=temporal_generation,
                        event_type="knowledge.verification.attested",
                        payload={
                            "attestation_id": event_id,
                            "item_id": item.item_id,
                            "revision_id": item.revision_id,
                            "schema_version": 1,
                            "temporal_generation": temporal_generation,
                            "temporal_summary_sha256": current_summary,
                        },
                        available_at_ms=now_ms,
                    ),
                )
                record = self._repository.append_verification(
                    connection,
                    VerificationDraft(
                        id=event_id,
                        space_id=item.space_id,
                        item_id=item.item_id,
                        revision_id=item.revision_id,
                        event_kind="attest",
                        temporal_summary_sha256=current_summary,
                        verifier_user_id=actor.id,
                        authority_proof_sha256=authority,
                        method=method,
                        evidence_sha256=evidence_sha,
                        evidence_reference=evidence_ref,
                        note=note,
                        subject_verified_at_ms=subject_ms,
                        predecessor_event_id=predecessor,
                        head_generation=generation,
                        operation_id=command.operation_id,
                        effect_key=f"verification:{item.revision_id}:{generation}",
                        audit_event_id=audit.id,
                        outbox_event_id=outbox.id,
                        attested_at_ms=now_ms,
                    ),
                )
                invalidated = self._repository.invalidate_current_ancestors(
                    connection, space_id=item.space_id, now_ms=now_ms
                )
                result = {
                    "attestation_id": record.id,
                    "head_generation": record.head_generation,
                    "last_verified_at": _utc_iso(record.attested_at_ms),
                    "operation_id": command.operation_id,
                    "request_id": command.request_id,
                    "revision_id": record.revision_id,
                    "temporal_generation": temporal_generation,
                    "temporal_summary_sha256": current_summary,
                    "invalidated_ancestor_count": invalidated,
                }
                self._finish(
                    connection,
                    dedup_id=dedup_id,
                    result=result,
                    now_ms=now_ms,
                )
                return result
        except TemporalItemUnavailableError as exc:
            raise TemporalNotFoundError(str(exc)) from exc
        except TemporalStateConflictError as exc:
            raise TemporalHeadConflictError(str(exc)) from exc
        except TemporalRepositoryError as exc:
            raise TemporalServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()

    def revoke_verification(
        self, command: VerificationRevokeCommand
    ) -> dict[str, object]:
        if not isinstance(command, VerificationRevokeCommand):
            raise TypeError("revoke_verification requires a command.")
        reason = _text(command.reason, field_name="reason", maximum=2000)
        for value, label in (
            (command.space_id, "space_id"),
            (command.item_id, "item_id"),
            (command.expected_revision_id, "expected_revision_id"),
            (command.attestation_id, "attestation_id"),
            (command.operation_id, "operation_id"),
            (command.request_id, "request_id"),
        ):
            _uuid(value, field_name=label)
        request_hash = self._request_hash(
            "verification.revoke",
            {
                "actor_scope_epoch": command.principal.captured_scope_epoch,
                "actor_user_id": command.principal.user_id,
                "attestation_id": command.attestation_id,
                "expected_head_generation": command.expected_head_generation,
                "expected_revision_id": command.expected_revision_id,
                "item_id": command.item_id,
                "reason": reason,
                "space_id": command.space_id,
            },
        )
        connection = self._factory.open()
        try:
            with transaction(connection):
                resolved, actor = self._resolve(
                    connection,
                    command,
                    capability=Capability.TEMPORAL_MUTATE,
                )
                now_ms = db_now_ms(connection)
                dedup_id, replay = self._begin(
                    connection,
                    actor_id=actor.id,
                    operation_type="verification.revoke",
                    operation_id=command.operation_id,
                    request_hash=request_hash,
                    now_ms=now_ms,
                )
                if replay is not None:
                    replay["request_id"] = command.request_id
                    return replay
                item = self._repository.require_current_item(
                    connection,
                    space_id=command.space_id,
                    item_id=command.item_id,
                )
                if item.revision_id != command.expected_revision_id:
                    raise VerificationNoncurrentError(
                        "The current revision changed."
                    )
                target = self._repository.get_verification(
                    connection, command.attestation_id
                )
                pair = self._repository.get_verification_head(
                    connection, revision_id=item.revision_id
                )
                if (
                    target is None
                    or pair is None
                    or pair[0].head_event_id != target.id
                    or pair[0].head_generation
                    != _head_generation(command.expected_head_generation)
                    or pair[0].state != "attested"
                ):
                    raise TemporalHeadConflictError(
                        "Only the exact current attestation can be revoked."
                    )
                _source, _effective, current_summary = self._summary(
                    connection, item.revision_id
                )
                event_id = str(
                    uuid5(
                        _VERIFICATION_NAMESPACE,
                        f"{command.operation_id}:revoke",
                    )
                )
                generation = target.head_generation + 1
                authority = self._authority_proof(resolved, item)
                temporal_generation = (
                    self._repository.advance_temporal_generation(
                        connection, item=item, now_ms=now_ms
                    )
                )
                audit = self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=str(
                            uuid5(
                                _AUDIT_NAMESPACE,
                                f"verification-revoke:{event_id}",
                            )
                        ),
                        occurred_at_ms=now_ms,
                        request_id=command.request_id,
                        operation_id=command.operation_id,
                        action="verification.revoke",
                        outcome="revoked",
                        actor_kind="user",
                        actor_user_id=actor.id,
                        actor_display_name=actor.display_name,
                        actor_role_snapshot=actor.system_role,
                        space_id=item.space_id,
                        item_id=item.item_id,
                        base_revision_id=item.revision_id,
                        new_revision_id=item.revision_id,
                        reason=reason,
                        details={
                            "attestation_id": event_id,
                            "head_generation": generation,
                            "schema_version": 1,
                            "temporal_generation": temporal_generation,
                            "temporal_summary_sha256": current_summary,
                        },
                        request_meta=dict(command.request_meta),
                    ),
                )
                outbox = self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=f"verification-revoke:{event_id}",
                        space_id=item.space_id,
                        aggregate_type="knowledge_item",
                        aggregate_id=item.item_id,
                        aggregate_version=temporal_generation,
                        event_type="knowledge.verification.revoked",
                        payload={
                            "attestation_id": event_id,
                            "item_id": item.item_id,
                            "revision_id": item.revision_id,
                            "schema_version": 1,
                            "temporal_generation": temporal_generation,
                            "temporal_summary_sha256": current_summary,
                        },
                        available_at_ms=now_ms,
                    ),
                )
                record = self._repository.append_verification(
                    connection,
                    VerificationDraft(
                        id=event_id,
                        space_id=item.space_id,
                        item_id=item.item_id,
                        revision_id=item.revision_id,
                        event_kind="revoke",
                        temporal_summary_sha256=current_summary,
                        verifier_user_id=actor.id,
                        authority_proof_sha256=authority,
                        method="revocation",
                        evidence_sha256=sha256(reason.encode("utf-8")).hexdigest(),
                        evidence_reference="user-revocation",
                        note=reason,
                        subject_verified_at_ms=None,
                        predecessor_event_id=target.id,
                        head_generation=generation,
                        operation_id=command.operation_id,
                        effect_key=f"verification:{item.revision_id}:{generation}",
                        audit_event_id=audit.id,
                        outbox_event_id=outbox.id,
                        attested_at_ms=now_ms,
                    ),
                )
                invalidated = self._repository.invalidate_current_ancestors(
                    connection, space_id=item.space_id, now_ms=now_ms
                )
                result = {
                    "attestation_id": record.id,
                    "head_generation": record.head_generation,
                    "last_verified_at": None,
                    "operation_id": command.operation_id,
                    "request_id": command.request_id,
                    "revision_id": record.revision_id,
                    "temporal_generation": temporal_generation,
                    "temporal_summary_sha256": current_summary,
                    "invalidated_ancestor_count": invalidated,
                }
                self._finish(
                    connection,
                    dedup_id=dedup_id,
                    result=result,
                    now_ms=now_ms,
                )
                return result
        except TemporalItemUnavailableError as exc:
            raise TemporalNotFoundError(str(exc)) from exc
        except TemporalStateConflictError as exc:
            raise TemporalHeadConflictError(str(exc)) from exc
        except TemporalRepositoryError as exc:
            raise TemporalServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()

    def read(self, request: TemporalReadRequest) -> dict[str, object]:
        if not isinstance(request, TemporalReadRequest):
            raise TypeError("read requires a TemporalReadRequest.")
        if (
            isinstance(request.after_audit_id, bool)
            or not isinstance(request.after_audit_id, int)
            or request.after_audit_id < 0
        ):
            raise TemporalValueInvalidError("after_audit_id is invalid.")
        if (
            isinstance(request.limit, bool)
            or not isinstance(request.limit, int)
            or not 1 <= request.limit <= 100
        ):
            raise TemporalValueInvalidError("limit must be 1..100.")
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._resolve(connection, request, capability=Capability.READ)
                item = self._repository.require_current_item(
                    connection,
                    space_id=request.space_id,
                    item_id=request.item_id,
                )
                source_pair, effective_pair, summary = self._summary(
                    connection, item.revision_id
                )
                verification_pair = self._repository.get_verification_head(
                    connection, revision_id=item.revision_id
                )
                source = (
                    None
                    if source_pair is None or source_pair[0].state != "asserted"
                    else _event_head(source_pair[1])["value"]
                )
                effective = (
                    None
                    if effective_pair is None
                    or effective_pair[0].state != "asserted"
                    else _event_head(effective_pair[1])["value"]
                )
                verified = None
                if (
                    verification_pair is not None
                    and verification_pair[0].state == "attested"
                    and verification_pair[1].temporal_summary_sha256 == summary
                ):
                    verified = _utc_iso(
                        verification_pair[1].attested_at_ms
                    )
                rows = connection.execute(
                    """SELECT audit_event_id,'assertion' AS stream,id,event_kind,
                              assertion_kind AS kind,committed_at AS occurred_at
                         FROM revision_temporal_assertion
                        WHERE revision_id=? AND audit_event_id>?
                        UNION ALL
                       SELECT audit_event_id,'verification',id,event_kind,NULL,
                              attested_at
                         FROM revision_verification_attestation
                        WHERE revision_id=? AND audit_event_id>?
                        ORDER BY audit_event_id LIMIT ?""",
                    (
                        item.revision_id,
                        request.after_audit_id,
                        item.revision_id,
                        request.after_audit_id,
                        request.limit + 1,
                    ),
                ).fetchall()
                page = rows[: request.limit]
                next_cursor = (
                    None
                    if len(rows) <= request.limit
                    else int(page[-1]["audit_event_id"])
                )
                history = [
                    {
                        "audit_event_id": int(row["audit_event_id"]),
                        "event_id": str(row["id"]),
                        "event_kind": str(row["event_kind"]),
                        "kind": row["kind"],
                        "occurred_at": _utc_iso(int(row["occurred_at"])),
                        "stream": str(row["stream"]),
                    }
                    for row in page
                ]
                return {
                    "content_modified_at": _utc_iso(
                        item.content_modified_at_ms
                    ),
                    "history": history,
                    "information_effective_at": effective,
                    "last_verified_at": verified,
                    "next_cursor": next_cursor,
                    "registered_at": _utc_iso(item.registered_at_ms),
                    "revision_id": item.revision_id,
                    "revision_no": item.revision_no,
                    "source_modified_at": source,
                    "temporal_generation": item.temporal_generation,
                    "temporal_summary_sha256": summary,
                }
        except TemporalItemUnavailableError as exc:
            raise TemporalNotFoundError(str(exc)) from exc
        except TemporalRepositoryError as exc:
            raise TemporalServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()


__all__ = [
    "TemporalAssertionCommand",
    "TemporalAssertionRevokeCommand",
    "TemporalAuthorizationError",
    "TemporalHeadConflictError",
    "TemporalIdempotencyError",
    "TemporalNotFoundError",
    "TemporalOperationInProgressError",
    "TemporalReadRequest",
    "TemporalScopeStaleError",
    "TemporalService",
    "TemporalServiceError",
    "TemporalServiceIntegrityError",
    "TemporalValue",
    "TemporalValueInvalidError",
    "VerificationAttestCommand",
    "VerificationNoncurrentError",
    "VerificationRevokeCommand",
    "normalize_notion_source_timestamp",
    "parse_temporal_value",
    "temporal_summary_document",
    "temporal_summary_sha256",
]

"""P3 conversation lifecycle orchestration with immutable space binding.

The service owns every mutation transaction and composes the canonical
conversation repository with actor/session-scope revalidation, operation
idempotency, audit, and outbox.  P3.2 pins are fixed to one visible current
revision in the conversation space.  Explicit workspace/project/Notion/source
context is authorized by an injected resolver outside the database transaction,
then scope and canonical snapshot evidence are rechecked inside the mutation.
The service deliberately does not stream model output, retrieve knowledge, or
touch legacy JSON sessions; those remain later P3 slices.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
import time
from typing import Literal, Protocol, TypeVar
from uuid import UUID, uuid5

from ..conversation_binding import (
    ConversationBindingFence,
    ConversationBindingIntegrityError,
)
from ..db import ConnectionFactory, TransactionMode, db_now_ms, transaction
from ..errors import KnowledgeCoreError
from ..turn_execution import TURN_EXECUTION_PROTOCOL, hold_turn_execution
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.conversations import (
    ConversationCreate,
    ConversationContextBindingCreate,
    ConversationContextBindingMode,
    ConversationContextBindingRecord,
    ConversationContextBindingType,
    ConversationListEntry,
    ConversationMessageRecord,
    ConversationNotFoundError,
    ConversationPinCreate,
    ConversationPinRecord,
    ConversationRecord,
    ConversationRepository,
    ConversationScopeError,
    ConversationStateConflictError,
    ConversationSummaryCreate,
    ConversationSummaryRecord,
    ConversationTurnAccept,
    ConversationTurnCompletion,
    ConversationTurnEventRecord,
    ConversationTurnRecord,
    ConversationTurnTermination,
    ConversationVersionConflictError,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.personal_wikis import (
    PersonalWikiProfileRecord,
    PersonalWikiProfileRepository,
)
from ..repositories.users import (
    OperationIdempotencyConflictError,
    OperationInProgressError,
    UserNotFoundError,
    UserRecord,
    UserRepository,
)
from ..scope import Capability, ScopeResolutionError, ScopeResolver, SessionPrincipal


ConversationMutationOutcome = Literal["created", "updated", "no_change"]
ConversationPinMutationOutcome = Literal["added", "removed", "no_change"]
ConversationContextMutationOutcome = Literal["added", "detached", "no_change"]

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_CONVERSATION_NAMESPACE = UUID("0a31eb87-5cfe-421c-a456-1a7653ad17c7")
_DEDUP_NAMESPACE = UUID("7779ff78-0d9c-4326-b275-b134c6835664")
_AUDIT_NAMESPACE = UUID("c3c0f1f3-f709-48f7-ac79-a03b369adffc")
_TURN_NAMESPACE = UUID("8687d4a4-5141-46ae-8115-e9819a3ecb4e")
_TURN_USER_MESSAGE_NAMESPACE = UUID("701c7e0f-88f7-41fd-991f-3b82e32284dd")
_TURN_ASSISTANT_MESSAGE_NAMESPACE = UUID("ea1f33fe-dca4-4859-96cd-4af03cf701f6")
_SUMMARY_NAMESPACE = UUID("283e6f5d-657a-411e-844a-d416e6aa9b3f")
_CONTEXT_BINDING_NAMESPACE = UUID("466f2819-97f5-456d-b031-0253856b66e4")
_PIN_AGGREGATE_NAMESPACE = UUID("1d98f28b-72d5-46f5-814f-a3e134c87268")
_SAFE_META_KEYS = frozenset({"correlation_id", "remote_addr_hash", "user_agent_hash"})
_SENSITIVE_FRAGMENTS = ("authorization", "cookie", "password", "secret", "token")
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,79}$", re.ASCII)
_AttachmentResult = TypeVar("_AttachmentResult")


class ConversationServiceError(KnowledgeCoreError):
    code = "CONVERSATION_SERVICE_ERROR"
    http_status = 409


class ConversationAuthorizationError(ConversationServiceError):
    code = "CONVERSATION_FORBIDDEN"
    http_status = 403


class ConversationSessionScopeStaleError(ConversationServiceError):
    code = "SCOPE_STALE"
    http_status = 409


class ConversationServiceScopeError(ConversationServiceError):
    code = "CONVERSATION_SCOPE_INVALID"
    http_status = 409


class ConversationServiceVersionError(ConversationServiceError):
    code = "CONVERSATION_VERSION_CONFLICT"
    http_status = 409


class ConversationIdempotencyError(ConversationServiceError):
    code = "CONVERSATION_IDEMPOTENCY_CONFLICT"
    http_status = 409


class ConversationOperationInProgressError(ConversationServiceError):
    code = "CONVERSATION_OPERATION_IN_PROGRESS"
    http_status = 409
    retryable = True


class ConversationWikiArchivedHistoryOnlyError(ConversationServiceError):
    code = "WIKI_ARCHIVED_HISTORY_ONLY"
    http_status = 409


class ConversationCorpusClosureStaleError(ConversationServiceError):
    code = "CORPUS_CLOSURE_STALE"
    http_status = 409


class ConversationContextResolverUnavailableError(ConversationServiceError):
    code = "CONVERSATION_CONTEXT_RESOLVER_UNAVAILABLE"
    http_status = 503
    retryable = True


@dataclass(frozen=True, slots=True)
class CreateConversationCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    space_id: str
    title: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UpdateConversationCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    conversation_id: str
    expected_row_version: int
    title: str | None = None
    status: Literal["active", "archived"] | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AcceptConversationTurnCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    conversation_id: str
    user_content: Mapping[str, object]
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompleteConversationTurnCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    conversation_id: str
    assistant_content: Mapping[str, object]
    final_response: Mapping[str, object]
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TerminateConversationTurnCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    conversation_id: str
    terminal_state: Literal["failed", "cancelled"]
    error_code: str | None = None
    retryable: bool = False
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompactConversationCommand:
    """Commit an already generated summary for one fixed message prefix."""

    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    conversation_id: str
    covered_through_sequence_no: int
    summary_md: str
    model_id: str
    prompt_version: str
    summarizer_version: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AddConversationPinCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    conversation_id: str
    item_id: str
    revision_id: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RemoveConversationPinCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    conversation_id: str
    item_id: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AddConversationContextCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    conversation_id: str
    binding_type: ConversationContextBindingType
    binding_mode: ConversationContextBindingMode
    external_key: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RemoveConversationContextCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    conversation_id: str
    binding_id: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConversationContextResolveRequest:
    actor_user_id: str
    actor_scope_epoch: int
    conversation_id: str
    space_id: str
    binding_type: ConversationContextBindingType
    binding_mode: ConversationContextBindingMode
    external_key: str


@dataclass(frozen=True, slots=True)
class ConversationContextResolution:
    space_id: str
    binding_type: ConversationContextBindingType
    binding_mode: ConversationContextBindingMode
    external_key: str
    source_artifact_id: str | None
    snapshot_sha256: str | None
    display_name: str


class ConversationContextResolver(Protocol):
    """Authorize and resolve one explicit attachment outside a DB transaction."""

    def resolve(
        self, request: ConversationContextResolveRequest
    ) -> ConversationContextResolution: ...


@dataclass(frozen=True, slots=True)
class ConversationTurnResult:
    request_id: str
    operation_id: str
    turn_id: str
    conversation_id: str
    state: Literal["accepted", "running", "completed", "failed", "cancelled"]
    user_message_id: str
    assistant_message_id: str | None
    final_response: Mapping[str, object] | None
    error: Mapping[str, object] | None
    event_cursor: int
    row_version: int

    def as_dict(self) -> dict[str, object]:
        return {
            "assistant_message_id": self.assistant_message_id,
            "conversation_id": self.conversation_id,
            "error": self.error,
            "event_cursor": self.event_cursor,
            "final_response": self.final_response,
            "operation_id": self.operation_id,
            "request_id": self.request_id,
            "row_version": self.row_version,
            "schema_version": 1,
            "state": self.state,
            "turn_id": self.turn_id,
            "user_message_id": self.user_message_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ConversationTurnResult":
        if frozenset(value) != {
            "assistant_message_id",
            "conversation_id",
            "error",
            "event_cursor",
            "final_response",
            "operation_id",
            "request_id",
            "row_version",
            "schema_version",
            "state",
            "turn_id",
            "user_message_id",
        } or value.get("schema_version") != 1:
            raise ValueError("Stored conversation turn result is invalid.")
        state = value.get("state")
        if state not in {"accepted", "running", "completed", "failed", "cancelled"}:
            raise ValueError("Stored conversation turn state is invalid.")
        assistant = value.get("assistant_message_id")
        if assistant is not None:
            assistant = _uuid(assistant, "assistant_message_id")
        final = value.get("final_response")
        if final is not None:
            final = _canonical_object(final, "final_response")
        error = value.get("error")
        if error is not None:
            error = _turn_error(error)
        if state == "completed" and not (
            assistant is not None and final is not None and error is None
        ):
            raise ValueError("Stored conversation completion shape is invalid.")
        if state == "failed" and not (
            assistant is None and final is None and error is not None
        ):
            raise ValueError("Stored failed-turn shape is invalid.")
        if state in {"accepted", "running", "cancelled"} and not (
            assistant is None and final is None and error is None
        ):
            raise ValueError("Stored non-completed turn shape is invalid.")
        return cls(
            request_id=_uuid(value.get("request_id"), "request_id"),
            operation_id=_uuid(value.get("operation_id"), "operation_id"),
            turn_id=_uuid(value.get("turn_id"), "turn_id"),
            conversation_id=_uuid(value.get("conversation_id"), "conversation_id"),
            state=state,
            user_message_id=_uuid(value.get("user_message_id"), "user_message_id"),
            assistant_message_id=assistant,
            final_response=final,
            error=error,
            event_cursor=_positive(value.get("event_cursor"), "event_cursor"),
            row_version=_positive(value.get("row_version"), "row_version"),
        )


@dataclass(frozen=True, slots=True)
class ConversationCompactResult:
    request_id: str
    operation_id: str
    outcome: Literal["created", "replaced", "no_change"]
    space_id: str
    summary: ConversationSummaryRecord

    def as_dict(self) -> dict[str, object]:
        value = self.summary
        return {
            "operation_id": self.operation_id,
            "outcome": self.outcome,
            "request_id": self.request_id,
            "schema_version": 1,
            "space_id": self.space_id,
            "summary": {
                "conversation_id": value.conversation_id,
                "covered_through_sequence_no": value.covered_through_sequence_no,
                "created_at_ms": value.created_at_ms,
                "created_by_user_id": value.created_by_user_id,
                "id": value.id,
                "invalidation_reason": value.invalidation_reason,
                "invalidated_at_ms": value.invalidated_at_ms,
                "model_id": value.model_id,
                "prompt_version": value.prompt_version,
                "row_version": value.row_version,
                "status": value.status,
                "summarizer_version": value.summarizer_version,
                "summary_md": value.summary_md,
                "summary_sha256": value.summary_sha256,
            },
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ConversationCompactResult":
        if frozenset(value) != {
            "operation_id",
            "outcome",
            "request_id",
            "schema_version",
            "space_id",
            "summary",
        } or value.get("schema_version") != 1:
            raise ValueError("Stored compact-summary result shape is invalid.")
        outcome = value.get("outcome")
        if outcome not in {"created", "replaced", "no_change"}:
            raise ValueError("Stored compact-summary outcome is invalid.")
        raw = value.get("summary")
        expected_keys = {
            "conversation_id",
            "covered_through_sequence_no",
            "created_at_ms",
            "created_by_user_id",
            "id",
            "invalidation_reason",
            "invalidated_at_ms",
            "model_id",
            "prompt_version",
            "row_version",
            "status",
            "summarizer_version",
            "summary_md",
            "summary_sha256",
        }
        if not isinstance(raw, Mapping) or frozenset(raw) != expected_keys:
            raise ValueError("Stored compact summary is invalid.")
        summary_md = raw.get("summary_md")
        if not isinstance(summary_md, str) or not summary_md.strip():
            raise ValueError("Stored compact-summary text is invalid.")
        summary_sha = raw.get("summary_sha256")
        if (
            not isinstance(summary_sha, str)
            or sha256(summary_md.encode("utf-8")).hexdigest() != summary_sha
        ):
            raise ValueError("Stored compact-summary hash is invalid.")
        creator = _user(raw.get("created_by_user_id"))
        status = raw.get("status")
        if (
            status != "active"
            or raw.get("invalidated_at_ms") is not None
            or raw.get("invalidation_reason") is not None
        ):
            raise ValueError("Stored compact-summary state is invalid.")
        model_id = _bounded_text(raw.get("model_id"), "model_id", 200)
        prompt_version = _bounded_text(
            raw.get("prompt_version"), "prompt_version", 120
        )
        summarizer_version = _bounded_text(
            raw.get("summarizer_version"), "summarizer_version", 120
        )
        row_version = _positive(raw.get("row_version"), "row_version")
        if row_version != 1:
            raise ValueError("Stored active compact-summary version is invalid.")
        record = ConversationSummaryRecord(
            id=_uuid(raw.get("id"), "summary.id"),
            conversation_id=_uuid(
                raw.get("conversation_id"), "summary.conversation_id"
            ),
            covered_through_sequence_no=_positive(
                raw.get("covered_through_sequence_no"),
                "covered_through_sequence_no",
            ),
            summary_md=summary_md,
            summary_sha256=summary_sha,
            model_id=model_id,
            prompt_version=prompt_version,
            summarizer_version=summarizer_version,
            status="active",
            created_by_user_id=creator,
            created_at_ms=_nonnegative(raw.get("created_at_ms"), "created_at_ms"),
            invalidated_at_ms=None,
            invalidation_reason=None,
            row_version=row_version,
        )
        return cls(
            request_id=_uuid(value.get("request_id"), "request_id"),
            operation_id=_uuid(value.get("operation_id"), "operation_id"),
            outcome=outcome,
            space_id=_uuid(value.get("space_id"), "space_id"),
            summary=record,
        )


@dataclass(frozen=True, slots=True)
class ConversationMutationResult:
    request_id: str
    operation_id: str
    outcome: ConversationMutationOutcome
    conversation: ConversationRecord

    def as_dict(self) -> dict[str, object]:
        value = self.conversation
        return {
            "conversation": {
                "captured_scope_epoch": value.captured_scope_epoch,
                "created_at_ms": value.created_at_ms,
                "id": value.id,
                "metadata": value.metadata,
                "owner_user_id": value.owner_user_id,
                "row_version": value.row_version,
                "space_id": value.space_id,
                "status": value.status,
                "title": value.title,
                "updated_at_ms": value.updated_at_ms,
            },
            "operation_id": self.operation_id,
            "outcome": self.outcome,
            "request_id": self.request_id,
            "schema_version": 1,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ConversationMutationResult":
        if frozenset(value) != {
            "conversation",
            "operation_id",
            "outcome",
            "request_id",
            "schema_version",
        } or value.get("schema_version") != 1:
            raise ValueError("Stored conversation result shape is invalid.")
        conversation = value.get("conversation")
        if not isinstance(conversation, Mapping) or frozenset(conversation) != {
            "captured_scope_epoch",
            "created_at_ms",
            "id",
            "metadata",
            "owner_user_id",
            "row_version",
            "space_id",
            "status",
            "title",
            "updated_at_ms",
        }:
            raise ValueError("Stored conversation result is invalid.")
        metadata = _canonical_object(conversation.get("metadata"), "metadata")
        outcome = value.get("outcome")
        status = conversation.get("status")
        if outcome not in {"created", "updated", "no_change"}:
            raise ValueError("Stored conversation outcome is invalid.")
        if status not in {"active", "archived"}:
            raise ValueError("Stored conversation status is invalid.")
        record = ConversationRecord(
            id=_uuid(conversation.get("id"), "conversation.id"),
            owner_user_id=_user(conversation.get("owner_user_id")),
            space_id=_uuid(conversation.get("space_id"), "conversation.space_id"),
            captured_scope_epoch=_positive(
                conversation.get("captured_scope_epoch"),
                "captured_scope_epoch",
            ),
            title=_title(conversation.get("title")),
            status=status,
            metadata_json=_canonical_json(metadata),
            created_at_ms=_nonnegative(conversation.get("created_at_ms"), "created_at_ms"),
            updated_at_ms=_nonnegative(conversation.get("updated_at_ms"), "updated_at_ms"),
            row_version=_positive(conversation.get("row_version"), "row_version"),
        )
        return cls(
            request_id=_uuid(value.get("request_id"), "request_id"),
            operation_id=_uuid(value.get("operation_id"), "operation_id"),
            outcome=outcome,
            conversation=record,
        )


@dataclass(frozen=True, slots=True)
class ConversationPinMutationResult:
    request_id: str
    operation_id: str
    mutation: Literal["add", "remove"]
    outcome: ConversationPinMutationOutcome
    conversation_id: str
    space_id: str
    item_id: str
    pin: ConversationPinRecord | None

    def as_dict(self) -> dict[str, object]:
        return {
            "conversation_id": self.conversation_id,
            "item_id": self.item_id,
            "mutation": self.mutation,
            "operation_id": self.operation_id,
            "outcome": self.outcome,
            "pin": None if self.pin is None else _pin_mapping(self.pin),
            "request_id": self.request_id,
            "schema_version": 1,
            "space_id": self.space_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ConversationPinMutationResult":
        if frozenset(value) != {
            "conversation_id",
            "item_id",
            "mutation",
            "operation_id",
            "outcome",
            "pin",
            "request_id",
            "schema_version",
            "space_id",
        } or value.get("schema_version") != 1:
            raise ValueError("Stored conversation pin result shape is invalid.")
        mutation = value.get("mutation")
        outcome = value.get("outcome")
        if mutation not in {"add", "remove"}:
            raise ValueError("Stored conversation pin mutation is invalid.")
        if outcome not in {"added", "removed", "no_change"}:
            raise ValueError("Stored conversation pin outcome is invalid.")
        if mutation == "add" and outcome not in {"added", "no_change"}:
            raise ValueError("Stored add-pin outcome is invalid.")
        if mutation == "remove" and outcome not in {"removed", "no_change"}:
            raise ValueError("Stored remove-pin outcome is invalid.")
        conversation_id = _uuid(value.get("conversation_id"), "conversation_id")
        item_id = _uuid(value.get("item_id"), "item_id")
        space_id = _uuid(value.get("space_id"), "space_id")
        raw_pin = value.get("pin")
        pin = None if raw_pin is None else _pin_from_mapping(raw_pin)
        if pin is not None and (
            pin.conversation_id != conversation_id
            or pin.item_id != item_id
            or pin.space_id != space_id
        ):
            raise ValueError("Stored conversation pin result is bound incorrectly.")
        if mutation == "add" and pin is None:
            raise ValueError("Stored add-pin result has no pin evidence.")
        if outcome in {"added", "removed"} and pin is None:
            raise ValueError("Stored changed pin result has no pin evidence.")
        return cls(
            request_id=_uuid(value.get("request_id"), "request_id"),
            operation_id=_uuid(value.get("operation_id"), "operation_id"),
            mutation=mutation,
            outcome=outcome,
            conversation_id=conversation_id,
            space_id=space_id,
            item_id=item_id,
            pin=pin,
        )


@dataclass(frozen=True, slots=True)
class ConversationContextMutationResult:
    request_id: str
    operation_id: str
    mutation: Literal["add", "remove"]
    outcome: ConversationContextMutationOutcome
    space_id: str
    binding: ConversationContextBindingRecord

    def as_dict(self) -> dict[str, object]:
        return {
            "binding": _binding_mapping(self.binding),
            "mutation": self.mutation,
            "operation_id": self.operation_id,
            "outcome": self.outcome,
            "request_id": self.request_id,
            "schema_version": 1,
            "space_id": self.space_id,
        }

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, object]
    ) -> "ConversationContextMutationResult":
        if frozenset(value) != {
            "binding",
            "mutation",
            "operation_id",
            "outcome",
            "request_id",
            "schema_version",
            "space_id",
        } or value.get("schema_version") != 1:
            raise ValueError("Stored conversation context result shape is invalid.")
        mutation = value.get("mutation")
        outcome = value.get("outcome")
        if mutation not in {"add", "remove"}:
            raise ValueError("Stored context mutation is invalid.")
        if outcome not in {"added", "detached", "no_change"}:
            raise ValueError("Stored context outcome is invalid.")
        if mutation == "add" and outcome not in {"added", "no_change"}:
            raise ValueError("Stored add-context outcome is invalid.")
        if mutation == "remove" and outcome not in {"detached", "no_change"}:
            raise ValueError("Stored remove-context outcome is invalid.")
        binding = _binding_from_mapping(value.get("binding"))
        if mutation == "add" and binding.status != "active":
            raise ValueError("Stored add-context result is not active.")
        if mutation == "remove" and binding.status != "detached":
            raise ValueError("Stored remove-context result is not detached.")
        return cls(
            request_id=_uuid(value.get("request_id"), "request_id"),
            operation_id=_uuid(value.get("operation_id"), "operation_id"),
            mutation=mutation,
            outcome=outcome,
            space_id=_uuid(value.get("space_id"), "space_id"),
            binding=binding,
        )


def _uuid(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a canonical lowercase UUID.")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("actor_user_id must be a canonical user ID.")
    return value


def _positive(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer.")
    return value


def _nonnegative(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer.")
    return value


def _title(value: object) -> str:
    if not isinstance(value, str) or "\x00" in value or len(value) > 200:
        raise ValueError("title must contain at most 200 characters.")
    return value


def _first_question_title(content: Mapping[str, object]) -> str:
    """Derive a compact, deterministic title from the first user question."""

    text = content.get("text")
    if not isinstance(text, str):
        return ""
    normalized = " ".join(text.split())
    if len(normalized) <= 80:
        return normalized
    return normalized[:79].rstrip() + "…"


def _bounded_text(value: object, field_name: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or "\x00" in value
        or len(value) > maximum
    ):
        raise ValueError(f"{field_name} is invalid.")
    return value


def _summary_text(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or len(value) > 1_000_000
    ):
        raise ValueError("summary_md is invalid.")
    return value


def _turn_error(value: object) -> dict[str, object]:
    error = _canonical_object(value, "error")
    code = error.get("code")
    retryable = error.get("retryable")
    if (
        frozenset(error) != {"code", "retryable", "schema_version"}
        or not isinstance(code, str)
        or _ERROR_CODE_RE.fullmatch(code) is None
        or not isinstance(retryable, bool)
        or error.get("schema_version") != 1
    ):
        raise ValueError("error must be a stable sanitized turn error.")
    return error


def _canonical_json(value: Mapping[str, object]) -> str:
    try:
        result = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Conversation metadata must be finite JSON.") from exc
    if not isinstance(json.loads(result), dict):
        raise ValueError("Conversation metadata must be an object.")
    return result


def _canonical_object(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object.")
    encoded = _canonical_json(value)
    parsed = json.loads(encoded)
    assert isinstance(parsed, dict)
    return parsed


def _sha(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 value.")
    return value


def _pin_mapping(value: ConversationPinRecord) -> dict[str, object]:
    return {
        "conversation_id": value.conversation_id,
        "created_at_ms": value.created_at_ms,
        "created_by": value.created_by,
        "item_id": value.item_id,
        "revision_id": value.revision_id,
        "space_id": value.space_id,
    }


def _pin_from_mapping(value: object) -> ConversationPinRecord:
    if not isinstance(value, Mapping) or frozenset(value) != {
        "conversation_id",
        "created_at_ms",
        "created_by",
        "item_id",
        "revision_id",
        "space_id",
    }:
        raise ValueError("Stored conversation pin shape is invalid.")
    return ConversationPinRecord(
        conversation_id=_uuid(value.get("conversation_id"), "conversation_id"),
        space_id=_uuid(value.get("space_id"), "space_id"),
        item_id=_uuid(value.get("item_id"), "item_id"),
        revision_id=_uuid(value.get("revision_id"), "revision_id"),
        created_by=_user(value.get("created_by")),
        created_at_ms=_nonnegative(value.get("created_at_ms"), "created_at_ms"),
    )


def _binding_mapping(value: ConversationContextBindingRecord) -> dict[str, object]:
    return {
        "binding_mode": value.binding_mode,
        "binding_type": value.binding_type,
        "conversation_id": value.conversation_id,
        "created_at_ms": value.created_at_ms,
        "created_by": value.created_by,
        "detached_at_ms": value.detached_at_ms,
        "display_metadata": value.display_metadata,
        "external_key": value.external_key,
        "id": value.id,
        "row_version": value.row_version,
        "snapshot_sha256": value.snapshot_sha256,
        "source_artifact_id": value.source_artifact_id,
        "status": value.status,
        "status_changed_at_ms": value.status_changed_at_ms,
        "status_reason": value.status_reason,
    }


def _binding_from_mapping(value: object) -> ConversationContextBindingRecord:
    if not isinstance(value, Mapping) or frozenset(value) != {
        "binding_mode",
        "binding_type",
        "conversation_id",
        "created_at_ms",
        "created_by",
        "detached_at_ms",
        "display_metadata",
        "external_key",
        "id",
        "row_version",
        "snapshot_sha256",
        "source_artifact_id",
        "status",
        "status_changed_at_ms",
        "status_reason",
    }:
        raise ValueError("Stored context-binding shape is invalid.")
    binding_type = value.get("binding_type")
    binding_mode = value.get("binding_mode")
    status = value.get("status")
    if binding_type not in {
        "workspace_file", "project_context", "notion_page", "source_artifact"
    }:
        raise ValueError("Stored context-binding type is invalid.")
    if binding_mode not in {"snapshot", "live"}:
        raise ValueError("Stored context-binding mode is invalid.")
    if status not in {"active", "unavailable", "detached"}:
        raise ValueError("Stored context-binding status is invalid.")
    source_artifact = value.get("source_artifact_id")
    snapshot_sha = value.get("snapshot_sha256")
    if source_artifact is not None:
        source_artifact = _uuid(source_artifact, "source_artifact_id")
    if snapshot_sha is not None:
        snapshot_sha = _sha(snapshot_sha, "snapshot_sha256")
    display_metadata = _canonical_object(
        value.get("display_metadata"), "display_metadata"
    )
    external_key = _bounded_text(value.get("external_key"), "external_key", 1000)
    reason = value.get("status_reason")
    if reason is not None:
        reason = _bounded_text(reason, "status_reason", 1000)
    detached = value.get("detached_at_ms")
    if detached is not None:
        detached = _nonnegative(detached, "detached_at_ms")
    if binding_mode == "snapshot":
        if source_artifact is None or snapshot_sha is None:
            raise ValueError("Stored snapshot binding has incomplete evidence.")
    elif source_artifact is not None or snapshot_sha is not None:
        raise ValueError("Stored live binding carries snapshot evidence.")
    if binding_type == "source_artifact" and binding_mode != "snapshot":
        raise ValueError("Stored source-artifact binding is not a snapshot.")
    if status == "active" and (reason is not None or detached is not None):
        raise ValueError("Stored active binding state is invalid.")
    if status == "unavailable" and (
        binding_mode != "live" or reason is None or detached is not None
    ):
        raise ValueError("Stored unavailable binding state is invalid.")
    if status == "detached" and detached is None:
        raise ValueError("Stored detached binding has no terminal time.")
    return ConversationContextBindingRecord(
        id=_uuid(value.get("id"), "binding_id"),
        conversation_id=_uuid(value.get("conversation_id"), "conversation_id"),
        binding_type=binding_type,
        binding_mode=binding_mode,
        external_key=external_key,
        source_artifact_id=source_artifact,
        snapshot_sha256=snapshot_sha,
        display_metadata_json=_canonical_json(display_metadata),
        status=status,
        status_reason=reason,
        created_by=_user(value.get("created_by")),
        created_at_ms=_nonnegative(value.get("created_at_ms"), "created_at_ms"),
        status_changed_at_ms=_nonnegative(
            value.get("status_changed_at_ms"), "status_changed_at_ms"
        ),
        detached_at_ms=detached,
        row_version=_positive(value.get("row_version"), "row_version"),
    )


def _safe_request_meta(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("request_meta must be an object.")
    result: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str):
            raise ValueError("request_meta keys must be strings.")
        key = raw_key.strip().lower()
        if any(fragment in key for fragment in _SENSITIVE_FRAGMENTS):
            raise ValueError("Sensitive request metadata cannot enter audit storage.")
        if key not in _SAFE_META_KEYS:
            continue
        if raw_value is not None and (
            not isinstance(raw_value, str) or len(raw_value) > 500
        ):
            raise ValueError("request_meta values are invalid.")
        result[key] = raw_value
    _canonical_json(result)
    return result


def _derived(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, "\x1f".join(parts)))


def conversation_id_for_create(actor_user_id: str, operation_id: str) -> str:
    return _derived(
        _CONVERSATION_NAMESPACE,
        _user(actor_user_id),
        _uuid(operation_id, "operation_id"),
    )


def conversation_turn_id(conversation_id: str, operation_id: str) -> str:
    return _derived(
        _TURN_NAMESPACE,
        _uuid(conversation_id, "conversation_id"),
        _uuid(operation_id, "operation_id"),
    )


def conversation_turn_user_message_id(
    conversation_id: str, operation_id: str
) -> str:
    return _derived(
        _TURN_USER_MESSAGE_NAMESPACE,
        _uuid(conversation_id, "conversation_id"),
        _uuid(operation_id, "operation_id"),
    )


def conversation_turn_assistant_message_id(
    conversation_id: str, operation_id: str
) -> str:
    return _derived(
        _TURN_ASSISTANT_MESSAGE_NAMESPACE,
        _uuid(conversation_id, "conversation_id"),
        _uuid(operation_id, "operation_id"),
    )


def conversation_summary_id(conversation_id: str, operation_id: str) -> str:
    return _derived(
        _SUMMARY_NAMESPACE,
        _uuid(conversation_id, "conversation_id"),
        _uuid(operation_id, "operation_id"),
    )


def conversation_context_binding_id(
    conversation_id: str, operation_id: str
) -> str:
    return _derived(
        _CONTEXT_BINDING_NAMESPACE,
        _uuid(conversation_id, "conversation_id"),
        _uuid(operation_id, "operation_id"),
    )


def conversation_pin_aggregate_id(conversation_id: str, item_id: str) -> str:
    return _derived(
        _PIN_AGGREGATE_NAMESPACE,
        _uuid(conversation_id, "conversation_id"),
        _uuid(item_id, "item_id"),
    )


def _semantic_hash(value: Mapping[str, object]) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class ConversationService:
    """Own canonical conversation lifecycle transactions and replay."""

    __slots__ = (
        "_audit", "_auto_curation_wait_ms", "_context_resolver", "_conversation_wiki_binding",
        "_conversations", "_factory", "_outbox", "_personal_wikis",
        "_scopes", "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        conversations: ConversationRepository | None = None,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        context_resolver: ConversationContextResolver | None = None,
        scope_resolver: ScopeResolver | None = None,
        personal_wikis: PersonalWikiProfileRepository | None = None,
        auto_curation_wait_ms: int = 0,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("ConversationService requires a ConnectionFactory.")
        if (type(auto_curation_wait_ms) is not int
                or not 0 <= auto_curation_wait_ms <= 30_000):
            raise ValueError("auto_curation_wait_ms must be between zero and 30000.")
        self._factory = factory
        self._auto_curation_wait_ms = auto_curation_wait_ms
        self._conversations = conversations or ConversationRepository()
        self._users = users or UserRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._conversation_wiki_binding = (
            self._conversations.conversation_wiki_binding
        )
        if self._conversation_wiki_binding and not isinstance(
            scope_resolver, ScopeResolver
        ):
            raise TypeError(
                "Conversation-Wiki binding requires a ScopeResolver."
            )
        if auto_curation_wait_ms and not self._conversation_wiki_binding:
            raise ValueError("Automatic curation waiting requires conversation-Wiki binding.")
        if scope_resolver is not None and not isinstance(
            scope_resolver, ScopeResolver
        ):
            raise TypeError("scope_resolver must be a ScopeResolver or null.")
        self._scopes = scope_resolver
        self._personal_wikis = (
            personal_wikis or PersonalWikiProfileRepository()
        )
        if context_resolver is not None and not callable(
            getattr(context_resolver, "resolve", None)
        ):
            raise TypeError("context_resolver must provide resolve(request).")
        self._context_resolver = context_resolver

    def _active_actor(self, connection, actor_user_id: str, scope_epoch: int) -> UserRecord:
        try:
            actor = self._users.require(connection, actor_user_id)
        except UserNotFoundError as exc:
            raise ConversationAuthorizationError(
                "An active canonical user is required."
            ) from exc
        if actor.account_status != "active":
            raise ConversationAuthorizationError(
                "An active canonical user is required."
            )
        if actor.scope_epoch != scope_epoch:
            raise ConversationSessionScopeStaleError(
                "The authenticated conversation scope is stale."
            )
        return actor

    def _binding_fence(
        self,
        connection,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        space_id: str,
    ) -> ConversationBindingFence:
        """Resolve one server-owned authorization/corpus snapshot."""

        if not self._conversation_wiki_binding or self._scopes is None:
            raise ConversationServiceError(
                "Conversation-Wiki binding is not enabled."
            )
        profile = self._personal_wikis.get_by_space_id(connection, space_id)
        if (
            profile is not None
            and profile.owner_user_id == actor_user_id
            and profile.lifecycle_status == "archived"
        ):
            raise ConversationWikiArchivedHistoryOnlyError(
                "An archived personal Wiki is available as history only."
            )
        try:
            plan = self._scopes.resolve_access_plan_in_transaction(
                connection,
                SessionPrincipal(actor_user_id, actor_scope_epoch),
                space_id,
                Capability.READ,
            )
        except ScopeResolutionError as exc:
            raise ConversationServiceScopeError(
                "The conversation Wiki/view is not currently authorized."
            ) from exc
        personal_wiki: PersonalWikiProfileRecord | None = None
        if plan.selected_view.kind == "personal":
            personal_wiki = profile
        try:
            return ConversationBindingFence.from_access_plan(
                plan,
                personal_wiki=personal_wiki,
            )
        except (ConversationBindingIntegrityError, TypeError, ValueError) as exc:
            raise ConversationServiceScopeError(
                "The conversation Wiki/view binding is invalid."
            ) from exc

    def _require_mutation_access(
        self,
        connection,
        *,
        actor_user_id: str,
        conversation_id: str,
    ) -> ConversationRecord:
        if self._conversation_wiki_binding:
            status = self._conversations.access_status(
                connection,
                conversation_id=conversation_id,
                owner_user_id=actor_user_id,
            )
            if status == "wiki_archived":
                raise ConversationWikiArchivedHistoryOnlyError(
                    "An archived personal Wiki is available as history only."
                )
        return self._conversations.require_accessible(
            connection,
            conversation_id=conversation_id,
            owner_user_id=actor_user_id,
        )

    def _required_turn_binding(
        self,
        connection,
        *,
        turn_id: str,
        conversation_id: str,
    ) -> ConversationBindingFence:
        events = self._conversations.list_turn_events(
            connection,
            turn_id=turn_id,
            conversation_id=conversation_id,
            limit=1,
        )
        payload = events[0].payload if events else None
        if not isinstance(payload, Mapping):
            raise ConversationCorpusClosureStaleError(
                "The accepted turn has no binding checkpoint."
            )
        try:
            fence = ConversationBindingFence.from_mapping(
                payload.get("conversation_binding")
            )
        except (ConversationBindingIntegrityError, TypeError, ValueError) as exc:
            raise ConversationCorpusClosureStaleError(
                "The accepted turn binding checkpoint is invalid."
            ) from exc
        if payload.get("conversation_binding_sha256") != fence.cache_identity_sha256:
            raise ConversationCorpusClosureStaleError(
                "The accepted turn binding digest is invalid."
            )
        return fence

    def _validate_turn_binding(
        self,
        connection,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        conversation_id: str,
        turn_id: str,
    ) -> ConversationBindingFence:
        conversation = self._conversations.require_owned(
            connection,
            conversation_id=conversation_id,
            owner_user_id=actor_user_id,
        )
        stored = self._required_turn_binding(
            connection,
            turn_id=turn_id,
            conversation_id=conversation_id,
        )
        current = self._binding_fence(
            connection,
            actor_user_id=actor_user_id,
            actor_scope_epoch=actor_scope_epoch,
            space_id=conversation.space_id,
        )
        if stored != current:
            raise ConversationCorpusClosureStaleError(
                "The accepted turn corpus closure is stale."
            )
        return current

    def create(self, command: CreateConversationCommand) -> ConversationMutationResult:
        if not isinstance(command, CreateConversationCommand):
            raise TypeError("A CreateConversationCommand is required.")
        actor_id = _user(command.actor_user_id)
        epoch = _positive(command.actor_scope_epoch, "actor_scope_epoch")
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        space_id = _uuid(command.space_id, "space_id")
        title = _title(command.title)
        metadata = _canonical_object(command.metadata, "metadata")
        safe_meta = _safe_request_meta(command.request_meta)
        operation_type = "conversation.create"
        request_hash = _semantic_hash(
            {
                "actor_scope_epoch": epoch,
                "actor_user_id": actor_id,
                "metadata": metadata,
                "operation_type": operation_type,
                "schema_version": 1,
                "space_id": space_id,
                "title": title,
            }
        )
        conversation_id = conversation_id_for_create(actor_id, operation_id)
        return self._mutate(
            actor_id=actor_id,
            actor_scope_epoch=epoch,
            expected_conversation_id=conversation_id,
            operation_id=operation_id,
            request_id=request_id,
            operation_type=operation_type,
            request_hash=request_hash,
            request_meta=safe_meta,
            apply=lambda connection, now_ms: self._create_effect(
                connection,
                actor_id=actor_id,
                epoch=epoch,
                conversation_id=conversation_id,
                space_id=space_id,
                title=title,
                metadata=metadata,
                now_ms=now_ms,
                request_id=request_id,
                operation_id=operation_id,
                request_meta=safe_meta,
            ),
        )

    def update(self, command: UpdateConversationCommand) -> ConversationMutationResult:
        if not isinstance(command, UpdateConversationCommand):
            raise TypeError("An UpdateConversationCommand is required.")
        actor_id = _user(command.actor_user_id)
        epoch = _positive(command.actor_scope_epoch, "actor_scope_epoch")
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        conversation_id = _uuid(command.conversation_id, "conversation_id")
        expected_version = _positive(command.expected_row_version, "expected_row_version")
        title = None if command.title is None else _title(command.title)
        if command.status not in {None, "active", "archived"}:
            raise ValueError("status must be active, archived, or null.")
        if title is None and command.status is None:
            raise ValueError("At least one lifecycle field must be supplied.")
        safe_meta = _safe_request_meta(command.request_meta)
        operation_type = "conversation.update"
        request_hash = _semantic_hash(
            {
                "actor_scope_epoch": epoch,
                "actor_user_id": actor_id,
                "conversation_id": conversation_id,
                "expected_row_version": expected_version,
                "operation_type": operation_type,
                "schema_version": 1,
                "status": command.status,
                "title": title,
            }
        )
        return self._mutate(
            actor_id=actor_id,
            actor_scope_epoch=epoch,
            expected_conversation_id=conversation_id,
            operation_id=operation_id,
            request_id=request_id,
            operation_type=operation_type,
            request_hash=request_hash,
            request_meta=safe_meta,
            apply=lambda connection, now_ms: self._update_effect(
                connection,
                actor_id=actor_id,
                operation_id=operation_id,
                request_id=request_id,
                conversation_id=conversation_id,
                expected_version=expected_version,
                title=title,
                status=command.status,
                now_ms=now_ms,
                request_meta=safe_meta,
            ),
        )

    def add_pin(
        self, command: AddConversationPinCommand
    ) -> ConversationPinMutationResult:
        if not isinstance(command, AddConversationPinCommand):
            raise TypeError("An AddConversationPinCommand is required.")
        actor_id = _user(command.actor_user_id)
        epoch = _positive(command.actor_scope_epoch, "actor_scope_epoch")
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        conversation_id = _uuid(command.conversation_id, "conversation_id")
        item_id = _uuid(command.item_id, "item_id")
        revision_id = _uuid(command.revision_id, "revision_id")
        safe_meta = _safe_request_meta(command.request_meta)
        operation_type = "conversation.pin.add"
        request_hash = _semantic_hash(
            {
                "actor_scope_epoch": epoch,
                "actor_user_id": actor_id,
                "conversation_id": conversation_id,
                "item_id": item_id,
                "operation_type": operation_type,
                "revision_id": revision_id,
                "schema_version": 1,
            }
        )
        return self._run_attachment_operation(
            actor_id=actor_id,
            actor_scope_epoch=epoch,
            conversation_id=conversation_id,
            operation_id=operation_id,
            request_id=request_id,
            operation_type=operation_type,
            request_hash=request_hash,
            parser=ConversationPinMutationResult.from_mapping,
            replay_validator=lambda connection, result: self._validate_pin_result(
                connection,
                actor_id=actor_id,
                conversation_id=conversation_id,
                item_id=item_id,
                mutation="add",
                result=result,
            ),
            apply=lambda connection, actor, now_ms: self._add_pin_effect(
                connection,
                actor=actor,
                operation_id=operation_id,
                request_id=request_id,
                conversation_id=conversation_id,
                item_id=item_id,
                revision_id=revision_id,
                now_ms=now_ms,
                request_meta=safe_meta,
            ),
        )

    def remove_pin(
        self, command: RemoveConversationPinCommand
    ) -> ConversationPinMutationResult:
        if not isinstance(command, RemoveConversationPinCommand):
            raise TypeError("A RemoveConversationPinCommand is required.")
        actor_id = _user(command.actor_user_id)
        epoch = _positive(command.actor_scope_epoch, "actor_scope_epoch")
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        conversation_id = _uuid(command.conversation_id, "conversation_id")
        item_id = _uuid(command.item_id, "item_id")
        safe_meta = _safe_request_meta(command.request_meta)
        operation_type = "conversation.pin.remove"
        request_hash = _semantic_hash(
            {
                "actor_scope_epoch": epoch,
                "actor_user_id": actor_id,
                "conversation_id": conversation_id,
                "item_id": item_id,
                "operation_type": operation_type,
                "schema_version": 1,
            }
        )
        return self._run_attachment_operation(
            actor_id=actor_id,
            actor_scope_epoch=epoch,
            conversation_id=conversation_id,
            operation_id=operation_id,
            request_id=request_id,
            operation_type=operation_type,
            request_hash=request_hash,
            parser=ConversationPinMutationResult.from_mapping,
            replay_validator=lambda connection, result: self._validate_pin_result(
                connection,
                actor_id=actor_id,
                conversation_id=conversation_id,
                item_id=item_id,
                mutation="remove",
                result=result,
            ),
            apply=lambda connection, actor, now_ms: self._remove_pin_effect(
                connection,
                actor=actor,
                operation_id=operation_id,
                request_id=request_id,
                conversation_id=conversation_id,
                item_id=item_id,
                now_ms=now_ms,
                request_meta=safe_meta,
            ),
        )

    def list_pins(
        self,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        conversation_id: str,
        limit: int = 100,
    ) -> tuple[ConversationPinRecord, ...]:
        actor_id = _user(actor_user_id)
        epoch = _positive(actor_scope_epoch, "actor_scope_epoch")
        conversation_id = _uuid(conversation_id, "conversation_id")
        connection = self._factory.open()
        try:
            self._active_actor(connection, actor_id, epoch)
            try:
                return self._conversations.list_pins(
                    connection,
                    conversation_id=conversation_id,
                    owner_user_id=actor_id,
                    limit=limit,
                )
            except (ConversationScopeError, ConversationNotFoundError) as exc:
                raise ConversationServiceScopeError(str(exc)) from exc
        finally:
            connection.close()

    def add_context(
        self, command: AddConversationContextCommand
    ) -> ConversationContextMutationResult:
        if not isinstance(command, AddConversationContextCommand):
            raise TypeError("An AddConversationContextCommand is required.")
        actor_id = _user(command.actor_user_id)
        epoch = _positive(command.actor_scope_epoch, "actor_scope_epoch")
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        conversation_id = _uuid(command.conversation_id, "conversation_id")
        binding_type = command.binding_type
        binding_mode = command.binding_mode
        if binding_type not in {
            "workspace_file", "project_context", "notion_page", "source_artifact"
        }:
            raise ValueError("binding_type is invalid.")
        if binding_mode not in {"snapshot", "live"}:
            raise ValueError("binding_mode is invalid.")
        if binding_type == "source_artifact" and binding_mode != "snapshot":
            raise ValueError("A source-artifact binding must be a snapshot.")
        external_key = _bounded_text(command.external_key, "external_key", 1000)
        safe_meta = _safe_request_meta(command.request_meta)
        operation_type = "conversation.context.add"
        request_hash = _semantic_hash(
            {
                "actor_scope_epoch": epoch,
                "actor_user_id": actor_id,
                "binding_mode": binding_mode,
                "binding_type": binding_type,
                "conversation_id": conversation_id,
                "external_key": external_key,
                "operation_type": operation_type,
                "schema_version": 1,
            }
        )
        replay = self._preflight_attachment_replay(
            actor_id=actor_id,
            actor_scope_epoch=epoch,
            conversation_id=conversation_id,
            operation_id=operation_id,
            operation_type=operation_type,
            request_hash=request_hash,
            parser=ConversationContextMutationResult.from_mapping,
            replay_validator=lambda connection, result: self._validate_context_result(
                connection,
                actor_id=actor_id,
                conversation_id=conversation_id,
                mutation="add",
                result=result,
            ),
        )
        if replay is not None:
            return replay
        resolution = self._resolve_context(
            actor_id=actor_id,
            actor_scope_epoch=epoch,
            conversation_id=conversation_id,
            binding_type=binding_type,
            binding_mode=binding_mode,
            external_key=external_key,
        )
        return self._run_attachment_operation(
            actor_id=actor_id,
            actor_scope_epoch=epoch,
            conversation_id=conversation_id,
            operation_id=operation_id,
            request_id=request_id,
            operation_type=operation_type,
            request_hash=request_hash,
            parser=ConversationContextMutationResult.from_mapping,
            replay_validator=lambda connection, result: self._validate_context_result(
                connection,
                actor_id=actor_id,
                conversation_id=conversation_id,
                mutation="add",
                result=result,
            ),
            apply=lambda connection, actor, now_ms: self._add_context_effect(
                connection,
                actor=actor,
                operation_id=operation_id,
                request_id=request_id,
                conversation_id=conversation_id,
                resolution=resolution,
                now_ms=now_ms,
                request_meta=safe_meta,
            ),
        )

    def remove_context(
        self, command: RemoveConversationContextCommand
    ) -> ConversationContextMutationResult:
        if not isinstance(command, RemoveConversationContextCommand):
            raise TypeError("A RemoveConversationContextCommand is required.")
        actor_id = _user(command.actor_user_id)
        epoch = _positive(command.actor_scope_epoch, "actor_scope_epoch")
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        conversation_id = _uuid(command.conversation_id, "conversation_id")
        binding_id = _uuid(command.binding_id, "binding_id")
        safe_meta = _safe_request_meta(command.request_meta)
        operation_type = "conversation.context.remove"
        request_hash = _semantic_hash(
            {
                "actor_scope_epoch": epoch,
                "actor_user_id": actor_id,
                "binding_id": binding_id,
                "conversation_id": conversation_id,
                "operation_type": operation_type,
                "schema_version": 1,
            }
        )
        return self._run_attachment_operation(
            actor_id=actor_id,
            actor_scope_epoch=epoch,
            conversation_id=conversation_id,
            operation_id=operation_id,
            request_id=request_id,
            operation_type=operation_type,
            request_hash=request_hash,
            parser=ConversationContextMutationResult.from_mapping,
            replay_validator=lambda connection, result: self._validate_context_result(
                connection,
                actor_id=actor_id,
                conversation_id=conversation_id,
                mutation="remove",
                result=result,
            ),
            apply=lambda connection, actor, now_ms: self._remove_context_effect(
                connection,
                actor=actor,
                operation_id=operation_id,
                request_id=request_id,
                conversation_id=conversation_id,
                binding_id=binding_id,
                now_ms=now_ms,
                request_meta=safe_meta,
            ),
        )

    def list_contexts(
        self,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        conversation_id: str,
        include_detached: bool = False,
        limit: int = 100,
    ) -> tuple[ConversationContextBindingRecord, ...]:
        actor_id = _user(actor_user_id)
        epoch = _positive(actor_scope_epoch, "actor_scope_epoch")
        conversation_id = _uuid(conversation_id, "conversation_id")
        connection = self._factory.open()
        try:
            self._active_actor(connection, actor_id, epoch)
            try:
                return self._conversations.list_context_bindings(
                    connection,
                    conversation_id=conversation_id,
                    owner_user_id=actor_id,
                    include_detached=include_detached,
                    limit=limit,
                )
            except (ConversationScopeError, ConversationNotFoundError) as exc:
                raise ConversationServiceScopeError(str(exc)) from exc
        finally:
            connection.close()

    def _auto_curation_pending_for_turn(self, command: AcceptConversationTurnCommand) -> bool:
        with closing(self._factory.open(read_only=True)) as connection:
            with transaction(connection, TransactionMode.DEFERRED):
                actor_id = _user(command.actor_user_id)
                epoch = _positive(command.actor_scope_epoch, "actor_scope_epoch")
                conversation_id = _uuid(command.conversation_id, "conversation_id")
                self._active_actor(connection, actor_id, epoch)
                conversation = self._conversations.require_owned(
                    connection, conversation_id=conversation_id,
                    owner_user_id=actor_id,
                )
                self._binding_fence(
                    connection, actor_user_id=actor_id,
                    actor_scope_epoch=epoch, space_id=conversation.space_id,
                )
                existing = self._conversations.get_turn_by_operation(
                    connection, conversation_id=conversation_id,
                    operation_id=command.operation_id,
                )
                if existing is not None:
                    return False
                # An item may already be published while its publication
                # outbox and semantic-edge jobs are still changing the corpus
                # closure.  Resolve the server-owned closure so inherited
                # Team/Responsibility sources are covered as well.
                plan = self._scopes.resolve_access_plan_in_transaction(
                    connection,
                    SessionPrincipal(actor_id, epoch),
                    conversation.space_id,
                    Capability.READ,
                )
                source_ids = tuple(sorted({conversation.space_id} | {
                    source.source_space_id for source in plan.corpus_closure.sources
                }))
                placeholders = ",".join("?" for _ in source_ids)
                if connection.execute(
                    f"""SELECT 1 FROM durable_job j
                          LEFT JOIN knowledge_item i
                            ON i.id=json_extract(j.input_ref_json,'$.item_id')
                         WHERE j.state IN ('queued','claimed','running','retry')
                           AND (
                             (j.job_type='semantic_edge.evaluate'
                              AND j.space_id IN ({placeholders}))
                             OR
                             (j.job_type='curation.rescan'
                              AND i.space_id IN ({placeholders})
                              AND i.curation_status='provisional')
                           )
                         LIMIT 1""",
                    source_ids + source_ids,
                ).fetchone() is not None:
                    return True
                return connection.execute(
                    f"""SELECT 1 FROM outbox_event
                         WHERE event_type='knowledge.item.published'
                           AND state IN ('pending','processing','retry')
                           AND space_id IN ({placeholders})
                         LIMIT 1""",
                    source_ids,
                ).fetchone() is not None

    def _await_auto_curation_before_turn(self, command: AcceptConversationTurnCommand) -> None:
        """Do not accept a turn against a soon-to-change publication closure.

        This opt-in waits for pending publication, automatic curation, and
        semantic-edge jobs in the authorized corpus closure. No DB connection
        or WAL snapshot is retained during the bounded wait. Existing turns
        replay immediately, and every observation rechecks actor/Wiki authority.
        """

        if not self._auto_curation_wait_ms:
            return
        deadline = time.monotonic() + self._auto_curation_wait_ms / 1000
        while True:
            pending = self._auto_curation_pending_for_turn(command)
            if not pending:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ConversationOperationInProgressError(
                    "The selected Wiki is still being indexed; retry this question shortly."
                )
            time.sleep(min(0.2, remaining))

    def execute_turn(
        self, command: AcceptConversationTurnCommand,
        executor: Callable[[ConversationTurnResult], object],
    ) -> ConversationTurnResult:
        """Execute or reconnect under cross-process ownership, outside SQL transactions."""
        if not isinstance(command, AcceptConversationTurnCommand) or not callable(executor):
            raise TypeError("Turn execution requires a command and executor.")
        with closing(self._factory.open(read_only=True)) as probe:
            identity = probe.database_identity
        # Do not retain an idle SQLite/WAL handle for the entire model call.
        # Execution exclusion belongs to the OS mutex, not an open DB reader.
        with hold_turn_execution(identity, command.conversation_id, command.operation_id) as owner:
            self._await_auto_curation_before_turn(command)
            accepted = self.accept_turn(command, _execution_protocol=TURN_EXECUTION_PROTOCOL)
            if owner and accepted.state not in {"completed", "failed", "cancelled"}:
                with closing(self._factory.open(read_only=True)) as probe:
                    if probe.database_identity != identity:
                        raise ConversationServiceError("The turn database file identity changed.")
                    first = self._conversations.list_turn_events(
                        probe, turn_id=accepted.turn_id,
                        conversation_id=accepted.conversation_id, limit=1,
                    )
                # Never steal a pre-upgrade request that lacks this ownership
                # protocol. Quiesced legacy-turn recovery is a separate action.
                if first and first[0].payload.get("execution_protocol") == TURN_EXECUTION_PROTOCOL:
                    executor(accepted)
            return accepted

    def accept_turn(
        self, command: AcceptConversationTurnCommand, *,
        _execution_protocol: str | None = None,
    ) -> ConversationTurnResult:
        if not isinstance(command, AcceptConversationTurnCommand):
            raise TypeError("An AcceptConversationTurnCommand is required.")
        if _execution_protocol not in {None, TURN_EXECUTION_PROTOCOL}:
            raise ValueError("The turn execution protocol is invalid.")
        actor_id = _user(command.actor_user_id)
        epoch = _positive(command.actor_scope_epoch, "actor_scope_epoch")
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        conversation_id = _uuid(command.conversation_id, "conversation_id")
        user_content = _canonical_object(command.user_content, "user_content")
        safe_meta = _safe_request_meta(command.request_meta)
        request_hash = _semantic_hash(
            {
                "actor_scope_epoch": epoch,
                "actor_user_id": actor_id,
                "conversation_id": conversation_id,
                "operation_type": "conversation_turn",
                "schema_version": 1,
                "user_content": user_content,
            }
        )
        dedup_id = _derived(
            _DEDUP_NAMESPACE,
            actor_id,
            "conversation_turn",
            operation_id,
        )
        turn_id = conversation_turn_id(conversation_id, operation_id)
        user_message_id = conversation_turn_user_message_id(
            conversation_id, operation_id
        )
        connection = self._factory.open()
        result: ConversationTurnResult | None = None
        try:
            with transaction(connection):
                actor = self._active_actor(connection, actor_id, epoch)
                now_ms = db_now_ms(connection)
                current_fence: ConversationBindingFence | None = None
                try:
                    begun = self._users.begin_operation(
                        connection,
                        actor_user_id=actor.id,
                        operation_type="conversation_turn",
                        operation_id=operation_id,
                        request_sha256=request_hash,
                        dedup_id=dedup_id,
                        now_ms=now_ms,
                    )
                except OperationIdempotencyConflictError as exc:
                    raise ConversationIdempotencyError(str(exc)) from exc
                if not begun.inserted and begun.operation.status == "committed":
                    stored = begun.operation.result
                    if not isinstance(stored, Mapping):
                        raise ConversationIdempotencyError(
                            "The completed turn has no stored result."
                        )
                    try:
                        result = ConversationTurnResult.from_mapping(stored)
                    except (TypeError, ValueError) as exc:
                        raise ConversationIdempotencyError(
                            "The completed turn result is invalid."
                        ) from exc
                    if (
                        result.operation_id != operation_id
                        or result.conversation_id != conversation_id
                        or result.turn_id != turn_id
                        or result.user_message_id != user_message_id
                        or (
                            result.assistant_message_id is not None
                            and result.assistant_message_id
                            != conversation_turn_assistant_message_id(
                                conversation_id, operation_id
                            )
                        )
                    ):
                        raise ConversationIdempotencyError(
                            "The completed turn result is bound incorrectly."
                        )
                    self._conversations.require_history_readable(
                        connection,
                        conversation_id=conversation_id,
                        owner_user_id=actor.id,
                    )
                else:
                    if self._conversation_wiki_binding:
                        if begun.inserted:
                            conversation = self._conversations.require_owned(
                                connection,
                                conversation_id=conversation_id,
                                owner_user_id=actor.id,
                            )
                            current_fence = self._binding_fence(
                                connection,
                                actor_user_id=actor.id,
                                actor_scope_epoch=epoch,
                                space_id=conversation.space_id,
                            )
                        else:
                            current_fence = self._validate_turn_binding(
                                connection,
                                actor_user_id=actor.id,
                                actor_scope_epoch=epoch,
                                conversation_id=conversation_id,
                                turn_id=turn_id,
                            )
                    effective_request_id = (
                        request_id
                        if begun.inserted
                        else self._required_turn_request_id(connection, turn_id)
                    )
                    execution_protocol = _execution_protocol
                    if not begun.inserted:
                        first = self._conversations.list_turn_events(
                            connection, turn_id=turn_id, conversation_id=conversation_id, limit=1
                        )
                        execution_protocol = first[0].payload.get("execution_protocol") if first else None
                    accepted = self._conversations.accept_turn(
                        connection,
                        ConversationTurnAccept(
                            turn_id=turn_id,
                            conversation_id=conversation_id,
                            actor_user_id=actor.id,
                            dedup_id=dedup_id,
                            operation_id=operation_id,
                            request_sha256=request_hash,
                            user_message_id=user_message_id,
                            user_content_json=_canonical_json(
                                {"content": user_content, "role": "user"}
                            ),
                            accepted_event_json=_canonical_json(
                                {
                                    **({"execution_protocol": execution_protocol}
                                       if execution_protocol is not None else {}),
                                    **(
                                        {}
                                        if current_fence is None
                                        else {
                                            "conversation_binding": (
                                                current_fence.as_mapping()
                                            ),
                                            "conversation_binding_sha256": (
                                                current_fence.cache_identity_sha256
                                            ),
                                        }
                                    ),
                                    "operation_id": operation_id,
                                    "request_id": effective_request_id,
                                    "schema_version": 1,
                                    "state": "accepted",
                                }
                            ),
                            created_at_ms=(
                                now_ms
                                if begun.inserted
                                else self._required_turn_created_at(
                                    connection, turn_id
                                )
                            ),
                        ),
                    )
                    result = self._turn_result(
                        connection,
                        accepted.turn,
                        request_id=effective_request_id,
                    )
                    if accepted.inserted:
                        conversation = self._conversations.require_owned(
                            connection,
                            conversation_id=conversation_id,
                            owner_user_id=actor.id,
                        )
                        automatic_title = _first_question_title(user_content)
                        if (
                            accepted.user_message.sequence_no == 1
                            and not conversation.title
                            and automatic_title
                        ):
                            self._conversations.update_lifecycle(
                                connection,
                                conversation_id=conversation.id,
                                owner_user_id=actor.id,
                                expected_row_version=conversation.row_version,
                                updated_at_ms=now_ms,
                                title=automatic_title,
                            )
                        self._append_turn_events(
                            connection,
                            result=result,
                            actor=actor,
                            action="conversation.turn.accept",
                            event_type="conversation.turn.accepted",
                            request_meta=safe_meta,
                            now_ms=now_ms,
                        )
        except (ConversationScopeError, ConversationNotFoundError) as exc:
            raise ConversationServiceScopeError(str(exc)) from exc
        finally:
            connection.close()
        if result is None:
            raise ConversationServiceError("The accepted turn result is unavailable.")
        return result

    def complete_turn(
        self, command: CompleteConversationTurnCommand
    ) -> ConversationTurnResult:
        if not isinstance(command, CompleteConversationTurnCommand):
            raise TypeError("A CompleteConversationTurnCommand is required.")
        actor_id = _user(command.actor_user_id)
        epoch = _positive(command.actor_scope_epoch, "actor_scope_epoch")
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        conversation_id = _uuid(command.conversation_id, "conversation_id")
        assistant_content = _canonical_object(
            command.assistant_content, "assistant_content"
        )
        final_response = _canonical_object(command.final_response, "final_response")
        safe_meta = _safe_request_meta(command.request_meta)
        dedup_id = _derived(
            _DEDUP_NAMESPACE,
            actor_id,
            "conversation_turn",
            operation_id,
        )
        turn_id = conversation_turn_id(conversation_id, operation_id)
        assistant_id = conversation_turn_assistant_message_id(
            conversation_id, operation_id
        )
        connection = self._factory.open()
        result: ConversationTurnResult | None = None
        try:
            with transaction(connection):
                actor = self._active_actor(connection, actor_id, epoch)
                operation = self._users.get_operation(connection, dedup_id)
                if (
                    operation is None
                    or operation.actor_user_id != actor.id
                    or operation.operation_type != "conversation_turn"
                    or operation.operation_id != operation_id
                ):
                    raise ConversationIdempotencyError(
                        "The accepted turn operation does not exist."
                    )
                if operation.status == "committed":
                    stored = operation.result
                    if not isinstance(stored, Mapping):
                        raise ConversationIdempotencyError(
                            "The completed turn has no stored result."
                        )
                    try:
                        result = ConversationTurnResult.from_mapping(stored)
                    except (TypeError, ValueError) as exc:
                        raise ConversationIdempotencyError(
                            "The completed turn result is invalid."
                        ) from exc
                    if (
                        result.operation_id != operation_id
                        or result.conversation_id != conversation_id
                        or result.turn_id != turn_id
                        or result.user_message_id
                        != conversation_turn_user_message_id(
                            conversation_id, operation_id
                        )
                        or (
                            result.assistant_message_id is not None
                            and result.assistant_message_id != assistant_id
                        )
                    ):
                        raise ConversationIdempotencyError(
                            "The completed turn result is bound incorrectly."
                        )
                    self._conversations.require_history_readable(
                        connection,
                        conversation_id=conversation_id,
                        owner_user_id=actor.id,
                    )
                elif operation.status != "in_progress":
                    raise ConversationIdempotencyError(
                        "The accepted turn cannot be completed."
                    )
                else:
                    now_ms = db_now_ms(connection)
                    accepted_request_id = self._required_turn_request_id(
                        connection, turn_id
                    )
                    if accepted_request_id != request_id:
                        raise ConversationIdempotencyError(
                            "The completion request does not bind the accepted turn."
                        )
                    if self._conversation_wiki_binding:
                        self._validate_turn_binding(
                            connection,
                            actor_user_id=actor.id,
                            actor_scope_epoch=epoch,
                            conversation_id=conversation_id,
                            turn_id=turn_id,
                        )
                    running = self._conversations.mark_turn_running(
                        connection,
                        turn_id=turn_id,
                        conversation_id=conversation_id,
                        actor_user_id=actor.id,
                        updated_at_ms=now_ms,
                    )
                    completed = self._conversations.complete_turn(
                        connection,
                        ConversationTurnCompletion(
                            turn_id=running.id,
                            conversation_id=conversation_id,
                            actor_user_id=actor.id,
                            assistant_message_id=assistant_id,
                            assistant_content_json=_canonical_json(
                                {"content": assistant_content, "role": "assistant"}
                            ),
                            final_response_json=_canonical_json(final_response),
                            completed_event_json=_canonical_json(
                                {
                                    "assistant_message_id": assistant_id,
                                    "schema_version": 1,
                                    "state": "completed",
                                }
                            ),
                            completed_at_ms=now_ms,
                        ),
                    )
                    result = self._turn_result(
                        connection,
                        completed.turn,
                        request_id=request_id,
                    )
                    self._append_turn_events(
                        connection,
                        result=result,
                        actor=actor,
                        action="conversation.turn.complete",
                        event_type="conversation.turn.completed",
                        request_meta=safe_meta,
                        now_ms=now_ms,
                    )
                    self._users.commit_operation(
                        connection,
                        dedup_id=dedup_id,
                        result=result.as_dict(),
                        http_status=200,
                        completed_at_ms=now_ms,
                    )
        except (ConversationScopeError, ConversationNotFoundError) as exc:
            raise ConversationServiceScopeError(str(exc)) from exc
        except ConversationVersionConflictError as exc:
            raise ConversationServiceVersionError(str(exc)) from exc
        finally:
            connection.close()
        if result is None:
            raise ConversationServiceError("The completed turn result is unavailable.")
        return result

    def terminate_turn(
        self, command: TerminateConversationTurnCommand
    ) -> ConversationTurnResult:
        if not isinstance(command, TerminateConversationTurnCommand):
            raise TypeError("A TerminateConversationTurnCommand is required.")
        actor_id = _user(command.actor_user_id)
        epoch = _positive(command.actor_scope_epoch, "actor_scope_epoch")
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        conversation_id = _uuid(command.conversation_id, "conversation_id")
        terminal_state = command.terminal_state
        if terminal_state not in {"failed", "cancelled"}:
            raise ValueError("terminal_state must be failed or cancelled.")
        if not isinstance(command.retryable, bool):
            raise ValueError("retryable must be a Boolean.")
        error: dict[str, object] | None
        if terminal_state == "failed":
            error = _turn_error(
                {
                    "code": command.error_code,
                    "retryable": command.retryable,
                    "schema_version": 1,
                }
            )
        else:
            if command.error_code is not None or command.retryable:
                raise ValueError("A cancelled turn cannot carry an error.")
            error = None
        safe_meta = _safe_request_meta(command.request_meta)
        dedup_id = _derived(
            _DEDUP_NAMESPACE,
            actor_id,
            "conversation_turn",
            operation_id,
        )
        turn_id = conversation_turn_id(conversation_id, operation_id)
        user_message_id = conversation_turn_user_message_id(
            conversation_id, operation_id
        )
        assistant_message_id = conversation_turn_assistant_message_id(
            conversation_id, operation_id
        )
        connection = self._factory.open()
        result: ConversationTurnResult | None = None
        try:
            with transaction(connection):
                actor = self._active_actor(connection, actor_id, epoch)
                operation = self._users.get_operation(connection, dedup_id)
                if (
                    operation is None
                    or operation.actor_user_id != actor.id
                    or operation.operation_type != "conversation_turn"
                    or operation.operation_id != operation_id
                ):
                    raise ConversationIdempotencyError(
                        "The accepted turn operation does not exist."
                    )
                if operation.status == "committed":
                    stored = operation.result
                    if not isinstance(stored, Mapping):
                        raise ConversationIdempotencyError(
                            "The terminal turn has no stored result."
                        )
                    try:
                        result = ConversationTurnResult.from_mapping(stored)
                    except (TypeError, ValueError) as exc:
                        raise ConversationIdempotencyError(
                            "The terminal turn result is invalid."
                        ) from exc
                    if (
                        result.operation_id != operation_id
                        or result.conversation_id != conversation_id
                        or result.turn_id != turn_id
                        or result.user_message_id != user_message_id
                        or (
                            result.assistant_message_id is not None
                            and result.assistant_message_id != assistant_message_id
                        )
                    ):
                        raise ConversationIdempotencyError(
                            "The terminal turn result is bound incorrectly."
                        )
                    self._conversations.require_history_readable(
                        connection,
                        conversation_id=conversation_id,
                        owner_user_id=actor.id,
                    )
                elif operation.status != "in_progress":
                    raise ConversationIdempotencyError(
                        "The accepted turn cannot be terminated."
                    )
                else:
                    accepted_request_id = self._required_turn_request_id(
                        connection, turn_id
                    )
                    if accepted_request_id != request_id:
                        raise ConversationIdempotencyError(
                            "The terminal request does not bind the accepted turn."
                        )
                    now_ms = db_now_ms(connection)
                    terminated = self._conversations.terminate_turn(
                        connection,
                        ConversationTurnTermination(
                            turn_id=turn_id,
                            conversation_id=conversation_id,
                            actor_user_id=actor.id,
                            terminal_state=terminal_state,
                            error_json=(
                                None if error is None else _canonical_json(error)
                            ),
                            terminal_event_json=_canonical_json(
                                {
                                    **({} if error is None else {"error": error}),
                                    "schema_version": 1,
                                    "state": terminal_state,
                                }
                            ),
                            completed_at_ms=now_ms,
                        ),
                    )
                    result = self._turn_result(
                        connection,
                        terminated.turn,
                        request_id=request_id,
                    )
                    self._append_turn_events(
                        connection,
                        result=result,
                        actor=actor,
                        action=f"conversation.turn.{terminal_state}",
                        event_type=f"conversation.turn.{terminal_state}",
                        request_meta=safe_meta,
                        now_ms=now_ms,
                    )
                    self._users.commit_operation(
                        connection,
                        dedup_id=dedup_id,
                        result=result.as_dict(),
                        http_status=200,
                        completed_at_ms=now_ms,
                    )
        except ConversationScopeError as exc:
            raise ConversationServiceScopeError(str(exc)) from exc
        except ConversationVersionConflictError as exc:
            raise ConversationServiceVersionError(str(exc)) from exc
        except ConversationStateConflictError as exc:
            raise ConversationIdempotencyError(str(exc)) from exc
        finally:
            connection.close()
        if result is None:
            raise ConversationServiceError("The terminal turn result is unavailable.")
        return result

    def compact(
        self,
        command: CompactConversationCommand,
    ) -> ConversationCompactResult:
        if not isinstance(command, CompactConversationCommand):
            raise TypeError("A CompactConversationCommand is required.")
        actor_id = _user(command.actor_user_id)
        epoch = _positive(command.actor_scope_epoch, "actor_scope_epoch")
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        conversation_id = _uuid(command.conversation_id, "conversation_id")
        covered = _positive(
            command.covered_through_sequence_no,
            "covered_through_sequence_no",
        )
        summary_md = _summary_text(command.summary_md)
        model_id = _bounded_text(command.model_id, "model_id", 200)
        prompt_version = _bounded_text(
            command.prompt_version,
            "prompt_version",
            120,
        )
        summarizer_version = _bounded_text(
            command.summarizer_version,
            "summarizer_version",
            120,
        )
        safe_meta = _safe_request_meta(command.request_meta)
        operation_type = "conversation.compact"
        request_hash = _semantic_hash(
            {
                "actor_scope_epoch": epoch,
                "actor_user_id": actor_id,
                "conversation_id": conversation_id,
                "covered_through_sequence_no": covered,
                "operation_type": operation_type,
                "schema_version": 1,
            }
        )
        dedup_id = _derived(
            _DEDUP_NAMESPACE,
            actor_id,
            operation_type,
            operation_id,
        )
        summary_id = conversation_summary_id(conversation_id, operation_id)
        connection = self._factory.open()
        result: ConversationCompactResult | None = None
        try:
            with transaction(connection):
                actor = self._active_actor(connection, actor_id, epoch)
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
                    raise ConversationIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored = begun.operation.result
                        if not isinstance(stored, Mapping):
                            raise ConversationIdempotencyError(
                                "The committed compaction has no stored result."
                            )
                        try:
                            result = ConversationCompactResult.from_mapping(stored)
                        except (TypeError, ValueError) as exc:
                            raise ConversationIdempotencyError(
                                "The committed compaction result is invalid."
                            ) from exc
                        if (
                            result.operation_id != operation_id
                            or result.summary.id != summary_id
                            or result.summary.conversation_id != conversation_id
                            or result.summary.created_by_user_id != actor.id
                        ):
                            raise ConversationIdempotencyError(
                                "The committed compaction result is bound incorrectly."
                            )
                        conversation = self._conversations.require_history_readable(
                            connection,
                            conversation_id=conversation_id,
                            owner_user_id=actor.id,
                        )
                        if result.space_id != conversation.space_id:
                            raise ConversationIdempotencyError(
                                "The committed compaction space is bound incorrectly."
                            )
                    elif begun.operation.status == "in_progress":
                        raise ConversationOperationInProgressError(
                            "The conversation compaction is already in progress."
                        )
                    else:
                        raise ConversationIdempotencyError(
                            "The conversation compaction is terminal without a result."
                        )
                else:
                    self._require_mutation_access(
                        connection,
                        actor_user_id=actor.id,
                        conversation_id=conversation_id,
                    )
                    try:
                        created = self._conversations.put_summary(
                            connection,
                            ConversationSummaryCreate(
                                summary_id=summary_id,
                                conversation_id=conversation_id,
                                owner_user_id=actor.id,
                                covered_through_sequence_no=covered,
                                summary_md=summary_md,
                                summary_sha256=sha256(
                                    summary_md.encode("utf-8")
                                ).hexdigest(),
                                model_id=model_id,
                                prompt_version=prompt_version,
                                summarizer_version=summarizer_version,
                                created_by_user_id=actor.id,
                                created_at_ms=now_ms,
                            ),
                        )
                    except ConversationScopeError as exc:
                        raise ConversationServiceScopeError(str(exc)) from exc
                    except ConversationVersionConflictError as exc:
                        raise ConversationServiceVersionError(str(exc)) from exc
                    except ConversationStateConflictError as exc:
                        raise ConversationIdempotencyError(str(exc)) from exc
                    conversation = self._conversations.require_owned(
                        connection,
                        conversation_id=conversation_id,
                        owner_user_id=actor.id,
                    )
                    outcome: Literal["created", "replaced", "no_change"]
                    if not created.inserted:
                        outcome = "no_change"
                    elif created.invalidated_summary_id is None:
                        outcome = "created"
                    else:
                        outcome = "replaced"
                    result = ConversationCompactResult(
                        request_id=request_id,
                        operation_id=operation_id,
                        outcome=outcome,
                        space_id=conversation.space_id,
                        summary=created.summary,
                    )
                    self._append_summary_events(
                        connection,
                        result=result,
                        actor=actor,
                        invalidated_summary_id=created.invalidated_summary_id,
                        request_meta=safe_meta,
                        now_ms=now_ms,
                    )
                    self._users.commit_operation(
                        connection,
                        dedup_id=dedup_id,
                        result=result.as_dict(),
                        http_status=202,
                        completed_at_ms=now_ms,
                    )
        except ConversationScopeError as exc:
            raise ConversationServiceScopeError(str(exc)) from exc
        except OperationInProgressError as exc:
            raise ConversationOperationInProgressError(str(exc)) from exc
        finally:
            connection.close()
        if result is None:
            raise ConversationServiceError(
                "The compact-summary result is unavailable."
            )
        return result

    def get_active_summary(
        self,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        conversation_id: str,
    ) -> ConversationSummaryRecord | None:
        actor_id = _user(actor_user_id)
        epoch = _positive(actor_scope_epoch, "actor_scope_epoch")
        conversation_id = _uuid(conversation_id, "conversation_id")
        connection = self._factory.open()
        try:
            self._active_actor(connection, actor_id, epoch)
            try:
                return self._conversations.get_active_summary(
                    connection,
                    conversation_id=conversation_id,
                    owner_user_id=actor_id,
                )
            except (ConversationScopeError, ConversationNotFoundError) as exc:
                raise ConversationServiceScopeError(str(exc)) from exc
        finally:
            connection.close()

    def list_summary_history(
        self,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        conversation_id: str,
        limit: int = 100,
    ) -> tuple[ConversationSummaryRecord, ...]:
        actor_id = _user(actor_user_id)
        epoch = _positive(actor_scope_epoch, "actor_scope_epoch")
        conversation_id = _uuid(conversation_id, "conversation_id")
        connection = self._factory.open()
        try:
            self._active_actor(connection, actor_id, epoch)
            try:
                return self._conversations.list_summary_history(
                    connection,
                    conversation_id=conversation_id,
                    owner_user_id=actor_id,
                    limit=limit,
                )
            except (ConversationScopeError, ConversationNotFoundError) as exc:
                raise ConversationServiceScopeError(str(exc)) from exc
        finally:
            connection.close()

    def reconnect_turn(
        self,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        conversation_id: str,
        operation_id: str,
        after_event_sequence_no: int = 0,
        limit: int = 100,
    ) -> tuple[ConversationTurnResult, tuple[ConversationTurnEventRecord, ...]]:
        actor_id = _user(actor_user_id)
        epoch = _positive(actor_scope_epoch, "actor_scope_epoch")
        conversation_id = _uuid(conversation_id, "conversation_id")
        operation_id = _uuid(operation_id, "operation_id")
        connection = self._factory.open()
        try:
            self._active_actor(connection, actor_id, epoch)
            self._conversations.require_history_readable(
                connection,
                conversation_id=conversation_id,
                owner_user_id=actor_id,
            )
            turn = self._conversations.get_turn_by_operation(
                connection,
                conversation_id=conversation_id,
                operation_id=operation_id,
            )
            if turn is None or turn.actor_user_id != actor_id:
                raise ConversationAuthorizationError(
                    "The requested conversation turn does not exist."
                )
            events = self._conversations.list_turn_events(
                connection,
                turn_id=turn.id,
                conversation_id=conversation_id,
                after_sequence_no=after_event_sequence_no,
                limit=limit,
            )
            first = self._conversations.list_turn_events(
                connection,
                turn_id=turn.id,
                conversation_id=conversation_id,
                limit=1,
            )
            request_id = (
                first[0].payload.get("request_id") if first else None
            )
            return (
                self._turn_result(
                    connection,
                    turn,
                    request_id=_uuid(request_id, "request_id"),
                ),
                events,
            )
        except ConversationScopeError as exc:
            raise ConversationServiceScopeError(str(exc)) from exc
        finally:
            connection.close()

    def list_for_owner(
        self,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        include_archived: bool = True,
        before_updated_at_ms: int | None = None,
        before_conversation_id: str | None = None,
        limit: int = 50,
    ) -> tuple[ConversationListEntry, ...]:
        actor_id = _user(actor_user_id)
        epoch = _positive(actor_scope_epoch, "actor_scope_epoch")
        connection = self._factory.open()
        try:
            self._active_actor(connection, actor_id, epoch)
            return self._conversations.list_for_owner(
                connection,
                owner_user_id=actor_id,
                include_archived=include_archived,
                before_updated_at_ms=before_updated_at_ms,
                before_conversation_id=before_conversation_id,
                limit=limit,
            )
        finally:
            connection.close()

    def get(
        self,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        conversation_id: str,
    ) -> ConversationRecord:
        actor_id = _user(actor_user_id)
        epoch = _positive(actor_scope_epoch, "actor_scope_epoch")
        conversation_id = _uuid(conversation_id, "conversation_id")
        connection = self._factory.open()
        try:
            self._active_actor(connection, actor_id, epoch)
            try:
                return self._conversations.require_history_readable(
                    connection,
                    conversation_id=conversation_id,
                    owner_user_id=actor_id,
                )
            except ConversationScopeError as exc:
                raise ConversationServiceScopeError(str(exc)) from exc
        finally:
            connection.close()

    def read_message_page(
        self, *, actor_user_id: str, actor_scope_epoch: int, conversation_id: str,
        after_sequence_no: int = 0, through_sequence_no: int | None = None, limit: int = 100,
    ) -> dict[str, object]:
        actor = _user(actor_user_id)
        epoch = _positive(actor_scope_epoch, "actor_scope_epoch")
        cid = _uuid(conversation_id, "conversation_id")
        after = _nonnegative(after_sequence_no, "after_sequence_no")
        requested_through = None if through_sequence_no is None else _nonnegative(through_sequence_no, "through_sequence_no")
        limit = _positive(limit, "limit")
        if limit > 100:
            raise ValueError("limit must not exceed 100.")
        with closing(self._factory.open(read_only=True)) as connection:
            with transaction(connection, TransactionMode.DEFERRED):
                self._active_actor(connection, actor, epoch)
                try:
                    watermark = self._conversations.message_watermark(
                        connection, conversation_id=cid, owner_user_id=actor,
                    )
                    through = watermark if requested_through is None else requested_through
                    if not after <= through <= watermark:
                        raise ValueError("The history page range is invalid.")
                    messages = self._conversations.list_messages(
                        connection, conversation_id=cid, owner_user_id=actor,
                        after_sequence_no=after, through_sequence_no=through, limit=limit,
                    )
                except ConversationScopeError as exc:
                    raise ConversationServiceScopeError(str(exc)) from exc
                last = messages[-1].sequence_no if messages else after
                has_more = last < through
                return {"schema_version": 1, "conversation_id": cid, "items": messages,
                        "through_sequence_no": through, "has_more": has_more,
                        "next_after_sequence_no": last if has_more else None}

    def read_turn_answer(
        self, *, actor_user_id: str, actor_scope_epoch: int,
        conversation_id: str, operation_id: str,
    ) -> tuple[ConversationTurnRecord, ConversationMessageRecord]:
        actor = _user(actor_user_id)
        epoch = _positive(actor_scope_epoch, "actor_scope_epoch")
        cid = _uuid(conversation_id, "conversation_id")
        operation = _uuid(operation_id, "operation_id")
        with closing(self._factory.open(read_only=True)) as connection:
            with transaction(connection, TransactionMode.DEFERRED):
                self._active_actor(connection, actor, epoch)
                try:
                    self._conversations.require_history_readable(connection, conversation_id=cid, owner_user_id=actor)
                except ConversationScopeError as exc:
                    raise ConversationServiceScopeError(str(exc)) from exc
                turn = self._conversations.get_turn_by_operation(connection, conversation_id=cid, operation_id=operation)
                if turn is None or turn.actor_user_id != actor:
                    raise ConversationNotFoundError("The requested owner-bound turn does not exist.")
                if turn.state != "completed" or turn.assistant_message_id is None:
                    raise ConversationStateConflictError("The requested turn has no completed answer.")
                message = self._conversations.get_message(connection, turn.assistant_message_id)
                if message is None or message.conversation_id != cid or message.role != "assistant":
                    raise ConversationIdempotencyError("The completed answer binding is invalid.")
                return turn, message

    def list_pending_turns(
        self, *, actor_user_id: str, actor_scope_epoch: int,
        conversation_id: str, after_sequence_no: int = 0, limit: int = 20,
    ) -> dict[str, object]:
        """Discovery only: POST still revalidates authority, binding and ownership.

        The server supplies the original operation and exact accepted text, not
        a text-similarity guess. A read never executes or changes a pending turn.
        """
        actor = _user(actor_user_id)
        epoch = _positive(actor_scope_epoch, "actor_scope_epoch")
        cid = _uuid(conversation_id, "conversation_id")
        after = _nonnegative(after_sequence_no, "after_sequence_no")
        limit = _positive(limit, "limit")
        if limit > 100:
            raise ValueError("limit must not exceed 100.")
        with closing(self._factory.open(read_only=True)) as connection:
            with transaction(connection, TransactionMode.DEFERRED):
                self._active_actor(connection, actor, epoch)
                try:
                    conversation = self._conversations.require_history_readable(
                        connection, conversation_id=cid, owner_user_id=actor,
                    )
                    turns = self._conversations.list_pending_turns(
                        connection, conversation_id=cid, owner_user_id=actor,
                        after_sequence_no=after, limit=limit,
                    )
                except ConversationScopeError as exc:
                    raise ConversationServiceScopeError(str(exc)) from exc
                items = []
                for turn in turns:
                    message = self._conversations.get_message(connection, turn.user_message_id)
                    if message is None or message.conversation_id != cid or message.role != "user":
                        raise ConversationIdempotencyError("The pending message binding is invalid.")
                    events = self._conversations.list_turn_events(
                        connection, turn_id=turn.id, conversation_id=cid, limit=1,
                    )
                    envelope = message.content
                    content = envelope.get("content")
                    if envelope.get("role") != "user" or not isinstance(content, dict):
                        raise ConversationIdempotencyError("The pending message envelope is invalid.")
                    text = content.get("text")
                    reason = None
                    if conversation.status != "active":
                        reason = "CONVERSATION_ARCHIVED"
                    elif not events or events[0].payload.get("execution_protocol") != TURN_EXECUTION_PROTOCOL:
                        reason = "LEGACY_EXECUTION_PROTOCOL"
                    elif set(content) != {"text"} or not isinstance(text, str) or not text:
                        reason = "UNSUPPORTED_MESSAGE_CONTENT"
                    elif turn.request_sha256 != _semantic_hash({
                        "actor_scope_epoch": epoch, "actor_user_id": actor,
                        "conversation_id": cid, "operation_type": "conversation_turn",
                        "schema_version": 1, "user_content": content,
                    }):
                        reason = "REQUEST_SCOPE_CHANGED"
                    else:
                        try:
                            self._require_mutation_access(connection, actor_user_id=actor, conversation_id=cid)
                            if self._conversation_wiki_binding:
                                self._validate_turn_binding(
                                    connection, actor_user_id=actor, actor_scope_epoch=epoch,
                                    conversation_id=cid, turn_id=turn.id,
                                )
                        except (ConversationServiceError, ConversationScopeError) as exc:
                            reason = getattr(exc, "code", "CONVERSATION_SCOPE_INVALID")
                    items.append({
                        "operation_id": turn.operation_id, "user_message_id": message.id,
                        "sequence_no": message.sequence_no, "state": turn.state,
                        "message": text if isinstance(text, str) else "",
                        "can_resume": reason is None, "reason": reason,
                    })
                next_after = items[-1]["sequence_no"] if items else after
                has_more = bool(items) and bool(self._conversations.list_pending_turns(
                    connection, conversation_id=cid, owner_user_id=actor,
                    after_sequence_no=next_after, limit=1,
                ))
                return {"schema_version": 1, "conversation_id": cid, "items": items,
                        "has_more": has_more, "next_after_sequence_no": next_after if has_more else None}

    def list_messages(
        self,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        conversation_id: str,
        after_sequence_no: int = 0,
        limit: int = 100,
    ) -> tuple[ConversationMessageRecord, ...]:
        actor_id = _user(actor_user_id)
        epoch = _positive(actor_scope_epoch, "actor_scope_epoch")
        connection = self._factory.open()
        try:
            self._active_actor(connection, actor_id, epoch)
            try:
                return self._conversations.list_messages(
                    connection,
                    conversation_id=conversation_id,
                    owner_user_id=actor_id,
                    after_sequence_no=after_sequence_no,
                    limit=limit,
                )
            except ConversationScopeError as exc:
                raise ConversationServiceScopeError(str(exc)) from exc
        finally:
            connection.close()

    def _required_turn_created_at(self, connection, turn_id: str) -> int:
        turn = self._conversations.get_turn(connection, turn_id)
        if turn is None:
            raise ConversationIdempotencyError(
                "The in-progress turn checkpoint does not exist."
            )
        return turn.created_at_ms

    def _required_turn_request_id(self, connection, turn_id: str) -> str:
        events = self._conversations.list_turn_events(
            connection,
            turn_id=turn_id,
            conversation_id=self._required_turn_conversation_id(connection, turn_id),
            limit=1,
        )
        request_id = events[0].payload.get("request_id") if events else None
        try:
            return _uuid(request_id, "request_id")
        except ValueError as exc:
            raise ConversationIdempotencyError(
                "The accepted turn request binding is invalid."
            ) from exc

    def _required_turn_conversation_id(self, connection, turn_id: str) -> str:
        turn = self._conversations.get_turn(connection, turn_id)
        if turn is None:
            raise ConversationIdempotencyError(
                "The accepted turn checkpoint does not exist."
            )
        return turn.conversation_id

    def _turn_result(
        self,
        connection,
        turn: ConversationTurnRecord,
        *,
        request_id: str,
    ) -> ConversationTurnResult:
        events = self._conversations.list_turn_events(
            connection,
            turn_id=turn.id,
            conversation_id=turn.conversation_id,
        )
        if not events:
            raise ConversationIdempotencyError(
                "The turn has no reconnect event checkpoint."
            )
        return ConversationTurnResult(
            request_id=_uuid(request_id, "request_id"),
            operation_id=turn.operation_id,
            turn_id=turn.id,
            conversation_id=turn.conversation_id,
            state=turn.state,
            user_message_id=turn.user_message_id,
            assistant_message_id=turn.assistant_message_id,
            final_response=turn.final_response,
            error=turn.error,
            event_cursor=events[-1].sequence_no,
            row_version=turn.row_version,
        )

    def _append_turn_events(
        self,
        connection,
        *,
        result: ConversationTurnResult,
        actor: UserRecord,
        action: str,
        event_type: str,
        request_meta: Mapping[str, object],
        now_ms: int,
    ) -> None:
        audit_uuid = _derived(
            _AUDIT_NAMESPACE,
            actor.id,
            action,
            result.operation_id,
        )
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=audit_uuid,
                occurred_at_ms=now_ms,
                request_id=result.request_id,
                operation_id=result.operation_id,
                action=action,
                outcome=result.state,
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=self._conversations.require_owned(
                    connection,
                    conversation_id=result.conversation_id,
                    owner_user_id=actor.id,
                ).space_id,
                details={
                    "conversation_id": result.conversation_id,
                    "event_cursor": result.event_cursor,
                    "row_version": result.row_version,
                    "schema_version": 1,
                    "turn_id": result.turn_id,
                },
                request_meta=request_meta,
            ),
        )
        conversation = self._conversations.require_owned(
            connection,
            conversation_id=result.conversation_id,
            owner_user_id=actor.id,
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=(
                    f"conversation-turn:{result.turn_id}:{result.state}:"
                    f"{result.row_version}"
                ),
                space_id=conversation.space_id,
                aggregate_type="conversation_turn",
                aggregate_id=result.turn_id,
                aggregate_version=result.row_version,
                event_type=event_type,
                payload={
                    "conversation_id": result.conversation_id,
                    "event_cursor": result.event_cursor,
                    "schema_version": 1,
                    "state": result.state,
                    "turn_id": result.turn_id,
                },
                available_at_ms=now_ms,
            ),
        )

    def _append_summary_events(
        self,
        connection,
        *,
        result: ConversationCompactResult,
        actor: UserRecord,
        invalidated_summary_id: str | None,
        request_meta: Mapping[str, object],
        now_ms: int,
    ) -> None:
        summary = result.summary
        audit_uuid = _derived(
            _AUDIT_NAMESPACE,
            actor.id,
            "conversation.compact",
            result.operation_id,
        )
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=audit_uuid,
                occurred_at_ms=now_ms,
                request_id=result.request_id,
                operation_id=result.operation_id,
                action="conversation.compact",
                outcome=result.outcome,
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=result.space_id,
                details={
                    "conversation_id": summary.conversation_id,
                    "covered_through_sequence_no": (
                        summary.covered_through_sequence_no
                    ),
                    "invalidated_summary_id": invalidated_summary_id,
                    "schema_version": 1,
                    "summary_id": summary.id,
                    "summary_sha256": summary.summary_sha256,
                },
                request_meta=request_meta,
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=(
                    f"conversation-summary:{summary.id}:"
                    f"{summary.row_version}:{result.operation_id}"
                ),
                space_id=result.space_id,
                aggregate_type="conversation_summary",
                aggregate_id=summary.id,
                aggregate_version=summary.row_version,
                event_type="conversation.summary.ready",
                payload={
                    "conversation_id": summary.conversation_id,
                    "covered_through_sequence_no": (
                        summary.covered_through_sequence_no
                    ),
                    "schema_version": 1,
                    "summary_id": summary.id,
                    "summary_sha256": summary.summary_sha256,
                },
                available_at_ms=now_ms,
            ),
        )

    def _preflight_attachment_replay(
        self,
        *,
        actor_id: str,
        actor_scope_epoch: int,
        conversation_id: str,
        operation_id: str,
        operation_type: str,
        request_hash: str,
        parser: Callable[[Mapping[str, object]], _AttachmentResult],
        replay_validator: Callable[[object, _AttachmentResult], None],
    ) -> _AttachmentResult | None:
        dedup_id = _derived(_DEDUP_NAMESPACE, actor_id, operation_type, operation_id)
        connection = self._factory.open()
        try:
            self._active_actor(connection, actor_id, actor_scope_epoch)
            operation = self._users.get_operation(connection, dedup_id)
            if operation is None:
                return None
            if (
                operation.actor_user_id != actor_id
                or operation.operation_type != operation_type
                or operation.operation_id != operation_id
                or operation.request_sha256 != request_hash
            ):
                raise ConversationIdempotencyError(
                    "The attachment operation is bound incorrectly."
                )
            if operation.status == "in_progress":
                raise ConversationOperationInProgressError(
                    "The attachment operation is already in progress."
                )
            if operation.status != "committed" or not isinstance(
                operation.result, Mapping
            ):
                raise ConversationIdempotencyError(
                    "The attachment operation is terminal without a result."
                )
            try:
                result = parser(operation.result)
                replay_validator(connection, result)
            except (TypeError, ValueError) as exc:
                raise ConversationIdempotencyError(
                    "The committed attachment result is invalid."
                ) from exc
            if result.operation_id != operation_id:
                raise ConversationIdempotencyError(
                    "The committed attachment result has the wrong operation."
                )
            return result
        except ConversationScopeError as exc:
            raise ConversationServiceScopeError(str(exc)) from exc
        finally:
            connection.close()

    def _resolve_context(
        self,
        *,
        actor_id: str,
        actor_scope_epoch: int,
        conversation_id: str,
        binding_type: ConversationContextBindingType,
        binding_mode: ConversationContextBindingMode,
        external_key: str,
    ) -> ConversationContextResolution:
        connection = self._factory.open()
        try:
            self._active_actor(connection, actor_id, actor_scope_epoch)
            try:
                conversation = self._require_mutation_access(
                    connection,
                    conversation_id=conversation_id,
                    actor_user_id=actor_id,
                )
            except ConversationScopeError as exc:
                raise ConversationServiceScopeError(str(exc)) from exc
            if conversation.status != "active":
                raise ConversationServiceScopeError(
                    "Only an active conversation can receive context."
                )
            space_id = conversation.space_id
        finally:
            connection.close()
        resolver = self._context_resolver
        if resolver is None:
            raise ConversationContextResolverUnavailableError(
                "An explicit context resolver is not configured."
            )
        request = ConversationContextResolveRequest(
            actor_user_id=actor_id,
            actor_scope_epoch=actor_scope_epoch,
            conversation_id=conversation_id,
            space_id=space_id,
            binding_type=binding_type,
            binding_mode=binding_mode,
            external_key=external_key,
        )
        try:
            resolution = resolver.resolve(request)
        except ConversationServiceError:
            raise
        except Exception as exc:
            raise ConversationContextResolverUnavailableError(
                "The explicit context could not be resolved."
            ) from exc
        if not isinstance(resolution, ConversationContextResolution):
            raise ConversationServiceScopeError(
                "The explicit context resolver returned invalid evidence."
            )
        resolved_space = _uuid(resolution.space_id, "resolved_space_id")
        resolved_key = _bounded_text(
            resolution.external_key, "resolved_external_key", 1000
        )
        display_name = _bounded_text(
            resolution.display_name, "resolved_display_name", 200
        )
        if (
            resolved_space != space_id
            or resolution.binding_type != binding_type
            or resolution.binding_mode != binding_mode
            or resolved_key != external_key
        ):
            raise ConversationServiceScopeError(
                "The explicit context resolution changed scope or identity."
            )
        artifact_id = resolution.source_artifact_id
        snapshot_sha = resolution.snapshot_sha256
        if binding_mode == "snapshot":
            artifact_id = _uuid(artifact_id, "source_artifact_id")
            snapshot_sha = _sha(snapshot_sha, "snapshot_sha256")
        elif artifact_id is not None or snapshot_sha is not None:
            raise ConversationServiceScopeError(
                "A live context resolution cannot carry snapshot evidence."
            )
        return ConversationContextResolution(
            space_id=resolved_space,
            binding_type=binding_type,
            binding_mode=binding_mode,
            external_key=resolved_key,
            source_artifact_id=artifact_id,
            snapshot_sha256=snapshot_sha,
            display_name=display_name,
        )

    def _run_attachment_operation(
        self,
        *,
        actor_id: str,
        actor_scope_epoch: int,
        conversation_id: str,
        operation_id: str,
        request_id: str,
        operation_type: str,
        request_hash: str,
        parser: Callable[[Mapping[str, object]], _AttachmentResult],
        replay_validator: Callable[[object, _AttachmentResult], None],
        apply: Callable[[object, UserRecord, int], _AttachmentResult],
    ) -> _AttachmentResult:
        dedup_id = _derived(_DEDUP_NAMESPACE, actor_id, operation_type, operation_id)
        connection = self._factory.open()
        result: _AttachmentResult | None = None
        try:
            with transaction(connection):
                actor = self._active_actor(
                    connection, actor_id, actor_scope_epoch
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
                    raise ConversationIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored = begun.operation.result
                        if not isinstance(stored, Mapping):
                            raise ConversationIdempotencyError(
                                "The committed attachment operation has no result."
                            )
                        try:
                            result = parser(stored)
                            replay_validator(connection, result)
                        except (TypeError, ValueError) as exc:
                            raise ConversationIdempotencyError(
                                "The committed attachment result is invalid."
                            ) from exc
                        if result.operation_id != operation_id:
                            raise ConversationIdempotencyError(
                                "The committed attachment result has the wrong operation."
                            )
                    elif begun.operation.status == "in_progress":
                        raise ConversationOperationInProgressError(
                            "The attachment operation is already in progress."
                        )
                    else:
                        raise ConversationIdempotencyError(
                            "The attachment operation is terminal without a result."
                        )
                else:
                    result = apply(connection, actor, now_ms)
                    self._users.commit_operation(
                        connection,
                        dedup_id=dedup_id,
                        result=result.as_dict(),
                        http_status=200,
                        completed_at_ms=now_ms,
                    )
        except ConversationScopeError as exc:
            raise ConversationServiceScopeError(str(exc)) from exc
        except ConversationNotFoundError as exc:
            raise ConversationServiceScopeError(str(exc)) from exc
        except ConversationVersionConflictError as exc:
            raise ConversationServiceVersionError(str(exc)) from exc
        except ConversationStateConflictError as exc:
            raise ConversationIdempotencyError(str(exc)) from exc
        except OperationInProgressError as exc:
            raise ConversationOperationInProgressError(str(exc)) from exc
        finally:
            connection.close()
        if result is None:
            raise ConversationServiceError("The attachment result is unavailable.")
        return result

    def _validate_pin_result(
        self,
        connection,
        *,
        actor_id: str,
        conversation_id: str,
        item_id: str,
        mutation: Literal["add", "remove"],
        result: ConversationPinMutationResult,
    ) -> None:
        if not isinstance(result, ConversationPinMutationResult) or (
            result.mutation != mutation
            or result.conversation_id != conversation_id
            or result.item_id != item_id
        ):
            raise ConversationIdempotencyError(
                "The committed pin result is bound incorrectly."
            )
        conversation = self._conversations.require_history_readable(
            connection,
            conversation_id=conversation_id,
            owner_user_id=actor_id,
        )
        if result.space_id != conversation.space_id or (
            result.pin is not None and result.pin.created_by != actor_id
        ):
            raise ConversationIdempotencyError(
                "The committed pin result has invalid scope evidence."
            )

    def _validate_context_result(
        self,
        connection,
        *,
        actor_id: str,
        conversation_id: str,
        mutation: Literal["add", "remove"],
        result: ConversationContextMutationResult,
    ) -> None:
        if not isinstance(result, ConversationContextMutationResult) or (
            result.mutation != mutation
            or result.binding.conversation_id != conversation_id
            or result.binding.created_by != actor_id
        ):
            raise ConversationIdempotencyError(
                "The committed context result is bound incorrectly."
            )
        conversation = self._conversations.require_history_readable(
            connection,
            conversation_id=conversation_id,
            owner_user_id=actor_id,
        )
        if result.space_id != conversation.space_id:
            raise ConversationIdempotencyError(
                "The committed context result has invalid space evidence."
            )
        current = self._conversations.get_context_binding(
            connection, result.binding.id
        )
        if current is None or (
            current.conversation_id != result.binding.conversation_id
            or current.binding_type != result.binding.binding_type
            or current.binding_mode != result.binding.binding_mode
            or current.external_key != result.binding.external_key
            or current.source_artifact_id != result.binding.source_artifact_id
            or current.snapshot_sha256 != result.binding.snapshot_sha256
            or current.display_metadata_json != result.binding.display_metadata_json
            or current.created_by != result.binding.created_by
            or current.created_at_ms != result.binding.created_at_ms
        ):
            raise ConversationIdempotencyError(
                "The committed context identity no longer matches storage."
            )
        if mutation == "remove" and current != result.binding:
            raise ConversationIdempotencyError(
                "The committed detached context differs from storage."
            )

    def _add_pin_effect(
        self,
        connection,
        *,
        actor: UserRecord,
        operation_id: str,
        request_id: str,
        conversation_id: str,
        item_id: str,
        revision_id: str,
        now_ms: int,
        request_meta: Mapping[str, object],
    ) -> ConversationPinMutationResult:
        self._require_mutation_access(
            connection,
            actor_user_id=actor.id,
            conversation_id=conversation_id,
        )
        created = self._conversations.put_pin(
            connection,
            ConversationPinCreate(
                conversation_id=conversation_id,
                owner_user_id=actor.id,
                item_id=item_id,
                revision_id=revision_id,
                created_at_ms=now_ms,
            ),
        )
        result = ConversationPinMutationResult(
            request_id=request_id,
            operation_id=operation_id,
            mutation="add",
            outcome="added" if created.inserted else "no_change",
            conversation_id=conversation_id,
            space_id=created.pin.space_id,
            item_id=item_id,
            pin=created.pin,
        )
        self._append_pin_events(
            connection,
            actor=actor,
            result=result,
            request_meta=request_meta,
            now_ms=now_ms,
        )
        return result

    def _remove_pin_effect(
        self,
        connection,
        *,
        actor: UserRecord,
        operation_id: str,
        request_id: str,
        conversation_id: str,
        item_id: str,
        now_ms: int,
        request_meta: Mapping[str, object],
    ) -> ConversationPinMutationResult:
        conversation = self._require_mutation_access(
            connection,
            conversation_id=conversation_id,
            actor_user_id=actor.id,
        )
        removed = self._conversations.remove_pin(
            connection,
            conversation_id=conversation_id,
            owner_user_id=actor.id,
            item_id=item_id,
        )
        result = ConversationPinMutationResult(
            request_id=request_id,
            operation_id=operation_id,
            mutation="remove",
            outcome="removed" if removed.removed else "no_change",
            conversation_id=conversation_id,
            space_id=conversation.space_id,
            item_id=item_id,
            pin=removed.pin,
        )
        self._append_pin_events(
            connection,
            actor=actor,
            result=result,
            request_meta=request_meta,
            now_ms=now_ms,
        )
        return result

    def _add_context_effect(
        self,
        connection,
        *,
        actor: UserRecord,
        operation_id: str,
        request_id: str,
        conversation_id: str,
        resolution: ConversationContextResolution,
        now_ms: int,
        request_meta: Mapping[str, object],
    ) -> ConversationContextMutationResult:
        conversation = self._require_mutation_access(
            connection,
            conversation_id=conversation_id,
            actor_user_id=actor.id,
        )
        if conversation.space_id != resolution.space_id:
            raise ConversationScopeError(
                "The resolved context no longer belongs to the conversation space."
            )
        created = self._conversations.put_context_binding(
            connection,
            ConversationContextBindingCreate(
                binding_id=conversation_context_binding_id(
                    conversation_id, operation_id
                ),
                conversation_id=conversation_id,
                owner_user_id=actor.id,
                binding_type=resolution.binding_type,
                binding_mode=resolution.binding_mode,
                external_key=resolution.external_key,
                source_artifact_id=resolution.source_artifact_id,
                snapshot_sha256=resolution.snapshot_sha256,
                display_metadata_json=_canonical_json(
                    {
                        "display_name": resolution.display_name,
                        "schema_version": 1,
                    }
                ),
                created_at_ms=now_ms,
            ),
        )
        result = ConversationContextMutationResult(
            request_id=request_id,
            operation_id=operation_id,
            mutation="add",
            outcome="added" if created.inserted else "no_change",
            space_id=conversation.space_id,
            binding=created.binding,
        )
        self._append_context_events(
            connection,
            actor=actor,
            result=result,
            request_meta=request_meta,
            now_ms=now_ms,
        )
        return result

    def _remove_context_effect(
        self,
        connection,
        *,
        actor: UserRecord,
        operation_id: str,
        request_id: str,
        conversation_id: str,
        binding_id: str,
        now_ms: int,
        request_meta: Mapping[str, object],
    ) -> ConversationContextMutationResult:
        conversation = self._require_mutation_access(
            connection,
            conversation_id=conversation_id,
            actor_user_id=actor.id,
        )
        detached = self._conversations.detach_context_binding(
            connection,
            binding_id=binding_id,
            conversation_id=conversation_id,
            owner_user_id=actor.id,
            detached_at_ms=now_ms,
        )
        result = ConversationContextMutationResult(
            request_id=request_id,
            operation_id=operation_id,
            mutation="remove",
            outcome="detached" if detached.detached else "no_change",
            space_id=conversation.space_id,
            binding=detached.binding,
        )
        self._append_context_events(
            connection,
            actor=actor,
            result=result,
            request_meta=request_meta,
            now_ms=now_ms,
        )
        return result

    def _append_pin_events(
        self,
        connection,
        *,
        actor: UserRecord,
        result: ConversationPinMutationResult,
        request_meta: Mapping[str, object],
        now_ms: int,
    ) -> None:
        action = f"conversation.pin.{result.mutation}"
        pin_id = conversation_pin_aggregate_id(
            result.conversation_id, result.item_id
        )
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived(
                    _AUDIT_NAMESPACE, actor.id, action, result.operation_id
                ),
                occurred_at_ms=now_ms,
                request_id=result.request_id,
                operation_id=result.operation_id,
                action=action,
                outcome=result.outcome,
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=result.space_id,
                item_id=result.item_id,
                base_revision_id=(
                    None if result.pin is None else result.pin.revision_id
                ),
                details={
                    "conversation_id": result.conversation_id,
                    "pin_id": pin_id,
                    "schema_version": 1,
                },
                request_meta=request_meta,
            ),
        )
        if result.outcome != "no_change":
            self._outbox.append(
                connection,
                OutboxEventDraft(
                    logical_key=f"conversation-pin:{pin_id}:{result.operation_id}",
                    space_id=result.space_id,
                    aggregate_type="conversation_pin",
                    aggregate_id=pin_id,
                    aggregate_version=now_ms,
                    event_type=(
                        "conversation.pin.added"
                        if result.mutation == "add"
                        else "conversation.pin.removed"
                    ),
                    payload={
                        "conversation_id": result.conversation_id,
                        "item_id": result.item_id,
                        "pin_id": pin_id,
                        "schema_version": 1,
                    },
                    available_at_ms=now_ms,
                ),
            )

    def _append_context_events(
        self,
        connection,
        *,
        actor: UserRecord,
        result: ConversationContextMutationResult,
        request_meta: Mapping[str, object],
        now_ms: int,
    ) -> None:
        action = f"conversation.context.{result.mutation}"
        binding = result.binding
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived(
                    _AUDIT_NAMESPACE, actor.id, action, result.operation_id
                ),
                occurred_at_ms=now_ms,
                request_id=result.request_id,
                operation_id=result.operation_id,
                action=action,
                outcome=result.outcome,
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=result.space_id,
                details={
                    "binding_id": binding.id,
                    "binding_mode": binding.binding_mode,
                    "binding_type": binding.binding_type,
                    "conversation_id": binding.conversation_id,
                    "row_version": binding.row_version,
                    "schema_version": 1,
                    "status": binding.status,
                },
                request_meta=request_meta,
            ),
        )
        if result.outcome != "no_change":
            self._outbox.append(
                connection,
                OutboxEventDraft(
                    logical_key=(
                        f"conversation-context:{binding.id}:"
                        f"{binding.row_version}:{result.operation_id}"
                    ),
                    space_id=result.space_id,
                    aggregate_type="conversation_context_binding",
                    aggregate_id=binding.id,
                    aggregate_version=binding.row_version,
                    event_type=(
                        "conversation.context.added"
                        if result.mutation == "add"
                        else "conversation.context.detached"
                    ),
                    payload={
                        "binding_id": binding.id,
                        "binding_mode": binding.binding_mode,
                        "binding_type": binding.binding_type,
                        "conversation_id": binding.conversation_id,
                        "row_version": binding.row_version,
                        "schema_version": 1,
                        "status": binding.status,
                    },
                    available_at_ms=now_ms,
                ),
            )

    def _mutate(
        self,
        *,
        actor_id: str,
        actor_scope_epoch: int,
        expected_conversation_id: str,
        operation_id: str,
        request_id: str,
        operation_type: str,
        request_hash: str,
        request_meta: Mapping[str, object],
        apply,
    ) -> ConversationMutationResult:
        dedup_id = _derived(_DEDUP_NAMESPACE, actor_id, operation_type, operation_id)
        connection = self._factory.open()
        result: ConversationMutationResult | None = None
        try:
            with transaction(connection):
                actor = self._active_actor(connection, actor_id, actor_scope_epoch)
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
                    raise ConversationIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored = begun.operation.result
                        if not isinstance(stored, Mapping):
                            raise ConversationIdempotencyError(
                                "The committed conversation operation has no result."
                            )
                        try:
                            result = ConversationMutationResult.from_mapping(stored)
                        except (TypeError, ValueError) as exc:
                            raise ConversationIdempotencyError(
                                "The committed conversation result is invalid."
                            ) from exc
                        if (
                            result.operation_id != operation_id
                            or result.conversation.id != expected_conversation_id
                            or result.conversation.owner_user_id != actor.id
                        ):
                            raise ConversationIdempotencyError(
                                "The committed conversation result is bound incorrectly."
                            )
                        if self._conversation_wiki_binding:
                            self._conversations.require_history_readable(
                                connection,
                                conversation_id=expected_conversation_id,
                                owner_user_id=actor.id,
                            )
                        else:
                            self._conversations.require_accessible(
                                connection,
                                conversation_id=expected_conversation_id,
                                owner_user_id=actor.id,
                            )
                    elif begun.operation.status == "in_progress":
                        raise ConversationOperationInProgressError(
                            "The conversation operation is already in progress."
                        )
                    else:
                        raise ConversationIdempotencyError(
                            "The conversation operation is terminal without a result."
                        )
                else:
                    try:
                        result = apply(connection, now_ms)
                    except ConversationScopeError as exc:
                        raise ConversationServiceScopeError(str(exc)) from exc
                    except ConversationVersionConflictError as exc:
                        raise ConversationServiceVersionError(str(exc)) from exc
                    except ConversationStateConflictError as exc:
                        raise ConversationIdempotencyError(str(exc)) from exc
                    self._users.commit_operation(
                        connection,
                        dedup_id=dedup_id,
                        result=result.as_dict(),
                        http_status=200,
                        completed_at_ms=now_ms,
                    )
        except ConversationScopeError as exc:
            raise ConversationServiceScopeError(str(exc)) from exc
        except OperationInProgressError as exc:
            raise ConversationOperationInProgressError(str(exc)) from exc
        finally:
            connection.close()
        if result is None:
            raise ConversationServiceError("The conversation result is unavailable.")
        return result

    def _create_effect(
        self,
        connection,
        *,
        actor_id: str,
        epoch: int,
        conversation_id: str,
        space_id: str,
        title: str,
        metadata: Mapping[str, object],
        now_ms: int,
        request_id: str,
        operation_id: str,
        request_meta: Mapping[str, object],
    ) -> ConversationMutationResult:
        if self._conversation_wiki_binding:
            self._binding_fence(
                connection,
                actor_user_id=actor_id,
                actor_scope_epoch=epoch,
                space_id=space_id,
            )
        created = self._conversations.create(
            connection,
            ConversationCreate(
                conversation_id=conversation_id,
                owner_user_id=actor_id,
                space_id=space_id,
                captured_scope_epoch=epoch,
                title=title,
                metadata_json=_canonical_json(metadata),
                created_at_ms=now_ms,
            ),
        )
        result = ConversationMutationResult(
            request_id=request_id,
            operation_id=operation_id,
            outcome="created" if created.inserted else "no_change",
            conversation=created.conversation,
        )
        self._append_events(
            connection,
            result=result,
            action="conversation.create",
            event_type="conversation.created",
            request_meta=request_meta,
            now_ms=now_ms,
        )
        return result

    def _update_effect(
        self,
        connection,
        *,
        actor_id: str,
        operation_id: str,
        request_id: str,
        conversation_id: str,
        expected_version: int,
        title: str | None,
        status: Literal["active", "archived"] | None,
        now_ms: int,
        request_meta: Mapping[str, object],
    ) -> ConversationMutationResult:
        self._require_mutation_access(
            connection,
            actor_user_id=actor_id,
            conversation_id=conversation_id,
        )
        updated = self._conversations.update_lifecycle(
            connection,
            conversation_id=conversation_id,
            owner_user_id=actor_id,
            expected_row_version=expected_version,
            updated_at_ms=now_ms,
            title=title,
            status=status,
        )
        result = ConversationMutationResult(
            request_id=request_id,
            operation_id=operation_id,
            outcome="updated" if updated.changed else "no_change",
            conversation=updated.conversation,
        )
        self._append_events(
            connection,
            result=result,
            action="conversation.update",
            event_type="conversation.updated",
            request_meta=request_meta,
            now_ms=now_ms,
        )
        return result

    def _append_events(
        self,
        connection,
        *,
        result: ConversationMutationResult,
        action: str,
        event_type: str,
        request_meta: Mapping[str, object],
        now_ms: int,
    ) -> None:
        actor = self._users.require(connection, result.conversation.owner_user_id)
        audit_uuid = _derived(
            _AUDIT_NAMESPACE,
            result.conversation.owner_user_id,
            action,
            result.operation_id,
        )
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=audit_uuid,
                occurred_at_ms=now_ms,
                request_id=result.request_id,
                operation_id=result.operation_id,
                action=action,
                outcome=result.outcome,
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=result.conversation.space_id,
                details={
                    "conversation_id": result.conversation.id,
                    "row_version": result.conversation.row_version,
                    "schema_version": 1,
                    "status": result.conversation.status,
                },
                request_meta=request_meta,
            ),
        )
        if result.outcome != "no_change":
            self._outbox.append(
                connection,
                OutboxEventDraft(
                    logical_key=(
                        f"conversation:{result.conversation.id}:"
                        f"{result.conversation.row_version}:{result.operation_id}"
                    ),
                    space_id=result.conversation.space_id,
                    aggregate_type="conversation",
                    aggregate_id=result.conversation.id,
                    aggregate_version=result.conversation.row_version,
                    event_type=event_type,
                    payload={
                        "conversation_id": result.conversation.id,
                        "owner_user_id": result.conversation.owner_user_id,
                        "row_version": result.conversation.row_version,
                        "schema_version": 1,
                        "space_id": result.conversation.space_id,
                        "status": result.conversation.status,
                    },
                    available_at_ms=now_ms,
                ),
            )


__all__ = [
    "AcceptConversationTurnCommand",
    "AddConversationContextCommand",
    "AddConversationPinCommand",
    "CompactConversationCommand",
    "CompleteConversationTurnCommand",
    "ConversationAuthorizationError",
    "ConversationContextMutationResult",
    "ConversationContextResolution",
    "ConversationContextResolveRequest",
    "ConversationContextResolver",
    "ConversationContextResolverUnavailableError",
    "ConversationCorpusClosureStaleError",
    "ConversationIdempotencyError",
    "ConversationCompactResult",
    "ConversationMutationResult",
    "ConversationOperationInProgressError",
    "ConversationPinMutationResult",
    "ConversationService",
    "ConversationServiceError",
    "ConversationServiceScopeError",
    "ConversationServiceVersionError",
    "ConversationSessionScopeStaleError",
    "ConversationTurnResult",
    "ConversationWikiArchivedHistoryOnlyError",
    "CreateConversationCommand",
    "RemoveConversationContextCommand",
    "RemoveConversationPinCommand",
    "UpdateConversationCommand",
    "TerminateConversationTurnCommand",
    "conversation_id_for_create",
    "conversation_context_binding_id",
    "conversation_pin_aggregate_id",
    "conversation_summary_id",
    "conversation_turn_assistant_message_id",
    "conversation_turn_id",
    "conversation_turn_user_message_id",
]

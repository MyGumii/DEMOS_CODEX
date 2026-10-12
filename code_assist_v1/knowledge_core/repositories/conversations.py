"""Canonical conversation persistence plus narrow P2.5 legacy primitives.

The legacy methods accept only material already decoded and verified by the
offline P2.5 adapter.  They perform no filesystem, Notion, or network I/O and
never own a transaction.  Each method preflights every relevant identity and
then inserts one canonical row or verifies an exact replay in the caller-owned
transaction.

The P3 runtime primitives create and read owner-bound conversations, expose a
stable accessibility decision for immutable space badges, update lifecycle
through optimistic row-version checks, persist same-space fixed-revision pins,
and persist explicitly resolved context bindings.  They never infer a new
space for an existing conversation.  Imported conversations are always
archived historical records.  Legacy messages remain immutable canonical
messages, summaries are labelled as legacy imports, and only a reviewed Notion
relationship may create a legacy live ``notion_page`` binding.
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


LegacyMessageRole = Literal["system", "user", "assistant", "tool"]
ConversationStatus = Literal["active", "archived"]
ConversationAccessStatus = Literal[
    "accessible",
    "wiki_archived",
    "owner_disabled",
    "personal_owner_mismatch",
    "space_inactive",
    "stale_part_scope",
    "unsupported_space_kind",
]
ConversationTurnState = Literal[
    "accepted", "running", "completed", "failed", "cancelled"
]
ConversationSummaryStatus = Literal["active", "invalidated"]
ConversationContextBindingType = Literal[
    "workspace_file", "project_context", "notion_page", "source_artifact"
]
ConversationContextBindingMode = Literal["snapshot", "live"]
ConversationContextBindingStatus = Literal["active", "unavailable", "detached"]

_CONVERSATION_NAMESPACE = UUID("728deee3-e1b1-48f3-95f7-05b977d90a40")
_MESSAGE_NAMESPACE = UUID("dbfd390e-2902-466a-8731-b2d7b21bc474")
_SUMMARY_NAMESPACE = UUID("b07c3c7c-a98d-49ed-9968-457498464e85")
_CONTEXT_BINDING_NAMESPACE = UUID("a048e22e-d527-44f4-8ed4-481061b0c40c")

_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_RECORD_RE = re.compile(r"^rec_[0-9a-f]{24}$", re.ASCII)
_RELATIONSHIP_RE = re.compile(r"^rel_[0-9a-f]{24}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,79}$", re.ASCII)
_SQLITE_INTEGER_MAX = 9_223_372_036_854_775_807

_CONVERSATION_COLUMNS = (
    "id", "owner_user_id", "space_id", "captured_scope_epoch", "title",
    "status", "metadata_json", "created_at", "updated_at", "row_version",
)
_MESSAGE_COLUMNS = (
    "id", "conversation_id", "sequence_no", "role", "content_json",
    "created_at",
)
_SUMMARY_COLUMNS = (
    "id", "conversation_id", "covered_through_sequence_no", "summary_md",
    "summary_sha256", "model_id", "prompt_version", "summarizer_version",
    "status", "created_by_user_id", "created_at", "invalidated_at",
    "invalidation_reason", "row_version",
)
_BINDING_COLUMNS = (
    "id", "conversation_id", "binding_type", "binding_mode", "external_key",
    "source_artifact_id", "snapshot_sha256", "display_metadata_json", "status",
    "status_reason", "created_by", "created_at", "status_changed_at",
    "detached_at", "row_version",
)
_TURN_COLUMNS = (
    "id", "conversation_id", "actor_user_id", "dedup_id", "operation_id",
    "request_sha256", "state", "user_message_id", "assistant_message_id",
    "final_response_json", "error_json", "created_at", "updated_at",
    "completed_at", "row_version",
)
_EVENT_COLUMNS = (
    "turn_id", "conversation_id", "sequence_no", "event_type",
    "payload_json", "created_at",
)
_PIN_COLUMNS = (
    "conversation_id", "space_id", "item_id", "revision_id", "created_by",
    "created_at",
)


class LegacyConversationRepositoryError(KnowledgeCoreDatabaseError):
    code = "LEGACY_CONVERSATION_REPOSITORY_ERROR"


class LegacyConversationConflictError(LegacyConversationRepositoryError):
    code = "LEGACY_CONVERSATION_STATE_CONFLICT"


class ConversationRepositoryError(KnowledgeCoreDatabaseError):
    code = "CONVERSATION_REPOSITORY_ERROR"


class ConversationNotFoundError(ConversationRepositoryError):
    code = "CONVERSATION_NOT_FOUND"


class ConversationStateConflictError(ConversationRepositoryError):
    code = "CONVERSATION_STATE_CONFLICT"


class ConversationVersionConflictError(ConversationRepositoryError):
    code = "CONVERSATION_VERSION_CONFLICT"


class ConversationScopeError(ConversationRepositoryError):
    code = "CONVERSATION_SCOPE_INVALID"


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    id: str
    owner_user_id: str
    space_id: str
    captured_scope_epoch: int
    title: str
    status: ConversationStatus
    metadata_json: str
    created_at_ms: int
    updated_at_ms: int
    row_version: int

    @property
    def metadata(self) -> dict[str, object]:
        value = json.loads(self.metadata_json)
        if not isinstance(value, dict):
            raise ConversationRepositoryError(
                "Stored conversation metadata is not an object."
            )
        return value


@dataclass(frozen=True, slots=True)
class ConversationListEntry:
    conversation: ConversationRecord
    access_status: ConversationAccessStatus
    turn_count: int = 0
    completed_turn_count: int = 0
    latest_turn_state: ConversationTurnState | None = None
    first_user_message: str | None = None

    @property
    def accessible(self) -> bool:
        return self.access_status == "accessible"

    @property
    def history_only(self) -> bool:
        return self.access_status == "wiki_archived"


@dataclass(frozen=True, slots=True)
class ConversationCreate:
    conversation_id: str
    owner_user_id: str
    space_id: str
    captured_scope_epoch: int
    title: str
    metadata_json: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ConversationCreateResult:
    conversation: ConversationRecord
    inserted: bool


@dataclass(frozen=True, slots=True)
class ConversationUpdateResult:
    conversation: ConversationRecord
    changed: bool


@dataclass(frozen=True, slots=True)
class ConversationMessageRecord:
    id: str
    conversation_id: str
    sequence_no: int
    role: LegacyMessageRole
    content_json: str
    created_at_ms: int

    @property
    def content(self) -> dict[str, object]:
        value = json.loads(self.content_json)
        if not isinstance(value, dict):
            raise ConversationRepositoryError(
                "Stored conversation message content is not an object."
            )
        return value


@dataclass(frozen=True, slots=True)
class ConversationTurnRecord:
    id: str
    conversation_id: str
    actor_user_id: str
    dedup_id: str
    operation_id: str
    request_sha256: str
    state: ConversationTurnState
    user_message_id: str
    assistant_message_id: str | None
    final_response_json: str | None
    error_json: str | None
    created_at_ms: int
    updated_at_ms: int
    completed_at_ms: int | None
    row_version: int

    @property
    def final_response(self) -> dict[str, object] | None:
        if self.final_response_json is None:
            return None
        value = json.loads(self.final_response_json)
        if not isinstance(value, dict):
            raise ConversationRepositoryError(
                "Stored turn final response is not an object."
            )
        return value

    @property
    def error(self) -> dict[str, object] | None:
        if self.error_json is None:
            return None
        value = json.loads(self.error_json)
        if not isinstance(value, dict):
            raise ConversationRepositoryError(
                "Stored turn error is not an object."
            )
        return value


@dataclass(frozen=True, slots=True)
class ConversationTurnEventRecord:
    turn_id: str
    conversation_id: str
    sequence_no: int
    event_type: Literal[
        "accepted", "delta", "tool", "completed", "failed", "cancelled"
    ]
    payload_json: str
    created_at_ms: int

    @property
    def payload(self) -> dict[str, object]:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise ConversationRepositoryError("Stored turn event is not an object.")
        return value


@dataclass(frozen=True, slots=True)
class ConversationTurnAccept:
    turn_id: str
    conversation_id: str
    actor_user_id: str
    dedup_id: str
    operation_id: str
    request_sha256: str
    user_message_id: str
    user_content_json: str
    accepted_event_json: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ConversationTurnAcceptResult:
    turn: ConversationTurnRecord
    user_message: ConversationMessageRecord
    inserted: bool


@dataclass(frozen=True, slots=True)
class ConversationTurnCompletion:
    turn_id: str
    conversation_id: str
    actor_user_id: str
    assistant_message_id: str
    assistant_content_json: str
    final_response_json: str
    completed_event_json: str
    completed_at_ms: int


@dataclass(frozen=True, slots=True)
class ConversationTurnCompletionResult:
    turn: ConversationTurnRecord
    assistant_message: ConversationMessageRecord
    completed: bool


@dataclass(frozen=True, slots=True)
class ConversationTurnTermination:
    turn_id: str
    conversation_id: str
    actor_user_id: str
    terminal_state: Literal["failed", "cancelled"]
    error_json: str | None
    terminal_event_json: str
    completed_at_ms: int


@dataclass(frozen=True, slots=True)
class ConversationTurnTerminationResult:
    turn: ConversationTurnRecord
    terminated: bool


@dataclass(frozen=True, slots=True)
class ConversationSummaryRecord:
    id: str
    conversation_id: str
    covered_through_sequence_no: int
    summary_md: str
    summary_sha256: str
    model_id: str
    prompt_version: str
    summarizer_version: str
    status: ConversationSummaryStatus
    created_by_user_id: str | None
    created_at_ms: int
    invalidated_at_ms: int | None
    invalidation_reason: str | None
    row_version: int


@dataclass(frozen=True, slots=True)
class ConversationSummaryCreate:
    summary_id: str
    conversation_id: str
    owner_user_id: str
    covered_through_sequence_no: int
    summary_md: str
    summary_sha256: str
    model_id: str
    prompt_version: str
    summarizer_version: str
    created_by_user_id: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ConversationSummaryCreateResult:
    summary: ConversationSummaryRecord
    inserted: bool
    invalidated_summary_id: str | None


@dataclass(frozen=True, slots=True)
class ConversationPinRecord:
    conversation_id: str
    space_id: str
    item_id: str
    revision_id: str
    created_by: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ConversationPinCreate:
    conversation_id: str
    owner_user_id: str
    item_id: str
    revision_id: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ConversationPinCreateResult:
    pin: ConversationPinRecord
    inserted: bool


@dataclass(frozen=True, slots=True)
class ConversationPinRemoveResult:
    pin: ConversationPinRecord | None
    removed: bool


@dataclass(frozen=True, slots=True)
class ConversationContextBindingRecord:
    id: str
    conversation_id: str
    binding_type: ConversationContextBindingType
    binding_mode: ConversationContextBindingMode
    external_key: str
    source_artifact_id: str | None
    snapshot_sha256: str | None
    display_metadata_json: str
    status: ConversationContextBindingStatus
    status_reason: str | None
    created_by: str
    created_at_ms: int
    status_changed_at_ms: int
    detached_at_ms: int | None
    row_version: int

    @property
    def display_metadata(self) -> dict[str, object]:
        value = json.loads(self.display_metadata_json)
        if not isinstance(value, dict):
            raise ConversationRepositoryError(
                "Stored context-binding display metadata is not an object."
            )
        return value


@dataclass(frozen=True, slots=True)
class ConversationContextBindingCreate:
    binding_id: str
    conversation_id: str
    owner_user_id: str
    binding_type: ConversationContextBindingType
    binding_mode: ConversationContextBindingMode
    external_key: str
    source_artifact_id: str | None
    snapshot_sha256: str | None
    display_metadata_json: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ConversationContextBindingCreateResult:
    binding: ConversationContextBindingRecord
    inserted: bool


@dataclass(frozen=True, slots=True)
class ConversationContextBindingDetachResult:
    binding: ConversationContextBindingRecord
    detached: bool


@dataclass(frozen=True, slots=True)
class ConversationContextBindingAvailabilityResult:
    binding: ConversationContextBindingRecord
    changed: bool


@dataclass(frozen=True, slots=True)
class LegacyConversation:
    plan_sha256: str
    inventory_record_id: str
    conversation_id: str
    owner_user_id: str
    personal_space_id: str
    captured_scope_epoch: int
    title: str
    metadata_json: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class LegacyConversationResult:
    conversation_id: str
    inserted: bool


@dataclass(frozen=True, slots=True)
class LegacyConversationMessage:
    plan_sha256: str
    inventory_record_id: str
    message_id: str
    conversation_id: str
    owner_user_id: str
    personal_space_id: str
    sequence_no: int
    role: LegacyMessageRole
    content_json: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class LegacyConversationMessageResult:
    message_id: str
    inserted: bool


@dataclass(frozen=True, slots=True)
class LegacyConversationSummary:
    plan_sha256: str
    inventory_record_id: str
    summary_id: str
    conversation_id: str
    owner_user_id: str
    personal_space_id: str
    covered_through_sequence_no: int
    summary_md: str
    summary_sha256: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class LegacyConversationSummaryResult:
    summary_id: str
    inserted: bool


@dataclass(frozen=True, slots=True)
class LegacyNotionContextBinding:
    plan_sha256: str
    relationship_id: str
    context_binding_id: str
    conversation_id: str
    owner_user_id: str
    personal_space_id: str
    external_page_key: str
    display_metadata_json: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class LegacyNotionContextBindingResult:
    context_binding_id: str
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


def _relationship(value: object) -> str:
    if not isinstance(value, str) or _RELATIONSHIP_RE.fullmatch(value) is None:
        raise ValueError("relationship_id is invalid.")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("owner_user_id is invalid.")
    return value


def _positive(value: object, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= _SQLITE_INTEGER_MAX
    ):
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _utc_ms(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _SQLITE_INTEGER_MAX
    ):
        raise ValueError("created_at_ms must be a non-negative UTC millisecond integer.")
    return value


def _text(
    value: object,
    field: str,
    maximum: int,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str) or "\x00" in value or len(value) > maximum:
        raise ValueError(f"{field} is invalid.")
    if not allow_empty and not value.strip():
        raise ValueError(f"{field} is invalid.")
    return value


def _canonical_object(value: object, field: str) -> tuple[str, dict[str, object]]:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be canonical JSON.")
    try:
        parsed = json.loads(value)
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
        raise ValueError(f"{field} must be canonical JSON.") from None
    if type(parsed) is not dict or canonical != value:
        raise ValueError(f"{field} must be a canonical JSON object.")
    return value, parsed


def _connection(connection: ManagedConnection) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("ConversationRepository requires a ManagedConnection.")
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Conversation repository writes require an existing managed transaction."
        )
    return connection


def _managed_connection(connection: ManagedConnection) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("ConversationRepository requires a ManagedConnection.")
    return connection


def _conversation_record(row: object) -> ConversationRecord:
    return ConversationRecord(
        id=str(row["id"]),  # type: ignore[index]
        owner_user_id=str(row["owner_user_id"]),  # type: ignore[index]
        space_id=str(row["space_id"]),  # type: ignore[index]
        captured_scope_epoch=int(row["captured_scope_epoch"]),  # type: ignore[index]
        title=str(row["title"]),  # type: ignore[index]
        status=str(row["status"]),  # type: ignore[index,arg-type]
        metadata_json=str(row["metadata_json"]),  # type: ignore[index]
        created_at_ms=int(row["created_at"]),  # type: ignore[index]
        updated_at_ms=int(row["updated_at"]),  # type: ignore[index]
        row_version=int(row["row_version"]),  # type: ignore[index]
    )


def _message_record(row: object) -> ConversationMessageRecord:
    return ConversationMessageRecord(
        id=str(row["id"]),  # type: ignore[index]
        conversation_id=str(row["conversation_id"]),  # type: ignore[index]
        sequence_no=int(row["sequence_no"]),  # type: ignore[index]
        role=str(row["role"]),  # type: ignore[index,arg-type]
        content_json=str(row["content_json"]),  # type: ignore[index]
        created_at_ms=int(row["created_at"]),  # type: ignore[index]
    )


def _turn_record(row: object) -> ConversationTurnRecord:
    return ConversationTurnRecord(
        id=str(row["id"]),  # type: ignore[index]
        conversation_id=str(row["conversation_id"]),  # type: ignore[index]
        actor_user_id=str(row["actor_user_id"]),  # type: ignore[index]
        dedup_id=str(row["dedup_id"]),  # type: ignore[index]
        operation_id=str(row["operation_id"]),  # type: ignore[index]
        request_sha256=str(row["request_sha256"]),  # type: ignore[index]
        state=str(row["state"]),  # type: ignore[index,arg-type]
        user_message_id=str(row["user_message_id"]),  # type: ignore[index]
        assistant_message_id=row["assistant_message_id"],  # type: ignore[index,arg-type]
        final_response_json=row["final_response_json"],  # type: ignore[index,arg-type]
        error_json=row["error_json"],  # type: ignore[index,arg-type]
        created_at_ms=int(row["created_at"]),  # type: ignore[index]
        updated_at_ms=int(row["updated_at"]),  # type: ignore[index]
        completed_at_ms=(
            None
            if row["completed_at"] is None  # type: ignore[index]
            else int(row["completed_at"])  # type: ignore[index]
        ),
        row_version=int(row["row_version"]),  # type: ignore[index]
    )


def _event_record(row: object) -> ConversationTurnEventRecord:
    return ConversationTurnEventRecord(
        turn_id=str(row["turn_id"]),  # type: ignore[index]
        conversation_id=str(row["conversation_id"]),  # type: ignore[index]
        sequence_no=int(row["sequence_no"]),  # type: ignore[index]
        event_type=str(row["event_type"]),  # type: ignore[index,arg-type]
        payload_json=str(row["payload_json"]),  # type: ignore[index]
        created_at_ms=int(row["created_at"]),  # type: ignore[index]
    )


def _summary_record(row: object) -> ConversationSummaryRecord:
    return ConversationSummaryRecord(
        id=str(row["id"]),  # type: ignore[index]
        conversation_id=str(row["conversation_id"]),  # type: ignore[index]
        covered_through_sequence_no=int(  # type: ignore[index]
            row["covered_through_sequence_no"]
        ),
        summary_md=str(row["summary_md"]),  # type: ignore[index]
        summary_sha256=str(row["summary_sha256"]),  # type: ignore[index]
        model_id=str(row["model_id"]),  # type: ignore[index]
        prompt_version=str(row["prompt_version"]),  # type: ignore[index]
        summarizer_version=str(row["summarizer_version"]),  # type: ignore[index]
        status=str(row["status"]),  # type: ignore[index,arg-type]
        created_by_user_id=row["created_by_user_id"],  # type: ignore[index,arg-type]
        created_at_ms=int(row["created_at"]),  # type: ignore[index]
        invalidated_at_ms=(
            None
            if row["invalidated_at"] is None  # type: ignore[index]
            else int(row["invalidated_at"])  # type: ignore[index]
        ),
        invalidation_reason=row["invalidation_reason"],  # type: ignore[index,arg-type]
        row_version=int(row["row_version"]),  # type: ignore[index]
    )


def _pin_record(row: object) -> ConversationPinRecord:
    revision_id = row["revision_id"]  # type: ignore[index]
    if revision_id is None:
        raise ConversationRepositoryError(
            "A runtime conversation pin has no fixed revision."
        )
    return ConversationPinRecord(
        conversation_id=str(row["conversation_id"]),  # type: ignore[index]
        space_id=str(row["space_id"]),  # type: ignore[index]
        item_id=str(row["item_id"]),  # type: ignore[index]
        revision_id=str(revision_id),
        created_by=str(row["created_by"]),  # type: ignore[index]
        created_at_ms=int(row["created_at"]),  # type: ignore[index]
    )


def _binding_record(row: object) -> ConversationContextBindingRecord:
    return ConversationContextBindingRecord(
        id=str(row["id"]),  # type: ignore[index]
        conversation_id=str(row["conversation_id"]),  # type: ignore[index]
        binding_type=str(row["binding_type"]),  # type: ignore[index,arg-type]
        binding_mode=str(row["binding_mode"]),  # type: ignore[index,arg-type]
        external_key=str(row["external_key"]),  # type: ignore[index]
        source_artifact_id=row["source_artifact_id"],  # type: ignore[index,arg-type]
        snapshot_sha256=row["snapshot_sha256"],  # type: ignore[index,arg-type]
        display_metadata_json=str(row["display_metadata_json"]),  # type: ignore[index]
        status=str(row["status"]),  # type: ignore[index,arg-type]
        status_reason=row["status_reason"],  # type: ignore[index,arg-type]
        created_by=str(row["created_by"]),  # type: ignore[index]
        created_at_ms=int(row["created_at"]),  # type: ignore[index]
        status_changed_at_ms=int(row["status_changed_at"]),  # type: ignore[index]
        detached_at_ms=(
            None
            if row["detached_at"] is None  # type: ignore[index]
            else int(row["detached_at"])  # type: ignore[index]
        ),
        row_version=int(row["row_version"]),  # type: ignore[index]
    )


def _optional_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("limit must be an integer from 1 through 100.")
    return value


def _nonnegative(value: object, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _SQLITE_INTEGER_MAX
    ):
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


def _scope_facts(
    connection: ManagedConnection,
    *,
    owner_user_id: str,
    space_id: str,
    enforce_wiki_lifecycle: bool = False,
) -> object | None:
    wiki_columns = (
        ",p.owner_user_id AS wiki_owner_user_id,"
        "p.lifecycle_status AS wiki_lifecycle_status,"
        "p.lifecycle_generation AS wiki_lifecycle_generation,"
        "p.display_name AS wiki_display_name"
        if enforce_wiki_lifecycle
        else ""
    )
    wiki_join = (
        "LEFT JOIN personal_wiki_profile AS p ON p.space_id=s.id"
        if enforce_wiki_lifecycle
        else ""
    )
    return connection.execute(
        f"""SELECT u.id AS user_id,u.scope_epoch,u.current_part_id,
                  u.account_status,s.space_kind,s.owner_user_id AS space_owner_user_id,
                  s.owner_org_unit_id,s.status AS space_status,
                  s.write_enabled,ou.kind AS org_kind,ou.status AS org_status,
                  m.id AS membership_id,
                  home.id AS home_affiliation_id,
                  home.home_kind AS home_kind,
                  home.org_unit_id AS home_org_unit_id,
                  current_part.id AS current_part_scope_id,
                  current_team.id AS current_team_scope_id,
                  current_responsibility.id AS current_responsibility_scope_id
                  {wiki_columns}
           FROM app_user AS u
           JOIN knowledge_space AS s ON s.id=?
           {wiki_join}
           LEFT JOIN org_unit AS ou ON ou.id=s.owner_org_unit_id
           LEFT JOIN org_home_affiliation AS home
             ON home.user_id=u.id AND home.valid_to IS NULL
           LEFT JOIN org_unit AS home_unit ON home_unit.id=home.org_unit_id
           LEFT JOIN org_unit AS current_part ON current_part.id=u.current_part_id
           LEFT JOIN org_unit AS current_team ON current_team.id=CASE
             WHEN home_unit.kind='team' THEN home_unit.id
             WHEN home_unit.kind='part' THEN current_part.parent_id
             ELSE NULL END
           LEFT JOIN org_unit AS current_responsibility
             ON current_responsibility.id=CASE
               WHEN home_unit.kind='responsibility' THEN home_unit.id
               ELSE current_team.parent_id END
           LEFT JOIN org_membership AS m
             ON m.user_id=u.id AND m.part_id=u.current_part_id
            AND m.valid_to IS NULL
           WHERE u.id=?""",
        (space_id, owner_user_id),
    ).fetchone()


def _access_status(
    row: object | None,
    *,
    captured_scope_epoch: int,
    allow_higher_spaces: bool = False,
    enforce_wiki_lifecycle: bool = False,
) -> ConversationAccessStatus:
    if row is None or row["account_status"] != "active":  # type: ignore[index]
        return "owner_disabled"
    if row["space_status"] != "active":  # type: ignore[index]
        return "space_inactive"
    kind = row["space_kind"]  # type: ignore[index]
    if kind == "personal":
        if (
            row["space_owner_user_id"] != row["user_id"]  # type: ignore[index]
            or row["owner_org_unit_id"] is not None  # type: ignore[index]
        ):
            return "personal_owner_mismatch"
        if enforce_wiki_lifecycle:
            if row["wiki_owner_user_id"] != row["user_id"]:  # type: ignore[index]
                return "personal_owner_mismatch"
            if row["wiki_lifecycle_status"] == "archived":  # type: ignore[index]
                return "wiki_archived"
            if row["wiki_lifecycle_status"] != "active":  # type: ignore[index]
                return "space_inactive"
        return "accessible"
    if kind == "part":
        if (
            row["org_kind"] != "part"  # type: ignore[index]
            or row["org_status"] != "active"  # type: ignore[index]
            or row["current_part_id"] != row["owner_org_unit_id"]  # type: ignore[index]
            or row["membership_id"] is None  # type: ignore[index]
            or row["home_kind"] != "part"  # type: ignore[index]
            or row["home_org_unit_id"] != row["owner_org_unit_id"]  # type: ignore[index]
            or row["scope_epoch"] != captured_scope_epoch  # type: ignore[index]
        ):
            return "stale_part_scope"
        return "accessible"
    if allow_higher_spaces and kind in {"team", "responsibility"}:
        expected = (
            row["current_team_scope_id"]  # type: ignore[index]
            if kind == "team"
            else row["current_responsibility_scope_id"]  # type: ignore[index]
        )
        home_kind = row["home_kind"]  # type: ignore[index]
        part_home = home_kind == "part"
        direct_home = (
            (kind == "team" and home_kind == "team")
            or (kind == "responsibility" and home_kind in {"team", "responsibility"})
        )
        if (
            row["org_kind"] != kind  # type: ignore[index]
            or row["org_status"] != "active"  # type: ignore[index]
            or row["owner_org_unit_id"] != expected  # type: ignore[index]
            or not (part_home or direct_home)
            or (part_home and (
                row["current_part_scope_id"] is None  # type: ignore[index]
                or row["membership_id"] is None  # type: ignore[index]
            ))
            or (direct_home and row["membership_id"] is not None)  # type: ignore[index]
            or row["scope_epoch"] != captured_scope_epoch  # type: ignore[index]
        ):
            return "stale_part_scope"
        return "accessible"
    return "unsupported_space_kind"


def _id_for(namespace: UUID, plan_sha256: str, source_id: str) -> str:
    plan = _sha(plan_sha256, "plan_sha256")
    return str(uuid5(namespace, f"p2.5:{plan}:{source_id}"))


def legacy_conversation_id(plan_sha256: str, inventory_record_id: str) -> str:
    return _id_for(_CONVERSATION_NAMESPACE, plan_sha256, _record(inventory_record_id))


def legacy_conversation_message_id(
    plan_sha256: str, inventory_record_id: str
) -> str:
    return _id_for(_MESSAGE_NAMESPACE, plan_sha256, _record(inventory_record_id))


def legacy_conversation_summary_id(
    plan_sha256: str, inventory_record_id: str
) -> str:
    return _id_for(_SUMMARY_NAMESPACE, plan_sha256, _record(inventory_record_id))


def legacy_notion_context_binding_id(
    plan_sha256: str, relationship_id: str
) -> str:
    return _id_for(
        _CONTEXT_BINDING_NAMESPACE,
        plan_sha256,
        _relationship(relationship_id),
    )


def _row_tuple(row: object, columns: tuple[str, ...]) -> tuple[object, ...]:
    return tuple(row[name] for name in columns)  # type: ignore[index]


def _conversation_scope(
    connection: ManagedConnection,
    *,
    conversation_id: str,
    owner_user_id: str,
    personal_space_id: str,
) -> object:
    row = connection.execute(
        """SELECT c.id,c.owner_user_id,c.space_id,c.captured_scope_epoch,
                  c.status,u.scope_epoch,u.account_status,s.status AS space_status,
                  s.space_kind,s.owner_user_id AS space_owner
           FROM conversation AS c
           JOIN app_user AS u ON u.id=c.owner_user_id
           JOIN knowledge_space AS s ON s.id=c.space_id
           WHERE c.id=?""",
        (conversation_id,),
    ).fetchone()
    if (
        row is None
        or row["owner_user_id"] != owner_user_id
        or row["space_id"] != personal_space_id
        or row["captured_scope_epoch"] != row["scope_epoch"]
        or row["status"] != "archived"
        or row["account_status"] not in {"active", "disabled"}
        or row["space_status"] != "active"
        or row["space_kind"] != "personal"
        or row["space_owner"] != owner_user_id
    ):
        raise LegacyConversationConflictError(
            "The legacy conversation is not an archived owner-bound personal conversation."
        )
    return row


def _owner_scope(
    connection: ManagedConnection,
    *,
    owner_user_id: str,
    personal_space_id: str,
    captured_scope_epoch: int,
) -> None:
    row = connection.execute(
        """SELECT u.scope_epoch,u.account_status,s.space_kind,s.owner_user_id,
                  s.owner_org_unit_id,s.status
           FROM app_user AS u JOIN knowledge_space AS s ON s.id=?
           WHERE u.id=?""",
        (personal_space_id, owner_user_id),
    ).fetchone()
    if (
        row is None
        or row["scope_epoch"] != captured_scope_epoch
        or row["account_status"] not in {"active", "disabled"}
        or row["space_kind"] != "personal"
        or row["owner_user_id"] != owner_user_id
        or row["owner_org_unit_id"] is not None
        or row["status"] != "active"
    ):
        raise LegacyConversationConflictError(
            "The reviewed owner, scope epoch, and personal space do not match."
        )


class ConversationRepository:
    """Persist P3 conversations and exactly replay reviewed P2.5 effects."""

    __slots__ = ("_allow_higher_spaces", "_conversation_wiki_binding")

    def __init__(
        self,
        *,
        allow_higher_spaces: bool = False,
        conversation_wiki_binding: bool = False,
    ) -> None:
        if not isinstance(allow_higher_spaces, bool):
            raise TypeError("allow_higher_spaces must be Boolean.")
        if not isinstance(conversation_wiki_binding, bool):
            raise TypeError("conversation_wiki_binding must be Boolean.")
        self._allow_higher_spaces = allow_higher_spaces
        self._conversation_wiki_binding = conversation_wiki_binding

    @property
    def conversation_wiki_binding(self) -> bool:
        return self._conversation_wiki_binding

    def get(
        self,
        connection: ManagedConnection,
        conversation_id: str,
    ) -> ConversationRecord | None:
        connection = _managed_connection(connection)
        conversation_id = _uuid(conversation_id, "conversation_id")
        row = connection.execute(
            f"SELECT {','.join(_CONVERSATION_COLUMNS)} FROM conversation WHERE id=?",
            (conversation_id,),
        ).fetchone()
        return None if row is None else _conversation_record(row)

    def get_message(
        self,
        connection: ManagedConnection,
        message_id: str,
    ) -> ConversationMessageRecord | None:
        """Return one immutable message without widening conversation access.

        Higher-level services must first authorize the owning conversation and
        then bind the returned row back to that conversation.  Keeping this as
        an exact primary-key lookup avoids loading an unbounded message history
        merely to recover the accepted turn's retrieval text.
        """

        connection = _managed_connection(connection)
        message_id = _uuid(message_id, "message_id")
        row = connection.execute(
            f"SELECT {','.join(_MESSAGE_COLUMNS)} FROM conversation_message WHERE id=?",
            (message_id,),
        ).fetchone()
        return None if row is None else _message_record(row)

    def require_owned(
        self,
        connection: ManagedConnection,
        *,
        conversation_id: str,
        owner_user_id: str,
    ) -> ConversationRecord:
        owner = _user(owner_user_id)
        record = self.get(connection, conversation_id)
        if record is None or record.owner_user_id != owner:
            raise ConversationNotFoundError(
                "The requested owner-bound conversation does not exist."
            )
        return record

    def access_status(
        self,
        connection: ManagedConnection,
        *,
        conversation_id: str,
        owner_user_id: str,
    ) -> ConversationAccessStatus:
        connection = _managed_connection(connection)
        record = self.require_owned(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
        )
        facts = _scope_facts(
            connection,
            owner_user_id=record.owner_user_id,
            space_id=record.space_id,
            enforce_wiki_lifecycle=self._conversation_wiki_binding,
        )
        return _access_status(
            facts,
            captured_scope_epoch=record.captured_scope_epoch,
            allow_higher_spaces=self._allow_higher_spaces,
            enforce_wiki_lifecycle=self._conversation_wiki_binding,
        )

    def require_accessible(
        self,
        connection: ManagedConnection,
        *,
        conversation_id: str,
        owner_user_id: str,
    ) -> ConversationRecord:
        record = self.require_owned(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
        )
        status = self.access_status(
            connection,
            conversation_id=record.id,
            owner_user_id=record.owner_user_id,
        )
        if status != "accessible":
            raise ConversationScopeError(
                f"The conversation scope is not accessible: {status}."
            )
        return record

    def require_history_readable(
        self,
        connection: ManagedConnection,
        *,
        conversation_id: str,
        owner_user_id: str,
    ) -> ConversationRecord:
        """Admit ordinary access or exact owner history for an archived Wiki."""

        record = self.require_owned(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
        )
        status = self.access_status(
            connection,
            conversation_id=record.id,
            owner_user_id=record.owner_user_id,
        )
        if status == "accessible" or (
            self._conversation_wiki_binding and status == "wiki_archived"
        ):
            return record
        raise ConversationScopeError(
            f"The conversation history scope is not readable: {status}."
        )

    def list_for_owner(
        self,
        connection: ManagedConnection,
        *,
        owner_user_id: str,
        include_archived: bool = True,
        before_updated_at_ms: int | None = None,
        before_conversation_id: str | None = None,
        limit: int = 50,
    ) -> tuple[ConversationListEntry, ...]:
        connection = _managed_connection(connection)
        owner = _user(owner_user_id)
        limit = _optional_limit(limit)
        if not isinstance(include_archived, bool):
            raise ValueError("include_archived must be boolean.")
        if (before_updated_at_ms is None) != (before_conversation_id is None):
            raise ValueError("Both cursor fields must be supplied together.")
        parameters: list[object] = [owner]
        clauses = ["c.owner_user_id=?"]
        if not include_archived:
            clauses.append("c.status='active'")
        if before_updated_at_ms is not None:
            before_time = _nonnegative(before_updated_at_ms, "before_updated_at_ms")
            before_id = _uuid(before_conversation_id, "before_conversation_id")
            clauses.append("(c.updated_at<? OR (c.updated_at=? AND c.id<?))")
            parameters.extend((before_time, before_time, before_id))
        parameters.append(limit)
        rows = connection.execute(
            f"""SELECT {','.join('c.' + key for key in _CONVERSATION_COLUMNS)},
                       (SELECT count(*) FROM conversation_turn AS turn_count_row
                         WHERE turn_count_row.conversation_id=c.id) AS turn_count,
                       (SELECT count(*) FROM conversation_turn AS completed_count_row
                         WHERE completed_count_row.conversation_id=c.id
                           AND completed_count_row.state='completed') AS completed_turn_count,
                       (SELECT latest_turn_row.state
                          FROM conversation_turn AS latest_turn_row
                         WHERE latest_turn_row.conversation_id=c.id
                         ORDER BY latest_turn_row.created_at DESC,latest_turn_row.id DESC
                         LIMIT 1) AS latest_turn_state,
                       (SELECT CASE
                           WHEN json_type(first_message.content_json,'$.content.text')='text'
                             THEN json_extract(first_message.content_json,'$.content.text')
                           WHEN json_type(first_message.content_json,'$.content')='text'
                             THEN json_extract(first_message.content_json,'$.content')
                           ELSE NULL END
                          FROM conversation_message AS first_message
                         WHERE first_message.conversation_id=c.id
                           AND first_message.role='user'
                         ORDER BY first_message.sequence_no
                         LIMIT 1) AS first_user_message
                FROM conversation AS c
                WHERE {' AND '.join(clauses)}
                ORDER BY c.updated_at DESC,c.id DESC LIMIT ?""",
            tuple(parameters),
        ).fetchall()
        result: list[ConversationListEntry] = []
        for row in rows:
            record = _conversation_record(row)
            turn_count = row["turn_count"]
            completed_turn_count = row["completed_turn_count"]
            latest_turn_state = row["latest_turn_state"]
            first_user_message = row["first_user_message"]
            if (
                isinstance(turn_count, bool)
                or not isinstance(turn_count, int)
                or turn_count < 0
                or isinstance(completed_turn_count, bool)
                or not isinstance(completed_turn_count, int)
                or not 0 <= completed_turn_count <= turn_count
                or latest_turn_state
                not in {None, "accepted", "running", "completed", "failed", "cancelled"}
                or (turn_count == 0) != (latest_turn_state is None)
                or (
                    first_user_message is not None
                    and (
                        not isinstance(first_user_message, str)
                        or "\x00" in first_user_message
                    )
                )
            ):
                raise ConversationRepositoryError(
                    "Stored conversation turn statistics are invalid."
                )
            facts = _scope_facts(
                connection,
                owner_user_id=record.owner_user_id,
                space_id=record.space_id,
                enforce_wiki_lifecycle=self._conversation_wiki_binding,
            )
            result.append(
                ConversationListEntry(
                    conversation=record,
                    access_status=_access_status(
                        facts,
                        captured_scope_epoch=record.captured_scope_epoch,
                        allow_higher_spaces=self._allow_higher_spaces,
                        enforce_wiki_lifecycle=self._conversation_wiki_binding,
                    ),
                    turn_count=turn_count,
                    completed_turn_count=completed_turn_count,
                    latest_turn_state=latest_turn_state,
                    first_user_message=first_user_message,
                )
            )
        return tuple(result)

    def list_messages(
        self,
        connection: ManagedConnection,
        *,
        conversation_id: str,
        owner_user_id: str,
        after_sequence_no: int = 0,
        limit: int = 100,
        through_sequence_no: int | None = None,
    ) -> tuple[ConversationMessageRecord, ...]:
        connection = _managed_connection(connection)
        conversation = self.require_history_readable(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
        )
        after = _nonnegative(after_sequence_no, "after_sequence_no")
        limit = _optional_limit(limit)
        through = _SQLITE_INTEGER_MAX if through_sequence_no is None else _nonnegative(through_sequence_no, "through_sequence_no")
        rows = connection.execute(
            f"""SELECT {','.join(_MESSAGE_COLUMNS)} FROM conversation_message
                WHERE conversation_id=? AND sequence_no>? AND sequence_no<=?
                ORDER BY sequence_no LIMIT ?""",
            (conversation.id, after, through, limit),
        ).fetchall()
        return tuple(_message_record(row) for row in rows)

    def message_watermark(self, connection: ManagedConnection, *,
                          conversation_id: str, owner_user_id: str) -> int:
        connection = _managed_connection(connection)
        conversation = self.require_history_readable(
            connection, conversation_id=conversation_id, owner_user_id=owner_user_id,
        )
        row = connection.execute(
            "SELECT sequence_no FROM conversation_message WHERE conversation_id=? ORDER BY sequence_no DESC LIMIT 1",
            (conversation.id,),
        ).fetchone()
        return 0 if row is None else int(row["sequence_no"])

    def list_pending_turns(
        self, connection: ManagedConnection, *, conversation_id: str,
        owner_user_id: str, after_sequence_no: int = 0, limit: int = 100,
    ) -> tuple[ConversationTurnRecord, ...]:
        """Read an owner-authorized page ordered by immutable user-message order."""
        connection = _managed_connection(connection)
        conversation = self.require_history_readable(
            connection, conversation_id=conversation_id, owner_user_id=owner_user_id,
        )
        after = _nonnegative(after_sequence_no, "after_sequence_no")
        limit = _optional_limit(limit)
        rows = connection.execute(
            f"""SELECT {','.join('t.' + name for name in _TURN_COLUMNS)}
                FROM conversation_turn t JOIN conversation_message m
                  ON m.id=t.user_message_id AND m.conversation_id=t.conversation_id
                WHERE t.conversation_id=? AND t.actor_user_id=?
                  AND t.state IN ('accepted','running') AND m.sequence_no>?
                ORDER BY m.sequence_no LIMIT ?""",
            (conversation.id, owner_user_id, after, limit),
        ).fetchall()
        return tuple(_turn_record(row) for row in rows)

    def create(
        self,
        connection: ManagedConnection,
        value: ConversationCreate,
    ) -> ConversationCreateResult:
        connection = _connection(connection)
        if not isinstance(value, ConversationCreate):
            raise TypeError("A ConversationCreate is required.")
        conversation_id = _uuid(value.conversation_id, "conversation_id")
        owner = _user(value.owner_user_id)
        space = _uuid(value.space_id, "space_id")
        epoch = _positive(value.captured_scope_epoch, "captured_scope_epoch")
        title = _text(value.title, "title", 200, allow_empty=True)
        metadata, _ = _canonical_object(value.metadata_json, "metadata_json")
        created = _utc_ms(value.created_at_ms)
        facts = _scope_facts(
            connection,
            owner_user_id=owner,
            space_id=space,
            enforce_wiki_lifecycle=self._conversation_wiki_binding,
        )
        access = _access_status(
            facts,
            captured_scope_epoch=epoch,
            allow_higher_spaces=self._allow_higher_spaces,
            enforce_wiki_lifecycle=self._conversation_wiki_binding,
        )
        if access != "accessible" or facts is None or facts["scope_epoch"] != epoch:
            raise ConversationScopeError(
                f"The requested conversation scope is invalid: {access}."
            )
        expected = (
            conversation_id,
            owner,
            space,
            epoch,
            title,
            "active",
            metadata,
            created,
            created,
            1,
        )
        existing = self.get(connection, conversation_id)
        if existing is not None:
            if (
                existing.id,
                existing.owner_user_id,
                existing.space_id,
                existing.captured_scope_epoch,
                existing.title,
                existing.status,
                existing.metadata_json,
                existing.created_at_ms,
                existing.updated_at_ms,
                existing.row_version,
            ) != expected:
                raise ConversationStateConflictError(
                    "The conversation identity is already bound incompatibly."
                )
            return ConversationCreateResult(existing, False)
        try:
            connection.execute(
                """INSERT INTO conversation(
                     id,owner_user_id,space_id,captured_scope_epoch,title,status,
                     metadata_json,created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,?,'active',?,?,?,1)""",
                (conversation_id, owner, space, epoch, title, metadata, created, created),
            )
        except sqlite3.IntegrityError as exc:
            raise ConversationStateConflictError(
                "The conversation violates canonical constraints."
            ) from exc
        record = self.get(connection, conversation_id)
        if record is None:
            raise ConversationRepositoryError(
                "The inserted conversation cannot be read."
            )
        return ConversationCreateResult(record, True)

    def update_lifecycle(
        self,
        connection: ManagedConnection,
        *,
        conversation_id: str,
        owner_user_id: str,
        expected_row_version: int,
        updated_at_ms: int,
        title: str | None = None,
        status: ConversationStatus | None = None,
    ) -> ConversationUpdateResult:
        connection = _connection(connection)
        expected_version = _positive(expected_row_version, "expected_row_version")
        updated = _utc_ms(updated_at_ms)
        if title is None and status is None:
            raise ValueError("At least one lifecycle field must be supplied.")
        if status is not None and status not in {"active", "archived"}:
            raise ValueError("status must be active, archived, or null.")
        record = self.require_accessible(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
        )
        if record.row_version != expected_version:
            raise ConversationVersionConflictError(
                "The conversation row version is stale."
            )
        next_title = record.title if title is None else _text(
            title, "title", 200, allow_empty=True
        )
        next_status = record.status if status is None else status
        if next_title == record.title and next_status == record.status:
            return ConversationUpdateResult(record, False)
        if updated < record.updated_at_ms:
            raise ValueError("updated_at_ms cannot move backwards.")
        cursor = connection.execute(
            """UPDATE conversation
               SET title=?,status=?,updated_at=?,row_version=row_version+1
               WHERE id=? AND owner_user_id=? AND row_version=?""",
            (
                next_title,
                next_status,
                updated,
                record.id,
                record.owner_user_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise ConversationVersionConflictError(
                "The conversation changed before the update committed."
            )
        current = self.get(connection, record.id)
        if current is None:
            raise ConversationRepositoryError(
                "The updated conversation cannot be read."
            )
        return ConversationUpdateResult(current, True)

    def get_turn(
        self,
        connection: ManagedConnection,
        turn_id: str,
    ) -> ConversationTurnRecord | None:
        connection = _managed_connection(connection)
        turn_id = _uuid(turn_id, "turn_id")
        row = connection.execute(
            f"SELECT {','.join(_TURN_COLUMNS)} FROM conversation_turn WHERE id=?",
            (turn_id,),
        ).fetchone()
        return None if row is None else _turn_record(row)

    def get_turn_by_operation(
        self,
        connection: ManagedConnection,
        *,
        conversation_id: str,
        operation_id: str,
    ) -> ConversationTurnRecord | None:
        connection = _managed_connection(connection)
        conversation_id = _uuid(conversation_id, "conversation_id")
        operation_id = _uuid(operation_id, "operation_id")
        row = connection.execute(
            f"""SELECT {','.join(_TURN_COLUMNS)} FROM conversation_turn
                WHERE conversation_id=? AND operation_id=?""",
            (conversation_id, operation_id),
        ).fetchone()
        return None if row is None else _turn_record(row)

    def list_turn_events(
        self,
        connection: ManagedConnection,
        *,
        turn_id: str,
        conversation_id: str,
        after_sequence_no: int = 0,
        limit: int = 100,
    ) -> tuple[ConversationTurnEventRecord, ...]:
        connection = _managed_connection(connection)
        turn_id = _uuid(turn_id, "turn_id")
        conversation_id = _uuid(conversation_id, "conversation_id")
        after = _nonnegative(after_sequence_no, "after_sequence_no")
        limit = _optional_limit(limit)
        rows = connection.execute(
            f"""SELECT {','.join(_EVENT_COLUMNS)} FROM conversation_turn_event
                WHERE turn_id=? AND conversation_id=? AND sequence_no>?
                ORDER BY sequence_no LIMIT ?""",
            (turn_id, conversation_id, after, limit),
        ).fetchall()
        return tuple(_event_record(row) for row in rows)

    def accept_turn(
        self,
        connection: ManagedConnection,
        value: ConversationTurnAccept,
    ) -> ConversationTurnAcceptResult:
        connection = _connection(connection)
        if not isinstance(value, ConversationTurnAccept):
            raise TypeError("A ConversationTurnAccept is required.")
        turn_id = _uuid(value.turn_id, "turn_id")
        conversation_id = _uuid(value.conversation_id, "conversation_id")
        actor = _user(value.actor_user_id)
        dedup_id = _uuid(value.dedup_id, "dedup_id")
        operation_id = _uuid(value.operation_id, "operation_id")
        request_sha = _sha(value.request_sha256, "request_sha256")
        user_message_id = _uuid(value.user_message_id, "user_message_id")
        user_content, parsed_content = _canonical_object(
            value.user_content_json, "user_content_json"
        )
        if parsed_content.get("role") != "user":
            raise ValueError("user_content_json must carry the user role.")
        accepted_event, accepted_payload = _canonical_object(
            value.accepted_event_json, "accepted_event_json"
        )
        if accepted_payload.get("state") != "accepted":
            raise ValueError("accepted_event_json must carry accepted state.")
        created = _utc_ms(value.created_at_ms)
        conversation = self.require_accessible(
            connection,
            conversation_id=conversation_id,
            owner_user_id=actor,
        )
        if conversation.status != "active":
            raise ConversationStateConflictError(
                "A turn can be accepted only for an active conversation."
            )
        operation = connection.execute(
            """SELECT actor_user_id,operation_type,operation_id,request_sha256,status
               FROM operation_dedup WHERE id=?""",
            (dedup_id,),
        ).fetchone()
        if (
            operation is None
            or operation["actor_user_id"] != actor
            or operation["operation_type"] != "conversation_turn"
            or operation["operation_id"] != operation_id
            or operation["request_sha256"] != request_sha
            or operation["status"] not in {"in_progress", "committed"}
        ):
            raise ConversationStateConflictError(
                "The turn operation binding is invalid."
            )
        existing = self.get_turn_by_operation(
            connection,
            conversation_id=conversation_id,
            operation_id=operation_id,
        )
        if existing is not None:
            message_row = connection.execute(
                f"SELECT {','.join(_MESSAGE_COLUMNS)} FROM conversation_message WHERE id=?",
                (existing.user_message_id,),
            ).fetchone()
            event_row = connection.execute(
                f"""SELECT {','.join(_EVENT_COLUMNS)} FROM conversation_turn_event
                    WHERE turn_id=? AND sequence_no=1""",
                (existing.id,),
            ).fetchone()
            if (
                existing.id != turn_id
                or existing.actor_user_id != actor
                or existing.dedup_id != dedup_id
                or existing.request_sha256 != request_sha
                or existing.user_message_id != user_message_id
                or existing.created_at_ms != created
                or message_row is None
                or event_row is None
            ):
                raise ConversationStateConflictError(
                    "The existing turn is bound incompatibly."
                )
            message = _message_record(message_row)
            event = _event_record(event_row)
            if (
                message.conversation_id != conversation_id
                or message.role != "user"
                or message.content_json != user_content
                or message.created_at_ms != created
                or event.conversation_id != conversation_id
                or event.event_type != "accepted"
                or event.payload_json != accepted_event
                or event.created_at_ms != created
            ):
                raise ConversationStateConflictError(
                    "The existing turn evidence is bound incompatibly."
                )
            return ConversationTurnAcceptResult(existing, message, False)
        sequence_row = connection.execute(
            """SELECT coalesce(max(sequence_no),0)+1 AS next_sequence
               FROM conversation_message WHERE conversation_id=?""",
            (conversation_id,),
        ).fetchone()
        if sequence_row is None:
            raise ConversationRepositoryError("The next message sequence is unavailable.")
        sequence = int(sequence_row["next_sequence"])
        try:
            connection.execute(
                """INSERT INTO conversation_message(
                     id,conversation_id,sequence_no,role,content_json,created_at
                   ) VALUES (?,?,?,'user',?,?)""",
                (user_message_id, conversation_id, sequence, user_content, created),
            )
            connection.execute(
                """INSERT INTO conversation_turn(
                     id,conversation_id,actor_user_id,dedup_id,operation_id,
                     request_sha256,state,user_message_id,assistant_message_id,
                     final_response_json,error_json,created_at,updated_at,
                     completed_at,row_version
                   ) VALUES (?,?,?,?,?,?,'accepted',?,NULL,NULL,NULL,?,?,NULL,1)""",
                (
                    turn_id,
                    conversation_id,
                    actor,
                    dedup_id,
                    operation_id,
                    request_sha,
                    user_message_id,
                    created,
                    created,
                ),
            )
            connection.execute(
                """INSERT INTO conversation_turn_event(
                     turn_id,conversation_id,sequence_no,event_type,payload_json,
                     created_at
                   ) VALUES (?,?,1,'accepted',?,?)""",
                (turn_id, conversation_id, accepted_event, created),
            )
        except sqlite3.IntegrityError as exc:
            raise ConversationStateConflictError(
                "The accepted turn violates canonical constraints."
            ) from exc
        turn = self.get_turn(connection, turn_id)
        message_row = connection.execute(
            f"SELECT {','.join(_MESSAGE_COLUMNS)} FROM conversation_message WHERE id=?",
            (user_message_id,),
        ).fetchone()
        if turn is None or message_row is None:
            raise ConversationRepositoryError("The accepted turn cannot be read.")
        return ConversationTurnAcceptResult(turn, _message_record(message_row), True)

    def mark_turn_running(
        self,
        connection: ManagedConnection,
        *,
        turn_id: str,
        conversation_id: str,
        actor_user_id: str,
        updated_at_ms: int,
    ) -> ConversationTurnRecord:
        connection = _connection(connection)
        turn_id = _uuid(turn_id, "turn_id")
        conversation_id = _uuid(conversation_id, "conversation_id")
        actor = _user(actor_user_id)
        updated = _utc_ms(updated_at_ms)
        self.require_accessible(
            connection,
            conversation_id=conversation_id,
            owner_user_id=actor,
        )
        turn = self.get_turn(connection, turn_id)
        if turn is None or turn.conversation_id != conversation_id or turn.actor_user_id != actor:
            raise ConversationNotFoundError("The requested turn does not exist.")
        if turn.state in {"running", "completed"}:
            return turn
        if turn.state != "accepted" or updated < turn.updated_at_ms:
            raise ConversationStateConflictError("The turn cannot enter running state.")
        try:
            cursor = connection.execute(
                """UPDATE conversation_turn
                   SET state='running',updated_at=?,row_version=row_version+1
                   WHERE id=? AND state='accepted' AND row_version=?""",
                (updated, turn.id, turn.row_version),
            )
        except sqlite3.IntegrityError as exc:
            raise ConversationStateConflictError(
                "The turn cannot enter running state."
            ) from exc
        if cursor.rowcount != 1:
            raise ConversationVersionConflictError("The turn state changed concurrently.")
        current = self.get_turn(connection, turn.id)
        if current is None:
            raise ConversationRepositoryError("The running turn cannot be read.")
        return current

    def complete_turn(
        self,
        connection: ManagedConnection,
        value: ConversationTurnCompletion,
    ) -> ConversationTurnCompletionResult:
        connection = _connection(connection)
        if not isinstance(value, ConversationTurnCompletion):
            raise TypeError("A ConversationTurnCompletion is required.")
        turn_id = _uuid(value.turn_id, "turn_id")
        conversation_id = _uuid(value.conversation_id, "conversation_id")
        actor = _user(value.actor_user_id)
        assistant_id = _uuid(value.assistant_message_id, "assistant_message_id")
        assistant_content, assistant_payload = _canonical_object(
            value.assistant_content_json, "assistant_content_json"
        )
        if assistant_payload.get("role") != "assistant":
            raise ValueError("assistant_content_json must carry the assistant role.")
        final_response, _ = _canonical_object(
            value.final_response_json, "final_response_json"
        )
        completed_event, completed_payload = _canonical_object(
            value.completed_event_json, "completed_event_json"
        )
        if completed_payload.get("state") != "completed":
            raise ValueError("completed_event_json must carry completed state.")
        completed_at = _utc_ms(value.completed_at_ms)
        self.require_accessible(
            connection,
            conversation_id=conversation_id,
            owner_user_id=actor,
        )
        turn = self.get_turn(connection, turn_id)
        if turn is None or turn.conversation_id != conversation_id or turn.actor_user_id != actor:
            raise ConversationNotFoundError("The requested turn does not exist.")
        if turn.state == "completed":
            message_row = connection.execute(
                f"SELECT {','.join(_MESSAGE_COLUMNS)} FROM conversation_message WHERE id=?",
                (assistant_id,),
            ).fetchone()
            events = self.list_turn_events(
                connection,
                turn_id=turn.id,
                conversation_id=conversation_id,
            )
            terminal = events[-1] if events else None
            if (
                turn.assistant_message_id != assistant_id
                or turn.final_response_json != final_response
                or turn.completed_at_ms != completed_at
                or message_row is None
                or _message_record(message_row).content_json != assistant_content
                or terminal is None
                or terminal.event_type != "completed"
                or terminal.payload_json != completed_event
            ):
                raise ConversationStateConflictError(
                    "The completed turn is bound incompatibly."
                )
            return ConversationTurnCompletionResult(
                turn,
                _message_record(message_row),
                False,
            )
        if turn.state != "running" or completed_at < turn.updated_at_ms:
            raise ConversationStateConflictError("The turn cannot complete from its state.")
        sequence_row = connection.execute(
            """SELECT coalesce(max(sequence_no),0)+1 AS next_sequence
               FROM conversation_message WHERE conversation_id=?""",
            (conversation_id,),
        ).fetchone()
        event_row = connection.execute(
            """SELECT coalesce(max(sequence_no),0)+1 AS next_sequence
               FROM conversation_turn_event WHERE turn_id=?""",
            (turn.id,),
        ).fetchone()
        if sequence_row is None or event_row is None:
            raise ConversationRepositoryError("The completion sequences are unavailable.")
        try:
            connection.execute(
                """INSERT INTO conversation_message(
                     id,conversation_id,sequence_no,role,content_json,created_at
                   ) VALUES (?,?,?,'assistant',?,?)""",
                (
                    assistant_id,
                    conversation_id,
                    int(sequence_row["next_sequence"]),
                    assistant_content,
                    completed_at,
                ),
            )
            update_cursor = connection.execute(
                """UPDATE conversation_turn
                   SET state='completed',assistant_message_id=?,final_response_json=?,
                       error_json=NULL,updated_at=?,completed_at=?,
                       row_version=row_version+1
                   WHERE id=? AND state='running' AND row_version=?""",
                (
                    assistant_id,
                    final_response,
                    completed_at,
                    completed_at,
                    turn.id,
                    turn.row_version,
                ),
            )
            if update_cursor.rowcount != 1:
                raise ConversationVersionConflictError(
                    "The turn state changed before completion."
                )
            connection.execute(
                """INSERT INTO conversation_turn_event(
                     turn_id,conversation_id,sequence_no,event_type,payload_json,
                     created_at
                   ) VALUES (?,?,?,'completed',?,?)""",
                (
                    turn.id,
                    conversation_id,
                    int(event_row["next_sequence"]),
                    completed_event,
                    completed_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConversationStateConflictError(
                "The turn completion violates canonical constraints."
            ) from exc
        current = self.get_turn(connection, turn.id)
        message_row = connection.execute(
            f"SELECT {','.join(_MESSAGE_COLUMNS)} FROM conversation_message WHERE id=?",
            (assistant_id,),
        ).fetchone()
        if current is None or message_row is None or current.state != "completed":
            raise ConversationRepositoryError("The completed turn cannot be read.")
        return ConversationTurnCompletionResult(
            current,
            _message_record(message_row),
            True,
        )

    def terminate_turn(
        self,
        connection: ManagedConnection,
        value: ConversationTurnTermination,
    ) -> ConversationTurnTerminationResult:
        connection = _connection(connection)
        if not isinstance(value, ConversationTurnTermination):
            raise TypeError("A ConversationTurnTermination is required.")
        turn_id = _uuid(value.turn_id, "turn_id")
        conversation_id = _uuid(value.conversation_id, "conversation_id")
        actor = _user(value.actor_user_id)
        terminal_state = value.terminal_state
        if terminal_state not in {"failed", "cancelled"}:
            raise ValueError("terminal_state must be failed or cancelled.")
        error_json: str | None = None
        error_payload: dict[str, object] | None = None
        if value.error_json is not None:
            error_json, error_payload = _canonical_object(
                value.error_json, "error_json"
            )
        terminal_event, event_payload = _canonical_object(
            value.terminal_event_json, "terminal_event_json"
        )
        if event_payload.get("state") != terminal_state:
            raise ValueError(
                "terminal_event_json must carry the requested terminal state."
            )
        if terminal_state == "failed":
            if (
                error_payload is None
                or frozenset(error_payload) != {"code", "retryable", "schema_version"}
                or error_payload.get("schema_version") != 1
                or not isinstance(error_payload.get("retryable"), bool)
                or not isinstance(error_payload.get("code"), str)
                or _ERROR_CODE_RE.fullmatch(str(error_payload.get("code"))) is None
                or frozenset(event_payload) != {"error", "schema_version", "state"}
                or event_payload.get("schema_version") != 1
                or event_payload.get("error") != error_payload
            ):
                raise ValueError(
                    "A failed turn event must carry the exact stored error."
                )
        elif (
            error_payload is not None
            or frozenset(event_payload) != {"schema_version", "state"}
            or event_payload.get("schema_version") != 1
        ):
            raise ValueError("A cancelled turn cannot carry an error.")
        completed_at = _utc_ms(value.completed_at_ms)
        self.require_history_readable(
            connection,
            conversation_id=conversation_id,
            owner_user_id=actor,
        )
        turn = self.get_turn(connection, turn_id)
        if (
            turn is None
            or turn.conversation_id != conversation_id
            or turn.actor_user_id != actor
        ):
            raise ConversationNotFoundError("The requested turn does not exist.")
        if turn.state in {"failed", "cancelled"}:
            events = self.list_turn_events(
                connection,
                turn_id=turn.id,
                conversation_id=conversation_id,
            )
            terminal = events[-1] if events else None
            if (
                turn.state != terminal_state
                or turn.assistant_message_id is not None
                or turn.final_response_json is not None
                or turn.error_json != error_json
                or turn.completed_at_ms != completed_at
                or terminal is None
                or terminal.event_type != terminal_state
                or terminal.payload_json != terminal_event
            ):
                raise ConversationStateConflictError(
                    "The terminal turn is bound incompatibly."
                )
            return ConversationTurnTerminationResult(turn, False)
        if turn.state not in {"accepted", "running"}:
            raise ConversationStateConflictError(
                "The turn cannot enter the requested terminal state."
            )
        if completed_at < turn.updated_at_ms:
            raise ConversationStateConflictError(
                "The terminal time precedes the current turn state."
            )
        event_row = connection.execute(
            """SELECT coalesce(max(sequence_no),0)+1 AS next_sequence
               FROM conversation_turn_event WHERE turn_id=?""",
            (turn.id,),
        ).fetchone()
        if event_row is None:
            raise ConversationRepositoryError(
                "The terminal event sequence is unavailable."
            )
        try:
            update_cursor = connection.execute(
                """UPDATE conversation_turn
                   SET state=?,assistant_message_id=NULL,final_response_json=NULL,
                       error_json=?,updated_at=?,completed_at=?,
                       row_version=row_version+1
                   WHERE id=? AND state IN ('accepted','running') AND row_version=?""",
                (
                    terminal_state,
                    error_json,
                    completed_at,
                    completed_at,
                    turn.id,
                    turn.row_version,
                ),
            )
            if update_cursor.rowcount != 1:
                raise ConversationVersionConflictError(
                    "The turn state changed before termination."
                )
            connection.execute(
                """INSERT INTO conversation_turn_event(
                     turn_id,conversation_id,sequence_no,event_type,payload_json,
                     created_at
                   ) VALUES (?,?,?,?,?,?)""",
                (
                    turn.id,
                    conversation_id,
                    int(event_row["next_sequence"]),
                    terminal_state,
                    terminal_event,
                    completed_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConversationStateConflictError(
                "The turn termination violates canonical constraints."
            ) from exc
        current = self.get_turn(connection, turn.id)
        if current is None or current.state != terminal_state:
            raise ConversationRepositoryError("The terminal turn cannot be read.")
        return ConversationTurnTerminationResult(current, True)

    def get_summary(
        self,
        connection: ManagedConnection,
        summary_id: str,
    ) -> ConversationSummaryRecord | None:
        connection = _managed_connection(connection)
        summary_id = _uuid(summary_id, "summary_id")
        row = connection.execute(
            f"SELECT {','.join(_SUMMARY_COLUMNS)} FROM conversation_summary WHERE id=?",
            (summary_id,),
        ).fetchone()
        return None if row is None else _summary_record(row)

    def get_active_summary(
        self,
        connection: ManagedConnection,
        *,
        conversation_id: str,
        owner_user_id: str,
    ) -> ConversationSummaryRecord | None:
        connection = _managed_connection(connection)
        conversation_id = _uuid(conversation_id, "conversation_id")
        owner = _user(owner_user_id)
        self.require_history_readable(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner,
        )
        row = connection.execute(
            f"""SELECT {','.join(_SUMMARY_COLUMNS)} FROM conversation_summary
                WHERE conversation_id=? AND status='active'""",
            (conversation_id,),
        ).fetchone()
        return None if row is None else _summary_record(row)

    def list_summary_history(
        self,
        connection: ManagedConnection,
        *,
        conversation_id: str,
        owner_user_id: str,
        limit: int = 100,
    ) -> tuple[ConversationSummaryRecord, ...]:
        connection = _managed_connection(connection)
        conversation_id = _uuid(conversation_id, "conversation_id")
        owner = _user(owner_user_id)
        limit = _optional_limit(limit)
        self.require_history_readable(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner,
        )
        rows = connection.execute(
            f"""SELECT {','.join(_SUMMARY_COLUMNS)} FROM conversation_summary
                WHERE conversation_id=? ORDER BY created_at DESC,id DESC LIMIT ?""",
            (conversation_id, limit),
        ).fetchall()
        return tuple(_summary_record(row) for row in rows)

    def put_summary(
        self,
        connection: ManagedConnection,
        value: ConversationSummaryCreate,
    ) -> ConversationSummaryCreateResult:
        connection = _connection(connection)
        if not isinstance(value, ConversationSummaryCreate):
            raise TypeError("A ConversationSummaryCreate is required.")
        summary_id = _uuid(value.summary_id, "summary_id")
        conversation_id = _uuid(value.conversation_id, "conversation_id")
        owner = _user(value.owner_user_id)
        creator = _user(value.created_by_user_id)
        if creator != owner:
            raise ConversationScopeError(
                "A compact summary creator must own the conversation."
            )
        covered = _positive(
            value.covered_through_sequence_no,
            "covered_through_sequence_no",
        )
        summary = _text(value.summary_md, "summary_md", 1_000_000)
        summary_sha = _sha(value.summary_sha256, "summary_sha256")
        if hashlib.sha256(summary.encode("utf-8")).hexdigest() != summary_sha:
            raise ValueError("summary_sha256 does not match UTF-8 summary_md.")
        model_id = _text(value.model_id, "model_id", 200)
        prompt_version = _text(value.prompt_version, "prompt_version", 120)
        summarizer_version = _text(
            value.summarizer_version,
            "summarizer_version",
            120,
        )
        if any(
            item != item.strip()
            for item in (model_id, prompt_version, summarizer_version)
        ):
            raise ValueError("Summary implementation identifiers must be trimmed.")
        created = _utc_ms(value.created_at_ms)
        conversation = self.require_accessible(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner,
        )
        if conversation.status != "active":
            raise ConversationStateConflictError(
                "Only an active conversation can be compacted."
            )
        covered_row = connection.execute(
            """SELECT 1 FROM conversation_message
               WHERE conversation_id=? AND sequence_no=?""",
            (conversation_id, covered),
        ).fetchone()
        if covered_row is None:
            raise ConversationStateConflictError(
                "The compact summary must end on an existing message."
            )
        expected = (
            summary_id,
            conversation_id,
            covered,
            summary,
            summary_sha,
            model_id,
            prompt_version,
            summarizer_version,
            "active",
            creator,
            created,
            None,
            None,
            1,
        )
        existing = connection.execute(
            f"SELECT {','.join(_SUMMARY_COLUMNS)} FROM conversation_summary WHERE id=?",
            (summary_id,),
        ).fetchone()
        active = connection.execute(
            f"""SELECT {','.join(_SUMMARY_COLUMNS)} FROM conversation_summary
                WHERE conversation_id=? AND status='active'""",
            (conversation_id,),
        ).fetchone()
        if existing is not None:
            if (
                _row_tuple(existing, _SUMMARY_COLUMNS) != expected
                or active is None
                or active["id"] != summary_id
            ):
                raise ConversationStateConflictError(
                    "The compact summary is already bound incompatibly."
                )
            return ConversationSummaryCreateResult(
                _summary_record(existing),
                False,
                None,
            )
        invalidated_id: str | None = None
        if active is not None:
            invalidated_id = str(active["id"])
            if created < int(active["created_at"]):
                raise ConversationStateConflictError(
                    "A replacement summary cannot predate the active summary."
                )
            try:
                invalidated = connection.execute(
                    """UPDATE conversation_summary
                       SET status='invalidated',invalidated_at=?,
                           invalidation_reason='superseded_by_compaction',
                           row_version=row_version+1
                       WHERE id=? AND status='active' AND row_version=?""",
                    (created, invalidated_id, int(active["row_version"])),
                )
            except sqlite3.IntegrityError as exc:
                raise ConversationStateConflictError(
                    "The active summary cannot be invalidated."
                ) from exc
            if invalidated.rowcount != 1:
                raise ConversationVersionConflictError(
                    "The active summary changed before replacement."
                )
        try:
            connection.execute(
                """INSERT INTO conversation_summary(
                     id,conversation_id,covered_through_sequence_no,summary_md,
                     summary_sha256,model_id,prompt_version,summarizer_version,
                     status,created_by_user_id,created_at,invalidated_at,
                     invalidation_reason,row_version
                   ) VALUES (?,?,?,?,?,?,?,?,'active',?,?,NULL,NULL,1)""",
                (
                    summary_id,
                    conversation_id,
                    covered,
                    summary,
                    summary_sha,
                    model_id,
                    prompt_version,
                    summarizer_version,
                    creator,
                    created,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConversationStateConflictError(
                "The compact summary violates canonical constraints."
            ) from exc
        current = self.get_summary(connection, summary_id)
        if current is None or current.status != "active":
            raise ConversationRepositoryError(
                "The compact summary cannot be read after insertion."
            )
        return ConversationSummaryCreateResult(current, True, invalidated_id)

    def list_pins(
        self,
        connection: ManagedConnection,
        *,
        conversation_id: str,
        owner_user_id: str,
        limit: int = 100,
    ) -> tuple[ConversationPinRecord, ...]:
        connection = _managed_connection(connection)
        conversation_id = _uuid(conversation_id, "conversation_id")
        owner = _user(owner_user_id)
        limit = _optional_limit(limit)
        self.require_history_readable(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner,
        )
        rows = connection.execute(
            f"""SELECT {','.join(_PIN_COLUMNS)} FROM conversation_pin
                WHERE conversation_id=? ORDER BY created_at,item_id LIMIT ?""",
            (conversation_id, limit),
        ).fetchall()
        return tuple(_pin_record(row) for row in rows)

    def put_pin(
        self,
        connection: ManagedConnection,
        value: ConversationPinCreate,
    ) -> ConversationPinCreateResult:
        connection = _connection(connection)
        if not isinstance(value, ConversationPinCreate):
            raise TypeError("A ConversationPinCreate is required.")
        conversation_id = _uuid(value.conversation_id, "conversation_id")
        owner = _user(value.owner_user_id)
        item_id = _uuid(value.item_id, "item_id")
        revision_id = _uuid(value.revision_id, "revision_id")
        created = _utc_ms(value.created_at_ms)
        conversation = self.require_accessible(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner,
        )
        if conversation.status != "active":
            raise ConversationStateConflictError(
                "Only an active conversation can receive a pin."
            )
        item = connection.execute(
            """SELECT i.space_id,i.lifecycle_status,i.curation_status,
                      i.current_revision_id,r.id AS revision_id
               FROM knowledge_item AS i
               LEFT JOIN knowledge_revision AS r
                 ON r.id=? AND r.item_id=i.id
               WHERE i.id=?""",
            (revision_id, item_id),
        ).fetchone()
        if (
            item is None
            or item["space_id"] != conversation.space_id
            or item["lifecycle_status"] != "active"
            or item["curation_status"] != "published"
            or item["current_revision_id"] != revision_id
            or item["revision_id"] != revision_id
        ):
            raise ConversationScopeError(
                "A pin requires the current visible revision from the conversation space."
            )
        expected = (
            conversation_id,
            conversation.space_id,
            item_id,
            revision_id,
            owner,
            created,
        )
        existing = connection.execute(
            f"""SELECT {','.join(_PIN_COLUMNS)} FROM conversation_pin
                WHERE conversation_id=? AND item_id=?""",
            (conversation_id, item_id),
        ).fetchone()
        if existing is not None:
            if _row_tuple(existing, _PIN_COLUMNS[:-1]) != expected[:-1]:
                raise ConversationStateConflictError(
                    "The item is already pinned with different evidence."
                )
            return ConversationPinCreateResult(_pin_record(existing), False)
        try:
            connection.execute(
                """INSERT INTO conversation_pin(
                     conversation_id,space_id,item_id,revision_id,created_by,
                     created_at
                   ) VALUES (?,?,?,?,?,?)""",
                expected,
            )
        except sqlite3.IntegrityError as exc:
            raise ConversationStateConflictError(
                "The pin violates canonical same-space constraints."
            ) from exc
        row = connection.execute(
            f"""SELECT {','.join(_PIN_COLUMNS)} FROM conversation_pin
                WHERE conversation_id=? AND item_id=?""",
            (conversation_id, item_id),
        ).fetchone()
        if row is None:
            raise ConversationRepositoryError("The inserted pin cannot be read.")
        return ConversationPinCreateResult(_pin_record(row), True)

    def remove_pin(
        self,
        connection: ManagedConnection,
        *,
        conversation_id: str,
        owner_user_id: str,
        item_id: str,
    ) -> ConversationPinRemoveResult:
        connection = _connection(connection)
        conversation_id = _uuid(conversation_id, "conversation_id")
        owner = _user(owner_user_id)
        item_id = _uuid(item_id, "item_id")
        self.require_accessible(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner,
        )
        row = connection.execute(
            f"""SELECT {','.join(_PIN_COLUMNS)} FROM conversation_pin
                WHERE conversation_id=? AND item_id=?""",
            (conversation_id, item_id),
        ).fetchone()
        if row is None:
            return ConversationPinRemoveResult(None, False)
        pin = _pin_record(row)
        if pin.created_by != owner:
            raise ConversationScopeError("Only the pin owner may remove this pin.")
        cursor = connection.execute(
            """DELETE FROM conversation_pin
               WHERE conversation_id=? AND item_id=? AND created_by=?""",
            (conversation_id, item_id, owner),
        )
        if cursor.rowcount != 1:
            raise ConversationVersionConflictError(
                "The pin changed before removal."
            )
        return ConversationPinRemoveResult(pin, True)

    def get_context_binding(
        self,
        connection: ManagedConnection,
        binding_id: str,
    ) -> ConversationContextBindingRecord | None:
        connection = _managed_connection(connection)
        binding_id = _uuid(binding_id, "binding_id")
        row = connection.execute(
            f"""SELECT {','.join(_BINDING_COLUMNS)}
                FROM conversation_context_binding WHERE id=?""",
            (binding_id,),
        ).fetchone()
        return None if row is None else _binding_record(row)

    def list_context_bindings(
        self,
        connection: ManagedConnection,
        *,
        conversation_id: str,
        owner_user_id: str,
        include_detached: bool = False,
        limit: int = 100,
    ) -> tuple[ConversationContextBindingRecord, ...]:
        connection = _managed_connection(connection)
        conversation_id = _uuid(conversation_id, "conversation_id")
        owner = _user(owner_user_id)
        if not isinstance(include_detached, bool):
            raise ValueError("include_detached must be a Boolean.")
        limit = _optional_limit(limit)
        self.require_history_readable(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner,
        )
        rows = connection.execute(
            f"""SELECT {','.join(_BINDING_COLUMNS)}
                FROM conversation_context_binding
                WHERE conversation_id=? AND (? OR status<>'detached')
                ORDER BY created_at,id LIMIT ?""",
            (conversation_id, int(include_detached), limit),
        ).fetchall()
        return tuple(_binding_record(row) for row in rows)

    def put_context_binding(
        self,
        connection: ManagedConnection,
        value: ConversationContextBindingCreate,
    ) -> ConversationContextBindingCreateResult:
        connection = _connection(connection)
        if not isinstance(value, ConversationContextBindingCreate):
            raise TypeError("A ConversationContextBindingCreate is required.")
        binding_id = _uuid(value.binding_id, "binding_id")
        conversation_id = _uuid(value.conversation_id, "conversation_id")
        owner = _user(value.owner_user_id)
        binding_type = value.binding_type
        binding_mode = value.binding_mode
        if binding_type not in {
            "workspace_file", "project_context", "notion_page", "source_artifact"
        }:
            raise ValueError("binding_type is invalid.")
        if binding_mode not in {"snapshot", "live"}:
            raise ValueError("binding_mode is invalid.")
        external_key = _text(value.external_key, "external_key", 1000)
        if external_key != external_key.strip():
            raise ValueError("external_key must be trimmed.")
        source_artifact_id = (
            None
            if value.source_artifact_id is None
            else _uuid(value.source_artifact_id, "source_artifact_id")
        )
        snapshot_sha256 = (
            None
            if value.snapshot_sha256 is None
            else _sha(value.snapshot_sha256, "snapshot_sha256")
        )
        if binding_mode == "snapshot":
            if source_artifact_id is None or snapshot_sha256 is None:
                raise ValueError(
                    "A snapshot binding requires artifact and hash evidence."
                )
        elif source_artifact_id is not None or snapshot_sha256 is not None:
            raise ValueError("A live binding cannot carry snapshot evidence.")
        if binding_type == "source_artifact" and binding_mode != "snapshot":
            raise ValueError("A source-artifact binding must be a snapshot.")
        display_metadata, _ = _canonical_object(
            value.display_metadata_json, "display_metadata_json"
        )
        created = _utc_ms(value.created_at_ms)
        conversation = self.require_accessible(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner,
        )
        if conversation.status != "active":
            raise ConversationStateConflictError(
                "Only an active conversation can receive a context binding."
            )
        expected = (
            binding_id,
            conversation_id,
            binding_type,
            binding_mode,
            external_key,
            source_artifact_id,
            snapshot_sha256,
            display_metadata,
            "active",
            None,
            owner,
            created,
            created,
            None,
            1,
        )
        existing = connection.execute(
            f"""SELECT {','.join(_BINDING_COLUMNS)}
                FROM conversation_context_binding WHERE id=?""",
            (binding_id,),
        ).fetchone()
        identity = connection.execute(
            f"""SELECT {','.join(_BINDING_COLUMNS)}
                FROM conversation_context_binding
                WHERE conversation_id=? AND binding_type=? AND binding_mode=?
                  AND external_key=? AND status<>'detached'""",
            (conversation_id, binding_type, binding_mode, external_key),
        ).fetchone()
        if existing is not None:
            if (
                _row_tuple(existing, _BINDING_COLUMNS) != expected
                or identity is None
                or identity["id"] != binding_id
            ):
                raise ConversationStateConflictError(
                    "The context binding is already bound incompatibly."
                )
            return ConversationContextBindingCreateResult(
                _binding_record(existing), False
            )
        if identity is not None:
            identity_record = _binding_record(identity)
            if (
                identity_record.conversation_id != conversation_id
                or identity_record.binding_type != binding_type
                or identity_record.binding_mode != binding_mode
                or identity_record.external_key != external_key
                or identity_record.source_artifact_id != source_artifact_id
                or identity_record.snapshot_sha256 != snapshot_sha256
                or identity_record.display_metadata_json != display_metadata
                or identity_record.status != "active"
                or identity_record.status_reason is not None
                or identity_record.created_by != owner
                or identity_record.detached_at_ms is not None
            ):
                raise ConversationStateConflictError(
                    "The explicit context is already attached incompatibly."
                )
            return ConversationContextBindingCreateResult(identity_record, False)
        try:
            connection.execute(
                """INSERT INTO conversation_context_binding(
                     id,conversation_id,binding_type,binding_mode,external_key,
                     source_artifact_id,snapshot_sha256,display_metadata_json,
                     status,status_reason,created_by,created_at,status_changed_at,
                     detached_at,row_version
                   ) VALUES (?,?,?,?,?,?,?,?,'active',NULL,?,?,?,NULL,1)""",
                (
                    binding_id,
                    conversation_id,
                    binding_type,
                    binding_mode,
                    external_key,
                    source_artifact_id,
                    snapshot_sha256,
                    display_metadata,
                    owner,
                    created,
                    created,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConversationStateConflictError(
                "The context binding violates canonical constraints."
            ) from exc
        current = self.get_context_binding(connection, binding_id)
        if current is None:
            raise ConversationRepositoryError(
                "The inserted context binding cannot be read."
            )
        return ConversationContextBindingCreateResult(current, True)

    def detach_context_binding(
        self,
        connection: ManagedConnection,
        *,
        binding_id: str,
        conversation_id: str,
        owner_user_id: str,
        detached_at_ms: int,
        reason: str = "removed_by_owner",
    ) -> ConversationContextBindingDetachResult:
        connection = _connection(connection)
        binding_id = _uuid(binding_id, "binding_id")
        conversation_id = _uuid(conversation_id, "conversation_id")
        owner = _user(owner_user_id)
        detached_at = _utc_ms(detached_at_ms)
        reason = _text(reason, "reason", 1000)
        if reason != "removed_by_owner":
            raise ValueError("Runtime context detach reason must be removed_by_owner.")
        self.require_accessible(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner,
        )
        current = self.get_context_binding(connection, binding_id)
        if (
            current is None
            or current.conversation_id != conversation_id
            or current.created_by != owner
        ):
            raise ConversationNotFoundError(
                "The requested context binding does not exist."
            )
        if current.status == "detached":
            if current.status_reason != reason:
                raise ConversationStateConflictError(
                    "The context binding has an incompatible terminal reason."
                )
            return ConversationContextBindingDetachResult(current, False)
        if detached_at < current.status_changed_at_ms:
            raise ConversationStateConflictError(
                "The detach time precedes the current binding state."
            )
        try:
            cursor = connection.execute(
                """UPDATE conversation_context_binding
                   SET status='detached',status_reason=?,status_changed_at=?,
                       detached_at=?,row_version=row_version+1
                   WHERE id=? AND conversation_id=? AND status IN ('active','unavailable')
                     AND row_version=?""",
                (
                    reason,
                    detached_at,
                    detached_at,
                    binding_id,
                    conversation_id,
                    current.row_version,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConversationStateConflictError(
                "The context binding cannot be detached."
            ) from exc
        if cursor.rowcount != 1:
            raise ConversationVersionConflictError(
                "The context binding changed before detach."
            )
        detached = self.get_context_binding(connection, binding_id)
        if detached is None or detached.status != "detached":
            raise ConversationRepositoryError(
                "The detached context binding cannot be read."
            )
        return ConversationContextBindingDetachResult(detached, True)

    def set_live_context_binding_availability(
        self,
        connection: ManagedConnection,
        *,
        binding_id: str,
        conversation_id: str,
        owner_user_id: str,
        expected_row_version: int,
        available: bool,
        unavailable_reason: str | None,
        changed_at_ms: int,
    ) -> ConversationContextBindingAvailabilityResult:
        """Record one externally observed live-reference availability state.

        The external authorization/materialization call belongs outside SQLite.
        This method only compare-and-swaps the already observed state after the
        caller has revalidated the conversation and complete attached-binding
        set in its transaction.
        """

        connection = _connection(connection)
        binding_id = _uuid(binding_id, "binding_id")
        conversation_id = _uuid(conversation_id, "conversation_id")
        owner = _user(owner_user_id)
        expected_version = _positive(expected_row_version, "expected_row_version")
        changed_at = _utc_ms(changed_at_ms)
        if not isinstance(available, bool):
            raise ValueError("available must be a Boolean.")
        reason = unavailable_reason
        if available:
            if reason is not None:
                raise ValueError("An available live context cannot have a reason.")
            target_status: ConversationContextBindingStatus = "active"
        else:
            reason = _text(reason, "unavailable_reason", 1000)
            if reason not in {
                "access_revoked",
                "resolver_unavailable",
                "source_unavailable",
            }:
                raise ValueError("unavailable_reason is not a stable reason code.")
            target_status = "unavailable"
        self.require_accessible(
            connection,
            conversation_id=conversation_id,
            owner_user_id=owner,
        )
        current = self.get_context_binding(connection, binding_id)
        if (
            current is None
            or current.conversation_id != conversation_id
            or current.created_by != owner
            or current.binding_mode != "live"
            or current.status == "detached"
        ):
            raise ConversationNotFoundError(
                "The attached live context binding does not exist."
            )
        if current.row_version != expected_version:
            raise ConversationVersionConflictError(
                "The live context binding changed during authorization."
            )
        if current.status == target_status and current.status_reason == reason:
            return ConversationContextBindingAvailabilityResult(current, False)
        if changed_at < current.status_changed_at_ms:
            raise ConversationStateConflictError(
                "The live context observation precedes the stored state."
            )
        try:
            cursor = connection.execute(
                """UPDATE conversation_context_binding
                   SET status=?,status_reason=?,status_changed_at=?,
                       detached_at=NULL,row_version=row_version+1
                   WHERE id=? AND conversation_id=? AND binding_mode='live'
                     AND status IN ('active','unavailable') AND row_version=?""",
                (
                    target_status,
                    reason,
                    changed_at,
                    binding_id,
                    conversation_id,
                    expected_version,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConversationStateConflictError(
                "The live context availability cannot be recorded."
            ) from exc
        if cursor.rowcount != 1:
            raise ConversationVersionConflictError(
                "The live context binding changed during authorization."
            )
        updated = self.get_context_binding(connection, binding_id)
        if (
            updated is None
            or updated.status != target_status
            or updated.status_reason != reason
            or updated.row_version != expected_version + 1
        ):
            raise ConversationRepositoryError(
                "The updated live context binding cannot be verified."
            )
        return ConversationContextBindingAvailabilityResult(updated, True)

    def put_legacy_conversation(
        self,
        connection: ManagedConnection,
        value: LegacyConversation,
    ) -> LegacyConversationResult:
        connection = _connection(connection)
        if not isinstance(value, LegacyConversation):
            raise TypeError("A LegacyConversation is required.")
        plan = _sha(value.plan_sha256, "plan_sha256")
        record_id = _record(value.inventory_record_id)
        expected_id = legacy_conversation_id(plan, record_id)
        if _uuid(value.conversation_id, "conversation_id") != expected_id:
            raise ValueError("conversation_id does not match the reviewed session.")
        owner = _user(value.owner_user_id)
        space = _uuid(value.personal_space_id, "personal_space_id")
        epoch = _positive(value.captured_scope_epoch, "captured_scope_epoch")
        title = _text(value.title, "title", 200, allow_empty=True)
        metadata, _ = _canonical_object(value.metadata_json, "metadata_json")
        created = _utc_ms(value.created_at_ms)
        _owner_scope(
            connection,
            owner_user_id=owner,
            personal_space_id=space,
            captured_scope_epoch=epoch,
        )
        expected = (
            expected_id,
            owner,
            space,
            epoch,
            title,
            "archived",
            metadata,
            created,
            created,
            1,
        )
        existing = connection.execute(
            f"SELECT {','.join(_CONVERSATION_COLUMNS)} FROM conversation WHERE id=?",
            (expected_id,),
        ).fetchone()
        if existing is not None:
            if _row_tuple(existing, _CONVERSATION_COLUMNS) != expected:
                raise LegacyConversationConflictError(
                    "The legacy conversation is already bound incompatibly."
                )
            return LegacyConversationResult(expected_id, False)
        try:
            connection.execute(
                """INSERT INTO conversation(
                     id,owner_user_id,space_id,captured_scope_epoch,title,status,
                     metadata_json,created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,?,'archived',?,?,?,1)""",
                (expected_id, owner, space, epoch, title, metadata, created, created),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyConversationConflictError(
                "The legacy conversation violates canonical constraints."
            ) from exc
        return LegacyConversationResult(expected_id, True)

    def put_legacy_message(
        self,
        connection: ManagedConnection,
        value: LegacyConversationMessage,
    ) -> LegacyConversationMessageResult:
        connection = _connection(connection)
        if not isinstance(value, LegacyConversationMessage):
            raise TypeError("A LegacyConversationMessage is required.")
        plan = _sha(value.plan_sha256, "plan_sha256")
        record_id = _record(value.inventory_record_id)
        expected_id = legacy_conversation_message_id(plan, record_id)
        if _uuid(value.message_id, "message_id") != expected_id:
            raise ValueError("message_id does not match the reviewed child record.")
        conversation = _uuid(value.conversation_id, "conversation_id")
        owner = _user(value.owner_user_id)
        space = _uuid(value.personal_space_id, "personal_space_id")
        sequence = _positive(value.sequence_no, "sequence_no")
        if value.role not in {"system", "user", "assistant", "tool"}:
            raise ValueError("role is not a canonical conversation role.")
        content, parsed = _canonical_object(value.content_json, "content_json")
        if parsed.get("role") != value.role:
            raise ValueError("content_json role does not match the canonical role.")
        created = _utc_ms(value.created_at_ms)
        _conversation_scope(
            connection,
            conversation_id=conversation,
            owner_user_id=owner,
            personal_space_id=space,
        )
        expected = (expected_id, conversation, sequence, value.role, content, created)
        existing = connection.execute(
            f"SELECT {','.join(_MESSAGE_COLUMNS)} FROM conversation_message WHERE id=?",
            (expected_id,),
        ).fetchone()
        by_sequence = connection.execute(
            """SELECT id FROM conversation_message
               WHERE conversation_id=? AND sequence_no=?""",
            (conversation, sequence),
        ).fetchone()
        if existing is not None:
            if (
                _row_tuple(existing, _MESSAGE_COLUMNS) != expected
                or by_sequence is None
                or by_sequence["id"] != expected_id
            ):
                raise LegacyConversationConflictError(
                    "The legacy message is already bound incompatibly."
                )
            return LegacyConversationMessageResult(expected_id, False)
        if by_sequence is not None:
            raise LegacyConversationConflictError(
                "The legacy conversation sequence is already occupied."
            )
        previous = connection.execute(
            """SELECT max(sequence_no) AS maximum FROM conversation_message
               WHERE conversation_id=?""",
            (conversation,),
        ).fetchone()
        maximum = previous["maximum"] if previous is not None else None
        if sequence != (1 if maximum is None else int(maximum) + 1):
            raise LegacyConversationConflictError(
                "Legacy messages must be appended in contiguous sequence order."
            )
        try:
            connection.execute(
                """INSERT INTO conversation_message(
                     id,conversation_id,sequence_no,role,content_json,created_at
                   ) VALUES (?,?,?,?,?,?)""",
                (expected_id, conversation, sequence, value.role, content, created),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyConversationConflictError(
                "The legacy message violates canonical constraints."
            ) from exc
        return LegacyConversationMessageResult(expected_id, True)

    def put_legacy_summary(
        self,
        connection: ManagedConnection,
        value: LegacyConversationSummary,
    ) -> LegacyConversationSummaryResult:
        connection = _connection(connection)
        if not isinstance(value, LegacyConversationSummary):
            raise TypeError("A LegacyConversationSummary is required.")
        plan = _sha(value.plan_sha256, "plan_sha256")
        record_id = _record(value.inventory_record_id)
        expected_id = legacy_conversation_summary_id(plan, record_id)
        if _uuid(value.summary_id, "summary_id") != expected_id:
            raise ValueError("summary_id does not match the reviewed child record.")
        conversation = _uuid(value.conversation_id, "conversation_id")
        owner = _user(value.owner_user_id)
        space = _uuid(value.personal_space_id, "personal_space_id")
        covered = _positive(
            value.covered_through_sequence_no, "covered_through_sequence_no"
        )
        summary = _text(value.summary_md, "summary_md", _SQLITE_INTEGER_MAX)
        summary_sha = _sha(value.summary_sha256, "summary_sha256")
        if hashlib.sha256(summary.encode("utf-8")).hexdigest() != summary_sha:
            raise ValueError("summary_sha256 does not match UTF-8 summary_md.")
        created = _utc_ms(value.created_at_ms)
        _conversation_scope(
            connection,
            conversation_id=conversation,
            owner_user_id=owner,
            personal_space_id=space,
        )
        message = connection.execute(
            """SELECT 1 FROM conversation_message
               WHERE conversation_id=? AND sequence_no=?""",
            (conversation, covered),
        ).fetchone()
        if message is None:
            raise LegacyConversationConflictError(
                "The legacy summary does not cover an imported message."
            )
        expected = (
            expected_id,
            conversation,
            covered,
            summary,
            summary_sha,
            "legacy-import",
            "p2.5-v1",
            "p2.5-v1",
            "active",
            None,
            created,
            None,
            None,
            1,
        )
        existing = connection.execute(
            f"SELECT {','.join(_SUMMARY_COLUMNS)} FROM conversation_summary WHERE id=?",
            (expected_id,),
        ).fetchone()
        active = connection.execute(
            """SELECT id FROM conversation_summary
               WHERE conversation_id=? AND status='active'""",
            (conversation,),
        ).fetchone()
        if existing is not None:
            if (
                _row_tuple(existing, _SUMMARY_COLUMNS) != expected
                or active is None
                or active["id"] != expected_id
            ):
                raise LegacyConversationConflictError(
                    "The legacy summary is already bound incompatibly."
                )
            return LegacyConversationSummaryResult(expected_id, False)
        if active is not None:
            raise LegacyConversationConflictError(
                "The conversation already has a different active summary."
            )
        try:
            connection.execute(
                """INSERT INTO conversation_summary(
                     id,conversation_id,covered_through_sequence_no,summary_md,
                     summary_sha256,model_id,prompt_version,summarizer_version,
                     status,created_by_user_id,created_at,invalidated_at,
                     invalidation_reason,row_version
                   ) VALUES (?,?,?,?,?,'legacy-import','p2.5-v1','p2.5-v1',
                             'active',NULL,?,NULL,NULL,1)""",
                (expected_id, conversation, covered, summary, summary_sha, created),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyConversationConflictError(
                "The legacy summary violates canonical constraints."
            ) from exc
        return LegacyConversationSummaryResult(expected_id, True)

    def put_legacy_notion_binding(
        self,
        connection: ManagedConnection,
        value: LegacyNotionContextBinding,
    ) -> LegacyNotionContextBindingResult:
        connection = _connection(connection)
        if not isinstance(value, LegacyNotionContextBinding):
            raise TypeError("A LegacyNotionContextBinding is required.")
        plan = _sha(value.plan_sha256, "plan_sha256")
        relationship_id = _relationship(value.relationship_id)
        expected_id = legacy_notion_context_binding_id(plan, relationship_id)
        if _uuid(value.context_binding_id, "context_binding_id") != expected_id:
            raise ValueError(
                "context_binding_id does not match the reviewed relationship."
            )
        conversation = _uuid(value.conversation_id, "conversation_id")
        owner = _user(value.owner_user_id)
        space = _uuid(value.personal_space_id, "personal_space_id")
        external_key = _text(value.external_page_key, "external_page_key", 1000)
        if external_key != external_key.strip():
            raise ValueError("external_page_key cannot contain surrounding whitespace.")
        display, _ = _canonical_object(
            value.display_metadata_json, "display_metadata_json"
        )
        created = _utc_ms(value.created_at_ms)
        _conversation_scope(
            connection,
            conversation_id=conversation,
            owner_user_id=owner,
            personal_space_id=space,
        )
        expected = (
            expected_id,
            conversation,
            "notion_page",
            "live",
            external_key,
            None,
            None,
            display,
            "active",
            None,
            owner,
            created,
            created,
            None,
            1,
        )
        existing = connection.execute(
            f"SELECT {','.join(_BINDING_COLUMNS)} FROM conversation_context_binding WHERE id=?",
            (expected_id,),
        ).fetchone()
        identity = connection.execute(
            """SELECT id FROM conversation_context_binding
               WHERE conversation_id=? AND binding_type='notion_page'
                 AND binding_mode='live' AND external_key=?""",
            (conversation, external_key),
        ).fetchone()
        if existing is not None:
            if (
                _row_tuple(existing, _BINDING_COLUMNS) != expected
                or identity is None
                or identity["id"] != expected_id
            ):
                raise LegacyConversationConflictError(
                    "The legacy Notion binding is already bound incompatibly."
                )
            return LegacyNotionContextBindingResult(expected_id, False)
        if identity is not None:
            raise LegacyConversationConflictError(
                "The reviewed Notion page is already bound to this conversation."
            )
        try:
            connection.execute(
                """INSERT INTO conversation_context_binding(
                     id,conversation_id,binding_type,binding_mode,external_key,
                     source_artifact_id,snapshot_sha256,display_metadata_json,
                     status,status_reason,created_by,created_at,status_changed_at,
                     detached_at,row_version
                   ) VALUES (?,?,'notion_page','live',?,NULL,NULL,?,'active',
                             NULL,?,?,?,NULL,1)""",
                (expected_id, conversation, external_key, display, owner, created, created),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyConversationConflictError(
                "The legacy Notion binding violates canonical constraints."
            ) from exc
        return LegacyNotionContextBindingResult(expected_id, True)


__all__ = [
    "ConversationAccessStatus",
    "ConversationContextBindingAvailabilityResult",
    "ConversationContextBindingCreate",
    "ConversationContextBindingCreateResult",
    "ConversationContextBindingDetachResult",
    "ConversationContextBindingMode",
    "ConversationContextBindingRecord",
    "ConversationContextBindingStatus",
    "ConversationContextBindingType",
    "ConversationCreate",
    "ConversationCreateResult",
    "ConversationListEntry",
    "ConversationMessageRecord",
    "ConversationNotFoundError",
    "ConversationPinCreate",
    "ConversationPinCreateResult",
    "ConversationPinRecord",
    "ConversationPinRemoveResult",
    "ConversationRecord",
    "ConversationRepository",
    "ConversationRepositoryError",
    "ConversationScopeError",
    "ConversationStateConflictError",
    "ConversationStatus",
    "ConversationSummaryCreate",
    "ConversationSummaryCreateResult",
    "ConversationSummaryRecord",
    "ConversationSummaryStatus",
    "ConversationTurnAccept",
    "ConversationTurnAcceptResult",
    "ConversationTurnCompletion",
    "ConversationTurnCompletionResult",
    "ConversationTurnEventRecord",
    "ConversationTurnRecord",
    "ConversationTurnState",
    "ConversationTurnTermination",
    "ConversationTurnTerminationResult",
    "ConversationUpdateResult",
    "ConversationVersionConflictError",
    "LegacyConversation",
    "LegacyConversationConflictError",
    "LegacyConversationMessage",
    "LegacyConversationMessageResult",
    "LegacyConversationRepositoryError",
    "LegacyConversationResult",
    "LegacyConversationSummary",
    "LegacyConversationSummaryResult",
    "LegacyMessageRole",
    "LegacyNotionContextBinding",
    "LegacyNotionContextBindingResult",
    "legacy_conversation_id",
    "legacy_conversation_message_id",
    "legacy_conversation_summary_id",
    "legacy_notion_context_binding_id",
]

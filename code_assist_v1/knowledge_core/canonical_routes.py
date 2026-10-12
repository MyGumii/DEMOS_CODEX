"""Dependency-injected canonical Flask routes through the disabled P5.6 gate.

Importing this module registers nothing.  A host must explicitly supply the
already-authenticated server principal plus canonical services.  Request JSON
never supplies actor identity, role, scope epoch, or a conversation-space
override.  The production composition root remains flag-off until the full
P3.7 canary gate is accepted.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import math
import os
import re
from typing import Any, Protocol
from urllib.parse import quote
from uuid import uuid4

from flask import Blueprint, Flask, Response, current_app, jsonify, request


_LOGGER = logging.getLogger(__name__)


def _worker_generation_status(supervisors):
    """Public liveness of owned durable lanes, never process diagnostics.

    A down projector does not make document generation unavailable. Hosts
    without owned supervisors keep their existing external-worker contract.
    """
    lanes = []
    for supervisor in supervisors:
        try:
            value = supervisor.status()
            durable = [child for child in value.get("children", []) if child.get("kind") == "durable"]
            if not durable:
                if value.get("status") in {"stopped", "stopping"}:
                    lanes.append((False, False))
                continue
            automatic = value.get("automatic_recovery") is True and value.get("monitoring") is True
            active = value.get("status") not in {"stopping", "stopped"}
            healthy = active and all(child.get("alive") is True and child.get("ready") is True for child in durable)
            if value.get("automatic_recovery") is True and not automatic:
                healthy = False
            lanes.append((healthy, active and automatic))
        except Exception:
            # Health diagnostics must not break an authorized status read.
            lanes.append((False, False))
    if not lanes:
        return None
    unhealthy = [automatic for healthy, automatic in lanes if not healthy]
    return {"status": "healthy" if not unhealthy else "recovering" if all(unhealthy) else "unavailable",
        "automatic_recovery": any(automatic for _, automatic in lanes)}


def _with_generation_worker_health(document):
    if "wiki_generation" in document:
        health = _worker_generation_status(current_app.extensions.get("full_second_brain_preview_worker_supervisors", []))
        if health is not None:
            document["generation_worker"] = health
    return document

from .errors import KnowledgeCoreError
from .answer_response import ANSWER_API_FAILURE_CODES
from .repositories.conversations import ConversationNotFoundError, ConversationScopeError
from .repositories.publication import PublicationPreviewNotFoundError
from .repositories.publication_authority import HigherPublicationTargetDescriptor
from .services.agent_context import AgentTurnContextRequest
from .services.conversations import (
    AcceptConversationTurnCommand,
    AddConversationContextCommand,
    AddConversationPinCommand,
    CompleteConversationTurnCommand,
    CompactConversationCommand,
    ConversationAuthorizationError,
    ConversationCorpusClosureStaleError,
    ConversationIdempotencyError,
    ConversationOperationInProgressError,
    ConversationServiceError,
    ConversationServiceScopeError,
    ConversationServiceVersionError,
    ConversationSessionScopeStaleError,
    ConversationWikiArchivedHistoryOnlyError,
    CreateConversationCommand,
    RemoveConversationContextCommand,
    RemoveConversationPinCommand,
    TerminateConversationTurnCommand,
    UpdateConversationCommand,
)
from .services.retrieval import SpaceRetrievalRequest
from .services.source_artifacts import (
    ItemSourceArtifactDownloadRequest,
    ItemSourceArtifactRequest,
)
from .services.management_reads import (
    ItemManagementReadRequest,
    ItemRevisionCompareRequest,
    ItemRevisionPageRequest,
    ItemRevisionReadRequest,
    SpaceManagementPageRequest,
    SpaceManagementReadRequest,
)
from .services.editing import (
    AcquireLeaseCommand,
    ForceReleaseLeaseCommand,
    HeartbeatLeaseCommand,
    LeaseServiceError,
    ReleaseLeaseCommand,
    SaveItemCommand,
)
from .services.lifecycle import (
    DeactivateItemCommand,
    LifecycleServiceError,
    RestoreItemCommand,
)
from .services.jobs import CancelDurableJobCommand, ReadDurableJobRequest
from .services.publication import (
    CancelPublicationPreviewCommand,
    CreateChatPreviewCommand,
    CreateManualPreviewCommand,
    CreateNotionPreviewCommand,
    CreateUploadPreviewCommand,
    GetPublicationPreviewQuery,
    PublishPreviewCommand,
)
from .services.purge import (
    PurgeItemCommand,
    PurgePreviewCommand,
    PurgeServiceError,
)
from .services.scope_selection import ScopeSelectionRequest
from .services.curation_metrics import CurationShadowMetricsRequest
from .services.findings import ReadFindingRequest, ReviewFindingCommand
from .services.merges import MergeApplyCommand, MergePreviewCommand
from .services.proposals import (
    ProposalApplyCommand,
    ProposalPreviewCommand,
    ProposalRebaseCommand,
)
from .scope import SessionPrincipal


CANONICAL_ROUTE_PREFIX = "/api/knowledge/v1"
MAX_CANONICAL_JSON_BYTES = 262_144
MAX_CANONICAL_MARKDOWN_CHARS = 2_000_000
MAX_CANONICAL_PUBLICATION_JSON_BYTES = 8_065_536
MAX_CANONICAL_UPLOAD_BYTES = 64 * 1024 * 1024
_REGISTERED_ATTRIBUTE = "_knowledge_core_canonical_v1_registered"
_PUBLICATION_REGISTERED_ATTRIBUTE = "_knowledge_core_publication_v1_registered"
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_CURSOR_RE = re.compile(r"^[A-Za-z0-9_-]+$", re.ASCII)
_LEASE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$", re.ASCII)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_VARIABLE_RULE_RE = re.compile(r"<(?:(?:[^:<>]+):)?[^<>]+>", re.ASCII)
_MAX_INT = 9_223_372_036_854_775_807
_SSE_EVENT_TYPES = {
    "accepted": "accepted",
    "delta": "delta",
    "tool": "metadata",
    "completed": "completed",
    "failed": "failed",
    # The public v1 schema exposes one unsuccessful terminal event.  Preserve
    # the stored cancelled state in the payload while using that public type.
    "cancelled": "failed",
}


class _RequestError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        *,
        retryable: bool = False,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = status
        self.retryable = retryable
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class _Principal:
    user_id: str
    scope_epoch: int


@dataclass(frozen=True, slots=True)
class AgentTurnCompletion:
    assistant_content: Mapping[str, object]
    final_response: Mapping[str, object]


class AgentTurnExecutor(Protocol):
    """Generate one final answer outside canonical SQLite transactions."""

    def complete(self, context: object) -> AgentTurnCompletion: ...


@dataclass(frozen=True, slots=True)
class CompactSummaryCompletion:
    summary_md: str
    model_id: str
    prompt_version: str
    summarizer_version: str


class ConversationSummarizer(Protocol):
    """Generate one summary outside a canonical SQLite transaction."""

    def summarize(
        self,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        conversation_id: str,
        through_message_sequence: int,
    ) -> CompactSummaryCompletion: ...


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise _RequestError("VALIDATION_ERROR", f"{field} is invalid.", 400)
    return value


def _optional_uuid(value: object, field: str) -> str | None:
    return None if value is None else _uuid(value, field)


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _RequestError("VALIDATION_ERROR", f"{field} is invalid.", 400)
    return value


def _positive(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_INT:
        raise _RequestError("VALIDATION_ERROR", f"{field} is invalid.", 400)
    return value


def _nonnegative(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_INT:
        raise _RequestError("VALIDATION_ERROR", f"{field} is invalid.", 400)
    return value


def _ranked_temporal_value_json(value: object) -> object:
    if value is None:
        return None
    if value.precision == "instant":
        return {
            "instant_value_ms": value.instant_value_ms,
            "precision": value.precision,
            "source_timezone": value.source_timezone,
            "source_utc_offset_minutes": value.source_utc_offset_minutes,
        }
    return {
        "date_value": value.date_value,
        "precision": value.precision,
        "source_timezone": value.source_timezone,
    }


def _ranked_temporal_metadata_json(value: object) -> object:
    if value is None:
        return None
    return {
        "content_modified_at_ms": value.content_modified_at_ms,
        "information_effective": _ranked_temporal_value_json(
            value.information_effective
        ),
        "last_verified_at_ms": value.last_verified_at_ms,
        "registered_at_ms": value.registered_at_ms,
        "relative_state": value.relative_state,
        "selected_time_kind": value.selected_time_kind,
        "selected_time_ms": value.selected_time_ms,
        "source_modified": _ranked_temporal_value_json(value.source_modified),
        "temporal_summary_sha256": value.temporal_summary_sha256,
        "verification_current": value.verification_current,
    }


def _bounded_text(value: object, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (not allow_empty and not value)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise _RequestError("VALIDATION_ERROR", f"{field} is invalid.", 400)
    return value


def _message_text(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 200_000
        or any(ord(character) < 32 and character not in {"\n", "\r", "\t"} for character in value)
        or "\x7f" in value
    ):
        raise _RequestError("VALIDATION_ERROR", "message is invalid.", 400)
    return value


def _request_id() -> str:
    return str(uuid4())


def _request_meta() -> dict[str, object]:
    return {
        "host": request.host,
        "method": request.method,
        "path": request.path,
        "remote_addr": request.remote_addr,
        "user_agent": (request.user_agent.string or "")[:500],
    }


def _operation_id(body: Mapping[str, object]) -> str:
    operation_id = _uuid(body.get("operation_id"), "operation_id")
    aliases = request.headers.getlist("Idempotency-Key")
    if not aliases:
        return operation_id
    if len(aliases) != 1:
        raise _RequestError("VALIDATION_ERROR", "Idempotency-Key is invalid.", 400)
    alias = _uuid(aliases[0], "Idempotency-Key")
    if alias != operation_id:
        raise _RequestError(
            "IDEMPOTENCY_CONFLICT",
            "Idempotency-Key must match operation_id.",
            409,
        )
    return operation_id


def _single_header(name: str, *, required: bool = True) -> str | None:
    values = request.headers.getlist(name)
    if not values:
        if required:
            raise _RequestError("VALIDATION_ERROR", f"{name} is required.", 400)
        return None
    if len(values) != 1 or not isinstance(values[0], str) or not values[0]:
        raise _RequestError("VALIDATION_ERROR", f"{name} is invalid.", 400)
    return values[0]


def _lease_token_header(name: str) -> str:
    value = _single_header(name)
    assert value is not None
    if _LEASE_TOKEN_RE.fullmatch(value) is None:
        raise _RequestError("VALIDATION_ERROR", f"{name} is invalid.", 400)
    return value


def _lease_token() -> str:
    return _lease_token_header("X-Knowledge-Lease-Token")


def _fencing_generation_header(name: str) -> int:
    value = _single_header(name)
    assert value is not None
    if re.fullmatch(r"[1-9][0-9]{0,18}", value, re.ASCII) is None:
        raise _RequestError("VALIDATION_ERROR", f"{name} is invalid.", 400)
    return _positive(int(value), name)


def _fencing_generation() -> int:
    return _fencing_generation_header("X-Knowledge-Fence")


def _validate_if_match(expected_revision_id: str) -> None:
    value = _single_header("If-Match", required=False)
    if value is not None and value != f'"{expected_revision_id}"':
        raise _RequestError(
            "REVISION_CONFLICT",
            "If-Match must equal expected_revision_id.",
            409,
            details={
                "draft_recovery": {
                    "actions": [
                        "view_current",
                        "copy_draft",
                        "reacquire_and_compare",
                    ],
                    "force_overwrite_allowed": False,
                    "preserve_local_draft": True,
                }
            },
        )


def _encode_conversation_cursor(updated_at_ms: int, conversation_id: str) -> str:
    payload = json.dumps(
        {
            "conversation_id": _uuid(conversation_id, "conversation_id"),
            "updated_at_ms": _nonnegative(updated_at_ms, "updated_at_ms"),
            "version": 1,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_conversation_cursor(value: str) -> tuple[int, str]:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or _CURSOR_RE.fullmatch(value) is None
    ):
        raise _RequestError("VALIDATION_ERROR", "cursor is invalid.", 400)
    try:
        raw = base64.b64decode(
            value + ("=" * (-len(value) % 4)),
            altchars=b"-_",
            validate=True,
        )
        decoded = json.loads(raw.decode("ascii", errors="strict"))
    except (UnicodeDecodeError, ValueError, binascii.Error, json.JSONDecodeError) as exc:
        raise _RequestError("VALIDATION_ERROR", "cursor is invalid.", 400) from exc
    if (
        not isinstance(decoded, dict)
        or frozenset(decoded) != {"conversation_id", "updated_at_ms", "version"}
        or decoded.get("version") != 1
    ):
        raise _RequestError("VALIDATION_ERROR", "cursor is invalid.", 400)
    updated_at_ms = _nonnegative(decoded.get("updated_at_ms"), "cursor.updated_at_ms")
    conversation_id = _uuid(decoded.get("conversation_id"), "cursor.conversation_id")
    if _encode_conversation_cursor(updated_at_ms, conversation_id) != value:
        raise _RequestError("VALIDATION_ERROR", "cursor is invalid.", 400)
    return updated_at_ms, conversation_id


def _conversation_list_options() -> tuple[int, int | None, str | None]:
    allowed = {"cursor", "limit"}
    if set(request.args) - allowed or any(
        len(request.args.getlist(key)) != 1 for key in request.args
    ):
        raise _RequestError("VALIDATION_ERROR", "The query string is invalid.", 400)
    limit_text = request.args.get("limit")
    if limit_text is None:
        limit = 50
    elif re.fullmatch(r"[1-9][0-9]{0,2}", limit_text, re.ASCII) is None:
        raise _RequestError("VALIDATION_ERROR", "limit is invalid.", 400)
    else:
        limit = int(limit_text)
        if limit > 100:
            raise _RequestError("VALIDATION_ERROR", "limit is invalid.", 400)
    cursor = request.args.get("cursor")
    if cursor is None:
        return limit, None, None
    before_updated_at_ms, before_conversation_id = _decode_conversation_cursor(cursor)
    return limit, before_updated_at_ms, before_conversation_id


def _encode_management_cursor(kind: str, updated_at_ms: int, record_id: str) -> str:
    if kind not in {"item", "finding", "activity", "revision"}:
        raise TypeError("Management cursor kind is invalid.")
    payload = json.dumps(
        {
            "id": _uuid(record_id, "record_id"),
            "kind": kind,
            "updated_at_ms": _nonnegative(updated_at_ms, "updated_at_ms"),
            "version": 1,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_management_cursor(value: str, kind: str) -> tuple[int, str]:
    if (
        kind not in {"item", "finding", "activity", "revision"}
        or not isinstance(value, str)
        or not value
        or len(value) > 4096
        or _CURSOR_RE.fullmatch(value) is None
    ):
        raise _RequestError("VALIDATION_ERROR", "cursor is invalid.", 400)
    try:
        raw = base64.b64decode(
            value + ("=" * (-len(value) % 4)),
            altchars=b"-_",
            validate=True,
        )
        decoded = json.loads(raw.decode("ascii", errors="strict"))
    except (UnicodeDecodeError, ValueError, binascii.Error, json.JSONDecodeError) as exc:
        raise _RequestError("VALIDATION_ERROR", "cursor is invalid.", 400) from exc
    if (
        not isinstance(decoded, dict)
        or frozenset(decoded) != {"id", "kind", "updated_at_ms", "version"}
        or decoded.get("kind") != kind
        or decoded.get("version") != 1
    ):
        raise _RequestError("VALIDATION_ERROR", "cursor is invalid.", 400)
    updated_at_ms = _nonnegative(decoded.get("updated_at_ms"), "cursor.updated_at_ms")
    record_id = _uuid(decoded.get("id"), "cursor.id")
    if _encode_management_cursor(kind, updated_at_ms, record_id) != value:
        raise _RequestError("VALIDATION_ERROR", "cursor is invalid.", 400)
    return updated_at_ms, record_id


def _management_list_options(kind: str) -> tuple[int, int | None, str | None]:
    allowed = {"cursor", "limit"}
    if set(request.args) - allowed or any(
        len(request.args.getlist(key)) != 1 for key in request.args
    ):
        raise _RequestError("VALIDATION_ERROR", "The query string is invalid.", 400)
    limit_text = request.args.get("limit")
    if limit_text is None:
        limit = 50
    elif re.fullmatch(r"[1-9][0-9]{0,2}", limit_text, re.ASCII) is None:
        raise _RequestError("VALIDATION_ERROR", "limit is invalid.", 400)
    else:
        limit = int(limit_text)
        if limit > 100:
            raise _RequestError("VALIDATION_ERROR", "limit is invalid.", 400)
    cursor = request.args.get("cursor")
    if cursor is None:
        return limit, None, None
    before_updated_at_ms, before_id = _decode_management_cursor(cursor, kind)
    return limit, before_updated_at_ms, before_id


def _require_empty_query() -> None:
    if request.args:
        raise _RequestError("VALIDATION_ERROR", "The query string is invalid.", 400)


def _scope_selection_options() -> tuple[str | None, str | None]:
    allowed = {"conversation_id", "selected_space_id"}
    if set(request.args) - allowed or any(
        len(request.args.getlist(key)) != 1 for key in request.args
    ):
        raise _RequestError("VALIDATION_ERROR", "The query string is invalid.", 400)
    selected_space_id = request.args.get("selected_space_id")
    conversation_id = request.args.get("conversation_id")
    if selected_space_id is not None and conversation_id is not None:
        raise _RequestError(
            "VALIDATION_ERROR",
            "Select either a requested space or an existing conversation.",
            400,
        )
    return (
        None
        if selected_space_id is None
        else _uuid(selected_space_id, "selected_space_id"),
        None
        if conversation_id is None
        else _uuid(conversation_id, "conversation_id"),
    )


def _compare_options() -> tuple[str, str]:
    if set(request.args) != {"from", "to"} or any(
        len(request.args.getlist(key)) != 1 for key in request.args
    ):
        raise _RequestError("VALIDATION_ERROR", "The query string is invalid.", 400)
    return (
        _uuid(request.args.get("from"), "from"),
        _uuid(request.args.get("to"), "to"),
    )


def _principal(resolver: Callable[[], object]) -> _Principal:
    value = resolver()
    if value is None:
        raise _RequestError(
            "AUTHENTICATION_REQUIRED",
            "A valid canonical account session is required.",
            401,
        )
    if isinstance(value, Mapping):
        user_id = value.get("id")
        epoch = value.get("scope_epoch")
    else:
        user_id = getattr(value, "id", None)
        epoch = getattr(value, "scope_epoch", None)
    if not isinstance(user_id, str) or _USER_RE.fullmatch(user_id) is None:
        raise _RequestError(
            "AUTHENTICATION_REQUIRED",
            "The canonical account session principal is invalid.",
            401,
        )
    try:
        scope_epoch = _positive(epoch, "principal.scope_epoch")
    except _RequestError as exc:
        raise _RequestError(
            "AUTHENTICATION_REQUIRED",
            "The canonical account session principal is invalid.",
            401,
        ) from exc
    return _Principal(user_id, scope_epoch)


def _read_json(
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    maximum_bytes: int = MAX_CANONICAL_JSON_BYTES,
) -> dict[str, object]:
    if (
        isinstance(maximum_bytes, bool)
        or not isinstance(maximum_bytes, int)
        or maximum_bytes < 1
        or maximum_bytes > MAX_CANONICAL_PUBLICATION_JSON_BYTES
    ):
        raise TypeError("maximum_bytes is invalid.")
    content_length = request.content_length
    if content_length is not None and content_length > maximum_bytes:
        raise _RequestError("VALIDATION_ERROR", "The JSON request is too large.", 400)
    if request.mimetype != "application/json":
        raise _RequestError("VALIDATION_ERROR", "application/json is required.", 400)
    raw = request.get_data(cache=False, as_text=False)
    if len(raw) > maximum_bytes or raw.startswith(b"\xef\xbb\xbf"):
        raise _RequestError("VALIDATION_ERROR", "The JSON request is invalid.", 400)

    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = item
        return result

    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=reject_duplicate,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise _RequestError("VALIDATION_ERROR", "The JSON request is invalid.", 400) from exc
    if not isinstance(value, dict) or frozenset(value) - required - optional or not required <= frozenset(value):
        raise _RequestError("VALIDATION_ERROR", "The JSON request shape is invalid.", 400)
    # JSON decoding can produce non-finite floats; reject them and bound depth/nodes.
    nodes = 0
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > 4096 or depth > 12:
            raise _RequestError("VALIDATION_ERROR", "The JSON request is too complex.", 400)
        if isinstance(current, float) and not math.isfinite(current):
            raise _RequestError("VALIDATION_ERROR", "The JSON request contains a non-finite number.", 400)
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    return value


def _publication_markdown(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_CANONICAL_MARKDOWN_CHARS
        or any(
            ord(character) < 32 and character not in {"\n", "\r", "\t"}
            for character in value
        )
        or "\x7f" in value
    ):
        raise _RequestError("VALIDATION_ERROR", "markdown is invalid.", 400)
    return value


def _publication_title(value: object) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, "title", 500)


def _higher_publication_target(value: object) -> HigherPublicationTargetDescriptor:
    if not isinstance(value, Mapping) or set(value) != {
        "capability",
        "kind",
        "role_assignment_generation",
        "role_assignment_id",
        "space_id",
    }:
        raise _RequestError(
            "VALIDATION_ERROR", "The publication target is invalid.", 400
        )
    kind = value.get("kind")
    if kind not in {"team", "responsibility"} or value.get("capability") != "publish":
        raise _RequestError(
            "VALIDATION_ERROR", "The publication target is invalid.", 400
        )
    return HigherPublicationTargetDescriptor(
        space_id=_uuid(value.get("space_id"), "target.space_id"),
        kind=kind,
        capability="publish",
        role_assignment_id=_uuid(
            value.get("role_assignment_id"), "target.role_assignment_id"
        ),
        role_assignment_generation=_positive(
            value.get("role_assignment_generation"),
            "target.role_assignment_generation",
        ),
    )


def _publication_result(value: object) -> dict[str, object]:
    operation_id = getattr(value, "operation_id", None)
    request_id = getattr(value, "request_id", None)
    targets = getattr(value, "targets", None)
    if (
        not isinstance(operation_id, str)
        or not isinstance(request_id, str)
        or not isinstance(targets, tuple)
        or not 1 <= len(targets) <= 2
        or any(not isinstance(target, Mapping) for target in targets)
    ):
        raise TypeError("The canonical publication result is invalid.")
    result = {
        "operation_id": operation_id,
        "request_id": request_id,
        "results": [dict(target) for target in targets],
    }
    # Internal selection IDs are replay metadata, not public source handles.
    if getattr(value, "body_source", None) is not None:
        if value.body_source not in {"authored_wiki", "extraction", "preserved_wiki", "native_wiki", "pptx_role_wiki", "pdf_wiki", "pdf_visual_wiki", "office_hybrid_wiki"}:
            raise TypeError("The canonical publication source kind is invalid.")
        result["body_source"] = value.body_source
    elif getattr(value, "authored_snapshot_id", None) is not None:
        result["body_source"] = "authored_wiki"
    elif getattr(value, "reused_extraction_snapshot_id", None) is not None:
        result["body_source"] = "extraction"
    return result


def _as_error(exc: Exception) -> tuple[dict[str, object], int]:
    if isinstance(exc, _RequestError):
        return (
            {
                "code": exc.code,
                "details": exc.details,
                "message": str(exc),
                "retryable": exc.retryable,
            },
            exc.http_status,
        )
    code = getattr(exc, "code", None)
    status = getattr(exc, "http_status", None)
    retryable = bool(getattr(exc, "retryable", False))
    if isinstance(exc, (ValueError, TypeError)):
        return (
            {
                "code": "VALIDATION_ERROR",
                "details": {},
                "message": "The request is invalid.",
                "retryable": False,
            },
            400,
        )
    if isinstance(exc, (ConversationNotFoundError, PublicationPreviewNotFoundError)):
        return (
            {
                "code": "NOT_FOUND",
                "details": {},
                "message": "The requested resource was not found.",
                "retryable": False,
            },
            404,
        )
    if isinstance(exc, ConversationScopeError):
        return (
            {
                "code": "SCOPE_STALE",
                "details": {},
                "message": "The conversation scope is no longer accessible.",
                "retryable": False,
            },
            409,
        )
    service_error: tuple[str, int, bool] | None = None
    if isinstance(exc, ConversationAuthorizationError):
        service_error = ("FORBIDDEN", 403, False)
    elif isinstance(exc, ConversationWikiArchivedHistoryOnlyError):
        service_error = ("WIKI_ARCHIVED_HISTORY_ONLY", 409, False)
    elif isinstance(exc, ConversationCorpusClosureStaleError):
        service_error = ("CORPUS_CLOSURE_STALE", 409, False)
    elif isinstance(exc, (ConversationSessionScopeStaleError, ConversationServiceScopeError)):
        service_error = ("SCOPE_STALE", 409, False)
    elif isinstance(exc, ConversationServiceVersionError):
        service_error = ("REVISION_CONFLICT", 409, False)
    elif isinstance(exc, ConversationIdempotencyError):
        service_error = ("IDEMPOTENCY_CONFLICT", 409, False)
    elif isinstance(exc, ConversationOperationInProgressError):
        service_error = ("STATE_CONFLICT", 409, True)
    elif isinstance(exc, ConversationServiceError):
        service_error = ("STATE_CONFLICT", 409, False)
    if service_error is not None:
        stable_code, stable_status, stable_retryable = service_error
        return (
            {
                "code": stable_code,
                "details": {},
                "message": str(exc),
                "retryable": stable_retryable,
            },
            stable_status,
        )
    if isinstance(
        exc, (LeaseServiceError, LifecycleServiceError, PurgeServiceError)
    ):
        return (exc.as_error(), exc.http_status)
    if isinstance(exc, KnowledgeCoreError) and isinstance(code, str) and isinstance(status, int):
        return (
            {
                "code": code,
                "details": {},
                "message": str(exc),
                "retryable": retryable,
            },
            status,
        )
    return (
        {
            "code": "INTERNAL_ERROR",
            "details": {"state": "internal_error"},
            "message": "The canonical knowledge request could not be completed.",
            "retryable": True,
        },
        500,
    )


def _conversation_message_payload(value):
    return {"id": value.id, "sequence_no": value.sequence_no, "role": value.role,
            "content": value.content, "created_at": _utc(value.created_at_ms)}


def _json_response(payload: Mapping[str, object], request_id: str, status: int = 200):
    value = {"request_id": request_id, **dict(payload)}
    response = jsonify(value)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Request-ID"] = request_id
    return response, status


def _error_response(exc: Exception, request_id: str):
    error, status = _as_error(exc)
    return _json_response({"error": error}, request_id, status)


def _utc(milliseconds: int) -> str:
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, int) or milliseconds < 0:
        raise TypeError("Canonical timestamp is invalid.")
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _conversation_display_title(stored: object, first_user_message: object = None) -> str:
    if isinstance(stored, str) and stored:
        return stored
    if isinstance(first_user_message, str):
        normalized = " ".join(first_user_message.split())
        if normalized:
            return normalized if len(normalized) <= 80 else normalized[:79].rstrip() + "…"
    return "새 대화"


def _conversation_message_title_text(message: object) -> str | None:
    if getattr(message, "role", None) != "user":
        return None
    content = getattr(message, "content", None)
    if not isinstance(content, Mapping):
        return None
    payload = content.get("content")
    if isinstance(payload, Mapping) and isinstance(payload.get("text"), str):
        return payload["text"]
    return payload if isinstance(payload, str) else None


def _conversation_summary(
    record: object,
    badge: object,
    *,
    first_user_message: object = None,
) -> dict[str, object]:
    badge_value = badge.as_dict()
    value: dict[str, object] = {
        "accessible": bool(badge_value["accessible"]),
        "archived": getattr(record, "status") == "archived",
        "created_at": _utc(getattr(record, "created_at_ms")),
        "id": getattr(record, "id"),
        "row_version": getattr(record, "row_version"),
        "space": {
            "capabilities": [],
            "id": badge_value["space_id"],
            "kind": badge_value["space_kind"],
            "name": badge_value["space_name"],
            "org_path": badge_value["org_path"],
        },
        "title": _conversation_display_title(
            getattr(record, "title"), first_user_message
        ),
        "updated_at": _utc(getattr(record, "updated_at_ms")),
    }
    if "history_only" in badge_value:
        value.update(
            {
                "access_status": badge_value["access_status"],
                "history_only": badge_value["history_only"],
                "selected_view_space_id": badge_value[
                    "selected_view_space_id"
                ],
                "wiki_lifecycle_generation": badge_value[
                    "wiki_lifecycle_generation"
                ],
            }
        )
    return value


def _revision_payload(
    revision: object, *, markdown: str | None = None, include_markdown: bool = False
) -> dict[str, object]:
    payload: dict[str, object] = {
        "base_revision_id": getattr(revision, "base_revision_id"),
        "body_present": bool(getattr(revision, "body_present")),
        "change_kind": getattr(revision, "change_kind"),
        "content_sha256": getattr(revision, "content_sha256"),
        "content_size_bytes": getattr(revision, "content_size_bytes"),
        "created_at": _utc(getattr(revision, "created_at_ms")),
        "created_by": getattr(revision, "created_by_user_id"),
        "created_by_display": getattr(revision, "created_by_display_name"),
        "id": getattr(revision, "id"),
        "item_id": getattr(revision, "item_id"),
        "normalized_content_sha256": getattr(
            revision, "normalized_content_sha256"
        ),
        "normalizer_version": getattr(revision, "normalizer_version"),
        "parent_revision_id": getattr(revision, "parent_revision_id"),
        "reason": getattr(revision, "reason"),
        "revision_no": getattr(revision, "revision_no"),
        "title": getattr(revision, "title"),
    }
    if include_markdown:
        payload["markdown"] = markdown
    return payload


def _edge_counts_payload(value: object) -> dict[str, object]:
    return {
        "active_incoming": getattr(value, "active_incoming"),
        "active_outgoing": getattr(value, "active_outgoing"),
        "inactive_incoming": getattr(value, "inactive_incoming"),
        "inactive_outgoing": getattr(value, "inactive_outgoing"),
        "total": getattr(value, "total"),
    }


def _reference_counts_payload(value: object) -> dict[str, object]:
    return {
        "active_incoming": getattr(value, "active_incoming"),
        "active_outgoing": getattr(value, "active_outgoing"),
        "conversation_pins": getattr(value, "conversation_pins"),
        "inactive_incoming": getattr(value, "inactive_incoming"),
        "inactive_outgoing": getattr(value, "inactive_outgoing"),
        "total": getattr(value, "total"),
    }


def _sse_event(*, event_type: str, event_sequence: int, payload: Mapping[str, object], result: object) -> str:
    body = {
        "event_sequence": event_sequence,
        "event_type": event_type,
        "operation_id": getattr(result, "operation_id"),
        "payload": dict(payload),
        "request_id": getattr(result, "request_id"),
        "terminal": event_type in {"completed", "failed"},
        "turn_id": getattr(result, "turn_id"),
    }
    return f"id: {event_sequence}\nevent: {event_type}\ndata: {json.dumps(body, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _sse_chunks(result: object, events: object) -> list[str]:
    chunks: list[str] = []
    for event in events:
        internal_type = getattr(event, "event_type", None)
        public_type = _SSE_EVENT_TYPES.get(internal_type)
        if public_type is None:
            raise TypeError("Stored conversation turn event type is invalid.")
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            raise TypeError("Stored conversation turn event payload is invalid.")
        chunks.append(
            _sse_event(
                event_type=public_type,
                event_sequence=getattr(event, "sequence_no"),
                payload=payload,
                result=result,
            )
        )
    return chunks


class _Handlers:
    def __init__(
        self,
        *,
        scope_selection: object,
        conversations: object,
        retrieval: object,
        management_reads: object,
        agent_context: object,
        turn_executor: AgentTurnExecutor,
        conversation_summarizer: ConversationSummarizer,
        principal_resolver: Callable[[], object],
        publication: object | None = None,
        publication_status_reader: object | None = None,
        item_editing: object | None = None,
        item_lifecycle: object | None = None,
        item_purge: object | None = None,
        curation: object | None = None,
        jobs: object | None = None,
        source_artifacts: object | None = None,
    ) -> None:
        self.scope_selection = scope_selection
        self.conversations = conversations
        self.retrieval = retrieval
        self.management_reads = management_reads
        self.agent_context = agent_context
        self.turn_executor = turn_executor
        self.conversation_summarizer = conversation_summarizer
        self.principal_resolver = principal_resolver
        self.publication = publication
        self.publication_status_reader = publication_status_reader
        self.item_editing = item_editing
        self.item_lifecycle = item_lifecycle
        self.item_purge = item_purge
        self.curation = curation
        self.jobs = jobs
        self.source_artifacts = source_artifacts

    def run(self, callback: Callable[[str, _Principal], object]):
        request_id = _request_id()
        try:
            return callback(request_id, _principal(self.principal_resolver))
        except Exception as exc:
            return _error_response(exc, request_id)

    @staticmethod
    def _session_principal(principal: _Principal) -> SessionPrincipal:
        return SessionPrincipal(
            user_id=principal.user_id,
            captured_scope_epoch=principal.scope_epoch,
        )

    def acquire_item_lease(self, space_id: str, item_id: str):
        def execute(rid: str, principal: _Principal):
            _require_empty_query()
            service = self.item_editing
            if service is None:
                raise RuntimeError("The canonical item-edit service is unavailable.")
            body = _read_json(
                required=frozenset({"editor_session_id", "operation_id"})
            )
            result = service.acquire(
                AcquireLeaseCommand(
                    principal=self._session_principal(principal),
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    editor_session_id=_uuid(
                        body.get("editor_session_id"), "editor_session_id"
                    ),
                    operation_id=_operation_id(body),
                    request_id=rid,
                    raw_token=_lease_token(),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), rid, 201)

        return self.run(execute)

    def heartbeat_item_lease(self, space_id: str, item_id: str):
        def execute(rid: str, principal: _Principal):
            _require_empty_query()
            service = self.item_editing
            if service is None:
                raise RuntimeError("The canonical item-edit service is unavailable.")
            body = _read_json(
                required=frozenset({"editor_session_id", "lease_id"})
            )
            result = service.heartbeat(
                HeartbeatLeaseCommand(
                    principal=self._session_principal(principal),
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    editor_session_id=_uuid(
                        body.get("editor_session_id"), "editor_session_id"
                    ),
                    lease_id=_uuid(body.get("lease_id"), "lease_id"),
                    fencing_generation=_fencing_generation(),
                    request_id=rid,
                    raw_token=_lease_token(),
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def release_item_lease(self, space_id: str, item_id: str):
        def execute(rid: str, principal: _Principal):
            _require_empty_query()
            service = self.item_editing
            if service is None:
                raise RuntimeError("The canonical item-edit service is unavailable.")
            body = _read_json(
                required=frozenset(
                    {"editor_session_id", "lease_id", "operation_id"}
                )
            )
            result = service.release(
                ReleaseLeaseCommand(
                    principal=self._session_principal(principal),
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    editor_session_id=_uuid(
                        body.get("editor_session_id"), "editor_session_id"
                    ),
                    lease_id=_uuid(body.get("lease_id"), "lease_id"),
                    fencing_generation=_fencing_generation(),
                    operation_id=_operation_id(body),
                    request_id=rid,
                    raw_token=_lease_token(),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def force_release_item_lease(self, space_id: str, item_id: str):
        def execute(rid: str, principal: _Principal):
            _require_empty_query()
            service = self.item_editing
            if service is None:
                raise RuntimeError("The canonical item-edit service is unavailable.")
            body = _read_json(
                required=frozenset({"confirmation", "operation_id", "reason"})
            )
            if body.get("confirmation") != "FORCE_RELEASE":
                raise _RequestError(
                    "VALIDATION_ERROR", "Force-release confirmation is invalid.", 400
                )
            result = service.force_release(
                ForceReleaseLeaseCommand(
                    principal=self._session_principal(principal),
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    operation_id=_operation_id(body),
                    request_id=rid,
                    reason=_bounded_text(body.get("reason"), "reason", 2000),
                    confirmation="FORCE_RELEASE",
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def update_item(self, space_id: str, item_id: str):
        def execute(rid: str, principal: _Principal):
            _require_empty_query()
            service = self.item_editing
            if service is None:
                raise RuntimeError("The canonical item-edit service is unavailable.")
            body = _read_json(
                required=frozenset(
                    {
                        "editor_session_id",
                        "expected_revision_id",
                        "expected_row_version",
                        "lease_id",
                        "markdown",
                        "operation_id",
                        "reason",
                    }
                ),
                maximum_bytes=MAX_CANONICAL_PUBLICATION_JSON_BYTES,
            )
            expected_revision_id = _uuid(
                body.get("expected_revision_id"), "expected_revision_id"
            )
            _validate_if_match(expected_revision_id)
            result = service.save(
                SaveItemCommand(
                    principal=self._session_principal(principal),
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    editor_session_id=_uuid(
                        body.get("editor_session_id"), "editor_session_id"
                    ),
                    lease_id=_uuid(body.get("lease_id"), "lease_id"),
                    fencing_generation=_fencing_generation(),
                    expected_item_row_version=_positive(
                        body.get("expected_row_version"), "expected_row_version"
                    ),
                    expected_revision_id=expected_revision_id,
                    operation_id=_operation_id(body),
                    request_id=rid,
                    markdown=_publication_markdown(body.get("markdown")),
                    reason=_bounded_text(body.get("reason"), "reason", 2000),
                    raw_token=_lease_token(),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def deactivate_item(self, space_id: str, item_id: str):
        def execute(rid: str, principal: _Principal):
            _require_empty_query()
            service = self.item_lifecycle
            if service is None:
                raise RuntimeError(
                    "The canonical item-lifecycle service is unavailable."
                )
            body = _read_json(
                required=frozenset(
                    {"expected_row_version", "operation_id", "reason"}
                )
            )
            result = service.deactivate(
                DeactivateItemCommand(
                    principal=self._session_principal(principal),
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    expected_item_row_version=_positive(
                        body.get("expected_row_version"), "expected_row_version"
                    ),
                    operation_id=_operation_id(body),
                    request_id=rid,
                    reason=_bounded_text(body.get("reason"), "reason", 2000),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def restore_item(self, space_id: str, item_id: str):
        def execute(rid: str, principal: _Principal):
            _require_empty_query()
            service = self.item_lifecycle
            if service is None:
                raise RuntimeError(
                    "The canonical item-lifecycle service is unavailable."
                )
            body = _read_json(
                required=frozenset({"expected_row_version", "operation_id"})
            )
            result = service.restore(
                RestoreItemCommand(
                    principal=self._session_principal(principal),
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    expected_item_row_version=_positive(
                        body.get("expected_row_version"), "expected_row_version"
                    ),
                    operation_id=_operation_id(body),
                    request_id=rid,
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def preview_item_purge(self, space_id: str, item_id: str):
        def execute(rid: str, principal: _Principal):
            _require_empty_query()
            service = self.item_purge
            if service is None:
                raise RuntimeError("The canonical item-purge service is unavailable.")
            result = service.preview(
                PurgePreviewCommand(
                    principal=self._session_principal(principal),
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    request_id=rid,
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def purge_item(self, space_id: str, item_id: str):
        def execute(rid: str, principal: _Principal):
            _require_empty_query()
            service = self.item_purge
            if service is None:
                raise RuntimeError("The canonical item-purge service is unavailable.")
            body = _read_json(
                required=frozenset(
                    {
                        "confirmation",
                        "expected_row_version",
                        "operation_id",
                        "preview_token",
                    }
                )
            )
            result = service.purge(
                PurgeItemCommand(
                    principal=self._session_principal(principal),
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    expected_item_row_version=_positive(
                        body.get("expected_row_version"), "expected_row_version"
                    ),
                    operation_id=_operation_id(body),
                    request_id=rid,
                    preview_token=_bounded_text(
                        body.get("preview_token"), "preview_token", 4096
                    ),
                    confirmation=_bounded_text(
                        body.get("confirmation"), "confirmation", 200
                    ),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def create_publication_preview(self):
        def execute(rid: str, principal: _Principal):
            service = self.publication
            if service is None:
                raise RuntimeError("The canonical publication service is unavailable.")
            if request.mimetype == "multipart/form-data":
                allowed_form = {"operation_id", "source_type", "target", "title"}
                if (
                    set(request.form) - allowed_form
                    or set(request.files) != {"file"}
                    or set(request.form) < {"operation_id", "source_type"}
                    or any(len(request.form.getlist(key)) != 1 for key in request.form)
                    or len(request.files.getlist("file")) != 1
                    or request.form.get("source_type") != "upload"
                ):
                    raise _RequestError(
                        "VALIDATION_ERROR", "The upload request shape is invalid.", 400
                    )
                operation_id = _operation_id(request.form)
                upload = request.files["file"]
                filename = _bounded_text(
                    upload.filename, "file.filename", 500
                )
                content = upload.stream.read(MAX_CANONICAL_UPLOAD_BYTES + 1)
                if len(content) > MAX_CANONICAL_UPLOAD_BYTES:
                    raise _RequestError(
                        "VALIDATION_ERROR", "The upload is too large.", 400
                    )
                media_type = upload.mimetype or "application/octet-stream"
                raw_target = request.form.get("target")
                target = None
                if raw_target is not None:
                    try:
                        decoded_target = json.loads(raw_target)
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        raise _RequestError(
                            "VALIDATION_ERROR",
                            "The publication target is invalid.",
                            400,
                        ) from exc
                    target = _higher_publication_target(decoded_target)
                result = service.create_upload_preview(
                    CreateUploadPreviewCommand(
                        request_id=rid,
                        operation_id=operation_id,
                        actor_user_id=principal.user_id,
                        actor_scope_epoch=principal.scope_epoch,
                        content=content,
                        original_filename=filename,
                        media_type=_bounded_text(media_type, "file.media_type", 255),
                        title=_publication_title(request.form.get("title")),
                        target=target,
                        request_meta=_request_meta(),
                    )
                )
                document = result.to_mapping()
                return _json_response(document, rid, 202)

            body = _read_json(
                required=frozenset({"operation_id", "source_type"}),
                optional=frozenset(
                    {
                        "conversation_id",
                        "markdown",
                        "message_range",
                        "page_id",
                        "target",
                        "title",
                    }
                ),
                maximum_bytes=MAX_CANONICAL_PUBLICATION_JSON_BYTES,
            )
            operation_id = _operation_id(body)
            source_type = body.get("source_type")
            keys = frozenset(body)
            target = (
                None
                if "target" not in body
                else _higher_publication_target(body.get("target"))
            )
            if (
                source_type == "manual"
                and {"operation_id", "source_type", "markdown"} <= keys
                and keys
                <= {"operation_id", "source_type", "markdown", "target", "title"}
            ):
                created = service.create_manual_preview(
                    CreateManualPreviewCommand(
                        request_id=rid,
                        operation_id=operation_id,
                        actor_user_id=principal.user_id,
                        actor_scope_epoch=principal.scope_epoch,
                        markdown=_publication_markdown(body.get("markdown")),
                        title=_publication_title(body.get("title")),
                        target=target,
                        request_meta=_request_meta(),
                    )
                )
            elif (
                source_type == "notion"
                and {"operation_id", "source_type", "page_id"} <= keys
                and keys <= {"operation_id", "source_type", "page_id", "target"}
            ):
                created = service.create_notion_preview(
                    CreateNotionPreviewCommand(
                        request_id=rid,
                        operation_id=operation_id,
                        actor_user_id=principal.user_id,
                        actor_scope_epoch=principal.scope_epoch,
                        page_id=_bounded_text(body.get("page_id"), "page_id", 500),
                        target=target,
                        request_meta=_request_meta(),
                    )
                )
            elif (
                source_type == "chat"
                and {
                    "conversation_id",
                    "message_range",
                    "operation_id",
                    "source_type",
                }
                <= keys
                and keys
                <= {
                    "conversation_id",
                    "message_range",
                    "operation_id",
                    "source_type",
                    "target",
                }
            ):
                message_range = body.get("message_range")
                if (
                    not isinstance(message_range, dict)
                    or frozenset(message_range) != {"from_sequence", "to_sequence"}
                ):
                    raise _RequestError(
                        "VALIDATION_ERROR", "message_range is invalid.", 400
                    )
                first = _positive(message_range.get("from_sequence"), "from_sequence")
                last = _positive(message_range.get("to_sequence"), "to_sequence")
                if first > last:
                    raise _RequestError(
                        "VALIDATION_ERROR", "message_range is invalid.", 400
                    )
                created = service.create_chat_preview(
                    CreateChatPreviewCommand(
                        request_id=rid,
                        operation_id=operation_id,
                        actor_user_id=principal.user_id,
                        actor_scope_epoch=principal.scope_epoch,
                        conversation_id=_uuid(body.get("conversation_id"), "conversation_id"),
                        first_sequence_no=first,
                        last_sequence_no=last,
                        target=target,
                        request_meta=_request_meta(),
                    )
                )
            else:
                raise _RequestError(
                    "VALIDATION_ERROR", "The publication source shape is invalid.", 400
                )
            document = service.get_preview(
                GetPublicationPreviewQuery(
                    actor_user_id=principal.user_id,
                    actor_scope_epoch=principal.scope_epoch,
                    preview_id=getattr(created, "preview_id"),
                )
            )
            return _json_response(_with_generation_worker_health(document), rid)

        return self.run(execute)

    def get_publication_preview(self, preview_id: str):
        def execute(rid: str, principal: _Principal, read_preview=None):
            if request.args:
                raise _RequestError(
                    "VALIDATION_ERROR", "The query string is invalid.", 400
                )
            service = self.publication
            if service is None:
                raise RuntimeError("The canonical publication service is unavailable.")
            document = (service.get_preview if read_preview is None else read_preview)(
                GetPublicationPreviewQuery(
                    actor_user_id=principal.user_id,
                    actor_scope_epoch=principal.scope_epoch,
                    preview_id=_uuid(preview_id, "preview_id"),
                )
            )
            return _json_response(_with_generation_worker_health(document), rid)

        if self.publication_status_reader is None:
            return self.run(execute)
        rid = _request_id()
        try:
            # Close is inside the same error envelope as principal/read failures.
            from_environ = getattr(self.publication_status_reader, "request_from_environ", None)
            read_request = (from_environ(request.environ) if callable(from_environ)
                else self.publication_status_reader.request())
            with read_request as reader:
                return execute(rid, _principal(reader.resolve_principal), reader.get_preview)
        except Exception as exc:
            return _error_response(exc, rid)

    def resume_pdf_publication_preview(self, preview_id: str):
        def execute(rid: str, principal: _Principal):
            if request.args:
                raise _RequestError("VALIDATION_ERROR", "The query string is invalid.", 400)
            body = _read_json(required=frozenset({"operation_id"}))
            from .services.pdf_visual_continuation_publication import ResumePdfPreviewCommand
            document = self.publication.resume_pdf_preview(ResumePdfPreviewCommand(
                request_id=rid, operation_id=_operation_id(body),
                actor_user_id=principal.user_id, actor_scope_epoch=principal.scope_epoch,
                preview_id=_uuid(preview_id, "preview_id")))
            return _json_response(_with_generation_worker_health(document), rid)
        return self.run(execute)

    def cancel_publication_preview(self, preview_id: str):
        def execute(rid: str, principal: _Principal):
            service = self.publication
            if service is None:
                raise RuntimeError("The canonical publication service is unavailable.")
            body = _read_json(required=frozenset({"operation_id"}))
            operation_id = _operation_id(body)
            cancelled = service.cancel_preview(
                CancelPublicationPreviewCommand(
                    request_id=rid,
                    operation_id=operation_id,
                    actor_user_id=principal.user_id,
                    actor_scope_epoch=principal.scope_epoch,
                    preview_id=_uuid(preview_id, "preview_id"),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(
                {
                    "operation_id": operation_id,
                    "resource_id": getattr(cancelled, "preview_id"),
                    "row_version": getattr(cancelled, "row_version"),
                    "status": getattr(cancelled, "status"),
                },
                rid,
            )

        return self.run(execute)

    def commit_publication(self):
        def execute(rid: str, principal: _Principal):
            service = self.publication
            if service is None:
                raise RuntimeError("The canonical publication service is unavailable.")
            body = _read_json(
                required=frozenset(
                    {
                        "operation_id",
                        "preview_id",
                        "source_snapshot_sha256",
                        "target_mode",
                    }
                ),
                optional=frozenset({"personal_space_id", "target"}),
            )
            operation_id = _operation_id(body)
            source_snapshot = body.get("source_snapshot_sha256")
            if (
                not isinstance(source_snapshot, str)
                or re.fullmatch(r"[0-9a-f]{64}", source_snapshot, re.ASCII) is None
            ):
                raise _RequestError(
                    "VALIDATION_ERROR", "source_snapshot_sha256 is invalid.", 400
                )
            target_mode = body.get("target_mode")
            if target_mode not in {"personal", "part", "both", "explicit"}:
                raise _RequestError("VALIDATION_ERROR", "target_mode is invalid.", 400)
            target = (
                _higher_publication_target(body.get("target"))
                if "target" in body
                else None
            )
            if (target_mode == "explicit") != (target is not None):
                raise _RequestError(
                    "VALIDATION_ERROR",
                    "target is required exactly when target_mode is explicit.",
                    400,
                )
            personal_space_id = (
                _uuid(body.get("personal_space_id"), "personal_space_id")
                if "personal_space_id" in body
                else None
            )
            if personal_space_id is not None and target_mode not in {
                "personal",
                "both",
            }:
                raise _RequestError(
                    "VALIDATION_ERROR",
                    "personal_space_id is valid only for personal or both publication.",
                    400,
                )
            result = service.publish(
                PublishPreviewCommand(
                    request_id=rid,
                    operation_id=operation_id,
                    actor_user_id=principal.user_id,
                    actor_scope_epoch=principal.scope_epoch,
                    preview_id=_uuid(body.get("preview_id"), "preview_id"),
                    source_snapshot_sha256=source_snapshot,
                    target_mode=target_mode,
                    target=target,
                    personal_space_id=personal_space_id,
                    request_meta=_request_meta(),
                )
            )
            return _json_response(_publication_result(result), rid, 201)

        return self.run(execute)

    def spaces(self):
        def execute(rid: str, p: _Principal):
            selected_space_id, conversation_id = _scope_selection_options()
            snapshot = self.scope_selection.read(
                ScopeSelectionRequest(
                    p.user_id,
                    p.scope_epoch,
                    selected_space_id=selected_space_id,
                    conversation_id=conversation_id,
                )
            )
            return _json_response(
                {
                    "items": [
                        {
                            key: value
                            for key, value in item.as_dict().items()
                            if key != "selected"
                        }
                        for item in snapshot.spaces
                    ],
                    "scope_epoch": p.scope_epoch,
                },
                rid,
            )

        return self.run(execute)

    def list_conversations(self):
        def execute(rid: str, p: _Principal):
            limit, before_updated_at_ms, before_conversation_id = (
                _conversation_list_options()
            )
            records = self.conversations.list_for_owner(
                actor_user_id=p.user_id,
                actor_scope_epoch=p.scope_epoch,
                before_updated_at_ms=before_updated_at_ms,
                before_conversation_id=before_conversation_id,
                limit=limit + 1 if limit < 100 else 100,
            )
            has_more = len(records) > limit
            rows = records[:limit]
            if limit == 100 and len(rows) == 100:
                last = rows[-1].conversation
                has_more = bool(
                    self.conversations.list_for_owner(
                        actor_user_id=p.user_id,
                        actor_scope_epoch=p.scope_epoch,
                        before_updated_at_ms=last.updated_at_ms,
                        before_conversation_id=last.id,
                        limit=1,
                    )
                )
            items = []
            for entry in rows:
                snapshot = self.scope_selection.read(
                    ScopeSelectionRequest(
                        p.user_id,
                        p.scope_epoch,
                        conversation_id=entry.conversation.id,
                    )
                )
                summary = _conversation_summary(
                    entry.conversation,
                    snapshot.conversation_badge,
                    first_user_message=entry.first_user_message,
                )
                summary.update(
                    {
                        "completed_turn_count": entry.completed_turn_count,
                        "latest_turn_state": entry.latest_turn_state,
                        "turn_count": entry.turn_count,
                    }
                )
                items.append(summary)
            next_cursor = None
            if has_more and rows:
                last = rows[-1].conversation
                next_cursor = _encode_conversation_cursor(
                    last.updated_at_ms,
                    last.id,
                )
            return _json_response(
                {
                    "has_more": has_more,
                    "items": items,
                    "next_cursor": next_cursor,
                },
                rid,
            )
        return self.run(execute)

    def create_conversation(self):
        def execute(rid: str, p: _Principal):
            body = _read_json(required=frozenset({"operation_id", "space_id"}), optional=frozenset({"title"}))
            operation_id = _operation_id(body)
            result = self.conversations.create(
                CreateConversationCommand(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    operation_id=operation_id,
                    request_id=rid,
                    space_id=_uuid(body["space_id"], "space_id"),
                    title=_bounded_text(body.get("title", ""), "title", 300, allow_empty=True),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(
                {
                    "operation_id": operation_id,
                    "resource_id": result.conversation.id,
                    "row_version": result.conversation.row_version,
                    "status": result.outcome,
                },
                rid,
                201,
            )
        return self.run(execute)

    def get_conversation(self, conversation_id: str):
        def execute(rid: str, p: _Principal):
            cid = _uuid(conversation_id, "conversation_id")
            record = self.conversations.get(
                actor_user_id=p.user_id,
                actor_scope_epoch=p.scope_epoch,
                conversation_id=cid,
            )
            badge = self.scope_selection.read(
                ScopeSelectionRequest(p.user_id, p.scope_epoch, conversation_id=cid)
            ).conversation_badge
            messages = self.conversations.list_messages(
                actor_user_id=p.user_id,
                actor_scope_epoch=p.scope_epoch,
                conversation_id=cid,
            )
            pins = self.conversations.list_pins(
                actor_user_id=p.user_id,
                actor_scope_epoch=p.scope_epoch,
                conversation_id=cid,
            )
            bindings = self.conversations.list_contexts(
                actor_user_id=p.user_id,
                actor_scope_epoch=p.scope_epoch,
                conversation_id=cid,
            )
            summary = self.conversations.get_active_summary(
                actor_user_id=p.user_id,
                actor_scope_epoch=p.scope_epoch,
                conversation_id=cid,
            )
            return _json_response(
                {
                    "context_bindings": [
                        {
                            "binding_mode": value.binding_mode,
                            "binding_type": value.binding_type,
                            "display_metadata": value.display_metadata,
                            "id": value.id,
                            "row_version": value.row_version,
                            "status": value.status,
                        }
                        for value in bindings
                    ],
                    "conversation": _conversation_summary(
                        record,
                        badge,
                        first_user_message=next(
                            (
                                text
                                for value in messages
                                if (text := _conversation_message_title_text(value))
                                is not None
                            ),
                            None,
                        ),
                    ),
                    "messages": [
                        {
                            "content": value.content,
                            "created_at": _utc(value.created_at_ms),
                            "id": value.id,
                            "role": value.role,
                            "sequence_no": value.sequence_no,
                        }
                        for value in messages
                    ],
                    "pins": [
                        {
                            "created_at": _utc(value.created_at_ms),
                            "item_id": value.item_id,
                            "revision_id": value.revision_id,
                            "space_id": value.space_id,
                        }
                        for value in pins
                    ],
                    "summary_status": "ready" if summary is not None else "absent",
                },
                rid,
            )
        return self.run(execute)

    def message_page(self, conversation_id: str):
        def execute(rid: str, p: _Principal):
            allowed = {"after_sequence_no", "through_sequence_no", "limit"}
            if set(request.args) - allowed or any(len(request.args.getlist(key)) != 1 for key in request.args):
                raise _RequestError("VALIDATION_ERROR", "Invalid history page query.", 400)
            values = {}
            for key in allowed:
                raw = request.args.get(key)
                if raw is not None:
                    if re.fullmatch(r"0|[1-9][0-9]{0,14}", raw, re.ASCII) is None:
                        raise _RequestError("VALIDATION_ERROR", f"{key} is invalid.", 400)
                    values[key] = int(raw)
            result = self.conversations.read_message_page(
                actor_user_id=p.user_id, actor_scope_epoch=p.scope_epoch,
                conversation_id=_uuid(conversation_id, "conversation_id"), **values,
            )
            return _json_response({**result, "items": [_conversation_message_payload(value) for value in result["items"]]}, rid)
        return self.run(execute)

    def turn_answer(self, conversation_id: str, operation_id: str):
        def execute(rid: str, p: _Principal):
            _require_empty_query()
            turn, message = self.conversations.read_turn_answer(
                actor_user_id=p.user_id, actor_scope_epoch=p.scope_epoch,
                conversation_id=_uuid(conversation_id, "conversation_id"), operation_id=_uuid(operation_id, "operation_id"),
            )
            return _json_response({"schema_version": 1, "conversation_id": turn.conversation_id,
                "operation_id": turn.operation_id, "turn_id": turn.id, "message": _conversation_message_payload(message)}, rid)
        return self.run(execute)

    def pending_turns(self, conversation_id: str):
        def execute(rid: str, p: _Principal):
            if set(request.args) - {"after_sequence_no", "limit"} or any(
                len(request.args.getlist(key)) != 1 for key in request.args
            ):
                raise _RequestError("VALIDATION_ERROR", "Invalid pending-turn query.", 400)
            values = {}
            for key, default in (("after_sequence_no", "0"), ("limit", "20")):
                raw = request.args.get(key, default)
                if re.fullmatch(r"0|[1-9][0-9]{0,14}", raw, re.ASCII) is None:
                    raise _RequestError("VALIDATION_ERROR", f"{key} is invalid.", 400)
                values[key] = int(raw)
            if not 1 <= values["limit"] <= 100:
                raise _RequestError("VALIDATION_ERROR", "limit is invalid.", 400)
            result = self.conversations.list_pending_turns(
                actor_user_id=p.user_id, actor_scope_epoch=p.scope_epoch,
                conversation_id=_uuid(conversation_id, "conversation_id"), **values,
            )
            return _json_response(result, rid)
        return self.run(execute)

    def update_conversation(self, conversation_id: str):
        def execute(rid: str, p: _Principal):
            body = _read_json(
                required=frozenset({"operation_id", "expected_row_version"}),
                optional=frozenset({"title", "archived"}),
            )
            if "title" not in body and "archived" not in body:
                raise _RequestError("VALIDATION_ERROR", "A lifecycle field is required.", 400)
            if "archived" in body and not isinstance(body["archived"], bool):
                raise _RequestError("VALIDATION_ERROR", "archived is invalid.", 400)
            operation_id = _operation_id(body)
            result = self.conversations.update(
                UpdateConversationCommand(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    operation_id=operation_id,
                    request_id=rid,
                    conversation_id=_uuid(conversation_id, "conversation_id"),
                    expected_row_version=_positive(body["expected_row_version"], "expected_row_version"),
                    title=None if "title" not in body else _bounded_text(body["title"], "title", 300),
                    status=None if "archived" not in body else ("archived" if body["archived"] else "active"),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(
                {
                    "operation_id": operation_id,
                    "resource_id": result.conversation.id,
                    "row_version": result.conversation.row_version,
                    "status": result.outcome,
                },
                rid,
            )
        return self.run(execute)

    def search(self, space_id: str):
        def execute(rid: str, p: _Principal):
            body = _read_json(required=frozenset({"query"}), optional=frozenset({"limit", "cursor", "include_management_states", "as_of_ms"}))
            if body.get("cursor") not in {None, ""} or body.get("include_management_states") not in {None, False}:
                raise _RequestError("VALIDATION_ERROR", "Unsupported search option.", 400)
            limit = 20 if "limit" not in body else _positive(body["limit"], "limit")
            if limit > 100:
                raise _RequestError("VALIDATION_ERROR", "limit is invalid.", 400)
            result = self.retrieval.retrieve_for_space(
                SpaceRetrievalRequest(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    space_id=_uuid(space_id, "space_id"),
                    query=_bounded_text(body["query"], "query", 2000),
                    top_k=limit,
                    as_of_ms=(
                        None
                        if body.get("as_of_ms") is None
                        else _nonnegative(body["as_of_ms"], "as_of_ms")
                    ),
                )
            )
            return _json_response(
                {
                    "corpus_closure_sha256": result.corpus_closure_sha256,
                    "has_more": False,
                    "items": [
                        {
                            "capabilities": ["read"],
                            "inherited": hit.inherited,
                            "item_id": hit.item_id,
                            "origin": hit.origin,
                            "overlay_grant_id": hit.overlay_grant_id,
                            "revision_id": hit.revision_id,
                            "score": hit.score_ppm / 1_000_000,
                            "selected_view_space_id": result.space_id,
                            "snippet": hit.excerpt_md,
                            "source_space_id": hit.source_space_id,
                            "title": hit.title,
                        }
                        for hit in result.ranked_documents
                    ],
                    "next_cursor": None,
                    "ranked_documents": [
                        {
                            "alternate_identities": [
                                {
                                    "item_id": alternate.item_id,
                                    "revision_id": alternate.revision_id,
                                    "source_space_id": alternate.source_space_id,
                                }
                                for alternate in hit.alternate_identities
                            ],
                            "graph_hop": hit.graph_hop,
                            "inherited": hit.inherited,
                            "item_id": hit.item_id,
                            "normalized_content_sha256": hit.normalized_content_sha256,
                            "deterministic_score_ppm": hit.deterministic_score_ppm,
                            "model_grade": hit.model_grade,
                            "origin": hit.origin,
                            "overlay_grant_id": hit.overlay_grant_id,
                            "provenance": list(hit.provenance),
                            "rank": hit.rank,
                            "relation_semantics": hit.relation_semantics,
                            "relation_type": hit.relation_type,
                            "revision_id": hit.revision_id,
                            "score_ppm": hit.score_ppm,
                            "selected_view_space_id": hit.selected_view_space_id,
                            "snippet": hit.excerpt_md,
                            "source_space_id": hit.source_space_id,
                            "title": hit.title,
                            "temporal": _ranked_temporal_metadata_json(
                                hit.temporal_metadata
                            ),
                            "trust_boundary": hit.trust_boundary,
                        }
                        for hit in result.ranked_documents
                    ],
                    "space_id": result.space_id,
                    "ranking": (
                        None
                        if result.ranking_envelope is None
                        else {
                            "as_of_explicit": result.ranking_envelope.as_of_explicit,
                            "as_of_ms": result.ranking_envelope.as_of_ms,
                            "cache_expires_at_ms": (
                                result.ranking_envelope.cache_expires_at_ms
                            ),
                            "cache_hit": result.ranking_envelope.cache_hit,
                            "input_sha256": result.ranking_envelope.input_sha256,
                            "model_id": result.ranking_envelope.model_id,
                            "model_revision_sha256": (
                                result.ranking_envelope.model_revision_sha256
                            ),
                            "next_effective_boundary_ms": (
                                result.ranking_envelope.next_effective_boundary_ms
                            ),
                            "pipeline_version": (
                                result.ranking_envelope.pipeline_version
                            ),
                            "prompt_revision_sha256": (
                                result.ranking_envelope.prompt_revision_sha256
                            ),
                            "reranker_reason": (
                                result.ranking_envelope.reranker_reason
                            ),
                            "reranker_status": (
                                result.ranking_envelope.reranker_status
                            ),
                            "temporal_intent": (
                                result.ranking_envelope.temporal_intent
                            ),
                        }
                    ),
                    "retrieval_explain": (
                        None
                        if result.hybrid_explain is None
                        else {
                            "cache_hit": result.hybrid_explain.cache_hit,
                            "direct_candidate_count": result.hybrid_explain.direct_candidate_count,
                            "final_document_count": result.hybrid_explain.final_document_count,
                            "one_hop_candidate_count": result.hybrid_explain.one_hop_candidate_count,
                            "pipeline_version": result.hybrid_explain.pipeline_version,
                            "pre_rank_candidate_count": result.hybrid_explain.pre_rank_candidate_count,
                            "two_hop_candidate_count": result.hybrid_explain.two_hop_candidate_count,
                            "two_hop_executed": result.hybrid_explain.two_hop_executed,
                        }
                    ),
                } if result.hybrid_explain is not None else {
                    "corpus_closure_sha256": result.corpus_closure_sha256,
                    "has_more": False,
                    "items": [
                        {
                            "capabilities": ["read"],
                            "inherited": hit.inherited,
                            "item_id": hit.item_id,
                            "origin": hit.origin,
                            "overlay_grant_id": hit.overlay_grant_id,
                            "revision_id": hit.revision_id,
                            "score": float(hit.bm25_score),
                            "selected_view_space_id": result.space_id,
                            "snippet": hit.excerpt_md,
                            "source_space_id": hit.space_id,
                            "title": hit.title,
                        }
                        for hit in result.hits
                    ],
                    "next_cursor": None,
                    "space_id": result.space_id,
                },
                rid,
            )
        return self.run(execute)

    def list_items(self, space_id: str):
        def execute(rid: str, p: _Principal):
            limit, before_updated_at_ms, before_id = _management_list_options("item")
            result = self.management_reads.list_items(
                SpaceManagementPageRequest(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    space_id=_uuid(space_id, "space_id"),
                    before_updated_at_ms=before_updated_at_ms,
                    before_id=before_id,
                    limit=limit,
                )
            )
            next_cursor = None
            if result.has_more and result.items:
                last = result.items[-1]
                next_cursor = _encode_management_cursor(
                    "item", last.updated_at_ms, last.id
                )
            return _json_response(
                {
                    "corpus_closure_sha256": result.corpus_closure_sha256,
                    "has_more": result.has_more,
                    "items": [
                        {
                            "curation_state": item.curation_status,
                            "current_revision_id": item.current_revision_id,
                            "capabilities": list(item.capabilities),
                            "id": item.id,
                            "inherited": item.inherited,
                            "origin": item.origin,
                            "overlay_grant_id": item.overlay_grant_id,
                            "row_version": item.row_version,
                            "selected_view_space_id": item.selected_view_space_id,
                            "source_space_id": item.space_id,
                            "space_id": item.space_id,
                            "state": item.lifecycle_status,
                            "title": item.title,
                            "updated_at": _utc(item.updated_at_ms),
                            "warning_badges": list(item.warning_badges),
                        }
                        for item in result.items
                    ],
                    "next_cursor": next_cursor,
                },
                rid,
            )

        return self.run(execute)

    def get_item(self, space_id: str, item_id: str):
        def execute(rid: str, p: _Principal):
            _require_empty_query()
            result = self.management_reads.get_item(
                ItemManagementReadRequest(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                )
            )
            record = result.record
            revision = record.anchor_revision
            deactivation = record.deactivation
            lease = record.lease
            return _json_response(
                {
                    "capabilities": list(result.capabilities),
                    "corpus_closure_sha256": result.corpus_closure_sha256,
                    "inherited": result.inherited,
                    "origin": result.origin,
                    "overlay_grant_id": result.overlay_grant_id,
                    "selected_view_space_id": result.selected_view_space_id,
                    "source_space_id": result.source_space_id,
                    "item": {
                        "capabilities": list(result.capabilities),
                        "curation_state": record.curation_status,
                        "current_revision_id": record.current_revision_id,
                        "id": record.id,
                        "inherited": result.inherited,
                        "origin": result.origin,
                        "overlay_grant_id": result.overlay_grant_id,
                        "row_version": record.row_version,
                        "selected_view_space_id": result.selected_view_space_id,
                        "source_space_id": result.source_space_id,
                        "space_id": record.space_id,
                        "state": record.lifecycle_status,
                        "title": revision.title,
                        "updated_at": _utc(record.updated_at_ms),
                        "warning_badges": [],
                    },
                    "lease": (
                        None
                        if lease is None
                        else {
                            "expires_at": _utc(lease.expires_at_ms),
                            "fencing_generation": lease.fencing_generation,
                            "heartbeat_at": _utc(lease.heartbeat_at_ms),
                            "held": True,
                            "holder_display": lease.holder_display_name,
                            "holder_user_id": lease.holder_user_id,
                        }
                    ),
                    "lifecycle": {
                        "deactivation": (
                            None
                            if deactivation is None
                            else {
                                "deactivated_at": _utc(
                                    deactivation.deactivated_at_ms
                                ),
                                "deactivated_by": {
                                    "display_name": (
                                        deactivation.deactivated_by_display_name
                                    ),
                                    "user_id": deactivation.deactivated_by_user_id,
                                },
                                "episode_id": deactivation.episode_id,
                                "episode_no": deactivation.episode_no,
                                "inactive_duration_seconds": (
                                    result.inactive_duration_seconds
                                ),
                                "purge_eligible_at": _utc(
                                    deactivation.purge_eligible_at_ms
                                ),
                                "reason": deactivation.reason,
                            }
                        ),
                        "edge_counts": _edge_counts_payload(record.edge_counts),
                        "reference_counts": _reference_counts_payload(
                            record.reference_counts
                        ),
                    },
                    "markdown": result.markdown,
                    "references": [
                        reference.as_dict() for reference in result.references
                    ],
                    "timeline": {
                        "content_age_seconds": result.content_age_seconds,
                        "content_created_at": _utc(revision.created_at_ms),
                        "last_reviewed_at": (
                            None
                            if record.last_reviewed_at_ms is None
                            else _utc(record.last_reviewed_at_ms)
                        ),
                        "last_updated_at": _utc(record.updated_at_ms),
                        "review_age_seconds": result.review_age_seconds,
                    },
                },
                rid,
            )

        return self.run(execute)

    def list_item_source_artifacts(self, space_id: str, item_id: str):
        def execute(rid: str, p: _Principal):
            _require_empty_query()
            service = self.source_artifacts
            if service is None:
                raise RuntimeError("The canonical source-artifact service is unavailable.")
            selected = _uuid(space_id, "space_id")
            item = _uuid(item_id, "item_id")
            result = service.list_for_item(
                ItemSourceArtifactRequest(
                    principal=self._session_principal(p),
                    selected_space_id=selected,
                    item_id=item,
                )
            )
            return _json_response(
                {
                    "corpus_closure_sha256": result.corpus_closure_sha256,
                    "item_id": result.item_id,
                    "items": [
                        {
                            "artifact_id": row.artifact_id,
                            "created_at": _utc(row.created_at_ms),
                            "download_url": (
                                f"{CANONICAL_ROUTE_PREFIX}/spaces/{selected}/items/"
                                f"{item}/source-artifacts/{row.artifact_id}/download"
                            ),
                            "download_permitted": (
                                None if row.protected_source_kind is None else
                                row.artifact_id in result.download_permitted_artifact_ids
                            ),
                            "media_type": row.media_type,
                            "origin_type": row.origin_type,
                            "original_filename": row.original_filename,
                            "size_bytes": row.size_bytes,
                            "source_revision_id": row.source_revision_id,
                        }
                        for row in result.items
                    ],
                    "selected_view_space_id": result.selected_space_id,
                },
                rid,
            )

        return self.run(execute)

    def download_item_source_artifact(
        self, space_id: str, item_id: str, artifact_id: str
    ):
        def execute(rid: str, p: _Principal):
            _require_empty_query()
            service = self.source_artifacts
            if service is None:
                raise RuntimeError("The canonical source-artifact service is unavailable.")
            result = service.download(
                ItemSourceArtifactDownloadRequest(
                    principal=self._session_principal(p),
                    selected_space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    artifact_id=_uuid(artifact_id, "artifact_id"),
                )
            )
            # The repository already reduces this to one safe filename
            # component.  RFC 5987 keeps the original Korean name usable while
            # the ASCII fallback remains header-safe for older clients.
            ascii_name = "".join(
                character
                for character in result.original_filename
                if 32 <= ord(character) < 127 and character not in {'"', '\\'}
            ).strip(" .") or f"source-{result.artifact_id}"
            response = Response(
                result.content,
                status=200,
                content_type=result.media_type,
            )
            response.headers["Content-Disposition"] = (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(result.original_filename, safe='')}"
            )
            response.headers["Cache-Control"] = "private, no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Original-Content-SHA256"] = result.content_sha256
            response.headers["X-Request-ID"] = rid
            response.content_length = result.size_bytes
            return response

        return self.run(execute)

    def list_revisions(self, space_id: str, item_id: str):
        def execute(rid: str, p: _Principal):
            limit, before_revision_no, before_revision_id = (
                _management_list_options("revision")
            )
            result = self.management_reads.list_revisions(
                ItemRevisionPageRequest(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    before_revision_no=before_revision_no,
                    before_revision_id=before_revision_id,
                    limit=limit,
                )
            )
            next_cursor = None
            if result.has_more and result.items:
                last = result.items[-1]
                next_cursor = _encode_management_cursor(
                    "revision", last.revision_no, last.id
                )
            return _json_response(
                {
                    "corpus_closure_sha256": result.corpus_closure_sha256,
                    "has_more": result.has_more,
                    "inherited": result.inherited,
                    "items": [
                        _revision_payload(revision) for revision in result.items
                    ],
                    "next_cursor": next_cursor,
                    "origin": result.origin,
                    "overlay_grant_id": result.overlay_grant_id,
                    "selected_view_space_id": result.selected_view_space_id,
                    "source_space_id": result.source_space_id,
                },
                rid,
            )

        return self.run(execute)

    def get_revision(self, space_id: str, item_id: str, revision_id: str):
        def execute(rid: str, p: _Principal):
            _require_empty_query()
            result = self.management_reads.get_revision(
                ItemRevisionReadRequest(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    revision_id=_uuid(revision_id, "revision_id"),
                )
            )
            return _json_response(
                {
                    "corpus_closure_sha256": result.corpus_closure_sha256,
                    "inherited": result.inherited,
                    "origin": result.origin,
                    "overlay_grant_id": result.overlay_grant_id,
                    "revision": _revision_payload(
                        result.revision,
                        markdown=result.markdown,
                        include_markdown=True,
                    ),
                    "selected_view_space_id": result.selected_view_space_id,
                    "source_space_id": result.source_space_id,
                },
                rid,
            )

        return self.run(execute)

    def compare_revisions(self, space_id: str, item_id: str):
        def execute(rid: str, p: _Principal):
            from_revision_id, to_revision_id = _compare_options()
            result = self.management_reads.compare_revisions(
                ItemRevisionCompareRequest(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    from_revision_id=from_revision_id,
                    to_revision_id=to_revision_id,
                )
            )
            return _json_response(
                {
                    "corpus_closure_sha256": result.corpus_closure_sha256,
                    "diff_format": result.diff_format,
                    "diff_sha256": result.diff_sha256,
                    "from_revision_id": result.from_revision_id,
                    "hunks": [hunk.as_dict() for hunk in result.hunks],
                    "inherited": result.inherited,
                    "origin": result.origin,
                    "overlay_grant_id": result.overlay_grant_id,
                    "selected_view_space_id": result.selected_view_space_id,
                    "source_space_id": result.source_space_id,
                    "to_revision_id": result.to_revision_id,
                },
                rid,
            )

        return self.run(execute)

    def graph(self, space_id: str):
        def execute(rid: str, p: _Principal):
            result = self.management_reads.graph(
                SpaceManagementReadRequest(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    space_id=_uuid(space_id, "space_id"),
                )
            )
            return _json_response(
                {
                    "corpus_closure_sha256": result.corpus_closure_sha256,
                    "edges": [
                        {
                            "capabilities": list(edge.capabilities),
                            "edge_type": edge.edge_type,
                            "edge_scope": edge.edge_scope,
                            "edge_status": edge.edge_status,
                            "id": edge.id,
                            "inherited": edge.inherited,
                            "origin": edge.origin,
                            "row_version": edge.row_version,
                            "source_evidence_revision_id": edge.source_evidence_revision_id,
                            "source_item_id": edge.source_item_id,
                            "source_space_id": edge.source_space_id or edge.space_id,
                            "home_space_id": edge.home_space_id,
                            "target_space_id": edge.target_space_id,
                            "direction": edge.direction,
                            "confidence_ppm": edge.confidence_ppm,
                            "detector_generation": edge.detector_generation,
                            "model_name": edge.model_name,
                            "model_provider": edge.model_provider,
                            "prompt_schema_version": edge.prompt_schema_version,
                            "scoring_version": edge.scoring_version,
                            "semantic_evidence_sha256": edge.semantic_evidence_sha256,
                            "source_evidence_span_sha256": edge.source_evidence_span_sha256,
                            "target_evidence_span_sha256": edge.target_evidence_span_sha256,
                            "source_evidence_location": edge.source_evidence_location,
                            "target_evidence_location": edge.target_evidence_location,
                            "selected_view_space_id": edge.selected_view_space_id,
                            "target_evidence_revision_id": edge.target_evidence_revision_id,
                            "target_item_id": edge.target_item_id,
                        }
                        for edge in result.edges
                    ],
                    "generation": result.generation,
                    "nodes": [
                        {
                            "curation_state": node.curation_status,
                            "capabilities": list(node.capabilities),
                            "current_revision_id": node.current_revision_id,
                            "id": node.id,
                            "inherited": node.inherited,
                            "origin": node.origin,
                            "overlay_grant_id": node.overlay_grant_id,
                            "row_version": node.row_version,
                            "state": node.lifecycle_status,
                            "selected_view_space_id": node.selected_view_space_id,
                            "source_space_id": node.space_id,
                            "title": node.title,
                        }
                        for node in result.nodes
                    ],
                    "space_id": result.space_id,
                },
                rid,
            )

        return self.run(execute)

    def projection_status(self, space_id: str):
        def execute(rid: str, p: _Principal):
            result = self.management_reads.projection_status(
                SpaceManagementReadRequest(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    space_id=_uuid(space_id, "space_id"),
                )
            )
            return _json_response(
                {
                    "built_at": (
                        None if result.built_at_ms is None else _utc(result.built_at_ms)
                    ),
                    "built_generation": result.built_generation,
                    "checksum_sha256": result.checksum_sha256,
                    "desired_generation": result.desired_generation,
                    "lag_generations": max(
                        0, result.desired_generation - result.built_generation
                    ),
                    "last_error": result.last_error,
                    "projection_name": result.projection_name,
                    "space_id": result.space_id,
                    "status": result.status,
                    "updated_at": _utc(result.updated_at_ms),
                },
                rid,
            )

        return self.run(execute)

    def list_findings(self, space_id: str):
        def execute(rid: str, p: _Principal):
            limit, before_updated_at_ms, before_id = _management_list_options(
                "finding"
            )
            result = self.management_reads.list_findings(
                SpaceManagementPageRequest(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    space_id=_uuid(space_id, "space_id"),
                    before_updated_at_ms=before_updated_at_ms,
                    before_id=before_id,
                    limit=limit,
                )
            )
            next_cursor = None
            if result.has_more and result.items:
                last = result.items[-1]
                next_cursor = _encode_management_cursor(
                    "finding", last.updated_at_ms, last.id
                )
            return _json_response(
                {
                    "corpus_closure_sha256": result.corpus_closure_sha256,
                    "has_more": result.has_more,
                    "items": [
                        {
                            "confidence": finding.confidence,
                            "capabilities": list(finding.capabilities),
                            "created_at": _utc(finding.created_at_ms),
                            "detector_name": finding.detector_name,
                            "detector_version": finding.detector_version,
                            "evidence": finding.evidence,
                            "finding_kind": finding.finding_kind,
                            "id": finding.id,
                            "inherited": finding.inherited,
                            "model_id": finding.model_id,
                            "prompt_version": finding.prompt_version,
                            "origin": finding.origin,
                            "overlay_grant_id": finding.overlay_grant_id,
                            "proposed_action": finding.proposed_action,
                            "required_reviewer": finding.required_reviewer,
                            "risk_tier": finding.risk_tier,
                            "row_version": finding.row_version,
                            "rule_version": finding.rule_version,
                            "severity": finding.severity,
                            "source_item_id": finding.source_item_id,
                            "source_revision_id": finding.source_revision_id,
                            "selected_view_space_id": finding.selected_view_space_id,
                            "source_space_id": finding.space_id,
                            "space_id": finding.space_id,
                            "status": finding.status,
                            "target_item_id": finding.target_item_id,
                            "target_revision_id": finding.target_revision_id,
                            "updated_at": _utc(finding.updated_at_ms),
                            "visibility_effect": finding.visibility_effect,
                        }
                        for finding in result.items
                    ],
                    "next_cursor": next_cursor,
                },
                rid,
            )

        return self.run(execute)

    def get_finding(self, space_id: str, finding_id: str):
        def execute(rid: str, p: _Principal):
            _require_empty_query()
            service = self.curation
            if service is None:
                raise RuntimeError("The canonical curation service is unavailable.")
            finding = service.get_finding(
                ReadFindingRequest(
                    principal=self._session_principal(p),
                    space_id=_uuid(space_id, "space_id"),
                    finding_id=_uuid(finding_id, "finding_id"),
                )
            )
            return _json_response(
                {
                    "finding": {
                        "confidence": finding.confidence,
                        "capabilities": list(getattr(finding, "capabilities", ())),
                        "corpus_closure_sha256": getattr(
                            finding, "corpus_closure_sha256", None
                        ),
                        "created_at": _utc(finding.created_at_ms),
                        "decision_mode": finding.decision_mode,
                        "detector_name": finding.detector_name,
                        "detector_version": finding.detector_version,
                        "evidence": finding.evidence,
                        "finding_kind": finding.finding_kind,
                        "id": finding.id,
                        "inherited": getattr(finding, "inherited", False),
                        "model_id": finding.model_id,
                        "origin": getattr(finding, "origin", "native"),
                        "overlay_grant_id": getattr(
                            finding, "overlay_grant_id", None
                        ),
                        "prompt_version": finding.prompt_version,
                        "proposed_action": finding.proposed_action,
                        "proposed_action_evidence": finding.proposed_action_evidence,
                        "required_reviewer": finding.required_reviewer,
                        "review_reason": finding.review_reason,
                        "reviewed_at": (
                            None
                            if finding.reviewed_at_ms is None
                            else _utc(finding.reviewed_at_ms)
                        ),
                        "reviewed_by": finding.reviewed_by,
                        "risk_tier": finding.risk_tier,
                        "row_version": finding.row_version,
                        "rule_version": finding.rule_version,
                        "selected_view_space_id": getattr(
                            finding, "selected_view_space_id", finding.space_id
                        ),
                        "severity": finding.severity,
                        "source_item_id": finding.source_item_id,
                        "source_revision_id": finding.source_revision_id,
                        "source_space_id": getattr(
                            finding, "source_space_id", finding.space_id
                        ),
                        "space_id": finding.space_id,
                        "status": finding.status,
                        "target_item_id": finding.target_item_id,
                        "target_revision_id": finding.target_revision_id,
                        "updated_at": _utc(finding.updated_at_ms),
                        "visibility_effect": finding.visibility_effect,
                    }
                },
                rid,
            )

        return self.run(execute)

    def review_finding(self, space_id: str, finding_id: str):
        def execute(rid: str, p: _Principal):
            _require_empty_query()
            service = self.curation
            if service is None:
                raise RuntimeError("The canonical curation service is unavailable.")
            body = _read_json(
                required=frozenset(
                    {
                        "decision",
                        "expected_finding_row_version",
                        "operation_id",
                        "reason",
                    }
                )
            )
            decision = body.get("decision")
            if decision not in {"approve", "reject", "ignore"}:
                raise _RequestError(
                    "VALIDATION_ERROR", "decision is invalid.", 400
                )
            result = service.review_finding(
                ReviewFindingCommand(
                    principal=self._session_principal(p),
                    finding_id=_uuid(finding_id, "finding_id"),
                    space_id=_uuid(space_id, "space_id"),
                    expected_row_version=_positive(
                        body.get("expected_finding_row_version"),
                        "expected_finding_row_version",
                    ),
                    decision=decision,
                    reason=_bounded_text(body.get("reason"), "reason", 2_000),
                    operation_id=_operation_id(body),
                    request_id=rid,
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def curation_shadow_metrics(self, space_id: str):
        def execute(rid: str, p: _Principal):
            if set(request.args) - {"sample_size"} or any(
                len(request.args.getlist(key)) != 1 for key in request.args
            ):
                raise _RequestError(
                    "VALIDATION_ERROR", "The query string is invalid.", 400
                )
            text = request.args.get("sample_size")
            if text is None:
                sample_size = 25
            elif re.fullmatch(r"[1-9][0-9]{0,2}", text, re.ASCII) is None:
                raise _RequestError("VALIDATION_ERROR", "sample_size is invalid.", 400)
            else:
                sample_size = int(text)
                if sample_size > 100:
                    raise _RequestError(
                        "VALIDATION_ERROR", "sample_size is invalid.", 400
                    )
            service = self.curation
            if service is None:
                raise RuntimeError("The canonical curation service is unavailable.")
            result = service.read_shadow_metrics(
                CurationShadowMetricsRequest(
                    principal=self._session_principal(p),
                    space_id=_uuid(space_id, "space_id"),
                    sample_size=sample_size,
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def get_durable_job(self, job_id: str):
        def execute(rid: str, p: _Principal):
            _require_empty_query()
            service = self.jobs
            if service is None:
                raise RuntimeError("The canonical durable-job service is unavailable.")
            result = service.read(
                ReadDurableJobRequest(
                    principal=self._session_principal(p),
                    job_id=_uuid(job_id, "job_id"),
                    request_id=rid,
                )
            )
            job = result.job
            return _json_response(
                {
                    "attempt_count": job.attempt_count,
                    "cancel_requested": job.cancel_requested,
                    "cancellable": job.cancellable,
                    "completed_at": (
                        None
                        if job.completed_at_ms is None
                        else _utc(job.completed_at_ms)
                    ),
                    "created_at": _utc(job.created_at_ms),
                    "error": job.error,
                    "handler_version": job.handler_version,
                    "job_id": job.id,
                    "job_type": job.job_type,
                    "max_attempts": job.max_attempts,
                    "progress_permille": job.progress_permille,
                    "result": job.result,
                    "state": job.state,
                    "status_url": f"{CANONICAL_ROUTE_PREFIX}/jobs/{job.id}",
                    "updated_at": _utc(job.updated_at_ms),
                },
                rid,
            )

        return self.run(execute)

    def cancel_durable_job(self, job_id: str):
        def execute(rid: str, p: _Principal):
            _require_empty_query()
            service = self.jobs
            if service is None:
                raise RuntimeError("The canonical durable-job service is unavailable.")
            body = _read_json(required=frozenset({"operation_id"}))
            result = service.cancel(
                CancelDurableJobCommand(
                    principal=self._session_principal(p),
                    job_id=_uuid(job_id, "job_id"),
                    operation_id=_operation_id(body),
                    request_id=rid,
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def preview_proposal(self, space_id: str, proposal_id: str):
        def execute(rid: str, p: _Principal):
            _require_empty_query()
            _read_json(required=frozenset())
            service = self.curation
            if service is None:
                raise RuntimeError("The canonical curation service is unavailable.")
            result = service.preview_proposal(
                ProposalPreviewCommand(
                    principal=self._session_principal(p),
                    space_id=_uuid(space_id, "space_id"),
                    proposal_id=_uuid(proposal_id, "proposal_id"),
                    request_id=rid,
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def rebase_proposal(self, space_id: str, proposal_id: str):
        def execute(rid: str, p: _Principal):
            _require_empty_query()
            body = _read_json(
                required=frozenset(
                    {
                        "expected_finding_row_version",
                        "expected_proposal_row_version",
                        "expected_source_revision_id",
                        "expected_target_revision_id",
                        "operation_id",
                    }
                ),
                optional=frozenset({"proposed_markdown"}),
            )
            proposed = body.get("proposed_markdown")
            if proposed is not None:
                proposed = _publication_markdown(proposed)
            service = self.curation
            if service is None:
                raise RuntimeError("The canonical curation service is unavailable.")
            result = service.rebase_proposal(
                ProposalRebaseCommand(
                    principal=self._session_principal(p),
                    space_id=_uuid(space_id, "space_id"),
                    proposal_id=_uuid(proposal_id, "proposal_id"),
                    expected_proposal_row_version=_positive(
                        body.get("expected_proposal_row_version"),
                        "expected_proposal_row_version",
                    ),
                    expected_finding_row_version=_positive(
                        body.get("expected_finding_row_version"),
                        "expected_finding_row_version",
                    ),
                    expected_target_revision_id=_uuid(
                        body.get("expected_target_revision_id"),
                        "expected_target_revision_id",
                    ),
                    expected_source_revision_id=_optional_uuid(
                        body.get("expected_source_revision_id"),
                        "expected_source_revision_id",
                    ),
                    proposed_markdown=proposed,
                    operation_id=_operation_id(body),
                    request_id=rid,
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), rid, 201)

        return self.run(execute)

    def apply_proposal(self, space_id: str, proposal_id: str):
        def execute(rid: str, p: _Principal):
            _require_empty_query()
            body = _read_json(
                required=frozenset(
                    {
                        "editor_session_id",
                        "expected_finding_row_version",
                        "expected_proposal_row_version",
                        "expected_source_revision_id",
                        "expected_target_item_row_version",
                        "expected_target_revision_id",
                        "lease_id",
                        "operation_id",
                        "preview_sha256",
                    }
                )
            )
            service = self.curation
            if service is None:
                raise RuntimeError("The canonical curation service is unavailable.")
            result = service.apply_proposal(
                ProposalApplyCommand(
                    principal=self._session_principal(p),
                    space_id=_uuid(space_id, "space_id"),
                    proposal_id=_uuid(proposal_id, "proposal_id"),
                    expected_proposal_row_version=_positive(
                        body.get("expected_proposal_row_version"),
                        "expected_proposal_row_version",
                    ),
                    expected_finding_row_version=_positive(
                        body.get("expected_finding_row_version"),
                        "expected_finding_row_version",
                    ),
                    expected_target_item_row_version=_positive(
                        body.get("expected_target_item_row_version"),
                        "expected_target_item_row_version",
                    ),
                    expected_target_revision_id=_uuid(
                        body.get("expected_target_revision_id"),
                        "expected_target_revision_id",
                    ),
                    expected_source_revision_id=_optional_uuid(
                        body.get("expected_source_revision_id"),
                        "expected_source_revision_id",
                    ),
                    preview_sha256=_sha256(
                        body.get("preview_sha256"), "preview_sha256"
                    ),
                    editor_session_id=_uuid(
                        body.get("editor_session_id"), "editor_session_id"
                    ),
                    lease_id=_uuid(body.get("lease_id"), "lease_id"),
                    fencing_generation=_fencing_generation(),
                    raw_token=_lease_token(),
                    operation_id=_operation_id(body),
                    request_id=rid,
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def preview_merge(self, space_id: str):
        def execute(rid: str, p: _Principal):
            _require_empty_query()
            body = _read_json(
                required=frozenset(
                    {
                        "result_markdown",
                        "source_item_id",
                        "source_revision_id",
                        "target_item_id",
                        "target_revision_id",
                    }
                )
            )
            service = self.curation
            if service is None:
                raise RuntimeError("The canonical curation service is unavailable.")
            result = service.preview_merge(
                MergePreviewCommand(
                    principal=self._session_principal(p),
                    space_id=_uuid(space_id, "space_id"),
                    source_item_id=_uuid(body.get("source_item_id"), "source_item_id"),
                    source_revision_id=_uuid(
                        body.get("source_revision_id"), "source_revision_id"
                    ),
                    target_item_id=_uuid(body.get("target_item_id"), "target_item_id"),
                    target_revision_id=_uuid(
                        body.get("target_revision_id"), "target_revision_id"
                    ),
                    result_markdown=_publication_markdown(body.get("result_markdown")),
                    request_id=rid,
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def apply_merge(self, space_id: str):
        def execute(rid: str, p: _Principal):
            _require_empty_query()
            body = _read_json(
                required=frozenset(
                    {
                        "operation_id",
                        "preview_sha256",
                        "result_markdown",
                        "source_editor_session_id",
                        "source_item_id",
                        "source_lease_id",
                        "source_revision_id",
                        "target_editor_session_id",
                        "target_item_id",
                        "target_lease_id",
                        "target_revision_id",
                    }
                )
            )
            service = self.curation
            if service is None:
                raise RuntimeError("The canonical curation service is unavailable.")
            result = service.apply_merge(
                MergeApplyCommand(
                    principal=self._session_principal(p),
                    space_id=_uuid(space_id, "space_id"),
                    source_item_id=_uuid(body.get("source_item_id"), "source_item_id"),
                    source_revision_id=_uuid(
                        body.get("source_revision_id"), "source_revision_id"
                    ),
                    target_item_id=_uuid(body.get("target_item_id"), "target_item_id"),
                    target_revision_id=_uuid(
                        body.get("target_revision_id"), "target_revision_id"
                    ),
                    result_markdown=_publication_markdown(body.get("result_markdown")),
                    preview_sha256=_sha256(
                        body.get("preview_sha256"), "preview_sha256"
                    ),
                    source_editor_session_id=_uuid(
                        body.get("source_editor_session_id"),
                        "source_editor_session_id",
                    ),
                    source_lease_id=_uuid(
                        body.get("source_lease_id"), "source_lease_id"
                    ),
                    source_fencing_generation=_fencing_generation_header(
                        "X-Knowledge-Source-Fence"
                    ),
                    source_raw_token=_lease_token_header(
                        "X-Knowledge-Source-Lease-Token"
                    ),
                    target_editor_session_id=_uuid(
                        body.get("target_editor_session_id"),
                        "target_editor_session_id",
                    ),
                    target_lease_id=_uuid(
                        body.get("target_lease_id"), "target_lease_id"
                    ),
                    target_fencing_generation=_fencing_generation_header(
                        "X-Knowledge-Target-Fence"
                    ),
                    target_raw_token=_lease_token_header(
                        "X-Knowledge-Target-Lease-Token"
                    ),
                    operation_id=_operation_id(body),
                    request_id=rid,
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), rid)

        return self.run(execute)

    def list_activity(self, space_id: str):
        def execute(rid: str, p: _Principal):
            limit, before_occurred_at_ms, before_event_uuid = (
                _management_list_options("activity")
            )
            result = self.management_reads.list_activity(
                SpaceManagementPageRequest(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    space_id=_uuid(space_id, "space_id"),
                    before_updated_at_ms=before_occurred_at_ms,
                    before_id=before_event_uuid,
                    limit=limit,
                )
            )
            next_cursor = None
            if result.has_more and result.items:
                last = result.items[-1]
                next_cursor = _encode_management_cursor(
                    "activity", last.occurred_at_ms, last.event_uuid
                )
            return _json_response(
                {
                    "corpus_closure_sha256": result.corpus_closure_sha256,
                    "has_more": result.has_more,
                    "items": [
                        {
                            "action": event.action,
                            "capabilities": list(event.capabilities),
                            "actor": {
                                "display_name": event.actor_display_name,
                                "kind": event.actor_kind,
                                "role_snapshot": event.actor_role_snapshot,
                                "user_id": event.actor_user_id,
                            },
                            "base_revision_id": event.base_revision_id,
                            "event_uuid": event.event_uuid,
                            "inherited": event.inherited,
                            "item_id": event.item_id,
                            "new_revision_id": event.new_revision_id,
                            "occurred_at": _utc(event.occurred_at_ms),
                            "outcome": event.outcome,
                            "origin": event.origin,
                            "overlay_grant_id": event.overlay_grant_id,
                            "reason": event.reason,
                            "selected_view_space_id": event.selected_view_space_id,
                            "source_space_id": event.space_id,
                            "space_id": event.space_id,
                        }
                        for event in result.items
                    ],
                    "next_cursor": next_cursor,
                },
                rid,
            )

        return self.run(execute)

    def add_pin(self, conversation_id: str):
        def execute(rid: str, p: _Principal):
            body = _read_json(required=frozenset({"operation_id", "item_id", "revision_id"}))
            operation_id = _operation_id(body)
            result = self.conversations.add_pin(
                AddConversationPinCommand(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    operation_id=operation_id,
                    request_id=rid,
                    conversation_id=_uuid(conversation_id, "conversation_id"),
                    item_id=_uuid(body["item_id"], "item_id"),
                    revision_id=_uuid(body["revision_id"], "revision_id"),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(
                {
                    "operation_id": operation_id,
                    "resource_id": result.item_id,
                    "row_version": None,
                    "status": result.outcome,
                },
                rid,
                201,
            )
        return self.run(execute)

    def remove_pin(self, conversation_id: str, item_id: str):
        def execute(rid: str, p: _Principal):
            body = _read_json(required=frozenset({"operation_id"}))
            operation_id = _operation_id(body)
            result = self.conversations.remove_pin(
                RemoveConversationPinCommand(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    operation_id=operation_id,
                    request_id=rid,
                    conversation_id=_uuid(conversation_id, "conversation_id"),
                    item_id=_uuid(item_id, "item_id"),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(
                {
                    "operation_id": operation_id,
                    "resource_id": result.item_id,
                    "row_version": None,
                    "status": result.outcome,
                },
                rid,
            )
        return self.run(execute)

    def add_context(self, conversation_id: str):
        def execute(rid: str, p: _Principal):
            body = _read_json(
                required=frozenset({"operation_id", "source_kind", "source_reference", "binding_mode"}),
                optional=frozenset({"display_name"}),
            )
            kind_map = {
                "workspace": "workspace_file",
                "project": "project_context",
                "notion": "notion_page",
                "source_artifact": "source_artifact",
            }
            source_kind = body["source_kind"]
            if source_kind not in kind_map or body["binding_mode"] not in {"snapshot", "live"}:
                raise _RequestError("VALIDATION_ERROR", "Context binding kind or mode is invalid.", 400)
            if "display_name" in body:
                _bounded_text(body["display_name"], "display_name", 300)
            operation_id = _operation_id(body)
            result = self.conversations.add_context(
                AddConversationContextCommand(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    operation_id=operation_id,
                    request_id=rid,
                    conversation_id=_uuid(conversation_id, "conversation_id"),
                    binding_type=kind_map[source_kind],
                    binding_mode=body["binding_mode"],
                    external_key=_bounded_text(body["source_reference"], "source_reference", 1000),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(
                {
                    "operation_id": operation_id,
                    "resource_id": result.binding.id,
                    "row_version": result.binding.row_version,
                    "status": result.outcome,
                },
                rid,
                201,
            )
        return self.run(execute)

    def remove_context(self, conversation_id: str, binding_id: str):
        def execute(rid: str, p: _Principal):
            body = _read_json(required=frozenset({"operation_id"}))
            operation_id = _operation_id(body)
            result = self.conversations.remove_context(
                RemoveConversationContextCommand(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    operation_id=operation_id,
                    request_id=rid,
                    conversation_id=_uuid(conversation_id, "conversation_id"),
                    binding_id=_uuid(binding_id, "binding_id"),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(
                {
                    "operation_id": operation_id,
                    "resource_id": result.binding.id,
                    "row_version": result.binding.row_version,
                    "status": result.outcome,
                },
                rid,
            )
        return self.run(execute)

    def compact(self, conversation_id: str):
        def execute(rid: str, p: _Principal):
            body = _read_json(required=frozenset({"operation_id", "through_message_sequence"}))
            operation_id = _operation_id(body)
            cid = _uuid(conversation_id, "conversation_id")
            through = _positive(body["through_message_sequence"], "through_message_sequence")
            completion = self.conversation_summarizer.summarize(
                actor_user_id=p.user_id,
                actor_scope_epoch=p.scope_epoch,
                conversation_id=cid,
                through_message_sequence=through,
            )
            if not isinstance(completion, CompactSummaryCompletion):
                raise TypeError("conversation summarizer returned an invalid completion")
            result = self.conversations.compact(
                CompactConversationCommand(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    operation_id=operation_id,
                    request_id=rid,
                    conversation_id=cid,
                    covered_through_sequence_no=through,
                    summary_md=completion.summary_md,
                    model_id=completion.model_id,
                    prompt_version=completion.prompt_version,
                    summarizer_version=completion.summarizer_version,
                    request_meta=_request_meta(),
                )
            )
            return _json_response(
                {
                    "operation_id": operation_id,
                    "resource_id": result.summary.id,
                    "row_version": result.summary.row_version,
                    "status": result.outcome,
                },
                rid,
                202,
            )
        return self.run(execute)

    def turn_stream(self, conversation_id: str):
        def execute(rid: str, p: _Principal):
            body = _read_json(
                required=frozenset({"operation_id", "message"}),
                optional=frozenset({"resume_after_event_sequence"}),
            )
            operation_id = _operation_id(body)
            cid = _uuid(conversation_id, "conversation_id")
            resume_after = _nonnegative(body.get("resume_after_event_sequence", 0), "resume_after_event_sequence")
            command = (
                AcceptConversationTurnCommand(
                    actor_user_id=p.user_id,
                    actor_scope_epoch=p.scope_epoch,
                    operation_id=operation_id,
                    request_id=rid,
                    conversation_id=cid,
                    user_content={"text": _message_text(body["message"])},
                    request_meta=_request_meta(),
                )
            )
            def complete_accepted(accepted):
                try:
                    context = self.agent_context.compose(
                        AgentTurnContextRequest(
                            actor_user_id=p.user_id,
                            actor_scope_epoch=p.scope_epoch,
                            conversation_id=cid,
                            turn_id=accepted.turn_id,
                        )
                    )
                    completion = self.turn_executor.complete(context)
                    if not isinstance(completion, AgentTurnCompletion):
                        raise TypeError("turn executor returned an invalid completion")
                    completed = self.conversations.complete_turn(
                        CompleteConversationTurnCommand(
                            actor_user_id=p.user_id,
                            actor_scope_epoch=p.scope_epoch,
                            operation_id=operation_id,
                            request_id=accepted.request_id,
                            conversation_id=cid,
                            assistant_content=completion.assistant_content,
                            final_response=completion.final_response,
                            request_meta=_request_meta(),
                        )
                    )
                except Exception as exc:
                    observed_code = getattr(exc, "code", None)
                    _LOGGER.warning(
                        "Canonical Agent turn execution failed: exception_type=%s code=%s%s",
                        type(exc).__name__,
                        observed_code if isinstance(observed_code, str) else "unclassified",
                        (
                            f" detail={str(exc)[:256]}"
                            if os.environ.get("CODE_ASSIST_TEST_MODE") == "1"
                            else ""
                        ),
                    )
                    terminal_code = (
                        observed_code
                        if isinstance(observed_code, str)
                        and observed_code in ANSWER_API_FAILURE_CODES | {
                            "CODEX_CLI_NOT_FOUND",
                            "CODEX_EMPTY_OUTPUT",
                            "CODEX_EXEC_FAILED",
                            "CODEX_EXEC_TIMEOUT",
                            "CODEX_NOT_AUTHENTICATED",
                            "CODEX_OUTPUT_INVALID",
                            "CORPUS_CLOSURE_STALE",
                            "SCOPE_STALE",
                            "WIKI_ARCHIVED_HISTORY_ONLY",
                        }
                        else "AGENT_EXECUTION_FAILED"
                    )
                    self.conversations.terminate_turn(
                        TerminateConversationTurnCommand(
                            actor_user_id=p.user_id,
                            actor_scope_epoch=p.scope_epoch,
                            operation_id=operation_id,
                            request_id=accepted.request_id,
                            conversation_id=cid,
                            terminal_state="failed",
                            error_code=terminal_code,
                            retryable=bool(getattr(exc, "retryable", False)),
                            request_meta=_request_meta(),
                        )
                    )
            self.conversations.execute_turn(command, complete_accepted)
            stored, events = self.conversations.reconnect_turn(
                actor_user_id=p.user_id,
                actor_scope_epoch=p.scope_epoch,
                conversation_id=cid,
                operation_id=operation_id,
                after_event_sequence_no=resume_after,
            )
            chunks = _sse_chunks(stored, events)
            response = Response("".join(chunks), status=200, mimetype="text/event-stream")
            response.headers["Cache-Control"] = "no-store, no-cache"
            response.headers["X-Accel-Buffering"] = "no"
            response.headers["X-Request-ID"] = rid
            return response
        return self.run(execute)


def _require_methods(value: object, name: str, methods: tuple[str, ...]) -> None:
    for method in methods:
        if not callable(getattr(value, method, None)):
            raise TypeError(f"{name} is missing {method}().")


def _effective_methods(methods: tuple[str, ...] | set[str]) -> frozenset[str]:
    effective = {str(method).upper() for method in methods}
    if "GET" in effective:
        effective.add("HEAD")
    effective.add("OPTIONS")
    return frozenset(effective)


def _validate_route_target(target: Flask | Blueprint) -> None:
    if isinstance(target, Flask) and bool(getattr(target, "_got_first_request", False)):
        raise RuntimeError("canonical routes must register before the first request")
    if isinstance(target, Blueprint):
        if bool(getattr(target, "_got_registered_once", False)):
            raise RuntimeError(
                "canonical routes must register before the Blueprint is attached"
            )
        if tuple(target.deferred_functions):
            raise RuntimeError("canonical routes require a dedicated empty Blueprint")
        callback_mappings = (
            target.before_request_funcs,
            target.after_request_funcs,
            target.teardown_request_funcs,
            target.error_handler_spec,
            target.url_value_preprocessors,
            target.url_default_functions,
        )
        has_callbacks = any(
            bool(value)
            for mapping in callback_mappings
            for value in mapping.values()
        )
        if (
            has_callbacks
            or bool(target.view_functions)
            or bool(getattr(target, "_blueprints", ()))
            or target.static_folder is not None
        ):
            raise RuntimeError("canonical routes require a dedicated empty Blueprint")


def _preflight_existing_routes(
    target: Flask,
    specs: tuple[tuple[str, str, tuple[str, ...], Callable[..., object]], ...],
) -> None:
    existing_endpoints = {rule.endpoint for rule in target.url_map.iter_rules()}
    existing_rules = {
        (_VARIABLE_RULE_RE.sub("<>", rule.rule), method)
        for rule in target.url_map.iter_rules()
        for method in _effective_methods(set(rule.methods))
    }
    for rule, endpoint, methods, _ in specs:
        if endpoint in existing_endpoints or endpoint in target.view_functions:
            raise RuntimeError(f"canonical route endpoint already exists: {endpoint}")
        normalized = _VARIABLE_RULE_RE.sub("<>", rule)
        for method in _effective_methods(methods):
            if (normalized, method) in existing_rules:
                raise RuntimeError(f"canonical route already exists: {method} {rule}")


def _blueprint_attach_preflight(
    blueprint: Blueprint,
    specs: tuple[tuple[str, str, tuple[str, ...], Callable[..., object]], ...],
) -> None:
    def verify(state: object) -> None:
        app = getattr(state, "app", None)
        if getattr(state, "url_prefix", None):
            raise RuntimeError("canonical Blueprint routes cannot use a URL prefix")
        if getattr(state, "subdomain", None):
            raise RuntimeError("canonical Blueprint routes cannot use a subdomain")
        if getattr(state, "url_defaults", None):
            raise RuntimeError("canonical Blueprint routes cannot use URL defaults")
        url_map = getattr(app, "url_map", None)
        if url_map is None or not callable(getattr(url_map, "iter_rules", None)):
            raise RuntimeError("Blueprint registration cannot inspect the host URL map")
        blueprint_name = str(getattr(state, "name", ""))
        name_prefix = str(getattr(state, "name_prefix", ""))
        if not blueprint_name:
            raise RuntimeError("Blueprint registration has no stable name")
        final_prefix = f"{name_prefix}.{blueprint_name}".lstrip(".")
        existing_endpoints = {rule.endpoint for rule in url_map.iter_rules()}
        view_functions = getattr(app, "view_functions", {})
        existing_rules = {
            (_VARIABLE_RULE_RE.sub("<>", rule.rule), method)
            for rule in url_map.iter_rules()
            for method in _effective_methods(set(rule.methods))
        }
        for rule, endpoint, methods, _ in specs:
            final_endpoint = f"{final_prefix}.{endpoint}"
            if final_endpoint in existing_endpoints or final_endpoint in view_functions:
                raise RuntimeError(
                    "canonical route endpoint already exists on Blueprint host: "
                    f"{final_endpoint}"
                )
            normalized = _VARIABLE_RULE_RE.sub("<>", rule)
            for method in _effective_methods(methods):
                if (normalized, method) in existing_rules:
                    raise RuntimeError(
                        "canonical route already exists on Blueprint host: "
                        f"{method} {rule}"
                    )

    # Flask replays route deferred functions for every named Blueprint attach.
    blueprint.record(verify)


def _publication_route_specs(
    handlers: _Handlers,
    *,
    include_spaces: bool,
) -> tuple[tuple[str, str, tuple[str, ...], Callable[..., object]], ...]:
    specs: tuple[
        tuple[str, str, tuple[str, ...], Callable[..., object]], ...
    ] = ()
    if include_spaces:
        specs += (
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces",
                "knowledge_v1_spaces",
                ("GET",),
                handlers.spaces,
            ),
        )
    if callable(getattr(handlers.publication, "resume_pdf_preview", None)):
        specs += ((
            f"{CANONICAL_ROUTE_PREFIX}/publication-previews/<preview_id>/resume",
            "knowledge_v1_pdf_publication_preview_resume",
            ("POST",),
            handlers.resume_pdf_publication_preview,
        ),)
    return specs + (
        (
            f"{CANONICAL_ROUTE_PREFIX}/publication-previews",
            "knowledge_v1_publication_preview_create",
            ("POST",),
            handlers.create_publication_preview,
        ),
        (
            f"{CANONICAL_ROUTE_PREFIX}/publication-previews/<preview_id>",
            "knowledge_v1_publication_preview_get",
            ("GET",),
            handlers.get_publication_preview,
        ),
        (
            f"{CANONICAL_ROUTE_PREFIX}/publication-previews/<preview_id>",
            "knowledge_v1_publication_preview_cancel",
            ("DELETE",),
            handlers.cancel_publication_preview,
        ),
        (
            f"{CANONICAL_ROUTE_PREFIX}/publications",
            "knowledge_v1_publication_commit",
            ("POST",),
            handlers.commit_publication,
        ),
    )


def _register_specs(
    target: Flask | Blueprint,
    specs: tuple[tuple[str, str, tuple[str, ...], Callable[..., object]], ...],
) -> tuple[str, ...]:
    if isinstance(target, Flask):
        _preflight_existing_routes(target, specs)
    else:
        _blueprint_attach_preflight(target, specs)
    for rule, endpoint, methods, handler in specs:
        target.add_url_rule(
            rule,
            endpoint=endpoint,
            view_func=handler,
            methods=list(methods),
        )
    return tuple(rule for rule, _, _, _ in specs)


def register_publication_routes(
    target: Flask | Blueprint,
    *,
    scope_selection_service: object,
    publication_service: object,
    principal_resolver: Callable[[], object],
    publication_status_reader: object | None = None,
) -> tuple[str, ...]:
    """Register the standalone P4.5 publication and target-discovery surface.

    The Notion host does not own the P3.7 conversation/read surface.  It still
    needs the same authenticated publication endpoints and concrete personal/
    current-part labels.  This registrar therefore exposes only ``GET /spaces``
    plus the four publication routes and cannot accidentally bind conversation,
    retrieval, management, or Agent execution dependencies.
    """

    if not isinstance(target, (Flask, Blueprint)):
        raise TypeError("canonical route target must be a Flask app or Blueprint")
    _validate_route_target(target)
    if getattr(target, _PUBLICATION_REGISTERED_ATTRIBUTE, False):
        raise RuntimeError("canonical P4.5 publication routes are already registered")
    if not callable(principal_resolver):
        raise TypeError("principal_resolver must be callable")
    _require_methods(scope_selection_service, "scope_selection_service", ("read",))
    _require_methods(
        publication_service,
        "publication_service",
        (
            "cancel_preview",
            "create_chat_preview",
            "create_manual_preview",
            "create_notion_preview",
            "create_upload_preview",
            "get_preview",
            "publish",
        ),
    )
    if publication_status_reader is not None:
        _require_methods(publication_status_reader, "publication_status_reader", ("request", "validate_bindings"))
        publication_status_reader.validate_bindings(principal_resolver, publication_service)
    handlers = _Handlers(
        scope_selection=scope_selection_service,
        conversations=None,
        retrieval=None,
        management_reads=None,
        agent_context=None,
        turn_executor=None,  # type: ignore[arg-type]
        conversation_summarizer=None,  # type: ignore[arg-type]
        principal_resolver=principal_resolver,
        publication=publication_service,
        publication_status_reader=publication_status_reader,
        item_editing=None,
    )
    specs = _publication_route_specs(handlers, include_spaces=True)
    registered = _register_specs(target, specs)
    setattr(target, _PUBLICATION_REGISTERED_ATTRIBUTE, True)
    return registered


def register_canonical_routes(
    target: Flask | Blueprint,
    *,
    scope_selection_service: object,
    conversation_service: object,
    retrieval_service: object,
    management_read_service: object,
    agent_context_service: object,
    turn_executor: AgentTurnExecutor,
    conversation_summarizer: ConversationSummarizer,
    principal_resolver: Callable[[], object],
    publication_service: object | None = None,
    publication_status_reader: object | None = None,
    item_edit_service: object | None = None,
    item_lifecycle_service: object | None = None,
    item_purge_service: object | None = None,
    curation_service: object | None = None,
    job_service: object | None = None,
    job_mutations_enabled: bool = False,
    source_artifact_service: object | None = None,
) -> tuple[str, ...]:
    """Register reads plus explicitly bound mutation/review route groups."""

    if not isinstance(target, (Flask, Blueprint)):
        raise TypeError("canonical route target must be a Flask app or Blueprint")
    _validate_route_target(target)
    if getattr(target, _REGISTERED_ATTRIBUTE, False):
        raise RuntimeError("canonical P3.7 routes are already registered")
    if publication_service is not None and getattr(
        target, _PUBLICATION_REGISTERED_ATTRIBUTE, False
    ):
        raise RuntimeError("canonical P4.5 publication routes are already registered")
    if not callable(principal_resolver):
        raise TypeError("principal_resolver must be callable")
    if not isinstance(job_mutations_enabled, bool):
        raise TypeError("job_mutations_enabled must be a Boolean")
    if job_mutations_enabled and job_service is None:
        raise TypeError("job mutations require a bound durable-job service")
    _require_methods(scope_selection_service, "scope_selection_service", ("read",))
    _require_methods(
        conversation_service,
        "conversation_service",
        (
            "accept_turn", "execute_turn", "complete_turn", "create", "get", "get_active_summary",
            "add_context", "add_pin", "compact", "list_contexts", "list_for_owner",
            "list_messages", "read_message_page", "read_turn_answer", "list_pending_turns", "list_pins", "reconnect_turn", "remove_context",
            "remove_pin", "terminate_turn", "update",
        ),
    )
    _require_methods(retrieval_service, "retrieval_service", ("retrieve_for_space",))
    _require_methods(
        management_read_service,
        "management_read_service",
        (
            "compare_revisions",
            "get_item",
            "get_revision",
            "graph",
            "list_activity",
            "list_findings",
            "list_items",
            "list_revisions",
            "projection_status",
        ),
    )
    _require_methods(agent_context_service, "agent_context_service", ("compose",))
    _require_methods(turn_executor, "turn_executor", ("complete",))
    _require_methods(conversation_summarizer, "conversation_summarizer", ("summarize",))
    if publication_service is not None:
        _require_methods(
            publication_service,
            "publication_service",
            (
                "cancel_preview",
                "create_chat_preview",
                "create_manual_preview",
                "create_notion_preview",
                "create_upload_preview",
                "get_preview",
                "publish",
            ),
        )
    if publication_status_reader is not None:
        _require_methods(publication_status_reader, "publication_status_reader", ("request", "validate_bindings"))
        publication_status_reader.validate_bindings(principal_resolver, publication_service)
    if item_edit_service is not None:
        _require_methods(
            item_edit_service,
            "item_edit_service",
            ("acquire", "force_release", "heartbeat", "release", "save"),
        )
    if item_lifecycle_service is not None:
        _require_methods(
            item_lifecycle_service,
            "item_lifecycle_service",
            ("deactivate", "restore"),
        )
    if item_purge_service is not None:
        _require_methods(
            item_purge_service,
            "item_purge_service",
            ("preview", "purge"),
        )
    if curation_service is not None:
        _require_methods(
            curation_service,
            "curation_service",
            (
                "apply_merge",
                "apply_proposal",
                "get_finding",
                "preview_merge",
                "preview_proposal",
                "read_shadow_metrics",
                "rebase_proposal",
                "review_finding",
            ),
        )
    if job_service is not None:
        _require_methods(
            job_service,
            "job_service",
            ("cancel", "read"),
        )
    if source_artifact_service is not None:
        _require_methods(
            source_artifact_service,
            "source_artifact_service",
            ("download", "list_for_item"),
        )
    handlers = _Handlers(
        scope_selection=scope_selection_service,
        conversations=conversation_service,
        retrieval=retrieval_service,
        management_reads=management_read_service,
        agent_context=agent_context_service,
        turn_executor=turn_executor,
        conversation_summarizer=conversation_summarizer,
        principal_resolver=principal_resolver,
        publication=publication_service,
        publication_status_reader=publication_status_reader,
        item_editing=item_edit_service,
        item_lifecycle=item_lifecycle_service,
        item_purge=item_purge_service,
        curation=curation_service,
        jobs=job_service,
        source_artifacts=source_artifact_service,
    )
    specs: tuple[tuple[str, str, tuple[str, ...], Callable[..., object]], ...] = (
        (f"{CANONICAL_ROUTE_PREFIX}/spaces", "knowledge_v1_spaces", ("GET",), handlers.spaces),
        (f"{CANONICAL_ROUTE_PREFIX}/conversations", "knowledge_v1_conversations_list", ("GET",), handlers.list_conversations),
        (f"{CANONICAL_ROUTE_PREFIX}/conversations", "knowledge_v1_conversations_create", ("POST",), handlers.create_conversation),
        (f"{CANONICAL_ROUTE_PREFIX}/conversations/<conversation_id>", "knowledge_v1_conversation_get", ("GET",), handlers.get_conversation),
        (f"{CANONICAL_ROUTE_PREFIX}/conversations/<conversation_id>/pending-turns", "knowledge_v1_pending_turns", ("GET",), handlers.pending_turns),
        (f"{CANONICAL_ROUTE_PREFIX}/conversations/<conversation_id>/messages", "knowledge_v1_message_page", ("GET",), handlers.message_page),
        (f"{CANONICAL_ROUTE_PREFIX}/conversations/<conversation_id>/turns/<operation_id>/answer", "knowledge_v1_turn_answer", ("GET",), handlers.turn_answer),
        (f"{CANONICAL_ROUTE_PREFIX}/conversations/<conversation_id>", "knowledge_v1_conversation_update", ("PATCH",), handlers.update_conversation),
        (f"{CANONICAL_ROUTE_PREFIX}/conversations/<conversation_id>/turns/stream", "knowledge_v1_turn_stream", ("POST",), handlers.turn_stream),
        (f"{CANONICAL_ROUTE_PREFIX}/conversations/<conversation_id>/compact", "knowledge_v1_conversation_compact", ("POST",), handlers.compact),
        (f"{CANONICAL_ROUTE_PREFIX}/conversations/<conversation_id>/pins", "knowledge_v1_pin_add", ("POST",), handlers.add_pin),
        (f"{CANONICAL_ROUTE_PREFIX}/conversations/<conversation_id>/pins/<item_id>", "knowledge_v1_pin_remove", ("DELETE",), handlers.remove_pin),
        (f"{CANONICAL_ROUTE_PREFIX}/conversations/<conversation_id>/context-bindings", "knowledge_v1_context_add", ("POST",), handlers.add_context),
        (f"{CANONICAL_ROUTE_PREFIX}/conversations/<conversation_id>/context-bindings/<binding_id>", "knowledge_v1_context_remove", ("DELETE",), handlers.remove_context),
        (f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items", "knowledge_v1_items_list", ("GET",), handlers.list_items),
        (f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>", "knowledge_v1_item_get", ("GET",), handlers.get_item),
        (f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>/revisions", "knowledge_v1_revisions_list", ("GET",), handlers.list_revisions),
        (f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>/revisions/<revision_id>", "knowledge_v1_revision_get", ("GET",), handlers.get_revision),
        (f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>/compare", "knowledge_v1_revisions_compare", ("GET",), handlers.compare_revisions),
        (f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/search", "knowledge_v1_space_search", ("POST",), handlers.search),
        (f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/graph", "knowledge_v1_space_graph", ("GET",), handlers.graph),
        (f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/activity", "knowledge_v1_activity_list", ("GET",), handlers.list_activity),
        (f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/projection-status", "knowledge_v1_projection_status", ("GET",), handlers.projection_status),
        (f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/curation/findings", "knowledge_v1_findings_list", ("GET",), handlers.list_findings),
    )
    if publication_service is not None:
        specs += _publication_route_specs(handlers, include_spaces=False)
    if source_artifact_service is not None:
        specs += (
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>/source-artifacts",
                "knowledge_v1_item_source_artifacts_list",
                ("GET",),
                handlers.list_item_source_artifacts,
            ),
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>/source-artifacts/<artifact_id>/download",
                "knowledge_v1_item_source_artifact_download",
                ("GET",),
                handlers.download_item_source_artifact,
            ),
        )
    if item_edit_service is not None:
        specs += (
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>",
                "knowledge_v1_item_update",
                ("PUT",),
                handlers.update_item,
            ),
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>/lease/acquire",
                "knowledge_v1_item_lease_acquire",
                ("POST",),
                handlers.acquire_item_lease,
            ),
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>/lease/heartbeat",
                "knowledge_v1_item_lease_heartbeat",
                ("POST",),
                handlers.heartbeat_item_lease,
            ),
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>/lease/release",
                "knowledge_v1_item_lease_release",
                ("POST",),
                handlers.release_item_lease,
            ),
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>/lease/force-release",
                "knowledge_v1_item_lease_force_release",
                ("POST",),
                handlers.force_release_item_lease,
            ),
        )
    if item_lifecycle_service is not None:
        specs += (
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>/deactivate",
                "knowledge_v1_item_deactivate",
                ("POST",),
                handlers.deactivate_item,
            ),
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>/restore",
                "knowledge_v1_item_restore",
                ("POST",),
                handlers.restore_item,
            ),
        )
    if item_purge_service is not None:
        specs += (
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>/purge-preview",
                "knowledge_v1_item_purge_preview",
                ("GET",),
                handlers.preview_item_purge,
            ),
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/items/<item_id>/purge",
                "knowledge_v1_item_purge",
                ("POST",),
                handlers.purge_item,
            ),
        )
    if curation_service is not None:
        specs += (
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/curation/findings/<finding_id>",
                "knowledge_v1_finding_get",
                ("GET",),
                handlers.get_finding,
            ),
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/curation/findings/<finding_id>/review",
                "knowledge_v1_finding_review",
                ("POST",),
                handlers.review_finding,
            ),
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/curation/shadow-metrics",
                "knowledge_v1_curation_shadow_metrics",
                ("GET",),
                handlers.curation_shadow_metrics,
            ),
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/change-proposals/<proposal_id>/preview",
                "knowledge_v1_proposal_preview",
                ("POST",),
                handlers.preview_proposal,
            ),
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/change-proposals/<proposal_id>/rebase",
                "knowledge_v1_proposal_rebase",
                ("POST",),
                handlers.rebase_proposal,
            ),
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/change-proposals/<proposal_id>/apply",
                "knowledge_v1_proposal_apply",
                ("POST",),
                handlers.apply_proposal,
            ),
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/merges/preview",
                "knowledge_v1_merge_preview",
                ("POST",),
                handlers.preview_merge,
            ),
            (
                f"{CANONICAL_ROUTE_PREFIX}/spaces/<space_id>/merges/apply",
                "knowledge_v1_merge_apply",
                ("POST",),
                handlers.apply_merge,
            ),
        )
    if job_service is not None:
        specs += (
            (
                f"{CANONICAL_ROUTE_PREFIX}/jobs/<job_id>",
                "knowledge_v1_job_get",
                ("GET",),
                handlers.get_durable_job,
            ),
        )
        if job_mutations_enabled:
            specs += (
                (
                    f"{CANONICAL_ROUTE_PREFIX}/jobs/<job_id>/cancel",
                    "knowledge_v1_job_cancel",
                    ("POST",),
                    handlers.cancel_durable_job,
                ),
            )
    registered = _register_specs(target, specs)
    setattr(target, _REGISTERED_ATTRIBUTE, True)
    from .services.source_artifacts import SourceArtifactService
    if isinstance(source_artifact_service, SourceArtifactService):
        from .workspace_feature_routes import register_workspace_feature_routes
        register_workspace_feature_routes(
            target, source_artifact_service, management_read_service, principal_resolver,
        )
    if publication_service is not None:
        setattr(target, _PUBLICATION_REGISTERED_ATTRIBUTE, True)
    return registered


__all__ = [
    "AgentTurnCompletion",
    "AgentTurnExecutor",
    "CANONICAL_ROUTE_PREFIX",
    "CompactSummaryCompletion",
    "ConversationSummarizer",
    "MAX_CANONICAL_JSON_BYTES",
    "MAX_CANONICAL_MARKDOWN_CHARS",
    "MAX_CANONICAL_PUBLICATION_JSON_BYTES",
    "MAX_CANONICAL_UPLOAD_BYTES",
    "register_canonical_routes",
    "register_publication_routes",
]


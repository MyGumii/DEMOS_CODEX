"""Crash-safe offline delivery of legacy page locator projections.

This module deliberately does not register a background worker.  A caller must
explicitly attest that the exact legacy owner sources are quiesced before one
bounded batch can be processed.  Delivery state is owned by the injected
workspace store; the orchestration below never opens or scans a catalog.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
import re
import sqlite3
from typing import Any, Protocol

from code_assist_v1.notion_workspace_inventory import WorkspaceInventoryError
from code_assist_v1.notion_workspace_projection import (
    LegacyProjectionAmbiguousError,
    LegacyProjectionPermissionError,
    LegacyProjectionStaleError,
    LegacyProjectionSupersededError,
    UnsafeLegacyFileError,
    WorkspaceProjectionError,
)
from code_assist_v1.notion_workspace_store import (
    WorkspaceCatalogFingerprintConflict,
    WorkspaceOperationConflict,
    WorkspaceRevisionConflict,
    WorkspaceSubstrateUnavailable,
)


PROJECTION_CONSUMER_VERSION = "np2a-page-locator-projection-v1"
PAGE_SOURCE_ACTIVATED_EVENT = "workspace.page_source_bound"
PAGE_SOURCE_AGGREGATE = "legacy_page_source"
DEFAULT_BATCH_LIMIT = 32
MAX_BATCH_LIMIT = 32
DEFAULT_LEASE_SECONDS = 60
MAX_LEASE_SECONDS = 15 * 60
DEFAULT_MAX_ATTEMPTS = 8
MAX_MAX_ATTEMPTS = 16
DEFAULT_RETRY_BASE_SECONDS = 2
MAX_RETRY_SECONDS = 5 * 60
MAX_EVENT_JSON_BYTES = 16 * 1024

_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_SUBJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
_RETRYABLE_CAS_MESSAGES = frozenset(
    {
        # These are the current store's explicit cross-snapshot/CAS fences.
        # They are inspected only for classification and are never reported.
        "active page binding generation is stale",
        "legacy page locator projection is invalid or stale",
        "page binding placement changed",
    }
)


class ProjectionOutboxStore(Protocol):
    """Narrow adapter implemented by the workspace delivery store."""

    def claim_pending_outbox(
        self,
        consumer: str,
        worker: str,
        *,
        limit: int,
        now: int | None,
        lease_seconds: int,
        max_attempts: int,
    ) -> list[dict[str, Any]]: ...

    def ack_outbox_delivery(
        self,
        consumer: str,
        worker: str,
        lease_token: str,
        *,
        now: int | None,
    ) -> dict[str, Any]: ...

    def retry_outbox_delivery(
        self,
        consumer: str,
        worker: str,
        lease_token: str,
        error_code: str,
        *,
        now: int | None,
        backoff_seconds: int,
        max_attempts: int,
    ) -> dict[str, Any]: ...

    def dead_letter_outbox_delivery(
        self,
        consumer: str,
        worker: str,
        lease_token: str,
        error_code: str,
        *,
        now: int | None,
    ) -> dict[str, Any]: ...


Projector = Callable[..., Mapping[str, Any]]
SourcePathResolver = Callable[[str], str | os.PathLike[str]]


@dataclass(frozen=True)
class _ProjectionEvent:
    outbox_id: str
    lease_token: str
    actor_subject_id: str
    workspace_id: str
    source_owner_subject_id: str
    source_page_id: str
    binding_version: int
    attempt_count: int


@dataclass(frozen=True)
class _Disposition:
    retry: bool
    error_code: str


class _InvalidDelivery(Exception):
    def __init__(self, error_code: str):
        self.error_code = error_code
        super().__init__(error_code)


def _default_projector(**kwargs: Any) -> Mapping[str, Any]:
    # Lazy import keeps the orchestration usable with a fake projector and
    # avoids registering any runtime behavior merely by importing this module.
    from code_assist_v1.notion_workspace_projection import project_exact_owner_page

    return project_exact_owner_page(**kwargs)


def _bounded_integer(
    value: int,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def _worker_name(value: str, *, field: str) -> str:
    normalized = str(value or "")
    if not _NAME_RE.fullmatch(normalized):
        raise ValueError(f"valid {field} required")
    return normalized


def projection_effect_operation_id(
    *,
    consumer: str,
    outbox_id: str,
    attempt_count: int,
) -> str:
    """Return the stable idempotency key for one delivery attempt.

    The attempt is intentional.  A changed first projection bumps the
    workspace version, so replaying its operation ID after an ack crash would
    produce a different request hash during a fresh exact-owner capture.  The
    next leased attempt therefore gets a new deterministic key and performs a
    state-idempotent replacement (normally a semantic no-op).
    """
    stable_consumer = _worker_name(consumer, field="consumer")
    stable_outbox_id = str(outbox_id or "")
    if not stable_outbox_id or len(stable_outbox_id) > 200:
        raise ValueError("valid outbox_id required")
    attempt = _bounded_integer(
        attempt_count, field="attempt_count", minimum=1, maximum=MAX_MAX_ATTEMPTS
    )
    digest = hashlib.sha256(
        (stable_consumer + "\0" + stable_outbox_id + "\0" + str(attempt)).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"np2a-project:{digest}"


def retry_backoff_seconds(
    attempt_count: int,
    *,
    base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
) -> int:
    """Return a bounded deterministic exponential retry delay."""
    attempt = _bounded_integer(
        attempt_count, field="attempt_count", minimum=1, maximum=MAX_MAX_ATTEMPTS
    )
    base = _bounded_integer(
        base_seconds, field="base_seconds", minimum=1, maximum=MAX_RETRY_SECONDS
    )
    return min(MAX_RETRY_SECONDS, base * (2 ** min(attempt - 1, 16)))


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        raw_size = len(raw.encode("utf-8")) if isinstance(raw, str) else 0
    except UnicodeError as exc:
        raise _InvalidDelivery("invalid_event_payload") from exc
    if not isinstance(raw, str) or not raw or raw_size > MAX_EVENT_JSON_BYTES:
        raise _InvalidDelivery("invalid_event_payload")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _InvalidDelivery("invalid_event_payload")
            result[key] = value
        return result

    try:
        decoded = json.loads(raw, object_pairs_hook=unique_object)
    except _InvalidDelivery:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _InvalidDelivery("invalid_event_payload") from exc
    if not isinstance(decoded, dict):
        raise _InvalidDelivery("invalid_event_payload")
    return decoded


def _required_string(
    source: Mapping[str, Any],
    key: str,
    *,
    maximum: int = 200,
) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise _InvalidDelivery("invalid_event_payload")
    if len(value) > maximum or "\x00" in value:
        raise _InvalidDelivery("invalid_event_payload")
    return value


def _decode_projection_event(
    delivery: Mapping[str, Any],
    *,
    expected_consumer: str,
) -> _ProjectionEvent:
    try:
        event_type = str(delivery.get("event_type") or "")
        aggregate_type = str(delivery.get("aggregate_type") or "")
    except Exception as exc:
        raise _InvalidDelivery("invalid_delivery_envelope") from exc
    if (
        event_type != PAGE_SOURCE_ACTIVATED_EVENT
        or aggregate_type != PAGE_SOURCE_AGGREGATE
    ):
        raise _InvalidDelivery("event_not_allowlisted")
    if delivery.get("status") != "leased":
        raise _InvalidDelivery("invalid_delivery_envelope")
    if delivery.get("consumer") != expected_consumer:
        raise _InvalidDelivery("invalid_delivery_envelope")

    outbox_id = _required_string(delivery, "outbox_id")
    lease_token = _required_string(delivery, "lease_token")
    actor = _required_string(delivery, "actor_subject_id", maximum=64)
    if not _SUBJECT_RE.fullmatch(actor):
        raise _InvalidDelivery("invalid_delivery_envelope")
    workspace_id = _required_string(delivery, "workspace_id")
    payload = _json_object(delivery.get("payload_json"))
    payload_workspace = _required_string(payload, "workspace_id")
    owner = _required_string(payload, "source_owner_subject_id", maximum=64)
    if not _SUBJECT_RE.fullmatch(owner):
        raise _InvalidDelivery("invalid_event_payload")
    page_id = _required_string(payload, "source_page_id", maximum=160)
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise _InvalidDelivery("invalid_event_payload")
    if payload_workspace != workspace_id:
        raise _InvalidDelivery("event_envelope_mismatch")
    aggregate_id = _required_string(delivery, "aggregate_id", maximum=225)
    if aggregate_id != f"{owner}:{page_id}":
        raise _InvalidDelivery("event_envelope_mismatch")
    attempt = delivery.get("attempt_count")
    if (
        isinstance(attempt, bool)
        or not isinstance(attempt, int)
        or attempt < 1
        or attempt > MAX_MAX_ATTEMPTS
    ):
        raise _InvalidDelivery("invalid_delivery_envelope")
    return _ProjectionEvent(
        outbox_id=outbox_id,
        lease_token=lease_token,
        actor_subject_id=actor,
        workspace_id=workspace_id,
        source_owner_subject_id=owner,
        source_page_id=page_id,
        binding_version=version,
        attempt_count=attempt,
    )


def _sqlite_is_transient(exc: sqlite3.Error) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    transient_codes = {
        getattr(sqlite3, name)
        for name in ("SQLITE_BUSY", "SQLITE_LOCKED", "SQLITE_IOERR")
        if hasattr(sqlite3, name)
    }
    if code is not None:
        # Extended SQLite codes retain the primary result in the low byte.
        return int(code) & 0xFF in transient_codes
    return isinstance(exc, sqlite3.OperationalError)


def _classify_projection_error(exc: Exception) -> _Disposition:
    # Revision and cross-snapshot races are expected to converge after another
    # exact-owner capture.  They never include exception text in the report.
    if isinstance(exc, LegacyProjectionSupersededError):
        return _Disposition(False, "binding_generation_superseded")
    if isinstance(exc, LegacyProjectionStaleError):
        return _Disposition(True, "source_revision_stale")
    if isinstance(exc, WorkspaceRevisionConflict):
        return _Disposition(True, "workspace_revision_conflict")
    if isinstance(exc, WorkspaceCatalogFingerprintConflict):
        return _Disposition(True, "catalog_revision_conflict")
    if isinstance(exc, ValueError) and str(exc) in _RETRYABLE_CAS_MESSAGES:
        return _Disposition(True, "projection_cas_conflict")
    if isinstance(exc, WorkspaceSubstrateUnavailable):
        return _Disposition(True, "catalog_unavailable")
    if isinstance(exc, sqlite3.Error):
        if _sqlite_is_transient(exc):
            return _Disposition(True, "sqlite_transient")
        return _Disposition(False, "sqlite_permanent")
    if isinstance(getattr(exc, "__cause__", None), sqlite3.Error):
        cause = exc.__cause__
        if _sqlite_is_transient(cause):
            return _Disposition(True, "sqlite_transient")
        return _Disposition(False, "sqlite_permanent")
    if isinstance(exc, LegacyProjectionPermissionError):
        return _Disposition(False, "source_permission_denied")
    if isinstance(exc, PermissionError):
        return _Disposition(False, "projection_permission_denied")
    if isinstance(exc, UnsafeLegacyFileError):
        return _Disposition(False, "unsafe_source_path")
    if isinstance(exc, LegacyProjectionAmbiguousError):
        return _Disposition(False, "ambiguous_source")
    if isinstance(exc, WorkspaceOperationConflict):
        return _Disposition(False, "projection_operation_conflict")
    if isinstance(exc, FileNotFoundError):
        return _Disposition(False, "source_unavailable")
    if isinstance(exc, (WorkspaceProjectionError, WorkspaceInventoryError, ValueError)):
        return _Disposition(False, "projection_input_invalid")
    return _Disposition(False, "projection_failed")


def _empty_report() -> dict[str, Any]:
    return {
        "claimed": 0,
        "projected": 0,
        "delivered": 0,
        "retried": 0,
        "dead_lettered": 0,
        "ignored": 0,
        "error_codes": {},
    }


def _record_error(report: dict[str, Any], code: str) -> None:
    counts = report["error_codes"]
    counts[code] = int(counts.get(code, 0)) + 1


def _record_retry_transition(report: dict[str, Any], result: Mapping[str, Any]) -> None:
    status = str(result.get("status") or "")
    if status in {"dead", "dead_letter", "dead_lettered"}:
        report["dead_lettered"] += 1
    else:
        report["retried"] += 1


def process_projection_outbox_batch(
    store: ProjectionOutboxStore,
    *,
    consumer: str = PROJECTION_CONSUMER_VERSION,
    worker: str,
    user_data_root: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    source_db_for_owner: SourcePathResolver,
    source_quiesced_confirmed: bool,
    limit: int = DEFAULT_BATCH_LIMIT,
    now: int | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    projector: Projector = _default_projector,
) -> dict[str, Any]:
    """Claim and process one bounded, explicitly quiesced projection batch.

    The return value is deliberately identifier-free.  Exceptions from lease
    state transitions propagate.  After an effect/ack crash, a later lease
    uses a new deterministic attempt operation ID and repeats the exact-owner
    replacement; an unchanged projection is a semantic no-op.  This is bounded
    at-least-once delivery with state-idempotent convergence, not exactly-once.
    """
    if source_quiesced_confirmed is not True:
        raise ValueError("source_quiesced_confirmed must be explicitly true")
    if not callable(source_db_for_owner):
        raise ValueError("source_db_for_owner required")
    if not callable(projector):
        raise ValueError("projector required")
    stable_consumer = _worker_name(consumer, field="consumer")
    stable_worker = _worker_name(worker, field="worker")
    batch_limit = _bounded_integer(
        limit, field="limit", minimum=1, maximum=MAX_BATCH_LIMIT
    )
    lease = _bounded_integer(
        lease_seconds,
        field="lease_seconds",
        minimum=1,
        maximum=MAX_LEASE_SECONDS,
    )
    attempts = _bounded_integer(
        max_attempts,
        field="max_attempts",
        minimum=1,
        maximum=MAX_MAX_ATTEMPTS,
    )
    if now is not None and (isinstance(now, bool) or not isinstance(now, int) or now < 0):
        raise ValueError("now must be a non-negative integer")

    claimed = store.claim_pending_outbox(
        stable_consumer,
        stable_worker,
        limit=batch_limit,
        now=now,
        lease_seconds=lease,
        max_attempts=attempts,
    )
    if not isinstance(claimed, list) or len(claimed) > batch_limit:
        raise RuntimeError("outbox store violated bounded claim contract")
    report = _empty_report()
    report["claimed"] = len(claimed)

    for delivery in claimed:
        if not isinstance(delivery, Mapping):
            raise RuntimeError("outbox store returned invalid delivery")
        lease_token = delivery.get("lease_token")
        if not isinstance(lease_token, str) or not lease_token:
            raise RuntimeError("outbox store returned invalid lease")
        try:
            event = _decode_projection_event(
                delivery,
                expected_consumer=stable_consumer,
            )
        except _InvalidDelivery as exc:
            store.dead_letter_outbox_delivery(
                stable_consumer,
                stable_worker,
                lease_token,
                exc.error_code,
                now=now,
            )
            report["dead_lettered"] += 1
            if exc.error_code == "event_not_allowlisted":
                report["ignored"] += 1
            _record_error(report, exc.error_code)
            continue

        try:
            source_db_path = source_db_for_owner(event.source_owner_subject_id)
            projector(
                user_data_root=user_data_root,
                source_owner_id=event.source_owner_subject_id,
                actor_subject_id=event.actor_subject_id,
                workspace_id=event.workspace_id,
                source_page_id=event.source_page_id,
                expected_source_db_path=source_db_path,
                expected_catalog_db_path=expected_catalog_db_path,
                expected_binding_version=event.binding_version,
                operation_id=projection_effect_operation_id(
                    consumer=stable_consumer,
                    outbox_id=event.outbox_id,
                    attempt_count=event.attempt_count,
                ),
                # This is forwarding the caller's explicit attestation, not an
                # inferred or runtime-generated quiescence claim.
                source_quiesced_confirmed=source_quiesced_confirmed,
            )
        except Exception as exc:
            disposition = _classify_projection_error(exc)
            _record_error(report, disposition.error_code)
            if disposition.retry:
                result = store.retry_outbox_delivery(
                    stable_consumer,
                    stable_worker,
                    event.lease_token,
                    disposition.error_code,
                    now=now,
                    backoff_seconds=retry_backoff_seconds(event.attempt_count),
                    max_attempts=attempts,
                )
                _record_retry_transition(report, result)
            else:
                store.dead_letter_outbox_delivery(
                    stable_consumer,
                    stable_worker,
                    event.lease_token,
                    disposition.error_code,
                    now=now,
                )
                report["dead_lettered"] += 1
            continue

        report["projected"] += 1
        # Deliberately outside the effect try/except.  If this transition is
        # interrupted, lease expiry causes a new attempt operation ID to repeat
        # the projection and converge through the store's semantic no-op.
        store.ack_outbox_delivery(
            stable_consumer,
            stable_worker,
            event.lease_token,
            now=now,
        )
        report["delivered"] += 1

    report["error_codes"] = dict(sorted(report["error_codes"].items()))
    return report


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "MAX_BATCH_LIMIT",
    "PAGE_SOURCE_ACTIVATED_EVENT",
    "PAGE_SOURCE_AGGREGATE",
    "PROJECTION_CONSUMER_VERSION",
    "ProjectionOutboxStore",
    "process_projection_outbox_batch",
    "projection_effect_operation_id",
    "retry_backoff_seconds",
]

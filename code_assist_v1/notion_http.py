"""Stable HTTP error and JSON-request boundary for Notion-owned routes.

The legacy APIs historically used the ``error`` field both as a machine code
and as a human-readable message.  The compatibility envelope therefore keeps
that field intact per error class while adding a stable ``code`` and explicit
``message``.  New clients should branch on ``code``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import current_app, jsonify, request
from werkzeug.exceptions import MethodNotAllowed, NotFound, RequestEntityTooLarge

from code_assist_v1 import notion_publication
from code_assist_v1 import notion_store


class NotionRequestBodyError(ValueError):
    """Raised when a non-empty request body is not one JSON object."""


class NotionRequestValidationError(ValueError):
    """Raised when a JSON or form field violates its public contract."""


@dataclass(frozen=True)
class NotionErrorEnvelope:
    status: int
    payload: dict[str, Any]
    log_unhandled: bool = False


def json_object_request() -> dict[str, Any]:
    """Return one JSON object while preserving legacy empty-body requests."""
    raw = request.get_data(cache=True)
    if not raw.strip():
        return {}
    value = request.get_json(force=True, silent=True)
    if not isinstance(value, dict):
        raise NotionRequestBodyError("request body must be a valid JSON object")
    return value


def _message(exc: BaseException, fallback: str) -> str:
    value = str(exc).strip()
    return value or fallback


def classify_error(exc: BaseException) -> NotionErrorEnvelope:
    """Classify an exception without requiring an active Flask context."""
    if isinstance(exc, notion_publication.PageSourceSnapshotConflict):
        code = "source_snapshot_conflict"
        return NotionErrorEnvelope(409, {
            "error": code,
            "code": code,
            "message": _message(exc, "page changed after publication preview"),
            "expected_source_snapshot_sha256": exc.expected_sha256,
            "current_source_snapshot": exc.snapshot.descriptor(),
        })
    if isinstance(exc, notion_store.AtomicSaveUnavailable):
        code = "atomic_save_unavailable"
        return NotionErrorEnvelope(503, {
            "error": code,
            "code": code,
            "message": _message(exc, "atomic save is unavailable"),
        })
    if isinstance(exc, notion_store.PageRevisionConflict):
        code = "revision_conflict"
        return NotionErrorEnvelope(409, {
            "error": code,
            "code": code,
            "message": _message(exc, "page revision conflict"),
            "current_revision": exc.current_revision,
        })
    if isinstance(exc, notion_store.OperationIdConflict):
        code = "operation_id_conflict"
        return NotionErrorEnvelope(409, {
            "error": code,
            "code": code,
            "message": _message(exc, "operation id conflict"),
            "operation_id": exc.operation_id,
            "current_revision": exc.current_revision,
        })
    if isinstance(exc, NotionRequestBodyError):
        code = "invalid_json_body"
        message = _message(exc, "request body must be a valid JSON object")
        return NotionErrorEnvelope(400, {
            "error": message,
            "code": code,
            "message": message,
        })
    if isinstance(exc, NotionRequestValidationError):
        code = "invalid_request"
        message = _message(exc, "invalid request")
        return NotionErrorEnvelope(400, {
            "error": message,
            "code": code,
            "message": message,
        })
    if isinstance(exc, FileNotFoundError) and not getattr(exc, "filename", None):
        code = "not_found"
        message = _message(exc, "not found")
        return NotionErrorEnvelope(404, {
            "error": message,
            "code": code,
            "message": message,
        })
    if isinstance(exc, PermissionError) and not getattr(exc, "filename", None):
        code = "permission_denied"
        message = _message(exc, "permission denied")
        return NotionErrorEnvelope(403, {
            "error": message,
            "code": code,
            "message": message,
        })
    if isinstance(exc, ValueError):
        code = "invalid_request"
        message = _message(exc, "invalid request")
        return NotionErrorEnvelope(400, {
            "error": message,
            "code": code,
            "message": message,
        })
    code = "internal_error"
    return NotionErrorEnvelope(
        500,
        {
            "error": code,
            "code": code,
            "message": "The Notion request could not be completed.",
        },
        log_unhandled=True,
    )


def error_response(exc: BaseException):
    envelope = classify_error(exc)
    if envelope.log_unhandled:
        current_app.logger.error(
            "Unhandled Notion route error",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    response = jsonify(envelope.payload)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response, envelope.status


def validation_response(
    message: str,
    *,
    code: str = "invalid_request",
    status: int = 400,
):
    """Return the common envelope for route-local validation failures."""
    text = str(message or "invalid request")
    response = jsonify({"error": text, "code": code, "message": text})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response, int(status)


def public_fallback_errors(meta: dict[str, Any] | None) -> list[str]:
    """Expose fallback state without reflecting provider exceptions or paths."""
    values = (meta or {}).get("fallback_errors") or []
    if not isinstance(values, list):
        values = [values]
    if not values and (meta or {}).get("error"):
        values = [(meta or {}).get("error")]
    if not values:
        return []
    if any("temporarily unavailable" in str(value).lower() for value in values):
        return ["AI provider temporarily unavailable"]
    return ["AI provider unavailable"]


def list_field(
    data: dict[str, Any],
    name: str,
    *,
    required: bool = False,
) -> list[Any]:
    if name not in data:
        if required:
            raise NotionRequestValidationError(f"{name} required")
        return []
    value = data[name]
    if not isinstance(value, list):
        raise NotionRequestValidationError(f"{name} must be a list")
    return value


def object_field(
    data: dict[str, Any],
    name: str,
    *,
    required: bool = False,
) -> dict[str, Any]:
    if name not in data:
        if required:
            raise NotionRequestValidationError(f"{name} required")
        return {}
    value = data[name]
    if not isinstance(value, dict):
        raise NotionRequestValidationError(f"{name} must be an object")
    return value


def boolean_field(
    data: dict[str, Any],
    name: str,
    *,
    default: bool = False,
) -> bool:
    if name not in data:
        return bool(default)
    value = data[name]
    if not isinstance(value, bool):
        raise NotionRequestValidationError(f"{name} must be a boolean")
    return value


def enum_field(
    data: dict[str, Any],
    name: str,
    allowed: set[str],
    *,
    default: str,
) -> str:
    if name not in data or data[name] in (None, ""):
        return default
    value = data[name]
    if not isinstance(value, str) or value.strip().lower() not in allowed:
        choices = ", ".join(sorted(allowed))
        raise NotionRequestValidationError(f"{name} must be one of: {choices}")
    return value.strip().lower()


def register_error_handlers(app: Any) -> None:
    """Install the out-of-handler JSON parser failure mapping once per app."""
    marker = "notion_http_error_handler_v1"
    if app.extensions.get(marker):
        return
    app.register_error_handler(NotionRequestBodyError, error_response)
    app.register_error_handler(NotionRequestValidationError, error_response)

    def notion_http_exception(exc: BaseException):
        path = request.path or ""
        if not (
            path == "/api/notion"
            or path.startswith("/api/notion/")
            or path == "/api/code/notion"
            or path.startswith("/api/code/notion/")
        ):
            return exc.get_response()
        if isinstance(exc, NotFound):
            return validation_response("Notion route not found", code="route_not_found", status=404)
        if isinstance(exc, MethodNotAllowed):
            return validation_response("Method not allowed", code="method_not_allowed", status=405)
        if isinstance(exc, RequestEntityTooLarge):
            return validation_response("Request payload is too large", code="payload_too_large", status=413)
        return exc.get_response()

    app.register_error_handler(NotFound, notion_http_exception)
    app.register_error_handler(MethodNotAllowed, notion_http_exception)
    app.register_error_handler(RequestEntityTooLarge, notion_http_exception)
    app.extensions[marker] = True

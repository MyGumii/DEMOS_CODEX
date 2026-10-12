"""Strict additive v2 HTTP adapters for semantic-edge correction."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
import re
from typing import Any
from uuid import uuid4

from flask import Blueprint, Flask, jsonify, request

from .errors import KnowledgeCoreError
from .scope import SessionPrincipal
from .services.semantic_edge_correction import (
    LiftSemanticEdgeForEdgeCommand,
    SuppressSemanticEdgeCommand,
)


SEMANTIC_EDGE_ROUTE_PREFIX = "/api/knowledge/v2/spaces"
MAX_SEMANTIC_EDGE_JSON_BYTES = 32 * 1024
_REGISTERED_ATTRIBUTE = "_code_assist_semantic_edge_v2_registered"
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_VARIABLE_RULE_RE = re.compile(r"<(?:(?:[^:<>]+):)?[^<>]+>")


class _RequestError(RuntimeError):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = status


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise _RequestError("VALIDATION_ERROR", f"{field} is invalid.", 400)
    return value


def _text(value: object, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > maximum
        or "\x00" in value
    ):
        raise _RequestError("VALIDATION_ERROR", f"{field} is invalid.", 400)
    return value


def _principal(resolver: Callable[[], object]) -> SessionPrincipal:
    value = resolver()
    if value is None:
        raise _RequestError(
            "AUTHENTICATION_REQUIRED", "A valid account session is required.", 401
        )
    if isinstance(value, Mapping):
        user_id = value.get("id")
        epoch = value.get("scope_epoch")
    else:
        user_id = getattr(value, "id", None)
        epoch = getattr(value, "scope_epoch", None)
    if (
        not isinstance(user_id, str)
        or _USER_RE.fullmatch(user_id) is None
        or isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or epoch < 1
    ):
        raise _RequestError(
            "AUTHENTICATION_REQUIRED", "The account session is invalid.", 401
        )
    return SessionPrincipal(user_id, epoch)


def _read_json(
    *, required: frozenset[str], optional: frozenset[str] = frozenset()
) -> dict[str, object]:
    if request.mimetype != "application/json":
        raise _RequestError("VALIDATION_ERROR", "application/json is required.", 400)
    if (
        request.content_length is not None
        and request.content_length > MAX_SEMANTIC_EDGE_JSON_BYTES
    ):
        raise _RequestError("VALIDATION_ERROR", "The JSON request is too large.", 400)
    raw = request.get_data(cache=False, as_text=False)
    if len(raw) > MAX_SEMANTIC_EDGE_JSON_BYTES or raw.startswith(b"\xef\xbb\xbf"):
        raise _RequestError("VALIDATION_ERROR", "The JSON request is invalid.", 400)

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    try:
        body = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=object_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise _RequestError("VALIDATION_ERROR", "The JSON request is invalid.", 400) from exc
    keys = frozenset(body) if isinstance(body, dict) else frozenset()
    if not isinstance(body, dict) or not required <= keys or keys - required - optional:
        raise _RequestError("VALIDATION_ERROR", "The JSON request shape is invalid.", 400)
    nodes = 0
    stack: list[tuple[object, int]] = [(body, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > 64 or depth > 3:
            raise _RequestError("VALIDATION_ERROR", "The JSON request is too complex.", 400)
        if isinstance(current, float) and not math.isfinite(current):
            raise _RequestError("VALIDATION_ERROR", "The JSON request is invalid.", 400)
        if isinstance(current, dict):
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
    return body


def _operation_id(body: Mapping[str, object]) -> str:
    operation_id = _uuid(body.get("operation_id"), "operation_id")
    aliases = request.headers.getlist("Idempotency-Key")
    if aliases and (len(aliases) != 1 or aliases[0] != operation_id):
        raise _RequestError(
            "IDEMPOTENCY_CONFLICT", "Idempotency-Key must match operation_id.", 409
        )
    return operation_id


def _expected_row_version(body: Mapping[str, object]) -> int | None:
    value = body.get("expected_row_version")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _RequestError("VALIDATION_ERROR", "expected_row_version is invalid.", 400)
    return value


def _request_meta() -> dict[str, object]:
    return {
        "host": request.host,
        "method": request.method,
        "path": request.path,
        "remote_addr": request.remote_addr,
        "user_agent": request.user_agent.string,
    }


def _response(payload: Mapping[str, object], request_id: str, status: int = 200):
    result = jsonify({"request_id": request_id, **dict(payload)})
    result.headers["Cache-Control"] = "no-store, max-age=0"
    result.headers["Pragma"] = "no-cache"
    result.headers["X-Request-ID"] = request_id
    return result, status


def _error(exc: Exception, request_id: str):
    if isinstance(exc, _RequestError):
        code, message, retryable, status = exc.code, str(exc), False, exc.http_status
    elif isinstance(exc, KnowledgeCoreError):
        code = getattr(exc, "code", "STATE_CONFLICT")
        message = str(exc)
        retryable = bool(getattr(exc, "retryable", False))
        status = int(getattr(exc, "http_status", 409))
    elif isinstance(exc, (TypeError, ValueError)):
        code, message, retryable, status = "VALIDATION_ERROR", "The request is invalid.", False, 400
    else:
        code, message, retryable, status = (
            "INTERNAL_ERROR",
            "The semantic-edge request could not be completed.",
            True,
            500,
        )
    return _response(
        {"error": {"code": code, "details": {}, "message": message, "retryable": retryable}},
        request_id,
        status,
    )


class _Handlers:
    __slots__ = ("principal_resolver", "service")

    def __init__(self, service: object, principal_resolver: Callable[[], object]) -> None:
        self.service = service
        self.principal_resolver = principal_resolver

    def run(self, action: Callable[[str], Any]):
        request_id = str(uuid4())
        try:
            return action(request_id)
        except Exception as exc:
            return _error(exc, request_id)

    def suppress(self, space_id: str, edge_id: str):
        def execute(request_id: str):
            body = _read_json(
                required=frozenset({"operation_id", "reason"}),
                optional=frozenset(
                    {"break_glass", "confirmation", "expected_row_version"}
                ),
            )
            break_glass = body.get("break_glass", False)
            if not isinstance(break_glass, bool):
                raise _RequestError("VALIDATION_ERROR", "break_glass is invalid.", 400)
            confirmation = body.get("confirmation")
            if confirmation is not None:
                confirmation = _text(confirmation, "confirmation", 256)
            result = self.service.suppress(
                SuppressSemanticEdgeCommand(
                    principal=_principal(self.principal_resolver),
                    selected_space_id=_uuid(space_id, "space_id"),
                    edge_id=_uuid(edge_id, "edge_id"),
                    edge_kind="auto",
                    reason=_text(body.get("reason"), "reason", 1000),
                    operation_id=_operation_id(body),
                    request_id=request_id,
                    break_glass=break_glass,
                    confirmation=confirmation,
                    expected_row_version=_expected_row_version(body),
                    request_meta=_request_meta(),
                )
            )
            return _response(result.as_dict(), request_id)

        return self.run(execute)

    def lift(self, space_id: str, edge_id: str):
        def execute(request_id: str):
            body = _read_json(
                required=frozenset({"operation_id", "reason"}),
                optional=frozenset({"expected_row_version"}),
            )
            result = self.service.lift_edge(
                LiftSemanticEdgeForEdgeCommand(
                    principal=_principal(self.principal_resolver),
                    selected_space_id=_uuid(space_id, "space_id"),
                    edge_id=_uuid(edge_id, "edge_id"),
                    reason=_text(body.get("reason"), "reason", 1000),
                    operation_id=_operation_id(body),
                    request_id=request_id,
                    expected_row_version=_expected_row_version(body),
                    request_meta=_request_meta(),
                )
            )
            return _response(result.as_dict(), request_id, 202)

        return self.run(execute)


def _specs(handlers: _Handlers) -> tuple[tuple[Any, ...], ...]:
    base = f"{SEMANTIC_EDGE_ROUTE_PREFIX}/<space_id>/edges/<edge_id>"
    return (
        (f"{base}/suppress", "semanticEdgeSuppress", ("POST",), handlers.suppress),
        (
            f"{base}/suppression/lift",
            "semanticEdgeSuppressionLift",
            ("POST",),
            handlers.lift,
        ),
    )


def _effective_methods(methods: tuple[str, ...] | set[str]) -> frozenset[str]:
    result = {str(method).upper() for method in methods}
    if "GET" in result:
        result.add("HEAD")
    result.add("OPTIONS")
    return frozenset(result)


def _preflight(target: Flask | Blueprint, specs: tuple[tuple[Any, ...], ...]) -> None:
    if isinstance(target, Flask):
        if bool(getattr(target, "_got_first_request", False)):
            raise RuntimeError("semantic-edge routes must register before the first request")
        endpoints = {rule.endpoint for rule in target.url_map.iter_rules()}
        rules = {
            (_VARIABLE_RULE_RE.sub("<>", rule.rule), method)
            for rule in target.url_map.iter_rules()
            for method in _effective_methods(set(rule.methods))
        }
        for rule, endpoint, methods, _handler in specs:
            normalized = _VARIABLE_RULE_RE.sub("<>", rule)
            if endpoint in endpoints or endpoint in target.view_functions:
                raise RuntimeError(f"semantic-edge endpoint already exists: {endpoint}")
            if any((normalized, method) in rules for method in _effective_methods(methods)):
                raise RuntimeError(f"semantic-edge route already exists: {rule}")
        return
    if bool(getattr(target, "_got_registered_once", False)):
        raise RuntimeError("semantic-edge routes must register before Blueprint attach")

    def verify(state: object) -> None:
        app = getattr(state, "app", None)
        if getattr(state, "url_prefix", None) or getattr(state, "subdomain", None):
            raise RuntimeError("semantic-edge Blueprint routes require the root host")
        existing = {
            (_VARIABLE_RULE_RE.sub("<>", rule.rule), method)
            for rule in app.url_map.iter_rules()
            for method in _effective_methods(set(rule.methods))
        }
        for rule, _endpoint, methods, _handler in specs:
            normalized = _VARIABLE_RULE_RE.sub("<>", rule)
            if any((normalized, method) in existing for method in _effective_methods(methods)):
                raise RuntimeError(f"semantic-edge route already exists: {rule}")

    target.record(verify)


def register_semantic_edge_routes(
    target: Flask | Blueprint,
    *,
    service: object,
    principal_resolver: Callable[[], object],
) -> tuple[str, ...]:
    if not isinstance(target, (Flask, Blueprint)):
        raise TypeError("semantic-edge route target must be a Flask app or Blueprint")
    if getattr(target, _REGISTERED_ATTRIBUTE, False):
        raise RuntimeError("semantic-edge v2 routes are already registered")
    if not callable(principal_resolver):
        raise TypeError("principal_resolver must be callable")
    for method in ("suppress", "lift_edge"):
        if not callable(getattr(service, method, None)):
            raise TypeError(f"semantic-edge service is missing {method}().")
    handlers = _Handlers(service, principal_resolver)
    specs = _specs(handlers)
    _preflight(target, specs)
    for rule, endpoint, methods, handler in specs:
        target.add_url_rule(rule, endpoint=endpoint, view_func=handler, methods=list(methods))
    setattr(target, _REGISTERED_ATTRIBUTE, True)
    return tuple(rule for rule, _endpoint, _methods, _handler in specs)


__all__ = [
    "MAX_SEMANTIC_EDGE_JSON_BYTES",
    "SEMANTIC_EDGE_ROUTE_PREFIX",
    "register_semantic_edge_routes",
]

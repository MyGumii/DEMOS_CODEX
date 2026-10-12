"""Strict additive v2 HTTP adapters for temporal provenance."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
import re
from typing import Any
from uuid import uuid4

from flask import Blueprint, Flask, jsonify, request

from .domain import TemporalAssertionKind, TemporalAssertionOrigin
from .errors import KnowledgeCoreError
from .scope import SessionPrincipal
from .services.temporal import (
    TemporalAssertionCommand,
    TemporalAssertionRevokeCommand,
    TemporalReadRequest,
    VerificationAttestCommand,
    VerificationRevokeCommand,
)


TEMPORAL_ROUTE_PREFIX = "/api/knowledge/v2/spaces"
MAX_TEMPORAL_JSON_BYTES = 32 * 1024
_REGISTERED_ATTRIBUTE = "_code_assist_temporal_v2_registered"
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
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


def _nullable_uuid(value: object, field: str) -> str | None:
    return None if value is None else _uuid(value, field)


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
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


def _nullable_text(value: object, field: str, maximum: int) -> str | None:
    return None if value is None else _text(value, field, maximum)


def _nonnegative(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
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
        and request.content_length > MAX_TEMPORAL_JSON_BYTES
    ):
        raise _RequestError("VALIDATION_ERROR", "The JSON request is too large.", 400)
    raw = request.get_data(cache=False, as_text=False)
    if len(raw) > MAX_TEMPORAL_JSON_BYTES or raw.startswith(b"\xef\xbb\xbf"):
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
    stack: list[tuple[object, int]] = [(body, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > 128 or depth > 4:
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
        code, message, retryable, status = (
            "VALIDATION_ERROR",
            "The request is invalid.",
            False,
            400,
        )
    else:
        code, message, retryable, status = (
            "INTERNAL_ERROR",
            "The temporal request could not be completed.",
            True,
            500,
        )
    return _response(
        {
            "error": {
                "code": code,
                "details": {},
                "message": message,
                "retryable": retryable,
            }
        },
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

    def read(self, space_id: str, item_id: str):
        def execute(request_id: str):
            if set(request.args) - {"after_audit_id", "limit"}:
                raise _RequestError("VALIDATION_ERROR", "The query string is invalid.", 400)
            raw_after = request.args.get("after_audit_id", "0")
            raw_limit = request.args.get("limit", "100")
            if (
                not raw_after.isascii()
                or not raw_after.isdecimal()
                or not raw_limit.isascii()
                or not raw_limit.isdecimal()
            ):
                raise _RequestError("VALIDATION_ERROR", "The query string is invalid.", 400)
            after = int(raw_after)
            limit = int(raw_limit)
            if not 1 <= limit <= 100:
                raise _RequestError("VALIDATION_ERROR", "limit is invalid.", 400)
            result = self.service.read(
                TemporalReadRequest(
                    principal=_principal(self.principal_resolver),
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    after_audit_id=after,
                    limit=limit,
                )
            )
            return _response(result, request_id)

        return self.run(execute)

    def create_assertion(self, space_id: str, item_id: str):
        def execute(request_id: str):
            body = _read_json(
                required=frozenset(
                    {
                        "assertion_kind",
                        "evidence_reference",
                        "evidence_sha256",
                        "expected_head_event_id",
                        "expected_head_generation",
                        "expected_revision_id",
                        "operation_id",
                        "origin",
                        "value",
                    }
                )
            )
            try:
                assertion_kind = TemporalAssertionKind(body["assertion_kind"])
                origin = TemporalAssertionOrigin(body["origin"])
            except (TypeError, ValueError) as exc:
                raise _RequestError(
                    "TEMPORAL_VALUE_INVALID", "The temporal assertion type is invalid.", 422
                ) from exc
            value = body["value"]
            if not isinstance(value, Mapping):
                raise _RequestError(
                    "TEMPORAL_VALUE_INVALID", "The tagged temporal value is invalid.", 422
                )
            result = self.service.assert_temporal(
                TemporalAssertionCommand(
                    principal=_principal(self.principal_resolver),
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    expected_revision_id=_uuid(
                        body["expected_revision_id"], "expected_revision_id"
                    ),
                    assertion_kind=assertion_kind,
                    value=value,
                    expected_head_event_id=_nullable_uuid(
                        body["expected_head_event_id"], "expected_head_event_id"
                    ),
                    expected_head_generation=_nonnegative(
                        body["expected_head_generation"], "expected_head_generation"
                    ),
                    origin=origin,
                    evidence_sha256=_sha(body["evidence_sha256"], "evidence_sha256"),
                    evidence_reference=_text(
                        body["evidence_reference"], "evidence_reference", 1000
                    ),
                    operation_id=_operation_id(body),
                    request_id=request_id,
                    request_meta=_request_meta(),
                )
            )
            return _response(result, request_id, 201)

        return self.run(execute)

    def revoke_assertion(self, space_id: str, item_id: str, assertion_id: str):
        def execute(request_id: str):
            body = _read_json(
                required=frozenset(
                    {
                        "expected_head_generation",
                        "expected_revision_id",
                        "operation_id",
                        "reason",
                    }
                )
            )
            result = self.service.revoke_assertion(
                TemporalAssertionRevokeCommand(
                    principal=_principal(self.principal_resolver),
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    expected_revision_id=_uuid(
                        body["expected_revision_id"], "expected_revision_id"
                    ),
                    assertion_id=_uuid(assertion_id, "assertion_id"),
                    expected_head_generation=_nonnegative(
                        body["expected_head_generation"], "expected_head_generation"
                    ),
                    reason=_text(body["reason"], "reason", 2000),
                    operation_id=_operation_id(body),
                    request_id=request_id,
                    request_meta=_request_meta(),
                )
            )
            return _response(result, request_id)

        return self.run(execute)

    def create_attestation(self, space_id: str, item_id: str):
        def execute(request_id: str):
            body = _read_json(
                required=frozenset(
                    {
                        "evidence_reference",
                        "evidence_sha256",
                        "expected_head_event_id",
                        "expected_head_generation",
                        "expected_revision_id",
                        "expected_temporal_summary_sha256",
                        "method",
                        "note",
                        "operation_id",
                        "subject_verified_at",
                    }
                )
            )
            result = self.service.attest(
                VerificationAttestCommand(
                    principal=_principal(self.principal_resolver),
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    expected_revision_id=_uuid(
                        body["expected_revision_id"], "expected_revision_id"
                    ),
                    expected_temporal_summary_sha256=_sha(
                        body["expected_temporal_summary_sha256"],
                        "expected_temporal_summary_sha256",
                    ),
                    expected_head_event_id=_nullable_uuid(
                        body["expected_head_event_id"], "expected_head_event_id"
                    ),
                    expected_head_generation=_nonnegative(
                        body["expected_head_generation"], "expected_head_generation"
                    ),
                    method=_text(body["method"], "method", 120),
                    evidence_sha256=_sha(body["evidence_sha256"], "evidence_sha256"),
                    evidence_reference=_text(
                        body["evidence_reference"], "evidence_reference", 1000
                    ),
                    note=_nullable_text(body["note"], "note", 2000),
                    subject_verified_at=_nullable_text(
                        body["subject_verified_at"], "subject_verified_at", 64
                    ),
                    operation_id=_operation_id(body),
                    request_id=request_id,
                    request_meta=_request_meta(),
                )
            )
            return _response(result, request_id, 201)

        return self.run(execute)

    def revoke_attestation(
        self, space_id: str, item_id: str, attestation_id: str
    ):
        def execute(request_id: str):
            body = _read_json(
                required=frozenset(
                    {
                        "expected_head_generation",
                        "expected_revision_id",
                        "operation_id",
                        "reason",
                    }
                )
            )
            result = self.service.revoke_verification(
                VerificationRevokeCommand(
                    principal=_principal(self.principal_resolver),
                    space_id=_uuid(space_id, "space_id"),
                    item_id=_uuid(item_id, "item_id"),
                    expected_revision_id=_uuid(
                        body["expected_revision_id"], "expected_revision_id"
                    ),
                    attestation_id=_uuid(attestation_id, "attestation_id"),
                    expected_head_generation=_nonnegative(
                        body["expected_head_generation"], "expected_head_generation"
                    ),
                    reason=_text(body["reason"], "reason", 2000),
                    operation_id=_operation_id(body),
                    request_id=request_id,
                    request_meta=_request_meta(),
                )
            )
            return _response(result, request_id)

        return self.run(execute)


def _specs(handlers: _Handlers) -> tuple[tuple[Any, ...], ...]:
    base = f"{TEMPORAL_ROUTE_PREFIX}/<space_id>/items/<item_id>"
    return (
        (f"{base}/temporal", "itemTemporalGet", ("GET",), handlers.read),
        (
            f"{base}/temporal-assertions",
            "temporalAssertionCreate",
            ("POST",),
            handlers.create_assertion,
        ),
        (
            f"{base}/temporal-assertions/<assertion_id>/revoke",
            "temporalAssertionRevoke",
            ("POST",),
            handlers.revoke_assertion,
        ),
        (
            f"{base}/verification-attestations",
            "verificationAttestationCreate",
            ("POST",),
            handlers.create_attestation,
        ),
        (
            f"{base}/verification-attestations/<attestation_id>/revoke",
            "verificationAttestationRevoke",
            ("POST",),
            handlers.revoke_attestation,
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
            raise RuntimeError("temporal routes must register before the first request")
        existing_endpoints = {rule.endpoint for rule in target.url_map.iter_rules()}
        existing_rules = {
            (_VARIABLE_RULE_RE.sub("<>", rule.rule), method)
            for rule in target.url_map.iter_rules()
            for method in _effective_methods(set(rule.methods))
        }
        for rule, endpoint, methods, _ in specs:
            normalized = _VARIABLE_RULE_RE.sub("<>", rule)
            if endpoint in existing_endpoints or endpoint in target.view_functions:
                raise RuntimeError(f"temporal endpoint already exists: {endpoint}")
            if any(
                (normalized, method) in existing_rules
                for method in _effective_methods(methods)
            ):
                raise RuntimeError(f"temporal route already exists: {rule}")
        return
    if bool(getattr(target, "_got_registered_once", False)):
        raise RuntimeError("temporal routes must register before Blueprint attach")

    def verify(state: object) -> None:
        app = getattr(state, "app", None)
        if getattr(state, "url_prefix", None) or getattr(state, "subdomain", None):
            raise RuntimeError("temporal Blueprint routes require the root host")
        existing_rules = {
            (_VARIABLE_RULE_RE.sub("<>", rule.rule), method)
            for rule in app.url_map.iter_rules()
            for method in _effective_methods(set(rule.methods))
        }
        for rule, _, methods, _ in specs:
            normalized = _VARIABLE_RULE_RE.sub("<>", rule)
            if any(
                (normalized, method) in existing_rules
                for method in _effective_methods(methods)
            ):
                raise RuntimeError(f"temporal Blueprint route already exists: {rule}")

    target.record(verify)


def register_temporal_routes(
    target: Flask | Blueprint,
    *,
    service: object,
    principal_resolver: Callable[[], object],
) -> tuple[str, ...]:
    if not isinstance(target, (Flask, Blueprint)):
        raise TypeError("temporal route target must be a Flask app or Blueprint")
    if getattr(target, _REGISTERED_ATTRIBUTE, False):
        raise RuntimeError("temporal v2 routes are already registered")
    if not callable(principal_resolver):
        raise TypeError("principal_resolver must be callable")
    for method in (
        "assert_temporal",
        "attest",
        "read",
        "revoke_assertion",
        "revoke_verification",
    ):
        if not callable(getattr(service, method, None)):
            raise TypeError(f"temporal service is missing {method}().")
    handlers = _Handlers(service, principal_resolver)
    specs = _specs(handlers)
    _preflight(target, specs)
    for rule, endpoint, methods, handler in specs:
        target.add_url_rule(
            rule, endpoint=endpoint, view_func=handler, methods=list(methods)
        )
    setattr(target, _REGISTERED_ATTRIBUTE, True)
    return tuple(rule for rule, _, _, _ in specs)


__all__ = [
    "MAX_TEMPORAL_JSON_BYTES",
    "TEMPORAL_ROUTE_PREFIX",
    "register_temporal_routes",
]

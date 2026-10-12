"""Strict SBE-2.3 Flask adapters for higher roles and distributions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import json
import math
import re
from typing import Any
from uuid import uuid4

from flask import Blueprint, Flask, jsonify, request

from .errors import KnowledgeCoreError
from .services.higher_authority import (
    CreateDistributionCommand,
    HigherAuthorityServiceError,
    ListDistributionTargetsQuery,
    ListDistributionsQuery,
    ReadHigherRoleQuery,
    RevokeDistributionCommand,
    SetHigherRoleCommand,
)


HIGHER_ADMIN_ROUTE_PREFIX = "/api/admin/v2/org"
HIGHER_KNOWLEDGE_ROUTE_PREFIX = "/api/knowledge/v2/spaces"
MAX_HIGHER_AUTHORITY_JSON_BYTES = 32 * 1024
_REGISTERED_ATTRIBUTE = "_code_assist_higher_authority_v2_registered"
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


def _positive(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _RequestError("VALIDATION_ERROR", f"{field} is invalid.", 400)
    return value


def _nullable_positive(value: object, field: str) -> int | None:
    return None if value is None else _positive(value, field)


def _nullable_uuid(value: object, field: str) -> str | None:
    return None if value is None else _uuid(value, field)


def _user(value: object, field: str) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise _RequestError("VALIDATION_ERROR", f"{field} is invalid.", 400)
    return value


def _nullable_user(value: object, field: str) -> str | None:
    return None if value is None else _user(value, field)


def _text(value: object, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > maximum
    ):
        raise _RequestError("VALIDATION_ERROR", f"{field} is invalid.", 400)
    return value


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise _RequestError("VALIDATION_ERROR", f"{field} is invalid.", 400)
    return value


def _principal(resolver: Callable[[], object]) -> tuple[str, int]:
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
    try:
        return _user(user_id, "principal.id"), _positive(
            epoch, "principal.scope_epoch"
        )
    except _RequestError as exc:
        raise _RequestError(
            "AUTHENTICATION_REQUIRED", "The account session is invalid.", 401
        ) from exc


def _read_json(
    *, required: frozenset[str], optional: frozenset[str] = frozenset()
) -> dict[str, object]:
    if request.mimetype != "application/json":
        raise _RequestError("VALIDATION_ERROR", "application/json is required.", 400)
    if (
        request.content_length is not None
        and request.content_length > MAX_HIGHER_AUTHORITY_JSON_BYTES
    ):
        raise _RequestError("VALIDATION_ERROR", "The JSON request is too large.", 400)
    raw = request.get_data(cache=False, as_text=False)
    if len(raw) > MAX_HIGHER_AUTHORITY_JSON_BYTES or raw.startswith(b"\xef\xbb\xbf"):
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
            "IDEMPOTENCY_CONFLICT",
            "Idempotency-Key must match operation_id.",
            409,
        )
    return operation_id


def _single_query(name: str) -> str | None:
    values = request.args.getlist(name)
    if len(values) > 1:
        raise _RequestError("VALIDATION_ERROR", "The query string is invalid.", 400)
    return None if not values else values[0]


def _request_meta() -> dict[str, object]:
    return {
        "host": request.host,
        "method": request.method,
        "path": request.path,
        "remote_addr": request.remote_addr,
        "user_agent": request.user_agent.string,
    }


def _utc(milliseconds: int | None) -> str | None:
    if milliseconds is None:
        return None
    return (
        datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _json_response(payload: Mapping[str, object], request_id: str, status: int = 200):
    response = jsonify({"request_id": request_id, **dict(payload)})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Request-ID"] = request_id
    return response, status


def _error_response(exc: Exception, request_id: str):
    if isinstance(exc, _RequestError):
        error = {
            "code": exc.code,
            "details": {},
            "message": str(exc),
            "retryable": False,
        }
        status = exc.http_status
    elif isinstance(exc, HigherAuthorityServiceError):
        error = exc.as_error()
        status = exc.http_status
    elif isinstance(exc, (TypeError, ValueError)):
        error = {
            "code": "VALIDATION_ERROR",
            "details": {},
            "message": "The request is invalid.",
            "retryable": False,
        }
        status = 400
    elif isinstance(exc, KnowledgeCoreError):
        error = {
            "code": getattr(exc, "code", "STATE_CONFLICT"),
            "details": {},
            "message": str(exc),
            "retryable": bool(getattr(exc, "retryable", False)),
        }
        status = int(getattr(exc, "http_status", 409))
    else:
        error = {
            "code": "INTERNAL_ERROR",
            "details": {"state": "internal_error"},
            "message": "The higher-authority request could not be completed.",
            "retryable": True,
        }
        status = 500
    return _json_response({"error": error}, request_id, status)


def _distribution_payload(item: object) -> dict[str, object]:
    return {
        "created_at": _utc(getattr(item, "created_at_ms")),
        "created_by_user_id": getattr(item, "created_by_user_id"),
        "grant_id": getattr(item, "id"),
        "grant_mode": getattr(item, "grant_mode"),
        "reason": getattr(item, "reason"),
        "revoke_mode": getattr(item, "revoke_mode"),
        "revoke_reason": getattr(item, "revoke_reason"),
        "revoked_at": _utc(getattr(item, "revoked_at_ms")),
        "revoked_by_user_id": getattr(item, "revoked_by_user_id"),
        "row_version": getattr(item, "row_version"),
        "source_item_id": getattr(item, "source_item_id"),
        "source_revision_id": getattr(item, "source_revision_id"),
        "source_role_assignment_id": getattr(item, "source_role_assignment_id"),
        "source_space_id": getattr(item, "source_space_id"),
        "target_org_unit_id": getattr(item, "target_org_unit_id"),
        "target_scope_id": getattr(item, "target_scope_id"),
        "target_scope_kind": getattr(item, "target_scope_kind"),
        "updated_at": _utc(getattr(item, "updated_at_ms")),
    }


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
            return _error_response(exc, request_id)

    def set_role(self, org_unit_id: str, role: str):
        def execute(request_id: str):
            actor, epoch = _principal(self.principal_resolver)
            body = _read_json(
                required=frozenset(
                    {
                        "assignee_user_id",
                        "enable_writes",
                        "expected_current_assignment_generation",
                        "expected_current_assignment_id",
                        "expected_current_assignment_row_version",
                        "expected_space_row_version",
                        "operation_id",
                        "reason",
                    }
                )
            )
            result = self.service.set_role(
                SetHigherRoleCommand(
                    actor_user_id=actor,
                    actor_scope_epoch=epoch,
                    operation_id=_operation_id(body),
                    request_id=request_id,
                    org_unit_id=_uuid(org_unit_id, "org_unit_id"),
                    role=role,
                    assignee_user_id=_nullable_user(
                        body["assignee_user_id"], "assignee_user_id"
                    ),
                    expected_current_assignment_id=_nullable_uuid(
                        body["expected_current_assignment_id"],
                        "expected_current_assignment_id",
                    ),
                    expected_current_assignment_generation=_nullable_positive(
                        body["expected_current_assignment_generation"],
                        "expected_current_assignment_generation",
                    ),
                    expected_current_assignment_row_version=_nullable_positive(
                        body["expected_current_assignment_row_version"],
                        "expected_current_assignment_row_version",
                    ),
                    expected_space_row_version=_positive(
                        body["expected_space_row_version"],
                        "expected_space_row_version",
                    ),
                    enable_writes=_boolean(body["enable_writes"], "enable_writes"),
                    reason=_text(body["reason"], "reason", 1000),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), request_id)

        return self.run(execute)

    def team_leader(self, team_id: str):
        if request.method == "GET":
            return self.read_role(team_id, "team_leader")
        return self.set_role(team_id, "team_leader")

    def responsibility_executive(self, responsibility_id: str):
        if request.method == "GET":
            return self.read_role(
                responsibility_id, "responsibility_executive"
            )
        return self.set_role(responsibility_id, "responsibility_executive")

    def read_role(self, org_unit_id: str, role: str):
        def execute(request_id: str):
            if request.args:
                raise _RequestError(
                    "VALIDATION_ERROR", "The query string is invalid.", 400
                )
            actor, epoch = _principal(self.principal_resolver)
            result = self.service.read_role(
                ReadHigherRoleQuery(
                    actor_user_id=actor,
                    actor_scope_epoch=epoch,
                    request_id=request_id,
                    org_unit_id=_uuid(org_unit_id, "org_unit_id"),
                    role=role,
                )
            )
            return _json_response(result.as_dict(), request_id)

        return self.run(execute)

    def list_distribution_targets(self, space_id: str):
        def execute(request_id: str):
            if request.args:
                raise _RequestError(
                    "VALIDATION_ERROR", "The query string is invalid.", 400
                )
            actor, epoch = _principal(self.principal_resolver)
            result = self.service.list_distribution_targets(
                ListDistributionTargetsQuery(
                    actor_user_id=actor,
                    actor_scope_epoch=epoch,
                    request_id=request_id,
                    source_space_id=_uuid(space_id, "space_id"),
                )
            )
            return _json_response(result.as_dict(), request_id)

        return self.run(execute)

    def list_distributions(self, space_id: str):
        def execute(request_id: str):
            if set(request.args) - {"cursor", "limit"}:
                raise _RequestError("VALIDATION_ERROR", "The query string is invalid.", 400)
            actor, epoch = _principal(self.principal_resolver)
            raw_limit = _single_query("limit")
            if raw_limit is None:
                limit = 50
            elif not raw_limit.isascii() or not raw_limit.isdecimal():
                raise _RequestError("VALIDATION_ERROR", "limit is invalid.", 400)
            else:
                limit = int(raw_limit)
            if not 1 <= limit <= 100:
                raise _RequestError("VALIDATION_ERROR", "limit is invalid.", 400)
            result = self.service.list_distributions(
                ListDistributionsQuery(
                    actor_user_id=actor,
                    actor_scope_epoch=epoch,
                    request_id=request_id,
                    source_space_id=_uuid(space_id, "space_id"),
                    cursor=_single_query("cursor"),
                    limit=limit,
                )
            )
            return _json_response(
                {
                    "items": [_distribution_payload(item) for item in result.items],
                    "next_cursor": result.next_cursor,
                },
                request_id,
            )

        return self.run(execute)

    def create_distribution(self, space_id: str):
        def execute(request_id: str):
            actor, epoch = _principal(self.principal_resolver)
            body = _read_json(
                required=frozenset(
                    {
                        "operation_id",
                        "reason",
                        "source_item_id",
                        "source_revision_id",
                        "target_org_unit_id",
                        "target_scope_id",
                        "target_scope_kind",
                    }
                )
            )
            target_kind = body["target_scope_kind"]
            if target_kind not in {"org_unit", "native_space"}:
                raise _RequestError("VALIDATION_ERROR", "target_scope_kind is invalid.", 400)
            result = self.service.create_distribution(
                CreateDistributionCommand(
                    actor_user_id=actor,
                    actor_scope_epoch=epoch,
                    operation_id=_operation_id(body),
                    request_id=request_id,
                    source_space_id=_uuid(space_id, "space_id"),
                    source_item_id=_uuid(body["source_item_id"], "source_item_id"),
                    source_revision_id=_uuid(
                        body["source_revision_id"], "source_revision_id"
                    ),
                    target_org_unit_id=_uuid(
                        body["target_org_unit_id"], "target_org_unit_id"
                    ),
                    target_scope_kind=target_kind,
                    target_scope_id=_uuid(body["target_scope_id"], "target_scope_id"),
                    reason=_text(body["reason"], "reason", 1000),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), request_id, 201)

        return self.run(execute)

    def revoke_distribution(self, space_id: str, grant_id: str):
        def execute(request_id: str):
            actor, epoch = _principal(self.principal_resolver)
            body = _read_json(
                required=frozenset(
                    {"break_glass", "expected_row_version", "operation_id", "reason"}
                ),
                optional=frozenset({"confirmation"}),
            )
            confirmation = body.get("confirmation")
            if confirmation is not None:
                confirmation = _text(confirmation, "confirmation", 256)
            result = self.service.revoke_distribution(
                RevokeDistributionCommand(
                    actor_user_id=actor,
                    actor_scope_epoch=epoch,
                    operation_id=_operation_id(body),
                    request_id=request_id,
                    source_space_id=_uuid(space_id, "space_id"),
                    grant_id=_uuid(grant_id, "grant_id"),
                    expected_row_version=_positive(
                        body["expected_row_version"], "expected_row_version"
                    ),
                    reason=_text(body["reason"], "reason", 1000),
                    break_glass=_boolean(body["break_glass"], "break_glass"),
                    confirmation=confirmation,
                    request_meta=_request_meta(),
                )
            )
            return _json_response(result.as_dict(), request_id)

        return self.run(execute)


def _specs(handlers: _Handlers):
    return (
        (
            f"{HIGHER_ADMIN_ROUTE_PREFIX}/teams/<team_id>/leader",
            "knowledge_admin_v2_team_leader",
            ("GET", "PUT"),
            handlers.team_leader,
        ),
        (
            f"{HIGHER_ADMIN_ROUTE_PREFIX}/responsibilities/<responsibility_id>/executive",
            "knowledge_admin_v2_responsibility_executive",
            ("GET", "PUT"),
            handlers.responsibility_executive,
        ),
        (
            f"{HIGHER_KNOWLEDGE_ROUTE_PREFIX}/<space_id>/distribution-targets",
            "knowledge_v2_distribution_targets_list",
            ("GET",),
            handlers.list_distribution_targets,
        ),
        (
            f"{HIGHER_KNOWLEDGE_ROUTE_PREFIX}/<space_id>/distributions",
            "knowledge_v2_distributions_list",
            ("GET",),
            handlers.list_distributions,
        ),
        (
            f"{HIGHER_KNOWLEDGE_ROUTE_PREFIX}/<space_id>/distributions",
            "knowledge_v2_distributions_create",
            ("POST",),
            handlers.create_distribution,
        ),
        (
            f"{HIGHER_KNOWLEDGE_ROUTE_PREFIX}/<space_id>/distributions/<grant_id>/revoke",
            "knowledge_v2_distribution_revoke",
            ("POST",),
            handlers.revoke_distribution,
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
            raise RuntimeError("higher-authority routes must register before the first request")
        existing_endpoints = {rule.endpoint for rule in target.url_map.iter_rules()}
        existing_rules = {
            (_VARIABLE_RULE_RE.sub("<>", rule.rule), method)
            for rule in target.url_map.iter_rules()
            for method in _effective_methods(set(rule.methods))
        }
        for rule, endpoint, methods, _ in specs:
            if endpoint in existing_endpoints or endpoint in target.view_functions:
                raise RuntimeError(f"higher-authority endpoint already exists: {endpoint}")
            normalized = _VARIABLE_RULE_RE.sub("<>", rule)
            if any((normalized, method) in existing_rules for method in _effective_methods(methods)):
                raise RuntimeError(f"higher-authority route already exists: {rule}")
        return
    if bool(getattr(target, "_got_registered_once", False)):
        raise RuntimeError("higher-authority routes must register before Blueprint attach")

    def verify(state: object) -> None:
        app = getattr(state, "app", None)
        if getattr(state, "url_prefix", None) or getattr(state, "subdomain", None):
            raise RuntimeError("higher-authority Blueprint routes require the root host")
        existing_rules = {
            (_VARIABLE_RULE_RE.sub("<>", rule.rule), method)
            for rule in app.url_map.iter_rules()
            for method in _effective_methods(set(rule.methods))
        }
        for rule, _, methods, _ in specs:
            normalized = _VARIABLE_RULE_RE.sub("<>", rule)
            if any((normalized, method) in existing_rules for method in _effective_methods(methods)):
                raise RuntimeError(f"higher-authority Blueprint route already exists: {rule}")

    target.record(verify)


def register_higher_space_routes(
    target: Flask | Blueprint,
    *,
    service: object,
    principal_resolver: Callable[[], object],
) -> tuple[str, ...]:
    """Register exact higher-role and downward-distribution routes."""

    if not isinstance(target, (Flask, Blueprint)):
        raise TypeError("higher-authority route target must be a Flask app or Blueprint")
    if getattr(target, _REGISTERED_ATTRIBUTE, False):
        raise RuntimeError("higher-authority v2 routes are already registered")
    if not callable(principal_resolver):
        raise TypeError("principal_resolver must be callable")
    for method in (
        "create_distribution",
        "list_distribution_targets",
        "list_distributions",
        "read_role",
        "revoke_distribution",
        "set_role",
    ):
        if not callable(getattr(service, method, None)):
            raise TypeError(f"higher_authority service is missing {method}().")
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
    "HIGHER_ADMIN_ROUTE_PREFIX",
    "HIGHER_KNOWLEDGE_ROUTE_PREFIX",
    "MAX_HIGHER_AUTHORITY_JSON_BYTES",
    "register_higher_space_routes",
]

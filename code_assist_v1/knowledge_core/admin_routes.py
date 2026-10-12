"""Explicit, dependency-injected Flask adapter for P1.5 organization commands.

The module is intentionally absent from the default composition root.  A host
must opt in by supplying the two canonical services and a resolver for the
already-authenticated canonical session principal.  Browser Host/Origin/CSRF
enforcement remains a host-level concern (``auth.register_auth_routes``); this
adapter repeats principal/admin/epoch validation and never accepts actor facts
from request JSON.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import math
import re
from typing import Any, Literal
from uuid import UUID, uuid4

from flask import Blueprint, Flask, g, jsonify, request

from .scope import SessionPrincipal
from .services.purge import PurgeEligiblePageCommand


ADMIN_ROUTE_PREFIX = "/api/admin/v1"
MAX_ADMIN_JSON_BYTES = 65_536
_MAX_JSON_DEPTH = 8
_MAX_JSON_NODES = 1_024
_SQLITE_MAX_INTEGER = 9_223_372_036_854_775_807
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_REGISTERED_ATTRIBUTE = "_knowledge_core_admin_v1_registered"
_REQUEST_ID_ATTRIBUTE = "code_assist_admin_request_id"
_VARIABLE_RULE_RE = re.compile(r"<(?:(?:[^:<>]+):)?[^<>]+>", re.ASCII)


class _RequestError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        http_status: int,
        *,
        retryable: bool = False,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        self.details = dict(details or {})

    def as_error(self) -> dict[str, object]:
        return {
            "code": self.code,
            "details": self.details,
            "message": str(self),
            "retryable": self.retryable,
        }


@dataclass(frozen=True, slots=True)
class _Principal:
    user_id: str
    scope_epoch: int
    role: str


@dataclass(frozen=True, slots=True)
class _RouteSpec:
    rule: str
    endpoint: str
    methods: tuple[str, ...]
    handler_name: str


_ROUTES = (
    _RouteSpec(f"{ADMIN_ROUTE_PREFIX}/org/tree", "knowledge_admin_v1_tree", ("GET",), "tree"),
    _RouteSpec(f"{ADMIN_ROUTE_PREFIX}/org/units", "knowledge_admin_v1_create_unit", ("POST",), "create_unit"),
    _RouteSpec(f"{ADMIN_ROUTE_PREFIX}/org/units/<unit_id>/change-preview", "knowledge_admin_v1_preview_unit", ("POST",), "preview_unit"),
    _RouteSpec(f"{ADMIN_ROUTE_PREFIX}/org/units/<unit_id>", "knowledge_admin_v1_update_unit", ("PATCH",), "update_unit"),
    _RouteSpec(f"{ADMIN_ROUTE_PREFIX}/org/units/<unit_id>/move", "knowledge_admin_v1_move_unit", ("POST",), "move_unit"),
    _RouteSpec(f"{ADMIN_ROUTE_PREFIX}/users/<user_id>/part-membership", "knowledge_admin_v1_membership", ("PUT",), "membership"),
    _RouteSpec(f"{ADMIN_ROUTE_PREFIX}/org/parts/<part_id>/leader", "knowledge_admin_v1_leader", ("PUT",), "leader"),
    _RouteSpec(f"{ADMIN_ROUTE_PREFIX}/users/<user_id>/disable", "knowledge_admin_v1_disable_user", ("POST",), "disable_user"),
    _RouteSpec(f"{ADMIN_ROUTE_PREFIX}/users/<user_id>/enable", "knowledge_admin_v1_enable_user", ("POST",), "enable_user"),
)
_PURGE_ROUTE = _RouteSpec(
    f"{ADMIN_ROUTE_PREFIX}/purge-eligible",
    "knowledge_admin_v1_purge_eligible",
    ("GET",),
    "purge_eligible",
)


def _effective_methods(methods: tuple[str, ...] | set[str]) -> frozenset[str]:
    """Mirror Flask's implicit method surface for collision preflight."""

    effective = {str(method).upper() for method in methods}
    if "GET" in effective:
        effective.add("HEAD")
    effective.add("OPTIONS")
    return frozenset(effective)


def _service_api() -> Any:
    # Delayed lookup keeps importing this inert adapter side-effect free and
    # permits the P1.5 service module to remain the sole owner of command DTOs.
    from .services import organization as service_api

    required = (
        "AccountLifecycleCommand",
        "CreateOrgUnitCommand",
        "InactivateOrgUnitCommand",
        "MoveOrgUnitCommand",
        "OrgChangePreviewCommand",
        "OrgInactivationResolution",
        "OrgMemberResolution",
        "OrgMoveResolution",
        "RenameOrgUnitCommand",
        "SetLeaderCommand",
        "SetMembershipCommand",
    )
    missing = tuple(name for name in required if not callable(getattr(service_api, name, None)))
    if missing:
        raise TypeError(f"organization service command API is incomplete: {', '.join(missing)}")
    return service_api


def _canonical_uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise _validation(f"{field} must be a canonical lowercase UUID.", field)
    try:
        parsed = UUID(value)
    except ValueError as exc:  # pragma: no cover - regex is already strict
        raise _validation(f"{field} must be a valid UUID.", field) from exc
    if str(parsed) != value:
        raise _validation(f"{field} must be a canonical lowercase UUID.", field)
    return value


def _optional_uuid(value: object, *, field: str) -> str | None:
    return None if value is None else _canonical_uuid(value, field=field)


def _user_id(value: object, *, field: str = "user_id") -> str:
    if not isinstance(value, str) or _USER_ID_RE.fullmatch(value) is None:
        raise _validation(f"{field} must use the canonical lowercase login format.", field)
    return value


def _positive_int(value: object, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= _SQLITE_MAX_INTEGER
    ):
        raise _validation(
            f"{field} must be a positive SQLite integer.",
            field,
        )
    return value


def _text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise _validation(f"{field} must be text.", field)
    normalized = value.strip()
    if not 1 <= len(normalized) <= maximum:
        raise _validation(
            f"{field} must contain 1 through {maximum} non-blank characters.",
            field,
        )
    return normalized


def _preview_token(value: object) -> str:
    token = _text(value, field="preview_token", maximum=4096)
    if len(token) < 32:
        raise _validation(
            "preview_token must contain at least 32 characters.",
            "preview_token",
        )
    return token


def _validation(message: str, field: str | None = None) -> _RequestError:
    details: dict[str, object] = {}
    if field is not None:
        details["field"] = field
    return _RequestError("VALIDATION_ERROR", message, 400, details=details)


def _check_json_shape(value: object, *, depth: int = 0, count: list[int] | None = None) -> None:
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > _MAX_JSON_NODES:
        raise _validation("The JSON request contains too many values.")
    if depth > _MAX_JSON_DEPTH:
        raise _validation("The JSON request is nested too deeply.")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):  # JSON keys are strings; retained as an invariant.
                raise _validation("JSON object keys must be strings.")
            _check_json_shape(child, depth=depth + 1, count=count)
    elif isinstance(value, list):
        for child in value:
            _check_json_shape(child, depth=depth + 1, count=count)
    elif isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise _validation("JSON strings must contain valid Unicode scalar values.") from exc
    elif isinstance(value, float) and not math.isfinite(value):
        raise _validation("Non-finite JSON numbers are forbidden.")


def _reject_constant(value: str) -> object:
    raise _validation(f"Non-finite JSON value {value!r} is forbidden.")


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, child in pairs:
        if key in value:
            raise _validation("Duplicate JSON object keys are forbidden.", key)
        value[key] = child
    return value


def _read_json() -> dict[str, object]:
    if request.mimetype != "application/json":
        raise _validation("Content-Type must be application/json.")
    if request.content_length is not None and request.content_length > MAX_ADMIN_JSON_BYTES:
        raise _validation("The JSON request exceeds 65536 bytes.")
    # Bound the stream read even when a reverse proxy forwards chunked input
    # without Content-Length.  Reading the entire request first and checking
    # afterward would leave this small administration surface open to a memory
    # denial of service.
    raw = request.stream.read(MAX_ADMIN_JSON_BYTES + 1)
    if not raw:
        raise _validation("A JSON object request body is required.")
    if len(raw) > MAX_ADMIN_JSON_BYTES:
        raise _validation("The JSON request exceeds 65536 bytes.")
    try:
        decoded = raw.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise _validation("The JSON request must be valid UTF-8.") from exc
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise _validation("The request body is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise _validation("The JSON request body must be an object.")
    _check_json_shape(value)
    return value


def _purge_query() -> tuple[str | None, int]:
    unknown = sorted(set(request.args).difference({"cursor", "limit"}))
    if unknown:
        raise _validation("Unknown query parameters are forbidden.", unknown[0])
    for key in ("cursor", "limit"):
        if len(request.args.getlist(key)) > 1:
            raise _validation(f"{key} may be supplied only once.", key)
    cursor = request.args.get("cursor")
    if cursor is not None and (
        not 1 <= len(cursor) <= 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in cursor)
    ):
        raise _validation("cursor is invalid.", "cursor")
    raw_limit = request.args.get("limit")
    if raw_limit is None:
        limit = 50
    elif (
        not raw_limit.isascii()
        or not raw_limit.isdigit()
        or raw_limit.startswith("0")
    ):
        raise _validation("limit must be an integer from 1 through 100.", "limit")
    else:
        limit = int(raw_limit)
        if not 1 <= limit <= 100:
            raise _validation("limit must be an integer from 1 through 100.", "limit")
    return cursor, limit


def _fields(
    value: Mapping[str, object],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    missing = sorted(required.difference(value))
    if missing:
        raise _validation("Missing required JSON fields.", missing[0])
    unknown = sorted(set(value).difference(required | optional))
    if unknown:
        raise _validation("Unknown JSON fields are forbidden.", unknown[0])


def _operation_id(value: Mapping[str, object]) -> str:
    body_value = value.get("operation_id")
    header_present = "Idempotency-Key" in request.headers
    header_value = request.headers.get("Idempotency-Key")
    if body_value is None:
        raise _RequestError(
            "OPERATION_ID_REQUIRED",
            "A retryable mutation requires body operation_id.",
            428,
        )
    body_id = _canonical_uuid(body_value, field="operation_id")
    if header_present and not header_value:
        raise _validation(
            "Idempotency-Key must be a canonical lowercase UUID when present.",
            "Idempotency-Key",
        )
    header_id = (
        None
        if not header_present
        else _canonical_uuid(header_value, field="Idempotency-Key")
    )
    if header_id is not None and body_id != header_id:
        raise _RequestError(
            "IDEMPOTENCY_CONFLICT",
            "Idempotency-Key must match body operation_id.",
            409,
            details={"field": "Idempotency-Key"},
        )
    return body_id


def _request_meta() -> dict[str, object]:
    return {
        "host": request.host,
        "method": request.method,
        "path": request.path,
        "remote_addr": request.remote_addr,
        "user_agent": request.user_agent.string[:500],
    }


def _resolve_principal(resolver: Callable[[], object]) -> _Principal:
    value = resolver()
    if value is None:
        raise _RequestError(
            "AUTHENTICATION_REQUIRED",
            "A valid canonical account session is required.",
            401,
        )
    if isinstance(value, Mapping):
        user_value = value.get("id")
        epoch_value = value.get("scope_epoch")
        role_value = value.get("role")
    else:
        user_value = getattr(value, "id", None)
        epoch_value = getattr(value, "scope_epoch", None)
        role_value = getattr(value, "role", None)
    try:
        principal = _Principal(
            user_id=_user_id(user_value, field="principal.id"),
            scope_epoch=_positive_int(epoch_value, field="principal.scope_epoch"),
            role=str(role_value) if isinstance(role_value, str) else "",
        )
    except _RequestError as exc:
        raise _RequestError(
            "AUTHENTICATION_REQUIRED",
            "The canonical account session principal is invalid.",
            401,
        ) from exc
    if principal.role != "admin":
        raise _RequestError(
            "FORBIDDEN",
            "An active system administrator must perform this command.",
            403,
        )
    return principal


def _error_response(error: object, request_id: str):
    if isinstance(error, _RequestError):
        payload = error.as_error()
        status = error.http_status
    else:
        as_error = getattr(error, "as_error", None)
        status = getattr(error, "http_status", None)
        if not callable(as_error) or isinstance(status, bool) or not isinstance(status, int):
            raise error  # type: ignore[misc]
        payload = as_error()
        if not isinstance(payload, Mapping):
            raise TypeError("canonical service as_error() must return a mapping")
        payload = dict(payload)
    response = jsonify({"error": payload, "request_id": request_id})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Request-ID"] = request_id
    return response, status


def _internal_error_response(request_id: str):
    """Return the frozen, non-disclosing envelope for an unexpected failure."""

    return _error_response(
        _RequestError(
            "INTERNAL_ERROR",
            "The administrator command could not be completed.",
            500,
            retryable=True,
            details={"state": "internal_error"},
        ),
        request_id,
    )


def _json_response(payload: Mapping[str, object], request_id: str, status: int = 200):
    response = jsonify(dict(payload))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Request-ID"] = request_id
    return response, status


def _result_mapping(result: object) -> dict[str, object]:
    as_dict = getattr(result, "as_dict", None)
    value = as_dict() if callable(as_dict) else result
    if not isinstance(value, Mapping):
        raise TypeError("canonical service result must be a mapping or expose as_dict()")
    return dict(value)


def _mutation_response(
    result: object,
    *,
    request_id: str,
    operation_id: str,
    status_code: int = 200,
):
    value = _result_mapping(result)
    if value.get("request_id") != request_id or value.get("operation_id") != operation_id:
        raise TypeError("canonical mutation result correlation does not match the request")
    payload = {
        "operation_id": operation_id,
        "request_id": request_id,
        "resource_id": value.get("resource_id"),
        "row_version": value.get("row_version"),
        "status": value.get("status"),
    }
    if not isinstance(payload["status"], str) or not 1 <= len(payload["status"]) <= 80:
        raise TypeError("canonical mutation result has an invalid status")
    if payload["resource_id"] is not None:
        if (
            not isinstance(payload["resource_id"], str)
            or _UUID_RE.fullmatch(payload["resource_id"]) is None
        ):
            raise TypeError("canonical mutation result has an invalid resource_id")
    if payload["row_version"] is not None:
        if (
            isinstance(payload["row_version"], bool)
            or not isinstance(payload["row_version"], int)
            or not 1 <= payload["row_version"] <= _SQLITE_MAX_INTEGER
        ):
            raise TypeError("canonical mutation result has an invalid row_version")
    return _json_response(payload, request_id, status_code)


def _account_response(result: object, *, request_id: str, operation_id: str):
    value = _result_mapping(result)
    if value.get("operation_id") != operation_id:
        raise TypeError("canonical account result operation does not match the request")
    row_version = value.get("row_version")
    if (
        isinstance(row_version, bool)
        or not isinstance(row_version, int)
        or not 1 <= row_version <= _SQLITE_MAX_INTEGER
    ):
        raise TypeError("canonical account result has an invalid row_version")
    status = value.get("account_status")
    if status not in ("active", "disabled"):
        raise TypeError("canonical account result has an invalid account status")
    return _json_response(
        {
            "operation_id": operation_id,
            "request_id": request_id,
            "resource_id": None,
            "row_version": row_version,
            "status": status,
        },
        request_id,
    )


class _Handlers:
    def __init__(
        self,
        organization_service: object,
        account_service: object,
        principal_resolver: Callable[[], object],
        service_api: object,
        purge_service: object | None,
    ) -> None:
        self.organization = organization_service
        self.accounts = account_service
        self.principal_resolver = principal_resolver
        self.api = service_api
        self.purge = purge_service

    def _run(self, callback: Callable[[str, _Principal], object]):
        inherited = getattr(g, _REQUEST_ID_ATTRIBUTE, None)
        request_id = (
            inherited
            if isinstance(inherited, str) and 1 <= len(inherited) <= 128
            else str(uuid4())
        )
        try:
            principal = _resolve_principal(self.principal_resolver)
            return callback(request_id, principal)
        except Exception as exc:
            try:
                return _error_response(exc, request_id)
            except Exception:
                return _internal_error_response(request_id)

    def tree(self):
        def execute(request_id: str, principal: _Principal):
            result = self.organization.get_tree(
                actor_user_id=principal.user_id,
                actor_scope_epoch=principal.scope_epoch,
                request_id=request_id,
            )
            value = _result_mapping(result)
            if value.get("request_id") != request_id:
                raise TypeError("canonical tree result correlation does not match the request")
            return _json_response(value, request_id)

        return self._run(execute)

    def purge_eligible(self):
        def execute(request_id: str, principal: _Principal):
            if self.purge is None:
                raise TypeError("purge queue service is unavailable")
            cursor, limit = _purge_query()
            result = self.purge.list_eligible(
                PurgeEligiblePageCommand(
                    principal=SessionPrincipal(
                        principal.user_id,
                        principal.scope_epoch,
                    ),
                    request_id=request_id,
                    cursor=cursor,
                    limit=limit,
                )
            )
            value = _result_mapping(result)
            if value.get("request_id") != request_id:
                raise TypeError(
                    "canonical purge queue correlation does not match the request"
                )
            return _json_response(value, request_id)

        return self._run(execute)

    def create_unit(self):
        def execute(request_id: str, principal: _Principal):
            body = _read_json()
            _fields(
                body,
                required=frozenset({"parent_id", "kind", "name"}),
                optional=frozenset({"operation_id"}),
            )
            operation_id = _operation_id(body)
            kind = body["kind"]
            if kind not in ("team", "part"):
                raise _validation("kind must be team or part.", "kind")
            command = self.api.CreateOrgUnitCommand(
                actor_user_id=principal.user_id,
                actor_scope_epoch=principal.scope_epoch,
                operation_id=operation_id,
                request_id=request_id,
                parent_id=_canonical_uuid(body["parent_id"], field="parent_id"),
                kind=kind,
                name=_text(body["name"], field="name", maximum=120),
                reason=None,
                request_meta=_request_meta(),
            )
            return _mutation_response(
                self.organization.create_unit(command),
                request_id=request_id,
                operation_id=operation_id,
                status_code=201,
            )

        return self._run(execute)

    def preview_unit(self, unit_id: str):
        def execute(request_id: str, principal: _Principal):
            canonical_unit_id = _canonical_uuid(unit_id, field="unit_id")
            body = _read_json()
            _fields(
                body,
                required=frozenset({"expected_row_version", "change"}),
                optional=frozenset({"new_parent_id"}),
            )
            change = body["change"]
            if change not in ("move", "inactivate"):
                raise _validation("change must be move or inactivate.", "change")
            raw_parent = body.get("new_parent_id")
            if change == "move" and raw_parent is None:
                raise _validation("new_parent_id is required for a move preview.", "new_parent_id")
            if change == "inactivate" and raw_parent is not None:
                raise _validation("new_parent_id must be null for inactivation.", "new_parent_id")
            command = self.api.OrgChangePreviewCommand(
                actor_user_id=principal.user_id,
                actor_scope_epoch=principal.scope_epoch,
                request_id=request_id,
                unit_id=canonical_unit_id,
                expected_row_version=_positive_int(body["expected_row_version"], field="expected_row_version"),
                change=change,
                new_parent_id=_optional_uuid(raw_parent, field="new_parent_id"),
                request_meta=_request_meta(),
            )
            value = _result_mapping(self.organization.preview_change(command))
            if value.get("request_id") != request_id:
                raise TypeError("canonical preview result correlation does not match the request")
            return _json_response(value, request_id)

        return self._run(execute)

    def update_unit(self, unit_id: str):
        def execute(request_id: str, principal: _Principal):
            canonical_unit_id = _canonical_uuid(unit_id, field="unit_id")
            body = _read_json()
            operation_id = _operation_id(body)
            if "name" in body:
                _fields(
                    body,
                    required=frozenset({"expected_row_version", "name"}),
                    optional=frozenset({"operation_id"}),
                )
                command = self.api.RenameOrgUnitCommand(
                    actor_user_id=principal.user_id,
                    actor_scope_epoch=principal.scope_epoch,
                    operation_id=operation_id,
                    request_id=request_id,
                    unit_id=canonical_unit_id,
                    expected_row_version=_positive_int(body["expected_row_version"], field="expected_row_version"),
                    name=_text(body["name"], field="name", maximum=120),
                    reason=None,
                    request_meta=_request_meta(),
                )
                result = self.organization.rename_unit(command)
            else:
                _fields(
                    body,
                    required=frozenset({"expected_row_version", "status", "reason", "preview_token", "resolution"}),
                    optional=frozenset({"operation_id"}),
                )
                if body["status"] != "inactive":
                    raise _validation("status must be inactive.", "status")
                reason = _text(body["reason"], field="reason", maximum=1000)
                resolution = self._inactivation_resolution(body["resolution"])
                command = self.api.InactivateOrgUnitCommand(
                    actor_user_id=principal.user_id,
                    actor_scope_epoch=principal.scope_epoch,
                    operation_id=operation_id,
                    request_id=request_id,
                    unit_id=canonical_unit_id,
                    expected_row_version=_positive_int(body["expected_row_version"], field="expected_row_version"),
                    preview_token=_preview_token(body["preview_token"]),
                    resolution=resolution,
                    reason=reason,
                    request_meta=_request_meta(),
                )
                result = self.organization.inactivate_unit(command)
            return _mutation_response(result, request_id=request_id, operation_id=operation_id)

        return self._run(execute)

    def _inactivation_resolution(self, value: object):
        if not isinstance(value, Mapping):
            raise _validation("resolution must be an object.", "resolution")
        _fields(
            value,
            required=frozenset({"member_resolutions", "expected_current_leader_assignment_id", "leader_action", "expected_space_row_version", "space_action"}),
        )
        if value["leader_action"] != "close":
            raise _validation("leader_action must be close.", "resolution.leader_action")
        if value["space_action"] != "freeze_if_present":
            raise _validation("space_action must be freeze_if_present.", "resolution.space_action")
        members = value["member_resolutions"]
        if not isinstance(members, list) or len(members) > 200:
            raise _validation("member_resolutions must be an array of at most 200 entries.", "resolution.member_resolutions")
        resolved = []
        seen: set[str] = set()
        for index, member in enumerate(members):
            field = f"resolution.member_resolutions[{index}]"
            if not isinstance(member, Mapping):
                raise _validation(f"{field} must be an object.", field)
            _fields(member, required=frozenset({"user_id", "expected_user_row_version", "target_part_id"}))
            user = _user_id(member["user_id"], field=f"{field}.user_id")
            if user in seen:
                raise _validation("Each member may be resolved only once.", f"{field}.user_id")
            seen.add(user)
            resolved.append(
                self.api.OrgMemberResolution(
                    user_id=user,
                    expected_user_row_version=_positive_int(member["expected_user_row_version"], field=f"{field}.expected_user_row_version"),
                    target_part_id=_optional_uuid(member["target_part_id"], field=f"{field}.target_part_id"),
                )
            )
        expected_space = value["expected_space_row_version"]
        return self.api.OrgInactivationResolution(
            member_resolutions=tuple(resolved),
            expected_current_leader_assignment_id=_optional_uuid(value["expected_current_leader_assignment_id"], field="resolution.expected_current_leader_assignment_id"),
            leader_action="close",
            expected_space_row_version=(None if expected_space is None else _positive_int(expected_space, field="resolution.expected_space_row_version")),
            space_action="freeze_if_present",
        )

    def move_unit(self, unit_id: str):
        def execute(request_id: str, principal: _Principal):
            canonical_unit_id = _canonical_uuid(unit_id, field="unit_id")
            body = _read_json()
            _fields(
                body,
                required=frozenset({"expected_row_version", "preview_token", "new_parent_id", "resolution"}),
                optional=frozenset({"operation_id"}),
            )
            operation_id = _operation_id(body)
            resolution = body["resolution"]
            if not isinstance(resolution, Mapping):
                raise _validation("resolution must be an object.", "resolution")
            _fields(resolution, required=frozenset({"retain_memberships", "retain_leader", "retain_space"}))
            if any(resolution[key] is not True for key in ("retain_memberships", "retain_leader", "retain_space")):
                raise _validation("Move resolution must explicitly retain memberships, leader, and space.", "resolution")
            command = self.api.MoveOrgUnitCommand(
                actor_user_id=principal.user_id,
                actor_scope_epoch=principal.scope_epoch,
                operation_id=operation_id,
                request_id=request_id,
                unit_id=canonical_unit_id,
                expected_row_version=_positive_int(body["expected_row_version"], field="expected_row_version"),
                preview_token=_preview_token(body["preview_token"]),
                new_parent_id=_canonical_uuid(body["new_parent_id"], field="new_parent_id"),
                resolution=self.api.OrgMoveResolution(
                    retain_memberships=True,
                    retain_leader=True,
                    retain_space=True,
                ),
                reason=None,
                request_meta=_request_meta(),
            )
            return _mutation_response(
                self.organization.move_unit(command),
                request_id=request_id,
                operation_id=operation_id,
            )

        return self._run(execute)

    def membership(self, user_id: str):
        def execute(request_id: str, principal: _Principal):
            target_user_id = _user_id(user_id)
            body = _read_json()
            _fields(
                body,
                required=frozenset({"expected_user_row_version", "part_id", "expected_current_leader_assignment_id", "expected_part_row_version", "reason"}),
                optional=frozenset({"operation_id", "replacement_leader_user_id"}),
            )
            operation_id = _operation_id(body)
            command = self.api.SetMembershipCommand(
                actor_user_id=principal.user_id,
                actor_scope_epoch=principal.scope_epoch,
                operation_id=operation_id,
                request_id=request_id,
                target_user_id=target_user_id,
                expected_user_row_version=_positive_int(body["expected_user_row_version"], field="expected_user_row_version"),
                part_id=_optional_uuid(body["part_id"], field="part_id"),
                expected_current_leader_assignment_id=_optional_uuid(body["expected_current_leader_assignment_id"], field="expected_current_leader_assignment_id"),
                replacement_leader_user_id=(None if body.get("replacement_leader_user_id") is None else _user_id(body["replacement_leader_user_id"], field="replacement_leader_user_id")),
                reason=_text(body["reason"], field="reason", maximum=1000),
                expected_part_row_version=(None if body["expected_part_row_version"] is None else _positive_int(body["expected_part_row_version"], field="expected_part_row_version")),
                request_meta=_request_meta(),
            )
            return _mutation_response(
                self.organization.set_membership(command),
                request_id=request_id,
                operation_id=operation_id,
            )

        return self._run(execute)

    def leader(self, part_id: str):
        def execute(request_id: str, principal: _Principal):
            canonical_part_id = _canonical_uuid(part_id, field="part_id")
            body = _read_json()
            _fields(
                body,
                required=frozenset({"leader_user_id", "expected_part_row_version", "expected_current_leader_assignment_id", "reason"}),
                optional=frozenset({"operation_id"}),
            )
            operation_id = _operation_id(body)
            command = self.api.SetLeaderCommand(
                actor_user_id=principal.user_id,
                actor_scope_epoch=principal.scope_epoch,
                operation_id=operation_id,
                request_id=request_id,
                part_id=canonical_part_id,
                leader_user_id=_user_id(body["leader_user_id"], field="leader_user_id"),
                expected_part_row_version=_positive_int(body["expected_part_row_version"], field="expected_part_row_version"),
                expected_current_leader_assignment_id=_optional_uuid(body["expected_current_leader_assignment_id"], field="expected_current_leader_assignment_id"),
                reason=_text(body["reason"], field="reason", maximum=1000),
                request_meta=_request_meta(),
            )
            return _mutation_response(
                self.organization.set_leader(command),
                request_id=request_id,
                operation_id=operation_id,
            )

        return self._run(execute)

    def _set_account_status(self, user_id: str, target: Literal["disabled", "active"]):
        def execute(request_id: str, principal: _Principal):
            target_user_id = _user_id(user_id)
            body = _read_json()
            _fields(
                body,
                required=frozenset({"expected_user_row_version", "expected_current_leader_assignment_id", "expected_part_row_version", "reason"}),
                optional=frozenset({"operation_id", "replacement_leader_user_id"}),
            )
            operation_id = _operation_id(body)
            replacement = body.get("replacement_leader_user_id")
            if target == "active" and replacement is not None:
                raise _validation("replacement_leader_user_id is valid only while disabling.", "replacement_leader_user_id")
            command = self.api.AccountLifecycleCommand(
                actor_user_id=principal.user_id,
                actor_scope_epoch=principal.scope_epoch,
                target_user_id=target_user_id,
                expected_row_version=_positive_int(body["expected_user_row_version"], field="expected_user_row_version"),
                operation_id=operation_id,
                request_id=request_id,
                reason=_text(body["reason"], field="reason", maximum=2000),
                replacement_leader_user_id=(None if replacement is None else _user_id(replacement, field="replacement_leader_user_id")),
                expected_current_leader_assignment_id=_optional_uuid(body["expected_current_leader_assignment_id"], field="expected_current_leader_assignment_id"),
                expected_part_row_version=(None if body["expected_part_row_version"] is None else _positive_int(body["expected_part_row_version"], field="expected_part_row_version")),
                request_meta=_request_meta(),
            )
            if target == "disabled":
                result = self.accounts.disable_account(command)
            else:
                result = self.accounts.reenable_account(command)
            return _account_response(result, request_id=request_id, operation_id=operation_id)

        return self._run(execute)

    def disable_user(self, user_id: str):
        return self._set_account_status(user_id, "disabled")

    def enable_user(self, user_id: str):
        return self._set_account_status(user_id, "active")


def _validate_registration(
    target: object,
    organization_service: object,
    account_service: object,
    principal_resolver: object,
    purge_service: object | None,
    route_specs: tuple[_RouteSpec, ...],
) -> object:
    if not isinstance(target, (Flask, Blueprint)):
        raise TypeError("admin route target must be a Flask app or Blueprint")
    if isinstance(target, Flask) and bool(getattr(target, "_got_first_request", False)):
        raise RuntimeError("administrator routes must register before the first request")
    if isinstance(target, Blueprint) and bool(getattr(target, "_got_registered_once", False)):
        raise RuntimeError("administrator routes must register before the Blueprint is attached")
    if isinstance(target, Blueprint) and tuple(target.deferred_functions):
        raise RuntimeError(
            "administrator routes require a dedicated empty Blueprint"
        )
    if isinstance(target, Blueprint):
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
            raise RuntimeError(
                "administrator routes require a dedicated empty Blueprint"
            )
    if getattr(target, _REGISTERED_ATTRIBUTE, False):
        raise RuntimeError("P1.5 administrator routes are already registered on this target")
    for method in (
        "get_tree",
        "preview_change",
        "create_unit",
        "rename_unit",
        "move_unit",
        "inactivate_unit",
        "set_membership",
        "set_leader",
    ):
        if not callable(getattr(organization_service, method, None)):
            raise TypeError(f"organization_service is missing {method}()")
    for method in ("disable_account", "reenable_account"):
        if not callable(getattr(account_service, method, None)):
            raise TypeError(f"account_service is missing {method}()")
    if not callable(principal_resolver):
        raise TypeError("principal_resolver must be callable")
    if purge_service is not None and not callable(
        getattr(purge_service, "list_eligible", None)
    ):
        raise TypeError("purge_service is missing list_eligible()")

    service_api = _service_api()
    url_map = getattr(target, "url_map", None)
    if url_map is not None and callable(getattr(url_map, "iter_rules", None)):
        existing_endpoints = {rule.endpoint for rule in url_map.iter_rules()}
        existing_rules = {
            (_VARIABLE_RULE_RE.sub("<>", rule.rule), method)
            for rule in url_map.iter_rules()
            for method in _effective_methods(set(rule.methods))
        }
        for spec in route_specs:
            if spec.endpoint in existing_endpoints:
                raise RuntimeError(f"route endpoint already exists: {spec.endpoint}")
            for method in _effective_methods(spec.methods):
                normalized = _VARIABLE_RULE_RE.sub("<>", spec.rule)
                if (normalized, method) in existing_rules:
                    raise RuntimeError(f"route already exists: {method} {spec.rule}")
    return service_api


def _blueprint_attach_preflight(
    blueprint: Blueprint, route_specs: tuple[_RouteSpec, ...]
) -> None:
    """Reject application-level shadow routes before deferred rules attach."""

    def verify(state: object) -> None:
        app = getattr(state, "app", None)
        if getattr(state, "url_prefix", None):
            raise RuntimeError(
                "administrator Blueprint routes cannot use a URL prefix"
            )
        url_map = getattr(app, "url_map", None)
        if url_map is None or not callable(getattr(url_map, "iter_rules", None)):
            raise RuntimeError("Blueprint registration cannot inspect the host URL map")
        if getattr(state, "subdomain", None):
            raise RuntimeError(
                "administrator Blueprint routes cannot use a subdomain"
            )
        if getattr(state, "url_defaults", None):
            raise RuntimeError(
                "administrator Blueprint routes cannot use URL defaults"
            )
        blueprint_name = str(getattr(state, "name", ""))
        name_prefix = str(getattr(state, "name_prefix", ""))
        if not blueprint_name:
            raise RuntimeError("Blueprint registration has no stable name")
        final_prefix = f"{name_prefix}.{blueprint_name}".lstrip(".")
        existing_endpoints = {rule.endpoint for rule in url_map.iter_rules()}
        view_functions = getattr(app, "view_functions", {})
        existing = {
            (_VARIABLE_RULE_RE.sub("<>", rule.rule), method)
            for rule in url_map.iter_rules()
            for method in _effective_methods(set(rule.methods))
        }
        for spec in route_specs:
            final_endpoint = f"{final_prefix}.{spec.endpoint}"
            if (
                final_endpoint in existing_endpoints
                or final_endpoint in view_functions
            ):
                raise RuntimeError(
                    "route endpoint already exists on Blueprint host: "
                    f"{final_endpoint}"
                )
            normalized = _VARIABLE_RULE_RE.sub("<>", spec.rule)
            for method in _effective_methods(spec.methods):
                if (normalized, method) in existing:
                    raise RuntimeError(
                        f"route already exists on Blueprint host: {method} {spec.rule}"
                    )

    # Flask replays route deferred functions for every named Blueprint attach,
    # so collision validation must also run every time (not record_once).
    blueprint.record(verify)


def register_admin_routes(
    app_or_blueprint: object,
    organization_service: object,
    account_service: object,
    principal_resolver: Callable[[], object],
    *,
    purge_service: object | None = None,
) -> tuple[str, ...]:
    """Register the nine P1.5 routes and optional P5.6 purge queue.

    Calling this function is the only registration path; importing the module
    or using ``register_knowledge_core`` continues to expose zero canonical
    routes. The purge queue appears only when its explicit service is supplied.
    """

    route_specs = _ROUTES + ((_PURGE_ROUTE,) if purge_service is not None else ())

    service_api = _validate_registration(
        app_or_blueprint,
        organization_service,
        account_service,
        principal_resolver,
        purge_service,
        route_specs,
    )
    handlers = _Handlers(
        organization_service,
        account_service,
        principal_resolver,
        service_api,
        purge_service,
    )
    if isinstance(app_or_blueprint, Blueprint):
        _blueprint_attach_preflight(app_or_blueprint, route_specs)
    for spec in route_specs:
        app_or_blueprint.add_url_rule(  # type: ignore[attr-defined]
            spec.rule,
            endpoint=spec.endpoint,
            view_func=getattr(handlers, spec.handler_name),
            methods=list(spec.methods),
        )
    setattr(app_or_blueprint, _REGISTERED_ATTRIBUTE, True)
    return tuple(spec.rule for spec in route_specs)


__all__ = [
    "ADMIN_ROUTE_PREFIX",
    "MAX_ADMIN_JSON_BYTES",
    "register_admin_routes",
]

"""Strict Flask adapters for the SBE-2.1 personal-Wiki lifecycle surface."""

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
from .services.personal_wikis import (
    ArchivePersonalWikiCommand,
    CreatePersonalWikiCommand,
    ListPersonalWikisQuery,
    PersonalWikiServiceError,
    RenamePersonalWikiCommand,
    RestorePersonalWikiCommand,
    SetDefaultPersonalWikiCommand,
)
from .services.personal_wiki_search import PersonalWikiManagementSearchRequest


PERSONAL_WIKI_ROUTE_PREFIX = "/api/knowledge/v2/personal-wikis"
MAX_PERSONAL_WIKI_JSON_BYTES = 16 * 1024

_REGISTERED_ATTRIBUTE = "_code_assist_personal_wiki_v2_registered"
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_VARIABLE_RULE_RE = re.compile(r"<(?:(?:[^:<>]+):)?[^<>]+>")


class _RequestError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        http_status: int,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.retryable = retryable


def _request_id() -> str:
    return str(uuid4())


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise _RequestError("VALIDATION_ERROR", f"{field} is invalid.", 400)
    return value


def _positive(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
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
    if not isinstance(user_id, str) or _USER_RE.fullmatch(user_id) is None:
        raise _RequestError(
            "AUTHENTICATION_REQUIRED", "The account session is invalid.", 401
        )
    try:
        scope_epoch = _positive(epoch, "principal.scope_epoch")
    except _RequestError as exc:
        raise _RequestError(
            "AUTHENTICATION_REQUIRED", "The account session is invalid.", 401
        ) from exc
    return user_id, scope_epoch


def _read_json(
    *, required: frozenset[str], optional: frozenset[str] = frozenset()
) -> dict[str, object]:
    content_length = request.content_length
    if content_length is not None and content_length > MAX_PERSONAL_WIKI_JSON_BYTES:
        raise _RequestError("VALIDATION_ERROR", "The JSON request is too large.", 400)
    if request.mimetype != "application/json":
        raise _RequestError("VALIDATION_ERROR", "application/json is required.", 400)
    raw = request.get_data(cache=False, as_text=False)
    if len(raw) > MAX_PERSONAL_WIKI_JSON_BYTES or raw.startswith(b"\xef\xbb\xbf"):
        raise _RequestError("VALIDATION_ERROR", "The JSON request is invalid.", 400)

    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON member")
            value[key] = item
        return value

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicate,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise _RequestError("VALIDATION_ERROR", "The JSON request is invalid.", 400) from exc
    if (
        not isinstance(value, dict)
        or frozenset(value) - required - optional
        or not required <= frozenset(value)
    ):
        raise _RequestError("VALIDATION_ERROR", "The JSON request shape is invalid.", 400)
    stack: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > 128 or depth > 4:
            raise _RequestError("VALIDATION_ERROR", "The JSON request is too complex.", 400)
        if isinstance(current, float) and not math.isfinite(current):
            raise _RequestError("VALIDATION_ERROR", "The JSON request is invalid.", 400)
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    return value


def _operation_id(body: Mapping[str, object]) -> str:
    operation_id = _uuid(body.get("operation_id"), "operation_id")
    aliases = request.headers.getlist("Idempotency-Key")
    if not aliases:
        return operation_id
    if len(aliases) != 1 or aliases[0] != operation_id:
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


def _utc(milliseconds: int) -> str:
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, int) or milliseconds < 0:
        raise TypeError("Canonical personal-Wiki timestamp is invalid.")
    return (
        datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _profile_payload(profile: object) -> dict[str, object]:
    archived_at = getattr(profile, "archived_at_ms")
    return {
        "archived_at": None if archived_at is None else _utc(archived_at),
        "archive_reason": getattr(profile, "archive_reason"),
        "created_at": _utc(getattr(profile, "created_at_ms")),
        "display_name": getattr(profile, "display_name"),
        "is_default": getattr(profile, "is_default"),
        "lifecycle_generation": getattr(profile, "lifecycle_generation"),
        "lifecycle_status": getattr(profile, "lifecycle_status"),
        "row_version": getattr(profile, "row_version"),
        "space_id": getattr(profile, "space_id"),
        "updated_at": _utc(getattr(profile, "updated_at_ms")),
    }


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
            "retryable": exc.retryable,
        }
        status = exc.http_status
    elif isinstance(exc, PersonalWikiServiceError):
        error = exc.as_error()
        status = exc.http_status
    elif isinstance(exc, (ValueError, TypeError)):
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
            "retryable": False,
        }
        status = int(getattr(exc, "http_status", 409))
    else:
        error = {
            "code": "INTERNAL_ERROR",
            "details": {"state": "internal_error"},
            "message": "The personal-Wiki request could not be completed.",
            "retryable": True,
        }
        status = 500
    return _json_response({"error": error}, request_id, status)


def _mutation_payload(result: object) -> dict[str, object]:
    return {
        "operation_id": getattr(result, "operation_id"),
        "resource_id": getattr(result, "resource_id"),
        "row_version": getattr(result, "row_version"),
        "status": getattr(result, "status"),
    }


class _Handlers:
    __slots__ = ("principal_resolver", "search_service", "service")

    def __init__(
        self,
        service: object,
        principal_resolver: Callable[[], object],
        search_service: object | None = None,
    ) -> None:
        self.service = service
        self.principal_resolver = principal_resolver
        self.search_service = search_service

    def run(self, action: Callable[[str], Any]):
        request_id = _request_id()
        try:
            return action(request_id)
        except Exception as exc:
            return _error_response(exc, request_id)

    def list_profiles(self):
        def execute(request_id: str):
            allowed = {"cursor", "lifecycle_status", "limit", "query"}
            if set(request.args) - allowed:
                raise _RequestError("VALIDATION_ERROR", "The query string is invalid.", 400)
            actor, epoch = _principal(self.principal_resolver)
            lifecycle = _single_query("lifecycle_status") or "active"
            if lifecycle not in {"active", "archived"}:
                raise _RequestError("VALIDATION_ERROR", "lifecycle_status is invalid.", 400)
            raw_limit = _single_query("limit")
            if raw_limit is None:
                limit = 50
            elif not raw_limit.isascii() or not raw_limit.isdecimal():
                raise _RequestError("VALIDATION_ERROR", "limit is invalid.", 400)
            else:
                limit = int(raw_limit)
            if not 1 <= limit <= 100:
                raise _RequestError("VALIDATION_ERROR", "limit is invalid.", 400)
            result = self.service.list_profiles(
                ListPersonalWikisQuery(
                    actor_user_id=actor,
                    actor_scope_epoch=epoch,
                    request_id=request_id,
                    lifecycle_status=lifecycle,
                    query=_single_query("query"),
                    cursor=_single_query("cursor"),
                    limit=limit,
                )
            )
            return _json_response(
                {
                    "items": [_profile_payload(item) for item in result.items],
                    "next_cursor": result.next_cursor,
                },
                request_id,
            )

        return self.run(execute)

    def search(self):
        def execute(request_id: str):
            if self.search_service is None:
                raise _RequestError("NOT_FOUND", "The route is unavailable.", 404)
            actor, epoch = _principal(self.principal_resolver)
            body = _read_json(
                required=frozenset({"query"}),
                optional=frozenset({"cursor", "limit"}),
            )
            query = body["query"]
            if not isinstance(query, str) or not 1 <= len(query) <= 500:
                raise _RequestError("VALIDATION_ERROR", "query is invalid.", 400)
            limit = body.get("limit", 10)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
                raise _RequestError("VALIDATION_ERROR", "limit is invalid.", 400)
            cursor = body.get("cursor")
            if cursor is not None and (
                not isinstance(cursor, str) or len(cursor) > 4096
            ):
                raise _RequestError("VALIDATION_ERROR", "cursor is invalid.", 400)
            result = self.search_service.search(
                PersonalWikiManagementSearchRequest(
                    actor_user_id=actor,
                    actor_scope_epoch=epoch,
                    request_id=request_id,
                    query=query,
                    limit=limit,
                    cursor=cursor,
                )
            )
            return _json_response(
                {
                    "items": [
                        {
                            "item_id": item.item_id,
                            "provenance": list(item.provenance),
                            "revision_id": item.revision_id,
                            "score": item.score_ppm / 1_000_000,
                            "snippet": item.excerpt_md,
                            "source_space_id": item.source_space_id,
                            "title": item.title,
                            "wiki_display_name": item.wiki_display_name,
                        }
                        for item in result.items
                    ],
                    "next_cursor": result.next_cursor,
                },
                request_id,
            )

        return self.run(execute)

    def create(self):
        def execute(request_id: str):
            actor, epoch = _principal(self.principal_resolver)
            body = _read_json(
                required=frozenset(
                    {"display_name", "expected_row_version", "operation_id"}
                )
            )
            result = self.service.create(
                CreatePersonalWikiCommand(
                    actor_user_id=actor,
                    actor_scope_epoch=epoch,
                    operation_id=_operation_id(body),
                    request_id=request_id,
                    display_name=body["display_name"],
                    expected_row_version=_positive(
                        body["expected_row_version"], "expected_row_version"
                    ),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(_mutation_payload(result), request_id, 201)

        return self.run(execute)

    def rename(self, space_id: str):
        def execute(request_id: str):
            actor, epoch = _principal(self.principal_resolver)
            body = _read_json(
                required=frozenset(
                    {"display_name", "expected_row_version", "operation_id"}
                )
            )
            result = self.service.rename(
                RenamePersonalWikiCommand(
                    actor_user_id=actor,
                    actor_scope_epoch=epoch,
                    operation_id=_operation_id(body),
                    request_id=request_id,
                    space_id=_uuid(space_id, "space_id"),
                    display_name=body["display_name"],
                    expected_row_version=_positive(
                        body["expected_row_version"], "expected_row_version"
                    ),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(_mutation_payload(result), request_id)

        return self.run(execute)

    def set_default(self, space_id: str):
        def execute(request_id: str):
            actor, epoch = _principal(self.principal_resolver)
            body = _read_json(
                required=frozenset({"expected_row_version", "operation_id"})
            )
            result = self.service.set_default(
                SetDefaultPersonalWikiCommand(
                    actor_user_id=actor,
                    actor_scope_epoch=epoch,
                    operation_id=_operation_id(body),
                    request_id=request_id,
                    space_id=_uuid(space_id, "space_id"),
                    expected_row_version=_positive(
                        body["expected_row_version"], "expected_row_version"
                    ),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(_mutation_payload(result), request_id)

        return self.run(execute)

    def archive(self, space_id: str):
        def execute(request_id: str):
            actor, epoch = _principal(self.principal_resolver)
            body = _read_json(
                required=frozenset({"expected_row_version", "operation_id"}),
                optional=frozenset({"reason"}),
            )
            result = self.service.archive(
                ArchivePersonalWikiCommand(
                    actor_user_id=actor,
                    actor_scope_epoch=epoch,
                    operation_id=_operation_id(body),
                    request_id=request_id,
                    space_id=_uuid(space_id, "space_id"),
                    expected_row_version=_positive(
                        body["expected_row_version"], "expected_row_version"
                    ),
                    reason=body.get("reason"),
                    request_meta=_request_meta(),
                )
            )
            return _json_response(_mutation_payload(result), request_id)

        return self.run(execute)

    def restore(self, space_id: str):
        def execute(request_id: str):
            actor, epoch = _principal(self.principal_resolver)
            body = _read_json(
                required=frozenset({"expected_row_version", "operation_id"}),
                optional=frozenset({"make_default"}),
            )
            make_default = body.get("make_default", False)
            if not isinstance(make_default, bool):
                raise _RequestError("VALIDATION_ERROR", "make_default is invalid.", 400)
            result = self.service.restore(
                RestorePersonalWikiCommand(
                    actor_user_id=actor,
                    actor_scope_epoch=epoch,
                    operation_id=_operation_id(body),
                    request_id=request_id,
                    space_id=_uuid(space_id, "space_id"),
                    expected_row_version=_positive(
                        body["expected_row_version"], "expected_row_version"
                    ),
                    make_default=make_default,
                    request_meta=_request_meta(),
                )
            )
            return _json_response(_mutation_payload(result), request_id)

        return self.run(execute)


def _effective_methods(methods: tuple[str, ...] | set[str]) -> frozenset[str]:
    result = {str(method).upper() for method in methods}
    if "GET" in result:
        result.add("HEAD")
    result.add("OPTIONS")
    return frozenset(result)


def _specs(
    handlers: _Handlers, *, writes_enabled: bool, search_enabled: bool = False
) -> tuple[tuple[str, str, tuple[str, ...], Callable[..., object]], ...]:
    values: tuple[tuple[str, str, tuple[str, ...], Callable[..., object]], ...] = (
        (
            PERSONAL_WIKI_ROUTE_PREFIX,
            "knowledge_v2_personal_wikis_list",
            ("GET",),
            handlers.list_profiles,
        ),
    )
    if search_enabled:
        values += (
            (
                f"{PERSONAL_WIKI_ROUTE_PREFIX}/search",
                "knowledge_v2_personal_wikis_search",
                ("POST",),
                handlers.search,
            ),
        )
    if not writes_enabled:
        return values
    return values + (
        (
            PERSONAL_WIKI_ROUTE_PREFIX,
            "knowledge_v2_personal_wikis_create",
            ("POST",),
            handlers.create,
        ),
        (
            f"{PERSONAL_WIKI_ROUTE_PREFIX}/<space_id>",
            "knowledge_v2_personal_wiki_rename",
            ("PATCH",),
            handlers.rename,
        ),
        (
            f"{PERSONAL_WIKI_ROUTE_PREFIX}/<space_id>/default",
            "knowledge_v2_personal_wiki_set_default",
            ("PUT",),
            handlers.set_default,
        ),
        (
            f"{PERSONAL_WIKI_ROUTE_PREFIX}/<space_id>/archive",
            "knowledge_v2_personal_wiki_archive",
            ("POST",),
            handlers.archive,
        ),
        (
            f"{PERSONAL_WIKI_ROUTE_PREFIX}/<space_id>/restore",
            "knowledge_v2_personal_wiki_restore",
            ("POST",),
            handlers.restore,
        ),
    )


def _preflight_flask(
    target: Flask,
    specs: tuple[tuple[str, str, tuple[str, ...], Callable[..., object]], ...],
) -> None:
    if bool(getattr(target, "_got_first_request", False)):
        raise RuntimeError("personal-Wiki routes must register before the first request")
    existing_endpoints = {rule.endpoint for rule in target.url_map.iter_rules()}
    existing_rules = {
        (_VARIABLE_RULE_RE.sub("<>", rule.rule), method)
        for rule in target.url_map.iter_rules()
        for method in _effective_methods(set(rule.methods))
    }
    for rule, endpoint, methods, _ in specs:
        if endpoint in existing_endpoints or endpoint in target.view_functions:
            raise RuntimeError(f"personal-Wiki route endpoint already exists: {endpoint}")
        normalized = _VARIABLE_RULE_RE.sub("<>", rule)
        for method in _effective_methods(methods):
            if (normalized, method) in existing_rules:
                raise RuntimeError(f"personal-Wiki route already exists: {method} {rule}")


def _preflight_blueprint(
    target: Blueprint,
    specs: tuple[tuple[str, str, tuple[str, ...], Callable[..., object]], ...],
) -> None:
    if bool(getattr(target, "_got_registered_once", False)):
        raise RuntimeError("personal-Wiki routes must register before Blueprint attach")

    def verify(state: object) -> None:
        app = getattr(state, "app", None)
        if getattr(state, "url_prefix", None) or getattr(state, "subdomain", None):
            raise RuntimeError("personal-Wiki Blueprint routes require the root host")
        url_map = getattr(app, "url_map", None)
        if url_map is None:
            raise RuntimeError("personal-Wiki Blueprint cannot inspect its host")
        prefix = f"{getattr(state, 'name_prefix', '')}.{getattr(state, 'name', '')}".strip(".")
        existing_endpoints = {rule.endpoint for rule in url_map.iter_rules()}
        existing_rules = {
            (_VARIABLE_RULE_RE.sub("<>", rule.rule), method)
            for rule in url_map.iter_rules()
            for method in _effective_methods(set(rule.methods))
        }
        for rule, endpoint, methods, _ in specs:
            if f"{prefix}.{endpoint}" in existing_endpoints:
                raise RuntimeError(
                    f"personal-Wiki Blueprint endpoint already exists: {endpoint}"
                )
            normalized = _VARIABLE_RULE_RE.sub("<>", rule)
            for method in _effective_methods(methods):
                if (normalized, method) in existing_rules:
                    raise RuntimeError(
                        f"personal-Wiki Blueprint route already exists: {method} {rule}"
                    )

    target.record(verify)


def register_personal_wiki_routes(
    target: Flask | Blueprint,
    *,
    service: object,
    search_service: object | None = None,
    principal_resolver: Callable[[], object],
    writes_enabled: bool,
) -> tuple[str, ...]:
    """Register the bounded owner list and optionally its five lifecycle writes."""

    if not isinstance(target, (Flask, Blueprint)):
        raise TypeError("personal-Wiki route target must be a Flask app or Blueprint")
    if getattr(target, _REGISTERED_ATTRIBUTE, False):
        raise RuntimeError("personal-Wiki v2 routes are already registered")
    if not callable(principal_resolver):
        raise TypeError("principal_resolver must be callable")
    if not isinstance(writes_enabled, bool):
        raise TypeError("writes_enabled must be a Boolean")
    required = ["list_profiles"]
    if writes_enabled:
        required.extend(("archive", "create", "rename", "restore", "set_default"))
    for method in required:
        if not callable(getattr(service, method, None)):
            raise TypeError(f"personal_wiki_service is missing {method}().")
    if search_service is not None and not callable(getattr(search_service, "search", None)):
        raise TypeError("personal_wiki_search_service is missing search().")
    handlers = _Handlers(service, principal_resolver, search_service)
    specs = _specs(
        handlers,
        writes_enabled=writes_enabled,
        search_enabled=search_service is not None,
    )
    if isinstance(target, Flask):
        _preflight_flask(target, specs)
    else:
        _preflight_blueprint(target, specs)
    for rule, endpoint, methods, handler in specs:
        target.add_url_rule(
            rule,
            endpoint=endpoint,
            view_func=handler,
            methods=list(methods),
        )
    setattr(target, _REGISTERED_ATTRIBUTE, True)
    return tuple(rule for rule, _, _, _ in specs)


__all__ = [
    "MAX_PERSONAL_WIKI_JSON_BYTES",
    "PERSONAL_WIKI_ROUTE_PREFIX",
    "register_personal_wiki_routes",
]

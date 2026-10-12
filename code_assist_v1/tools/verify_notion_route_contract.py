"""Verify the frozen NP-1B Notion HTTP route surface.

The verifier registers routes on empty Flask applications and inspects only
their ``url_map``. It never sends a request or opens a Notion/user database.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
import importlib
import io
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence

from flask import Flask


SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
PHASE = "NP-1B"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "docs" / "notion_route_contract.json"
IGNORED_AUTOMATIC_METHODS = frozenset({"HEAD", "OPTIONS"})
ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
ROUTE_KEYS = frozenset({"methods", "path", "endpoint"})
REGISTRAR_KEYS = frozenset(
    {"id", "module", "callable", "allowed_path_prefixes", "routes"}
)
HARD_ROUTE_KEYS = frozenset({"id", "registrar", "method", "path", "endpoint"})
TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "contract_id",
        "phase",
        "method_policy",
        "registrars",
        "required_hard_routes",
    }
)
METHOD_POLICY_KEYS = frozenset({"allowed", "ignored_automatic"})
ENDPOINT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


@dataclass(frozen=True, order=True)
class RouteSurface:
    registrar: str
    methods: tuple[str, ...]
    path: str
    endpoint: str

    @property
    def key(self) -> tuple[str, tuple[str, ...], str]:
        return self.registrar, self.methods, self.path

    @property
    def method_path_keys(self) -> tuple[tuple[str, str, str], ...]:
        return tuple((self.registrar, method, self.path) for method in self.methods)

    def descriptor(self) -> str:
        return (
            f"{self.registrar} {','.join(self.methods)} {self.path} -> {self.endpoint}"
        )


@dataclass(frozen=True)
class RegistrarSpec:
    module: str
    callable_name: str
    allowed_path_prefixes: tuple[str, ...]


REGISTRAR_SPECS: dict[str, RegistrarSpec] = {
    "notion": RegistrarSpec(
        module="code_assist_v1.routes_notion",
        callable_name="register_notion_routes",
        allowed_path_prefixes=("/api/notion", "/public/notion"),
    ),
    "notion_bridge": RegistrarSpec(
        module="code_assist_v1.routes_notion_bridge",
        callable_name="register_notion_bridge_routes",
        allowed_path_prefixes=("/api/code/notion",),
    ),
}


def _hard(
    identifier: str,
    registrar: str,
    method: str,
    path: str,
    endpoint: str,
) -> RouteSurface:
    return RouteSurface(registrar, (method,), path, endpoint)


REQUIRED_HARD_ROUTES: dict[str, RouteSurface] = {
    "atomic_save": _hard(
        "atomic_save", "notion", "POST",
        "/api/notion/pages/<path:page_id>/save", "api_notion_page_save",
    ),
    "notion_chat_to_notion": _hard(
        "notion_chat_to_notion", "notion", "POST",
        "/api/notion/import_chat", "api_notion_import_chat",
    ),
    "notion_publish_preview": _hard(
        "notion_publish_preview", "notion", "GET",
        "/api/notion/pages/<path:page_id>/publish_preview",
        "api_notion_publish_preview",
    ),
    "notion_publish_second_brain": _hard(
        "notion_publish_second_brain", "notion", "POST",
        "/api/notion/pages/<path:page_id>/publish_second_brain",
        "api_notion_publish_second_brain",
    ),
    "notion_session_attach": _hard(
        "notion_session_attach", "notion", "POST",
        "/api/notion/pages/<path:page_id>/attach", "api_notion_page_attach",
    ),
    "notion_session_detach": _hard(
        "notion_session_detach", "notion", "DELETE",
        "/api/notion/session_pages/<path:page_id>",
        "api_notion_session_page_detach",
    ),
    "notion_session_list": _hard(
        "notion_session_list", "notion", "GET",
        "/api/notion/session_pages", "api_notion_session_pages",
    ),
    "notion_template_second_brain": _hard(
        "notion_template_second_brain", "notion", "POST",
        "/api/notion/templates/second_brain", "api_notion_second_brain_template",
    ),
    "bridge_chat_to_notion": _hard(
        "bridge_chat_to_notion", "notion_bridge", "POST",
        "/api/code/notion/save_chat_summary",
        "api_code_notion_save_chat_summary",
    ),
    "bridge_publish_second_brain": _hard(
        "bridge_publish_second_brain", "notion_bridge", "POST",
        "/api/code/notion/pages/<path:page_id>/publish_second_brain",
        "api_code_notion_publish",
    ),
    "bridge_session_attach": _hard(
        "bridge_session_attach", "notion_bridge", "POST",
        "/api/code/notion/session_attach", "api_code_notion_session_attach",
    ),
    "bridge_session_detach": _hard(
        "bridge_session_detach", "notion_bridge", "POST",
        "/api/code/notion/session_detach", "api_code_notion_session_detach",
    ),
    "bridge_session_list": _hard(
        "bridge_session_list", "notion_bridge", "GET",
        "/api/code/notion/session_pages", "api_code_notion_session_pages",
    ),
    "bridge_template_second_brain": _hard(
        "bridge_template_second_brain", "notion_bridge", "POST",
        "/api/code/notion/templates/second_brain",
        "api_code_notion_second_brain_template",
    ),
    "bridge_thought_to_notion": _hard(
        "bridge_thought_to_notion", "notion_bridge", "POST",
        "/api/code/notion/import_thought", "api_code_notion_import_thought",
    ),
}


class DuplicateJsonKeyError(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise DuplicateJsonKeyError(f"duplicate JSON object key: {key}")
        output[key] = value
    return output


def load_manifest(path: str | Path) -> dict[str, Any]:
    data = json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_json_keys,
    )
    if not isinstance(data, dict):
        raise ValueError("route contract root must be an object")
    return data


def _issue(
    errors: list[dict[str, str]],
    code: str,
    message: str,
    *,
    subject: str = "",
) -> None:
    item = {"code": code, "message": message}
    if subject:
        item["subject"] = subject
    errors.append(item)


def _unknown_keys(
    errors: list[dict[str, str]],
    item: Mapping[str, Any],
    allowed: frozenset[str],
    subject: str,
) -> None:
    unknown = sorted(set(item) - allowed)
    if unknown:
        _issue(
            errors,
            "UNKNOWN_MANIFEST_KEY",
            f"unknown keys: {', '.join(unknown)}",
            subject=subject,
        )


def _path_in_prefixes(path: str, prefixes: Iterable[str]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def _validate_path(
    errors: list[dict[str, str]],
    path: str,
    prefixes: tuple[str, ...],
    subject: str,
) -> None:
    unsafe = (
        not path.startswith("/")
        or "\\" in path
        or "?" in path
        or "#" in path
        or "//" in path
        or any(segment in {".", ".."} for segment in path.split("/"))
    )
    if unsafe or not _path_in_prefixes(path, prefixes):
        _issue(
            errors,
            "PHASE_PATH_ESCAPE",
            f"route path escapes the NP-1B registrar prefixes: {path!r}",
            subject=subject,
        )


def _route_from_manifest(
    errors: list[dict[str, str]],
    registrar_id: str,
    value: Any,
    index: int,
) -> RouteSurface | None:
    subject = f"registrars[{registrar_id}].routes[{index}]"
    if not isinstance(value, dict):
        _issue(errors, "ROUTE_INVALID", "route must be an object", subject=subject)
        return None
    _unknown_keys(errors, value, ROUTE_KEYS, subject)
    if set(value) != ROUTE_KEYS:
        missing = sorted(ROUTE_KEYS - set(value))
        if missing:
            _issue(
                errors,
                "ROUTE_KEY_MISSING",
                f"missing route keys: {', '.join(missing)}",
                subject=subject,
            )
        return None
    methods_value = value.get("methods")
    path = value.get("path")
    endpoint = value.get("endpoint")
    methods: tuple[str, ...] = ()
    if not isinstance(methods_value, list) or not methods_value:
        _issue(
            errors,
            "UNKNOWN_METHOD",
            "methods must be a non-empty array",
            subject=subject,
        )
    else:
        normalized = tuple(methods_value)
        if (
            any(not isinstance(method, str) or method not in ALLOWED_METHODS for method in normalized)
            or len(set(normalized)) != len(normalized)
            or normalized != tuple(sorted(normalized))
        ):
            _issue(
                errors,
                "UNKNOWN_METHOD",
                "methods must contain unique, sorted values from "
                + ", ".join(sorted(ALLOWED_METHODS)),
                subject=subject,
            )
        else:
            methods = normalized
    if not isinstance(path, str):
        _issue(errors, "ROUTE_PATH_INVALID", "path must be a string", subject=subject)
    else:
        _validate_path(
            errors,
            path,
            REGISTRAR_SPECS[registrar_id].allowed_path_prefixes,
            subject,
        )
    if not isinstance(endpoint, str) or not ENDPOINT_RE.fullmatch(endpoint):
        _issue(
            errors,
            "ENDPOINT_INVALID",
            f"invalid endpoint: {endpoint!r}",
            subject=subject,
        )
    if (
        not methods
        or not isinstance(path, str)
        or not isinstance(endpoint, str)
        or not ENDPOINT_RE.fullmatch(endpoint)
    ):
        return None
    return RouteSurface(registrar_id, methods, path, endpoint)


def _validate_method_policy(
    errors: list[dict[str, str]],
    value: Any,
) -> None:
    if not isinstance(value, dict):
        _issue(errors, "METHOD_POLICY_INVALID", "method_policy must be an object")
        return
    _unknown_keys(errors, value, METHOD_POLICY_KEYS, "method_policy")
    expected_allowed = sorted(ALLOWED_METHODS)
    expected_ignored = sorted(IGNORED_AUTOMATIC_METHODS)
    if value.get("allowed") != expected_allowed:
        _issue(
            errors,
            "METHOD_POLICY_INVALID",
            f"allowed must exactly equal {expected_allowed!r}",
            subject="method_policy.allowed",
        )
    if value.get("ignored_automatic") != expected_ignored:
        _issue(
            errors,
            "METHOD_POLICY_INVALID",
            f"ignored_automatic must exactly equal {expected_ignored!r}",
            subject="method_policy.ignored_automatic",
        )


def _validate_registrars(
    errors: list[dict[str, str]],
    value: Any,
) -> list[RouteSurface]:
    if not isinstance(value, list):
        _issue(errors, "REGISTRARS_INVALID", "registrars must be an array")
        return []
    routes: list[RouteSurface] = []
    seen_registrars: set[str] = set()
    seen_route_keys: set[tuple[str, str, str]] = set()
    for index, registrar in enumerate(value):
        subject = f"registrars[{index}]"
        if not isinstance(registrar, dict):
            _issue(errors, "REGISTRAR_INVALID", "registrar must be an object", subject=subject)
            continue
        _unknown_keys(errors, registrar, REGISTRAR_KEYS, subject)
        registrar_id = registrar.get("id")
        if not isinstance(registrar_id, str) or registrar_id not in REGISTRAR_SPECS:
            _issue(
                errors,
                "REGISTRAR_UNKNOWN",
                f"unknown registrar: {registrar_id!r}",
                subject=subject,
            )
            continue
        if registrar_id in seen_registrars:
            _issue(
                errors,
                "DUPLICATE_REGISTRAR",
                f"duplicate registrar: {registrar_id}",
                subject=subject,
            )
            continue
        seen_registrars.add(registrar_id)
        spec = REGISTRAR_SPECS[registrar_id]
        if registrar.get("module") != spec.module:
            _issue(
                errors,
                "REGISTRAR_TARGET_MISMATCH",
                f"module must be {spec.module}",
                subject=subject,
            )
        if registrar.get("callable") != spec.callable_name:
            _issue(
                errors,
                "REGISTRAR_TARGET_MISMATCH",
                f"callable must be {spec.callable_name}",
                subject=subject,
            )
        if registrar.get("allowed_path_prefixes") != list(spec.allowed_path_prefixes):
            _issue(
                errors,
                "REGISTRAR_PREFIX_MISMATCH",
                f"allowed_path_prefixes must equal {list(spec.allowed_path_prefixes)!r}",
                subject=subject,
            )
        route_values = registrar.get("routes")
        if not isinstance(route_values, list):
            _issue(errors, "ROUTES_INVALID", "routes must be an array", subject=subject)
            continue
        for route_index, route_value in enumerate(route_values):
            route = _route_from_manifest(errors, registrar_id, route_value, route_index)
            if route is None:
                continue
            overlapping_keys = sorted(set(route.method_path_keys) & seen_route_keys)
            if overlapping_keys:
                _registrar, method, path = overlapping_keys[0]
                _issue(
                    errors,
                    "DUPLICATE_ROUTE_KEY",
                    f"duplicate route key: {route.registrar} {method} {path}",
                    subject=route.descriptor(),
                )
                continue
            seen_route_keys.update(route.method_path_keys)
            routes.append(route)
    missing_registrars = sorted(set(REGISTRAR_SPECS) - seen_registrars)
    for registrar_id in missing_registrars:
        _issue(
            errors,
            "REGISTRAR_MISSING",
            f"required registrar is missing: {registrar_id}",
            subject=registrar_id,
        )
    return routes


def _validate_hard_routes(
    errors: list[dict[str, str]],
    value: Any,
    manifest_routes: Iterable[RouteSurface],
) -> None:
    if not isinstance(value, list):
        _issue(
            errors,
            "REQUIRED_HARD_ROUTES_INVALID",
            "required_hard_routes must be an array",
        )
        return
    manifest_routes = tuple(manifest_routes)
    seen_ids: set[str] = set()
    for index, item in enumerate(value):
        subject = f"required_hard_routes[{index}]"
        if not isinstance(item, dict):
            _issue(errors, "HARD_ROUTE_INVALID", "hard route must be an object", subject=subject)
            continue
        _unknown_keys(errors, item, HARD_ROUTE_KEYS, subject)
        identifier = item.get("id")
        if not isinstance(identifier, str) or identifier not in REQUIRED_HARD_ROUTES:
            _issue(
                errors,
                "HARD_ROUTE_UNKNOWN",
                f"unknown required hard route: {identifier!r}",
                subject=subject,
            )
            continue
        if identifier in seen_ids:
            _issue(
                errors,
                "DUPLICATE_HARD_ROUTE",
                f"duplicate required hard route: {identifier}",
                subject=subject,
            )
            continue
        seen_ids.add(identifier)
        expected = REQUIRED_HARD_ROUTES[identifier]
        actual_fields = (
            item.get("registrar"), item.get("method"), item.get("path"), item.get("endpoint")
        )
        expected_fields = (
            expected.registrar, expected.methods[0], expected.path, expected.endpoint
        )
        if actual_fields != expected_fields:
            _issue(
                errors,
                "HARD_ROUTE_CONTRACT_MISMATCH",
                f"hard route must equal {expected.descriptor()}",
                subject=identifier,
            )
        if not _contains_hard_route(manifest_routes, expected):
            _issue(
                errors,
                "HARD_ROUTE_NOT_IN_MANIFEST",
                f"hard route is absent from registrar routes: {expected.descriptor()}",
                subject=identifier,
            )
    for identifier in sorted(set(REQUIRED_HARD_ROUTES) - seen_ids):
        _issue(
            errors,
            "HARD_ROUTE_MISSING",
            f"required hard route is missing: {identifier}",
            subject=identifier,
        )


def _contains_hard_route(
    routes: Iterable[RouteSurface],
    hard_route: RouteSurface,
) -> bool:
    method = hard_route.methods[0]
    return any(
        route.registrar == hard_route.registrar
        and route.path == hard_route.path
        and route.endpoint == hard_route.endpoint
        and method in route.methods
        for route in routes
    )


def _load_registrar(spec: RegistrarSpec) -> Callable[[Flask], Any]:
    # Some legacy route dependencies announce their configuration at import
    # time. Keep the verifier's stdout a single deterministic JSON document.
    with redirect_stdout(io.StringIO()):
        module = importlib.import_module(spec.module)
    registrar = getattr(module, spec.callable_name, None)
    if not callable(registrar):
        raise RuntimeError(f"route registrar is not callable: {spec.module}.{spec.callable_name}")
    return registrar


def collect_actual_routes(
    registrar_overrides: Mapping[str, Callable[[Flask], Any]] | None = None,
) -> tuple[list[RouteSurface], list[dict[str, str]]]:
    """Register routes only and return their normalized Flask rule surface."""
    overrides = dict(registrar_overrides or {})
    routes: list[RouteSurface] = []
    errors: list[dict[str, str]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for registrar_id in sorted(REGISTRAR_SPECS):
        spec = REGISTRAR_SPECS[registrar_id]
        app = Flask(f"notion-route-contract-{registrar_id}", static_folder=None)
        registrar = overrides.get(registrar_id) or _load_registrar(spec)
        registrar(app)
        for rule in sorted(app.url_map.iter_rules(), key=lambda item: (str(item), item.endpoint)):
            path = str(rule)
            _validate_path(errors, path, spec.allowed_path_prefixes, registrar_id)
            methods = tuple(sorted(set(rule.methods or ()) - IGNORED_AUTOMATIC_METHODS))
            route = RouteSurface(registrar_id, methods, path, str(rule.endpoint))
            for method in methods:
                if method not in ALLOWED_METHODS:
                    _issue(
                        errors,
                        "ACTUAL_UNKNOWN_METHOD",
                        f"registered unknown method: {route.descriptor()}",
                        subject=route.descriptor(),
                    )
            overlapping_keys = sorted(set(route.method_path_keys) & seen_keys)
            if overlapping_keys:
                _registrar, method, duplicate_path = overlapping_keys[0]
                _issue(
                    errors,
                    "ACTUAL_DUPLICATE_ROUTE_KEY",
                    f"duplicate registered route key: {registrar_id} {method} {duplicate_path}",
                    subject=route.descriptor(),
                )
            seen_keys.update(route.method_path_keys)
            routes.append(route)
    return sorted(routes), errors


def validate_manifest_data(
    manifest: Any,
    *,
    actual_routes: Sequence[RouteSurface] | None = None,
    actual_errors: Sequence[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    if not isinstance(manifest, dict):
        _issue(errors, "MANIFEST_ROOT_INVALID", "manifest root must be an object")
        return _report(errors, [], []), 2
    _unknown_keys(errors, manifest, TOP_LEVEL_KEYS, "manifest")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        _issue(
            errors,
            "SCHEMA_VERSION_INVALID",
            f"schema_version must be {SCHEMA_VERSION}",
        )
    if manifest.get("contract_id") != "notion-route-contract-np1b":
        _issue(
            errors,
            "CONTRACT_ID_INVALID",
            "contract_id must be notion-route-contract-np1b",
        )
    if manifest.get("phase") != PHASE:
        _issue(errors, "PHASE_INVALID", f"phase must be {PHASE}")
    _validate_method_policy(errors, manifest.get("method_policy"))
    expected_routes = _validate_registrars(errors, manifest.get("registrars"))
    _validate_hard_routes(
        errors,
        manifest.get("required_hard_routes"),
        expected_routes,
    )

    if actual_routes is None:
        collected_routes, collected_errors = collect_actual_routes()
        actual_routes = collected_routes
        actual_errors = collected_errors
    else:
        actual_routes = sorted(actual_routes)
    errors.extend(dict(item) for item in (actual_errors or ()))

    expected_by_key = {route.key: route for route in expected_routes}
    actual_by_key = {route.key: route for route in actual_routes}
    for key in sorted(set(expected_by_key) - set(actual_by_key)):
        route = expected_by_key[key]
        _issue(
            errors,
            "MISSING_ACTUAL_ROUTE",
            f"manifest route is not registered: {route.descriptor()}",
            subject=route.descriptor(),
        )
    for key in sorted(set(actual_by_key) - set(expected_by_key)):
        route = actual_by_key[key]
        _issue(
            errors,
            "UNEXPECTED_ACTUAL_ROUTE",
            f"registered route is absent from manifest: {route.descriptor()}",
            subject=route.descriptor(),
        )
    for key in sorted(set(expected_by_key) & set(actual_by_key)):
        expected = expected_by_key[key]
        actual = actual_by_key[key]
        if expected.endpoint != actual.endpoint:
            _issue(
                errors,
                "ENDPOINT_MISMATCH",
                f"expected {expected.endpoint}, registered {actual.endpoint}",
                subject=(
                    f"{expected.registrar} {','.join(expected.methods)} {expected.path}"
                ),
            )

    for identifier, required in sorted(REQUIRED_HARD_ROUTES.items()):
        if not _contains_hard_route(actual_routes, required):
            _issue(
                errors,
                "HARD_ROUTE_ACTUAL_MISSING",
                f"required hard route is not registered: {required.descriptor()}",
                subject=identifier,
            )
    report = _report(errors, expected_routes, list(actual_routes))
    return report, 0 if not errors else 2


def _report(
    errors: Sequence[dict[str, str]],
    expected_routes: Sequence[RouteSurface],
    actual_routes: Sequence[RouteSurface],
) -> dict[str, Any]:
    ordered_errors = sorted(
        (dict(item) for item in errors),
        key=lambda item: (
            item.get("code", ""), item.get("subject", ""), item.get("message", "")
        ),
    )
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "contract_id": "notion-route-contract-np1b",
        "phase": PHASE,
        "status": "pass" if not ordered_errors else "fail",
        "summary": {
            "registrar_count": len(REGISTRAR_SPECS),
            "expected_route_count": len(expected_routes),
            "actual_route_count": len(actual_routes),
            "required_hard_route_count": len(REQUIRED_HARD_ROUTES),
            "error_count": len(ordered_errors),
        },
        "errors": ordered_errors,
    }


def verify_contract(
    manifest_path: str | Path = DEFAULT_MANIFEST,
) -> tuple[dict[str, Any], int]:
    try:
        manifest = load_manifest(manifest_path)
    except DuplicateJsonKeyError as exc:
        errors: list[dict[str, str]] = []
        _issue(errors, "DUPLICATE_JSON_KEY", str(exc))
        return _report(errors, [], []), 2
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors = []
        _issue(errors, "MANIFEST_LOAD_FAILED", str(exc))
        return _report(errors, [], []), 2
    try:
        return validate_manifest_data(manifest)
    except Exception as exc:
        errors = []
        _issue(errors, "ROUTE_COLLECTION_FAILED", str(exc))
        return _report(errors, [], []), 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the frozen NP-1B Notion Flask route contract"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Route contract JSON (default: checked-in NP-1B contract)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit compact deterministic JSON",
    )
    args = parser.parse_args(argv)
    report, exit_code = verify_contract(args.manifest)
    json.dump(
        report,
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
        indent=None if args.compact else 2,
        separators=(",", ":") if args.compact else None,
    )
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

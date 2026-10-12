"""Strict verifier for the NP-1C Notion route semantic characterization.

The manifest describes the current handler boundary; it is not a claim of
Notion product parity. Verification registers routes only through the frozen
NP-1B route-contract verifier and never sends requests or opens user data.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence

from code_assist_v1.tools.verify_notion_route_contract import (
    REQUIRED_HARD_ROUTES,
    load_manifest as load_route_contract,
    verify_contract,
)


SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "docs" / "notion_route_semantics.json"
ROUTE_CONTRACT_PATH = PROJECT_ROOT / "docs" / "notion_route_contract.json"
ROUTE_CONTRACT_ID = "notion-route-contract-np1b"
EXPECTED_RULE_COUNT = 59
EXPECTED_OPERATION_COUNT = 61

AUTH_CLASSES = ("authenticated", "public_form", "public_read")
TRANSPORTS = ("form", "json_object", "multipart", "none", "path", "query")
CONTENT_TYPES = (
    "application/json",
    "dynamic_file",
    "text/csv",
    "text/html",
    "text/markdown",
)
METHODS = ("DELETE", "GET", "PATCH", "POST", "PUT")
ERROR_CODES = (
    "FORBIDDEN",
    "atomic_save_unavailable",
    "authentication_required",
    "internal_error",
    "invalid_json_body",
    "invalid_request",
    "method_not_allowed",
    "not_found",
    "operation_id_conflict",
    "payload_too_large",
    "password_change_required",
    "permission_denied",
    "public_authentication_redirect",
    "public_internal_error",
    "public_invalid_request",
    "public_not_found",
    "public_permission_denied",
    "revision_conflict",
    "route_not_found",
    "source_snapshot_conflict",
)

EXPECTED_ERROR_PROFILES: dict[str, tuple[tuple[int, str, bool], ...]] = {
    "auth_required": ((401, "authentication_required", False),),
    "password_change": ((403, "password_change_required", False),),
    "request_security": ((403, "FORBIDDEN", True),),
    "json_body": ((400, "invalid_json_body", True),),
    "notion_standard": (
        (400, "invalid_request", True),
        (403, "permission_denied", True),
        (404, "not_found", True),
        (500, "internal_error", True),
    ),
    "atomic_save": (
        (409, "operation_id_conflict", True),
        (409, "revision_conflict", True),
        (503, "atomic_save_unavailable", True),
    ),
    "publication_conflict": ((409, "source_snapshot_conflict", True),),
    "payload_limit": ((413, "payload_too_large", True),),
    "public_page": (
        (404, "public_not_found", False),
        (500, "public_internal_error", False),
    ),
    "public_authentication": (
        (302, "public_authentication_redirect", False),
    ),
    "public_duplicate": (
        (400, "public_invalid_request", False),
        (403, "public_permission_denied", False),
        (404, "public_not_found", False),
        (500, "public_internal_error", False),
    ),
    "public_form": (
        (400, "public_invalid_request", False),
        (404, "public_not_found", False),
        (500, "public_internal_error", False),
    ),
    "notion_prefix_http": (
        (404, "route_not_found", True),
        (405, "method_not_allowed", True),
        (413, "payload_too_large", True),
    ),
}

EXPECTED_CHARACTERIZATION = {
    "claim": "current_handler_characterization_not_notion_parity",
    "known_boundaries": {
        "ai_explicit_page_context_failure": (
            "A requested page_id context failure is returned as an error; the handler does not silently use other pages."
        ),
        "ai_fallback_error_redaction": (
            "AI fallback_errors are limited to an empty list, AI provider unavailable, or AI provider temporarily unavailable; provider exceptions and filesystem paths are not exposed."
        ),
        "anonymous_public_form_scope": (
            "The route accepts path block_id values, while the application auth exemption currently covers only a single path segment."
        ),
        "best_effort_degradation_fields": (
            "Primary success payloads are preserved while degraded and sorted degraded_sources report inbox=outlook_notifications; search=page_content; overview=calendar_events,second_brain_sources,second_brain_thoughts,session_page_bindings; AI ask=related_page_content."
        ),
        "filename_os_error_redaction": (
            "Filename-bearing OS exceptions are classified as sanitized internal_error responses."
        ),
        "notion_prefix_http_errors": (
            "Once Flask error dispatch is reached, unknown routes, disallowed methods, and oversized requests under Notion API prefixes use scoped JSON errors; application auth or security checks may run first."
        ),
        "public_page_failure_text": (
            "Public page rendering failures return fixed generic text; they do not reflect exception details."
        ),
        "route_scoped_render_and_file_errors": (
            "Private/public file sending and public HTML rendering execute inside their route error boundaries."
        ),
    },
    "prefix_error_profile": "notion_prefix_http",
    "prefixes": ["/api/code/notion", "/api/notion"],
}

TOP_KEYS = frozenset(
    {
        "schema_version",
        "manifest_id",
        "status",
        "route_contract",
        "characterization",
        "vocabulary",
        "error_profiles",
        "required_hard_operations",
        "operations",
    }
)
CHARACTERIZATION_KEYS = frozenset(
    {"claim", "known_boundaries", "prefix_error_profile", "prefixes"}
)
ROUTE_CONTRACT_KEYS = frozenset({"contract_id", "path"})
VOCABULARY_KEYS = frozenset(
    {"auth_classes", "transports", "content_types", "methods", "error_codes"}
)
ERROR_PROFILE_KEYS = frozenset({"id", "errors"})
ERROR_KEYS = frozenset({"status", "code", "wire_code_field"})
HARD_KEYS = frozenset({"id", "operation_id"})
OPERATION_KEYS = frozenset(
    {
        "id",
        "registrar",
        "method",
        "path",
        "endpoint",
        "auth_class",
        "request",
        "success",
        "error_profiles",
    }
)
REQUEST_KEYS = frozenset(
    {
        "transport",
        "required_fields",
        "required_any_of",
        "required_query_fields",
        "path_fields",
    }
)
SUCCESS_KEYS = frozenset({"statuses", "content_types", "required_keys"})
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,159}$")
FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
ENDPOINT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
PATH_PARAMETER_RE = re.compile(r"<(?:(?:path|string|int|float|uuid):)?([A-Za-z_][A-Za-z0-9_]*)>")


class DuplicateJsonKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise DuplicateJsonKeyError(f"duplicate JSON object key: {key}")
        output[key] = value
    return output


def load_manifest(path: str | Path) -> dict[str, Any]:
    value = json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    if not isinstance(value, dict):
        raise ValueError("semantic manifest root must be an object")
    return value


def _add(
    errors: list[dict[str, str]],
    code: str,
    message: str,
    subject: str = "",
) -> None:
    error = {"code": code, "message": message}
    if subject:
        error["subject"] = subject
    errors.append(error)


def _unknown(
    errors: list[dict[str, str]],
    value: Mapping[str, Any],
    allowed: frozenset[str],
    subject: str,
) -> None:
    keys = sorted(set(value) - allowed)
    if keys:
        _add(
            errors,
            "UNKNOWN_KEY",
            f"unknown keys: {', '.join(keys)}",
            subject,
        )


def _exact_keys(
    errors: list[dict[str, str]],
    value: Mapping[str, Any],
    allowed: frozenset[str],
    subject: str,
) -> bool:
    _unknown(errors, value, allowed, subject)
    missing = sorted(allowed - set(value))
    if missing:
        _add(errors, "MISSING_KEY", f"missing keys: {', '.join(missing)}", subject)
    return not missing


def _unique_strings(
    errors: list[dict[str, str]],
    value: Any,
    subject: str,
    *,
    allowed: Iterable[str] | None = None,
) -> tuple[str, ...] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _add(errors, "STRING_LIST_INVALID", "must be an array of strings", subject)
        return None
    items = tuple(value)
    if len(set(items)) != len(items):
        _add(errors, "DUPLICATE_LIST_VALUE", "list values must be unique", subject)
    if items != tuple(sorted(items)):
        _add(errors, "LIST_ORDER_INVALID", "list values must be sorted", subject)
    if allowed is not None:
        unknown = sorted(set(items) - set(allowed))
        if unknown:
            _add(errors, "ENUM_VALUE_UNKNOWN", f"unknown values: {', '.join(unknown)}", subject)
    return items


def _validate_vocabulary(errors: list[dict[str, str]], value: Any) -> None:
    if not isinstance(value, dict):
        _add(errors, "VOCABULARY_INVALID", "vocabulary must be an object")
        return
    _exact_keys(errors, value, VOCABULARY_KEYS, "vocabulary")
    expected = {
        "auth_classes": list(AUTH_CLASSES),
        "transports": list(TRANSPORTS),
        "content_types": list(CONTENT_TYPES),
        "methods": list(METHODS),
        "error_codes": list(ERROR_CODES),
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            _add(
                errors,
                "VOCABULARY_MISMATCH",
                f"must exactly equal {expected_value!r}",
                f"vocabulary.{key}",
            )


def _validate_characterization(errors: list[dict[str, str]], value: Any) -> None:
    if not isinstance(value, dict):
        _add(errors, "CHARACTERIZATION_INVALID", "characterization must be an object")
        return
    _exact_keys(errors, value, CHARACTERIZATION_KEYS, "characterization")
    if value != EXPECTED_CHARACTERIZATION:
        _add(
            errors,
            "CHARACTERIZATION_MISMATCH",
            "characterization must exactly describe the reviewed current boundaries",
            "characterization",
        )


def _validate_error_profiles(
    errors: list[dict[str, str]],
    value: Any,
) -> set[str]:
    if not isinstance(value, list):
        _add(errors, "ERROR_PROFILES_INVALID", "error_profiles must be an array")
        return set()
    seen: set[str] = set()
    actual: dict[str, tuple[tuple[int, str, bool], ...]] = {}
    for index, profile in enumerate(value):
        subject = f"error_profiles[{index}]"
        if not isinstance(profile, dict):
            _add(errors, "ERROR_PROFILE_INVALID", "profile must be an object", subject)
            continue
        if not _exact_keys(errors, profile, ERROR_PROFILE_KEYS, subject):
            continue
        profile_id = profile.get("id")
        if not isinstance(profile_id, str) or profile_id not in EXPECTED_ERROR_PROFILES:
            _add(errors, "ERROR_PROFILE_UNKNOWN", f"unknown profile: {profile_id!r}", subject)
            continue
        if profile_id in seen:
            _add(errors, "DUPLICATE_ERROR_PROFILE", f"duplicate profile: {profile_id}", subject)
            continue
        seen.add(profile_id)
        entries = profile.get("errors")
        if not isinstance(entries, list):
            _add(errors, "ERROR_LIST_INVALID", "errors must be an array", subject)
            continue
        normalized: list[tuple[int, str, bool]] = []
        for error_index, entry in enumerate(entries):
            entry_subject = f"{subject}.errors[{error_index}]"
            if not isinstance(entry, dict):
                _add(errors, "ERROR_ENTRY_INVALID", "error must be an object", entry_subject)
                continue
            if not _exact_keys(errors, entry, ERROR_KEYS, entry_subject):
                continue
            status = entry.get("status")
            code = entry.get("code")
            wire_code_field = entry.get("wire_code_field")
            if not isinstance(status, int) or isinstance(status, bool) or not 300 <= status <= 599:
                _add(errors, "ERROR_STATUS_INVALID", f"invalid status: {status!r}", entry_subject)
                continue
            if not isinstance(code, str) or code not in ERROR_CODES:
                _add(errors, "ERROR_CODE_UNKNOWN", f"unknown error code: {code!r}", entry_subject)
                continue
            if not isinstance(wire_code_field, bool):
                _add(errors, "WIRE_CODE_FLAG_INVALID", "wire_code_field must be boolean", entry_subject)
                continue
            normalized.append((status, code, wire_code_field))
        if len(set(normalized)) != len(normalized):
            _add(errors, "DUPLICATE_ERROR_ENTRY", "error entries must be unique", subject)
        actual[profile_id] = tuple(normalized)
    for profile_id in sorted(set(EXPECTED_ERROR_PROFILES) - seen):
        _add(errors, "ERROR_PROFILE_MISSING", f"missing profile: {profile_id}", profile_id)
    for profile_id, expected in EXPECTED_ERROR_PROFILES.items():
        if profile_id in actual and actual[profile_id] != expected:
            _add(
                errors,
                "ERROR_PROFILE_MISMATCH",
                f"must exactly equal {expected!r}",
                profile_id,
            )
    return seen


def _contract_operations(
    errors: list[dict[str, str]],
    contract: Any,
) -> tuple[dict[tuple[str, str, str], str], int]:
    expected: dict[tuple[str, str, str], str] = {}
    rule_count = 0
    if not isinstance(contract, dict):
        _add(errors, "ROUTE_CONTRACT_INVALID", "route contract must be an object")
        return expected, rule_count
    registrars = contract.get("registrars")
    if not isinstance(registrars, list):
        _add(errors, "ROUTE_CONTRACT_INVALID", "route contract registrars must be an array")
        return expected, rule_count
    for registrar in registrars:
        if not isinstance(registrar, dict):
            continue
        registrar_id = registrar.get("id")
        routes = registrar.get("routes")
        if not isinstance(registrar_id, str) or not isinstance(routes, list):
            continue
        for route in routes:
            if not isinstance(route, dict):
                continue
            path = route.get("path")
            endpoint = route.get("endpoint")
            methods = route.get("methods")
            if not isinstance(path, str) or not isinstance(endpoint, str) or not isinstance(methods, list):
                continue
            rule_count += 1
            for method in methods:
                if isinstance(method, str):
                    key = (registrar_id, method, path)
                    if key in expected:
                        _add(errors, "ROUTE_CONTRACT_DUPLICATE", f"duplicate operation: {key!r}")
                    expected[key] = endpoint
    if rule_count != EXPECTED_RULE_COUNT:
        _add(
            errors,
            "ROUTE_CONTRACT_RULE_COUNT",
            f"expected {EXPECTED_RULE_COUNT} rules, found {rule_count}",
        )
    if len(expected) != EXPECTED_OPERATION_COUNT:
        _add(
            errors,
            "ROUTE_CONTRACT_OPERATION_COUNT",
            f"expected {EXPECTED_OPERATION_COUNT} operations, found {len(expected)}",
        )
    return expected, rule_count


def _path_safe(registrar: str, path: str) -> bool:
    prefixes = {
        "notion": ("/api/notion", "/public/notion"),
        "notion_bridge": ("/api/code/notion",),
    }.get(registrar, ())
    unsafe = (
        not path.startswith("/")
        or "\\" in path
        or "?" in path
        or "#" in path
        or "//" in path
        or any(part in {".", ".."} for part in path.split("/"))
    )
    return not unsafe and any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def _validate_request(
    errors: list[dict[str, str]],
    value: Any,
    *,
    path: str,
    subject: str,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        _add(errors, "REQUEST_INVALID", "request must be an object", subject)
        return None
    if not _exact_keys(errors, value, REQUEST_KEYS, subject):
        return None
    transport = value.get("transport")
    if transport not in TRANSPORTS:
        _add(errors, "TRANSPORT_UNKNOWN", f"unknown transport: {transport!r}", subject)
    fields = _unique_strings(errors, value.get("required_fields"), f"{subject}.required_fields")
    query_fields = _unique_strings(
        errors, value.get("required_query_fields"), f"{subject}.required_query_fields"
    )
    path_fields = _unique_strings(errors, value.get("path_fields"), f"{subject}.path_fields")
    any_of_value = value.get("required_any_of")
    any_of: list[tuple[str, ...]] = []
    if not isinstance(any_of_value, list):
        _add(errors, "REQUIRED_ANY_OF_INVALID", "required_any_of must be an array", subject)
    else:
        for index, group in enumerate(any_of_value):
            normalized = _unique_strings(errors, group, f"{subject}.required_any_of[{index}]")
            if not normalized:
                _add(errors, "REQUIRED_ANY_OF_INVALID", "groups must be non-empty", subject)
            else:
                any_of.append(normalized)
        if len(set(any_of)) != len(any_of):
            _add(errors, "DUPLICATE_REQUIRED_ANY_OF", "required_any_of groups must be unique", subject)
    for field_list, field_subject in (
        (fields, "required_fields"),
        (query_fields, "required_query_fields"),
        (path_fields, "path_fields"),
    ):
        if field_list is not None:
            for field in field_list:
                if not FIELD_RE.fullmatch(field):
                    _add(errors, "FIELD_NAME_INVALID", f"invalid field: {field!r}", f"{subject}.{field_subject}")
    actual_path_fields = tuple(sorted(PATH_PARAMETER_RE.findall(path)))
    if path_fields is not None and path_fields != actual_path_fields:
        _add(
            errors,
            "PATH_FIELDS_MISMATCH",
            f"must exactly equal path parameters {actual_path_fields!r}",
            subject,
        )
    if transport in {"none", "path", "query"} and (fields or any_of):
        _add(errors, "TRANSPORT_FIELD_MISMATCH", "body fields require a body transport", subject)
    if transport != "query" and query_fields:
        _add(errors, "TRANSPORT_QUERY_MISMATCH", "query fields require query transport", subject)
    return value


def _validate_success(
    errors: list[dict[str, str]],
    value: Any,
    subject: str,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        _add(errors, "SUCCESS_INVALID", "success must be an object", subject)
        return None
    if not _exact_keys(errors, value, SUCCESS_KEYS, subject):
        return None
    statuses = value.get("statuses")
    if (
        not isinstance(statuses, list)
        or not statuses
        or any(not isinstance(status, int) or isinstance(status, bool) or not 200 <= status <= 399 for status in statuses)
    ):
        _add(errors, "SUCCESS_STATUS_INVALID", "statuses must contain HTTP 2xx/3xx integers", subject)
    elif statuses != sorted(set(statuses)):
        _add(errors, "SUCCESS_STATUS_INVALID", "statuses must be unique and sorted", subject)
    content_types = _unique_strings(
        errors,
        value.get("content_types"),
        f"{subject}.content_types",
        allowed=CONTENT_TYPES,
    )
    if content_types is not None and not content_types:
        _add(errors, "CONTENT_TYPES_EMPTY", "content_types must not be empty", subject)
    required_keys = _unique_strings(
        errors, value.get("required_keys"), f"{subject}.required_keys"
    )
    if required_keys is not None:
        for key in required_keys:
            if not FIELD_RE.fullmatch(key):
                _add(errors, "RESPONSE_KEY_INVALID", f"invalid response key: {key!r}", subject)
    if content_types and "application/json" not in content_types and required_keys:
        _add(
            errors,
            "NON_JSON_RESPONSE_KEYS",
            "non-JSON success cannot declare top-level response keys",
            subject,
        )
    return value


def _operation_id(registrar: str, endpoint: str, method: str) -> str:
    return f"{registrar}.{endpoint}.{method.lower()}"


def _hard_expectations() -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for hard_id, route in REQUIRED_HARD_ROUTES.items():
        method = route.methods[0]
        output[hard_id] = {
            "operation_id": _operation_id(route.registrar, route.endpoint, method),
            "key": (route.registrar, method, route.path),
            "auth_class": "authenticated",
        }
    output["atomic_save"].update(
        transport="json_object",
        required_fields=("base_revision", "blocks", "operation_id"),
        success_keys=("blocks", "files", "page", "revision", "status"),
        profiles={"atomic_save", "auth_required", "json_body", "notion_standard", "password_change", "request_security"},
    )
    for hard_id in ("notion_template_second_brain", "bridge_template_second_brain"):
        output[hard_id].update(
            transport="none",
            required_fields=(),
            success_keys=("home_page_id", "pages", "status"),
            profiles={"auth_required", "notion_standard", "password_change", "request_security"},
        )
    output["notion_publish_preview"].update(
        transport="path",
        required_fields=(),
        success_keys=("file_count", "image_count", "markdown", "page", "source_snapshot"),
        profiles={"auth_required", "notion_standard", "password_change"},
    )
    for hard_id in ("notion_publish_second_brain", "bridge_publish_second_brain"):
        output[hard_id].update(
            transport="json_object",
            required_fields=(),
            success_keys=(
                "analysis_error", "analysis_model_kind", "analysis_model_used",
                "asset_count", "assets", "auto_triage", "candidate_count",
                "candidates", "conversion_error", "conversion_fallback_errors",
                "conversion_model_kind", "conversion_model_used", "converted_to_wiki",
                "extractor", "graph_edges_added", "image_count", "images",
                "markdown_chars", "pending_candidate_count", "raw_chunk_count",
                "raw_path", "source_id", "source_metadata", "source_type", "status",
                "warnings", "wiki_path",
            ),
            profiles={
                "auth_required", "json_body", "notion_standard",
                "password_change", "publication_conflict", "request_security",
            },
        )
    output["notion_session_attach"].update(
        transport="json_object", required_fields=("session_id",),
        success_keys=("owner_user_id", "page_id", "session_id", "status", "title"),
        profiles={"auth_required", "json_body", "notion_standard", "password_change", "request_security"},
    )
    for hard_id in ("bridge_session_attach", "bridge_session_detach"):
        output[hard_id].update(
            transport="json_object",
            required_fields=("page_id", "session_id") if hard_id == "bridge_session_attach" else ("session_id",),
            success_keys=("owner_user_id", "page_id", "session_id", "status", "title")
            if hard_id == "bridge_session_attach"
            else ("detached", "page_id", "session_id", "status"),
            profiles={"auth_required", "json_body", "notion_standard", "password_change", "request_security"},
        )
    output["notion_session_detach"].update(
        transport="query", required_fields=(), required_query_fields=("session_id",),
        success_keys=("detached", "page_id", "session_id", "status"),
        profiles={"auth_required", "notion_standard", "password_change", "request_security"},
    )
    for hard_id in ("notion_session_list", "bridge_session_list"):
        output[hard_id].update(
            transport="query", required_fields=(), required_query_fields=(),
            success_keys=("count", "items"),
            profiles={"auth_required", "notion_standard", "password_change"},
        )
    output["notion_chat_to_notion"].update(
        transport="json_object", required_fields=("messages",),
        success_keys=("blocks", "capabilities", "files", "page", "status"),
        profiles={"auth_required", "json_body", "notion_standard", "password_change", "request_security"},
    )
    output["bridge_chat_to_notion"].update(
        transport="json_object", required_fields=("messages",),
        success_keys=(
            "blocks", "capabilities", "fallback_errors", "files", "model_kind",
            "model_used", "page", "save_mode", "status",
        ),
        profiles={"auth_required", "json_body", "notion_standard", "password_change", "request_security"},
    )
    output["bridge_thought_to_notion"].update(
        transport="json_object", required_fields=(), required_any_of=(("content", "thought_id"),),
        success_keys=("blocks", "capabilities", "files", "page", "status"),
        profiles={"auth_required", "json_body", "notion_standard", "password_change", "request_security"},
    )
    return output


HARD_EXPECTATIONS = _hard_expectations()


def _validate_operations(
    errors: list[dict[str, str]],
    value: Any,
    expected_contract: Mapping[tuple[str, str, str], str],
    known_profiles: set[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not isinstance(value, list):
        _add(errors, "OPERATIONS_INVALID", "operations must be an array")
        return [], {}
    normalized: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for index, operation in enumerate(value):
        subject = f"operations[{index}]"
        if not isinstance(operation, dict):
            _add(errors, "OPERATION_INVALID", "operation must be an object", subject)
            continue
        if not _exact_keys(errors, operation, OPERATION_KEYS, subject):
            continue
        operation_id = operation.get("id")
        registrar = operation.get("registrar")
        method = operation.get("method")
        path = operation.get("path")
        endpoint = operation.get("endpoint")
        auth_class = operation.get("auth_class")
        if not isinstance(operation_id, str) or not IDENTIFIER_RE.fullmatch(operation_id):
            _add(errors, "OPERATION_ID_INVALID", f"invalid id: {operation_id!r}", subject)
        elif operation_id in by_id:
            _add(errors, "DUPLICATE_OPERATION_ID", f"duplicate id: {operation_id}", subject)
        if registrar not in {"notion", "notion_bridge"}:
            _add(errors, "REGISTRAR_UNKNOWN", f"unknown registrar: {registrar!r}", subject)
        if method not in METHODS:
            _add(errors, "METHOD_UNKNOWN", f"unknown method: {method!r}", subject)
        if not isinstance(path, str) or not _path_safe(str(registrar), path):
            _add(errors, "PHASE_PATH_ESCAPE", f"unsafe or out-of-scope path: {path!r}", subject)
        if not isinstance(endpoint, str) or not ENDPOINT_RE.fullmatch(endpoint):
            _add(errors, "ENDPOINT_INVALID", f"invalid endpoint: {endpoint!r}", subject)
        if auth_class not in AUTH_CLASSES:
            _add(errors, "AUTH_CLASS_UNKNOWN", f"unknown auth class: {auth_class!r}", subject)
        request_value = _validate_request(
            errors, operation.get("request"), path=path if isinstance(path, str) else "", subject=f"{subject}.request"
        )
        _validate_success(errors, operation.get("success"), f"{subject}.success")
        profiles = _unique_strings(
            errors,
            operation.get("error_profiles"),
            f"{subject}.error_profiles",
            allowed=known_profiles,
        )
        if profiles is not None:
            profile_set = set(profiles)
            if auth_class == "authenticated" and path.startswith("/api/"):
                if "auth_required" not in profile_set:
                    _add(errors, "AUTH_ERROR_PROFILE_MISSING", "authenticated API route needs auth_required", subject)
                if "password_change" not in profile_set:
                    _add(errors, "AUTH_ERROR_PROFILE_MISSING", "authenticated API route needs password_change", subject)
            if (
                auth_class == "authenticated"
                and path.startswith("/public/")
                and "public_authentication" not in profile_set
            ):
                _add(errors, "AUTH_ERROR_PROFILE_MISSING", "authenticated public route needs redirect auth profile", subject)
            if auth_class == "authenticated" and method in {"DELETE", "PATCH", "POST", "PUT"} and "request_security" not in profile_set:
                _add(errors, "AUTH_ERROR_PROFILE_MISSING", "unsafe route needs request_security", subject)
            if (
                auth_class == "authenticated"
                and method in {"DELETE", "PATCH", "POST", "PUT"}
                and request_value
                and request_value.get("transport") == "json_object"
                and "json_body" not in profile_set
            ):
                _add(errors, "INVALID_JSON_PROFILE_MISSING", "unsafe JSON route needs json_body", subject)
        if all(
            (
                isinstance(registrar, str), isinstance(method, str),
                isinstance(path, str), isinstance(endpoint, str),
            )
        ):
            key = (registrar, method, path)
            if key in by_key:
                _add(errors, "DUPLICATE_OPERATION_KEY", f"duplicate operation: {key!r}", subject)
            else:
                by_key[key] = operation
            expected_endpoint = expected_contract.get(key)
            if expected_endpoint is None:
                _add(errors, "UNEXPECTED_OPERATION", f"operation absent from route contract: {key!r}", subject)
            elif endpoint != expected_endpoint:
                _add(
                    errors,
                    "ENDPOINT_MISMATCH",
                    f"expected endpoint {expected_endpoint}, found {endpoint}",
                    subject,
                )
            expected_id = _operation_id(registrar, endpoint, method)
            if operation_id != expected_id:
                _add(errors, "OPERATION_ID_MISMATCH", f"id must be {expected_id}", subject)
        if isinstance(operation_id, str) and operation_id not in by_id:
            by_id[operation_id] = operation
        normalized.append(operation)
    for key in sorted(set(expected_contract) - set(by_key)):
        _add(errors, "MISSING_OPERATION", f"missing route operation: {key!r}")
    if len(normalized) != EXPECTED_OPERATION_COUNT:
        _add(
            errors,
            "OPERATION_COUNT_INVALID",
            f"expected {EXPECTED_OPERATION_COUNT} operation records, found {len(normalized)}",
        )
    groups: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for operation in normalized:
        registrar = operation.get("registrar")
        path = operation.get("path")
        endpoint = operation.get("endpoint")
        method = operation.get("method")
        if all(isinstance(item, str) for item in (registrar, path, endpoint, method)):
            groups[(registrar, path, endpoint)].add(method)
    if len(groups) != EXPECTED_RULE_COUNT:
        _add(
            errors,
            "SEMANTIC_RULE_COUNT_INVALID",
            f"expected {EXPECTED_RULE_COUNT} grouped rules, found {len(groups)}",
        )
    return normalized, by_id


def _validate_hard_operations(
    errors: list[dict[str, str]],
    value: Any,
    by_id: Mapping[str, dict[str, Any]],
) -> None:
    if not isinstance(value, list):
        _add(errors, "HARD_OPERATIONS_INVALID", "required_hard_operations must be an array")
        return
    seen: set[str] = set()
    for index, item in enumerate(value):
        subject = f"required_hard_operations[{index}]"
        if not isinstance(item, dict):
            _add(errors, "HARD_OPERATION_INVALID", "entry must be an object", subject)
            continue
        if not _exact_keys(errors, item, HARD_KEYS, subject):
            continue
        hard_id = item.get("id")
        operation_id = item.get("operation_id")
        expected = HARD_EXPECTATIONS.get(hard_id) if isinstance(hard_id, str) else None
        if expected is None:
            _add(errors, "HARD_OPERATION_UNKNOWN", f"unknown hard id: {hard_id!r}", subject)
            continue
        if hard_id in seen:
            _add(errors, "DUPLICATE_HARD_OPERATION", f"duplicate hard id: {hard_id}", subject)
            continue
        seen.add(hard_id)
        if operation_id != expected["operation_id"]:
            _add(
                errors,
                "HARD_OPERATION_ID_MISMATCH",
                f"operation_id must be {expected['operation_id']}",
                subject,
            )
        operation = by_id.get(str(operation_id))
        if operation is None:
            _add(errors, "HARD_OPERATION_MISSING", "hard operation record is absent", subject)
            continue
        key = (operation.get("registrar"), operation.get("method"), operation.get("path"))
        if key != expected["key"]:
            _add(errors, "HARD_ROUTE_MISMATCH", f"route must equal {expected['key']!r}", subject)
        if operation.get("auth_class") != expected["auth_class"]:
            _add(errors, "HARD_AUTH_MISMATCH", "hard route auth_class changed", subject)
        request_value = operation.get("request") if isinstance(operation.get("request"), dict) else {}
        success_value = operation.get("success") if isinstance(operation.get("success"), dict) else {}
        if request_value.get("transport") != expected.get("transport"):
            _add(errors, "HARD_TRANSPORT_MISMATCH", "hard route transport changed", subject)
        if tuple(request_value.get("required_fields") or ()) != expected.get("required_fields", ()):
            _add(errors, "HARD_REQUIRED_FIELDS_MISMATCH", "hard route required fields changed", subject)
        if "required_query_fields" in expected and tuple(request_value.get("required_query_fields") or ()) != expected["required_query_fields"]:
            _add(errors, "HARD_QUERY_FIELDS_MISMATCH", "hard route query fields changed", subject)
        if "required_any_of" in expected:
            actual_any = tuple(tuple(group) for group in (request_value.get("required_any_of") or ()))
            if actual_any != expected["required_any_of"]:
                _add(errors, "HARD_REQUIRED_ANY_OF_MISMATCH", "hard route alternatives changed", subject)
        if tuple(success_value.get("required_keys") or ()) != expected.get("success_keys", ()):
            _add(errors, "HARD_SUCCESS_KEYS_MISMATCH", "hard route success keys changed", subject)
        if set(operation.get("error_profiles") or ()) != expected.get("profiles", set()):
            _add(errors, "HARD_ERROR_PROFILES_MISMATCH", "hard route errors changed", subject)
    for hard_id in sorted(set(HARD_EXPECTATIONS) - seen):
        _add(errors, "HARD_OPERATION_MISSING", f"missing hard operation: {hard_id}", hard_id)


def _report(
    errors: Sequence[dict[str, str]],
    *,
    operation_count: int = 0,
    rule_count: int = 0,
) -> dict[str, Any]:
    ordered = sorted(
        (dict(item) for item in errors),
        key=lambda item: (item.get("code", ""), item.get("subject", ""), item.get("message", "")),
    )
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "manifest_id": "notion-route-semantics-np1c-v2",
        "status": "pass" if not ordered else "fail",
        "summary": {
            "rule_count": rule_count,
            "operation_count": operation_count,
            "hard_operation_count": len(HARD_EXPECTATIONS),
            "error_count": len(ordered),
        },
        "errors": ordered,
    }


def validate_manifest_data(
    manifest: Any,
    *,
    route_contract: dict[str, Any] | None = None,
    verify_route_surface: bool = True,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    if not isinstance(manifest, dict):
        _add(errors, "MANIFEST_ROOT_INVALID", "manifest root must be an object")
        return _report(errors), 2
    _exact_keys(errors, manifest, TOP_KEYS, "manifest")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        _add(errors, "SCHEMA_VERSION_INVALID", f"schema_version must be {SCHEMA_VERSION}")
    if manifest.get("manifest_id") != "notion-route-semantics-np1c-v2":
        _add(errors, "MANIFEST_ID_INVALID", "unexpected manifest_id")
    if manifest.get("status") != "characterization_manifest":
        _add(errors, "STATUS_INVALID", "status must be characterization_manifest")
    _validate_characterization(errors, manifest.get("characterization"))
    contract_ref = manifest.get("route_contract")
    if not isinstance(contract_ref, dict):
        _add(errors, "ROUTE_CONTRACT_REF_INVALID", "route_contract must be an object")
    else:
        _exact_keys(errors, contract_ref, ROUTE_CONTRACT_KEYS, "route_contract")
        if contract_ref.get("contract_id") != ROUTE_CONTRACT_ID:
            _add(errors, "ROUTE_CONTRACT_REF_INVALID", "contract_id mismatch")
        if contract_ref.get("path") != "docs/notion_route_contract.json":
            _add(errors, "ROUTE_CONTRACT_REF_INVALID", "route contract path mismatch")
    _validate_vocabulary(errors, manifest.get("vocabulary"))
    known_profiles = _validate_error_profiles(errors, manifest.get("error_profiles"))
    if route_contract is None:
        try:
            route_contract = load_route_contract(ROUTE_CONTRACT_PATH)
        except Exception as exc:
            _add(errors, "ROUTE_CONTRACT_LOAD_FAILED", str(exc))
            route_contract = {}
    if verify_route_surface:
        surface_report, surface_exit = verify_contract(ROUTE_CONTRACT_PATH)
        if surface_exit != 0:
            _add(
                errors,
                "ROUTE_CONTRACT_SURFACE_FAILED",
                json.dumps(surface_report.get("errors", []), sort_keys=True),
            )
    expected_contract, rule_count = _contract_operations(errors, route_contract)
    operations, by_id = _validate_operations(
        errors, manifest.get("operations"), expected_contract, known_profiles
    )
    _validate_hard_operations(errors, manifest.get("required_hard_operations"), by_id)
    report = _report(errors, operation_count=len(operations), rule_count=rule_count)
    return report, 0 if not errors else 2


def verify_manifest(path: str | Path = DEFAULT_MANIFEST) -> tuple[dict[str, Any], int]:
    try:
        manifest = load_manifest(path)
    except DuplicateJsonKeyError as exc:
        errors: list[dict[str, str]] = []
        _add(errors, "DUPLICATE_JSON_KEY", str(exc))
        return _report(errors), 2
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors = []
        _add(errors, "MANIFEST_LOAD_FAILED", str(exc))
        return _report(errors), 2
    try:
        return validate_manifest_data(manifest)
    except Exception as exc:
        errors = []
        _add(errors, "SEMANTIC_VERIFICATION_FAILED", str(exc))
        return _report(errors), 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the NP-1C Notion route semantic characterization"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_manifest(args.manifest)
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

"""Sealed, credential-redacting target load runner for the P7.8 gate.

Importing this module performs no network, process, filesystem, or environment
work.  The public CLI requires an exact confirmation phrase before it will
authenticate to a target.  Target credentials are read from a separate input
and are never copied, hashed, logged, or serialized into an evidence artifact.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import http.cookiejar
import json
import math
import os
from pathlib import Path, PurePosixPath
import random
import re
import secrets
import ssl
import sys
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    HTTPSHandler,
    Request,
    build_opener,
)
from uuid import uuid4


FIXTURE_SCHEMA = "code-assist.performance-load-fixture"
CREDENTIAL_SCHEMA = "code-assist.performance-load-credentials"
HARDWARE_SCHEMA = "code-assist.performance-target-hardware"
REPORT_SCHEMA = "code-assist.performance-load-report"
GATE_SCHEMA = "code-assist.performance-load-gate"
SCHEMA_VERSION = 1
CONFIRMATION = "PERF-001_TARGET_LOAD"
HEALTH_SCHEMA = "code-assist.production-health"
PERCENTILE_METHOD = "nearest-rank-v1"

PROFILES = frozenset(
    {
        "steady",
        "hot-item",
        "write-saturation",
        "projection-burst",
        "leak-scan",
    }
)
PROFILE_ORDER = (
    "steady",
    "hot-item",
    "write-saturation",
    "projection-burst",
    "leak-scan",
)
STEADY_WEIGHTS: Mapping[str, int] = {
    "fts_search": 35,
    "item_read": 20,
    "conversation": 10,
    "manual_publication": 8,
    "edit_save": 8,
    "personal_publication": 5,
    "dual_publication": 4,
    "lifecycle_pair": 4,
    "lease_cycle": 4,
    "projection_job_status": 2,
}
PROFILE_TEMPLATES: Mapping[str, tuple[str, ...]] = {
    "steady": tuple(STEADY_WEIGHTS),
    "hot-item": ("hot_item",),
    "write-saturation": ("write_saturation",),
    "projection-burst": ("projection_mutation",),
    "leak-scan": (
        "leak_api",
        "leak_fts",
        "leak_agent",
        "leak_graph",
        "leak_projection",
    ),
}
PROFILE_CONFIG: Mapping[str, Mapping[str, int]] = {
    "steady": {
        "clients": 40,
        "ramp_seconds": 120,
        "duration_seconds": 1200,
        "cooldown_seconds": 60,
        "operation_target": 0,
    },
    "hot-item": {
        "clients": 40,
        "ramp_seconds": 0,
        "duration_seconds": 300,
        "cooldown_seconds": 60,
        "operation_target": 0,
    },
    "write-saturation": {
        "clients": 40,
        "ramp_seconds": 0,
        "duration_seconds": 600,
        "cooldown_seconds": 60,
        "operation_target": 0,
    },
    "projection-burst": {
        "clients": 40,
        "ramp_seconds": 0,
        "duration_seconds": 60,
        "cooldown_seconds": 60,
        "operation_target": 1000,
    },
    "leak-scan": {
        "clients": 40,
        "ramp_seconds": 0,
        "duration_seconds": 0,
        "cooldown_seconds": 60,
        "operation_target": 0,
    },
}
ENDPOINT_CLASSES = frozenset({"crud", "search", "projection", "job", "leak"})
METRIC_KINDS = frozenset({"invariant", "request", "projection_lag"})
INVARIANT_CODES = frozenset(
    {
        "DOMAIN_EFFECT_AT_MOST_ONCE",
        "DUAL_PUBLICATION_ATOMIC",
        "JOB_TERMINAL_OR_RECOVERABLE",
        "STALE_SAVE_NO_REVISION",
    }
)
PROFILE_INVARIANTS: Mapping[str, frozenset[str]] = {
    "steady": INVARIANT_CODES,
    "hot-item": frozenset({"STALE_SAVE_NO_REVISION"}),
    "write-saturation": frozenset({"DOMAIN_EFFECT_AT_MOST_ONCE"}),
    "projection-burst": frozenset(
        {"DOMAIN_EFFECT_AT_MOST_ONCE", "JOB_TERMINAL_OR_RECOVERABLE"}
    ),
    "leak-scan": frozenset(),
}
ASSERTION_OPERATORS = frozenset({"absent", "all_present", "equals", "in"})
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
GENERATORS = frozenset({"uuid", "lease_token", "sequence", "markdown_nonce"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$", re.ASCII)
_VARIABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$", re.ASCII)
_PLACEHOLDER_RE = re.compile(r"\$\{([a-z][a-z0-9_]{0,63})\}", re.ASCII)
_CLIENT_ID_RE = re.compile(r"^client-(?:0[0-9]|[12][0-9]|3[0-9])$", re.ASCII)
_SENSITIVE_FRAGMENT_RE = re.compile(
    r"(?:password|passwd|secret|token|cookie|authorization|api[_-]?key)",
    re.IGNORECASE,
)
_MAX_JSON_BYTES = 16 * 1024 * 1024
_MAX_REPORT_BYTES = 512 * 1024 * 1024
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class PerformanceLoadError(RuntimeError):
    """Stable, secret-free load-runner failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PerformanceLoadError("DUPLICATE_JSON_MEMBER")
        result[key] = value
    return result


def _parse_json_document(
    raw: bytes, *, code: str, maximum_bytes: int = _MAX_JSON_BYTES
) -> Mapping[str, object]:
    if not isinstance(raw, bytes) or not raw or len(raw) > maximum_bytes:
        raise PerformanceLoadError(code)
    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or not raw.endswith(b"\n"):
        raise PerformanceLoadError(code)
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                PerformanceLoadError(code)
            ),
        )
    except PerformanceLoadError:
        raise
    except (UnicodeError, ValueError, TypeError) as exc:
        raise PerformanceLoadError(code) from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise PerformanceLoadError(code)
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], code: str) -> None:
    if set(value) != expected:
        raise PerformanceLoadError(code)


def _text(value: object, *, code: str, maximum: int = 2000) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value != value.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise PerformanceLoadError(code)
    return value


def _nonnegative_int(value: object, *, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PerformanceLoadError(code)
    return value


def _positive_int(value: object, *, code: str) -> int:
    result = _nonnegative_int(value, code=code)
    if result == 0:
        raise PerformanceLoadError(code)
    return result


def _sensitive_key(key: object) -> bool:
    return isinstance(key, str) and _SENSITIVE_FRAGMENT_RE.search(key) is not None


def _assert_fixture_sanitized(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _sensitive_key(key):
                raise PerformanceLoadError("FIXTURE_SECRET_FIELD")
            _assert_fixture_sanitized(child)
    elif isinstance(value, list):
        for child in value:
            _assert_fixture_sanitized(child)
    elif isinstance(value, str):
        if len(value) > 2_000_000 or "\x00" in value:
            raise PerformanceLoadError("FIXTURE_SHAPE")
    elif value is not None and not isinstance(value, (bool, int)):
        raise PerformanceLoadError("FIXTURE_SHAPE")


@dataclass(frozen=True, slots=True)
class Capture:
    source: str
    pointer: str


@dataclass(frozen=True, slots=True)
class Poll:
    pointer: str
    equals: object
    timeout_seconds: int
    interval_ms: int


@dataclass(frozen=True, slots=True)
class InvariantAssertion:
    code: str
    operator: str
    pointers: tuple[str, ...]
    expected: object


@dataclass(frozen=True, slots=True)
class Step:
    name: str
    method: str
    path: str
    body: Mapping[str, object] | None
    headers: Mapping[str, str]
    expected_status: tuple[int, ...]
    database_backed: bool
    endpoint_class: str
    captures: Mapping[str, Capture]
    forbid_body_values: tuple[str, ...]
    poll: Poll | None
    assertions: tuple[InvariantAssertion, ...] = ()


@dataclass(frozen=True, slots=True)
class OperationTemplate:
    name: str
    weight: int
    generate: Mapping[str, str]
    steps: tuple[Step, ...]


@dataclass(frozen=True, slots=True)
class LeakCase:
    case_id: str
    variables: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class LoadClient:
    client_id: str
    user_id: str
    part_key: str
    variables: Mapping[str, object]
    leak_cases: tuple[LeakCase, ...]


@dataclass(frozen=True, slots=True)
class LoadFixture:
    fixture_id: str
    clients: tuple[LoadClient, ...]
    templates: Mapping[str, OperationTemplate]
    document_sha256: str


@dataclass(frozen=True, slots=True)
class Credential:
    client_id: str
    user_id: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class HardwareFacts:
    server_id: str
    os_name: str
    os_version: str
    cpu_model: str
    logical_cpu_count: int
    ram_bytes: int
    gpu_model: str
    gpu_memory_bytes: int
    storage_model: str
    document_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "cpu_model": self.cpu_model,
            "document_sha256": self.document_sha256,
            "gpu_memory_bytes": self.gpu_memory_bytes,
            "gpu_model": self.gpu_model,
            "logical_cpu_count": self.logical_cpu_count,
            "os_name": self.os_name,
            "os_version": self.os_version,
            "ram_bytes": self.ram_bytes,
            "server_id": self.server_id,
            "storage_model": self.storage_model,
        }


def _parse_capture(value: object) -> Capture:
    if not isinstance(value, dict):
        raise PerformanceLoadError("FIXTURE_CAPTURE")
    _exact_keys(value, {"pointer", "source"}, "FIXTURE_CAPTURE")
    source = value.get("source")
    pointer = value.get("pointer")
    if source not in {"json", "header"} or not isinstance(pointer, str):
        raise PerformanceLoadError("FIXTURE_CAPTURE")
    if source == "json" and (not pointer.startswith("/") or len(pointer) > 500):
        raise PerformanceLoadError("FIXTURE_CAPTURE")
    if source == "header" and re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", pointer) is None:
        raise PerformanceLoadError("FIXTURE_CAPTURE")
    return Capture(source=source, pointer=pointer)


def _parse_poll(value: object) -> Poll | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise PerformanceLoadError("FIXTURE_POLL")
    _exact_keys(
        value,
        {"equals", "interval_ms", "pointer", "timeout_seconds"},
        "FIXTURE_POLL",
    )
    pointer = value.get("pointer")
    if not isinstance(pointer, str) or not pointer.startswith("/") or len(pointer) > 500:
        raise PerformanceLoadError("FIXTURE_POLL")
    timeout = _positive_int(value.get("timeout_seconds"), code="FIXTURE_POLL")
    interval = _positive_int(value.get("interval_ms"), code="FIXTURE_POLL")
    if timeout > 300 or interval > 10_000:
        raise PerformanceLoadError("FIXTURE_POLL")
    equals = value.get("equals")
    if isinstance(equals, (dict, list)) or (
        equals is not None and not isinstance(equals, (str, bool, int))
    ):
        raise PerformanceLoadError("FIXTURE_POLL")
    if isinstance(equals, str) and (
        len(equals) > 2000 or "\x00" in equals
    ):
        raise PerformanceLoadError("FIXTURE_POLL")
    return Poll(pointer, equals, timeout, interval)


def _parse_assertion(value: object) -> InvariantAssertion:
    if not isinstance(value, dict):
        raise PerformanceLoadError("FIXTURE_ASSERTION")
    _exact_keys(
        value,
        {"code", "expected", "operator", "pointers"},
        "FIXTURE_ASSERTION",
    )
    code = value.get("code")
    operator = value.get("operator")
    pointers = value.get("pointers")
    expected = value.get("expected")
    if (
        code not in INVARIANT_CODES
        or operator not in ASSERTION_OPERATORS
        or not isinstance(pointers, list)
        or not pointers
        or len(pointers) > 20
        or any(
            not isinstance(pointer, str)
            or not pointer.startswith("/")
            or len(pointer) > 500
            for pointer in pointers
        )
        or pointers != sorted(set(pointers))
    ):
        raise PerformanceLoadError("FIXTURE_ASSERTION")
    if operator in {"equals", "in"} and len(pointers) != 1:
        raise PerformanceLoadError("FIXTURE_ASSERTION")
    if operator in {"absent", "all_present"}:
        if expected is not None:
            raise PerformanceLoadError("FIXTURE_ASSERTION")
    elif operator == "equals":
        if isinstance(expected, (dict, list)) or (
            expected is not None and not isinstance(expected, (str, bool, int))
        ):
            raise PerformanceLoadError("FIXTURE_ASSERTION")
    elif operator == "in":
        if (
            not isinstance(expected, list)
            or not expected
            or len(expected) > 20
            or any(
                isinstance(item, (dict, list))
                or (item is not None and not isinstance(item, (str, bool, int)))
                for item in expected
            )
        ):
            raise PerformanceLoadError("FIXTURE_ASSERTION")
    _assert_fixture_sanitized(expected)
    return InvariantAssertion(
        code=str(code),
        operator=str(operator),
        pointers=tuple(pointers),
        expected=expected,
    )


def _validate_api_path(path: object) -> str:
    value = _text(path, code="FIXTURE_PATH", maximum=2000)
    if (
        not value.startswith("/api/knowledge/v1/")
        or "\\" in value
        or "#" in value
        or "//" in value
        or any(part in {"", ".", ".."} for part in PurePosixPath(value.split("?", 1)[0]).parts[1:])
    ):
        raise PerformanceLoadError("FIXTURE_PATH")
    return value


def _parse_step(value: object) -> Step:
    if not isinstance(value, dict):
        raise PerformanceLoadError("FIXTURE_STEP")
    _exact_keys(
        value,
        {
            "assertions",
            "body",
            "captures",
            "database_backed",
            "endpoint_class",
            "expected_status",
            "forbid_body_values",
            "headers",
            "method",
            "name",
            "path",
            "poll",
        },
        "FIXTURE_STEP",
    )
    name = _text(value.get("name"), code="FIXTURE_STEP", maximum=64)
    if _ID_RE.fullmatch(name) is None:
        raise PerformanceLoadError("FIXTURE_STEP")
    method = value.get("method")
    if method not in METHODS:
        raise PerformanceLoadError("FIXTURE_STEP")
    path = _validate_api_path(value.get("path"))
    body = value.get("body")
    if body is not None and not isinstance(body, dict):
        raise PerformanceLoadError("FIXTURE_STEP")
    if body is not None:
        _assert_fixture_sanitized(body)
    if method in {"GET"} and body is not None:
        raise PerformanceLoadError("FIXTURE_STEP")
    headers = value.get("headers")
    if not isinstance(headers, dict):
        raise PerformanceLoadError("FIXTURE_STEP")
    parsed_headers: dict[str, str] = {}
    for key, item in headers.items():
        if (
            not isinstance(key, str)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", key) is None
            or key.casefold()
            in {
                "authorization",
                "cookie",
                "host",
                "origin",
                "x-csrf-token",
                "content-length",
            }
            or not isinstance(item, str)
            or len(item) > 4096
        ):
            raise PerformanceLoadError("FIXTURE_HEADER")
        parsed_headers[key] = item
    statuses = value.get("expected_status")
    if (
        not isinstance(statuses, list)
        or not statuses
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 100 or item > 599 for item in statuses)
        or statuses != sorted(set(statuses))
    ):
        raise PerformanceLoadError("FIXTURE_STEP")
    database_backed = value.get("database_backed")
    endpoint_class = value.get("endpoint_class")
    if not isinstance(database_backed, bool) or endpoint_class not in ENDPOINT_CLASSES:
        raise PerformanceLoadError("FIXTURE_STEP")
    captures = value.get("captures")
    if not isinstance(captures, dict):
        raise PerformanceLoadError("FIXTURE_CAPTURE")
    parsed_captures: dict[str, Capture] = {}
    for variable, capture in captures.items():
        if not isinstance(variable, str) or _VARIABLE_RE.fullmatch(variable) is None:
            raise PerformanceLoadError("FIXTURE_CAPTURE")
        parsed_captures[variable] = _parse_capture(capture)
    forbidden = value.get("forbid_body_values")
    if (
        not isinstance(forbidden, list)
        or any(not isinstance(item, str) or not item or len(item) > 2000 for item in forbidden)
    ):
        raise PerformanceLoadError("FIXTURE_STEP")
    assertion_rows = value.get("assertions")
    if not isinstance(assertion_rows, list) or len(assertion_rows) > 4:
        raise PerformanceLoadError("FIXTURE_ASSERTION")
    assertions = tuple(_parse_assertion(item) for item in assertion_rows)
    if len({item.code for item in assertions}) != len(assertions):
        raise PerformanceLoadError("FIXTURE_ASSERTION")
    return Step(
        name=name,
        method=str(method),
        path=path,
        body=body,
        headers=dict(sorted(parsed_headers.items())),
        expected_status=tuple(statuses),
        database_backed=database_backed,
        endpoint_class=str(endpoint_class),
        captures=dict(sorted(parsed_captures.items())),
        forbid_body_values=tuple(forbidden),
        poll=_parse_poll(value.get("poll")),
        assertions=assertions,
    )


def _placeholders(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            found.update(_placeholders(key))
            found.update(_placeholders(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_placeholders(child))
    elif isinstance(value, str):
        found.update(_PLACEHOLDER_RE.findall(value))
    return found


def _parse_template(name: str, value: object) -> OperationTemplate:
    if not isinstance(value, dict) or _ID_RE.fullmatch(name) is None:
        raise PerformanceLoadError("FIXTURE_TEMPLATE")
    _exact_keys(value, {"generate", "steps", "weight"}, "FIXTURE_TEMPLATE")
    weight = _nonnegative_int(value.get("weight"), code="FIXTURE_TEMPLATE")
    generated = value.get("generate")
    if not isinstance(generated, dict):
        raise PerformanceLoadError("FIXTURE_TEMPLATE")
    parsed_generated: dict[str, str] = {}
    for variable, generator in generated.items():
        if (
            not isinstance(variable, str)
            or _VARIABLE_RE.fullmatch(variable) is None
            or generator not in GENERATORS
        ):
            raise PerformanceLoadError("FIXTURE_TEMPLATE")
        parsed_generated[variable] = str(generator)
    rows = value.get("steps")
    if not isinstance(rows, list) or not rows or len(rows) > 20:
        raise PerformanceLoadError("FIXTURE_TEMPLATE")
    steps = tuple(_parse_step(row) for row in rows)
    if len({step.name for step in steps}) != len(steps):
        raise PerformanceLoadError("FIXTURE_TEMPLATE")
    return OperationTemplate(
        name=name,
        weight=weight,
        generate=dict(sorted(parsed_generated.items())),
        steps=steps,
    )


def _parse_variables(value: object, *, code: str) -> dict[str, object]:
    if not isinstance(value, dict) or len(value) > 200:
        raise PerformanceLoadError(code)
    result: dict[str, object] = {}
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or _VARIABLE_RE.fullmatch(key) is None
            or _sensitive_key(key)
        ):
            raise PerformanceLoadError(code)
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise PerformanceLoadError(code)
        if isinstance(item, str) and (not item or len(item) > 2_000_000 or "\x00" in item):
            raise PerformanceLoadError(code)
        result[key] = item
    return dict(sorted(result.items()))


def parse_fixture(raw: bytes) -> LoadFixture:
    value = _parse_json_document(raw, code="FIXTURE_INVALID")
    _exact_keys(
        value,
        {"clients", "fixture_id", "fixture_schema", "schema_version", "templates"},
        "FIXTURE_SHAPE",
    )
    if value.get("fixture_schema") != FIXTURE_SCHEMA or value.get("schema_version") != 1:
        raise PerformanceLoadError("FIXTURE_VERSION")
    fixture_id = _text(value.get("fixture_id"), code="FIXTURE_SHAPE", maximum=64)
    if _ID_RE.fullmatch(fixture_id) is None:
        raise PerformanceLoadError("FIXTURE_SHAPE")
    template_rows = value.get("templates")
    if not isinstance(template_rows, dict) or set(template_rows) != {
        name for names in PROFILE_TEMPLATES.values() for name in names
    }:
        raise PerformanceLoadError("FIXTURE_TEMPLATE_SET")
    templates = {
        name: _parse_template(name, template_rows[name])
        for name in sorted(template_rows)
    }
    if {name: templates[name].weight for name in STEADY_WEIGHTS} != dict(STEADY_WEIGHTS):
        raise PerformanceLoadError("FIXTURE_STEADY_MIX")
    if any(templates[name].weight != 0 for name in set(templates) - set(STEADY_WEIGHTS)):
        raise PerformanceLoadError("FIXTURE_STEADY_MIX")
    if not any(
        step.endpoint_class == "search"
        for step in templates["fts_search"].steps
    ):
        raise PerformanceLoadError("FIXTURE_STEADY_MIX")
    if not any(
        step.endpoint_class == "projection" and step.poll is not None
        for step in templates["projection_job_status"].steps
    ):
        raise PerformanceLoadError("FIXTURE_PROJECTION_LAG")
    for profile, required_codes in PROFILE_INVARIANTS.items():
        observed_codes = {
            assertion.code
            for template_name in PROFILE_TEMPLATES[profile]
            for step in templates[template_name].steps
            for assertion in step.assertions
        }
        if observed_codes != set(required_codes):
            raise PerformanceLoadError("FIXTURE_INVARIANT_SET")

    rows = value.get("clients")
    if not isinstance(rows, list) or len(rows) != 40:
        raise PerformanceLoadError("FIXTURE_CLIENT_COUNT")
    clients: list[LoadClient] = []
    seen_client: set[str] = set()
    seen_user: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise PerformanceLoadError("FIXTURE_CLIENT")
        _exact_keys(
            row,
            {"client_id", "leak_cases", "part_key", "user_id", "variables"},
            "FIXTURE_CLIENT",
        )
        client_id = row.get("client_id")
        user_id = row.get("user_id")
        part_key = row.get("part_key")
        if (
            not isinstance(client_id, str)
            or _CLIENT_ID_RE.fullmatch(client_id) is None
            or not isinstance(user_id, str)
            or _ID_RE.fullmatch(user_id) is None
            or not isinstance(part_key, str)
            or _ID_RE.fullmatch(part_key) is None
            or client_id in seen_client
            or user_id.casefold() in seen_user
        ):
            raise PerformanceLoadError("FIXTURE_CLIENT")
        seen_client.add(client_id)
        seen_user.add(user_id.casefold())
        variables = _parse_variables(row.get("variables"), code="FIXTURE_CLIENT")
        leak_rows = row.get("leak_cases")
        if not isinstance(leak_rows, list) or not leak_rows:
            raise PerformanceLoadError("FIXTURE_LEAK_CASE")
        leak_cases: list[LeakCase] = []
        seen_case: set[str] = set()
        for leak in leak_rows:
            if not isinstance(leak, dict):
                raise PerformanceLoadError("FIXTURE_LEAK_CASE")
            _exact_keys(leak, {"case_id", "variables"}, "FIXTURE_LEAK_CASE")
            case_id = leak.get("case_id")
            if (
                not isinstance(case_id, str)
                or _ID_RE.fullmatch(case_id) is None
                or case_id in seen_case
            ):
                raise PerformanceLoadError("FIXTURE_LEAK_CASE")
            seen_case.add(case_id)
            leak_cases.append(
                LeakCase(
                    case_id=case_id,
                    variables=_parse_variables(
                        leak.get("variables"), code="FIXTURE_LEAK_CASE"
                    ),
                )
            )
        clients.append(
            LoadClient(
                client_id=client_id,
                user_id=user_id,
                part_key=part_key,
                variables=variables,
                leak_cases=tuple(sorted(leak_cases, key=lambda item: item.case_id)),
            )
        )
    clients.sort(key=lambda item: item.client_id)
    if [item.client_id for item in clients] != [f"client-{index:02d}" for index in range(40)]:
        raise PerformanceLoadError("FIXTURE_CLIENT_SET")
    part_counts = Counter(item.part_key for item in clients)
    if sorted(part_counts.values()) != [10, 10, 10, 10]:
        raise PerformanceLoadError("FIXTURE_HOT_ITEM_GROUPS")
    required_client_variables = {
        "hot_item_id",
        "markdown_value",
        "part_space_id",
        "personal_canary",
        "personal_canary_item_id",
        "personal_space_id",
        "saturation_item_id",
        "search_query",
    }
    if any(
        not required_client_variables.issubset(client.variables)
        or any(
            not isinstance(client.variables.get(key), str)
            for key in required_client_variables
        )
        for client in clients
    ):
        raise PerformanceLoadError("FIXTURE_CLIENT_VARIABLES")
    hot_item_by_part: dict[str, tuple[object, object]] = {}
    for client in clients:
        binding = (
            client.variables["part_space_id"],
            client.variables["hot_item_id"],
        )
        prior = hot_item_by_part.setdefault(client.part_key, binding)
        if prior != binding:
            raise PerformanceLoadError("FIXTURE_HOT_ITEM_GROUPS")
    if len(set(hot_item_by_part.values())) != 4:
        raise PerformanceLoadError("FIXTURE_HOT_ITEM_GROUPS")
    if len(
        {
            (client.variables["personal_space_id"], client.variables["saturation_item_id"])
            for client in clients
        }
    ) != 40:
        raise PerformanceLoadError("FIXTURE_SATURATION_IDENTITIES")

    personal_canaries: dict[str, tuple[object, object, object]] = {}
    for client in clients:
        binding = (
            client.variables["personal_canary"],
            client.variables["personal_canary_item_id"],
            client.variables["personal_space_id"],
        )
        if binding in personal_canaries.values():
            raise PerformanceLoadError("FIXTURE_CANARY_IDENTITIES")
        personal_canaries[client.user_id] = binding
    leak_keys = {
        "foreign_canary",
        "foreign_item_id",
        "foreign_space_id",
        "foreign_user_id",
    }
    for client in clients:
        if len(client.leak_cases) != 39:
            raise PerformanceLoadError("FIXTURE_LEAK_MATRIX")
        observed: set[tuple[object, object, object, object]] = set()
        for case in client.leak_cases:
            if set(case.variables) != leak_keys:
                raise PerformanceLoadError("FIXTURE_LEAK_MATRIX")
            observed.add(
                (
                    case.variables["foreign_user_id"],
                    case.variables["foreign_canary"],
                    case.variables["foreign_item_id"],
                    case.variables["foreign_space_id"],
                )
            )
        expected = {
            (user_id, canary, item_id, space_id)
            for user_id, (canary, item_id, space_id) in personal_canaries.items()
            if user_id != client.user_id
        }
        if observed != expected or len(observed) != 39:
            raise PerformanceLoadError("FIXTURE_LEAK_MATRIX")
    all_client_variables = set.intersection(
        *(set(client.variables) for client in clients)
    )
    for template in templates.values():
        available = {"client_id", "user_id"} | set(all_client_variables) | set(template.generate)
        for step in template.steps:
            missing = _placeholders(
                {
                    "assertions": [
                        {
                            "expected": assertion.expected,
                            "pointers": list(assertion.pointers),
                        }
                        for assertion in step.assertions
                    ],
                    "body": step.body,
                    "forbid": list(step.forbid_body_values),
                    "headers": dict(step.headers),
                    "path": step.path,
                    "poll": None if step.poll is None else step.poll.equals,
                }
            ) - available
            if template.name.startswith("leak_"):
                leak_keys = set.intersection(
                    *(
                        set(case.variables)
                        for client in clients
                        for case in client.leak_cases
                    )
                )
                missing -= leak_keys
            if missing:
                raise PerformanceLoadError("FIXTURE_PLACEHOLDER")
            available.update(step.captures)
    return LoadFixture(
        fixture_id=fixture_id,
        clients=tuple(clients),
        templates=templates,
        document_sha256=sha256_bytes(raw),
    )


def parse_credentials(raw: bytes, fixture: LoadFixture) -> tuple[Credential, ...]:
    value = _parse_json_document(raw, code="CREDENTIALS_INVALID")
    _exact_keys(
        value,
        {"clients", "credential_schema", "schema_version"},
        "CREDENTIALS_INVALID",
    )
    if value.get("credential_schema") != CREDENTIAL_SCHEMA or value.get("schema_version") != 1:
        raise PerformanceLoadError("CREDENTIALS_INVALID")
    rows = value.get("clients")
    if not isinstance(rows, list) or len(rows) != 40:
        raise PerformanceLoadError("CREDENTIALS_INVALID")
    parsed: list[Credential] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise PerformanceLoadError("CREDENTIALS_INVALID")
        _exact_keys(row, {"client_id", "password", "user_id"}, "CREDENTIALS_INVALID")
        client_id = row.get("client_id")
        user_id = row.get("user_id")
        password = row.get("password")
        if (
            not isinstance(client_id, str)
            or not isinstance(user_id, str)
            or not isinstance(password, str)
            or not password
            or len(password) > 1024
            or client_id in seen
        ):
            raise PerformanceLoadError("CREDENTIALS_INVALID")
        seen.add(client_id)
        parsed.append(Credential(client_id, user_id, password))
    parsed.sort(key=lambda item: item.client_id)
    if [(row.client_id, row.user_id) for row in parsed] != [
        (row.client_id, row.user_id) for row in fixture.clients
    ]:
        raise PerformanceLoadError("CREDENTIAL_BINDING")
    return tuple(parsed)


def parse_hardware(raw: bytes) -> HardwareFacts:
    value = _parse_json_document(raw, code="HARDWARE_INVALID")
    _exact_keys(
        value,
        {
            "cpu_model",
            "gpu_memory_bytes",
            "gpu_model",
            "hardware_schema",
            "logical_cpu_count",
            "os_name",
            "os_version",
            "ram_bytes",
            "schema_version",
            "server_id",
            "storage_model",
        },
        "HARDWARE_INVALID",
    )
    if value.get("hardware_schema") != HARDWARE_SCHEMA or value.get("schema_version") != 1:
        raise PerformanceLoadError("HARDWARE_INVALID")
    return HardwareFacts(
        server_id=_text(value.get("server_id"), code="HARDWARE_INVALID", maximum=128),
        os_name=_text(value.get("os_name"), code="HARDWARE_INVALID", maximum=256),
        os_version=_text(value.get("os_version"), code="HARDWARE_INVALID", maximum=256),
        cpu_model=_text(value.get("cpu_model"), code="HARDWARE_INVALID", maximum=512),
        logical_cpu_count=_positive_int(value.get("logical_cpu_count"), code="HARDWARE_INVALID"),
        ram_bytes=_positive_int(value.get("ram_bytes"), code="HARDWARE_INVALID"),
        gpu_model=_text(value.get("gpu_model"), code="HARDWARE_INVALID", maximum=512),
        gpu_memory_bytes=_positive_int(value.get("gpu_memory_bytes"), code="HARDWARE_INVALID"),
        storage_model=_text(value.get("storage_model"), code="HARDWARE_INVALID", maximum=512),
        document_sha256=sha256_bytes(raw),
    )


@dataclass(frozen=True, slots=True)
class RunConfig:
    profile: str
    clients: int
    ramp_seconds: int
    duration_seconds: int
    cooldown_seconds: int
    seed: int

    @classmethod
    def exact(cls, profile: str, *, seed: int) -> "RunConfig":
        if profile not in PROFILES:
            raise PerformanceLoadError("PROFILE_INVALID")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0 or seed > 0xFFFFFFFF:
            raise PerformanceLoadError("SEED_INVALID")
        row = PROFILE_CONFIG[profile]
        return cls(
            profile=profile,
            clients=row["clients"],
            ramp_seconds=row["ramp_seconds"],
            duration_seconds=row["duration_seconds"],
            cooldown_seconds=row["cooldown_seconds"],
            seed=seed,
        )


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes
    latency_ms: float


@dataclass(frozen=True, slots=True)
class Sample:
    sequence: int
    client_index: int
    phase: str
    profile: str
    operation_class: str
    step_name: str
    endpoint_class: str
    metric_kind: str
    started_offset_ms: int
    latency_ms: float
    status: int
    success: bool
    error_code: str | None
    database_backed: bool
    busy_failure: bool
    canary_exposure: bool
    invariant_code: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "busy_failure": self.busy_failure,
            "canary_exposure": self.canary_exposure,
            "client_index": self.client_index,
            "database_backed": self.database_backed,
            "endpoint_class": self.endpoint_class,
            "error_code": self.error_code,
            "latency_ms": round(self.latency_ms, 6),
            "invariant_code": self.invariant_code,
            "metric_kind": self.metric_kind,
            "operation_class": self.operation_class,
            "phase": self.phase,
            "profile": self.profile,
            "sequence": self.sequence,
            "started_offset_ms": self.started_offset_ms,
            "status": self.status,
            "step_name": self.step_name,
            "success": self.success,
        }


class _AtomicCounter:
    def __init__(self, initial: int = 0) -> None:
        self._value = initial
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            value = self._value
            self._value += 1
            return value

    def claim_below(self, limit: int) -> int | None:
        with self._lock:
            if self._value >= limit:
                return None
            value = self._value
            self._value += 1
            return value


class _Session:
    def __init__(
        self,
        *,
        base_url: str,
        origin: str,
        timeout_seconds: int,
        tls_context: ssl.SSLContext,
    ) -> None:
        self.base_url = base_url
        self.origin = origin
        self.timeout_seconds = timeout_seconds
        self.csrf_token: str | None = None
        self._opener = build_opener(
            HTTPCookieProcessor(http.cookiejar.CookieJar()),
            HTTPSHandler(context=tls_context),
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        require_csrf: bool = True,
    ) -> HttpResult:
        encoded = None if body is None else canonical_json_bytes(body)
        request_headers = {
            "Accept": "application/json",
            "Origin": self.origin,
            "User-Agent": "code-assist-perf001/1",
        }
        if encoded is not None:
            request_headers["Content-Type"] = "application/json"
        if require_csrf and method not in {"GET", "HEAD", "OPTIONS"}:
            if self.csrf_token is None:
                raise PerformanceLoadError("SESSION_NOT_AUTHENTICATED")
            request_headers["X-CSRF-Token"] = self.csrf_token
        request_headers.update(headers or {})
        started = time.perf_counter_ns()
        try:
            response = self._opener.open(
                Request(
                    self.base_url + path,
                    data=encoded,
                    headers=request_headers,
                    method=method,
                ),
                timeout=self.timeout_seconds,
            )
            try:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                status = int(response.status)
                response_headers = {key.casefold(): value for key, value in response.headers.items()}
            finally:
                response.close()
        except HTTPError as exc:
            raw = exc.read(_MAX_RESPONSE_BYTES + 1)
            status = int(exc.code)
            response_headers = {key.casefold(): value for key, value in exc.headers.items()}
        except (OSError, URLError, TimeoutError) as exc:
            raise PerformanceLoadError("TARGET_REQUEST_FAILED") from exc
        latency_ms = (time.perf_counter_ns() - started) / 1_000_000.0
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise PerformanceLoadError("TARGET_RESPONSE_TOO_LARGE")
        return HttpResult(status, response_headers, raw, latency_ms)

    def login(self, credential: Credential) -> None:
        result = self.request(
            "POST",
            "/api/code/auth/login",
            body={"password": credential.password, "user_id": credential.user_id},
            require_csrf=False,
        )
        if result.status != 200:
            raise PerformanceLoadError("TARGET_LOGIN_FAILED")
        document = _response_json(result.body)
        token = document.get("csrf_token")
        if (
            not isinstance(token, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", token, re.ASCII) is None
        ):
            raise PerformanceLoadError("TARGET_LOGIN_FAILED")
        self.csrf_token = token


def _response_json(raw: bytes) -> Mapping[str, object]:
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_reject_duplicate_pairs)
    except PerformanceLoadError:
        raise
    except (UnicodeError, ValueError, TypeError) as exc:
        raise PerformanceLoadError("TARGET_RESPONSE_INVALID") from exc
    if not isinstance(value, dict):
        raise PerformanceLoadError("TARGET_RESPONSE_INVALID")
    return value


def _json_pointer(value: object, pointer: str) -> object:
    current = value
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise PerformanceLoadError("TARGET_RESPONSE_BINDING")
    return current


def _optional_json_pointer(value: object, pointer: str) -> tuple[bool, object]:
    try:
        return True, _json_pointer(value, pointer)
    except PerformanceLoadError as exc:
        if exc.code != "TARGET_RESPONSE_BINDING":
            raise
        return False, None


def _assertion_passes(
    assertion: InvariantAssertion,
    document: Mapping[str, object],
    variables: Mapping[str, object],
) -> bool:
    observed = [_optional_json_pointer(document, pointer) for pointer in assertion.pointers]
    if assertion.operator == "all_present":
        return all(present for present, _value in observed)
    if assertion.operator == "absent":
        return all(not present for present, _value in observed)
    present, value = observed[0]
    if not present:
        return False
    expected = _render(assertion.expected, variables)
    if assertion.operator == "equals":
        return value == expected
    if assertion.operator == "in":
        return isinstance(expected, list) and value in expected
    raise PerformanceLoadError("FIXTURE_ASSERTION")


def _render_string(value: str, variables: Mapping[str, object]) -> object:
    match = _PLACEHOLDER_RE.fullmatch(value)
    if match is not None:
        name = match.group(1)
        if name not in variables:
            raise PerformanceLoadError("FIXTURE_PLACEHOLDER")
        return variables[name]

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise PerformanceLoadError("FIXTURE_PLACEHOLDER")
        item = variables[name]
        if not isinstance(item, (str, int)) or isinstance(item, bool):
            raise PerformanceLoadError("FIXTURE_PLACEHOLDER")
        return str(item)

    return _PLACEHOLDER_RE.sub(replace, value)


def _render(value: object, variables: Mapping[str, object]) -> object:
    if isinstance(value, dict):
        return {
            str(_render_string(key, variables)): _render(child, variables)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_render(child, variables) for child in value]
    if isinstance(value, str):
        return _render_string(value, variables)
    return value


def _generated(kind: str, sequence: int) -> object:
    if kind == "uuid":
        return str(uuid4())
    if kind == "lease_token":
        return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    if kind == "sequence":
        return sequence
    if kind == "markdown_nonce":
        return f"perf001-{sequence}-{uuid4().hex}"
    raise PerformanceLoadError("FIXTURE_TEMPLATE")


def _error_code(body: bytes) -> str | None:
    try:
        value = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    error = value.get("error")
    candidate = error.get("code") if isinstance(error, dict) else value.get("code")
    if isinstance(candidate, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", candidate, re.ASCII):
        return candidate
    return None


def _phase(now: float, steady_start: float) -> str:
    return "ramp" if now < steady_start else "measure"


def _execute_operation(
    *,
    session: _Session,
    client: LoadClient,
    client_index: int,
    template: OperationTemplate,
    profile: str,
    sequence: int,
    run_started: float,
    steady_start: float,
    leak_variables: Mapping[str, object] | None = None,
) -> tuple[list[Sample], bool]:
    variables: dict[str, object] = {
        "client_id": client.client_id,
        "user_id": client.user_id,
        **client.variables,
        **(leak_variables or {}),
    }
    for name, kind in template.generate.items():
        variables[name] = _generated(kind, sequence)
    samples: list[Sample] = []
    operation_ok = True
    for step in template.steps:
        step_started = time.monotonic()
        poll_deadline = (
            None
            if step.poll is None
            else step_started + step.poll.timeout_seconds
        )
        last_status = 0
        poll_satisfied = step.poll is None
        while True:
            rendered_path = _render(step.path, variables)
            rendered_body = _render(step.body, variables)
            rendered_headers = _render(dict(step.headers), variables)
            forbidden = _render(list(step.forbid_body_values), variables)
            if not isinstance(rendered_path, str) or not isinstance(rendered_headers, dict):
                raise PerformanceLoadError("FIXTURE_PLACEHOLDER")
            if rendered_body is not None and not isinstance(rendered_body, dict):
                raise PerformanceLoadError("FIXTURE_PLACEHOLDER")
            if any(not isinstance(key, str) or not isinstance(item, str) for key, item in rendered_headers.items()):
                raise PerformanceLoadError("FIXTURE_PLACEHOLDER")
            started = time.monotonic()
            result = session.request(
                step.method,
                rendered_path,
                body=rendered_body,
                headers=rendered_headers,
            )
            exposure = any(
                isinstance(item, str) and item.encode("utf-8") in result.body
                for item in forbidden
            )
            code = _error_code(result.body)
            success = result.status in step.expected_status and not exposure
            last_status = result.status
            busy = step.database_backed and code in {"SQLITE_BUSY", "SQLITE_LOCKED"}
            samples.append(
                Sample(
                    sequence=sequence,
                    client_index=client_index,
                    phase=_phase(started, steady_start),
                    profile=profile,
                    operation_class=template.name,
                    step_name=step.name,
                    endpoint_class=step.endpoint_class,
                    metric_kind="request",
                    started_offset_ms=max(0, int((started - run_started) * 1000)),
                    latency_ms=result.latency_ms,
                    status=result.status,
                    success=success,
                    error_code=code,
                    database_backed=step.database_backed,
                    busy_failure=busy,
                    canary_exposure=exposure,
                )
            )
            if not success:
                operation_ok = False
                break
            document: Mapping[str, object] | None = None
            if step.captures or step.poll is not None or step.assertions:
                document = _response_json(result.body)
            for name, capture in step.captures.items():
                if capture.source == "json":
                    assert document is not None
                    captured = _json_pointer(document, capture.pointer)
                else:
                    captured = result.headers.get(capture.pointer.casefold())
                    if captured is None:
                        raise PerformanceLoadError("TARGET_RESPONSE_BINDING")
                if isinstance(captured, bool) or not isinstance(captured, (str, int)):
                    raise PerformanceLoadError("TARGET_RESPONSE_BINDING")
                variables[name] = captured
            if step.poll is not None:
                assert document is not None
                expected_poll_value = _render(step.poll.equals, variables)
                if _json_pointer(document, step.poll.pointer) == expected_poll_value:
                    poll_satisfied = True
                else:
                    if poll_deadline is None or time.monotonic() >= poll_deadline:
                        operation_ok = False
                        break
                    time.sleep(step.poll.interval_ms / 1000.0)
                    continue
            if step.assertions:
                assert document is not None
                for assertion in step.assertions:
                    invariant_ok = _assertion_passes(assertion, document, variables)
                    samples.append(
                        Sample(
                            sequence=sequence,
                            client_index=client_index,
                            phase=_phase(started, steady_start),
                            profile=profile,
                            operation_class=template.name,
                            step_name=f"{step.name}.assert",
                            endpoint_class=step.endpoint_class,
                            metric_kind="invariant",
                            started_offset_ms=max(
                                0, int((started - run_started) * 1000)
                            ),
                            latency_ms=0.0,
                            status=result.status,
                            success=invariant_ok,
                            error_code=None if invariant_ok else assertion.code,
                            database_backed=False,
                            busy_failure=False,
                            canary_exposure=False,
                            invariant_code=assertion.code,
                        )
                    )
                    if not invariant_ok:
                        operation_ok = False
                        break
            break
        if step.poll is not None:
            samples.append(
                Sample(
                    sequence=sequence,
                    client_index=client_index,
                    phase=_phase(step_started, steady_start),
                    profile=profile,
                    operation_class=template.name,
                    step_name=f"{step.name}.lag",
                    endpoint_class="projection",
                    metric_kind="projection_lag",
                    started_offset_ms=max(
                        0, int((step_started - run_started) * 1000)
                    ),
                    latency_ms=(time.monotonic() - step_started) * 1000.0,
                    status=last_status,
                    success=poll_satisfied and operation_ok,
                    error_code=(
                        None
                        if poll_satisfied and operation_ok
                        else "PROJECTION_TIMEOUT"
                    ),
                    database_backed=False,
                    busy_failure=False,
                    canary_exposure=False,
                )
            )
        if not operation_ok:
            break
    return samples, operation_ok


def _validate_base_url(value: str, *, allow_loopback_http: bool) -> tuple[str, str]:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise PerformanceLoadError("BASE_URL_INVALID") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
    ):
        raise PerformanceLoadError("BASE_URL_INVALID")
    host = parsed.hostname.casefold()
    loopback = host in {"127.0.0.1", "::1", "localhost"}
    if parsed.scheme != "https" and not (loopback and allow_loopback_http):
        raise PerformanceLoadError("BASE_URL_INSECURE")
    canonical_host = f"[{host}]" if ":" in host else host
    origin = f"{parsed.scheme}://{canonical_host}:{port}"
    return origin, origin


def _health_binding(document: Mapping[str, object]) -> dict[str, object]:
    if (
        document.get("report_schema") != HEALTH_SCHEMA
        or document.get("schema_version") != 1
        or not isinstance(document.get("gate"), dict)
    ):
        raise PerformanceLoadError("TARGET_HEALTH_INVALID")
    gate = document["gate"]
    assert isinstance(gate, dict)
    if (
        gate.get("status") not in {"ready", "not_ready"}
        or not isinstance(gate.get("reason_codes"), list)
        or any(
            not isinstance(item, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{1,127}", item, re.ASCII) is None
            for item in gate["reason_codes"]
        )
        or gate["reason_codes"] != sorted(set(gate["reason_codes"]))
        or (gate["status"] == "ready") != (gate["reason_codes"] == [])
    ):
        raise PerformanceLoadError("TARGET_HEALTH_INVALID")
    build = document.get("build")
    sqlite_row = document.get("sqlite")
    metrics = document.get("database_metrics")
    projection = document.get("projection")
    jobs = document.get("jobs")
    outbox = document.get("outbox")
    if not all(
        isinstance(item, dict)
        for item in (build, sqlite_row, metrics, projection, jobs, outbox)
    ):
        raise PerformanceLoadError("TARGET_HEALTH_INVALID")
    assert isinstance(build, dict) and isinstance(sqlite_row, dict)
    assert isinstance(metrics, dict) and isinstance(projection, dict)
    migration = build.get("schema_migration")
    if not isinstance(migration, dict):
        raise PerformanceLoadError("TARGET_HEALTH_INVALID")
    selected = {
        "application": build.get("application"),
        "application_version": build.get("application_version"),
        "config_sha256": build.get("config_sha256"),
        "database_metrics": {
            key: metrics.get(key)
            for key in (
                "application_retry_count",
                "execute_count",
                "sqlite_busy_count",
                "sqlite_locked_count",
            )
        },
        "gate": {
            "reason_codes": list(gate["reason_codes"]),
            "status": gate["status"],
        },
        "jobs": {
            key: jobs.get(key)  # type: ignore[union-attr]
            for key in (
                "attempt_count",
                "oldest_nonterminal_age_ms",
                "retry_count",
                "state_counts",
                "unrecoverable_count",
            )
        },
        "migration_catalog_sha256": build.get("migration_catalog_sha256"),
        "outbox": {
            key: outbox.get(key)  # type: ignore[union-attr]
            for key in (
                "attempt_count",
                "oldest_nonterminal_age_ms",
                "retry_count",
                "state_counts",
            )
        },
        "projection": {
            key: projection.get(key)
            for key in (
                "built_generation",
                "desired_generation",
                "error_count",
                "lag_generation",
                "lagging_count",
            )
        },
        "schema_migration": {
            key: migration.get(key)
            for key in ("checksum_sha256", "name", "version")
        },
        "service_id": document.get("service_id"),
        "sqlite": {
            "source_id": sqlite_row.get("source_id"),
            "version": sqlite_row.get("version"),
        },
    }
    encoded = canonical_json_bytes(selected)
    if _SENSITIVE_FRAGMENT_RE.search(encoded.decode("utf-8", errors="strict")):
        # Selected key names are deliberately non-secret; this catches a future
        # accidental sensitive extension rather than inspecting source values.
        raise PerformanceLoadError("TARGET_HEALTH_INVALID")
    return selected


def _fetch_health(
    session: _Session, *, require_ready: bool
) -> dict[str, object]:
    result = session.request("GET", "/healthz")
    if result.status not in {200, 503}:
        raise PerformanceLoadError("TARGET_NOT_READY")
    selected = _health_binding(_response_json(result.body))
    gate = selected["gate"]
    assert isinstance(gate, dict)
    if require_ready and (result.status != 200 or gate["status"] != "ready"):
        raise PerformanceLoadError("TARGET_NOT_READY")
    return selected


def nearest_rank(values: Sequence[float], percentile: int) -> float | None:
    if percentile not in {50, 95, 99}:
        raise ValueError("unsupported percentile")
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    return round(ordered[rank - 1], 6)


def _aggregate_samples(samples: Sequence[Sample], *, profile: str) -> dict[str, object]:
    measured = [sample for sample in samples if sample.phase == "measure"]
    measured_requests = [sample for sample in measured if sample.metric_kind == "request"]
    measured_invariants = [
        sample for sample in measured if sample.metric_kind == "invariant"
    ]
    operation_rows: dict[tuple[int, str], list[Sample]] = {}
    for sample in measured:
        operation_rows.setdefault((sample.sequence, sample.operation_class), []).append(
            sample
        )
    operation_failure_count = sum(
        any(not sample.success for sample in rows) for rows in operation_rows.values()
    )
    operation_count = len(operation_rows)
    operation_error_rate = (
        0.0 if operation_count == 0 else operation_failure_count / operation_count
    )
    duration_seconds = PROFILE_CONFIG[profile]["duration_seconds"]
    if duration_seconds > 0:
        operation_throughput = operation_count / duration_seconds
        request_throughput = len(measured_requests) / duration_seconds
    else:
        measured_span_ms = max(
            (
                sample.started_offset_ms + int(math.ceil(sample.latency_ms))
                for sample in measured
            ),
            default=0,
        )
        denominator = max(0.001, measured_span_ms / 1000.0)
        operation_throughput = operation_count / denominator
        request_throughput = len(measured_requests) / denominator
    classes: dict[str, object] = {}
    for name in sorted(ENDPOINT_CLASSES):
        rows = [
            sample
            for sample in measured
            if sample.endpoint_class == name
            and (
                sample.metric_kind == "projection_lag"
                if name == "projection"
                else sample.metric_kind == "request"
            )
        ]
        values = [sample.latency_ms for sample in rows if sample.success]
        classes[name] = {
            "attempt_count": len(rows),
            "error_count": sum(not row.success for row in rows),
            "p50_ms": nearest_rank(values, 50),
            "p95_ms": nearest_rank(values, 95),
            "p99_ms": nearest_rank(values, 99),
            "success_count": sum(row.success for row in rows),
        }
    database = [sample for sample in measured_requests if sample.database_backed]
    crud = [
        sample.latency_ms
        for sample in measured_requests
        if sample.endpoint_class == "crud" and sample.success
    ]
    search = [
        sample.latency_ms
        for sample in measured_requests
        if sample.endpoint_class == "search" and sample.success
    ]
    projection = [
        sample.latency_ms
        for sample in measured
        if sample.metric_kind == "projection_lag" and sample.success
    ]
    busy_count = sum(sample.busy_failure for sample in database)
    busy_rate = 0.0 if not database else busy_count / len(database)
    canary_count = sum(sample.canary_exposure for sample in measured)
    criteria: dict[str, bool] = {
        "busy_failure_rate_lt_0_001": busy_rate < 0.001,
        "cross_space_canary_exposure_zero": canary_count == 0,
        "unhandled_error_count_zero": sum(not sample.success for sample in measured) == 0,
    }
    invariant_counts = {
        code: {
            "attempt_count": sum(
                sample.invariant_code == code for sample in measured_invariants
            ),
            "failure_count": sum(
                sample.invariant_code == code and not sample.success
                for sample in measured_invariants
            ),
        }
        for code in sorted(INVARIANT_CODES)
    }
    for code in sorted(PROFILE_INVARIANTS[profile]):
        label = code.casefold()
        criteria[f"{label}_observed"] = invariant_counts[code]["attempt_count"] > 0
        criteria[f"{label}_failure_zero"] = invariant_counts[code]["failure_count"] == 0
    if profile == "steady":
        criteria.update(
            {
                "crud_p95_le_500_ms": nearest_rank(crud, 95) is not None
                and nearest_rank(crud, 95) <= 500,
                "crud_sample_count_ge_100": len(crud) >= 100,
                "projection_p95_le_5000_ms": nearest_rank(projection, 95)
                is not None
                and nearest_rank(projection, 95) <= 5000,
                "projection_sample_count_ge_100": len(projection) >= 100,
                "search_p95_le_1500_ms": nearest_rank(search, 95) is not None
                and nearest_rank(search, 95) <= 1500,
                "search_sample_count_ge_100": len(search) >= 100,
            }
        )
    return {
        "busy_failure_count": busy_count,
        "busy_failure_rate": round(busy_rate, 9),
        "canary_exposure_count": canary_count,
        "criteria": criteria,
        "endpoint_classes": classes,
        "invariants": invariant_counts,
        "measurement_metric_count": len(measured),
        "measurement_request_count": len(measured_requests),
        "operation_error_rate": round(operation_error_rate, 9),
        "operation_failure_count": operation_failure_count,
        "operation_throughput_per_second": round(operation_throughput, 6),
        "pass": all(criteria.values()),
        "percentile_method": PERCENTILE_METHOD,
        "ramp_metric_count": len(samples) - len(measured),
        "ramp_request_count": sum(
            sample.phase == "ramp" and sample.metric_kind == "request"
            for sample in samples
        ),
        "request_throughput_per_second": round(request_throughput, 6),
    }


def _steady_schedule(seed: int) -> tuple[str, ...]:
    rows = [
        name
        for name, weight in STEADY_WEIGHTS.items()
        for _ in range(weight)
    ]
    if len(rows) != 100:
        raise AssertionError("steady weights do not total 100")
    random.Random(seed).shuffle(rows)
    return tuple(rows)


def _run_timed_client(
    *,
    session: _Session,
    fixture: LoadFixture,
    client: LoadClient,
    client_index: int,
    config: RunConfig,
    run_started: float,
    steady_start: float,
    steady_end: float,
    sample_counter: _AtomicCounter,
    projection_counter: _AtomicCounter,
    steady_schedule: tuple[str, ...],
) -> list[Sample]:
    rng = random.Random(config.seed ^ ((client_index + 1) * 0x9E3779B1))
    if config.profile == "steady" and config.clients > 1:
        activation = run_started + (
            config.ramp_seconds * (client_index / (config.clients - 1))
        )
    else:
        activation = run_started
    remaining = activation - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
    rows: list[Sample] = []
    while time.monotonic() < steady_end:
        if config.profile == "projection-burst":
            claimed = projection_counter.claim_below(
                PROFILE_CONFIG["projection-burst"]["operation_target"]
            )
            if claimed is None:
                break
            template = fixture.templates["projection_mutation"]
            sequence = sample_counter.next()
        elif config.profile == "steady":
            sequence = sample_counter.next()
            template = fixture.templates[steady_schedule[sequence % 100]]
        elif config.profile == "hot-item":
            sequence = sample_counter.next()
            template = fixture.templates["hot_item"]
        elif config.profile == "write-saturation":
            sequence = sample_counter.next()
            template = fixture.templates["write_saturation"]
        else:
            raise PerformanceLoadError("PROFILE_INVALID")
        produced, _ = _execute_operation(
            session=session,
            client=client,
            client_index=client_index,
            template=template,
            profile=config.profile,
            sequence=sequence,
            run_started=run_started,
            steady_start=steady_start,
        )
        rows.extend(produced)
        if config.profile == "steady":
            time.sleep(rng.uniform(0.250, 1.250))
    return rows


def _run_leak_client(
    *,
    session: _Session,
    fixture: LoadFixture,
    client: LoadClient,
    client_index: int,
    config: RunConfig,
    run_started: float,
    sample_counter: _AtomicCounter,
) -> list[Sample]:
    rows: list[Sample] = []
    for leak_case in client.leak_cases:
        for name in PROFILE_TEMPLATES["leak-scan"]:
            sequence = sample_counter.next()
            produced, _ = _execute_operation(
                session=session,
                client=client,
                client_index=client_index,
                template=fixture.templates[name],
                profile=config.profile,
                sequence=sequence,
                run_started=run_started,
                steady_start=run_started,
                leak_variables=leak_case.variables,
            )
            rows.extend(produced)
    return rows


def _client_binding_sha256(fixture: LoadFixture) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            [
                {"client_id": client.client_id, "user_id": client.user_id}
                for client in fixture.clients
            ]
        )
    )


def _stable_target_binding(before: Mapping[str, object], after: Mapping[str, object]) -> bool:
    stable_keys = (
        "application",
        "application_version",
        "config_sha256",
        "migration_catalog_sha256",
        "schema_migration",
        "service_id",
        "sqlite",
    )
    return all(before.get(key) == after.get(key) for key in stable_keys)


def _service_bindings_consistent(
    web: Mapping[str, object], notion: Mapping[str, object]
) -> bool:
    shared_keys = (
        "application",
        "application_version",
        "config_sha256",
        "migration_catalog_sha256",
        "schema_migration",
        "sqlite",
    )
    return (
        web.get("service_id") == "CodeAssist-Web"
        and notion.get("service_id") == "CodeAssist-Notion"
        and all(web.get(key) == notion.get(key) for key in shared_keys)
    )


def _finalize_summary(
    samples: Sequence[Sample],
    *,
    profile: str,
    health_before: Mapping[str, object],
    health_after: Mapping[str, object],
    notion_health_before: Mapping[str, object],
    notion_health_after: Mapping[str, object],
    health_observations: Sequence[Mapping[str, object]] = (),
    health_sample_errors: Sequence[str] = (),
) -> dict[str, object]:
    summary = _aggregate_samples(samples, profile=profile)
    measured_operation_keys = {
        (sample.sequence, sample.operation_class)
        for sample in samples
        if sample.phase == "measure"
    }
    operation_counts = Counter(name for _, name in measured_operation_keys)
    operation_total = sum(operation_counts.values())
    summary["operation_counts"] = dict(sorted(operation_counts.items()))
    summary["operation_count"] = operation_total
    summary["operation_percentages"] = {
        name: (
            0.0
            if operation_total == 0
            else round(100.0 * count / operation_total, 6)
        )
        for name, count in sorted(operation_counts.items())
    }
    if profile == "steady":
        observed = summary["operation_percentages"]
        assert isinstance(observed, dict)
        mix_ok = operation_total >= 100 and all(
            abs(float(observed.get(name, 0.0)) - weight) <= 1.0
            for name, weight in STEADY_WEIGHTS.items()
        )
        summary["steady_mix_within_one_percentage_point"] = mix_ok
        summary["pass"] = bool(summary["pass"]) and mix_ok
    if profile == "projection-burst":
        exact = operation_counts["projection_mutation"] == 1000
        summary["projection_mutation_count_exact_1000"] = exact
        summary["pass"] = bool(summary["pass"]) and exact
    health_rows = [health_before, *health_observations, health_after]

    def count_nonterminal(row: Mapping[str, object], section: str) -> int:
        section_value = row.get(section)
        if not isinstance(section_value, dict):
            return 0
        counts = section_value.get("state_counts")
        if not isinstance(counts, dict):
            return 0
        terminal = (
            {"cancelled", "failed", "succeeded"}
            if section == "jobs"
            else {"dead", "sent"}
        )
        return sum(
            int(count)
            for state, count in counts.items()
            if state not in terminal and isinstance(count, int) and not isinstance(count, bool)
        )

    projection_lags = []
    gates_ready = True
    for row in [*health_rows, notion_health_before, notion_health_after]:
        gate = row.get("gate")
        projection_row = row.get("projection")
        gates_ready = gates_ready and isinstance(gate, dict) and gate.get("status") == "ready"
        if isinstance(projection_row, dict) and isinstance(
            projection_row.get("lag_generation"), int
        ):
            projection_lags.append(int(projection_row["lag_generation"]))
    after_projection = health_after.get("projection")
    after_projection_ok = (
        isinstance(after_projection, dict)
        and after_projection.get("lag_generation") == 0
        and after_projection.get("error_count") == 0
    )
    operational = {
        "all_health_samples_ready": gates_ready,
        "health_sample_count": len(health_observations),
        "health_sample_error_codes": sorted(set(health_sample_errors)),
        "health_sample_minimum": max(
            1,
            (
                PROFILE_CONFIG[profile]["ramp_seconds"]
                + PROFILE_CONFIG[profile]["duration_seconds"]
                + PROFILE_CONFIG[profile]["cooldown_seconds"]
            )
            // 5,
        ),
        "job_nonterminal_peak": max(
            (count_nonterminal(row, "jobs") for row in health_rows), default=0
        ),
        "job_nonterminal_remaining": count_nonterminal(health_after, "jobs"),
        "outbox_nonterminal_peak": max(
            (count_nonterminal(row, "outbox") for row in health_rows), default=0
        ),
        "outbox_nonterminal_remaining": count_nonterminal(health_after, "outbox"),
        "projection_lag_generation_max": max(projection_lags, default=0),
        "projection_settled_after_cooldown": after_projection_ok,
    }
    before_metrics = health_before.get("database_metrics")
    after_metrics = health_after.get("database_metrics")
    metric_names = (
        "application_retry_count",
        "execute_count",
        "sqlite_busy_count",
        "sqlite_locked_count",
    )
    metric_deltas: dict[str, int] = {}
    metrics_monotonic = isinstance(before_metrics, dict) and isinstance(
        after_metrics, dict
    )
    if metrics_monotonic:
        for name in metric_names:
            before_value = before_metrics.get(name)
            after_value = after_metrics.get(name)
            if (
                isinstance(before_value, bool)
                or not isinstance(before_value, int)
                or isinstance(after_value, bool)
                or not isinstance(after_value, int)
                or after_value < before_value
            ):
                metrics_monotonic = False
                break
            metric_deltas[name] = after_value - before_value
    operational["database_metric_deltas"] = (
        metric_deltas if metrics_monotonic else None
    )
    operational["database_metrics_monotonic"] = metrics_monotonic
    operational["health_sampling_sufficient"] = (
        operational["health_sample_count"] >= operational["health_sample_minimum"]
    )
    summary["operations"] = operational
    operational_ok = (
        operational["all_health_samples_ready"] is True
        and operational["health_sample_error_codes"] == []
        and operational["health_sampling_sufficient"] is True
        and operational["database_metrics_monotonic"] is True
        and operational["job_nonterminal_remaining"] == 0
        and operational["outbox_nonterminal_remaining"] == 0
        and operational["projection_settled_after_cooldown"] is True
    )
    summary["target_binding_stable"] = _stable_target_binding(
        health_before, health_after
    ) and _stable_target_binding(notion_health_before, notion_health_after)
    summary["web_notion_binding_consistent"] = (
        _service_bindings_consistent(health_before, notion_health_before)
        and _service_bindings_consistent(health_after, notion_health_after)
    )
    summary["pass"] = (
        bool(summary["pass"])
        and bool(summary["target_binding_stable"])
        and bool(summary["web_notion_binding_consistent"])
        and operational_ok
    )
    return summary


def run_profile(
    *,
    base_url: str,
    notion_base_url: str,
    fixture: LoadFixture,
    credentials: Sequence[Credential],
    hardware: HardwareFacts,
    config: RunConfig,
    confirmation: str,
    allow_loopback_http: bool = False,
    request_timeout_seconds: int = 30,
    tls_context: ssl.SSLContext | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Execute one exact target profile and return a self-hashed report.

    This function performs network traffic.  The CLI calls it only after the
    explicit confirmation gate.  Tests may inject a TLS context and sleep
    function but cannot relax the exact profile configuration.
    """

    if confirmation != CONFIRMATION:
        raise PerformanceLoadError("TARGET_LOAD_CONFIRMATION_REQUIRED")
    if config != RunConfig.exact(config.profile, seed=config.seed):
        raise PerformanceLoadError("PROFILE_CONFIGURATION_DRIFT")
    if len(credentials) != len(fixture.clients) or len(credentials) != 40:
        raise PerformanceLoadError("CREDENTIAL_BINDING")
    if request_timeout_seconds < 1 or request_timeout_seconds > 300:
        raise PerformanceLoadError("REQUEST_TIMEOUT_INVALID")
    canonical_base, origin = _validate_base_url(
        base_url, allow_loopback_http=allow_loopback_http
    )
    canonical_notion_base, notion_origin = _validate_base_url(
        notion_base_url, allow_loopback_http=allow_loopback_http
    )
    if canonical_notion_base == canonical_base:
        raise PerformanceLoadError("TARGET_SERVICE_ENDPOINTS_COLLIDE")
    context = tls_context or ssl.create_default_context()
    health_session = _Session(
        base_url=canonical_base,
        origin=origin,
        timeout_seconds=request_timeout_seconds,
        tls_context=context,
    )
    notion_health_session = _Session(
        base_url=canonical_notion_base,
        origin=notion_origin,
        timeout_seconds=request_timeout_seconds,
        tls_context=context,
    )
    health_before = _fetch_health(health_session, require_ready=True)
    notion_health_before = _fetch_health(notion_health_session, require_ready=True)
    if not _service_bindings_consistent(health_before, notion_health_before):
        raise PerformanceLoadError("TARGET_SERVICE_BINDING_DRIFT")
    sessions = [
        _Session(
            base_url=canonical_base,
            origin=origin,
            timeout_seconds=request_timeout_seconds,
            tls_context=context,
        )
        for _ in credentials
    ]
    with ThreadPoolExecutor(max_workers=40, thread_name_prefix="perf001-auth") as pool:
        futures = [
            pool.submit(session.login, credential)
            for session, credential in zip(sessions, credentials, strict=True)
        ]
        for future in futures:
            future.result()
    started_at_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
    run_started = time.monotonic()
    steady_start = run_started + config.ramp_seconds
    steady_end = steady_start + config.duration_seconds
    sample_counter = _AtomicCounter()
    projection_counter = _AtomicCounter()
    steady_schedule = _steady_schedule(config.seed)
    health_observations: list[dict[str, object]] = []
    health_sample_errors: list[str] = []
    health_stop = threading.Event()

    def sample_health() -> None:
        while not health_stop.wait(1.0):
            try:
                observed = _fetch_health(health_session, require_ready=False)
                health_observations.append(
                    {
                        "health": observed,
                        "offset_ms": max(
                            0, int((time.monotonic() - run_started) * 1000)
                        ),
                    }
                )
            except PerformanceLoadError as exc:
                health_sample_errors.append(exc.code)

    health_thread = threading.Thread(
        target=sample_health, name="perf001-health", daemon=False
    )
    health_thread.start()
    try:
        with ThreadPoolExecutor(max_workers=40, thread_name_prefix="perf001-client") as pool:
            if config.profile == "leak-scan":
                futures = [
                    pool.submit(
                        _run_leak_client,
                        session=session,
                        fixture=fixture,
                        client=client,
                        client_index=index,
                        config=config,
                        run_started=run_started,
                        sample_counter=sample_counter,
                    )
                    for index, (session, client) in enumerate(
                        zip(sessions, fixture.clients, strict=True)
                    )
                ]
            else:
                futures = [
                    pool.submit(
                        _run_timed_client,
                        session=session,
                        fixture=fixture,
                        client=client,
                        client_index=index,
                        config=config,
                        run_started=run_started,
                        steady_start=steady_start,
                        steady_end=steady_end,
                        sample_counter=sample_counter,
                        projection_counter=projection_counter,
                        steady_schedule=steady_schedule,
                    )
                    for index, (session, client) in enumerate(
                        zip(sessions, fixture.clients, strict=True)
                    )
                ]
            sample_groups = [future.result() for future in futures]
        if config.cooldown_seconds:
            sleep(float(config.cooldown_seconds))
    finally:
        health_stop.set()
        health_thread.join(timeout=max(5, request_timeout_seconds + 2))
    if health_thread.is_alive():
        raise PerformanceLoadError("TARGET_HEALTH_SAMPLE_FAILED")
    health_after = _fetch_health(health_session, require_ready=False)
    notion_health_after = _fetch_health(notion_health_session, require_ready=False)
    completed_at_utc = datetime.now(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    elapsed_ms = max(0, int((time.monotonic() - run_started) * 1000))
    samples = sorted(
        (sample for group in sample_groups for sample in group),
        key=lambda item: (
            item.started_offset_ms,
            item.client_index,
            item.sequence,
            item.step_name,
        ),
    )
    summary = _finalize_summary(
        samples,
        profile=config.profile,
        health_before=health_before,
        health_after=health_after,
        notion_health_before=notion_health_before,
        notion_health_after=notion_health_after,
        health_observations=[row["health"] for row in health_observations],  # type: ignore[list-item]
        health_sample_errors=health_sample_errors,
    )
    target_origin_sha256 = sha256_bytes(canonical_base.encode("ascii"))
    report: dict[str, object] = {
        "configuration": {
            "clients": config.clients,
            "cooldown_seconds": config.cooldown_seconds,
            "duration_seconds": config.duration_seconds,
            "fixture_document_sha256": fixture.document_sha256,
            "fixture_id": fixture.fixture_id,
            "ramp_seconds": config.ramp_seconds,
            "seed": config.seed,
            "steady_weights": (
                dict(STEADY_WEIGHTS) if config.profile == "steady" else None
            ),
        },
        "hardware": hardware.as_dict(),
        "health_after": health_after,
        "health_before": health_before,
        "health_observations": health_observations,
        "health_sample_error_codes": sorted(set(health_sample_errors)),
        "notion_health_after": notion_health_after,
        "notion_health_before": notion_health_before,
        "notion_target_origin_sha256": sha256_bytes(
            canonical_notion_base.encode("ascii")
        ),
        "profile": config.profile,
        "report_schema": REPORT_SCHEMA,
        "samples": [sample.as_dict() for sample in samples],
        "sanitized_client_binding_sha256": _client_binding_sha256(fixture),
        "schema_version": SCHEMA_VERSION,
        "stage": "completed",
        "summary": summary,
        "target_origin_sha256": target_origin_sha256,
        "run": {
            "completed_at_utc": completed_at_utc,
            "elapsed_ms": elapsed_ms,
            "run_id": str(uuid4()),
            "started_at_utc": started_at_utc,
        },
    }
    report["report_sha256"] = sha256_bytes(canonical_json_bytes(report))
    return report


def _csv_bytes(report: Mapping[str, object]) -> bytes:
    rows = report.get("samples")
    if not isinstance(rows, list):
        raise PerformanceLoadError("REPORT_INVALID")
    fields = (
        "sequence",
        "client_index",
        "phase",
        "profile",
        "operation_class",
        "step_name",
        "endpoint_class",
        "metric_kind",
        "started_offset_ms",
        "latency_ms",
        "invariant_code",
        "status",
        "success",
        "error_code",
        "database_backed",
        "busy_failure",
        "canary_exposure",
    )
    lines = [",".join(fields)]
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(fields):
            raise PerformanceLoadError("REPORT_INVALID")
        values: list[str] = []
        for field_name in fields:
            value = row[field_name]
            if value is None:
                text = ""
            elif isinstance(value, bool):
                text = "true" if value else "false"
            else:
                text = str(value)
            if any(character in text for character in ',"\r\n'):
                text = '"' + text.replace('"', '""') + '"'
            values.append(text)
        lines.append(",".join(values))
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _sample_from_mapping(value: object, *, ramp_seconds: int, profile: str) -> Sample:
    if not isinstance(value, dict):
        raise PerformanceLoadError("REPORT_SAMPLE_SHAPE")
    expected = {
        "busy_failure",
        "canary_exposure",
        "client_index",
        "database_backed",
        "endpoint_class",
        "error_code",
        "latency_ms",
        "invariant_code",
        "metric_kind",
        "operation_class",
        "phase",
        "profile",
        "sequence",
        "started_offset_ms",
        "status",
        "step_name",
        "success",
    }
    _exact_keys(value, expected, "REPORT_SAMPLE_SHAPE")
    client_index = _nonnegative_int(value.get("client_index"), code="REPORT_SAMPLE_SHAPE")
    sequence = _nonnegative_int(value.get("sequence"), code="REPORT_SAMPLE_SHAPE")
    started_offset = _nonnegative_int(
        value.get("started_offset_ms"), code="REPORT_SAMPLE_SHAPE"
    )
    status = _positive_int(value.get("status"), code="REPORT_SAMPLE_SHAPE")
    latency = value.get("latency_ms")
    if (
        isinstance(latency, bool)
        or not isinstance(latency, (int, float))
        or not math.isfinite(float(latency))
        or float(latency) < 0
        or client_index >= 40
        or status > 599
    ):
        raise PerformanceLoadError("REPORT_SAMPLE_SHAPE")
    phase = value.get("phase")
    expected_phase = "ramp" if started_offset < ramp_seconds * 1000 else "measure"
    endpoint_class = value.get("endpoint_class")
    metric_kind = value.get("metric_kind")
    operation_class = value.get("operation_class")
    step_name = value.get("step_name")
    error_code = value.get("error_code")
    invariant_code = value.get("invariant_code")
    if (
        phase != expected_phase
        or value.get("profile") != profile
        or endpoint_class not in ENDPOINT_CLASSES
        or metric_kind not in METRIC_KINDS
        or not isinstance(operation_class, str)
        or operation_class not in PROFILE_TEMPLATES[profile]
        or not isinstance(step_name, str)
        or _ID_RE.fullmatch(
            step_name.removesuffix(".lag").removesuffix(".assert")
        )
        is None
        or (
            error_code is not None
            and (
                not isinstance(error_code, str)
                or re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", error_code, re.ASCII)
                is None
            )
        )
        or (invariant_code is not None and invariant_code not in INVARIANT_CODES)
        or not all(
            isinstance(value.get(key), bool)
            for key in (
                "busy_failure",
                "canary_exposure",
                "database_backed",
                "success",
            )
        )
    ):
        raise PerformanceLoadError("REPORT_SAMPLE_SHAPE")
    if metric_kind == "projection_lag" and (
        endpoint_class != "projection"
        or value.get("database_backed") is not False
        or not step_name.endswith(".lag")
    ):
        raise PerformanceLoadError("REPORT_SAMPLE_SHAPE")
    if metric_kind == "request" and step_name.endswith((".assert", ".lag")):
        raise PerformanceLoadError("REPORT_SAMPLE_SHAPE")
    if metric_kind == "invariant" and (
        invariant_code not in INVARIANT_CODES
        or not step_name.endswith(".assert")
        or value.get("database_backed") is not False
        or value.get("canary_exposure") is not False
        or value.get("busy_failure") is not False
        or (value.get("success") is True and error_code is not None)
        or (value.get("success") is False and error_code != invariant_code)
    ):
        raise PerformanceLoadError("REPORT_SAMPLE_SHAPE")
    if metric_kind != "invariant" and invariant_code is not None:
        raise PerformanceLoadError("REPORT_SAMPLE_SHAPE")
    busy = bool(value.get("busy_failure"))
    if busy and (
        value.get("database_backed") is not True
        or error_code not in {"SQLITE_BUSY", "SQLITE_LOCKED"}
    ):
        raise PerformanceLoadError("REPORT_SAMPLE_SHAPE")
    return Sample(
        sequence=sequence,
        client_index=client_index,
        phase=str(phase),
        profile=profile,
        operation_class=operation_class,
        step_name=step_name,
        endpoint_class=str(endpoint_class),
        metric_kind=str(metric_kind),
        started_offset_ms=started_offset,
        latency_ms=float(latency),
        status=status,
        success=bool(value.get("success")),
        error_code=error_code,
        database_backed=bool(value.get("database_backed")),
        busy_failure=busy,
        canary_exposure=bool(value.get("canary_exposure")),
        invariant_code=invariant_code,
    )


def _valid_selected_health(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "application",
        "application_version",
        "config_sha256",
        "database_metrics",
        "gate",
        "jobs",
        "migration_catalog_sha256",
        "outbox",
        "projection",
        "schema_migration",
        "service_id",
        "sqlite",
    }:
        return False
    if value.get("application") != "code-assist-v1":
        return False
    for key in ("application_version", "service_id"):
        item = value.get(key)
        if not isinstance(item, str) or not item or len(item) > 256:
            return False
    for key in ("config_sha256", "migration_catalog_sha256"):
        if not isinstance(value.get(key), str) or _SHA256_RE.fullmatch(value[key]) is None:  # type: ignore[arg-type]
            return False
    metrics = value.get("database_metrics")
    gate = value.get("gate")
    jobs = value.get("jobs")
    outbox = value.get("outbox")
    projection = value.get("projection")
    migration = value.get("schema_migration")
    sqlite_row = value.get("sqlite")
    if not all(
        isinstance(row, dict)
        for row in (metrics, gate, jobs, outbox, projection, migration, sqlite_row)
    ):
        return False
    assert isinstance(metrics, dict) and isinstance(projection, dict)
    assert isinstance(gate, dict) and isinstance(jobs, dict) and isinstance(outbox, dict)
    assert isinstance(migration, dict) and isinstance(sqlite_row, dict)
    if set(metrics) != {
        "application_retry_count",
        "execute_count",
        "sqlite_busy_count",
        "sqlite_locked_count",
    } or any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0
        for item in metrics.values()
    ):
        return False
    if (
        set(gate) != {"reason_codes", "status"}
        or gate.get("status") not in {"ready", "not_ready"}
        or not isinstance(gate.get("reason_codes"), list)
        or any(
            not isinstance(item, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{1,127}", item, re.ASCII) is None
            for item in gate["reason_codes"]
        )
        or gate["reason_codes"] != sorted(set(gate["reason_codes"]))
        or (gate["status"] == "ready") != (gate["reason_codes"] == [])
    ):
        return False
    for row, expected_keys in (
        (
            jobs,
            {
                "attempt_count",
                "oldest_nonterminal_age_ms",
                "retry_count",
                "state_counts",
                "unrecoverable_count",
            },
        ),
        (
            outbox,
            {
                "attempt_count",
                "oldest_nonterminal_age_ms",
                "retry_count",
                "state_counts",
            },
        ),
    ):
        if set(row) != expected_keys or not isinstance(row.get("state_counts"), dict):
            return False
        for key, item in row.items():
            if key == "state_counts":
                if any(
                    not isinstance(state, str)
                    or isinstance(count, bool)
                    or not isinstance(count, int)
                    or count < 0
                    for state, count in item.items()  # type: ignore[union-attr]
                ):
                    return False
            elif key == "oldest_nonterminal_age_ms" and item is None:
                continue
            elif isinstance(item, bool) or not isinstance(item, int) or item < 0:
                return False
    if set(projection) != {
        "built_generation",
        "desired_generation",
        "error_count",
        "lag_generation",
        "lagging_count",
    } or any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0
        for item in projection.values()
    ):
        return False
    if set(migration) != {"checksum_sha256", "name", "version"}:
        return False
    if (
        not isinstance(migration.get("checksum_sha256"), str)
        or _SHA256_RE.fullmatch(migration["checksum_sha256"]) is None  # type: ignore[arg-type]
        or not isinstance(migration.get("name"), str)
        or not migration["name"]
        or isinstance(migration.get("version"), bool)
        or not isinstance(migration.get("version"), int)
        or migration["version"] < 1
    ):
        return False
    if set(sqlite_row) != {"source_id", "version"} or any(
        not isinstance(sqlite_row.get(key), str) or not sqlite_row[key]
        for key in ("source_id", "version")
    ):
        return False
    return True


def performance_report_errors(
    value: object,
    *,
    csv_bytes: bytes | None,
    fixture: LoadFixture,
) -> tuple[str, ...]:
    """Return deterministic contract errors for any JSON-compatible value."""

    errors: set[str] = set()
    try:
        if not isinstance(value, dict):
            return ("report_shape",)
        expected = {
            "configuration",
            "csv",
            "hardware",
            "health_after",
            "health_before",
            "health_observations",
            "health_sample_error_codes",
            "notion_health_after",
            "notion_health_before",
            "notion_target_origin_sha256",
            "profile",
            "report_schema",
            "report_sha256",
            "run",
            "samples",
            "sanitized_client_binding_sha256",
            "schema_version",
            "stage",
            "summary",
            "target_origin_sha256",
        }
        if set(value) != expected:
            errors.add("report_shape")
        if (
            value.get("report_schema") != REPORT_SCHEMA
            or value.get("schema_version") != 1
            or value.get("stage") != "completed"
            or value.get("profile") not in PROFILES
        ):
            errors.add("report_identity")
        profile = value.get("profile")
        if profile not in PROFILES:
            return tuple(sorted(errors))
        assert isinstance(profile, str)
        config = value.get("configuration")
        if not isinstance(config, dict) or set(config) != {
            "clients",
            "cooldown_seconds",
            "duration_seconds",
            "fixture_document_sha256",
            "fixture_id",
            "ramp_seconds",
            "seed",
            "steady_weights",
        }:
            errors.add("configuration_shape")
            return tuple(sorted(errors))
        exact = PROFILE_CONFIG[profile]
        if any(config.get(key) != exact[key] for key in (
            "clients", "cooldown_seconds", "duration_seconds", "ramp_seconds"
        )):
            errors.add("configuration_binding")
        if (
            config.get("fixture_document_sha256") != fixture.document_sha256
            or config.get("fixture_id") != fixture.fixture_id
            or value.get("sanitized_client_binding_sha256")
            != _client_binding_sha256(fixture)
        ):
            errors.add("fixture_binding")
        seed = config.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0 or seed > 0xFFFFFFFF:
            errors.add("configuration_binding")
        if (
            not isinstance(config.get("fixture_document_sha256"), str)
            or _SHA256_RE.fullmatch(config["fixture_document_sha256"]) is None  # type: ignore[arg-type]
            or not isinstance(config.get("fixture_id"), str)
            or _ID_RE.fullmatch(config["fixture_id"]) is None  # type: ignore[arg-type]
            or config.get("steady_weights")
            != (dict(STEADY_WEIGHTS) if profile == "steady" else None)
        ):
            errors.add("configuration_binding")
        for key in (
            "notion_target_origin_sha256",
            "sanitized_client_binding_sha256",
            "target_origin_sha256",
        ):
            item = value.get(key)
            if not isinstance(item, str) or _SHA256_RE.fullmatch(item) is None:
                errors.add("binding_shape")
        if value.get("target_origin_sha256") == value.get(
            "notion_target_origin_sha256"
        ):
            errors.add("service_binding")
        hardware = value.get("hardware")
        if not isinstance(hardware, dict) or set(hardware) != {
            "cpu_model", "document_sha256", "gpu_memory_bytes", "gpu_model",
            "logical_cpu_count", "os_name", "os_version", "ram_bytes",
            "server_id", "storage_model",
        }:
            errors.add("hardware_shape")
        else:
            for key in ("cpu_model", "gpu_model", "os_name", "os_version", "server_id", "storage_model"):
                item = hardware.get(key)
                if not isinstance(item, str) or not item or len(item) > 512:
                    errors.add("hardware_shape")
            for key in ("gpu_memory_bytes", "logical_cpu_count", "ram_bytes"):
                item = hardware.get(key)
                if isinstance(item, bool) or not isinstance(item, int) or item < 1:
                    errors.add("hardware_shape")
            if not isinstance(hardware.get("document_sha256"), str) or _SHA256_RE.fullmatch(hardware["document_sha256"]) is None:  # type: ignore[arg-type]
                errors.add("hardware_shape")
            else:
                hardware_document = {
                    "cpu_model": hardware.get("cpu_model"),
                    "gpu_memory_bytes": hardware.get("gpu_memory_bytes"),
                    "gpu_model": hardware.get("gpu_model"),
                    "hardware_schema": HARDWARE_SCHEMA,
                    "logical_cpu_count": hardware.get("logical_cpu_count"),
                    "os_name": hardware.get("os_name"),
                    "os_version": hardware.get("os_version"),
                    "ram_bytes": hardware.get("ram_bytes"),
                    "schema_version": SCHEMA_VERSION,
                    "server_id": hardware.get("server_id"),
                    "storage_model": hardware.get("storage_model"),
                }
                if hardware["document_sha256"] != sha256_bytes(
                    canonical_json_bytes(hardware_document)
                ):
                    errors.add("hardware_binding")
        before = value.get("health_before")
        after = value.get("health_after")
        notion_before = value.get("notion_health_before")
        notion_after = value.get("notion_health_after")
        if (
            not _valid_selected_health(before)
            or not _valid_selected_health(after)
            or not _valid_selected_health(notion_before)
            or not _valid_selected_health(notion_after)
        ):
            errors.add("health_shape")
        elif not (
            isinstance(before, dict)
            and isinstance(after, dict)
            and isinstance(notion_before, dict)
            and isinstance(notion_after, dict)
            and _stable_target_binding(before, after)
            and _stable_target_binding(notion_before, notion_after)
            and _service_bindings_consistent(before, notion_before)
            and _service_bindings_consistent(after, notion_after)
        ):
            errors.add("service_binding")
        observation_rows = value.get("health_observations")
        observations: list[Mapping[str, object]] = []
        prior_offset = -1
        if not isinstance(observation_rows, list) or len(observation_rows) > 100_000:
            errors.add("health_observations_shape")
        else:
            for row in observation_rows:
                if (
                    not isinstance(row, dict)
                    or set(row) != {"health", "offset_ms"}
                    or isinstance(row.get("offset_ms"), bool)
                    or not isinstance(row.get("offset_ms"), int)
                    or row["offset_ms"] < 0
                    or row["offset_ms"] < prior_offset
                    or not _valid_selected_health(row.get("health"))
                ):
                    errors.add("health_observations_shape")
                    break
                prior_offset = row["offset_ms"]
                observations.append(row["health"])  # type: ignore[arg-type]
        health_errors = value.get("health_sample_error_codes")
        if (
            not isinstance(health_errors, list)
            or health_errors != sorted(set(health_errors))
            or any(
                not isinstance(item, str)
                or re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", item, re.ASCII) is None
                for item in health_errors
            )
        ):
            errors.add("health_observations_shape")
            health_errors = []
        rows = value.get("samples")
        samples: list[Sample] = []
        if not isinstance(rows, list) or len(rows) > 10_000_000:
            errors.add("samples_shape")
        else:
            for row in rows:
                try:
                    samples.append(
                        _sample_from_mapping(
                            row,
                            ramp_seconds=exact["ramp_seconds"],
                            profile=profile,
                        )
                    )
                except PerformanceLoadError:
                    errors.add("samples_shape")
                    break
            for sample in samples:
                template = fixture.templates.get(sample.operation_class)
                if template is None:
                    errors.add("fixture_sample_binding")
                    break
                base_step_name = sample.step_name.removesuffix(".lag").removesuffix(
                    ".assert"
                )
                step = next(
                    (item for item in template.steps if item.name == base_step_name),
                    None,
                )
                if step is None:
                    errors.add("fixture_sample_binding")
                    break
                if sample.metric_kind == "request":
                    expected_success = (
                        sample.status in step.expected_status
                        and not sample.canary_exposure
                    )
                    if (
                        sample.endpoint_class != step.endpoint_class
                        or sample.database_backed != step.database_backed
                        or sample.success != expected_success
                    ):
                        errors.add("fixture_sample_binding")
                        break
                elif sample.metric_kind == "projection_lag":
                    if step.poll is None:
                        errors.add("fixture_sample_binding")
                        break
                else:
                    if (
                        sample.invariant_code
                        not in {assertion.code for assertion in step.assertions}
                        or sample.endpoint_class != step.endpoint_class
                    ):
                        errors.add("fixture_sample_binding")
                        break
                if profile == "steady" and sample.metric_kind == "request":
                    scheduled = _steady_schedule(int(config["seed"]))[
                        sample.sequence % 100
                    ]
                    if sample.operation_class != scheduled:
                        errors.add("fixture_sample_binding")
                        break
            if samples and [sample.as_dict() for sample in samples] != rows:
                errors.add("samples_canonical")
            if samples != sorted(
                samples,
                key=lambda item: (
                    item.started_offset_ms,
                    item.client_index,
                    item.sequence,
                    item.step_name,
                ),
            ):
                errors.add("samples_order")
        if (
            samples
            and isinstance(before, dict)
            and isinstance(after, dict)
            and isinstance(notion_before, dict)
            and isinstance(notion_after, dict)
        ):
            expected_summary = _finalize_summary(
                samples,
                profile=profile,
                health_before=before,
                health_after=after,
                notion_health_before=notion_before,
                notion_health_after=notion_after,
                health_observations=observations,
                health_sample_errors=health_errors,
            )
            if value.get("summary") != expected_summary:
                errors.add("summary_binding")
        else:
            errors.add("summary_binding")
        csv_row = value.get("csv")
        if not isinstance(csv_row, dict) or set(csv_row) != {"filename", "sha256"}:
            errors.add("csv_shape")
        else:
            filename = csv_row.get("filename")
            digest = csv_row.get("sha256")
            if (
                not isinstance(filename, str)
                or re.fullmatch(r"[A-Za-z0-9_.-]{1,255}\.csv", filename, re.ASCII) is None
                or not isinstance(digest, str)
                or _SHA256_RE.fullmatch(digest) is None
            ):
                errors.add("csv_shape")
            if csv_bytes is None or digest != sha256_bytes(csv_bytes):
                errors.add("csv_binding")
            elif isinstance(rows, list):
                try:
                    if _csv_bytes({"samples": rows}) != csv_bytes:
                        errors.add("csv_binding")
                except PerformanceLoadError:
                    errors.add("csv_binding")
        report_hash = value.get("report_sha256")
        payload = dict(value)
        payload.pop("report_sha256", None)
        if (
            not isinstance(report_hash, str)
            or _SHA256_RE.fullmatch(report_hash) is None
            or report_hash != sha256_bytes(canonical_json_bytes(payload))
        ):
            errors.add("report_hash")
        run = value.get("run")
        if not isinstance(run, dict) or set(run) != {
            "completed_at_utc", "elapsed_ms", "run_id", "started_at_utc"
        }:
            errors.add("run_shape")
        else:
            try:
                started_text = run["started_at_utc"]
                completed_text = run["completed_at_utc"]
                if (
                    not isinstance(started_text, str)
                    or not isinstance(completed_text, str)
                    or re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z",
                        started_text,
                        re.ASCII,
                    )
                    is None
                    or re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z",
                        completed_text,
                        re.ASCII,
                    )
                    is None
                ):
                    raise ValueError("invalid UTC")
                started_dt = datetime.fromisoformat(started_text[:-1] + "+00:00")
                completed_dt = datetime.fromisoformat(completed_text[:-1] + "+00:00")
                elapsed = run.get("elapsed_ms")
                minimum_elapsed = (
                    exact["ramp_seconds"]
                    + exact["duration_seconds"]
                    + exact["cooldown_seconds"]
                ) * 1000
                if (
                    completed_dt < started_dt
                    or isinstance(elapsed, bool)
                    or not isinstance(elapsed, int)
                    or elapsed < minimum_elapsed
                    or not isinstance(run.get("run_id"), str)
                    or re.fullmatch(
                        r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                        run["run_id"],
                        re.ASCII,
                    )
                    is None
                ):
                    raise ValueError("invalid run")
                if observation_rows and isinstance(observation_rows, list):
                    if any(
                        isinstance(row, dict)
                        and isinstance(row.get("offset_ms"), int)
                        and row["offset_ms"] > elapsed
                        for row in observation_rows
                    ):
                        raise ValueError("health observation after run")
            except (KeyError, TypeError, ValueError):
                errors.add("run_shape")
    except (KeyError, TypeError, ValueError, OverflowError):
        errors.add("validator_totality")
    return tuple(sorted(errors))


def parse_performance_report(raw: bytes) -> Mapping[str, object]:
    return _parse_json_document(
        raw,
        code="REPORT_INVALID",
        maximum_bytes=_MAX_REPORT_BYTES,
    )


def _gate_common_binding(report: Mapping[str, object]) -> dict[str, object]:
    configuration = report.get("configuration")
    hardware = report.get("hardware")
    health = report.get("health_before")
    notion_health = report.get("notion_health_before")
    if not isinstance(configuration, dict) or not isinstance(hardware, dict):
        raise PerformanceLoadError("GATE_REPORT_INVALID")
    if not isinstance(health, dict) or not _valid_selected_health(health):
        raise PerformanceLoadError("GATE_REPORT_INVALID")
    if not isinstance(notion_health, dict) or not _valid_selected_health(notion_health):
        raise PerformanceLoadError("GATE_REPORT_INVALID")
    if not _service_bindings_consistent(health, notion_health):
        raise PerformanceLoadError("GATE_REPORT_INVALID")
    schema_migration = health.get("schema_migration")
    sqlite_row = health.get("sqlite")
    if not isinstance(schema_migration, dict) or not isinstance(sqlite_row, dict):
        raise PerformanceLoadError("GATE_REPORT_INVALID")
    return {
        "application": health["application"],
        "application_version": health["application_version"],
        "config_sha256": health["config_sha256"],
        "fixture_document_sha256": configuration["fixture_document_sha256"],
        "fixture_id": configuration["fixture_id"],
        "hardware_document_sha256": hardware["document_sha256"],
        "migration_catalog_sha256": health["migration_catalog_sha256"],
        "notion_service_id": notion_health["service_id"],
        "notion_target_origin_sha256": report["notion_target_origin_sha256"],
        "sanitized_client_binding_sha256": report[
            "sanitized_client_binding_sha256"
        ],
        "schema_migration": dict(schema_migration),
        "seed": configuration["seed"],
        "server_id": hardware["server_id"],
        "service_id": health["service_id"],
        "sqlite": dict(sqlite_row),
        "target_origin_sha256": report["target_origin_sha256"],
    }


def _valid_gate_common_binding(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "application",
        "application_version",
        "config_sha256",
        "fixture_document_sha256",
        "fixture_id",
        "hardware_document_sha256",
        "migration_catalog_sha256",
        "notion_service_id",
        "notion_target_origin_sha256",
        "sanitized_client_binding_sha256",
        "schema_migration",
        "seed",
        "server_id",
        "service_id",
        "sqlite",
        "target_origin_sha256",
    }:
        return False
    if value.get("application") != "code-assist-v1":
        return False
    for key in (
        "application_version",
        "fixture_id",
        "server_id",
        "service_id",
        "notion_service_id",
    ):
        item = value.get(key)
        if not isinstance(item, str) or not item or len(item) > 512:
            return False
    for key in (
        "config_sha256",
        "fixture_document_sha256",
        "hardware_document_sha256",
        "migration_catalog_sha256",
        "notion_target_origin_sha256",
        "sanitized_client_binding_sha256",
        "target_origin_sha256",
    ):
        item = value.get(key)
        if not isinstance(item, str) or _SHA256_RE.fullmatch(item) is None:
            return False
    if value.get("service_id") != "CodeAssist-Web" or value.get(
        "notion_service_id"
    ) != "CodeAssist-Notion":
        return False
    if value.get("target_origin_sha256") == value.get("notion_target_origin_sha256"):
        return False
    seed = value.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 0xFFFFFFFF:
        return False
    migration = value.get("schema_migration")
    sqlite_row = value.get("sqlite")
    if not isinstance(migration, dict) or set(migration) != {
        "checksum_sha256", "name", "version"
    }:
        return False
    if (
        not isinstance(migration.get("checksum_sha256"), str)
        or _SHA256_RE.fullmatch(migration["checksum_sha256"]) is None  # type: ignore[arg-type]
        or not isinstance(migration.get("name"), str)
        or not migration["name"]
        or isinstance(migration.get("version"), bool)
        or not isinstance(migration.get("version"), int)
        or migration["version"] < 1
    ):
        return False
    return bool(
        isinstance(sqlite_row, dict)
        and set(sqlite_row) == {"source_id", "version"}
        and all(
            isinstance(sqlite_row.get(key), str) and bool(sqlite_row[key])
            for key in ("source_id", "version")
        )
    )


def build_performance_gate(
    report_pairs: Sequence[tuple[Mapping[str, object], bytes]],
    *,
    fixture: LoadFixture,
) -> dict[str, object]:
    """Bind the five independently verified PERF-001 profile reports."""

    reports: dict[str, tuple[Mapping[str, object], bytes]] = {}
    common_binding: dict[str, object] | None = None
    for report, csv_payload in report_pairs:
        errors = performance_report_errors(
            report, csv_bytes=csv_payload, fixture=fixture
        )
        if errors:
            raise PerformanceLoadError("GATE_REPORT_INVALID")
        profile = report.get("profile")
        if not isinstance(profile, str) or profile not in PROFILES or profile in reports:
            raise PerformanceLoadError("GATE_PROFILE_SET_INVALID")
        binding = _gate_common_binding(report)
        if common_binding is None:
            common_binding = binding
        elif binding != common_binding:
            raise PerformanceLoadError("GATE_TARGET_BINDING_DRIFT")
        reports[profile] = (report, csv_payload)
    if set(reports) != set(PROFILES) or common_binding is None:
        raise PerformanceLoadError("GATE_PROFILE_SET_INVALID")

    profile_rows: list[dict[str, object]] = []
    for profile in PROFILE_ORDER:
        report, _csv_payload = reports[profile]
        summary = report["summary"]
        csv_row = report["csv"]
        run = report["run"]
        assert isinstance(summary, dict) and isinstance(csv_row, dict)
        assert isinstance(run, dict)
        profile_rows.append(
            {
                "csv_sha256": csv_row["sha256"],
                "passed": summary.get("pass") is True,
                "profile": profile,
                "report_sha256": report["report_sha256"],
                "run_id": run["run_id"],
            }
        )
    passed_count = sum(row["passed"] is True for row in profile_rows)
    gate: dict[str, object] = {
        "common_binding": common_binding,
        "gate_schema": GATE_SCHEMA,
        "profiles": profile_rows,
        "schema_version": SCHEMA_VERSION,
        "stage": "verified",
        "summary": {
            "all_profiles_pass": passed_count == len(PROFILE_ORDER),
            "profile_count": len(profile_rows),
            "profiles_passed": passed_count,
        },
    }
    gate["gate_sha256"] = sha256_bytes(canonical_json_bytes(gate))
    return gate


def performance_gate_errors(value: object) -> tuple[str, ...]:
    """Return deterministic structural errors for a PERF-001 gate artifact."""

    errors: set[str] = set()
    try:
        if not isinstance(value, dict):
            return ("gate_shape",)
        if set(value) != {
            "common_binding",
            "gate_schema",
            "gate_sha256",
            "profiles",
            "schema_version",
            "stage",
            "summary",
        }:
            errors.add("gate_shape")
        if (
            value.get("gate_schema") != GATE_SCHEMA
            or value.get("schema_version") != SCHEMA_VERSION
            or value.get("stage") != "verified"
        ):
            errors.add("gate_identity")
        if not _valid_gate_common_binding(value.get("common_binding")):
            errors.add("gate_common_binding")
        rows = value.get("profiles")
        normalized_rows: list[dict[str, object]] = []
        if not isinstance(rows, list) or len(rows) != len(PROFILE_ORDER):
            errors.add("gate_profiles")
        else:
            for row in rows:
                if not isinstance(row, dict) or set(row) != {
                    "csv_sha256", "passed", "profile", "report_sha256", "run_id"
                }:
                    errors.add("gate_profiles")
                    break
                if (
                    row.get("profile") not in PROFILES
                    or not isinstance(row.get("passed"), bool)
                    or not isinstance(row.get("csv_sha256"), str)
                    or _SHA256_RE.fullmatch(row["csv_sha256"]) is None  # type: ignore[arg-type]
                    or not isinstance(row.get("report_sha256"), str)
                    or _SHA256_RE.fullmatch(row["report_sha256"]) is None  # type: ignore[arg-type]
                    or not isinstance(row.get("run_id"), str)
                    or re.fullmatch(
                        r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                        row["run_id"],  # type: ignore[arg-type]
                        re.ASCII,
                    )
                    is None
                ):
                    errors.add("gate_profiles")
                    break
                normalized_rows.append(row)
            if [row.get("profile") for row in normalized_rows] != list(PROFILE_ORDER):
                errors.add("gate_profiles")
            if len({row.get("report_sha256") for row in normalized_rows}) != len(
                PROFILE_ORDER
            ) or len({row.get("run_id") for row in normalized_rows}) != len(
                PROFILE_ORDER
            ):
                errors.add("gate_profiles")
        passed_count = sum(row.get("passed") is True for row in normalized_rows)
        expected_summary = {
            "all_profiles_pass": passed_count == len(PROFILE_ORDER),
            "profile_count": len(normalized_rows),
            "profiles_passed": passed_count,
        }
        if value.get("summary") != expected_summary:
            errors.add("gate_summary")
        payload = dict(value)
        gate_hash = payload.pop("gate_sha256", None)
        if (
            not isinstance(gate_hash, str)
            or _SHA256_RE.fullmatch(gate_hash) is None
            or gate_hash != sha256_bytes(canonical_json_bytes(payload))
        ):
            errors.add("gate_hash")
    except (KeyError, TypeError, ValueError, OverflowError):
        errors.add("gate_validator_totality")
    return tuple(sorted(errors))


def _write_new(path: Path, payload: bytes) -> None:
    if not path.is_absolute() or not path.parent.is_dir() or path.name in {"", ".", ".."}:
        raise PerformanceLoadError("OUTPUT_PATH_INVALID")
    if os.name == "nt":
        from code_assist_v1.tools.legacy_inventory import (
            _close_windows_handle,
            _nt_clear_disposition,
            _nt_set_disposition,
            _observe_output_parent,
            _pinned_directory,
            _validate_simple_windows_component,
            _windows_final_path,
            _write_windows_handle,
        )

        try:
            _validate_simple_windows_component(path.name)
            parent_resolved, parent_identity = _observe_output_parent(path.parent)
            with _pinned_directory(path.parent) as anchor:
                observed_parent, observed_identity = _observe_output_parent(path.parent)
                if (
                    observed_parent != parent_resolved
                    or observed_identity != parent_identity
                ):
                    raise OSError("output_parent_identity_changed")
                handle: int | None = None
                keep = False
                try:
                    handle = anchor.create_relative_file(path.name)
                    # Keep the new file delete-pending until all bytes are
                    # flushed.  Another process cannot rename or hard-link a
                    # partially written evidence artifact into another tree.
                    _nt_set_disposition(handle)
                    _write_windows_handle(handle, payload)
                    _nt_clear_disposition(handle)
                    if os.path.normcase(str(_windows_final_path(handle))) != os.path.normcase(
                        str(path.resolve(strict=True))
                    ):
                        raise OSError("output_final_path_changed")
                    keep = True
                finally:
                    if handle is not None:
                        if not keep:
                            try:
                                _nt_set_disposition(handle)
                            except OSError:
                                pass
                        _close_windows_handle(handle)
            return
        except OSError as exc:
            code = getattr(exc, "winerror", None)
            if code in {80, 183} or getattr(exc, "errno", None) in {17, 80, 183}:
                raise PerformanceLoadError("OUTPUT_EXISTS") from exc
            raise PerformanceLoadError("OUTPUT_WRITE_FAILED") from exc
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise PerformanceLoadError("OUTPUT_EXISTS") from exc
    except OSError as exc:
        raise PerformanceLoadError("OUTPUT_WRITE_FAILED") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def write_report_pair(
    output: Path,
    report: Mapping[str, object],
    *,
    fixture: LoadFixture | None = None,
) -> tuple[Path, Path]:
    if output.suffix.casefold() != ".json":
        raise PerformanceLoadError("OUTPUT_PATH_INVALID")
    csv_path = output.with_suffix(".csv")
    if output.exists() or csv_path.exists():
        raise PerformanceLoadError("OUTPUT_EXISTS")
    csv_payload = _csv_bytes(report)
    json_document = dict(report)
    json_document["csv"] = {
        "filename": csv_path.name,
        "sha256": sha256_bytes(csv_payload),
    }
    old_hash = json_document.pop("report_sha256", None)
    if not isinstance(old_hash, str) or _SHA256_RE.fullmatch(old_hash) is None:
        raise PerformanceLoadError("REPORT_INVALID")
    json_document["report_sha256"] = sha256_bytes(canonical_json_bytes(json_document))
    if fixture is not None:
        errors = performance_report_errors(
            json_document, csv_bytes=csv_payload, fixture=fixture
        )
        if errors:
            raise PerformanceLoadError("REPORT_INVALID")
    # A CSV orphan is not a completed report.  The authoritative JSON is
    # created last and binds the exact CSV bytes.
    _write_new(csv_path, csv_payload)
    # JSON is the authoritative completion marker and is created last.  A
    # crash may leave a hash-addressed CSV orphan, but path-based cleanup after
    # releasing the anchored handle could delete an exchanged file.
    _write_new(output, canonical_json_bytes(json_document))
    return output, csv_path


def _read_file(
    path: Path, *, code: str, maximum_bytes: int = _MAX_JSON_BYTES
) -> bytes:
    if os.name == "nt":
        try:
            from code_assist_v1.knowledge_core.cli import _read_bound_file

            return _read_bound_file(str(path), maximum_bytes=maximum_bytes)
        except (OSError, OverflowError, ValueError) as exc:
            raise PerformanceLoadError(code) from exc
    try:
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise OSError("ineligible input")
        if path.stat().st_size > maximum_bytes:
            raise OSError("input too large")
        value = path.read_bytes()
        if len(value) > maximum_bytes:
            raise OSError("input too large")
        return value
    except OSError as exc:
        raise PerformanceLoadError(code) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_load.py")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--notion-base-url", required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--hardware", type=Path, required=True)
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--clients", type=int, required=True)
    parser.add_argument("--ramp-seconds", type=int, required=True)
    parser.add_argument("--duration-seconds", type=int, required=True)
    parser.add_argument("--cooldown-seconds", type=int, default=60)
    parser.add_argument("--seed", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-target-load", default="")
    parser.add_argument("--allow-loopback-http", action="store_true")
    parser.add_argument("--request-timeout-seconds", type=int, default=30)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        fixture = parse_fixture(_read_file(args.fixture, code="FIXTURE_INVALID"))
        hardware = parse_hardware(_read_file(args.hardware, code="HARDWARE_INVALID"))
        config = RunConfig(
            profile=args.profile,
            clients=args.clients,
            ramp_seconds=args.ramp_seconds,
            duration_seconds=args.duration_seconds,
            cooldown_seconds=args.cooldown_seconds,
            seed=args.seed,
        )
        if config != RunConfig.exact(config.profile, seed=config.seed):
            raise PerformanceLoadError("PROFILE_CONFIGURATION_DRIFT")
        web_origin, _ = _validate_base_url(
            args.base_url, allow_loopback_http=args.allow_loopback_http
        )
        notion_origin, _ = _validate_base_url(
            args.notion_base_url, allow_loopback_http=args.allow_loopback_http
        )
        if web_origin == notion_origin:
            raise PerformanceLoadError("TARGET_SERVICE_ENDPOINTS_COLLIDE")
        if not args.validate_only and args.confirm_target_load != CONFIRMATION:
            # Refuse intended traffic before touching the secret-bearing
            # credential document.  Validation-only remains an explicitly
            # non-network path and therefore validates the complete input set.
            raise PerformanceLoadError("TARGET_LOAD_CONFIRMATION_REQUIRED")
        credentials = parse_credentials(
            _read_file(args.credentials, code="CREDENTIALS_INVALID"), fixture
        )
        if args.validate_only:
            result = {
                "client_count": len(fixture.clients),
                "fixture_document_sha256": fixture.document_sha256,
                "fixture_id": fixture.fixture_id,
                "hardware_document_sha256": hardware.document_sha256,
                "profile": config.profile,
                "status": "valid",
            }
            sys.stdout.buffer.write(canonical_json_bytes(result))
            return 0
        report = run_profile(
            base_url=args.base_url,
            notion_base_url=args.notion_base_url,
            fixture=fixture,
            credentials=credentials,
            hardware=hardware,
            config=config,
            confirmation=args.confirm_target_load,
            allow_loopback_http=args.allow_loopback_http,
            request_timeout_seconds=args.request_timeout_seconds,
        )
        output, csv_path = write_report_pair(args.output, report, fixture=fixture)
        summary = report["summary"]
        assert isinstance(summary, dict)
        sys.stdout.buffer.write(
            canonical_json_bytes(
                {
                    "csv": csv_path.name,
                    "output": output.name,
                    "pass": summary.get("pass"),
                    "profile": config.profile,
                    "status": "complete",
                }
            )
        )
        return 0 if summary.get("pass") is True else 2
    except PerformanceLoadError as exc:
        sys.stdout.buffer.write(
            canonical_json_bytes({"code": exc.code, "status": "error"})
        )
        return 3


def verify_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_load.py")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        report = parse_performance_report(
            _read_file(
                args.report,
                code="REPORT_INVALID",
                maximum_bytes=_MAX_REPORT_BYTES,
            )
        )
        csv_bytes = _read_file(
            args.csv,
            code="CSV_INVALID",
            maximum_bytes=_MAX_REPORT_BYTES,
        )
        fixture = parse_fixture(
            _read_file(args.fixture, code="FIXTURE_INVALID")
        )
        csv_row = report.get("csv")
        if not isinstance(csv_row, dict) or csv_row.get("filename") != args.csv.name:
            raise PerformanceLoadError("CSV_INVALID")
        errors = performance_report_errors(
            report, csv_bytes=csv_bytes, fixture=fixture
        )
        if errors:
            sys.stdout.buffer.write(
                canonical_json_bytes(
                    {"errors": list(errors), "status": "invalid"}
                )
            )
            return 3
        summary = report["summary"]
        assert isinstance(summary, dict)
        passed = summary.get("pass") is True
        sys.stdout.buffer.write(
            canonical_json_bytes(
                {
                    "pass": passed,
                    "profile": report["profile"],
                    "report_sha256": report["report_sha256"],
                    "status": "valid",
                }
            )
        )
        return 0 if passed else 2
    except PerformanceLoadError as exc:
        sys.stdout.buffer.write(
            canonical_json_bytes({"code": exc.code, "status": "error"})
        )
        return 3


def verify_gate_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_gate.py")
    parser.add_argument(
        "--report-pair",
        action="append",
        nargs=2,
        required=True,
        metavar=("REPORT_JSON", "SAMPLES_CSV"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        fixture = parse_fixture(
            _read_file(args.fixture, code="FIXTURE_INVALID")
        )
        pairs: list[tuple[Mapping[str, object], bytes]] = []
        for report_name, csv_name in args.report_pair:
            report_path = Path(report_name)
            csv_path = Path(csv_name)
            report = parse_performance_report(
                _read_file(
                    report_path,
                    code="REPORT_INVALID",
                    maximum_bytes=_MAX_REPORT_BYTES,
                )
            )
            csv_payload = _read_file(
                csv_path,
                code="CSV_INVALID",
                maximum_bytes=_MAX_REPORT_BYTES,
            )
            csv_row = report.get("csv")
            if not isinstance(csv_row, dict) or csv_row.get("filename") != csv_path.name:
                raise PerformanceLoadError("GATE_REPORT_INVALID")
            pairs.append((report, csv_payload))
        gate = build_performance_gate(pairs, fixture=fixture)
        if performance_gate_errors(gate):
            raise PerformanceLoadError("GATE_INVALID")
        if args.output.suffix.casefold() != ".json":
            raise PerformanceLoadError("OUTPUT_PATH_INVALID")
        _write_new(args.output, canonical_json_bytes(gate))
        summary = gate["summary"]
        assert isinstance(summary, dict)
        passed = summary["all_profiles_pass"] is True
        sys.stdout.buffer.write(
            canonical_json_bytes(
                {
                    "gate_sha256": gate["gate_sha256"],
                    "output": args.output.name,
                    "pass": passed,
                    "status": "valid",
                }
            )
        )
        return 0 if passed else 2
    except PerformanceLoadError as exc:
        sys.stdout.buffer.write(
            canonical_json_bytes({"code": exc.code, "status": "error"})
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CONFIRMATION",
    "CREDENTIAL_SCHEMA",
    "FIXTURE_SCHEMA",
    "GATE_SCHEMA",
    "HARDWARE_SCHEMA",
    "HardwareFacts",
    "LoadFixture",
    "PerformanceLoadError",
    "PROFILE_CONFIG",
    "PROFILE_ORDER",
    "PROFILES",
    "REPORT_SCHEMA",
    "RunConfig",
    "SCHEMA_VERSION",
    "STEADY_WEIGHTS",
    "canonical_json_bytes",
    "build_performance_gate",
    "main",
    "nearest_rank",
    "parse_credentials",
    "parse_fixture",
    "parse_hardware",
    "parse_performance_report",
    "performance_gate_errors",
    "performance_report_errors",
    "run_profile",
    "sha256_bytes",
    "write_report_pair",
    "verify_main",
    "verify_gate_main",
]

"""Strict, read-only verifier for the NP-0 scenario catalog.

Only the explicitly selected catalog, its JSON Schema, and the frozen parity
manifest are read.  The module is standard-library only and deliberately does
not import application, route, store, browser, or migration code.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit


REPORT_SCHEMA_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_DIR = PROJECT_ROOT / "docs" / "notion_parity" / "catalog"
DEFAULT_SCHEMA = DEFAULT_CATALOG_DIR / "schema.json"
DEFAULT_MANIFEST = PROJECT_ROOT / "docs" / "notion_parity_manifest.json"

PHASES = {"NP-2", "NP-3"}
OBSERVATION_STATES = {
    "unknown",
    "reference-only",
    "exists",
    "contract-tested",
    "live-characterized",
    "differential-passed",
    "reference-conflict",
    "excluded",
}
PROMOTION_STATES = {
    "exists",
    "contract_tested",
    "live_characterized",
    "differential_passed",
}
PROMOTION_FOR_OBSERVATION = {
    "unknown": None,
    "reference-only": None,
    "exists": "exists",
    "contract-tested": "contract_tested",
    "live-characterized": "live_characterized",
    "differential-passed": "differential_passed",
    "reference-conflict": None,
    "excluded": None,
}
LIVE_STATES = {"live-characterized", "differential-passed"}
ARTIFACT_KINDS = {"live_capture", "differential_report"}
RECORD_KINDS = {
    "official_reference",
    "repository_source",
    "contract_test",
    "live_capture",
    "differential_report",
    "legal_review",
    "security_review",
}
SOURCE_KINDS = {
    "official_help",
    "official_release",
    "official_developer",
}
SCENARIO_ID_RE = re.compile(
    r"^NP(?P<phase>2|3)-[A-Z0-9]+(?:-[A-Z0-9]+)+-[0-9]{3}$"
)
SAFE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,159}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Z][A-Za-z0-9]{1,7})+$")
OFFICIAL_HOSTS = {"notion.com", "www.notion.com", "developers.notion.com"}

TOP_KEYS = frozenset(
    {
        "schema_version",
        "catalog_id",
        "title",
        "baseline",
        "scope",
        "claims",
        "profiles",
        "sources",
        "reference_conflicts",
        "second_brain_hard_gates",
        "scenarios",
    }
)
BASELINE_KEYS = frozenset(
    {"reference_date", "timezone", "source_priority", "live_observation"}
)
LIVE_OBSERVATION_KEYS = frozenset(
    {"availability", "observation_state", "reason"}
)
SCOPE_KEYS = frozenset(
    {"phases", "catalog_completeness", "phase_completion_claim", "description"}
)
CLAIM_KEYS = frozenset(
    {
        "live_characterized",
        "differential_passed",
        "np2_gate_complete",
        "np3_gate_complete",
    }
)
PROFILE_KEYS = frozenset({"id", "plan", "role", "platform", "locale"})
SOURCE_KEYS = frozenset(
    {
        "id",
        "kind",
        "title",
        "url",
        "reference_date",
        "observation_state",
        "promotion_status",
        "live_observation_status",
        "claim_summary",
    }
)
SCENARIO_REQUIRED_KEYS = frozenset(
    {
        "id",
        "feature_id",
        "phase",
        "title",
        "profile_ids",
        "source_ids",
        "reference_date",
        "plan",
        "role",
        "platform",
        "locale",
        "preconditions",
        "actions",
        "assertions",
        "reference_conflict_ids",
        "second_brain_hard_gate_ids",
        "evidence",
        "notes",
    }
)
SCENARIO_ALLOWED_KEYS = SCENARIO_REQUIRED_KEYS
EVIDENCE_KEYS = frozenset(
    {
        "observation_state",
        "promotion_status",
        "live_characterization",
        "differential",
        "unverified_reason",
        "records",
    }
)
RECORD_REQUIRED_KEYS = frozenset(
    {"id", "kind", "observation_state", "promotion_status", "description"}
)
RECORD_ALLOWED_KEYS = RECORD_REQUIRED_KEYS | {
    "source_id",
    "path",
    "sha256",
    "reviewer",
    "reviewed_at",
    "description",
    "notes",
}
CONFLICT_REQUIRED_KEYS = frozenset(
    {
        "id",
        "status",
        "type",
        "scenario_ids",
        "source_ids",
        "summary",
        "impact",
        "blocks_status_at_or_above",
        "resolution",
    }
)
CONFLICT_ALLOWED_KEYS = CONFLICT_REQUIRED_KEYS | {"notes"}
GATE_REQUIRED_KEYS = frozenset(
    {
        "id",
        "source_manifest_gate_id",
        "title",
        "blocking",
        "applies_to_phases",
        "assertions",
        "evidence",
    }
)
GATE_ALLOWED_KEYS = GATE_REQUIRED_KEYS


class DuplicateJsonKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_json(path: str | Path) -> Any:
    """Load one UTF-8 JSON document while rejecting duplicate object keys."""

    return json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {value}")
        ),
    )


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _add(
    errors: list[dict[str, str]], code: str, subject: str, message: str
) -> None:
    errors.append(_error(code, subject, message))


def _sorted_errors(errors: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    unique = {
        (item["code"], item["subject"], item["message"]): item
        for item in errors
    }
    return [unique[key] for key in sorted(unique)]


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _safe_relative_path(value: str) -> bool:
    if not value or "\x00" in value or "\\" in value:
        return False
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    return not (
        windows.is_absolute()
        or bool(windows.drive)
        or posix.is_absolute()
        or any(part in {"", ".", ".."} for part in posix.parts)
    )


def _exact_keys(
    errors: list[dict[str, str]],
    value: Any,
    required: frozenset[str],
    allowed: frozenset[str],
    subject: str,
) -> Mapping[str, Any] | None:
    if not isinstance(value, dict):
        _add(errors, "TYPE_INVALID", subject, "expected an object")
        return None
    for key in sorted(required - set(value)):
        _add(
            errors,
            "REQUIRED_FIELD_MISSING",
            f"{subject}.{key}",
            f"required field missing: {key}",
        )
    for key in sorted(set(value) - allowed):
        _add(
            errors,
            "UNKNOWN_FIELD",
            f"{subject}.{key}",
            f"unknown field: {key}",
        )
    return value


def _string_list(
    errors: list[dict[str, str]],
    value: Any,
    subject: str,
    *,
    required: bool = True,
) -> list[str]:
    if not isinstance(value, list) or (required and not value):
        _add(errors, "ARRAY_INVALID", subject, "expected a non-empty array")
        return []
    output: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            _add(
                errors,
                "STRING_INVALID",
                f"{subject}[{index}]",
                "expected a non-empty string",
            )
        else:
            output.append(item)
    duplicates = sorted(item for item, count in Counter(output).items() if count > 1)
    for item in duplicates:
        _add(errors, "DUPLICATE_VALUE", subject, f"duplicate value: {item}")
    return output


def _valid_date(
    errors: list[dict[str, str]], value: Any, subject: str
) -> date | None:
    if not isinstance(value, str):
        _add(errors, "DATE_REQUIRED", subject, "reference date is required")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        _add(errors, "DATE_INVALID", subject, "date must use YYYY-MM-DD")
        return None


class JsonSchemaValidator:
    """Small, fail-closed validator for the 2020-12 subset used by the catalog."""

    def __init__(self, schema: Any) -> None:
        self.schema = schema
        self.errors: list[dict[str, str]] = []

    def _resolve(self, reference: str, subject: str) -> Any | None:
        if not reference.startswith("#/"):
            _add(
                self.errors,
                "SCHEMA_REFERENCE_ESCAPE",
                subject,
                "only local JSON Pointer references are allowed",
            )
            return None
        current = self.schema
        for raw_part in reference[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, dict) or part not in current:
                _add(
                    self.errors,
                    "SCHEMA_REFERENCE_INVALID",
                    subject,
                    f"unresolved schema reference: {reference}",
                )
                return None
            current = current[part]
        return current

    def validate(self, instance: Any) -> list[dict[str, str]]:
        self._visit(instance, self.schema, "$", "schema")
        return _sorted_errors(self.errors)

    def _passes(self, value: Any, schema: Any, subject: str) -> bool:
        child = JsonSchemaValidator(self.schema)
        child._visit(value, schema, subject, "condition")
        return not child.errors

    def _visit(self, value: Any, schema: Any, subject: str, schema_subject: str) -> None:
        if isinstance(schema, bool):
            if not schema:
                _add(self.errors, "SCHEMA_REJECTED", subject, "value is disallowed")
            return
        if not isinstance(schema, dict):
            _add(
                self.errors,
                "SCHEMA_DEFINITION_INVALID",
                schema_subject,
                "schema node must be an object or boolean",
            )
            return
        if "$ref" in schema:
            reference = schema.get("$ref")
            if not isinstance(reference, str):
                _add(
                    self.errors,
                    "SCHEMA_REFERENCE_INVALID",
                    schema_subject,
                    "$ref must be a string",
                )
            else:
                resolved = self._resolve(reference, schema_subject + ".$ref")
                if resolved is not None:
                    self._visit(value, resolved, subject, reference)
            return

        condition = schema.get("if")
        if condition is not None:
            branch = schema.get("then") if self._passes(value, condition, subject) else schema.get("else")
            if branch is not None:
                self._visit(value, branch, subject, schema_subject + (".then" if self._passes(value, condition, subject) else ".else"))
        if "not" in schema and self._passes(value, schema["not"], subject):
            _add(self.errors, "SCHEMA_NOT_INVALID", subject, "value satisfies a forbidden schema")

        for keyword in ("allOf",):
            branches = schema.get(keyword)
            if branches is not None:
                if not isinstance(branches, list):
                    _add(self.errors, "SCHEMA_DEFINITION_INVALID", schema_subject, f"{keyword} must be an array")
                else:
                    for index, branch in enumerate(branches):
                        self._visit(value, branch, subject, f"{schema_subject}.{keyword}[{index}]")
        for keyword, expected in (("anyOf", False), ("oneOf", True)):
            branches = schema.get(keyword)
            if branches is None:
                continue
            if not isinstance(branches, list) or not branches:
                _add(self.errors, "SCHEMA_DEFINITION_INVALID", schema_subject, f"{keyword} must be non-empty")
                continue
            passes = 0
            for branch in branches:
                child = JsonSchemaValidator(self.schema)
                child._visit(value, branch, subject, schema_subject + "." + keyword)
                passes += not child.errors
            if (expected and passes != 1) or (not expected and passes == 0):
                _add(self.errors, "SCHEMA_COMPOSITION_INVALID", subject, f"value does not satisfy {keyword}")

        if "const" in schema and value != schema["const"]:
            _add(self.errors, "CONST_INVALID", subject, f"expected constant {schema['const']!r}")
        enum = schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            _add(self.errors, "ENUM_INVALID", subject, "value is outside the schema enum")

        declared_type = schema.get("type")
        if declared_type is not None:
            allowed_types = declared_type if isinstance(declared_type, list) else [declared_type]
            checks = {
                "null": value is None,
                "object": isinstance(value, dict),
                "array": isinstance(value, list),
                "string": isinstance(value, str),
                "integer": isinstance(value, int) and not isinstance(value, bool),
                "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                "boolean": isinstance(value, bool),
            }
            if not any(checks.get(item, False) for item in allowed_types):
                _add(self.errors, "TYPE_INVALID", subject, f"expected schema type {declared_type!r}")
                return

        if isinstance(value, dict):
            required = schema.get("required", [])
            if isinstance(required, list):
                for key in sorted(set(required) - set(value)):
                    _add(self.errors, "REQUIRED_FIELD_MISSING", f"{subject}.{key}", f"required field missing: {key}")
            properties = schema.get("properties", {})
            if not isinstance(properties, dict):
                _add(self.errors, "SCHEMA_DEFINITION_INVALID", schema_subject, "properties must be an object")
                properties = {}
            additional = schema.get("additionalProperties", True)
            for key in sorted(value):
                child_subject = f"{subject}.{key}"
                if key in properties:
                    self._visit(value[key], properties[key], child_subject, f"{schema_subject}.properties.{key}")
                elif additional is False:
                    _add(self.errors, "UNKNOWN_FIELD", child_subject, f"unknown field: {key}")
                elif isinstance(additional, dict):
                    self._visit(value[key], additional, child_subject, schema_subject + ".additionalProperties")

        if isinstance(value, list):
            minimum = schema.get("minItems")
            if isinstance(minimum, int) and len(value) < minimum:
                _add(self.errors, "ARRAY_TOO_SHORT", subject, f"requires at least {minimum} items")
            maximum = schema.get("maxItems")
            if isinstance(maximum, int) and len(value) > maximum:
                _add(self.errors, "ARRAY_TOO_LONG", subject, f"allows at most {maximum} items")
            if schema.get("uniqueItems") is True:
                canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
                if len(canonical) != len(set(canonical)):
                    _add(self.errors, "DUPLICATE_VALUE", subject, "array items must be unique")
            prefix_items = schema.get("prefixItems", [])
            if prefix_items is not None and not isinstance(prefix_items, list):
                _add(self.errors, "SCHEMA_DEFINITION_INVALID", schema_subject, "prefixItems must be an array")
                prefix_items = []
            for index, item_schema in enumerate(prefix_items):
                if index < len(value):
                    self._visit(value[index], item_schema, f"{subject}[{index}]", schema_subject + f".prefixItems[{index}]")
            item_schema = schema.get("items")
            if item_schema is not None:
                for index, item in enumerate(value[len(prefix_items):], start=len(prefix_items)):
                    self._visit(item, item_schema, f"{subject}[{index}]", schema_subject + ".items")
            contains = schema.get("contains")
            if contains is not None and not any(self._passes(item, contains, subject) for item in value):
                _add(self.errors, "ARRAY_CONTAINS_MISSING", subject, "array lacks an item required by contains")

        if isinstance(value, str):
            minimum = schema.get("minLength")
            if isinstance(minimum, int) and len(value) < minimum:
                _add(self.errors, "STRING_TOO_SHORT", subject, f"requires at least {minimum} characters")
            pattern = schema.get("pattern")
            if isinstance(pattern, str):
                try:
                    matches = re.search(pattern, value) is not None
                except re.error:
                    _add(self.errors, "SCHEMA_PATTERN_INVALID", schema_subject, "schema pattern is invalid")
                else:
                    if not matches:
                        _add(self.errors, "PATTERN_INVALID", subject, "value does not match schema pattern")
            maximum = schema.get("maxLength")
            if isinstance(maximum, int) and len(value) > maximum:
                _add(self.errors, "STRING_TOO_LONG", subject, f"allows at most {maximum} characters")
            format_name = schema.get("format")
            if format_name == "date":
                try:
                    date.fromisoformat(value)
                except ValueError:
                    _add(self.errors, "FORMAT_INVALID", subject, "value is not an ISO date")
            elif format_name == "date-time":
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError("timezone missing")
                except ValueError:
                    _add(self.errors, "FORMAT_INVALID", subject, "value is not a timezone-aware ISO date-time")
            elif format_name == "uri":
                parsed = urlsplit(value)
                if not parsed.scheme or not parsed.netloc:
                    _add(self.errors, "FORMAT_INVALID", subject, "value is not an absolute URI")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = schema.get("minimum")
            maximum = schema.get("maximum")
            if isinstance(minimum, (int, float)) and value < minimum:
                _add(self.errors, "NUMBER_TOO_SMALL", subject, f"value must be at least {minimum}")
            if isinstance(maximum, (int, float)) and value > maximum:
                _add(self.errors, "NUMBER_TOO_LARGE", subject, f"value must be at most {maximum}")


def _validate_schema_contract(schema: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(schema, dict):
        return [_error("SCHEMA_ROOT_INVALID", "schema", "schema root must be an object")]
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        _add(errors, "SCHEMA_DIALECT_INVALID", "schema.$schema", "catalog schema must use JSON Schema draft 2020-12")
    if schema.get("type") != "object":
        _add(errors, "SCHEMA_ROOT_INVALID", "schema.type", "catalog schema root type must be object")
    required = schema.get("required")
    if not isinstance(required, list) or set(required) != TOP_KEYS:
        _add(errors, "SCHEMA_REQUIRED_INVALID", "schema.required", "root required fields must exactly match the catalog contract")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or set(properties) != TOP_KEYS:
        _add(errors, "SCHEMA_PROPERTIES_INVALID", "schema.properties", "root properties must exactly match the catalog contract")
    if schema.get("additionalProperties") is not False:
        _add(errors, "SCHEMA_NOT_STRICT", "schema.additionalProperties", "root additionalProperties must be false")

    allowed_keywords = {
        "$schema", "$id", "$defs", "$ref", "$comment", "title", "description",
        "type", "required", "properties", "additionalProperties", "items",
        "minItems", "maxItems", "uniqueItems", "minLength", "maxLength",
        "pattern", "format", "enum", "const", "minimum", "maximum",
        "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
        "prefixItems", "contains",
    }

    def walk(node: Any, subject: str) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                reference = node["$ref"]
                if not isinstance(reference, str) or not reference.startswith("#/"):
                    _add(errors, "SCHEMA_REFERENCE_ESCAPE", subject + ".$ref", "external schema references are forbidden")
            schema_like = (
                not subject.endswith(".properties")
                and not subject.endswith(".$defs")
                and any(
                    key in node
                    for key in (
                        "type",
                        "$ref",
                        "properties",
                        "items",
                        "enum",
                        "const",
                        "allOf",
                        "oneOf",
                        "anyOf",
                    )
                )
            )
            if schema_like:
                for key in sorted(set(node) - allowed_keywords):
                    _add(errors, "SCHEMA_KEYWORD_UNSUPPORTED", subject + "." + key, f"unsupported schema keyword: {key}")
                if node.get("type") == "object" and node.get("additionalProperties") is not False:
                    _add(errors, "SCHEMA_NOT_STRICT", subject + ".additionalProperties", "object schemas must set additionalProperties=false")
            for key, value in node.items():
                walk(value, f"{subject}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{subject}[{index}]")

    walk(schema, "schema")
    return _sorted_errors(errors)


class CatalogValidator:
    def __init__(self, catalogs: Mapping[str, Any], manifest: Any, project_root: Path) -> None:
        self.catalogs = catalogs
        self.manifest = manifest
        self.project_root = project_root.resolve(strict=False)
        self.errors: list[dict[str, str]] = []
        self.global_ids: dict[str, str] = {}
        self.scenario_count = 0
        self.record_count = 0
        self.status_counts: Counter[str] = Counter()

    def add(self, code: str, subject: str, message: str) -> None:
        _add(self.errors, code, subject, message)

    def register(self, identifier: Any, subject: str, kind: str, *, global_id: bool = True) -> str | None:
        if not isinstance(identifier, str) or not SAFE_ID_RE.fullmatch(identifier):
            self.add(f"{kind.upper()}_ID_INVALID", subject, f"invalid {kind} id")
            return None
        if global_id:
            previous = self.global_ids.get(identifier)
            if previous is not None:
                self.add("DUPLICATE_ID", subject, f"duplicate id {identifier}; first declared at {previous}")
            else:
                self.global_ids[identifier] = subject
        return identifier

    def _manifest_profiles(self) -> tuple[dict[str, Mapping[str, Any]], dict[str, set[str]], set[str]]:
        profiles: dict[str, Mapping[str, Any]] = {}
        vocabulary: dict[str, set[str]] = {}
        statuses: set[str] = set()
        if not isinstance(self.manifest, dict):
            self.add("MANIFEST_INVALID", "manifest", "parity manifest root must be an object")
            return profiles, vocabulary, statuses
        for item in self.manifest.get("baseline_profiles", []):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                profiles[item["id"]] = item
        vocab = self.manifest.get("vocabulary")
        if isinstance(vocab, dict):
            for catalog_key, manifest_key in (
                ("plan", "subscription_plans"),
                ("role", "roles"),
                ("platform", "platforms"),
            ):
                values = vocab.get(manifest_key)
                vocabulary[catalog_key] = set(values) if isinstance(values, list) else set()
            values = vocab.get("statuses")
            statuses = set(values) if isinstance(values, list) else set()
        if not profiles or any(not values for values in vocabulary.values()) or not statuses:
            self.add("MANIFEST_VOCABULARY_INVALID", "manifest", "parity manifest profiles or vocabulary are incomplete")
        return profiles, vocabulary, statuses

    def validate(self) -> dict[str, Any]:
        manifest_profiles, vocabulary, manifest_statuses = self._manifest_profiles()
        catalog_ids: set[str] = set()
        for name in sorted(self.catalogs):
            self._catalog(name, self.catalogs[name], manifest_profiles, vocabulary, manifest_statuses, catalog_ids)
        errors = _sorted_errors(self.errors)
        return {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "status": "pass" if not errors else "fail",
            "error_count": len(errors),
            "errors": errors,
            "summary": {
                "catalog_count": len(self.catalogs),
                "scenario_count": self.scenario_count,
                "evidence_record_count": self.record_count,
                "observation_state_counts": dict(sorted(self.status_counts.items())),
            },
        }

    def _catalog(
        self,
        name: str,
        raw: Any,
        manifest_profiles: Mapping[str, Mapping[str, Any]],
        vocabulary: Mapping[str, set[str]],
        manifest_statuses: set[str],
        catalog_ids: set[str],
    ) -> None:
        root_subject = name
        root = _exact_keys(self.errors, raw, TOP_KEYS, TOP_KEYS, root_subject)
        if root is None:
            return
        catalog_id = root.get("catalog_id")
        if not isinstance(catalog_id, str) or not SAFE_ID_RE.fullmatch(catalog_id):
            self.add("CATALOG_ID_INVALID", f"{name}.catalog_id", "invalid catalog id")
        elif catalog_id in catalog_ids:
            self.add("DUPLICATE_ID", f"{name}.catalog_id", f"duplicate catalog id: {catalog_id}")
        else:
            catalog_ids.add(catalog_id)
        if root.get("schema_version") != 1:
            self.add("SCHEMA_VERSION_INVALID", f"{name}.schema_version", "schema_version must be 1")
        if not isinstance(root.get("title"), str) or not root.get("title", "").strip():
            self.add("TITLE_REQUIRED", f"{name}.title", "catalog title is required")

        baseline = _exact_keys(self.errors, root.get("baseline"), BASELINE_KEYS, BASELINE_KEYS, f"{name}.baseline")
        baseline_date = None
        if baseline is not None:
            baseline_date = _valid_date(self.errors, baseline.get("reference_date"), f"{name}.baseline.reference_date")
            if baseline.get("timezone") != "Asia/Seoul":
                self.add("TIMEZONE_INVALID", f"{name}.baseline.timezone", "timezone must be Asia/Seoul")
            live = _exact_keys(self.errors, baseline.get("live_observation"), LIVE_OBSERVATION_KEYS, LIVE_OBSERVATION_KEYS, f"{name}.baseline.live_observation")
            if live and (
                live.get("observation_state") != "unknown"
                or live.get("availability") != "unavailable"
            ):
                self.add("BASELINE_LIVE_CLAIM_INVALID", f"{name}.baseline.live_observation", "baseline live observation must remain explicitly unknown")

        scope = _exact_keys(self.errors, root.get("scope"), SCOPE_KEYS, SCOPE_KEYS, f"{name}.scope")
        scope_phases: set[str] = set()
        if scope:
            scope_phases = set(_string_list(self.errors, scope.get("phases"), f"{name}.scope.phases"))
            for phase in sorted(scope_phases - PHASES):
                self.add("PHASE_SCOPE_ESCAPE", f"{name}.scope.phases", f"phase outside NP-2/NP-3 catalog scope: {phase}")
            if not isinstance(scope.get("phase_completion_claim"), bool):
                self.add("CLAIM_TYPE_INVALID", f"{name}.scope.phase_completion_claim", "phase completion claim must be boolean")

        claims = _exact_keys(self.errors, root.get("claims"), CLAIM_KEYS, CLAIM_KEYS, f"{name}.claims")
        if claims:
            for key, value in claims.items():
                if not isinstance(value, bool):
                    self.add("CLAIM_TYPE_INVALID", f"{name}.claims.{key}", "claim must be boolean")

        profiles: dict[str, Mapping[str, Any]] = {}
        profile_items = root.get("profiles")
        if not isinstance(profile_items, list) or not profile_items:
            self.add("PROFILE_REQUIRED", f"{name}.profiles", "at least one profile is required")
            profile_items = []
        for index, item in enumerate(profile_items):
            subject = f"{name}.profiles[{index}]"
            profile = _exact_keys(self.errors, item, PROFILE_KEYS, PROFILE_KEYS, subject)
            if profile is None:
                continue
            identifier = self.register(profile.get("id"), subject + ".id", "profile", global_id=False)
            if identifier:
                if identifier in profiles:
                    self.add("DUPLICATE_ID", subject + ".id", f"duplicate local profile id: {identifier}")
                else:
                    profiles[identifier] = profile
                expected = manifest_profiles.get(identifier)
                actual_tuple = (profile.get("plan"), profile.get("role"), profile.get("platform"), profile.get("locale"))
                if expected is None:
                    self.add("PROFILE_MANIFEST_UNKNOWN", subject + ".id", f"profile is absent from parity manifest: {identifier}")
                else:
                    expected_tuple = (expected.get("subscription_plan"), expected.get("role"), expected.get("platform"), expected.get("locale"))
                    if actual_tuple != expected_tuple:
                        self.add("PROFILE_MANIFEST_MISMATCH", subject, "profile dimensions differ from parity manifest")
            for key in ("plan", "role", "platform"):
                if profile.get(key) not in vocabulary.get(key, set()):
                    self.add(f"{key.upper()}_INVALID", subject + "." + key, f"value is outside manifest {key} vocabulary")
            locale = profile.get("locale")
            if not isinstance(locale, str) or not LOCALE_RE.fullmatch(locale):
                self.add("LOCALE_INVALID", subject + ".locale", "invalid locale")

        sources: dict[str, Mapping[str, Any]] = {}
        source_items = root.get("sources")
        if not isinstance(source_items, list) or not source_items:
            self.add("SOURCE_REQUIRED", f"{name}.sources", "at least one official source is required")
            source_items = []
        for index, item in enumerate(source_items):
            subject = f"{name}.sources[{index}]"
            source = _exact_keys(self.errors, item, SOURCE_KEYS, SOURCE_KEYS, subject)
            if source is None:
                continue
            identifier = self.register(source.get("id"), subject + ".id", "source", global_id=False)
            if identifier:
                if identifier in sources:
                    self.add("DUPLICATE_ID", subject + ".id", f"duplicate local source id: {identifier}")
                else:
                    sources[identifier] = source
            if source.get("kind") not in SOURCE_KINDS:
                self.add("SOURCE_KIND_INVALID", subject + ".kind", "unknown official source kind")
            source_date = _valid_date(self.errors, source.get("reference_date"), subject + ".reference_date")
            if source_date and baseline_date and source_date > baseline_date:
                self.add("SOURCE_DATE_AFTER_BASELINE", subject + ".reference_date", "source date is after catalog baseline")
            url = source.get("url")
            if not isinstance(url, str):
                self.add("SOURCE_URL_INVALID", subject + ".url", "official source URL is required")
            else:
                parsed = urlsplit(url)
                if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS or parsed.username or parsed.password:
                    self.add("SOURCE_URL_INVALID", subject + ".url", "source must be an official Notion HTTPS URL")
            if (
                source.get("observation_state") != "reference-only"
                or source.get("promotion_status") is not None
                or source.get("live_observation_status") != "unknown"
            ):
                self.add("SOURCE_LIVE_CLAIM_FORBIDDEN", subject, "official sources are reference-only and cannot claim live observation")

        scenarios: dict[str, Mapping[str, Any]] = {}
        known_conflict_ids = {
            item.get("id")
            for item in root.get("reference_conflicts", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        known_gate_ids = {
            item.get("id")
            for item in root.get("second_brain_hard_gates", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        scenario_items = root.get("scenarios")
        if not isinstance(scenario_items, list) or not scenario_items:
            self.add("SCENARIO_REQUIRED", f"{name}.scenarios", "at least one scenario is required")
            scenario_items = []
        for index, item in enumerate(scenario_items):
            subject = f"{name}.scenarios[{index}]"
            scenario = _exact_keys(self.errors, item, SCENARIO_REQUIRED_KEYS, SCENARIO_ALLOWED_KEYS, subject)
            if scenario is None:
                continue
            identifier_value = scenario.get("id")
            identifier = self.register(identifier_value, subject + ".id", "scenario")
            match = SCENARIO_ID_RE.fullmatch(identifier_value) if isinstance(identifier_value, str) else None
            if not match:
                self.add("SCENARIO_ID_INVALID", subject + ".id", "scenario id must use NP<phase>-AREA-BEHAVIOR-NNN")
            elif scenario.get("phase") != f"NP-{match.group('phase')}":
                self.add("PHASE_ID_MISMATCH", subject, "scenario phase differs from its id")
            if identifier:
                scenarios[identifier] = scenario
            phase = scenario.get("phase")
            if phase not in PHASES:
                self.add("PHASE_INVALID", subject + ".phase", "scenario phase must be NP-2 or NP-3")
            elif phase not in scope_phases:
                self.add("PHASE_SCOPE_ESCAPE", subject + ".phase", "scenario phase is outside declared catalog scope")
            self.scenario_count += 1
            evidence_value = scenario.get("evidence")
            state = evidence_value.get("observation_state") if isinstance(evidence_value, dict) else None
            if isinstance(state, str):
                self.status_counts[state] += 1
            self._scenario(
                name,
                subject,
                scenario,
                profiles,
                sources,
                vocabulary,
                manifest_statuses,
                baseline_date,
                known_conflict_ids,
                known_gate_ids,
            )

        self._conflicts(name, root.get("reference_conflicts"), scenarios, sources)
        gates_ok = self._gates(name, root.get("second_brain_hard_gates"), scope_phases)
        self._claims(name, claims, scope, scenarios, gates_ok)

    def _scenario(
        self,
        name: str,
        subject: str,
        scenario: Mapping[str, Any],
        profiles: Mapping[str, Mapping[str, Any]],
        sources: Mapping[str, Mapping[str, Any]],
        vocabulary: Mapping[str, set[str]],
        manifest_statuses: set[str],
        baseline_date: date | None,
        known_conflict_ids: set[str],
        known_gate_ids: set[str],
    ) -> None:
        source_ids = _string_list(self.errors, scenario.get("source_ids"), subject + ".source_ids")
        for source_id in source_ids:
            if source_id not in sources:
                self.add("SOURCE_REFERENCE_UNKNOWN", subject + ".source_ids", f"unknown source id: {source_id}")
        scenario_date = _valid_date(self.errors, scenario.get("reference_date"), subject + ".reference_date")
        if scenario_date and baseline_date and scenario_date > baseline_date:
            self.add("SCENARIO_DATE_AFTER_BASELINE", subject + ".reference_date", "scenario date is after catalog baseline")

        profile_ids = _string_list(self.errors, scenario.get("profile_ids"), subject + ".profile_ids")
        selected: list[Mapping[str, Any]] = []
        for profile_id in profile_ids:
            profile = profiles.get(profile_id)
            if profile is None:
                self.add("PROFILE_REFERENCE_UNKNOWN", subject + ".profile_ids", f"unknown profile id: {profile_id}")
            else:
                selected.append(profile)
        for dimension in ("plan", "role", "platform", "locale"):
            actual = _string_list(self.errors, scenario.get(dimension), subject + "." + dimension)
            if dimension in vocabulary:
                for value in sorted(set(actual) - vocabulary[dimension]):
                    self.add(f"{dimension.upper()}_INVALID", subject + "." + dimension, f"value is outside manifest vocabulary: {value}")
            if dimension == "locale":
                for value in actual:
                    if not LOCALE_RE.fullmatch(value):
                        self.add("LOCALE_INVALID", subject + ".locale", f"invalid locale: {value}")
            expected = {str(profile.get(dimension)) for profile in selected}
            if set(actual) != expected:
                self.add("PROFILE_DIMENSION_MISMATCH", subject + "." + dimension, f"{dimension} must equal the referenced profile union")
        for field in ("preconditions", "actions", "assertions"):
            value = scenario.get(field)
            if not isinstance(value, list) or not value:
                self.add("ARRAY_INVALID", subject + "." + field, "expected a non-empty array")
        for field, known in (
            ("reference_conflict_ids", known_conflict_ids),
            ("second_brain_hard_gate_ids", known_gate_ids),
        ):
            values = _string_list(
                self.errors,
                scenario.get(field),
                subject + "." + field,
                required=field == "second_brain_hard_gate_ids",
            )
            for value in values:
                if value not in known:
                    self.add("SCENARIO_REFERENCE_UNKNOWN", subject + "." + field, f"unknown reference: {value}")
        if not isinstance(scenario.get("feature_id"), str) or not SAFE_ID_RE.fullmatch(str(scenario.get("feature_id", ""))):
            self.add("FEATURE_ID_INVALID", subject + ".feature_id", "invalid feature id")

        evidence = _exact_keys(self.errors, scenario.get("evidence"), EVIDENCE_KEYS, EVIDENCE_KEYS, subject + ".evidence")
        state = evidence.get("observation_state") if evidence is not None else None
        promotion = evidence.get("promotion_status") if evidence is not None else None
        if state not in OBSERVATION_STATES:
            self.add("OBSERVATION_STATE_INVALID", subject + ".observation_state", "unknown observation state")
        expected_promotion = PROMOTION_FOR_OBSERVATION.get(state)
        if promotion != expected_promotion:
            self.add("STATUS_PROGRESSION_INVALID", subject + ".promotion_status", f"promotion status must be {expected_promotion!r} for {state!r}")
        if promotion is not None and (promotion not in PROMOTION_STATES or promotion not in manifest_statuses):
            self.add("PROMOTION_STATUS_INVALID", subject + ".promotion_status", "promotion status is incompatible with parity manifest vocabulary")
        if evidence is not None:
            self._evidence(
                subject + ".evidence",
                evidence,
                state,
                promotion,
                set(source_ids),
                sources,
                require_records=True,
            )

    def _evidence(
        self,
        subject: str,
        evidence: Mapping[str, Any],
        state: Any,
        promotion: Any,
        scenario_source_ids: set[str],
        sources: Mapping[str, Mapping[str, Any]],
        *,
        require_records: bool,
    ) -> None:
        live = evidence.get("live_characterization")
        differential = evidence.get("differential")
        valid = False
        if state in {"unknown", "reference-only", "exists", "contract-tested", "reference-conflict", "excluded"}:
            valid = live == "unknown" and differential == "unknown"
        elif state == "live-characterized":
            valid = live == "captured" and differential in {"unknown", "failed"}
        elif state == "differential-passed":
            valid = live == "captured" and differential == "passed"
        if not valid:
            self.add("STATUS_PROGRESSION_INVALID", subject, "live/differential fields do not form a valid cumulative progression")

        records = evidence.get("records")
        if not isinstance(records, list) or (require_records and not records):
            message = "at least one evidence record is required" if require_records else "evidence records must be an array"
            self.add("EVIDENCE_REQUIRED", subject + ".records", message)
            records = []
        kinds: set[str] = set()
        for index, item in enumerate(records):
            record_subject = f"{subject}.records[{index}]"
            record = _exact_keys(self.errors, item, RECORD_REQUIRED_KEYS, RECORD_ALLOWED_KEYS, record_subject)
            if record is None:
                continue
            self.record_count += 1
            self.register(record.get("id"), record_subject + ".id", "evidence")
            kind = record.get("kind")
            if kind not in RECORD_KINDS:
                self.add("EVIDENCE_KIND_INVALID", record_subject + ".kind", "unknown evidence kind")
                continue
            kinds.add(kind)
            source_id = record.get("source_id")
            record_state = record.get("observation_state")
            record_promotion = record.get("promotion_status")
            if record_promotion != PROMOTION_FOR_OBSERVATION.get(record_state):
                self.add("STATUS_PROGRESSION_INVALID", record_subject, "evidence record observation and promotion states disagree")
            if kind == "official_reference":
                if source_id not in sources:
                    self.add("SOURCE_REFERENCE_UNKNOWN", record_subject + ".source_id", f"unknown source id: {source_id}")
                elif source_id not in scenario_source_ids:
                    self.add("EVIDENCE_SOURCE_MISMATCH", record_subject + ".source_id", "official evidence source is not declared by the scenario")
                if record_state != "reference-only" or record_promotion is not None:
                    self.add("EVIDENCE_STATUS_INVALID", record_subject, "official reference evidence must be reference-only")
            if kind in ARTIFACT_KINDS:
                self._artifact(record, record_subject)
            if state in {"unknown", "reference-only"} and kind in ARTIFACT_KINDS:
                self.add("LIVE_CLAIM_FORBIDDEN", record_subject, "unknown/reference-only scenarios cannot contain live or differential artifacts")
        if state == "reference-only" and "official_reference" not in kinds:
            self.add("REFERENCE_EVIDENCE_MISSING", subject, "reference-only status requires official reference evidence")
        if state in LIVE_STATES and "live_capture" not in kinds:
            self.add("LIVE_EVIDENCE_MISSING", subject, "live status requires a live_capture record with path and hash")
        if state == "differential-passed" and "differential_report" not in kinds:
            self.add("DIFFERENTIAL_EVIDENCE_MISSING", subject, "differential-passed requires a differential_report record with path and hash")
        if differential == "failed" and "differential_report" not in kinds:
            self.add("DIFFERENTIAL_EVIDENCE_MISSING", subject, "a failed differential result requires its report")

    def _artifact(self, record: Mapping[str, Any], subject: str) -> None:
        path_value = record.get("path")
        digest = record.get("sha256")
        if not isinstance(path_value, str):
            self.add("EVIDENCE_PATH_REQUIRED", subject + ".path", "artifact evidence path is required")
        elif not _safe_relative_path(path_value):
            self.add("EVIDENCE_PATH_ESCAPE", subject + ".path", "evidence path must be a safe project-relative POSIX path")
        else:
            resolved = (self.project_root / PurePosixPath(path_value)).resolve(strict=False)
            if not _is_relative_to(resolved, self.project_root):
                self.add("EVIDENCE_PATH_ESCAPE", subject + ".path", "evidence path escapes project root")
            elif not resolved.is_file():
                self.add("EVIDENCE_PATH_MISSING", subject + ".path", f"evidence file does not exist: {path_value}")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            self.add("EVIDENCE_HASH_INVALID", subject + ".sha256", "artifact hash must be sha256:<64 lowercase hex>")
        reviewer = record.get("reviewer")
        reviewed_at = record.get("reviewed_at")
        if not isinstance(reviewer, str) or not reviewer.strip():
            self.add("EVIDENCE_REVIEWER_REQUIRED", subject + ".reviewer", "artifact evidence requires reviewer")
        if not isinstance(reviewed_at, str):
            self.add("EVIDENCE_REVIEWED_AT_INVALID", subject + ".reviewed_at", "artifact evidence requires timezone-aware ISO-8601 reviewed_at")
        else:
            try:
                parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone required")
            except ValueError:
                self.add("EVIDENCE_REVIEWED_AT_INVALID", subject + ".reviewed_at", "artifact evidence requires timezone-aware ISO-8601 reviewed_at")

    def _conflicts(self, name: str, value: Any, scenarios: Mapping[str, Any], sources: Mapping[str, Any]) -> None:
        if not isinstance(value, list):
            self.add("TYPE_INVALID", f"{name}.reference_conflicts", "expected an array")
            return
        for index, item in enumerate(value):
            subject = f"{name}.reference_conflicts[{index}]"
            conflict = _exact_keys(self.errors, item, CONFLICT_REQUIRED_KEYS, CONFLICT_ALLOWED_KEYS, subject)
            if conflict is None:
                continue
            self.register(conflict.get("id"), subject + ".id", "conflict")
            if conflict.get("status") not in {"open", "resolved"}:
                self.add("CONFLICT_STATUS_INVALID", subject + ".status", "conflict status must be open or resolved")
            for field, registry in (("scenario_ids", scenarios), ("source_ids", sources)):
                identifiers = _string_list(self.errors, conflict.get(field), subject + "." + field)
                for identifier in identifiers:
                    if identifier not in registry:
                        self.add("CONFLICT_REFERENCE_UNKNOWN", subject + "." + field, f"unknown reference: {identifier}")
            if conflict.get("blocks_status_at_or_above") not in OBSERVATION_STATES:
                self.add("CONFLICT_BLOCK_STATUS_INVALID", subject + ".blocks_status_at_or_above", "unknown blocking status")
            resolution = conflict.get("resolution")
            if conflict.get("status") == "resolved" and (not isinstance(resolution, str) or not resolution.strip()):
                self.add("CONFLICT_RESOLUTION_REQUIRED", subject + ".resolution", "resolved conflict requires resolution")

    def _gates(self, name: str, value: Any, phases: set[str]) -> bool:
        if not isinstance(value, list) or not value:
            self.add("SECOND_BRAIN_GATE_REQUIRED", f"{name}.second_brain_hard_gates", "Second Brain hard gates are required")
            return False
        passed = True
        for index, item in enumerate(value):
            subject = f"{name}.second_brain_hard_gates[{index}]"
            gate = _exact_keys(self.errors, item, GATE_REQUIRED_KEYS, GATE_ALLOWED_KEYS, subject)
            if gate is None:
                passed = False
                continue
            identifier = gate.get("id")
            if not isinstance(identifier, str) or not re.fullmatch(r"^SB-[A-Z0-9]+(?:-[A-Z0-9]+)*$", identifier):
                self.add("SECOND_BRAIN_GATE_ID_INVALID", subject + ".id", "invalid Second Brain gate id")
            if gate.get("blocking") is not True:
                self.add("SECOND_BRAIN_GATE_NOT_BLOCKING", subject + ".blocking", "Second Brain hard gate must be blocking")
            applies = set(_string_list(self.errors, gate.get("applies_to_phases"), subject + ".applies_to_phases"))
            if applies - phases:
                self.add("PHASE_SCOPE_ESCAPE", subject + ".applies_to_phases", "gate applies outside catalog scope")
            _string_list(self.errors, gate.get("assertions"), subject + ".assertions")
            evidence = gate.get("evidence")
            evidence_object = _exact_keys(
                self.errors,
                evidence,
                EVIDENCE_KEYS,
                EVIDENCE_KEYS,
                subject + ".evidence",
            )
            if evidence_object is None:
                self.add("EVIDENCE_REQUIRED", subject + ".evidence", "gate evidence object is required")
                passed = False
            else:
                gate_state = evidence_object.get("observation_state")
                gate_promotion = evidence_object.get("promotion_status")
                self._evidence(
                    subject + ".evidence",
                    evidence_object,
                    gate_state,
                    gate_promotion,
                    set(),
                    {},
                    require_records=False,
                )
                if gate_state != "differential-passed":
                    passed = False
        return passed

    def _claims(self, name: str, claims: Mapping[str, Any] | None, scope: Mapping[str, Any] | None, scenarios: Mapping[str, Mapping[str, Any]], gates_ok: bool) -> None:
        if claims is None or scope is None:
            return
        states = [
            item.get("evidence", {}).get("observation_state")
            if isinstance(item.get("evidence"), dict)
            else None
            for item in scenarios.values()
        ]
        if claims.get("live_characterized") and (not states or any(state not in LIVE_STATES for state in states)):
            self.add("LIVE_CLAIM_UNSUPPORTED", f"{name}.claims.live_characterized", "catalog-wide live claim requires every scenario to have live evidence")
        if claims.get("differential_passed") and (not states or any(state != "differential-passed" for state in states)):
            self.add("DIFFERENTIAL_CLAIM_UNSUPPORTED", f"{name}.claims.differential_passed", "catalog-wide differential claim requires every scenario to pass")
        for phase, key in (("NP-2", "np2_gate_complete"), ("NP-3", "np3_gate_complete")):
            phase_states = [
                item.get("evidence", {}).get("observation_state")
                if isinstance(item.get("evidence"), dict)
                else None
                for item in scenarios.values()
                if item.get("phase") == phase
            ]
            if claims.get(key) and (not phase_states or any(state != "differential-passed" for state in phase_states) or not gates_ok):
                self.add("PHASE_COMPLETION_CLAIM_UNSUPPORTED", f"{name}.claims.{key}", f"{phase} completion requires all differential passes and verified hard gates")
        any_gate_claim = bool(claims.get("np2_gate_complete") or claims.get("np3_gate_complete"))
        if scope.get("phase_completion_claim") != any_gate_claim:
            self.add("PHASE_COMPLETION_CLAIM_MISMATCH", f"{name}.scope.phase_completion_claim", "scope and phase completion claims disagree")


def validate_catalog_data(
    catalogs: Mapping[str, Any],
    *,
    schema: Any,
    manifest: Any,
    project_root: str | Path = PROJECT_ROOT,
) -> tuple[dict[str, Any], int]:
    errors = _validate_schema_contract(schema)
    for name in sorted(catalogs):
        schema_errors = JsonSchemaValidator(schema).validate(catalogs[name])
        for item in schema_errors:
            errors.append(
                _error(item["code"], f"{name}:{item['subject']}", item["message"])
            )
    semantic = CatalogValidator(catalogs, manifest, Path(project_root)).validate()
    errors.extend(semantic["errors"])
    errors = _sorted_errors(errors)
    report = {
        **semantic,
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "errors": errors,
    }
    report["summary"] = {**semantic["summary"], "error_count": len(errors)}
    return report, 0 if not errors else 2


def verify_catalog(
    catalog_dir: str | Path = DEFAULT_CATALOG_DIR,
    *,
    schema_path: str | Path | None = None,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    project_root: str | Path = PROJECT_ROOT,
) -> tuple[dict[str, Any], int]:
    root = Path(project_root).resolve(strict=False)
    directory = Path(catalog_dir).resolve(strict=False)
    selected_schema = Path(schema_path).resolve(strict=False) if schema_path else directory / "schema.json"
    errors: list[dict[str, str]] = []
    if not _is_relative_to(directory, root):
        errors.append(_error("CATALOG_PATH_ESCAPE", "catalog_dir", "catalog directory escapes project root"))
    if not _is_relative_to(selected_schema, directory):
        errors.append(_error("SCHEMA_PATH_ESCAPE", "schema", "schema path must be inside catalog directory"))
    catalogs: dict[str, Any] = {}
    schema: Any = {}
    manifest: Any = {}
    if not errors:
        try:
            schema = load_json(selected_schema)
        except DuplicateJsonKeyError as exc:
            errors.append(_error("DUPLICATE_JSON_KEY", "schema.json", str(exc)))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(_error("SCHEMA_READ_INVALID", "schema.json", f"schema could not be read: {type(exc).__name__}"))
        try:
            manifest = load_json(manifest_path)
        except DuplicateJsonKeyError as exc:
            errors.append(_error("DUPLICATE_JSON_KEY", "manifest", str(exc)))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(_error("MANIFEST_READ_INVALID", "manifest", f"manifest could not be read: {type(exc).__name__}"))
        try:
            candidates = sorted(directory.rglob("*.json"), key=lambda path: path.as_posix())
        except OSError as exc:
            errors.append(_error("CATALOG_READ_INVALID", "catalog_dir", f"catalog directory could not be read: {type(exc).__name__}"))
            candidates = []
        for path in candidates:
            resolved = path.resolve(strict=False)
            if resolved == selected_schema:
                continue
            relative = path.relative_to(directory).as_posix()
            if not _is_relative_to(resolved, directory):
                errors.append(_error("CATALOG_PATH_ESCAPE", relative, "catalog file escapes catalog directory"))
                continue
            try:
                catalogs[relative] = load_json(path)
            except DuplicateJsonKeyError as exc:
                errors.append(_error("DUPLICATE_JSON_KEY", relative, str(exc)))
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(_error("CATALOG_READ_INVALID", relative, f"catalog could not be read: {type(exc).__name__}"))
        if not catalogs:
            errors.append(_error("CATALOG_EMPTY", "catalog_dir", "no catalog JSON documents were found"))
    if errors:
        errors = _sorted_errors(errors)
        return {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "status": "fail",
            "error_count": len(errors),
            "errors": errors,
            "summary": {"catalog_count": len(catalogs), "scenario_count": 0, "evidence_record_count": 0, "observation_state_counts": {}, "error_count": len(errors)},
        }, 2
    report, exit_code = validate_catalog_data(catalogs, schema=schema, manifest=manifest, project_root=root)
    report["catalog_dir"] = str(directory)
    report["schema_sha256"] = hashlib.sha256(selected_schema.read_bytes()).hexdigest()
    report["catalog_sha256"] = {
        name: hashlib.sha256((directory / PurePosixPath(name)).read_bytes()).hexdigest()
        for name in sorted(catalogs)
    }
    return report, exit_code


verify_catalog_directory = verify_catalog


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description="Strictly audit the NP-0 Notion parity catalog")
    parser.add_argument("--catalog-dir", default=str(DEFAULT_CATALOG_DIR))
    parser.add_argument("--schema")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    compact = False
    try:
        args = _parser().parse_args(argv)
        compact = args.compact
        project_root = Path(args.project_root)
        if not project_root.is_absolute():
            raise ValueError("--project-root must be absolute")
        report, exit_code = verify_catalog(
            args.catalog_dir,
            schema_path=args.schema,
            manifest_path=args.manifest,
            project_root=project_root,
        )
    except ValueError as exc:
        report = {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "status": "fail",
            "error_count": 1,
            "errors": [_error("CLI_ARGUMENT_INVALID", "<cli>", str(exc))],
            "summary": {},
        }
        exit_code = 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if compact else 2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

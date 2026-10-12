"""Deterministic, read-only audit for Knowledge Space machine contracts.

This module is intentionally standard-library only and must not import application,
configuration, authentication, or storage modules.  It validates the static P0.6
contract set.  Report schema v2 registers the approved Second Brain successor
surface without claiming that its later runtime packets are implemented.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import unquote, urlsplit
import uuid


REPORT_SCHEMA_VERSION = 2
CONTRACT_MANIFEST = Path("contracts") / "contract-manifest.json"
ALLOWED_HTTP_METHODS = {"delete", "get", "patch", "post", "put"}
MUTATING_HTTP_METHODS = {"delete", "patch", "post", "put"}
ALLOWED_IDEMPOTENCY = {"lease-conditional", "not-applicable", "required"}
REQUIRED_SCHEMA_ROLES = {
    "backup_manifest",
    "cursor_page",
    "error_envelope",
    "goal_contract",
    "job_state",
    "merge_preview",
    "projection_manifest",
    "proposal_preview",
    "publication_preview",
    "publication_result",
    "stable_mutation_response",
    "successor_api",
    "successor_feature_flags",
}
BASELINE_COUNTS = {
    "requirements": 31,
    "goals": 14,
    "scenarios": 124,
    "openapi_paths": 52,
    "openapi_operations": 56,
    "stable_errors": 17,
}
REGISTERED_COUNTS = {
    "requirements": 41,
    "goals": 20,
    "scenarios": 284,
    "openapi_paths": 72,
    "openapi_operations": 80,
    "stable_errors": 31,
}
BASELINE_HASHES = {
    "requirements": "47a5ce485b0f8bc791ea4534cdec1b86d662c300558e0db2c7473a0a0eebd0a6",
    "goals": "a9a2a6c1c308d1d3edd8e28f143d4ce37169b9eee90436cbd1ab97d4d63fcc9c",
    "scenarios": "c63fc22a5908393b73cef5fd1d9839c15af807417afa9f39ca31c8a6b6bcddf9",
    "openapi_operations": "75b7270c72c7275879ab43824c0988717390502b319f3c970aecceb944b200c1",
}
SUCCESSOR_SOURCES = [
    "second_brain_evolution/README.md",
    "second_brain_evolution/PRODUCT_CONTRACT.md",
    "second_brain_evolution/FORWARD_SCHEMA.md",
    "second_brain_evolution/EXECUTION_PLAN.md",
    "second_brain_evolution/ACCEPTANCE.md",
]
DOCS_AUTHORITY_SOURCES = [
    "README.md",
    "DECISIONS.md",
    "ARCHITECTURE.md",
    "SCHEMA_V1.md",
    "API_UI_CONTRACT.md",
    "MIGRATION_ROLLOUT.md",
    "TEST_OPERATIONS.md",
    "IMPLEMENTATION_BACKLOG.md",
    "TRACEABILITY.md",
    "GOAL_CONTRACT.json",
    "STATUS.md",
]
SUCCESSOR_SCENARIO_FAMILIES = {
    "AGRAPH": 17,
    "AUTOEDGE": 19,
    "HSPACE": 18,
    "RANK": 21,
    "REF": 8,
    "SBEOPS": 18,
    "TIME": 22,
    "UPLOAD": 15,
    "WIKI": 22,
}
SOURCE_ARTIFACT_LIST_ROUTE = "/api/knowledge/v1/spaces/{space_id}/items/{item_id}/source-artifacts"
SOURCE_ARTIFACT_DOWNLOAD_ROUTE = SOURCE_ARTIFACT_LIST_ROUTE + "/{artifact_id}/download"
SOURCE_ARTIFACT_PATH_OPERATIONS = {
    "GET " + SOURCE_ARTIFACT_LIST_ROUTE: "listItemSourceArtifacts",
    "GET " + SOURCE_ARTIFACT_DOWNLOAD_ROUTE: "downloadItemSourceArtifact",
}
SOURCE_ARTIFACT_DOWNLOAD_HEADER_SCHEMAS = {
    "Content-Disposition": {"type": "string", "pattern": "^attachment;"},
    "Cache-Control": {"type": "string", "const": "private, no-store, max-age=0"},
    "Pragma": {"type": "string", "const": "no-cache"},
    "X-Content-Type-Options": {"type": "string", "const": "nosniff"},
    "X-Original-Content-SHA256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "X-Request-ID": {"type": "string", "minLength": 1, "maxLength": 128},
    "Content-Length": {"type": "integer", "minimum": 0},
}
SUCCESSOR_PATH_OPERATIONS = {
    "GET /api/knowledge/v2/personal-wikis": "personalWikisList",
    "POST /api/knowledge/v2/personal-wikis": "personalWikisCreate",
    "POST /api/knowledge/v2/personal-wikis/search": "personalWikisSearch",
    "PATCH /api/knowledge/v2/personal-wikis/{space_id}": "personalWikiRename",
    "PUT /api/knowledge/v2/personal-wikis/{space_id}/default": "personalWikiSetDefault",
    "POST /api/knowledge/v2/personal-wikis/{space_id}/archive": "personalWikiArchive",
    "POST /api/knowledge/v2/personal-wikis/{space_id}/restore": "personalWikiRestore",
    "GET /api/admin/v2/org/teams/{team_id}/leader": "teamKnowledgeLeaderGet",
    "PUT /api/admin/v2/org/teams/{team_id}/leader": "teamKnowledgeLeaderPut",
    "GET /api/admin/v2/org/responsibilities/{responsibility_id}/executive": "responsibilityKnowledgeExecutiveGet",
    "PUT /api/admin/v2/org/responsibilities/{responsibility_id}/executive": "responsibilityKnowledgeExecutivePut",
    "GET /api/knowledge/v2/spaces/{space_id}/distribution-targets": "distributionTargetsList",
    "GET /api/knowledge/v2/spaces/{space_id}/distributions": "distributionsList",
    "POST /api/knowledge/v2/spaces/{space_id}/distributions": "distributionsCreate",
    "POST /api/knowledge/v2/spaces/{space_id}/distributions/{grant_id}/revoke": "distributionRevoke",
    "POST /api/knowledge/v2/spaces/{space_id}/edges/{edge_id}/suppress": "semanticEdgeSuppress",
    "POST /api/knowledge/v2/spaces/{space_id}/edges/{edge_id}/suppression/lift": "semanticEdgeSuppressionLift",
    "GET /api/knowledge/v2/spaces/{space_id}/items/{item_id}/temporal": "itemTemporalGet",
    "POST /api/knowledge/v2/spaces/{space_id}/items/{item_id}/temporal-assertions": "temporalAssertionCreate",
    "POST /api/knowledge/v2/spaces/{space_id}/items/{item_id}/temporal-assertions/{assertion_id}/revoke": "temporalAssertionRevoke",
    "POST /api/knowledge/v2/spaces/{space_id}/items/{item_id}/verification-attestations": "verificationAttestationCreate",
    "POST /api/knowledge/v2/spaces/{space_id}/items/{item_id}/verification-attestations/{attestation_id}/revoke": "verificationAttestationRevoke",
    # SBE-8.12 adds two existing v1 reads without changing the frozen v7 IDs.
    **SOURCE_ARTIFACT_PATH_OPERATIONS,
}
SUCCESSOR_ERRORS = {
    "CORPUS_CLOSURE_STALE",
    "DISTRIBUTION_STATE_CONFLICT",
    "EDGE_HOME_INVALID",
    "EDGE_SUPPRESSED",
    "NATIVE_WRITE_DISABLED",
    "PROJECTION_MANIFEST_UNTRUSTED",
    "REFERENCE_TARGET_INVALID",
    "ROLE_EPISODE_STALE",
    "TEMPORAL_HEAD_CONFLICT",
    "TEMPORAL_VALUE_INVALID",
    "VERIFICATION_NONCURRENT",
    "WIKI_ARCHIVED_HISTORY_ONLY",
    "WIKI_DEFAULT_REQUIRED",
    "WIKI_NAME_CONFLICT",
}
SUCCESSOR_FLAGS = {
    "KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA": [],
    "KNOWLEDGE_CORE_MULTI_WIKI_READS": [
        "KNOWLEDGE_CORE_ENABLED",
        "KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA",
        "KNOWLEDGE_CORE_READS",
    ],
    "KNOWLEDGE_CORE_MULTI_WIKI_WRITES": [
        "KNOWLEDGE_CORE_MULTI_WIKI_READS",
        "KNOWLEDGE_CORE_PERSONAL_WRITES",
    ],
    "KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING": [
        "KNOWLEDGE_CORE_MULTI_WIKI_READS"
    ],
    "KNOWLEDGE_CORE_TEAM_WRITES": [
        "KNOWLEDGE_CORE_ENABLED",
        "KNOWLEDGE_CORE_PART_READS",
        "KNOWLEDGE_CORE_HIGHER_SPACES",
    ],
    "KNOWLEDGE_CORE_RESPONSIBILITY_WRITES": [
        "KNOWLEDGE_CORE_ENABLED",
        "KNOWLEDGE_CORE_PART_READS",
        "KNOWLEDGE_CORE_HIGHER_SPACES",
    ],
    "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS": [
        "KNOWLEDGE_CORE_ENABLED",
        "KNOWLEDGE_CORE_PART_READS",
        "KNOWLEDGE_CORE_HIGHER_SPACES",
    ],
    "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL": ["KNOWLEDGE_CORE_ENABLED"],
    "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE": [
        "KNOWLEDGE_CORE_ENABLED",
        "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL",
        "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS",
    ],
    "KNOWLEDGE_CORE_HYBRID_RERANK": [
        "KNOWLEDGE_CORE_ENABLED",
        "KNOWLEDGE_CORE_READS",
    ],
    "KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER": ["KNOWLEDGE_CORE_HYBRID_RERANK"],
    "KNOWLEDGE_CORE_TEMPORAL_RANKING": ["KNOWLEDGE_CORE_HYBRID_RERANK"],
    "KNOWLEDGE_CORE_PROJECTION_FOOTER_V2": [
        "KNOWLEDGE_CORE_ENABLED",
        "KNOWLEDGE_CORE_PROJECTIONS",
    ],
}
SUPERSESSION_LEDGER = [
    {"selector": "SPACE-005", "action": "retain_flags_off_compatibility"},
    {"selector": "CUR-005", "action": "retain_legacy_human_approval_mode"},
    {"selector": "CUR-011", "action": "retain_destructive_and_t3_review"},
    {"selector": "R-004", "action": "rewrite_selected_view_corpus_closure"},
    {"selector": "R-017", "action": "rewrite_native_higher_spaces"},
    {"selector": "R-028", "action": "split_validated_edge_from_destructive_curation"},
    {"selector": "G-07", "action": "rewrite_authorized_closure_isolation"},
    {"selector": "G-08", "action": "rewrite_valid_auto_edge_safety"},
    {"selector": "G-09", "action": "retain_slo_rebaseline_successor_profile"},
    {"selector": "API_UI_CONTRACT.md#9:system-admin-Part-authorship", "action": "remove_admin_status_ordinary_authorship"},
]
REQUIREMENT_ID_RE = re.compile(r"^R-(\d{3})$")
REQUIREMENT_REFERENCE_RE = re.compile(
    r"(?<![A-Z0-9])R-(\d{3})(?:\.\.(?:R-)?(\d{3}))?(?![A-Z0-9])"
)
GOAL_ID_RE = re.compile(r"^G-(\d{2})$")
SCENARIO_ID_RE = re.compile(r"^([A-Z]+)-(\d{3})$")
SCENARIO_REFERENCE_RE = re.compile(
    r"(?<![A-Z0-9])([A-Z]+)-(\*|\d{3}(?:\.\.\d{3})?)(?![A-Z0-9])"
)
MARKDOWN_SCENARIO_ROW_RE = re.compile(
    r"^\|\s*`([A-Z]+-\d{3})`\s*\|", re.MULTILINE
)
SUCCESSOR_SCENARIO_ROW_RE = re.compile(
    r"^- \*\*([A-Z]+-\d{3})\*\*\s+—", re.MULTILINE
)
MARKDOWN_REQUIREMENT_ROW_RE = re.compile(
    r"^\|\s*`(R-\d{3})`\s*\|", re.MULTILINE
)
MARKDOWN_GOAL_ROW_RE = re.compile(r"^\|\s*`(G-\d{2})`\s*\|", re.MULTILINE)
PATH_PARAMETER_RE = re.compile(r"\{([^{}]+)\}")
STABLE_ERROR_ROW_RE = re.compile(
    r"^\|\s*\d{3}\s*\|\s*`([A-Z][A-Z0-9_]+)`\s*\|", re.MULTILINE
)
HUMAN_API_OPERATION_RE = re.compile(
    r"`(GET|POST|PUT|PATCH|DELETE)\s+([^`]+)`"
)


class DuplicateJsonKeyError(ValueError):
    """Raised when strict JSON decoding encounters a duplicate object key."""


class CliArgumentError(ValueError):
    """Raised instead of argparse writing an unstructured error to stderr."""


class _JsonParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - exercised via main
        raise CliArgumentError(message)


def canonical_json(value: Any) -> str:
    """Return one deterministic ASCII-safe JSON value."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _strict_json_bytes(raw: bytes, *, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} is not valid UTF-8") from exc

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DuplicateJsonKeyError(f"duplicate key in {label}")
            result[key] = value
        return result

    def reject_constant(_: str) -> None:
        raise ValueError(f"non-finite JSON number in {label}")

    return json.loads(
        text,
        object_pairs_hook=pairs_hook,
        parse_constant=reject_constant,
    )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _markdown_section(text: str, heading: str, next_heading: str | None) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    start += len(heading)
    if next_heading is None:
        return text[start:]
    end = text.find(next_heading, start)
    return text[start:] if end < 0 else text[start:end]


def _markdown_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _strip_code(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        return value[1:-1]
    return value


def _sequential_ids(prefix: str, count: int, width: int) -> list[str]:
    return [f"{prefix}-{number:0{width}d}" for number in range(1, count + 1)]


def _instance_type_matches(instance: Any, type_name: str) -> bool:
    if type_name == "null":
        return instance is None
    if type_name == "boolean":
        return isinstance(instance, bool)
    if type_name == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if type_name == "number":
        return (
            isinstance(instance, (int, float))
            and not isinstance(instance, bool)
            and math.isfinite(instance)
        )
    if type_name == "string":
        return isinstance(instance, str)
    if type_name == "array":
        return isinstance(instance, list)
    if type_name == "object":
        return isinstance(instance, dict)
    return False


class ContractAuditor:
    """Audit one explicit knowledge-space documentation root."""

    def __init__(self, docs_root: str | os.PathLike[str]) -> None:
        requested = Path(docs_root)
        if not requested.is_absolute():
            raise ValueError("docs_root must be absolute")
        self.docs_root = requested.resolve(strict=False)
        if len(self.docs_root.parents) < 2:
            raise ValueError("docs_root has no repository root")
        self.repository_root = self.docs_root.parents[1]
        self.contract_root = self.docs_root / "contracts"
        self.errors: list[dict[str, Any]] = []
        self._error_keys: set[tuple[str, str, str, tuple[str, ...]]] = set()
        self._source_bytes: dict[Path, bytes] = {}
        self._json_cache: dict[Path, Any] = {}
        self._ref_error_keys: set[tuple[Path, str]] = set()
        self.reference_count = 0
        self.resolved_reference_count = 0
        self.successor_state = "invalid"
        self.successor_scenario_text = ""
        self.stats: dict[str, Any] = {
            "goal": {},
            "openapi": {},
            "requirements": {},
            "scenarios": {},
            "schemas": {},
            "successor_transition": {},
        }

    def error(
        self,
        code: str,
        location: str,
        message: str,
        ids: Iterable[str] = (),
    ) -> None:
        normalized_ids = tuple(sorted(set(ids)))
        key = (code, location, message, normalized_ids)
        if key in self._error_keys:
            return
        self._error_keys.add(key)
        item: dict[str, Any] = {
            "code": code,
            "location": location,
            "message": message,
        }
        if normalized_ids:
            item["ids"] = list(normalized_ids)
        self.errors.append(item)

    def relative(self, path: Path) -> str:
        resolved = path.resolve(strict=False)
        repository_agents = (self.repository_root / "AGENTS.md").resolve(strict=False)
        if resolved == repository_agents:
            return "repository:AGENTS.md"
        try:
            return resolved.relative_to(self.docs_root).as_posix()
        except ValueError:
            return "<outside-docs-root>"

    def read_repository_agents(self) -> str:
        """Read only the one explicitly allowlisted repository authority file."""

        path = (self.repository_root / "AGENTS.md").resolve(strict=False)
        if path.parent != self.repository_root or path.name != "AGENTS.md":
            self.error(
                "REPOSITORY_AUTHORITY_PATH_INVALID",
                "repository:AGENTS.md",
                "The repository authority allowlist resolved unexpectedly.",
            )
            return ""
        if not path.exists() or not path.is_file():
            self.error(
                "SOURCE_MISSING",
                "repository:AGENTS.md",
                "The required repository authority file is missing.",
            )
            return ""
        try:
            raw = path.read_bytes()
        except OSError:
            self.error(
                "SOURCE_UNREADABLE",
                "repository:AGENTS.md",
                "The repository authority file could not be read.",
            )
            return ""
        self._source_bytes[path] = raw
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            self.error(
                "SOURCE_NOT_UTF8",
                "repository:AGENTS.md",
                "The repository authority file is not valid UTF-8.",
            )
            return ""

    def _safe_path(self, path: Path, *, must_exist: bool = True) -> Path | None:
        resolved = path.resolve(strict=False)
        if not _is_relative_to(resolved, self.docs_root):
            self.error(
                "PATH_ESCAPES_DOCS_ROOT",
                self.relative(path),
                "A contract source or reference resolves outside the documentation root.",
            )
            return None
        if must_exist and (not resolved.exists() or not resolved.is_file()):
            self.error(
                "SOURCE_MISSING",
                self.relative(resolved),
                "A required contract source file is missing.",
            )
            return None
        return resolved

    def read_bytes(self, path: Path) -> bytes | None:
        safe = self._safe_path(path)
        if safe is None:
            return None
        if safe in self._source_bytes:
            return self._source_bytes[safe]
        try:
            raw = safe.read_bytes()
        except OSError:
            self.error(
                "SOURCE_UNREADABLE",
                self.relative(safe),
                "A required contract source could not be read.",
            )
            return None
        self._source_bytes[safe] = raw
        return raw

    def read_text(self, path: Path) -> str:
        raw = self.read_bytes(path)
        if raw is None:
            return ""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            self.error(
                "SOURCE_NOT_UTF8",
                self.relative(path),
                "A contract source is not valid UTF-8.",
            )
            return ""

    def load_json(self, path: Path) -> Any:
        safe = self._safe_path(path)
        if safe is None:
            return None
        if safe in self._json_cache:
            return self._json_cache[safe]
        raw = self.read_bytes(safe)
        if raw is None:
            return None
        try:
            value = _strict_json_bytes(raw, label=self.relative(safe))
        except DuplicateJsonKeyError:
            self.error(
                "JSON_DUPLICATE_KEY",
                self.relative(safe),
                "A JSON contract contains a duplicate object key.",
            )
            return None
        except (TypeError, ValueError, json.JSONDecodeError):
            self.error(
                "JSON_INVALID",
                self.relative(safe),
                "A JSON contract is malformed, non-finite, or not UTF-8.",
            )
            return None
        self._json_cache[safe] = value
        return value

    def manifest_path(self, manifest: Mapping[str, Any], key: str) -> Path | None:
        value = manifest.get(key)
        if not isinstance(value, str) or not value:
            self.error(
                "MANIFEST_FIELD_INVALID",
                CONTRACT_MANIFEST.as_posix(),
                "A required manifest path field is absent or invalid.",
            )
            return None
        return self._safe_path(self.contract_root / value)

    def document_paths(self, manifest: Mapping[str, Any]) -> dict[str, Path]:
        result: dict[str, Path] = {}
        documents = manifest.get("documents")
        if not isinstance(documents, dict):
            self.error(
                "MANIFEST_FIELD_INVALID",
                CONTRACT_MANIFEST.as_posix(),
                "The documents map is absent or invalid.",
            )
            return result
        for role in ("api_ui", "backlog", "scenarios", "traceability"):
            value = documents.get(role)
            if not isinstance(value, str) or not value:
                self.error(
                    "MANIFEST_FIELD_INVALID",
                    CONTRACT_MANIFEST.as_posix(),
                    "A required human-document role is absent or invalid.",
                    [role],
                )
                continue
            path = self._safe_path(self.contract_root / value)
            if path is not None:
                result[role] = path
        return result

    def resolve_ref(self, origin: Path, reference: str) -> tuple[Any, Path] | None:
        self.reference_count += 1
        cache_key = (origin.resolve(strict=False), reference)
        split = urlsplit(reference)
        if split.scheme or split.netloc or split.query:
            if cache_key not in self._ref_error_keys:
                self._ref_error_keys.add(cache_key)
                self.error(
                    "REMOTE_REFERENCE_FORBIDDEN",
                    self.relative(origin),
                    "Machine contracts may use only local file and fragment references.",
                )
            return None
        path_part = unquote(split.path)
        target_path = origin if not path_part else origin.parent / path_part
        safe = self._safe_path(target_path)
        if safe is None:
            return None
        document = self.load_json(safe)
        if document is None:
            return None
        target = document
        fragment = unquote(split.fragment)
        if fragment:
            if not fragment.startswith("/"):
                if cache_key not in self._ref_error_keys:
                    self._ref_error_keys.add(cache_key)
                    self.error(
                        "JSON_POINTER_INVALID",
                        self.relative(origin),
                        "A local reference fragment is not an RFC 6901 JSON pointer.",
                    )
                return None
            for token in fragment[1:].split("/"):
                decoded = token.replace("~1", "/").replace("~0", "~")
                if isinstance(target, dict) and decoded in target:
                    target = target[decoded]
                elif isinstance(target, list) and decoded.isdigit() and int(decoded) < len(target):
                    target = target[int(decoded)]
                else:
                    if cache_key not in self._ref_error_keys:
                        self._ref_error_keys.add(cache_key)
                        self.error(
                            "JSON_POINTER_MISSING",
                            self.relative(origin),
                            "A local reference points to a missing JSON fragment.",
                        )
                    return None
        self.resolved_reference_count += 1
        return target, safe

    def walk_references(self, value: Any, origin: Path) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "$ref":
                    if not isinstance(child, str) or not child:
                        self.error(
                            "REFERENCE_INVALID",
                            self.relative(origin),
                            "A $ref value must be a non-empty string.",
                        )
                    else:
                        self.resolve_ref(origin, child)
                else:
                    self.walk_references(child, origin)
        elif isinstance(value, list):
            for child in value:
                self.walk_references(child, origin)

    def validate_instance(
        self,
        instance: Any,
        schema: Any,
        schema_path: Path,
        instance_path: str = "$",
        depth: int = 0,
    ) -> list[str]:
        if depth > 100:
            return [f"{instance_path}: schema recursion limit exceeded"]
        if isinstance(schema, bool):
            return [] if schema else [f"{instance_path}: value is forbidden"]
        if not isinstance(schema, dict):
            return [f"{instance_path}: schema node is not an object or boolean"]
        failures: list[str] = []
        if "$ref" in schema:
            reference = schema.get("$ref")
            if not isinstance(reference, str):
                return [f"{instance_path}: invalid schema reference"]
            resolved = self.resolve_ref(schema_path, reference)
            if resolved is None:
                return [f"{instance_path}: unresolved schema reference"]
            target, target_path = resolved
            failures.extend(
                self.validate_instance(
                    instance, target, target_path, instance_path, depth + 1
                )
            )

        for branch in schema.get("allOf", []):
            failures.extend(
                self.validate_instance(
                    instance, branch, schema_path, instance_path, depth + 1
                )
            )
        if "anyOf" in schema:
            matches = [
                not self.validate_instance(
                    instance, branch, schema_path, instance_path, depth + 1
                )
                for branch in schema["anyOf"]
            ]
            if not any(matches):
                failures.append(f"{instance_path}: no anyOf branch matched")
        if "oneOf" in schema:
            match_count = sum(
                not self.validate_instance(
                    instance, branch, schema_path, instance_path, depth + 1
                )
                for branch in schema["oneOf"]
            )
            if match_count != 1:
                failures.append(
                    f"{instance_path}: expected one matching oneOf branch, got {match_count}"
                )
        if "not" in schema:
            if not self.validate_instance(
                instance, schema["not"], schema_path, instance_path, depth + 1
            ):
                failures.append(f"{instance_path}: forbidden schema matched")
        if "if" in schema:
            condition_failures = self.validate_instance(
                instance, schema["if"], schema_path, instance_path, depth + 1
            )
            selected = schema.get("then") if not condition_failures else schema.get("else")
            if selected is not None:
                failures.extend(
                    self.validate_instance(
                        instance, selected, schema_path, instance_path, depth + 1
                    )
                )

        if "const" in schema and instance != schema["const"]:
            failures.append(f"{instance_path}: value does not match const")
        if "enum" in schema and instance not in schema["enum"]:
            failures.append(f"{instance_path}: value is outside enum")

        declared_type = schema.get("type")
        if declared_type is not None:
            type_names = [declared_type] if isinstance(declared_type, str) else declared_type
            if not isinstance(type_names, list) or not all(
                isinstance(item, str) for item in type_names
            ):
                failures.append(f"{instance_path}: schema type declaration is invalid")
                return failures
            if not any(_instance_type_matches(instance, item) for item in type_names):
                failures.append(f"{instance_path}: instance type is not allowed")
                return failures

        if isinstance(instance, dict):
            required = schema.get("required", [])
            if isinstance(required, list):
                for key in required:
                    if key not in instance:
                        failures.append(f"{instance_path}: missing required property {key}")
            properties = schema.get("properties", {})
            if isinstance(properties, dict):
                for key, child_schema in properties.items():
                    if key in instance:
                        failures.extend(
                            self.validate_instance(
                                instance[key],
                                child_schema,
                                schema_path,
                                f"{instance_path}.{key}",
                                depth + 1,
                            )
                        )
                extras = set(instance) - set(properties)
                additional = schema.get("additionalProperties", True)
                if additional is False and extras:
                    failures.append(
                        f"{instance_path}: additional properties are forbidden"
                    )
                elif isinstance(additional, dict):
                    for key in extras:
                        failures.extend(
                            self.validate_instance(
                                instance[key],
                                additional,
                                schema_path,
                                f"{instance_path}.{key}",
                                depth + 1,
                            )
                        )

        if isinstance(instance, list):
            if isinstance(schema.get("minItems"), int) and len(instance) < schema["minItems"]:
                failures.append(f"{instance_path}: too few array items")
            if isinstance(schema.get("maxItems"), int) and len(instance) > schema["maxItems"]:
                failures.append(f"{instance_path}: too many array items")
            if schema.get("uniqueItems") is True:
                encoded = [canonical_json(item) for item in instance]
                if len(encoded) != len(set(encoded)):
                    failures.append(f"{instance_path}: array items are not unique")
            prefix_items = schema.get("prefixItems", [])
            if isinstance(prefix_items, list):
                for index, child_schema in enumerate(prefix_items[: len(instance)]):
                    failures.extend(
                        self.validate_instance(
                            instance[index],
                            child_schema,
                            schema_path,
                            f"{instance_path}[{index}]",
                            depth + 1,
                        )
                    )
            items_schema = schema.get("items")
            if items_schema is not None:
                start = len(prefix_items) if isinstance(prefix_items, list) else 0
                for index in range(start, len(instance)):
                    failures.extend(
                        self.validate_instance(
                            instance[index],
                            items_schema,
                            schema_path,
                            f"{instance_path}[{index}]",
                            depth + 1,
                        )
                    )

        if isinstance(instance, str):
            if isinstance(schema.get("minLength"), int) and len(instance) < schema["minLength"]:
                failures.append(f"{instance_path}: string is too short")
            if isinstance(schema.get("maxLength"), int) and len(instance) > schema["maxLength"]:
                failures.append(f"{instance_path}: string is too long")
            pattern = schema.get("pattern")
            if isinstance(pattern, str):
                try:
                    matched = re.search(pattern, instance) is not None
                except re.error:
                    failures.append(f"{instance_path}: schema regex is invalid")
                else:
                    if not matched:
                        failures.append(f"{instance_path}: string does not match pattern")
            format_name = schema.get("format")
            try:
                if format_name == "uuid":
                    uuid.UUID(instance)
                elif format_name == "date":
                    date.fromisoformat(instance)
                elif format_name == "date-time":
                    parsed = datetime.fromisoformat(instance.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError("timezone required")
            except (TypeError, ValueError):
                failures.append(f"{instance_path}: string format is invalid")

        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                failures.append(f"{instance_path}: number is below minimum")
            if "maximum" in schema and instance > schema["maximum"]:
                failures.append(f"{instance_path}: number is above maximum")
        return failures

    def audit_manifest(self, manifest: Any) -> None:
        if not isinstance(manifest, dict):
            self.error(
                "MANIFEST_INVALID",
                CONTRACT_MANIFEST.as_posix(),
                "The contract manifest must be a JSON object.",
            )
            return
        if manifest.get("schema_version") != 2:
            self.error(
                "MANIFEST_VERSION_INVALID",
                CONTRACT_MANIFEST.as_posix(),
                "The contract manifest schema_version must be 2.",
            )
        expected = manifest.get("expected")
        if not isinstance(expected, dict) or any(
            not isinstance(expected.get(key), int) or expected[key] < 1
            for key in (
                "goal_metrics",
                "requirements",
                "scenarios",
                "openapi_paths",
                "openapi_operations",
                "stable_errors",
            )
        ):
            self.error(
                "MANIFEST_EXPECTED_INVALID",
                CONTRACT_MANIFEST.as_posix(),
                "Manifest expected counts are missing or invalid.",
            )
        roles = manifest.get("schema_roles")
        if not isinstance(roles, dict):
            self.error(
                "SCHEMA_ROLES_INVALID",
                CONTRACT_MANIFEST.as_posix(),
                "The schema role registry is missing or invalid.",
            )
            return
        role_names = set(roles)
        if role_names != REQUIRED_SCHEMA_ROLES:
            self.error(
                "SCHEMA_ROLE_SET_MISMATCH",
                CONTRACT_MANIFEST.as_posix(),
                "The required schema-role set is incomplete or contains unknown roles.",
                REQUIRED_SCHEMA_ROLES.symmetric_difference(role_names),
            )
        for role, reference in sorted(roles.items()):
            if not isinstance(reference, str) or not reference:
                self.error(
                    "SCHEMA_ROLE_REFERENCE_INVALID",
                    CONTRACT_MANIFEST.as_posix(),
                    "A schema role reference is absent or invalid.",
                    [role],
                )
                continue
            resolved = self.resolve_ref(
                self.contract_root / "contract-manifest.json", reference
            )
            if resolved is None:
                self.error(
                    "SCHEMA_ROLE_UNRESOLVED",
                    CONTRACT_MANIFEST.as_posix(),
                    "A required schema role does not resolve.",
                    [role],
                )

        self.audit_successor_transition(manifest)

    def audit_successor_transition(self, manifest: Mapping[str, Any]) -> None:
        transition = manifest.get("successor_transition")
        location = CONTRACT_MANIFEST.as_posix() + ":successor_transition"
        if not isinstance(transition, dict):
            self.error(
                "SUCCESSOR_TRANSITION_INVALID",
                location,
                "The closed successor transition ledger is missing.",
            )
            return
        required_keys = {
            "state",
            "baseline",
            "additions",
            "registered_totals",
            "sources",
            "authority_sources",
            "supersessions",
            "feature_flag_contract",
        }
        if set(transition) != required_keys:
            self.error(
                "SUCCESSOR_TRANSITION_KEYS_INVALID",
                location,
                "The successor transition ledger has missing or unknown keys.",
                set(transition).symmetric_difference(required_keys),
            )
        state = transition.get("state")
        if state not in {"proposed", "registered"}:
            self.error(
                "SUCCESSOR_TRANSITION_STATE_INVALID",
                location,
                "The successor transition state must be proposed or registered.",
            )
            return
        self.successor_state = state

        baseline = transition.get("baseline")
        expected_baseline = {"counts": BASELINE_COUNTS, "hashes": BASELINE_HASHES}
        if baseline != expected_baseline:
            self.error(
                "SUCCESSOR_BASELINE_MISMATCH",
                location,
                "The pinned v7 baseline counts or hashes drifted.",
            )
        if transition.get("registered_totals") != REGISTERED_COUNTS:
            self.error(
                "SUCCESSOR_TOTALS_MISMATCH",
                location,
                "The registered successor totals drifted.",
            )

        expected_requirements = _sequential_ids("R", 41, 3)[31:]
        expected_goals = _sequential_ids("G", 20, 2)[14:]
        expected_operations = [
            {
                "method": method_path.split(" ", 1)[0],
                "path": method_path.split(" ", 1)[1],
                "operation_id": operation_id,
            }
            for method_path, operation_id in SUCCESSOR_PATH_OPERATIONS.items()
        ]
        additions = transition.get("additions")
        expected_additions = {
            "requirements": expected_requirements,
            "goals": expected_goals,
            "scenario_families": SUCCESSOR_SCENARIO_FAMILIES,
            "openapi_operations": expected_operations,
            "stable_errors": sorted(SUCCESSOR_ERRORS),
        }
        if additions != expected_additions:
            self.error(
                "SUCCESSOR_ADDITIONS_MISMATCH",
                location,
                "The exact successor R/G/scenario/API/error addition ledger drifted.",
            )
        declared_sources = transition.get("sources")
        if declared_sources != SUCCESSOR_SOURCES:
            self.error(
                "SUCCESSOR_SOURCE_SET_MISMATCH",
                location,
                "The exact five successor source paths drifted.",
            )
        expected_authority = {
            "repository_root": ["AGENTS.md"],
            "docs_root": DOCS_AUTHORITY_SOURCES,
        }
        declared_authority = transition.get("authority_sources")
        if declared_authority != expected_authority:
            self.error(
                "SUCCESSOR_AUTHORITY_SET_MISMATCH",
                location,
                "The exact authority synchronization ledger drifted.",
            )
        if transition.get("supersessions") != SUPERSESSION_LEDGER:
            self.error(
                "SUCCESSOR_SUPERSESSION_MISMATCH",
                location,
                "The exact ten-entry supersession ledger drifted.",
            )

        expected_counts = REGISTERED_COUNTS if state == "registered" else BASELINE_COUNTS
        manifest_expected = manifest.get("expected", {})
        normalized_expected = {
            "requirements": manifest_expected.get("requirements"),
            "goals": manifest_expected.get("goal_metrics"),
            "scenarios": manifest_expected.get("scenarios"),
            "openapi_paths": manifest_expected.get("openapi_paths"),
            "openapi_operations": manifest_expected.get("openapi_operations"),
            "stable_errors": manifest_expected.get("stable_errors"),
        }
        if normalized_expected != expected_counts:
            self.error(
                "SUCCESSOR_STATE_COUNT_MISMATCH",
                location,
                "Manifest active counts do not match the transition state.",
            )

        successor_texts: dict[str, str] = {}
        if isinstance(declared_sources, list):
            for relative_path in declared_sources:
                if not isinstance(relative_path, str) or not relative_path:
                    self.error(
                        "SUCCESSOR_SOURCE_PATH_INVALID",
                        location,
                        "A successor source path is absent or invalid.",
                    )
                    continue
                successor_texts[relative_path] = self.read_text(
                    self.docs_root / relative_path
                )
        self.successor_scenario_text = successor_texts.get(
            "second_brain_evolution/ACCEPTANCE.md", ""
        )
        required_successor_tokens = {
            "second_brain_evolution/README.md": ["PRODUCT_CONTRACT.md", "SBE-0.1"],
            "second_brain_evolution/PRODUCT_CONTRACT.md": ["CorpusClosure", "automatic-edge-v1"],
            "second_brain_evolution/FORWARD_SCHEMA.md": ["72 paths/80 operations", "SBE-8.12", "WIKI_NAME_CONFLICT"],
            "second_brain_evolution/EXECUTION_PLAN.md": ["successor_transition.state", "SBE-7.2"],
            "second_brain_evolution/ACCEPTANCE.md": ["SBEOPS-001", "TIME-022"],
        }
        for source, tokens in required_successor_tokens.items():
            if any(token not in successor_texts.get(source, "") for token in tokens):
                self.error(
                    "SUCCESSOR_SOURCE_NARRATIVE_DRIFT",
                    source,
                    "A successor source is missing a required contract marker.",
                )

        declared_docs_authority = (
            declared_authority.get("docs_root", [])
            if isinstance(declared_authority, dict)
            else []
        )
        authority_texts: dict[str, str] = {}
        if isinstance(declared_docs_authority, list):
            for source in declared_docs_authority:
                if not isinstance(source, str) or not source:
                    self.error(
                        "SUCCESSOR_AUTHORITY_PATH_INVALID",
                        location,
                        "A docs authority path is absent or invalid.",
                    )
                    continue
                authority_texts[source] = self.read_text(self.docs_root / source)
        agents_text = self.read_repository_agents()
        if "second_brain_evolution/EXECUTION_PLAN.md" not in agents_text:
            self.error(
                "SUCCESSOR_AUTHORITY_NARRATIVE_DRIFT",
                "repository:AGENTS.md",
                "AGENTS.md does not route implementation through the successor plan.",
            )
        authority_markers = {
            "README.md": ["second_brain_evolution/PRODUCT_CONTRACT.md"],
            "DECISIONS.md": ["SBE-0.1"],
            "ARCHITECTURE.md": ["second_brain_evolution/PRODUCT_CONTRACT.md"],
            "SCHEMA_V1.md": ["second_brain_evolution/FORWARD_SCHEMA.md"],
            "API_UI_CONTRACT.md": ["72-path/80-operation", "SBE-8.12", "WIKI_NAME_CONFLICT"],
            "MIGRATION_ROLLOUT.md": list(SUCCESSOR_FLAGS),
            "TEST_OPERATIONS.md": ["284", "ACCEPTANCE.md"],
            "IMPLEMENTATION_BACKLOG.md": ["SBE-0.1", "SBE-7.2"],
            "TRACEABILITY.md": ["R-041", "G-20"],
            "GOAL_CONTRACT.json": ['"G-20"', '"required_named_scenario_count": 284'],
            "STATUS.md": ["SBE-0.1", "VERIFIED"],
        }
        for source, tokens in authority_markers.items():
            if any(token not in authority_texts.get(source, "") for token in tokens):
                self.error(
                    "SUCCESSOR_AUTHORITY_NARRATIVE_DRIFT",
                    source,
                    "An authority source is missing a required registered-successor marker.",
                )

        flag_path_value = transition.get("feature_flag_contract")
        if not isinstance(flag_path_value, str) or not flag_path_value:
            self.error(
                "SUCCESSOR_FLAG_CONTRACT_INVALID",
                location,
                "The successor feature-flag contract path is invalid.",
            )
            flag_contract = None
            flag_path = None
        else:
            flag_path = self._safe_path(self.contract_root / flag_path_value)
            flag_contract = self.load_json(flag_path) if flag_path is not None else None
        role_ref = manifest.get("schema_roles", {}).get("successor_feature_flags")
        if isinstance(role_ref, str) and flag_path is not None:
            resolved = self.resolve_ref(self.contract_root / "contract-manifest.json", role_ref)
            if resolved is not None:
                schema, schema_path = resolved
                for failure in self.validate_instance(flag_contract, schema, schema_path)[:50]:
                    self.error(
                        "SUCCESSOR_FLAG_SCHEMA_VIOLATION",
                        self.relative(flag_path),
                        failure,
                    )
        actual_flags: dict[str, list[str]] = {}
        if isinstance(flag_contract, dict) and flag_contract.get("default_enabled") is False:
            for entry in flag_contract.get("flags", []):
                if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    actual_flags[entry["name"]] = entry.get("prerequisites")
                    if entry.get("default") is not False:
                        self.error(
                            "SUCCESSOR_FLAG_DEFAULT_INVALID",
                            self.relative(flag_path or self.contract_root),
                            "Every successor flag must default false.",
                            [entry["name"]],
                        )
        if actual_flags != SUCCESSOR_FLAGS:
            self.error(
                "SUCCESSOR_FLAG_DAG_MISMATCH",
                self.relative(flag_path or self.contract_root),
                "The machine-readable successor feature-flag DAG drifted.",
            )

        self.stats["successor_transition"] = {
            "state": state,
            "baseline": BASELINE_COUNTS,
            "additions": {
                key: REGISTERED_COUNTS[key] - BASELINE_COUNTS[key]
                for key in REGISTERED_COUNTS
            },
            "registered_totals": REGISTERED_COUNTS,
            "successor_source_count": len(SUCCESSOR_SOURCES),
            "authority_source_count": len(DOCS_AUTHORITY_SOURCES) + 1,
            "supersession_count": len(SUPERSESSION_LEDGER),
            "feature_flag_count": len(SUCCESSOR_FLAGS),
        }

    def audit_goal(
        self,
        goal: Any,
        goal_path: Path,
        manifest: Mapping[str, Any],
    ) -> tuple[set[str], int, int]:
        expected = manifest.get("expected", {})
        expected_goal_count = expected.get("goal_metrics", 0)
        expected_requirement_count = expected.get("requirements", 0)
        expected_scenario_count = expected.get("scenarios", 0)
        goal_schema_ref = manifest.get("schema_roles", {}).get("goal_contract")
        if isinstance(goal_schema_ref, str):
            resolved = self.resolve_ref(
                self.contract_root / "contract-manifest.json", goal_schema_ref
            )
            if resolved is not None:
                schema, schema_path = resolved
                for failure in self.validate_instance(goal, schema, schema_path)[:100]:
                    self.error(
                        "GOAL_SCHEMA_VIOLATION",
                        self.relative(goal_path),
                        failure,
                    )
        if not isinstance(goal, dict):
            self.error(
                "GOAL_CONTRACT_INVALID",
                self.relative(goal_path),
                "GOAL_CONTRACT.json must be an object.",
            )
            return set(), expected_requirement_count, expected_scenario_count
        metrics = goal.get("metrics")
        metric_ids: list[str] = []
        if isinstance(metrics, list):
            metric_ids = [
                entry.get("id")
                for entry in metrics
                if isinstance(entry, dict) and isinstance(entry.get("id"), str)
            ]
        duplicates = sorted(
            item for item, count in Counter(metric_ids).items() if count > 1
        )
        if duplicates:
            self.error(
                "GOAL_ID_DUPLICATE",
                self.relative(goal_path),
                "Goal metric IDs must be unique.",
                duplicates,
            )
        expected_ids = set(_sequential_ids("G", expected_goal_count, 2))
        actual_ids = set(metric_ids)
        if actual_ids != expected_ids:
            self.error(
                "GOAL_ID_SET_MISMATCH",
                self.relative(goal_path),
                "Goal metric IDs are missing or unexpected.",
                expected_ids.symmetric_difference(actual_ids),
            )
        baseline_goal_ids = set(_sequential_ids("G", BASELINE_COUNTS["goals"], 2))
        baseline_goal_hash = _sha256(
            "\n".join(sorted(actual_ids & baseline_goal_ids)).encode("ascii")
        )
        if baseline_goal_hash != BASELINE_HASHES["goals"]:
            self.error(
                "SUCCESSOR_BASELINE_GOAL_DRIFT",
                self.relative(goal_path),
                "The frozen G-01..G-14 ID baseline changed during transition.",
            )
        metric_by_id = {
            entry.get("id"): entry
            for entry in metrics or []
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        }
        g01 = metric_by_id.get("G-01", {})
        g13 = metric_by_id.get("G-13", {})
        if isinstance(g01, dict):
            goal_requirement_count = g01.get("required_requirement_count")
            if isinstance(goal_requirement_count, int):
                expected_requirement_count = goal_requirement_count
        if isinstance(g13, dict):
            goal_scenario_count = g13.get("required_named_scenario_count")
            if isinstance(goal_scenario_count, int):
                expected_scenario_count = goal_scenario_count
            for zero_field in ("max_missing_ids", "max_duplicate_ids"):
                if g13.get(zero_field) != 0:
                    self.error(
                        "GOAL_SCENARIO_GATE_INVALID",
                        self.relative(goal_path),
                        "G-13 missing and duplicate ID tolerances must be zero.",
                    )
        if expected.get("requirements") != expected_requirement_count:
            self.error(
                "MANIFEST_GOAL_COUNT_MISMATCH",
                CONTRACT_MANIFEST.as_posix(),
                "Manifest and G-01 requirement counts differ.",
            )
        if expected.get("scenarios") != expected_scenario_count:
            self.error(
                "MANIFEST_GOAL_COUNT_MISMATCH",
                CONTRACT_MANIFEST.as_posix(),
                "Manifest and G-13 scenario counts differ.",
            )
        self.stats["goal"] = {
            "count": len(metric_ids),
            "duplicate_ids": len(duplicates),
            "expected_count": expected_goal_count,
            "ids_sha256": _sha256("\n".join(sorted(actual_ids)).encode("ascii")),
            "baseline_ids_sha256": baseline_goal_hash,
        }
        return actual_ids, expected_requirement_count, expected_scenario_count

    def expand_references(
        self,
        text: str,
        universe: set[str],
        location: str,
    ) -> set[str]:
        expanded: set[str] = set()
        for match in SCENARIO_REFERENCE_RE.finditer(text):
            prefix, suffix = match.groups()
            if suffix == "*":
                members = {item for item in universe if item.startswith(prefix + "-")}
                if not members:
                    self.error(
                        "SCENARIO_WILDCARD_EMPTY",
                        location,
                        "A scenario wildcard matches no declared scenario.",
                        [prefix + "-*"],
                    )
                expanded.update(members)
                continue
            if ".." in suffix:
                start_text, end_text = suffix.split("..", 1)
                start = int(start_text)
                end = int(end_text)
                if end < start:
                    self.error(
                        "SCENARIO_RANGE_INVALID",
                        location,
                        "A scenario range is descending.",
                        [f"{prefix}-{suffix}"],
                    )
                    continue
                candidates = {
                    f"{prefix}-{number:03d}" for number in range(start, end + 1)
                }
            else:
                candidates = {f"{prefix}-{suffix}"}
            unknown = candidates - universe
            if unknown:
                self.error(
                    "SCENARIO_REFERENCE_UNKNOWN",
                    location,
                    "A scenario reference or range contains undeclared IDs.",
                    unknown,
                )
            expanded.update(candidates & universe)
        return expanded

    def expand_requirement_references(
        self, text: str, universe: set[str], location: str
    ) -> set[str]:
        expanded: set[str] = set()
        for match in REQUIREMENT_REFERENCE_RE.finditer(text):
            start = int(match.group(1))
            end = int(match.group(2) or match.group(1))
            if end < start:
                self.error(
                    "REQUIREMENT_RANGE_INVALID",
                    location,
                    "A requirement range is descending.",
                )
                continue
            candidates = {f"R-{number:03d}" for number in range(start, end + 1)}
            unknown = candidates - universe
            if unknown:
                self.error(
                    "REQUIREMENT_REFERENCE_UNKNOWN",
                    location,
                    "A requirement reference or range contains undeclared IDs.",
                    unknown,
                )
            expanded.update(candidates & universe)
        return expanded

    def audit_scenarios(
        self, text: str, path: Path, expected_count: int
    ) -> tuple[set[str], dict[str, int]]:
        section = _markdown_section(
            text, "## 4. Mandatory acceptance scenarios", "## 5. Fault injection"
        )
        if not section:
            self.error(
                "SCENARIO_SECTION_MISSING",
                self.relative(path),
                "The mandatory acceptance scenario section is missing.",
            )
        baseline_ids = MARKDOWN_SCENARIO_ROW_RE.findall(section)
        successor_ids = (
            SUCCESSOR_SCENARIO_ROW_RE.findall(self.successor_scenario_text)
            if self.successor_state == "registered"
            else []
        )
        scenario_ids = baseline_ids + successor_ids
        duplicates = sorted(
            item for item, count in Counter(scenario_ids).items() if count > 1
        )
        if duplicates:
            self.error(
                "SCENARIO_ID_DUPLICATE",
                self.relative(path),
                "Mandatory scenario IDs must be unique.",
                duplicates,
            )
        unique = set(scenario_ids)
        if len(scenario_ids) != expected_count or len(unique) != expected_count:
            self.error(
                "SCENARIO_COUNT_MISMATCH",
                self.relative(path),
                "The mandatory scenario count differs from G-13.",
            )
        declared_match = re.search(
            r"following\s+(\d+)\s+named scenarios are mandatory", section
        )
        if not declared_match or int(declared_match.group(1)) != BASELINE_COUNTS["scenarios"]:
            self.error(
                "SCENARIO_DECLARED_COUNT_MISMATCH",
                self.relative(path),
                "The baseline scenario table declaration drifted.",
            )
        expected_successor = {
            f"{prefix}-{number:03d}"
            for prefix, count in SUCCESSOR_SCENARIO_FAMILIES.items()
            for number in range(1, count + 1)
        }
        actual_successor = set(successor_ids)
        if self.successor_state == "registered" and actual_successor != expected_successor:
            self.error(
                "SUCCESSOR_SCENARIO_SET_MISMATCH",
                "second_brain_evolution/ACCEPTANCE.md",
                "The exact 160-scenario successor family inventory drifted.",
                actual_successor.symmetric_difference(expected_successor),
            )
        baseline_set = set(baseline_ids)
        baseline_hash = _sha256("\n".join(sorted(baseline_set)).encode("ascii"))
        if (
            len(baseline_ids) != BASELINE_COUNTS["scenarios"]
            or baseline_hash != BASELINE_HASHES["scenarios"]
        ):
            self.error(
                "SUCCESSOR_BASELINE_SCENARIO_DRIFT",
                self.relative(path),
                "The frozen 124-scenario baseline changed during transition.",
            )
        families: dict[str, list[int]] = defaultdict(list)
        invalid_ids: list[str] = []
        for scenario_id in unique:
            match = SCENARIO_ID_RE.fullmatch(scenario_id)
            if match is None:
                invalid_ids.append(scenario_id)
                continue
            families[match.group(1)].append(int(match.group(2)))
        if invalid_ids:
            self.error(
                "SCENARIO_ID_INVALID",
                self.relative(path),
                "Scenario IDs must use PREFIX-NNN.",
                invalid_ids,
            )
        family_counts: dict[str, int] = {}
        for prefix, numbers in sorted(families.items()):
            ordered = sorted(numbers)
            expected_numbers = list(range(1, len(ordered) + 1))
            if ordered != expected_numbers:
                self.error(
                    "SCENARIO_FAMILY_GAP",
                    self.relative(path),
                    "A scenario family is not contiguous from 001.",
                    [prefix],
                )
            family_counts[prefix] = len(numbers)
        self.stats["scenarios"] = {
            "backlog_covered": 0,
            "count": len(scenario_ids),
            "duplicate_ids": len(duplicates),
            "expected_count": expected_count,
            "families": family_counts,
            "ids_sha256": _sha256("\n".join(sorted(unique)).encode("ascii")),
            "baseline_count": len(baseline_ids),
            "baseline_ids_sha256": baseline_hash,
            "successor_addition_count": len(successor_ids),
            "traceability_covered": 0,
        }
        return unique, family_counts

    def audit_traceability(
        self,
        text: str,
        path: Path,
        expected_requirement_count: int,
        goal_ids: set[str],
        scenario_ids: set[str],
    ) -> tuple[set[str], set[str]]:
        requirement_section = _markdown_section(
            text,
            "## 1. Confirmed product and architecture requirements",
            "## 2. Active-goal quantitative metrics",
        )
        goal_section = _markdown_section(
            text,
            "## 2. Active-goal quantitative metrics",
            "## 3. Implementation artifacts expected per phase",
        )
        requirement_rows: list[tuple[str, list[str]]] = []
        for line in requirement_section.splitlines():
            cells = _markdown_cells(line)
            if len(cells) == 5:
                identifier = _strip_code(cells[0])
                if REQUIREMENT_ID_RE.fullmatch(identifier):
                    requirement_rows.append((identifier, cells))
        requirement_id_list = [item[0] for item in requirement_rows]
        requirement_duplicates = sorted(
            item
            for item, count in Counter(requirement_id_list).items()
            if count > 1
        )
        if requirement_duplicates:
            self.error(
                "REQUIREMENT_ID_DUPLICATE",
                self.relative(path),
                "Requirement IDs must be unique.",
                requirement_duplicates,
            )
        requirement_ids = set(requirement_id_list)
        expected_ids = set(
            _sequential_ids("R", expected_requirement_count, 3)
        )
        if requirement_ids != expected_ids:
            self.error(
                "REQUIREMENT_ID_SET_MISMATCH",
                self.relative(path),
                "Requirement IDs are missing or unexpected.",
                requirement_ids.symmetric_difference(expected_ids),
            )
        baseline_requirement_ids = set(
            _sequential_ids("R", BASELINE_COUNTS["requirements"], 3)
        )
        baseline_requirement_hash = _sha256(
            "\n".join(
                sorted(requirement_ids & baseline_requirement_ids)
            ).encode("ascii")
        )
        if baseline_requirement_hash != BASELINE_HASHES["requirements"]:
            self.error(
                "SUCCESSOR_BASELINE_REQUIREMENT_DRIFT",
                self.relative(path),
                "The frozen R-001..R-031 ID baseline changed during transition.",
            )
        coverage_line = next(
            (
                line
                for line in requirement_section.splitlines()
                if line.startswith("Coverage status:")
            ),
            "",
        )
        declared_requirement_coverage = self.expand_requirement_references(
            coverage_line,
            requirement_ids,
            self.relative(path) + ":coverage-status",
        )
        if declared_requirement_coverage != requirement_ids:
            self.error(
                "REQUIREMENT_DECLARED_RANGE_MISMATCH",
                self.relative(path),
                "The human requirement coverage declaration does not expand to every requirement.",
                declared_requirement_coverage.symmetric_difference(requirement_ids),
            )
        traced_scenarios: set[str] = set()
        for identifier, cells in requirement_rows:
            if any(not cells[index].strip() for index in (1, 2, 3, 4)):
                self.error(
                    "REQUIREMENT_TRACE_INCOMPLETE",
                    self.relative(path),
                    "A requirement lacks requirement, design, implementation, or acceptance text.",
                    [identifier],
                )
            acceptance = self.expand_references(
                cells[4], scenario_ids, self.relative(path) + ":" + identifier
            )
            if not acceptance:
                self.error(
                    "REQUIREMENT_TEST_UNTRACED",
                    self.relative(path),
                    "A requirement has no resolvable acceptance scenario.",
                    [identifier],
                )
            traced_scenarios.update(acceptance)

        goal_rows: list[tuple[str, list[str]]] = []
        for line in goal_section.splitlines():
            cells = _markdown_cells(line)
            if len(cells) == 4:
                identifier = _strip_code(cells[0])
                if GOAL_ID_RE.fullmatch(identifier):
                    goal_rows.append((identifier, cells))
        trace_goal_id_list = [item[0] for item in goal_rows]
        goal_duplicates = sorted(
            item
            for item, count in Counter(trace_goal_id_list).items()
            if count > 1
        )
        if goal_duplicates:
            self.error(
                "TRACE_GOAL_ID_DUPLICATE",
                self.relative(path),
                "Traceability goal IDs must be unique.",
                goal_duplicates,
            )
        trace_goal_ids = set(trace_goal_id_list)
        if trace_goal_ids != goal_ids:
            self.error(
                "TRACE_GOAL_ID_SET_MISMATCH",
                self.relative(path),
                "Traceability goal IDs differ from GOAL_CONTRACT.json.",
                trace_goal_ids.symmetric_difference(goal_ids),
            )
        for identifier, cells in goal_rows:
            if any(not cells[index].strip() for index in (1, 2, 3)):
                self.error(
                    "GOAL_TRACE_INCOMPLETE",
                    self.relative(path),
                    "A goal metric lacks metric, design/plan, or evidence text.",
                    [identifier],
                )
            traced_scenarios.update(
                self.expand_references(
                    cells[3], scenario_ids, self.relative(path) + ":" + identifier
                )
            )
        untraced = scenario_ids - traced_scenarios
        if untraced:
            self.error(
                "SCENARIO_TRACEABILITY_MISSING",
                self.relative(path),
                "Mandatory scenarios are absent from requirement/goal traceability.",
                untraced,
            )
        self.stats["requirements"] = {
            "count": len(requirement_id_list),
            "duplicate_ids": len(requirement_duplicates),
            "expected_count": expected_requirement_count,
            "ids_sha256": _sha256(
                "\n".join(sorted(requirement_ids)).encode("ascii")
            ),
            "baseline_ids_sha256": baseline_requirement_hash,
            "declared_range_covered": len(declared_requirement_coverage),
            "openapi_covered": 0,
            "with_acceptance": sum(
                bool(
                    self.expand_references(
                        cells[4], scenario_ids, "traceability-count"
                    )
                )
                for _, cells in requirement_rows
            ),
        }
        self.stats["scenarios"]["traceability_covered"] = len(
            scenario_ids & traced_scenarios
        )
        return requirement_ids, traced_scenarios

    def audit_backlog(
        self, text: str, path: Path, scenario_ids: set[str]
    ) -> set[str]:
        covered = self.expand_references(text, scenario_ids, self.relative(path))
        missing = scenario_ids - covered
        if missing:
            self.error(
                "SCENARIO_BACKLOG_MISSING",
                self.relative(path),
                "Mandatory scenarios are not assigned in the ordered backlog.",
                missing,
            )
        self.stats["scenarios"]["backlog_covered"] = len(
            scenario_ids & covered
        )
        return covered

    def _deref(self, value: Any, origin: Path) -> tuple[Any, Path]:
        current = value
        current_origin = origin
        seen: set[tuple[Path, str]] = set()
        while isinstance(current, dict) and "$ref" in current:
            reference = current.get("$ref")
            if not isinstance(reference, str):
                break
            key = (current_origin, reference)
            if key in seen:
                break
            seen.add(key)
            resolved = self.resolve_ref(current_origin, reference)
            if resolved is None:
                break
            current, current_origin = resolved
        return current, current_origin

    def _schema_requires_property(
        self,
        schema: Any,
        origin: Path,
        property_name: str,
        seen: set[tuple[Path, int, str]] | None = None,
    ) -> bool:
        seen = set() if seen is None else seen
        schema, origin = self._deref(schema, origin)
        key = (origin, id(schema), property_name)
        if key in seen:
            return False
        seen.add(key)
        if not isinstance(schema, dict):
            return False
        if property_name in schema.get("required", []):
            return True
        if isinstance(schema.get("allOf"), list) and any(
            self._schema_requires_property(branch, origin, property_name, seen)
            for branch in schema["allOf"]
        ):
            return True
        for combinator in ("oneOf", "anyOf"):
            branches = schema.get(combinator)
            if isinstance(branches, list) and branches:
                return all(
                    self._schema_requires_property(
                        branch, origin, property_name, set(seen)
                    )
                    for branch in branches
                )
        return False

    def _parameter_objects(
        self, parameters: Any, origin: Path
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if not isinstance(parameters, list):
            return result
        for parameter in parameters:
            resolved, _ = self._deref(parameter, origin)
            if isinstance(resolved, dict):
                result.append(resolved)
        return result

    def _response_schema(
        self, response: Any, origin: Path
    ) -> tuple[Any, Path, str | None]:
        response, response_origin = self._deref(response, origin)
        if not isinstance(response, dict):
            return None, response_origin, None
        content = response.get("content")
        if not isinstance(content, dict):
            return None, response_origin, None
        if "application/json" in content:
            media = content["application/json"]
            media_type = "application/json"
        elif "text/event-stream" in content:
            media = content["text/event-stream"]
            media_type = "text/event-stream"
        else:
            return None, response_origin, None
        if not isinstance(media, dict):
            return None, response_origin, media_type
        return media.get("schema"), response_origin, media_type

    def _audit_source_artifact_download_response(
        self, response: Any, origin: Path, location: str
    ) -> None:
        """Keep the exact SBE-8.12 binary exception separate from JSON envelopes."""
        response, response_origin = self._deref(response, origin)
        content = response.get("content", {}) if isinstance(response, dict) else {}
        media = content.get("*/*", {}) if isinstance(content, dict) else {}
        schema, _ = self._deref(
            media.get("schema") if isinstance(media, dict) else None,
            response_origin,
        )
        if (
            not isinstance(content, dict)
            or set(content) != {"*/*"}
            or not isinstance(schema, dict)
            or schema.get("type") != "string"
            or schema.get("format") != "binary"
        ):
            self.error(
                "OPENAPI_BINARY_RESPONSE_INVALID", location,
                "Source-artifact downloads require original-media-type */* binary content.",
            )
        headers = response.get("headers", {}) if isinstance(response, dict) else {}
        for name, expected in SOURCE_ARTIFACT_DOWNLOAD_HEADER_SCHEMAS.items():
            header, header_origin = self._deref(
                headers.get(name) if isinstance(headers, dict) else None,
                response_origin,
            )
            header_schema, _ = self._deref(
                header.get("schema") if isinstance(header, dict) else None,
                header_origin,
            )
            if (
                not isinstance(header, dict)
                or header.get("required") is not True
                or not isinstance(header_schema, dict)
                or any(header_schema.get(key) != value for key, value in expected.items())
            ):
                self.error(
                    "OPENAPI_BINARY_HEADER_INVALID", location + ".headers." + name,
                    "Source-artifact download safety and correlation headers must retain their exact constraints.",
                )

    def _request_schemas(
        self, request_body: Any, origin: Path
    ) -> list[tuple[Any, Path]]:
        request_body, request_origin = self._deref(request_body, origin)
        if not isinstance(request_body, dict):
            return []
        content = request_body.get("content")
        if not isinstance(content, dict):
            return []
        result: list[tuple[Any, Path]] = []
        for media in content.values():
            if isinstance(media, dict) and "schema" in media:
                result.append((media["schema"], request_origin))
        return result

    def human_api_operations(
        self, text: str, path: Path
    ) -> set[tuple[str, str]]:
        operations: list[tuple[str, str]] = []
        for match in HUMAN_API_OPERATION_RE.finditer(text):
            method = match.group(1).lower()
            route = match.group(2).strip()
            if route == ".../{preview_id}":
                route = "/api/knowledge/v1/publication-previews/{preview_id}"
            elif route == ".../{job_id}/cancel":
                route = "/api/knowledge/v1/jobs/{job_id}/cancel"
            elif route.startswith("..."):
                self.error(
                    "HUMAN_API_ROUTE_AMBIGUOUS",
                    self.relative(path),
                    "A shorthand ellipsis API route is not in the fixed normalization allowlist.",
                )
                continue
            route = route.split("?", 1)[0]
            if route.startswith("/spaces/"):
                route = "/api/knowledge/v1" + route
            elif route.startswith("/conversations/"):
                route = "/api/knowledge/v1" + route
            route = route.replace("{s}", "{space_id}")
            route = route.replace("{i}", "{item_id}")
            route = route.replace("{c}", "{conversation_id}")
            if "{id}" in route:
                if "/conversations/{id}" in route:
                    replacement = "{conversation_id}"
                elif "/org/units/{id}" in route:
                    replacement = "{unit_id}"
                elif "/curation/findings/{id}" in route:
                    replacement = "{finding_id}"
                elif "/change-proposals/{id}" in route:
                    replacement = "{proposal_id}"
                elif "/edges/proposals/{id}" in route:
                    replacement = "{proposal_id}"
                elif "/edges/{id}" in route:
                    replacement = "{edge_id}"
                else:
                    self.error(
                        "HUMAN_API_ROUTE_AMBIGUOUS",
                        self.relative(path),
                        "A generic {id} API route is not in the fixed normalization allowlist.",
                    )
                    continue
                route = route.replace("{id}", replacement)
            operations.append((method, route))
        duplicates = sorted(
            f"{method.upper()} {route}"
            for (method, route), count in Counter(operations).items()
            if count > 1
        )
        if duplicates:
            self.error(
                "HUMAN_API_OPERATION_DUPLICATE",
                self.relative(path),
                "The human API contract declares duplicate normalized operations.",
                duplicates,
            )
        return set(operations)

    def audit_openapi(
        self,
        openapi: Any,
        path: Path,
        requirement_ids: set[str],
        stable_errors: set[str],
        human_operations: set[tuple[str, str]],
    ) -> None:
        location = self.relative(path)
        if not isinstance(openapi, dict):
            self.error(
                "OPENAPI_INVALID", location, "The OpenAPI document must be an object."
            )
            return
        if openapi.get("openapi") != "3.1.0":
            self.error(
                "OPENAPI_VERSION_INVALID",
                location,
                "The OpenAPI version must be exactly 3.1.0.",
            )
        if openapi.get("jsonSchemaDialect") != "https://json-schema.org/draft/2020-12/schema":
            self.error(
                "OPENAPI_DIALECT_INVALID",
                location,
                "The OpenAPI JSON Schema dialect must be Draft 2020-12.",
            )
        if openapi.get("security") != [{"sessionCookie": []}]:
            self.error(
                "OPENAPI_SECURITY_INVALID",
                location,
                "Session-cookie security must be the global default.",
            )
        root_coverage = openapi.get("x-requirements-covered")
        if not isinstance(root_coverage, list) or set(root_coverage) != requirement_ids:
            actual = set(root_coverage) if isinstance(root_coverage, list) else set()
            self.error(
                "OPENAPI_ROOT_REQUIREMENT_MISMATCH",
                location,
                "OpenAPI root requirement coverage differs from TRACEABILITY.md.",
                actual.symmetric_difference(requirement_ids),
            )
        paths = openapi.get("paths")
        if not isinstance(paths, dict) or not paths:
            self.error(
                "OPENAPI_PATHS_INVALID", location, "OpenAPI paths are missing."
            )
            return
        operation_ids: list[str] = []
        operation_map: dict[str, str] = {}
        operation_requirements: set[str] = set()
        openapi_operations: set[tuple[str, str]] = set()
        method_counts: Counter[str] = Counter()
        for route, path_item in sorted(paths.items()):
            if not isinstance(route, str) or not (
                route.startswith("/api/knowledge/v1/")
                or route == "/api/knowledge/v1/spaces"
                or route.startswith("/api/admin/v1/")
                or route.startswith("/api/knowledge/v2/")
                or route.startswith("/api/admin/v2/")
            ):
                self.error(
                    "OPENAPI_NAMESPACE_INVALID",
                    location,
                    "Every route must use the knowledge-v1 or admin-v1 namespace.",
                )
                continue
            if not isinstance(path_item, dict):
                self.error(
                    "OPENAPI_PATH_ITEM_INVALID",
                    location,
                    "An OpenAPI path item is not an object.",
                )
                continue
            path_parameters = self._parameter_objects(
                path_item.get("parameters", []), path
            )
            for method, operation in sorted(path_item.items()):
                if method == "parameters":
                    continue
                if method not in ALLOWED_HTTP_METHODS:
                    self.error(
                        "OPENAPI_METHOD_INVALID",
                        location,
                        "A path contains an unsupported or unknown operation key.",
                    )
                    continue
                method_counts[method] += 1
                openapi_operations.add((method, route))
                op_location = f"{location}:{method.upper()} {route}"
                if not isinstance(operation, dict):
                    self.error(
                        "OPENAPI_OPERATION_INVALID",
                        op_location,
                        "An OpenAPI operation is not an object.",
                    )
                    continue
                operation_id = operation.get("operationId")
                if not isinstance(operation_id, str) or not operation_id:
                    self.error(
                        "OPENAPI_OPERATION_ID_MISSING",
                        op_location,
                        "Every operation needs a stable operationId.",
                    )
                else:
                    operation_ids.append(operation_id)
                    operation_map[f"{method.upper()} {route}"] = operation_id
                if not isinstance(operation.get("summary"), str) or not operation["summary"]:
                    self.error(
                        "OPENAPI_SUMMARY_MISSING",
                        op_location,
                        "Every operation needs a non-empty summary.",
                    )
                if not isinstance(operation.get("tags"), list) or not operation["tags"]:
                    self.error(
                        "OPENAPI_TAG_MISSING",
                        op_location,
                        "Every operation needs at least one tag.",
                    )
                idempotency = operation.get("x-idempotency")
                if idempotency not in ALLOWED_IDEMPOTENCY:
                    self.error(
                        "OPENAPI_IDEMPOTENCY_INVALID",
                        op_location,
                        "Every operation needs a valid x-idempotency policy.",
                    )
                if method == "get" and idempotency != "not-applicable":
                    self.error(
                        "OPENAPI_GET_IDEMPOTENCY_INVALID",
                        op_location,
                        "GET operations cannot require mutation idempotency.",
                    )
                requirements = operation.get("x-requirements")
                if not isinstance(requirements, list) or not requirements:
                    self.error(
                        "OPENAPI_REQUIREMENT_MISSING",
                        op_location,
                        "Every operation needs at least one requirement link.",
                    )
                    requirements = []
                unknown_requirements = set(requirements) - requirement_ids
                if unknown_requirements:
                    self.error(
                        "OPENAPI_REQUIREMENT_UNKNOWN",
                        op_location,
                        "An operation links an unknown requirement.",
                        unknown_requirements,
                    )
                if len(requirements) != len(set(requirements)):
                    self.error(
                        "OPENAPI_REQUIREMENT_DUPLICATE",
                        op_location,
                        "An operation repeats a requirement link.",
                    )
                operation_requirements.update(set(requirements) & requirement_ids)

                parameters = path_parameters + self._parameter_objects(
                    operation.get("parameters", []), path
                )
                parameter_names = {
                    item.get("name")
                    for item in parameters
                    if isinstance(item.get("name"), str)
                }
                declared_path_names = {
                    item.get("name")
                    for item in parameters
                    if item.get("in") == "path" and item.get("required") is True
                }
                placeholders = set(PATH_PARAMETER_RE.findall(route))
                if declared_path_names != placeholders:
                    self.error(
                        "OPENAPI_PATH_PARAMETER_MISMATCH",
                        op_location,
                        "Required path parameters do not exactly match route placeholders.",
                        declared_path_names.symmetric_difference(placeholders),
                    )
                if method in MUTATING_HTTP_METHODS:
                    if "X-CSRF-Token" not in parameter_names:
                        self.error(
                            "OPENAPI_CSRF_MISSING",
                            op_location,
                            "Every non-GET operation must declare the CSRF header.",
                        )
                    if "R-031" not in requirements:
                        self.error(
                            "OPENAPI_SECURITY_TRACE_MISSING",
                            op_location,
                            "Every non-GET operation must trace the CSRF/origin requirement.",
                        )
                if idempotency == "required":
                    if "Idempotency-Key" not in parameter_names:
                        self.error(
                            "OPENAPI_IDEMPOTENCY_HEADER_MISSING",
                            op_location,
                            "An idempotent mutation must declare Idempotency-Key.",
                        )
                    request_schemas = self._request_schemas(
                        operation.get("requestBody"), path
                    )
                    if not request_schemas or any(
                        not self._schema_requires_property(
                            schema, schema_origin, "operation_id"
                        )
                        for schema, schema_origin in request_schemas
                    ):
                        self.error(
                            "OPENAPI_OPERATION_ID_REQUEST_MISSING",
                            op_location,
                            "Every idempotent mutation media type must require operation_id.",
                        )
                elif idempotency == "lease-conditional" and "Idempotency-Key" in parameter_names:
                    self.error(
                        "OPENAPI_HEARTBEAT_IDEMPOTENCY_INVALID",
                        op_location,
                        "Lease heartbeat must not declare operation idempotency.",
                    )

                responses = operation.get("responses")
                if not isinstance(responses, dict):
                    self.error(
                        "OPENAPI_RESPONSES_MISSING",
                        op_location,
                        "Every operation needs responses.",
                    )
                    continue
                success_items = [
                    (status, response)
                    for status, response in responses.items()
                    if isinstance(status, str) and status.startswith("2")
                ]
                if not success_items:
                    self.error(
                        "OPENAPI_SUCCESS_RESPONSE_MISSING",
                        op_location,
                        "Every operation needs at least one 2xx response.",
                    )
                if (
                    method == "get"
                    and route in {SOURCE_ARTIFACT_LIST_ROUTE, SOURCE_ARTIFACT_DOWNLOAD_ROUTE}
                    and {status for status, _ in success_items} != {"200"}
                ):
                    self.error(
                        "OPENAPI_SOURCE_ARTIFACT_SUCCESS_STATUS_INVALID",
                        op_location,
                        "Source-artifact GET operations must declare exactly HTTP 200 as success.",
                    )
                if "default" not in responses:
                    self.error(
                        "OPENAPI_ERROR_RESPONSE_MISSING",
                        op_location,
                        "Every operation needs the stable default error response.",
                    )
                for _, response in success_items:
                    if method == "get" and route == SOURCE_ARTIFACT_DOWNLOAD_ROUTE:
                        self._audit_source_artifact_download_response(response, path, op_location)
                        continue
                    schema, schema_origin, media_type = self._response_schema(
                        response, path
                    )
                    if media_type == "text/event-stream":
                        response_object, _ = self._deref(response, path)
                        headers = (
                            response_object.get("headers", {})
                            if isinstance(response_object, dict)
                            else {}
                        )
                        if "X-Request-ID" not in headers:
                            self.error(
                                "OPENAPI_STREAM_REQUEST_ID_HEADER_MISSING",
                                op_location,
                                "A streaming response must declare X-Request-ID.",
                            )
                    if schema is None or not self._schema_requires_property(
                        schema, schema_origin, "request_id"
                    ):
                        self.error(
                            "OPENAPI_REQUEST_ID_RESPONSE_MISSING",
                            op_location,
                            "Every JSON success response must require request_id.",
                        )
                    if idempotency == "required" and (
                        schema is None
                        or not self._schema_requires_property(
                            schema, schema_origin, "operation_id"
                        )
                    ):
                        self.error(
                            "OPENAPI_OPERATION_ID_RESPONSE_MISSING",
                            op_location,
                            "Every idempotent mutation response must require operation_id.",
                        )

        duplicate_operation_ids = sorted(
            item for item, count in Counter(operation_ids).items() if count > 1
        )
        if duplicate_operation_ids:
            self.error(
                "OPENAPI_OPERATION_ID_DUPLICATE",
                location,
                "OpenAPI operation IDs must be unique.",
                duplicate_operation_ids,
            )
        expected_count_set = (
            REGISTERED_COUNTS if self.successor_state == "registered" else BASELINE_COUNTS
        )
        if len(paths) != expected_count_set["openapi_paths"]:
            self.error(
                "OPENAPI_PATH_COUNT_MISMATCH",
                location,
                "OpenAPI path count does not match the transition state.",
            )
        if len(operation_ids) != expected_count_set["openapi_operations"]:
            self.error(
                "OPENAPI_OPERATION_COUNT_MISMATCH",
                location,
                "OpenAPI operation count does not match the transition state.",
            )
        expected_successor_map = SUCCESSOR_PATH_OPERATIONS
        actual_successor_map = {
            key: value
            for key, value in operation_map.items()
            if key in expected_successor_map
        }
        if self.successor_state == "registered" and actual_successor_map != expected_successor_map:
            self.error(
                "OPENAPI_SUCCESSOR_DELTA_MISMATCH",
                location,
                "The exact successor delta (18 paths/22 operations plus SBE-8.12 2 paths/2 operations) drifted.",
                set(expected_successor_map).symmetric_difference(actual_successor_map),
            )
        if self.successor_state == "proposed" and actual_successor_map:
            self.error(
                "OPENAPI_SUCCESSOR_PREMATURE_REGISTRATION",
                location,
                "Successor operations cannot be active while state is proposed.",
                actual_successor_map,
            )
        successor_operation_ids = set(SUCCESSOR_PATH_OPERATIONS.values())
        baseline_operation_ids = set(operation_ids) - successor_operation_ids
        baseline_operation_hash = _sha256(
            "\n".join(sorted(baseline_operation_ids)).encode("ascii")
        )
        if baseline_operation_hash != BASELINE_HASHES["openapi_operations"]:
            self.error(
                "SUCCESSOR_BASELINE_OPERATION_DRIFT",
                location,
                "The frozen 56-operation OpenAPI baseline changed during transition.",
            )
        missing_requirement_links = requirement_ids - operation_requirements
        if missing_requirement_links:
            self.error(
                "OPENAPI_REQUIREMENT_COVERAGE_MISSING",
                location,
                "Requirements are absent from operation-level OpenAPI links.",
                missing_requirement_links,
            )
        if openapi_operations != human_operations:
            mismatch = {
                f"{method.upper()} {route}"
                for method, route in openapi_operations.symmetric_difference(
                    human_operations
                )
            }
            self.error(
                "OPENAPI_HUMAN_OPERATION_MISMATCH",
                location,
                "OpenAPI operations differ from normalized API_UI_CONTRACT.md operations.",
                mismatch,
            )
        common_schema = self.load_json(
            self.contract_root / "schemas" / "common.schema.json"
        )
        schema_errors: set[str] = set()
        if isinstance(common_schema, dict):
            schema_errors_value = (
                common_schema.get("$defs", {})
                .get("ErrorCode", {})
                .get("enum", [])
            )
            if isinstance(schema_errors_value, list):
                schema_errors = set(schema_errors_value)
        if schema_errors != stable_errors:
            self.error(
                "STABLE_ERROR_SET_MISMATCH",
                "contracts/schemas/common.schema.json",
                "Machine error codes differ from API_UI_CONTRACT.md.",
                schema_errors.symmetric_difference(stable_errors),
            )
        if len(schema_errors) != expected_count_set["stable_errors"]:
            self.error(
                "STABLE_ERROR_COUNT_MISMATCH",
                "contracts/schemas/common.schema.json",
                "Stable public error count does not match the transition state.",
            )
        if self.successor_state == "registered" and not SUCCESSOR_ERRORS <= schema_errors:
            self.error(
                "STABLE_ERROR_SUCCESSOR_DELTA_MISMATCH",
                "contracts/schemas/common.schema.json",
                "The exact 14-code successor error delta is incomplete.",
                SUCCESSOR_ERRORS - schema_errors,
            )
        self.stats["openapi"] = {
            "method_counts": dict(sorted(method_counts.items())),
            "operation_count": len(operation_ids),
            "human_contract_operation_count": len(human_operations),
            "operation_id_duplicates": len(duplicate_operation_ids),
            "operation_ids_sha256": _sha256(
                "\n".join(sorted(set(operation_ids))).encode("ascii")
            ),
            "baseline_operation_ids_sha256": baseline_operation_hash,
            "path_count": len(paths),
            "requirement_coverage": len(
                requirement_ids & operation_requirements
            ),
            "stable_error_count": len(schema_errors),
        }
        self.stats["requirements"]["openapi_covered"] = len(
            requirement_ids & operation_requirements
        )

    def audit_schema_documents(self) -> None:
        if not self.contract_root.exists() or not self.contract_root.is_dir():
            self.error(
                "CONTRACT_ROOT_MISSING",
                "contracts",
                "The contracts directory is missing.",
            )
            return
        schema_files: list[Path] = []
        for candidate in sorted(self.contract_root.rglob("*.schema.json")):
            safe = self._safe_path(candidate)
            if safe is not None:
                schema_files.append(safe)
        schema_ids: list[str] = []
        for path in schema_files:
            value = self.load_json(path)
            if not isinstance(value, dict):
                continue
            if value.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
                self.error(
                    "SCHEMA_DIALECT_INVALID",
                    self.relative(path),
                    "Every JSON Schema must declare Draft 2020-12.",
                )
            schema_id = value.get("$id")
            if not isinstance(schema_id, str) or not schema_id:
                self.error(
                    "SCHEMA_ID_MISSING",
                    self.relative(path),
                    "Every JSON Schema needs a stable $id.",
                )
            else:
                schema_ids.append(schema_id)
            if not isinstance(value.get("title"), str) or not value["title"]:
                self.error(
                    "SCHEMA_TITLE_MISSING",
                    self.relative(path),
                    "Every JSON Schema needs a title.",
                )
            self.audit_schema_node(value, path, "$", 0)
            self.walk_references(value, path)
        duplicate_ids = sorted(
            item for item, count in Counter(schema_ids).items() if count > 1
        )
        if duplicate_ids:
            self.error(
                "SCHEMA_ID_DUPLICATE",
                "contracts/schemas",
                "JSON Schema $id values must be unique.",
                duplicate_ids,
            )
        self.stats["schemas"] = {
            "document_count": len(schema_files),
            "duplicate_ids": len(duplicate_ids),
        }

    def audit_schema_node(
        self, schema: Any, origin: Path, schema_location: str, depth: int
    ) -> None:
        if depth > 100:
            self.error(
                "SCHEMA_RECURSION_INVALID",
                self.relative(origin),
                "A JSON Schema syntax tree exceeds the audit recursion limit.",
            )
            return
        if isinstance(schema, bool):
            return
        if not isinstance(schema, dict):
            self.error(
                "SCHEMA_NODE_INVALID",
                self.relative(origin),
                "A JSON Schema node must be an object or boolean.",
            )
            return
        declared_type = schema.get("type")
        if declared_type is not None:
            type_names = [declared_type] if isinstance(declared_type, str) else declared_type
            allowed = {"array", "boolean", "integer", "null", "number", "object", "string"}
            if (
                not isinstance(type_names, list)
                or not type_names
                or not all(isinstance(item, str) and item in allowed for item in type_names)
                or len(type_names) != len(set(type_names))
            ):
                self.error(
                    "SCHEMA_TYPE_INVALID",
                    self.relative(origin),
                    "A JSON Schema type declaration is invalid or duplicated.",
                )
        required = schema.get("required")
        if required is not None and (
            not isinstance(required, list)
            or not all(isinstance(item, str) for item in required)
            or len(required) != len(set(required))
        ):
            self.error(
                "SCHEMA_REQUIRED_INVALID",
                self.relative(origin),
                "A JSON Schema required list is invalid or duplicated.",
            )
        enum = schema.get("enum")
        if enum is not None:
            if not isinstance(enum, list) or not enum:
                self.error(
                    "SCHEMA_ENUM_INVALID",
                    self.relative(origin),
                    "A JSON Schema enum must be a non-empty array.",
                )
            else:
                try:
                    encoded = [canonical_json(item) for item in enum]
                except (TypeError, ValueError):
                    encoded = []
                    self.error(
                        "SCHEMA_ENUM_INVALID",
                        self.relative(origin),
                        "A JSON Schema enum contains a non-JSON value.",
                    )
                if encoded and len(encoded) != len(set(encoded)):
                    self.error(
                        "SCHEMA_ENUM_DUPLICATE",
                        self.relative(origin),
                        "A JSON Schema enum contains duplicate values.",
                    )
        pattern = schema.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                self.error(
                    "SCHEMA_PATTERN_INVALID",
                    self.relative(origin),
                    "A JSON Schema pattern must be a string.",
                )
            else:
                try:
                    re.compile(pattern)
                except re.error:
                    self.error(
                        "SCHEMA_PATTERN_INVALID",
                        self.relative(origin),
                        "A JSON Schema pattern does not compile.",
                    )
        for numeric_key in ("maxItems", "maxLength", "maximum", "minItems", "minLength", "minimum"):
            value = schema.get(numeric_key)
            if value is not None and (
                not isinstance(value, (int, float)) or isinstance(value, bool)
            ):
                self.error(
                    "SCHEMA_BOUND_INVALID",
                    self.relative(origin),
                    "A JSON Schema numeric bound is invalid.",
                )
        for mapping_key in ("$defs", "patternProperties", "properties"):
            mapping = schema.get(mapping_key)
            if mapping is None:
                continue
            if not isinstance(mapping, dict):
                self.error(
                    "SCHEMA_MAPPING_INVALID",
                    self.relative(origin),
                    "A JSON Schema child-schema map is invalid.",
                )
                continue
            for name, child in mapping.items():
                self.audit_schema_node(
                    child,
                    origin,
                    f"{schema_location}/{mapping_key}/{name}",
                    depth + 1,
                )
        for single_key in (
            "additionalProperties",
            "contains",
            "else",
            "if",
            "items",
            "not",
            "then",
            "unevaluatedProperties",
        ):
            child = schema.get(single_key)
            if child is not None:
                self.audit_schema_node(
                    child,
                    origin,
                    f"{schema_location}/{single_key}",
                    depth + 1,
                )
        for sequence_key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            children = schema.get(sequence_key)
            if children is None:
                continue
            if not isinstance(children, list) or not children:
                self.error(
                    "SCHEMA_COMBINATOR_INVALID",
                    self.relative(origin),
                    "A JSON Schema combinator must be a non-empty array.",
                )
                continue
            for index, child in enumerate(children):
                self.audit_schema_node(
                    child,
                    origin,
                    f"{schema_location}/{sequence_key}/{index}",
                    depth + 1,
                )

    def audit_examples(self, manifest: Mapping[str, Any]) -> None:
        examples_path = self.manifest_path(manifest, "examples")
        examples = self.load_json(examples_path) if examples_path is not None else None
        if not isinstance(examples, dict):
            self.error(
                "CONTRACT_EXAMPLES_INVALID",
                self.relative(examples_path or self.contract_root / "examples.json"),
                "The contract example registry is missing or invalid.",
            )
            self.stats["schemas"]["example_count"] = 0
            return
        roles = manifest.get("schema_roles", {})
        expected_roles = REQUIRED_SCHEMA_ROLES - {"goal_contract"}
        actual_roles = set(examples)
        if actual_roles != expected_roles:
            self.error(
                "CONTRACT_EXAMPLE_SET_MISMATCH",
                self.relative(examples_path or self.contract_root / "examples.json"),
                "The example registry must cover every non-goal schema role exactly once.",
                actual_roles.symmetric_difference(expected_roles),
            )
        valid_count = 0
        for role in sorted(expected_roles & actual_roles):
            reference = roles.get(role) if isinstance(roles, dict) else None
            if not isinstance(reference, str):
                continue
            resolved = self.resolve_ref(
                self.contract_root / "contract-manifest.json", reference
            )
            if resolved is None:
                continue
            schema, schema_path = resolved
            failures = self.validate_instance(
                examples[role], schema, schema_path, f"$.{role}"
            )
            if failures:
                for failure in failures[:20]:
                    self.error(
                        "CONTRACT_EXAMPLE_SCHEMA_VIOLATION",
                        self.relative(examples_path or self.contract_root / "examples.json"),
                        failure,
                        [role],
                    )
            else:
                valid_count += 1
        self.stats["schemas"]["example_count"] = len(examples)
        self.stats["schemas"]["valid_example_count"] = valid_count

    def _verify_high_water(self) -> None:
        for path, expected in sorted(
            self._source_bytes.items(), key=lambda item: self.relative(item[0])
        ):
            try:
                current = path.read_bytes()
            except OSError:
                self.error(
                    "SOURCE_CHANGED",
                    self.relative(path),
                    "A contract source disappeared during the audit.",
                )
                continue
            if current != expected:
                self.error(
                    "SOURCE_CHANGED",
                    self.relative(path),
                    "A contract source changed during the audit.",
                )

    def run(self) -> tuple[dict[str, Any], int]:
        if not self.docs_root.exists() or not self.docs_root.is_dir():
            self.error(
                "DOCS_ROOT_INVALID",
                "<docs-root>",
                "The explicit documentation root is missing or not a directory.",
            )
            return self.report(), 2
        manifest_path = self.docs_root / CONTRACT_MANIFEST
        manifest = self.load_json(manifest_path)
        self.audit_manifest(manifest)
        if not isinstance(manifest, dict):
            self._verify_high_water()
            return self.report(), 2

        goal_path = self.manifest_path(manifest, "goal_contract")
        openapi_path = self.manifest_path(manifest, "openapi")
        documents = self.document_paths(manifest)
        goal = self.load_json(goal_path) if goal_path is not None else None
        goal_ids, requirement_count, scenario_count = self.audit_goal(
            goal, goal_path or self.docs_root / "GOAL_CONTRACT.json", manifest
        )
        scenario_path = documents.get("scenarios")
        scenario_text = self.read_text(scenario_path) if scenario_path else ""
        scenario_ids, _ = self.audit_scenarios(
            scenario_text,
            scenario_path or self.docs_root / "TEST_OPERATIONS.md",
            scenario_count,
        )
        trace_path = documents.get("traceability")
        trace_text = self.read_text(trace_path) if trace_path else ""
        requirement_ids, _ = self.audit_traceability(
            trace_text,
            trace_path or self.docs_root / "TRACEABILITY.md",
            requirement_count,
            goal_ids,
            scenario_ids,
        )
        backlog_path = documents.get("backlog")
        backlog_text = self.read_text(backlog_path) if backlog_path else ""
        self.audit_backlog(
            backlog_text,
            backlog_path or self.docs_root / "IMPLEMENTATION_BACKLOG.md",
            scenario_ids,
        )
        api_path = documents.get("api_ui")
        api_text = self.read_text(api_path) if api_path else ""
        stable_errors = set(STABLE_ERROR_ROW_RE.findall(api_text))
        human_operations = self.human_api_operations(
            api_text, api_path or self.docs_root / "API_UI_CONTRACT.md"
        )
        if not stable_errors:
            self.error(
                "STABLE_ERROR_TABLE_MISSING",
                self.relative(api_path or self.docs_root / "API_UI_CONTRACT.md"),
                "The stable API error table is missing.",
            )
        openapi = self.load_json(openapi_path) if openapi_path is not None else None
        if openapi_path is not None:
            self.walk_references(openapi, openapi_path)
        self.audit_schema_documents()
        self.audit_examples(manifest)
        self.audit_openapi(
            openapi,
            openapi_path or self.contract_root / "openapi.json",
            requirement_ids,
            stable_errors,
            human_operations,
        )
        self.stats["schemas"]["reference_count"] = self.reference_count
        self.stats["schemas"]["resolved_reference_count"] = (
            self.resolved_reference_count
        )
        self._verify_high_water()
        report = self.report()
        return report, 0 if report["status"] == "pass" else 2

    def report(self) -> dict[str, Any]:
        source_files = [
            {
                "path": self.relative(path),
                "sha256": _sha256(raw),
                "size_bytes": len(raw),
            }
            for path, raw in sorted(
                self._source_bytes.items(), key=lambda item: self.relative(item[0])
            )
        ]
        fingerprint_payload = "\n".join(
            f"{entry['path']}\0{entry['size_bytes']}\0{entry['sha256']}"
            for entry in source_files
        ).encode("utf-8")
        errors = sorted(
            self.errors,
            key=lambda item: (
                item["code"],
                item["location"],
                item["message"],
                tuple(item.get("ids", [])),
            ),
        )
        return {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "status": "pass" if not errors else "fail",
            "error_count": len(errors),
            "errors": errors,
            "input_sha256": _sha256(fingerprint_payload),
            "source_file_count": len(source_files),
            "source_files": source_files,
            "summary": self.stats,
        }


def audit_contracts(
    docs_root: str | os.PathLike[str],
) -> tuple[dict[str, Any], int]:
    """Public in-process contract-audit API (report schema v2)."""

    try:
        auditor = ContractAuditor(docs_root)
    except (OSError, ValueError):
        report = {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "status": "fail",
            "error_count": 1,
            "errors": [
                {
                    "code": "DOCS_ROOT_INVALID",
                    "location": "<docs-root>",
                    "message": "The documentation root must be an explicit absolute directory path.",
                }
            ],
            "input_sha256": _sha256(b""),
            "source_file_count": 0,
            "source_files": [],
            "summary": {
                "goal": {},
                "openapi": {},
                "requirements": {},
                "scenarios": {},
                "schemas": {},
                "successor_transition": {},
            },
        }
        return report, 2
    return auditor.run()


def _write_report(path_value: str, report: Mapping[str, Any], replace: bool) -> int:
    payload = (canonical_json(report) + "\n").encode("ascii")
    if path_value == "-":
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return 0
    destination = Path(path_value)
    if not destination.is_absolute():
        return 3
    destination = destination.resolve(strict=False)
    if not destination.parent.exists() or not destination.parent.is_dir():
        return 3
    if destination.exists() and (not replace or not destination.is_file()):
        return 3
    descriptor = -1
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".contract-audit-", suffix=".tmp", dir=destination.parent
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, destination)
            temporary = None
        else:
            os.link(temporary, destination)
            os.unlink(temporary)
            temporary = None
        return 0
    except OSError:
        return 3
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = _JsonParser(prog="contract_audit")
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit")
    audit.add_argument("--docs-root", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--replace-output", action="store_true")
    return parser


def _failure_report(code: str, message: str) -> dict[str, Any]:
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "status": "fail",
        "error_count": 1,
        "errors": [{"code": code, "location": "<cli>", "message": message}],
        "input_sha256": _sha256(b""),
        "source_file_count": 0,
        "source_files": [],
        "summary": {
            "goal": {},
            "openapi": {},
            "requirements": {},
            "scenarios": {},
            "schemas": {},
            "successor_transition": {},
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
    except CliArgumentError:
        report = _failure_report(
            "CLI_INVALID_ARGUMENT", "The contract-audit arguments are invalid."
        )
        sys.stdout.buffer.write((canonical_json(report) + "\n").encode("ascii"))
        return 2
    try:
        docs_root = Path(arguments.docs_root)
        output = arguments.output
        if output != "-":
            output_path = Path(output)
            if not output_path.is_absolute():
                report = _failure_report(
                    "OUTPUT_PATH_INVALID",
                    "Audit output must be '-' or an explicit absolute file path.",
                )
                _write_report("-", report, False)
                return 3
            if not output_path.parent.exists() or not output_path.parent.is_dir():
                report = _failure_report(
                    "OUTPUT_PARENT_INVALID",
                    "The audit output parent must already exist as a directory.",
                )
                _write_report("-", report, False)
                return 3
            if output_path.exists() and (
                not arguments.replace_output or not output_path.is_file()
            ):
                report = _failure_report(
                    "OUTPUT_EXISTS",
                    "The audit output exists and cannot be replaced under the requested policy.",
                )
                _write_report("-", report, False)
                return 3
            if output_path.is_absolute() and docs_root.is_absolute():
                resolved_docs = docs_root.resolve(strict=False)
                resolved_output = output_path.resolve(strict=False)
                if _is_relative_to(resolved_output, resolved_docs):
                    report = _failure_report(
                        "OUTPUT_OVERLAPS_SOURCE",
                        "Audit output cannot be written inside the documentation source root.",
                    )
                    write_code = _write_report("-", report, False)
                    return 2 if write_code == 0 else 3
        report, audit_code = audit_contracts(docs_root)
        write_code = _write_report(output, report, arguments.replace_output)
        if write_code:
            failure = _failure_report(
                "OUTPUT_WRITE_FAILED",
                "The audit report could not be committed atomically.",
            )
            _write_report("-", failure, False)
            return 3
        return audit_code
    except Exception:
        report = _failure_report(
            "CONTRACT_AUDIT_INTERNAL_ERROR",
            "The contract audit failed without exposing internal details.",
        )
        sys.stdout.buffer.write((canonical_json(report) + "\n").encode("ascii"))
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

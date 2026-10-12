"""Deterministic, create-only release-candidate freeze for collaboration R0.

The freeze binds product source, dependency locks, schema/migrations, protocol,
frontend assets, and a separately hashed validation inventory.  It creates no
archive, starts no process, reads no runtime root, and grants no deployment or
canary authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


RELEASE_CANDIDATE_CONTRACT = (
    "code_assist.notion.internal_collaborative_release_candidate.v1"
)
RELEASE_VERIFIER_CONTRACT = (
    "code_assist.notion.internal_collaborative_release_candidate_verifier.v1"
)
INVENTORY_POLICY_ID = "internal-collaboration-r0-release-inventory-v1"
_GENERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SKIP_PARTS = frozenset({".git", ".runtime", ".tmp", "__pycache__", "node_modules"})

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_ROOT = PROJECT_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
DEFAULT_SCHEMA_CATALOG = CONTRACT_ROOT / "internal_collaborative_completion_matrix.v1.json"

DEFAULT_RELEASE_PATTERNS: Mapping[str, tuple[str, ...]] = {
    "product_source": (
        "code_assist_v1/*.py",
        "code_assist_v1/notion_collaboration/**/*",
        "code_assist_v1/knowledge_core/**/*",
        "code_assist_v1/notion_static/**/*",
        "code_assist_v1/collaboration_runtime/**/*",
    ),
    "dependency_locks": (
        "code_assist_v1/requirements-runtime.lock",
        "code_assist_v1/collaboration_runtime/python-requirements.lock",
        "code_assist_v1/collaboration_runtime/node/package-lock.json",
    ),
    "database_schema_and_migrations": (
        "code_assist_v1/notion_*migration.py",
        "code_assist_v1/knowledge_core/migrations/**/*",
        "code_assist_v1/docs/knowledge_spaces/second_brain_evolution/FORWARD_SCHEMA.md",
    ),
    "collaboration_protocol": (
        "code_assist_v1/notion_collaboration/protocol_contract.py",
        "code_assist_v1/notion_collaboration/selected_runtime_contract.py",
        "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_protocol_contract.v1.json",
        "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_gateway_contract.v1.json",
        "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_crdt_winner_adr.v1.json",
        "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_gateway_topology_adr.v1.json",
        "code_assist_v1/collaboration_runtime/ARTIFACT_MANIFEST.json",
    ),
    "frontend_build": (
        "code_assist_v1/notion_static/**/*",
    ),
    "validation_source": (
        "code_assist_v1/tests/notion_collaboration/**/*.py",
        "code_assist_v1/tests/browser/test_notion_ui*.py",
        "code_assist_v1/tools/internal_collaborative*.py",
        "code_assist_v1/tools/freeze_internal_collaborative_release.py",
        "code_assist_v1/tools/run_internal_collaborative_validation.py",
        "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_packets.v2.json",
        "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_validation_ladder.v1.json",
        "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_completion_matrix.v1.json",
        "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_execution_receipt.v1.schema.json",
    ),
}

_CATEGORY_ORDER = (
    "product_source",
    "dependency_locks",
    "database_schema_and_migrations",
    "collaboration_protocol",
    "frontend_build",
    "validation_source",
)
_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "contract",
        "manifest_id",
        "recorded_at",
        "inventory_policy_id",
        "deployment_generation_id",
        "release_candidate_id",
        "artifact_manifest_sha256",
        "derivation_proof_sha256",
        "source_manifest_sha256",
        "validation_manifest_sha256",
        "schema_catalog_sha256",
        "canonical_inputs",
        "categories",
        "claims",
    }
)


class ReleaseCandidateError(ValueError):
    """A release inventory, manifest, or freeze operation is invalid."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReleaseCandidateError("canonical JSON value required") from exc


def canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseCandidateError("duplicate JSON key")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_bytes().decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateError("valid UTF-8 JSON manifest required") from exc
    if not isinstance(value, dict):
        raise ReleaseCandidateError("release manifest must be an object")
    return value


def _validate_generation_id(value: str) -> str:
    if not isinstance(value, str) or _GENERATION_ID.fullmatch(value) is None:
        raise ReleaseCandidateError(
            "deployment_generation_id must be an opaque 8-128 character identifier"
        )
    return value


def _validate_patterns(patterns: Mapping[str, Sequence[str]]) -> None:
    if set(patterns) != set(_CATEGORY_ORDER):
        raise ReleaseCandidateError("release inventory category set is not exact")
    for category in _CATEGORY_ORDER:
        values = patterns[category]
        if not isinstance(values, (tuple, list)) or not values:
            raise ReleaseCandidateError(f"{category} patterns are required")
        for pattern in values:
            candidate = Path(pattern)
            if not isinstance(pattern, str) or not pattern or candidate.is_absolute() or ".." in candidate.parts:
                raise ReleaseCandidateError(f"unsafe release inventory pattern: {pattern!r}")


def _inventory_category(
    repository_root: Path,
    category: str,
    patterns: Sequence[str],
) -> dict[str, Any]:
    root = repository_root.resolve(strict=True)
    selected: dict[str, Path] = {}
    pattern_counts: dict[str, int] = {}
    for pattern in patterns:
        matches = []
        for candidate in root.glob(pattern):
            if not candidate.is_file() or any(part in _SKIP_PARTS for part in candidate.parts):
                continue
            resolved = candidate.resolve(strict=True)
            if candidate.is_symlink() or not resolved.is_relative_to(root):
                raise ReleaseCandidateError(f"{category} inventory contains a symlink or escaped path")
            relative = resolved.relative_to(root).as_posix()
            selected[relative] = resolved
            matches.append(relative)
        if not matches:
            raise ReleaseCandidateError(f"release inventory pattern matched no files: {pattern}")
        pattern_counts[pattern] = len(set(matches))
    files = [
        {
            "path": relative,
            "bytes": selected[relative].stat().st_size,
            "sha256": sha256_file(selected[relative]),
        }
        for relative in sorted(selected)
    ]
    if not files:
        raise ReleaseCandidateError(f"{category} inventory is empty")
    return {
        "patterns": list(patterns),
        "pattern_match_counts": pattern_counts,
        "file_count": len(files),
        "files": files,
        "manifest_sha256": canonical_sha256(files),
    }


def build_release_candidate_manifest(
    *,
    repository_root: Path = REPOSITORY_ROOT,
    deployment_generation_id: str,
    recorded_at: str | None = None,
    patterns: Mapping[str, Sequence[str]] = DEFAULT_RELEASE_PATTERNS,
    schema_catalog_path: Path = DEFAULT_SCHEMA_CATALOG,
) -> dict[str, Any]:
    """Build a deterministic release manifest from the current checked-in tree."""

    generation = _validate_generation_id(deployment_generation_id)
    _validate_patterns(patterns)
    root = Path(repository_root).resolve(strict=True)
    schema_path = Path(schema_catalog_path)
    if not schema_path.is_absolute():
        schema_path = root / schema_path
    schema_path = schema_path.resolve(strict=True)
    if not schema_path.is_file() or not schema_path.is_relative_to(root):
        raise ReleaseCandidateError("schema catalog must stay inside the repository")
    categories = {
        category: _inventory_category(root, category, patterns[category])
        for category in _CATEGORY_ORDER
    }
    canonical_inputs = {
        "source_manifest_without_runtime_evidence": categories["product_source"]["manifest_sha256"],
        "dependency_lock_hashes": categories["dependency_locks"]["manifest_sha256"],
        "database_schema_version_and_migration_hashes": categories[
            "database_schema_and_migrations"
        ]["manifest_sha256"],
        "collaboration_protocol_version": categories["collaboration_protocol"]["manifest_sha256"],
        "frontend_build_manifest_hash": categories["frontend_build"]["manifest_sha256"],
    }
    release_candidate_id = canonical_sha256(canonical_inputs)
    artifact_manifest = {
        "inventory_policy_id": INVENTORY_POLICY_ID,
        "release_candidate_id": release_candidate_id,
        "deployment_generation_id": generation,
        "product_source_manifest_sha256": categories["product_source"]["manifest_sha256"],
        "dependency_lock_manifest_sha256": categories["dependency_locks"]["manifest_sha256"],
        "schema_migration_manifest_sha256": categories["database_schema_and_migrations"]["manifest_sha256"],
        "protocol_manifest_sha256": categories["collaboration_protocol"]["manifest_sha256"],
        "frontend_manifest_sha256": categories["frontend_build"]["manifest_sha256"],
    }
    derivation_proof = {
        "algorithm": "sha256_canonical_json_v1",
        "release_candidate_id": release_candidate_id,
        "canonical_inputs": canonical_inputs,
        "artifact_manifest_sha256": canonical_sha256(artifact_manifest),
    }
    recorded = recorded_at or _utc_now()
    try:
        parsed = datetime.fromisoformat(recorded.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseCandidateError("recorded_at must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ReleaseCandidateError("recorded_at must be an ISO-8601 UTC timestamp")
    manifest = {
        "schema_version": 1,
        "contract": RELEASE_CANDIDATE_CONTRACT,
        "manifest_id": f"RC-{release_candidate_id[7:23]}-{generation}",
        "recorded_at": recorded,
        "inventory_policy_id": INVENTORY_POLICY_ID,
        "deployment_generation_id": generation,
        "release_candidate_id": release_candidate_id,
        "artifact_manifest_sha256": canonical_sha256(artifact_manifest),
        "derivation_proof_sha256": canonical_sha256(derivation_proof),
        "source_manifest_sha256": categories["product_source"]["manifest_sha256"],
        "validation_manifest_sha256": categories["validation_source"]["manifest_sha256"],
        "schema_catalog_sha256": sha256_file(schema_path),
        "canonical_inputs": canonical_inputs,
        "categories": categories,
        "claims": {
            "runtime_evidence_excluded_from_release_candidate_id": True,
            "validation_source_bound_separately": True,
            "product_source_pre_and_post_hash_required_at_execution": True,
            "artifact_archive_created": False,
            "service_started": False,
            "production_or_runtime_root_accessed": False,
            "deployment_authorized": False,
            "canary_authorized": False,
            "product_complete": False,
        },
    }
    return manifest


def write_release_candidate_manifest_create_only(path: Path, manifest: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(dict(manifest), output, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
        output.write("\n")


def verify_release_candidate_manifest(
    path: Path,
    *,
    repository_root: Path = REPOSITORY_ROOT,
    patterns: Mapping[str, Sequence[str]] = DEFAULT_RELEASE_PATTERNS,
    schema_catalog_path: Path = DEFAULT_SCHEMA_CATALOG,
) -> tuple[dict[str, Any], int]:
    """Rebuild the inventory and reject any release or validation drift."""

    try:
        candidate = _load_json(Path(path))
        if set(candidate) != _TOP_LEVEL_KEYS:
            raise ReleaseCandidateError("release manifest field set is not exact")
        if candidate.get("schema_version") != 1 or candidate.get("contract") != RELEASE_CANDIDATE_CONTRACT:
            raise ReleaseCandidateError("release manifest contract mismatch")
        for key in (
            "release_candidate_id",
            "artifact_manifest_sha256",
            "derivation_proof_sha256",
            "source_manifest_sha256",
            "validation_manifest_sha256",
            "schema_catalog_sha256",
        ):
            if not isinstance(candidate.get(key), str) or _SHA256.fullmatch(candidate[key]) is None:
                raise ReleaseCandidateError(f"{key} must be a canonical sha256")
        rebuilt = build_release_candidate_manifest(
            repository_root=repository_root,
            deployment_generation_id=candidate.get("deployment_generation_id"),
            recorded_at=candidate.get("recorded_at"),
            patterns=patterns,
            schema_catalog_path=schema_catalog_path,
        )
        if candidate != rebuilt:
            differing = sorted(
                key for key in set(candidate) | set(rebuilt) if candidate.get(key) != rebuilt.get(key)
            )
            raise ReleaseCandidateError(f"release candidate drift: {differing}")
        return {
            "report_schema_version": 1,
            "contract": RELEASE_VERIFIER_CONTRACT,
            "status": "pass",
            "error_count": 0,
            "errors": [],
            "manifest_sha256": sha256_file(Path(path)),
            "manifest_id": candidate["manifest_id"],
            "release_candidate_id": candidate["release_candidate_id"],
            "deployment_generation_id": candidate["deployment_generation_id"],
            "artifact_manifest_sha256": candidate["artifact_manifest_sha256"],
            "derivation_proof_sha256": candidate["derivation_proof_sha256"],
            "source_manifest_sha256": candidate["source_manifest_sha256"],
            "validation_manifest_sha256": candidate["validation_manifest_sha256"],
            "schema_catalog_sha256": candidate["schema_catalog_sha256"],
            "file_counts": {
                category: candidate["categories"][category]["file_count"]
                for category in _CATEGORY_ORDER
            },
            "artifact_archive_created": False,
            "service_started": False,
            "production_or_runtime_root_accessed": False,
            "deployment_authorized": False,
            "canary_authorized": False,
            "product_complete": False,
        }, 0
    except (ReleaseCandidateError, OSError, TypeError, KeyError) as exc:
        return {
            "report_schema_version": 1,
            "contract": RELEASE_VERIFIER_CONTRACT,
            "status": "fail",
            "error_count": 1,
            "errors": [
                {
                    "code": "RELEASE_CANDIDATE_INVALID",
                    "subject": "release_manifest",
                    "message": str(exc),
                }
            ],
            "artifact_archive_created": False,
            "service_started": False,
            "production_or_runtime_root_accessed": False,
            "deployment_authorized": False,
            "canary_authorized": False,
            "product_complete": False,
        }, 1


__all__ = [
    "DEFAULT_RELEASE_PATTERNS",
    "DEFAULT_SCHEMA_CATALOG",
    "INVENTORY_POLICY_ID",
    "RELEASE_CANDIDATE_CONTRACT",
    "RELEASE_VERIFIER_CONTRACT",
    "ReleaseCandidateError",
    "build_release_candidate_manifest",
    "canonical_sha256",
    "sha256_file",
    "verify_release_candidate_manifest",
    "write_release_candidate_manifest_create_only",
]

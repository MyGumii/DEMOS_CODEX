"""Fail-closed approval boundary for the isolated E0-P2 CRDT spike.

This module is deliberately side-effect free.  It can validate the frozen E0-P1
candidate matrix, describe the exact approval request, validate a user approval
receipt, and produce an authorized-but-not-executed manifest.  It cannot create
directories, access a package registry, install or import a candidate, or start
a process.  Those effects belong to the post-approval E0-P2 executor.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


CONTRACT = "code_assist.notion.crdt_runtime_spike_preflight.v1"
APPROVAL_CONTRACT = "code_assist.notion.crdt_runtime_spike_approval.v1"
EXECUTION_MANIFEST_CONTRACT = "code_assist.notion.crdt_runtime_spike_manifest.v1"
APPROVAL_ID = "AP-RUNTIME-SPIKE"
PACKET_ID = "E0-P2"
MATRIX_SHA256 = "sha256:a97bde3c677acc198e1f451619367dcec20c59a76aa4934fe7f0905718831d57"
DEFAULT_MATRIX = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_crdt_candidate_matrix.v1.json"
)

EXPECTED_CANDIDATES: dict[str, tuple[str, ...]] = {
    "NODE_YJS_HOCUSPOCUS": ("yjs", "@hocuspocus/server"),
    "PY_NATIVE_PYCRDT": ("pycrdt", "pycrdt-websocket", "uvicorn"),
    "PY_OPAQUE_NODE_COMPACTOR": ("uvicorn", "websockets", "yjs"),
}

EXPECTED_PACKAGES: dict[str, dict[str, str]] = {
    "@hocuspocus/server": {
        "ecosystem": "npm",
        "version": "4.6.0",
        "artifact": "@hocuspocus/server@4.6.0",
        "integrity": "sha512:Ex4tka1vsm9NycHU1vPwxj3weQj+L2+736U8uentRyykAefUcYkHEh7Gfxiy9PHBholWcw1H+oYgZ3LP0ShTWQ==",
    },
    "pycrdt": {
        "ecosystem": "pypi",
        "version": "0.14.3",
        "artifact": "pycrdt-0.14.3-cp314-cp314-win_amd64.whl",
        "integrity": "sha256:5f9c5fde46e7961b3595141833d96ce31badcc980c6256e5e62689e5b4d404bd",
    },
    "pycrdt-websocket": {
        "ecosystem": "pypi",
        "version": "0.16.4",
        "artifact": "pycrdt_websocket-0.16.4-py3-none-any.whl",
        "integrity": "sha256:5c1612ea4e8743c4eb9d46b938b9e727ddfc7aee0d20130627e0d6c1fcc083da",
    },
    "uvicorn": {
        "ecosystem": "pypi",
        "version": "0.52.4",
        "artifact": "uvicorn-0.52.4-py3-none-any.whl",
        "integrity": "sha256:f86e41a149d7d05a9969337e3946a9c171c06a5d42680896daaba624aeac8da1",
    },
    "websockets": {
        "ecosystem": "pypi",
        "version": "17.0.1",
        "artifact": "websockets-17.0.1-cp314-cp314-win_amd64.whl",
        "integrity": "sha256:81ce19c6046ace11da7001781be7317bb1dc389f399af4b2ed962190f76f9add",
    },
    "yjs": {
        "ecosystem": "npm",
        "version": "13.6.32",
        "artifact": "yjs@13.6.32",
        "integrity": "sha512:lfiJIIC4Xayt5ItynE407ehlE03pCjeOc4hkR4yxxvvNJ4kuiN25B0g+Qp8XagYz361LLL7DCzR5bvFJ81QKtQ==",
    },
}

ALLOWED_EFFECTS_AFTER_APPROVAL = (
    "artifact_download_to_candidate_owned_temp_root",
    "candidate_dependency_install_in_candidate_owned_temp_root",
    "candidate_import_and_execution_in_candidate_owned_process",
    "candidate_owned_process_start_stop_crash_restart",
    "evidence_capture_without_content_or_secret_material",
    "candidate_owned_process_and_temp_root_cleanup",
)

FORBIDDEN_EFFECTS = (
    "10111_10112_10113_10011_10012_10013_mutation",
    "candidate_winner_selection",
    "existing_product_lockfile_change",
    "knowledge_or_user_data_access",
    "live_identity_or_workspace_access",
    "persistent_service_or_fixed_port_start",
    "production_runtime_import_or_enablement",
    "schema_migration_allocation",
)

RECEIPT_KEYS = frozenset(
    {
        "contract",
        "approval_id",
        "status",
        "approved_by",
        "approval_source",
        "approved_at",
        "scope_sha256",
        "matrix_sha256",
        "candidate_ids",
        "package_ids",
        "single_use",
    }
)
RFC3339_Z_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class SpikeContractError(ValueError):
    """The frozen E0-P2 approval or candidate contract is invalid."""


class ApprovalRequired(PermissionError):
    """AP-RUNTIME-SPIKE has not been explicitly approved."""


def _duplicate_rejecting_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SpikeContractError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> None:
    raise SpikeContractError(f"non-finite JSON number: {value}")


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SpikeContractError("value is not canonical JSON") from exc


def canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def load_candidate_matrix(
    path: Path = DEFAULT_MATRIX,
    *,
    expected_sha256: str = MATRIX_SHA256,
) -> dict[str, Any]:
    """Load the exact frozen E0-P1 matrix and reject every relevant drift."""

    if not isinstance(path, Path):
        raise TypeError("path must be a pathlib.Path")
    if _file_sha256(path) != expected_sha256:
        raise SpikeContractError("candidate matrix SHA-256 drift")
    try:
        matrix = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_duplicate_rejecting_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SpikeContractError("candidate matrix is not strict UTF-8 JSON") from exc
    if not isinstance(matrix, dict):
        raise SpikeContractError("candidate matrix must be an object")
    _validate_matrix(matrix)
    return matrix


def _validate_matrix(matrix: Mapping[str, Any]) -> None:
    if (
        matrix.get("packet_id") != "E0-P1"
        or matrix.get("status") != "frozen_for_isolated_spike_no_winner"
    ):
        raise SpikeContractError("E0-P1 matrix is not frozen for an unselected spike")

    candidates = matrix.get("candidates")
    if not isinstance(candidates, list):
        raise SpikeContractError("candidate list is missing")
    observed_candidates: dict[str, tuple[str, ...]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise SpikeContractError("candidate entry must be an object")
        candidate_id = candidate.get("id")
        package_ids = candidate.get("package_ids")
        if not isinstance(candidate_id, str) or not isinstance(package_ids, list):
            raise SpikeContractError("candidate identity or package list is invalid")
        if candidate.get("eligibility") != "eligible_for_isolated_spike_not_selected":
            raise SpikeContractError("candidate was selected before E0-P4")
        if any(not isinstance(item, str) for item in package_ids):
            raise SpikeContractError("candidate package id is invalid")
        observed_candidates[candidate_id] = tuple(package_ids)
    if observed_candidates != EXPECTED_CANDIDATES:
        raise SpikeContractError("candidate or package membership drift")

    observations = matrix.get("source_observations")
    if not isinstance(observations, list):
        raise SpikeContractError("package observations are missing")
    observed_packages: dict[str, dict[str, str]] = {}
    for observation in observations:
        if not isinstance(observation, dict):
            raise SpikeContractError("package observation must be an object")
        package_id = observation.get("package_id")
        if not isinstance(package_id, str):
            raise SpikeContractError("package observation id is invalid")
        if observation.get("locally_installed") is not False:
            raise SpikeContractError("package availability changed without a new E0-P1 receipt")
        observed_packages[package_id] = {
            "ecosystem": observation.get("ecosystem"),
            "version": observation.get("observed_version"),
            "artifact": observation.get("target_artifact"),
            "integrity": observation.get("target_artifact_integrity"),
        }
    if observed_packages != EXPECTED_PACKAGES:
        raise SpikeContractError("package version, artifact, or integrity drift")

    availability = matrix.get("availability_conclusion")
    if not isinstance(availability, dict):
        raise SpikeContractError("availability conclusion is missing")
    if (
        availability.get("every_candidate_dependency_locally_available") is not False
        or availability.get("next_packet") != PACKET_ID
        or availability.get("next_packet_requires_approval") is not True
        or availability.get("required_approval_id") != APPROVAL_ID
        or tuple(availability.get("unavailable_package_ids", ()))
        != tuple(sorted(EXPECTED_PACKAGES))
    ):
        raise SpikeContractError("AP-RUNTIME-SPIKE trigger drift")

    boundary = matrix.get("spike_boundary")
    if not isinstance(boundary, dict):
        raise SpikeContractError("spike boundary is missing")
    expected_false = (
        "production_lockfile_modification",
        "production_runtime_import",
        "schema_migration_allocation",
        "service_or_port_start",
        "live_data_or_identity_access",
    )
    if (
        boundary.get("candidate_install_root") != "new_OS_temp_child_per_candidate"
        or any(boundary.get(key) is not False for key in expected_false)
        or boundary.get("winner_selection_owner") != "E0-P4"
    ):
        raise SpikeContractError("isolated spike boundary drift")

    claims = matrix.get("claims")
    if not isinstance(claims, dict) or any(
        claims.get(key) is not False
        for key in (
            "candidate_winner_selected",
            "dependency_downloaded_or_installed",
            "production_dependency_pinned",
            "runtime_implemented",
            "product_complete",
        )
    ):
        raise SpikeContractError("E0-P1 completion truth drift")


def approval_scope_descriptor(matrix: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable effects and artifact scope the user must approve."""

    _validate_matrix(matrix)
    return {
        "contract": CONTRACT,
        "packet_id": PACKET_ID,
        "approval_id": APPROVAL_ID,
        "matrix_sha256": MATRIX_SHA256,
        "candidate_ids": sorted(EXPECTED_CANDIDATES),
        "packages": [
            {"package_id": package_id, **EXPECTED_PACKAGES[package_id]}
            for package_id in sorted(EXPECTED_PACKAGES)
        ],
        "candidate_root_policy": "new_OS_temp_child_per_candidate",
        "direct_artifact_integrity_required_before_install": True,
        "isolated_transitive_lock_required_before_execution": True,
        "candidate_owned_process_cleanup_required": True,
        "candidate_owned_temp_root_cleanup_required": True,
        "allowed_effects_after_approval": list(ALLOWED_EFFECTS_AFTER_APPROVAL),
        "forbidden_effects": list(FORBIDDEN_EFFECTS),
        "winner_selection_owner": "E0-P4",
    }


def approval_request_descriptor(matrix: Mapping[str, Any]) -> dict[str, Any]:
    scope = approval_scope_descriptor(matrix)
    return {
        "contract": APPROVAL_CONTRACT,
        "approval_id": APPROVAL_ID,
        "status": "pending",
        "reason": "all_frozen_candidate_dependencies_are_unavailable_locally",
        "scope": scope,
        "scope_sha256": canonical_sha256(scope),
        "explicit_user_approval_observed": False,
        "network_install_or_execution_allowed": False,
    }


def validate_approval_receipt(
    receipt: Mapping[str, Any],
    *,
    scope_sha256: str,
) -> dict[str, Any]:
    """Validate an exact, single-use explicit-user approval receipt."""

    if not isinstance(receipt, Mapping) or set(receipt) != RECEIPT_KEYS:
        raise ApprovalRequired("exact AP-RUNTIME-SPIKE approval receipt is missing")
    if (
        receipt.get("contract") != APPROVAL_CONTRACT
        or receipt.get("approval_id") != APPROVAL_ID
        or receipt.get("status") != "approved"
        or receipt.get("approved_by") != "user"
        or receipt.get("approval_source") != "explicit_conversation"
        or receipt.get("scope_sha256") != scope_sha256
        or receipt.get("matrix_sha256") != MATRIX_SHA256
        or receipt.get("candidate_ids") != sorted(EXPECTED_CANDIDATES)
        or receipt.get("package_ids") != sorted(EXPECTED_PACKAGES)
        or receipt.get("single_use") is not True
    ):
        raise ApprovalRequired("AP-RUNTIME-SPIKE receipt does not match the frozen scope")
    approved_at = receipt.get("approved_at")
    if not isinstance(approved_at, str) or not RFC3339_Z_RE.fullmatch(approved_at):
        raise ApprovalRequired("AP-RUNTIME-SPIKE approval time is invalid")
    return dict(receipt)


def build_execution_manifest(
    matrix: Mapping[str, Any],
    approval_receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build a pure authorized manifest; never execute or prepare a candidate."""

    request = approval_request_descriptor(matrix)
    if approval_receipt is None:
        raise ApprovalRequired("AP-RUNTIME-SPIKE explicit user approval is required")
    receipt = validate_approval_receipt(
        approval_receipt,
        scope_sha256=request["scope_sha256"],
    )
    candidates_by_id = {
        candidate["id"]: candidate
        for candidate in matrix["candidates"]
    }
    return {
        "contract": EXECUTION_MANIFEST_CONTRACT,
        "packet_id": PACKET_ID,
        "status": "authorized_not_executed",
        "approval_binding": {
            "approval_id": APPROVAL_ID,
            "approved_at": receipt["approved_at"],
            "scope_sha256": receipt["scope_sha256"],
            "matrix_sha256": receipt["matrix_sha256"],
            "single_use": True,
        },
        "candidate_plans": [
            {
                "candidate_id": candidate_id,
                "architecture": candidates_by_id[candidate_id]["architecture"],
                "gateway_execution_owner": candidates_by_id[candidate_id]["gateway_execution_owner"],
                "crdt_execution_owner": candidates_by_id[candidate_id]["crdt_execution_owner"],
                "package_ids": list(EXPECTED_CANDIDATES[candidate_id]),
                "candidate_root": f"OS_TEMP/new-child/{candidate_id.lower()}",
                "persistent_service": False,
                "fixed_port": False,
                "candidate_selected": False,
            }
            for candidate_id in sorted(EXPECTED_CANDIDATES)
        ],
        "required_receipts": [
            "direct_artifact_integrity",
            "isolated_transitive_dependency_lock",
            "clean_install",
            "candidate_start_stop_crash_restart",
            "common_corpus_result",
            "owned_process_cleanup",
            "owned_temp_root_cleanup",
        ],
        "forbidden_effects": list(FORBIDDEN_EFFECTS),
        "claims": {
            "network_attempted": False,
            "dependency_downloaded_or_installed": False,
            "candidate_process_started": False,
            "candidate_winner_selected": False,
            "product_runtime_changed": False,
            "product_complete": False,
        },
    }


__all__ = [
    "APPROVAL_CONTRACT",
    "APPROVAL_ID",
    "ApprovalRequired",
    "CONTRACT",
    "DEFAULT_MATRIX",
    "EXECUTION_MANIFEST_CONTRACT",
    "EXPECTED_CANDIDATES",
    "EXPECTED_PACKAGES",
    "FORBIDDEN_EFFECTS",
    "MATRIX_SHA256",
    "PACKET_ID",
    "SpikeContractError",
    "approval_request_descriptor",
    "approval_scope_descriptor",
    "build_execution_manifest",
    "canonical_sha256",
    "load_candidate_matrix",
    "validate_approval_receipt",
]

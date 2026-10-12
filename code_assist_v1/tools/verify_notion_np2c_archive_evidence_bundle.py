"""Validate a complete external NP2C-5a Archive evidence bundle.

The verifier reads only explicitly listed files beneath an explicitly supplied
external evidence root. It never opens a browser, scans a directory, imports an
application factory, or authorizes source/catalog/runtime implementation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import sys
from typing import Any, Iterable, Sequence

from code_assist_v1.tools.notion_parity.evidence import (
    canonical_sha256,
    validate_manifest,
)
from code_assist_v1.tools.verify_notion_np2c_archive_characterization import (
    DEFAULT_MATRIX,
    load_matrix,
    validate_matrix,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
BUNDLE_CONTRACT = "np2c5a_archive_characterization_evidence_bundle_v1"
BUNDLE_STATUS = "reviewed_complete"
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
MAX_BUNDLE_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_EVIDENCE_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_EVIDENCE_BYTES = 512 * 1024 * 1024
MAX_RUNS = 128
MAX_AUTHORIZATION_WINDOW = timedelta(hours=24)


class DuplicateJsonKeyError(ValueError):
    pass


class BundlePathError(ValueError):
    pass


def _object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJsonKeyError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def _load_json_bytes(payload: bytes) -> Any:
    return json.loads(
        payload.decode("utf-8"),
        object_pairs_hook=_object,
        parse_constant=lambda item: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {item}")
        ),
    )


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return False
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    return (
        not windows.is_absolute()
        and not windows.drive
        and not posix.is_absolute()
        and all(part not in {"", ".", ".."} for part in posix.parts)
    )


def _is_reparse(stat_result: os.stat_result) -> bool:
    attributes = getattr(stat_result, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse)


def _assert_no_link_components(path: Path) -> None:
    absolute = path.absolute()
    parts = absolute.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise BundlePathError("evidence path contains a link or reparse component")


def _outside_repository_root(root: Path) -> Path:
    if not root.is_absolute():
        raise BundlePathError("evidence root must be absolute")
    _assert_no_link_components(root)
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise BundlePathError("evidence root must be a directory")
    try:
        resolved.relative_to(REPOSITORY_ROOT.resolve(strict=True))
    except ValueError:
        return resolved
    raise BundlePathError("evidence root must be outside the repository")


def _relative_to_root(root: Path, path: Path) -> str:
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise BundlePathError("evidence file escapes the approved root") from exc
    value = relative.as_posix()
    if not _safe_relative_path(value):
        raise BundlePathError("evidence file path is invalid")
    return value


def _read_explicit_file(root: Path, relative: str, maximum: int) -> bytes:
    if not _safe_relative_path(relative):
        raise BundlePathError("evidence file path is invalid")
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    _assert_no_link_components(candidate)
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BundlePathError("evidence file escapes the approved root") from exc
    before = resolved.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise BundlePathError("evidence file must be a single-link regular file")
    if before.st_size > maximum:
        raise BundlePathError("evidence file exceeds its byte limit")
    with resolved.open("rb") as handle:
        payload = handle.read(maximum + 1)
        opened = os.fstat(handle.fileno())
    after = resolved.stat()
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_nlink,
        item.st_size,
        item.st_mtime_ns,
    )
    if identity(before) != identity(opened) or identity(opened) != identity(after):
        raise BundlePathError("evidence file changed while it was read")
    if len(payload) > maximum:
        raise BundlePathError("evidence file exceeds its byte limit")
    return payload


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _redacted_json(value: Any) -> bool:
    for item in _strings(value):
        if re.search(r"(?i)(?:authorization:\s*bearer|cookie\s*=|token=)", item):
            return False
        if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|/Users/|/home/)", item):
            return False
        if "@" in item:
            return False
    return True


def validate_authorization(
    authorization: Any,
    *,
    approved_authorization_sha256: str | None = None,
    require_approval_digest: bool = True,
    execution_time: datetime | None = None,
) -> dict[str, Any]:
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(isinstance(authorization, dict), "authorization must be an object")
    approval_from: datetime | None = None
    approval_until: datetime | None = None
    reviewer_id: str | None = None
    operator_id: str | None = None
    tenant_by_id: dict[str, dict[str, Any]] = {}
    authorization_sha256 = (
        canonical_sha256(authorization) if isinstance(authorization, dict) else None
    )
    digest_matches = bool(
        SHA256_PATTERN.fullmatch(str(approved_authorization_sha256))
        and authorization_sha256 == approved_authorization_sha256
    )
    approval_digest_matched = digest_matches if require_approval_digest else False
    if require_approval_digest:
        require(
            approval_digest_matched,
            "authorization does not match the out-of-band approved digest",
        )
    if isinstance(authorization, dict):
        require(
            set(authorization)
            == {
                "authorization_id",
                "approved",
                "operator_id",
                "reviewer_id",
                "approved_at",
                "valid_from",
                "valid_until",
                "approved_tenants",
                "normal_public_ui_only",
                "synthetic_fixture_only",
                "external_evidence_root_approved",
                "cleanup_approved",
                "public_link_scenario_approved",
                "plan_transition_scenario_approved",
                "production_or_personal_workspace_allowed",
                "api_private_endpoint_network_capture_allowed",
                "source_catalog_or_runtime_mutation_authorized",
            },
            "authorization keys are invalid",
        )
        require(authorization.get("approved") is True, "bundle authorization is not approved")
        for field in (
            "normal_public_ui_only",
            "synthetic_fixture_only",
            "external_evidence_root_approved",
            "cleanup_approved",
            "public_link_scenario_approved",
            "plan_transition_scenario_approved",
        ):
            require(authorization.get(field) is True, f"authorization {field} must be true")
        for field in (
            "production_or_personal_workspace_allowed",
            "api_private_endpoint_network_capture_allowed",
            "source_catalog_or_runtime_mutation_authorized",
        ):
            require(authorization.get(field) is False, f"authorization {field} must be false")
        for field in ("authorization_id", "operator_id", "reviewer_id"):
            require(
                SAFE_ID_PATTERN.fullmatch(str(authorization.get(field))) is not None,
                f"authorization {field} is invalid",
            )
        reviewer_id = (
            authorization.get("reviewer_id")
            if isinstance(authorization.get("reviewer_id"), str)
            else None
        )
        operator_id = (
            authorization.get("operator_id")
            if isinstance(authorization.get("operator_id"), str)
            else None
        )
        require(operator_id != reviewer_id, "operator and reviewer must be independent")
        approved_at = _parse_time(authorization.get("approved_at"))
        approval_from = _parse_time(authorization.get("valid_from"))
        approval_until = _parse_time(authorization.get("valid_until"))
        require(
            approved_at is not None
            and approval_from is not None
            and approval_until is not None,
            "authorization times are invalid",
        )
        if approved_at is not None and approval_from is not None and approval_until is not None:
            require(approved_at <= approval_from < approval_until, "authorization time window is invalid")
            require(
                approval_until - approval_from <= MAX_AUTHORIZATION_WINDOW,
                "authorization window exceeds 24 hours",
            )
            require(
                approval_from - approved_at <= MAX_AUTHORIZATION_WINDOW,
                "authorization was approved too far before its window",
            )
            if execution_time is not None:
                require(
                    execution_time.tzinfo is not None
                    and execution_time.utcoffset() is not None,
                    "execution time must be timezone-aware",
                )
                if execution_time.tzinfo is not None and execution_time.utcoffset() is not None:
                    require(
                        approval_from <= execution_time <= approval_until,
                        "authorization is not live at execution time",
                    )
        tenants = authorization.get("approved_tenants")
        require(
            isinstance(tenants, list) and 2 <= len(tenants) <= 8,
            "approved tenant list is invalid",
        )
        if isinstance(tenants, list):
            for tenant in tenants:
                require(
                    isinstance(tenant, dict)
                    and set(tenant) == {"tenant_id", "plan", "synthetic", "approved"},
                    "approved tenant shape is invalid",
                )
                if not isinstance(tenant, dict):
                    continue
                tenant_id = str(tenant.get("tenant_id"))
                require(SAFE_ID_PATTERN.fullmatch(tenant_id) is not None, "approved tenant id is invalid")
                require(tenant.get("plan") in {"business", "enterprise"}, "approved tenant plan is invalid")
                require(
                    tenant.get("synthetic") is True and tenant.get("approved") is True,
                    "approved tenant must be synthetic and approved",
                )
                require(tenant_id not in tenant_by_id, "approved tenant ids must be unique")
                tenant_by_id[tenant_id] = tenant
            require(
                {item.get("plan") for item in tenants if isinstance(item, dict)}
                == {"business", "enterprise"},
                "Business and Enterprise tenants are both required",
            )

    return {
        "errors": sorted(set(errors)),
        "reviewer_id": reviewer_id,
        "operator_id": operator_id,
        "approval_from": approval_from,
        "approval_until": approval_until,
        "tenant_by_id": tenant_by_id,
        "authorization_sha256": authorization_sha256,
        "approval_digest_matched": approval_digest_matched,
    }


def _expected_pairs(matrix: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (scenario["id"], profile_id)
        for scenario in matrix["scenarios"]
        for profile_id in scenario["profile_ids"]
    )


def validate_bundle(
    bundle: Any,
    *,
    evidence_root: Path,
    matrix: dict[str, Any],
    matrix_sha256: str,
    approved_authorization_sha256: str,
) -> dict[str, Any]:
    errors: list[str] = []
    total_evidence_bytes = 0
    all_explicit_paths: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    try:
        evidence_root = _outside_repository_root(Path(evidence_root))
    except (OSError, ValueError):
        return {
            "valid": False,
            "accepted": False,
            "error_count": 1,
            "errors": ["evidence root is not an approved external directory"],
            "run_count": 0,
            "expected_run_count": len(_expected_pairs(matrix)),
            "reference_conflict_count": 0,
            "approval_digest_matched": False,
            "implementation_authorized": False,
        }

    if not isinstance(bundle, dict):
        return {
            "valid": False,
            "accepted": False,
            "error_count": 1,
            "errors": ["bundle root must be an object"],
            "run_count": 0,
            "expected_run_count": len(_expected_pairs(matrix)),
            "reference_conflict_count": 0,
            "approval_digest_matched": False,
            "implementation_authorized": False,
        }

    require(
        set(bundle)
        == {
            "schema_version",
            "contract",
            "status",
            "matrix_sha256",
            "authorization",
            "runs",
            "unknown_exceptions",
            "reference_conflicts",
            "decision",
            "claims",
        },
        "bundle root keys are invalid",
    )
    require(bundle.get("schema_version") == 1, "bundle schema_version must be 1")
    require(bundle.get("contract") == BUNDLE_CONTRACT, "bundle contract is invalid")
    require(bundle.get("status") == BUNDLE_STATUS, "bundle status must be reviewed_complete")
    require(bundle.get("matrix_sha256") == matrix_sha256, "bundle matrix hash is stale")

    authorization = bundle.get("authorization")
    authorization_result = validate_authorization(
        authorization,
        approved_authorization_sha256=approved_authorization_sha256,
    )
    errors.extend(authorization_result["errors"])
    approval_from = authorization_result["approval_from"]
    approval_until = authorization_result["approval_until"]
    reviewer_id = authorization_result["reviewer_id"]
    tenant_by_id = authorization_result["tenant_by_id"]

    matrix_profiles = {item["id"]: item for item in matrix["profiles"]}
    matrix_scenarios = {item["id"]: item for item in matrix["scenarios"]}
    expected_pairs = _expected_pairs(matrix)
    runs = bundle.get("runs")
    require(isinstance(runs, list) and len(runs) <= MAX_RUNS, "run list is invalid")
    seen_pairs: list[tuple[str, str]] = []
    latest_review: datetime | None = None
    if isinstance(runs, list):
        for run in runs:
            require(
                isinstance(run, dict)
                and set(run)
                == {
                    "scenario_id",
                    "profile_id",
                    "tenant_id",
                    "operator_id",
                    "manifest_path",
                    "manifest_sha256",
                    "fixture_manifest_path",
                    "fixture_manifest_sha256",
                    "action_script_path",
                    "action_script_sha256",
                    "redaction_report_sha256",
                    "evidence_files",
                },
                "run shape is invalid",
            )
            if not isinstance(run, dict):
                continue
            scenario_id = str(run.get("scenario_id"))
            profile_id = str(run.get("profile_id"))
            pair = (scenario_id, profile_id)
            seen_pairs.append(pair)
            require(pair in expected_pairs, "run pair is not required by the matrix")
            profile = matrix_profiles.get(profile_id)
            scenario = matrix_scenarios.get(scenario_id)
            tenant = tenant_by_id.get(str(run.get("tenant_id")))
            require(tenant is not None, "run tenant is not approved")
            require(
                isinstance(authorization, dict)
                and run.get("operator_id") == authorization.get("operator_id"),
                "run operator is not authorized",
            )
            if tenant is not None and profile is not None and profile.get("plan") != "not_applicable":
                require(tenant.get("plan") == profile.get("plan"), "run tenant plan does not match profile")
            for field in (
                "manifest_sha256",
                "fixture_manifest_sha256",
                "action_script_sha256",
                "redaction_report_sha256",
            ):
                require(SHA256_PATTERN.fullmatch(str(run.get(field))) is not None, f"run {field} is invalid")

            manifest_path = run.get("manifest_path")
            all_explicit_paths.append(str(manifest_path))
            manifest: Any = None
            manifest_reviewed_at: datetime | None = None
            try:
                manifest_payload = _read_explicit_file(
                    evidence_root, str(manifest_path), MAX_MANIFEST_BYTES
                )
                manifest = _load_json_bytes(manifest_payload)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                require(False, "run manifest could not be verified")
            else:
                total_evidence_bytes += len(manifest_payload)
                require(canonical_sha256(manifest) == run.get("manifest_sha256"), "run manifest hash is invalid")
                require(validate_manifest(manifest) == [], "run manifest violates the evidence policy")
                if isinstance(manifest, dict) and profile is not None and scenario is not None:
                    require(manifest.get("scenario_id") == scenario_id, "run manifest scenario is invalid")
                    require(manifest.get("observation_state") == "live-characterized", "run manifest must be live-characterized")
                    require(manifest.get("promotion_status") == "live_characterized", "run manifest promotion is invalid")
                    for field in ("plan", "role", "platform", "locale"):
                        require(manifest.get(field) == profile.get(field), f"run manifest {field} does not match profile")
                    expected_urls = {
                        item["url"]
                        for item in matrix["reference_sources"]
                        if item["id"] in scenario["reference_source_ids"]
                    }
                    require(set(manifest.get("reference_sources", ())) == expected_urls, "run manifest references are incomplete")
                    require(manifest.get("reviewer") == reviewer_id, "run manifest reviewer is invalid")
                    reviewed_at = _parse_time(manifest.get("reviewed_at"))
                    manifest_reviewed_at = reviewed_at
                    require(reviewed_at is not None, "run manifest reviewed_at is invalid")
                    if reviewed_at is not None and approval_from is not None and approval_until is not None:
                        require(approval_from <= reviewed_at <= approval_until, "run review is outside the authorization window")
                        latest_review = reviewed_at if latest_review is None or reviewed_at > latest_review else latest_review

            supporting_values: dict[str, Any] = {}
            for path_field, hash_field, label in (
                ("fixture_manifest_path", "fixture_manifest_sha256", "fixture manifest"),
                ("action_script_path", "action_script_sha256", "action script"),
            ):
                relative = str(run.get(path_field))
                all_explicit_paths.append(relative)
                try:
                    supporting_payload = _read_explicit_file(
                        evidence_root, relative, MAX_MANIFEST_BYTES
                    )
                    supporting_json = _load_json_bytes(supporting_payload)
                except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                    require(False, f"run {label} could not be verified")
                else:
                    total_evidence_bytes += len(supporting_payload)
                    require(
                        _sha256_bytes(supporting_payload) == run.get(hash_field),
                        f"run {label} hash is invalid",
                    )
                    require(
                        isinstance(supporting_json, (dict, list))
                        and _redacted_json(supporting_json),
                        f"run {label} is not redacted JSON",
                    )
                    supporting_values[label] = supporting_json

            if scenario is not None and profile is not None:
                require(
                    supporting_values.get("fixture manifest")
                    == {
                        "schema_version": 1,
                        "scenario_id": scenario_id,
                        "profile_id": profile_id,
                        "fixture_id": scenario["fixture_id"],
                        "synthetic": True,
                        "real_user_or_organization_content": False,
                    },
                    "run fixture manifest contract is invalid",
                )
                require(
                    supporting_values.get("action script")
                    == {
                        "schema_version": 1,
                        "scenario_id": scenario_id,
                        "profile_id": profile_id,
                        "normal_public_ui_only": True,
                        "actions": scenario["actions"],
                    },
                    "run action script contract is invalid",
                )

            if isinstance(manifest, dict):
                require(
                    manifest.get("input_sha256") == run.get("action_script_sha256"),
                    "run manifest input hash is not bound to the action script",
                )

            evidence_files = run.get("evidence_files")
            require(isinstance(evidence_files, list) and 1 <= len(evidence_files) <= 32, "run evidence file list is invalid")
            listed_paths: list[str] = []
            redaction_hashes: list[str] = []
            result_hashes: list[str] = []
            non_redaction_hashes: list[str] = []
            result_values: list[Any] = []
            redaction_values: list[Any] = []
            if isinstance(evidence_files, list):
                for item in evidence_files:
                    require(
                        isinstance(item, dict)
                        and set(item) == {"path", "sha256", "kind"},
                        "evidence file shape is invalid",
                    )
                    if not isinstance(item, dict):
                        continue
                    relative = str(item.get("path"))
                    listed_paths.append(relative)
                    all_explicit_paths.append(relative)
                    require(item.get("kind") in {"result", "screenshot", "accessibility", "redaction_report", "deletion_record"}, "evidence file kind is invalid")
                    require(SHA256_PATTERN.fullmatch(str(item.get("sha256"))) is not None, "evidence file hash is invalid")
                    try:
                        payload = _read_explicit_file(
                            evidence_root, relative, MAX_EVIDENCE_FILE_BYTES
                        )
                    except (OSError, ValueError):
                        require(False, "evidence file could not be verified")
                    else:
                        total_evidence_bytes += len(payload)
                        require(_sha256_bytes(payload) == item.get("sha256"), "evidence file content hash is invalid")
                        if item.get("kind") != "screenshot":
                            try:
                                evidence_json = _load_json_bytes(payload)
                            except (UnicodeError, ValueError, json.JSONDecodeError):
                                require(False, "non-screenshot evidence must be redacted JSON")
                            else:
                                require(
                                    isinstance(evidence_json, (dict, list))
                                    and _redacted_json(evidence_json),
                                    "evidence JSON is not redacted",
                                )
                                if item.get("kind") == "result":
                                    result_values.append(evidence_json)
                                if item.get("kind") == "redaction_report":
                                    redaction_values.append(evidence_json)
                    if item.get("kind") == "redaction_report":
                        redaction_hashes.append(str(item.get("sha256")))
                    else:
                        non_redaction_hashes.append(str(item.get("sha256")))
                    if item.get("kind") == "result":
                        result_hashes.append(str(item.get("sha256")))
                require(len(listed_paths) == len(set(listed_paths)), "evidence file paths must be unique")
                require(redaction_hashes == [run.get("redaction_report_sha256")], "exactly one bound redaction report is required")
                require(len(result_hashes) == 1, "exactly one result artifact is required")
                if isinstance(manifest, dict):
                    require(manifest.get("evidence") == listed_paths, "manifest evidence paths do not match the bundle")
                    require(
                        result_hashes == [manifest.get("result_sha256")],
                        "run manifest result hash is not bound to the result artifact",
                    )
                if scenario is not None:
                    result_value = result_values[0] if len(result_values) == 1 else None
                    require(
                        isinstance(result_value, dict)
                        and set(result_value)
                        == {"schema_version", "scenario_id", "profile_id", "observations"},
                        "run result contract is invalid",
                    )
                    if isinstance(result_value, dict):
                        require(result_value.get("schema_version") == 1, "run result schema is invalid")
                        require(result_value.get("scenario_id") == scenario_id, "run result scenario is invalid")
                        require(result_value.get("profile_id") == profile_id, "run result profile is invalid")
                        observations = result_value.get("observations")
                        require(isinstance(observations, list), "run observations are invalid")
                        if isinstance(observations, list):
                            require(
                                tuple(
                                    item.get("dimension")
                                    for item in observations
                                    if isinstance(item, dict)
                                )
                                == tuple(scenario["observation_dimensions"]),
                                "run observation dimensions are incomplete or reordered",
                            )
                            for observation in observations:
                                require(
                                    isinstance(observation, dict)
                                    and set(observation)
                                    == {"dimension", "status", "observation_sha256"}
                                    and observation.get("status") == "observed"
                                    and SHA256_PATTERN.fullmatch(
                                        str(observation.get("observation_sha256"))
                                    )
                                    is not None,
                                    "reviewed_complete observations must be fully observed and hashed",
                                )
                redaction_value = redaction_values[0] if len(redaction_values) == 1 else None
                require(
                    isinstance(redaction_value, dict)
                    and set(redaction_value)
                    == {
                        "schema_version",
                        "status",
                        "reviewer_id",
                        "reviewed_at",
                        "artifact_sha256s",
                    },
                    "redaction report contract is invalid",
                )
                if isinstance(redaction_value, dict):
                    redaction_time = _parse_time(redaction_value.get("reviewed_at"))
                    require(redaction_value.get("schema_version") == 1, "redaction report schema is invalid")
                    require(redaction_value.get("status") == "passed", "redaction report did not pass")
                    require(redaction_value.get("reviewer_id") == reviewer_id, "redaction reviewer is invalid")
                    require(redaction_value.get("artifact_sha256s") == non_redaction_hashes, "redaction report does not bind every artifact")
                    require(redaction_time is not None, "redaction reviewed_at is invalid")
                    if redaction_time is not None and approval_from is not None and approval_until is not None:
                        require(approval_from <= redaction_time <= approval_until, "redaction review is outside the authorization window")
                    if redaction_time is not None and manifest_reviewed_at is not None:
                        require(redaction_time <= manifest_reviewed_at, "manifest review predates redaction review")

    require(tuple(seen_pairs) == expected_pairs, "required scenario/profile runs are incomplete or reordered")
    require(len(seen_pairs) == len(set(seen_pairs)), "scenario/profile runs must be unique")
    require(bundle.get("unknown_exceptions") == [], "reviewed_complete bundles cannot retain unknown profile exceptions")
    require(bundle.get("reference_conflicts") == [], "reviewed_complete bundles cannot retain unresolved reference conflicts")

    decision = bundle.get("decision")
    require(isinstance(decision, dict), "decision must be an object")
    if isinstance(decision, dict):
        require(
            set(decision)
            == {
                "semantic_decision_sha256",
                "semantic_decision_path",
                "reviewer_id",
                "reviewed_at",
                "all_required_runs_reviewed",
                "reference_conflicts_resolved",
                "unobserved_dimensions_remaining",
                "source_schema_semantics_frozen",
                "implementation_authorized",
                "next_gate",
            },
            "decision keys are invalid",
        )
        decision_path = str(decision.get("semantic_decision_path"))
        all_explicit_paths.append(decision_path)
        semantic_decision: Any = None
        try:
            decision_payload = _read_explicit_file(
                evidence_root, decision_path, MAX_MANIFEST_BYTES
            )
            semantic_decision = _load_json_bytes(decision_payload)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            require(False, "semantic decision record could not be verified")
        else:
            total_evidence_bytes += len(decision_payload)
            require(
                _sha256_bytes(decision_payload)
                == decision.get("semantic_decision_sha256"),
                "semantic decision hash is invalid",
            )
            require(
                isinstance(semantic_decision, dict)
                and _redacted_json(semantic_decision),
                "semantic decision record is not redacted JSON",
            )
        require(decision.get("reviewer_id") == reviewer_id, "decision reviewer is invalid")
        decision_time = _parse_time(decision.get("reviewed_at"))
        require(decision_time is not None, "decision reviewed_at is invalid")
        if decision_time is not None and approval_from is not None and approval_until is not None:
            require(approval_from <= decision_time <= approval_until, "decision is outside the authorization window")
            if latest_review is not None:
                require(decision_time >= latest_review, "decision predates a run review")
        require(decision.get("all_required_runs_reviewed") is True, "not all required runs were reviewed")
        require(decision.get("reference_conflicts_resolved") is True, "reference conflicts are unresolved")
        require(decision.get("unobserved_dimensions_remaining") == 0, "unobserved dimensions remain")
        require(decision.get("source_schema_semantics_frozen") is True, "source semantics are not frozen")
        require(decision.get("implementation_authorized") is False, "characterization cannot authorize implementation")
        require(
            decision.get("next_gate")
            == "NP2C-5b-exact-v4-to-v5-archive-source-schema-and-route-unregistered-adapter",
            "decision next gate is invalid",
        )
        require(
            isinstance(semantic_decision, dict)
            and set(semantic_decision)
            == {
                "schema_version",
                "contract",
                "reviewer_id",
                "reviewed_at",
                "scenario_decisions",
                "implementation_authorized",
                "next_gate",
            },
            "semantic decision record contract is invalid",
        )
        if isinstance(semantic_decision, dict):
            require(semantic_decision.get("schema_version") == 1, "semantic decision schema is invalid")
            require(semantic_decision.get("contract") == "np2c5a_archive_semantic_decision_v1", "semantic decision contract is invalid")
            require(semantic_decision.get("reviewer_id") == reviewer_id, "semantic decision record reviewer is invalid")
            require(semantic_decision.get("reviewed_at") == decision.get("reviewed_at"), "semantic decision record time is invalid")
            require(semantic_decision.get("implementation_authorized") is False, "semantic decision record cannot authorize implementation")
            require(semantic_decision.get("next_gate") == decision.get("next_gate"), "semantic decision record next gate is invalid")
            scenario_decisions = semantic_decision.get("scenario_decisions")
            require(isinstance(scenario_decisions, list), "semantic scenario decisions are invalid")
            if isinstance(scenario_decisions, list):
                require(
                    tuple(
                        item.get("scenario_id")
                        for item in scenario_decisions
                        if isinstance(item, dict)
                    )
                    == tuple(item["id"] for item in matrix["scenarios"]),
                    "semantic scenario decisions are incomplete or reordered",
                )
                for item in scenario_decisions:
                    require(
                        isinstance(item, dict)
                        and set(item) == {"scenario_id", "decision"}
                        and isinstance(item.get("decision"), dict)
                        and bool(item.get("decision")),
                        "semantic scenario decision is invalid",
                    )

    require(
        len(all_explicit_paths) == len(set(all_explicit_paths)),
        "every bundle file must be distinct and explicitly listed",
    )
    require(total_evidence_bytes <= MAX_TOTAL_EVIDENCE_BYTES, "aggregate evidence exceeds the byte limit")

    claims = bundle.get("claims")
    require(isinstance(claims, dict), "claims must be an object")
    if isinstance(claims, dict):
        require(
            claims
            == {
                "live_characterized": True,
                "np2c5a_complete": True,
                "differential_passed": False,
                "source_v5_implemented": False,
                "workspace_1318_implemented": False,
                "runtime_archive_enabled": False,
                "np2c_complete": False,
                "overall_parity_complete": False,
            },
            "bundle claims are invalid",
        )

    require(_redacted_json(bundle), "bundle contains a secret, email, or absolute path")

    unique_errors = sorted(set(errors))
    return {
        "valid": not unique_errors,
        "accepted": not unique_errors,
        "error_count": len(unique_errors),
        "errors": unique_errors,
        "run_count": len(runs) if isinstance(runs, list) else 0,
        "expected_run_count": len(expected_pairs),
        "reference_conflict_count": len(bundle.get("reference_conflicts", ()))
        if isinstance(bundle.get("reference_conflicts"), list)
        else 0,
        "approval_digest_matched": authorization_result["approval_digest_matched"],
        "implementation_authorized": False,
    }


def verify_bundle(
    bundle_path: str | Path,
    evidence_root: str | Path,
    *,
    approved_authorization_sha256: str,
    matrix_path: str | Path = DEFAULT_MATRIX,
) -> dict[str, Any]:
    try:
        matrix_path_obj = Path(matrix_path)
        matrix = load_matrix(matrix_path_obj)
        matrix_report = validate_matrix(matrix)
        if not matrix_report["valid"]:
            raise ValueError("Archive characterization matrix is invalid")
        matrix_payload = matrix_path_obj.read_bytes()
        matrix_sha256 = _sha256_bytes(matrix_payload)
        root = _outside_repository_root(Path(evidence_root))
        bundle_file = Path(bundle_path)
        relative_bundle = _relative_to_root(root, bundle_file)
        payload = _read_explicit_file(root, relative_bundle, MAX_BUNDLE_BYTES)
        bundle = _load_json_bytes(payload)
        return validate_bundle(
            bundle,
            evidence_root=root,
            matrix=matrix,
            matrix_sha256=matrix_sha256,
            approved_authorization_sha256=approved_authorization_sha256,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return {
            "valid": False,
            "accepted": False,
            "error_count": 1,
            "errors": ["evidence bundle could not be verified"],
            "run_count": 0,
            "expected_run_count": 0,
            "reference_conflict_count": 0,
            "approval_digest_matched": False,
            "implementation_authorized": False,
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle")
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--approved-authorization-sha256", required=True)
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX))
    args = parser.parse_args(argv)
    report = verify_bundle(
        args.bundle,
        args.evidence_root,
        approved_authorization_sha256=args.approved_authorization_sha256,
        matrix_path=args.matrix,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    sys.exit(main())

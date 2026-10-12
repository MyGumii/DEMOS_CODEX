"""Compile one immutable Z0 final-completion evidence manifest.

The compiler consumes the exact full-Python, six Node, and production-canary
receipts only after reconstructing them against the current repository and SBE
contracts.  It writes a create-only manifest and grants no packet credit,
executes no tests or operator actions, and enables no rollout.  The separate Z0
seal and status-integrator steps remain mandatory.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from code_assist_v1.tools.contract_audit import audit_contracts
import code_assist_v1.tools.internal_collaborative_validation as validation


RESULT_CONTRACT = (
    "code_assist.notion.internal_collaborative_z0_final_manifest_compile_result.v1"
)
_SHARED_SUITE_FIELDS = (
    "release_candidate_id",
    "deployment_generation_id",
    "source_manifest_sha256",
    "validation_manifest_sha256",
    "schema_catalog_sha256",
    "current_schema_lineage_sha256",
    "agents_md_sha256",
    "goal_observation_sha256",
    "goal_thread_id",
    "cross_catalog_ownership_snapshot_sha256",
    "sbe_contract_audit_input_sha256",
    "realm_config_fingerprint",
)


class Z0ManifestCompileError(ValueError):
    """The final manifest cannot be compiled without weakening its evidence."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise Z0ManifestCompileError("invalid_arguments")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise Z0ManifestCompileError("duplicate_json_key")
        value[key] = item
    return value


def _load_once(path: Path) -> tuple[Any, str]:
    raw = path.read_bytes()
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=lambda item: (_ for _ in ()).throw(
            Z0ManifestCompileError(f"invalid_json_constant:{item}")
        ),
    )
    return value, "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _pretty_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _resolve_input(path: Path, *, root: Path, label: str) -> Path:
    repository_root = validation.REPOSITORY_ROOT.resolve(strict=True)
    candidate = (
        path.resolve(strict=True)
        if path.is_absolute()
        else (repository_root / path).resolve(strict=True)
    )
    allowed_root = root.resolve(strict=True)
    if (
        not candidate.is_file()
        or candidate.suffix.lower() != ".json"
        or not candidate.is_relative_to(allowed_root)
    ):
        raise Z0ManifestCompileError(f"{label}_path_invalid")
    return candidate


def _resolve_output(path: Path) -> Path:
    repository_root = validation.REPOSITORY_ROOT.resolve(strict=True)
    candidate = (
        path.resolve(strict=False)
        if path.is_absolute()
        else (repository_root / path).resolve(strict=False)
    )
    allowed_root = validation.Z0_FINAL_EVIDENCE_ROOT.resolve(strict=True)
    if (
        candidate.suffix.lower() != ".json"
        or not candidate.is_relative_to(allowed_root)
        or not candidate.parent.is_dir()
    ):
        raise Z0ManifestCompileError("output_path_invalid")
    if candidate.exists():
        raise FileExistsError(candidate)
    return candidate


def _relative(path: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(
            validation.REPOSITORY_ROOT.resolve(strict=True)
        ).as_posix()
    except ValueError as exc:
        raise Z0ManifestCompileError("evidence_outside_repository") from exc


def _current_sbe_report() -> dict[str, Any]:
    report, exit_code = audit_contracts(validation.KNOWLEDGE_DOCS_ROOT.resolve())
    if not (
        exit_code == 0
        and isinstance(report, dict)
        and report.get("status") == "pass"
        and report.get("error_count") == 0
        and report.get("errors") == []
        and validation.normalize_sha256(report.get("input_sha256")) is not None
    ):
        raise Z0ManifestCompileError("sbe_contract_audit_not_green")
    return report


def _current_authority(sbe_report: Mapping[str, Any]) -> tuple[dict[str, Any], set[Path]]:
    checkpoint, checkpoint_path, pointer = validation.load_current_checkpoint()
    goal_observation = checkpoint.get("goal_observation")
    ownership = checkpoint.get("cross_catalog_ownership_snapshot")
    if not isinstance(goal_observation, dict) or not isinstance(ownership, dict):
        raise Z0ManifestCompileError("current_checkpoint_authority_invalid")
    lineage_path = (
        validation.KNOWLEDGE_DOCS_ROOT
        / "second_brain_evolution"
        / "FORWARD_SCHEMA.md"
    ).resolve(strict=True)
    agents_path = (validation.PROJECT_ROOT / "AGENTS.md").resolve(strict=True)
    lineage_sha256 = validation.sha256_file(lineage_path)
    agents_sha256 = validation.sha256_file(agents_path)
    if (
        checkpoint.get("current_schema_lineage_sha256") != lineage_sha256
        or checkpoint.get("agents_md_sha256") != agents_sha256
    ):
        raise Z0ManifestCompileError("current_checkpoint_authority_invalid")
    expected = {
        "source_manifest_sha256": validation.sha256_file(validation.DEFAULT_CATALOG),
        "validation_manifest_sha256": validation.sha256_file(validation.DEFAULT_LADDER),
        "schema_catalog_sha256": validation.sha256_file(validation.DEFAULT_MATRIX),
        "current_schema_lineage_sha256": lineage_sha256,
        "agents_md_sha256": agents_sha256,
        "goal_observation_sha256": validation.canonical_sha256(goal_observation),
        "goal_thread_id": goal_observation.get("thread_id"),
        "goal_status_observed": goal_observation.get("persisted_tool_status"),
        "cross_catalog_ownership_snapshot_sha256": validation.canonical_sha256(
            ownership
        ),
        "sbe_contract_audit_input_sha256": validation.normalize_sha256(
            sbe_report.get("input_sha256")
        ),
    }
    sha_fields = (
        "source_manifest_sha256",
        "validation_manifest_sha256",
        "schema_catalog_sha256",
        "current_schema_lineage_sha256",
        "agents_md_sha256",
        "goal_observation_sha256",
        "cross_catalog_ownership_snapshot_sha256",
        "sbe_contract_audit_input_sha256",
    )
    if (
        any(validation.normalize_sha256(expected[field]) is None for field in sha_fields)
        or not isinstance(expected["goal_thread_id"], str)
        or not expected["goal_thread_id"]
        or expected["goal_status_observed"] != "active"
    ):
        raise Z0ManifestCompileError("current_checkpoint_authority_invalid")
    watched = {
        checkpoint_path.resolve(strict=True),
        validation.DEFAULT_CATALOG.resolve(strict=True),
        validation.DEFAULT_LADDER.resolve(strict=True),
        validation.DEFAULT_MATRIX.resolve(strict=True),
        lineage_path,
        agents_path,
    }
    if pointer is not None:
        watched.add(validation.DEFAULT_CHECKPOINT.resolve(strict=True))
    return expected, watched


def _evidence_binding(
    path: Path,
    sha256: str,
    value: Mapping[str, Any],
    *,
    identity_field: str = "receipt_id",
) -> dict[str, str]:
    identity = value.get(identity_field)
    if not isinstance(identity, str) or not identity:
        raise Z0ManifestCompileError("evidence_identity_invalid")
    return {
        "path": _relative(path),
        "sha256": sha256,
        "receipt_id": identity,
    }


def _assert_inputs_unchanged(bindings: Mapping[Path, str]) -> None:
    for path, expected in bindings.items():
        if validation.sha256_file(path) != expected:
            raise Z0ManifestCompileError("evidence_changed_before_publish")


def _staged_generic_receipt(
    manifest: Mapping[str, Any],
    *,
    manifest_path: Path,
    manifest_sha256: str,
) -> dict[str, Any]:
    relative = _relative(manifest_path)
    return {
        "packet_ids": ["Z0-P1"],
        "lane_id": "L9-REALM-PROMOTION",
        "realm_id": "production_canary",
        "environment_class": "strict_completion_receipt_verification",
        "matrix_row_ids": [],
        "scenario_ids": [],
        "deployment_generation_id": manifest["deployment_generation_id"],
        "final_suite_receipt_count": len(validation.Z0_REQUIRED_SUITE_LANES),
        "release_candidate_id": manifest["release_candidate_id"],
        "realm_config_fingerprint": manifest[
            "production_realm_config_fingerprint"
        ],
        "source_manifest_sha256": manifest["source_manifest_sha256"],
        "validation_manifest_sha256": manifest["validation_manifest_sha256"],
        "schema_catalog_sha256": manifest["schema_catalog_sha256"],
        "current_schema_lineage_sha256": manifest[
            "current_schema_lineage_sha256"
        ],
        "agents_md_sha256": manifest["agents_md_sha256"],
        "goal_observation_sha256": manifest["goal_observation_sha256"],
        "goal_thread_id": manifest["goal_thread_id"],
        "cross_catalog_ownership_snapshot_sha256": manifest[
            "cross_catalog_ownership_snapshot_sha256"
        ],
        "sbe_contract_audit_input_sha256": manifest[
            "sbe_contract_audit_input_sha256"
        ],
        "artifact_hashes": {relative: manifest_sha256},
        "isolation_attestation": {
            "operator_actions_executed_by_verifier": False,
            "pytest_invoked_by_verifier": False,
        },
        "full_rollout_enabled_by_verifier": False,
        "product_complete": False,
        "active_shared_goal_complete": False,
        "z0_final_evidence": {
            "path": relative,
            "sha256": manifest_sha256,
            "manifest_id": manifest["manifest_id"],
        },
    }


def compile_z0_final_manifest(
    *,
    suite_receipt_paths: Sequence[Path],
    promotion_receipt_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Validate and publish an exact create-only Z0 evidence manifest."""

    if len(suite_receipt_paths) != len(validation.Z0_REQUIRED_SUITE_LANES):
        raise Z0ManifestCompileError("exactly_seven_suite_receipts_required")
    output = _resolve_output(output_path)
    ladder = validation.load_json(validation.DEFAULT_LADDER)
    if not isinstance(ladder, dict):
        raise Z0ManifestCompileError("validation_ladder_invalid")
    sbe_report = _current_sbe_report()
    current, checkpoint_paths = _current_authority(sbe_report)

    input_hashes: dict[Path, str] = {
        path: validation.sha256_file(path) for path in checkpoint_paths
    }
    suites_by_lane: dict[str, tuple[Path, dict[str, Any], str]] = {}
    release_declaration: dict[str, Any] | None = None
    for declared_path in suite_receipt_paths:
        path = _resolve_input(
            declared_path,
            root=validation.Z0_FINAL_EVIDENCE_ROOT,
            label="suite_receipt",
        )
        if path in input_hashes:
            raise Z0ManifestCompileError("suite_receipt_reused")
        value, raw_sha256 = _load_once(path)
        if not isinstance(value, dict):
            raise Z0ManifestCompileError("suite_receipt_object_required")
        lane_id = value.get("lane_id")
        if (
            lane_id not in validation.Z0_REQUIRED_SUITE_LANES
            or lane_id in suites_by_lane
        ):
            raise Z0ManifestCompileError("suite_lane_set_invalid")
        declaration = value.get("release_candidate_manifest")
        if not isinstance(declaration, dict):
            raise Z0ManifestCompileError("suite_release_manifest_required")
        if release_declaration is None:
            release_declaration = deepcopy(declaration)
        elif declaration != release_declaration:
            raise Z0ManifestCompileError("suite_release_manifest_drift")
        input_hashes[path] = raw_sha256
        suites_by_lane[lane_id] = (path, value, raw_sha256)
    if set(suites_by_lane) != set(validation.Z0_REQUIRED_SUITE_LANES):
        raise Z0ManifestCompileError("suite_lane_set_invalid")

    assert release_declaration is not None
    if set(release_declaration) != {"path", "sha256", "manifest_id"}:
        raise Z0ManifestCompileError("suite_release_manifest_binding_invalid")
    release_path = _resolve_input(
        Path(str(release_declaration.get("path"))),
        root=validation.PROMOTION_EVIDENCE_ROOT,
        label="release_manifest",
    )
    _, release_sha256 = _load_once(release_path)
    if release_sha256 != validation.normalize_sha256(
        release_declaration.get("sha256")
    ):
        raise Z0ManifestCompileError("suite_release_manifest_hash_mismatch")
    input_hashes[release_path] = release_sha256

    first = suites_by_lane[validation.Z0_REQUIRED_SUITE_LANES[0]][1]
    shared = {field: first.get(field) for field in _SHARED_SUITE_FIELDS}
    for lane_id in validation.Z0_REQUIRED_SUITE_LANES:
        value = suites_by_lane[lane_id][1]
        if any(value.get(field) != shared[field] for field in _SHARED_SUITE_FIELDS):
            raise Z0ManifestCompileError("suite_authority_binding_drift")
        if any(value.get(field) != current[field] for field in current):
            raise Z0ManifestCompileError("suite_current_authority_mismatch")

    promotion_path = _resolve_input(
        promotion_receipt_path,
        root=validation.DEFAULT_RECEIPTS_ROOT,
        label="promotion_receipt",
    )
    promotion, promotion_sha256 = _load_once(promotion_path)
    if not isinstance(promotion, dict):
        raise Z0ManifestCompileError("promotion_receipt_object_required")
    input_hashes[promotion_path] = promotion_sha256
    if (
        set(promotion.get("packet_ids", [])) != validation.PROMOTION_GATE_PACKETS
        or promotion.get("release_candidate_id") != shared["release_candidate_id"]
        or promotion.get("deployment_generation_id")
        != shared["deployment_generation_id"]
        or validation.normalize_sha256(promotion.get("realm_config_fingerprint"))
        is None
    ):
        raise Z0ManifestCompileError("promotion_receipt_binding_mismatch")
    if validation._validate_realm_promotion_evidence(  # noqa: SLF001
        promotion,
        "promotion_receipt",
    ):
        raise Z0ManifestCompileError("promotion_receipt_reconstruction_failed")

    suite_bindings = []
    for lane_id in validation.Z0_REQUIRED_SUITE_LANES:
        path, value, raw_sha256 = suites_by_lane[lane_id]
        suite_bindings.append(
            {
                "lane_id": lane_id,
                **_evidence_binding(path, raw_sha256, value),
            }
        )
    promotion_binding = _evidence_binding(
        promotion_path,
        promotion_sha256,
        promotion,
    )
    seed = {
        "suite_receipts": suite_bindings,
        "promotion_receipt": promotion_binding,
        "release_candidate_id": shared["release_candidate_id"],
        "deployment_generation_id": shared["deployment_generation_id"],
    }
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "contract": validation.Z0_FINAL_MANIFEST_CONTRACT,
        "manifest_id": (
            "Z0-FINAL-"
            + hashlib.sha256(_canonical_bytes(seed)).hexdigest()[:16].upper()
        ),
        "release_candidate_id": shared["release_candidate_id"],
        "deployment_generation_id": shared["deployment_generation_id"],
        "source_manifest_sha256": shared["source_manifest_sha256"],
        "validation_manifest_sha256": shared["validation_manifest_sha256"],
        "schema_catalog_sha256": shared["schema_catalog_sha256"],
        "current_schema_lineage_sha256": shared[
            "current_schema_lineage_sha256"
        ],
        "agents_md_sha256": shared["agents_md_sha256"],
        "goal_observation_sha256": shared["goal_observation_sha256"],
        "goal_thread_id": shared["goal_thread_id"],
        "cross_catalog_ownership_snapshot_sha256": shared[
            "cross_catalog_ownership_snapshot_sha256"
        ],
        "sbe_contract_audit_input_sha256": shared[
            "sbe_contract_audit_input_sha256"
        ],
        "synthetic_realm_config_fingerprint": shared[
            "realm_config_fingerprint"
        ],
        "production_realm_config_fingerprint": promotion[
            "realm_config_fingerprint"
        ],
        "suite_receipts": suite_bindings,
        "promotion_receipt": promotion_binding,
        "claims": deepcopy(validation.Z0_FINAL_MANIFEST_CLAIMS),
    }
    if set(manifest) != validation.Z0_FINAL_MANIFEST_FIELDS:
        raise Z0ManifestCompileError("final_manifest_field_set_invalid")
    for lane_id in validation.Z0_REQUIRED_SUITE_LANES:
        suite_errors = validation._validate_z0_suite_receipt(  # noqa: SLF001
            suites_by_lane[lane_id][1],
            lane_id=lane_id,
            manifest=manifest,
            ladder=ladder,
            subject="suite_receipt." + lane_id,
        )
        if suite_errors:
            raise Z0ManifestCompileError("suite_receipt_reconstruction_failed")

    payload = _pretty_bytes(manifest)
    manifest_sha256 = "sha256:" + hashlib.sha256(payload).hexdigest()
    evidence_root = validation.Z0_FINAL_EVIDENCE_ROOT.resolve(strict=True)
    stage_path: Path | None = None
    try:
        descriptor, stage_name = tempfile.mkstemp(
            prefix=".z0-final-manifest-stage-",
            suffix=".json",
            dir=str(evidence_root),
        )
        stage_path = Path(stage_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        final_errors = validation._validate_z0_final_evidence(  # noqa: SLF001
            _staged_generic_receipt(
                manifest,
                manifest_path=stage_path,
                manifest_sha256=manifest_sha256,
            ),
            "z0_final_manifest",
            ladder=ladder,
            sbe_report=sbe_report,
        )
        if final_errors:
            raise Z0ManifestCompileError("final_manifest_reconstruction_failed")

        _assert_inputs_unchanged(input_hashes)
        current_sbe = _current_sbe_report()
        if current_sbe.get("input_sha256") != sbe_report.get("input_sha256"):
            raise Z0ManifestCompileError("sbe_contract_audit_changed_before_publish")
        current_after, checkpoint_paths_after = _current_authority(current_sbe)
        if current_after != current or checkpoint_paths_after != checkpoint_paths:
            raise Z0ManifestCompileError("authority_changed_before_publish")
        # The second contract audit may take long enough for a direct evidence
        # input to change after the first byte check.  Close that last TOCTOU
        # window immediately before the create-only publish.
        _assert_inputs_unchanged(input_hashes)

        with output.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if stage_path is not None:
            try:
                stage_path.unlink(missing_ok=True)
            except OSError:
                pass

    return {
        "contract": RESULT_CONTRACT,
        "status": "pass",
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest_sha256,
        "suite_receipt_count": len(suite_bindings),
        "promotion_receipt_count": 1,
        "tests_executed_by_compiler": False,
        "operator_actions_executed": False,
        "full_rollout_enabled": False,
        "new_packet_or_gate_credit_awarded": False,
        "product_complete": False,
        "active_shared_goal_complete": False,
    }


def _parser() -> _Parser:
    parser = _Parser(prog="compile-internal-collaborative-z0-final-manifest")
    parser.add_argument(
        "--suite-receipt",
        action="append",
        type=Path,
        required=True,
    )
    parser.add_argument("--promotion-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    compact = False
    try:
        arguments = _parser().parse_args(argv)
        compact = arguments.compact
        result = compile_z0_final_manifest(
            suite_receipt_paths=arguments.suite_receipt,
            promotion_receipt_path=arguments.promotion_receipt,
            output_path=arguments.output,
        )
        exit_code = 0
    except FileExistsError:
        result = {
            "contract": RESULT_CONTRACT,
            "status": "fail",
            "error": {"code": "OUTPUT_ALREADY_EXISTS"},
            "tests_executed_by_compiler": False,
            "operator_actions_executed": False,
            "full_rollout_enabled": False,
            "new_packet_or_gate_credit_awarded": False,
            "product_complete": False,
        }
        exit_code = 1
    except (OSError, UnicodeError, json.JSONDecodeError, Z0ManifestCompileError) as exc:
        result = {
            "contract": RESULT_CONTRACT,
            "status": "fail",
            "error": {"code": str(exc) or "Z0_FINAL_MANIFEST_COMPILE_FAILED"},
            "tests_executed_by_compiler": False,
            "operator_actions_executed": False,
            "full_rollout_enabled": False,
            "new_packet_or_gate_credit_awarded": False,
            "product_complete": False,
        }
        exit_code = 1
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if compact else 2,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RESULT_CONTRACT",
    "Z0ManifestCompileError",
    "compile_z0_final_manifest",
    "main",
]

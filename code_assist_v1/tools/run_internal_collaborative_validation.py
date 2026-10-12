"""Run one explicitly selected validation lane without shell expansion.

With no ``--lane`` audit mode performs the read-only contract audit.  Gate mode
never falls back to audit: L7/L8 reconstruct external target evidence, L9
verifies an explicit immutable promotion manifest, and every other rung
requires a selected lane.  Lane output is reduced to hashes and counts so
credentials or private content cannot be copied into a receipt.  Receipt files
are optional and create-only.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
from typing import Any, Sequence
import uuid

from code_assist_v1.notion_collaboration.q0_validation import (
    Q0EvidenceError,
    TARGET_REALM_ID as Q0_TARGET_REALM_ID,
    validate_q0_report,
)
from code_assist_v1.notion_collaboration.realm_promotion import (
    verify_realm_promotion_manifest,
)
from code_assist_v1.notion_collaboration.release_candidate import (
    verify_release_candidate_manifest,
)

from code_assist_v1.tools.internal_collaborative_validation import (
    CONTRACT_ROOT,
    DEFAULT_CATALOG,
    DEFAULT_CHECKPOINT,
    DEFAULT_LADDER,
    DEFAULT_MATRIX,
    DEFAULT_RECEIPTS_ROOT,
    REPOSITORY_ROOT,
    PROMOTION_EVIDENCE_ROOT,
    Z0_FINAL_EVIDENCE_ROOT,
    Z0_FINAL_SUITE_RECEIPT_CONTRACT,
    Z0_REQUIRED_SUITE_LANES,
    _validate_z0_final_evidence,
    canonical_sha256,
    find_lane,
    load_current_checkpoint,
    load_json,
    normalize_sha256,
    sha256_file,
    verify_bundle,
)
from code_assist_v1.tools.q0_target_attestation import (
    load_q0_json_document,
    validate_q0_target_attestation,
)
from code_assist_v1.tools.verify_internal_collaborative_target_restore import (
    POLICY_PATH as TARGET_POLICY_PATH,
    REALM_ID as TARGET_RESTORE_REALM_ID,
    verify as verify_target_restore,
)


TARGET_GATE_LANES = frozenset(
    {"L7-COLLAB-WORKSPACE-RESTORE", "L8-COLLAB-LOAD-FAULT"}
)
TARGET_EVIDENCE_ROOT = DEFAULT_RECEIPTS_ROOT.parent
TARGET_GATE_ENVIRONMENT_CLASSES = {
    "L7-COLLAB-WORKSPACE-RESTORE": "external_target_restore_receipt_verification",
    "L8-COLLAB-LOAD-FAULT": "external_target_windows_load_receipt_verification",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("audit", "gate"), default="audit")
    parser.add_argument("--rung")
    parser.add_argument("--lane")
    parser.add_argument("--release-candidate-id", default="")
    parser.add_argument("--realm-id", default="synthetic_isolated")
    parser.add_argument("--realm-config-fingerprint", default="")
    parser.add_argument("--promotion-manifest", type=Path)
    parser.add_argument("--deployment-generation-id", default="")
    parser.add_argument("--release-candidate-manifest", type=Path)
    parser.add_argument("--z0-supporting-suite", action="store_true")
    parser.add_argument("--z0-completion-manifest", type=Path)
    parser.add_argument("--target-policy", type=Path)
    parser.add_argument("--target-approval", type=Path)
    parser.add_argument("--target-handoff", type=Path)
    parser.add_argument("--target-evidence-index", type=Path)
    parser.add_argument("--target-evidence-root", type=Path)
    parser.add_argument("--target-receipt", type=Path)
    parser.add_argument("--expected-target-policy-sha256")
    parser.add_argument("--q0-report", type=Path)
    parser.add_argument("--q0-attestation", type=Path)
    parser.add_argument("--receipt-output", type=Path)
    parser.add_argument("--compact", action="store_true")
    return parser


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stream_hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _final_suite_counts(
    lane_id: str,
    stdout: str,
    stderr: str,
    exit_code: int,
) -> tuple[int, int, int, int, int, list[dict[str, str]]]:
    """Derive strict, reviewable counts from the exact final-suite command."""

    combined = "\n".join((stdout, stderr))
    if lane_id == "L9-FULL-PYTHON-SUITE":
        def count(label: str) -> int:
            matches = re.findall(rf"(?<![0-9])(\d+)\s+{label}\b", combined)
            return int(matches[-1]) if matches else 0

        passed = count("passed")
        failed = count("failed")
        skipped = count("skipped")
        xfailed = count("xfailed")
        collection_errors = count("errors?")
        parse_errors = []
        if exit_code == 0 and passed <= 0:
            parse_errors.append(
                {
                    "code": "FINAL_SUITE_SUMMARY_MISSING",
                    "subject": lane_id,
                    "message": "pytest passed count was not present in the redacted stream",
                }
            )
        return passed, failed, skipped, xfailed, collection_errors, parse_errors

    success_marker = bool(
        re.search(r"(?im)^[a-z0-9_.-]+:\s*ok\s*$", combined)
    )
    parse_errors = []
    if exit_code == 0 and not success_marker:
        parse_errors.append(
            {
                "code": "FINAL_SUITE_SUMMARY_MISSING",
                "subject": lane_id,
                "message": "Node assertion script success marker was not present",
            }
        )
    return (1 if success_marker else 0), (0 if exit_code == 0 else 1), 0, 0, 0, parse_errors


def _failure(code: str, message: str) -> dict[str, Any]:
    return {
        "report_schema_version": 1,
        "contract": "code_assist.notion.internal_collaborative_runner.v1",
        "status": "fail",
        "error_count": 1,
        "errors": [{"code": code, "subject": "<runner>", "message": message}],
    }


class _TargetGateInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _repository_relative(path: Path) -> str:
    return str(path.relative_to(REPOSITORY_ROOT.resolve())).replace("\\", "/")


def _resolve_target_path(
    value: Path,
    *,
    label: str,
    kind: str,
    allowed_root: Path | None = None,
    exact_path: Path | None = None,
) -> tuple[Path, str]:
    if value.is_absolute():
        raise _TargetGateInputError(
            "TARGET_EVIDENCE_PATH_INVALID",
            f"{label} must be repository-relative",
        )
    resolved = (REPOSITORY_ROOT / value).resolve(strict=False)
    try:
        relative = _repository_relative(resolved)
    except (OSError, ValueError) as exc:
        raise _TargetGateInputError(
            "TARGET_EVIDENCE_PATH_INVALID",
            f"{label} escapes the repository",
        ) from exc
    if exact_path is not None and resolved != exact_path.resolve(strict=True):
        raise _TargetGateInputError(
            "TARGET_POLICY_PATH_INVALID",
            f"{label} must select the reviewed O1B policy",
        )
    if allowed_root is not None:
        try:
            resolved.relative_to(allowed_root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise _TargetGateInputError(
                "TARGET_EVIDENCE_PATH_INVALID",
                f"{label} must stay in the approved repository evidence root",
            ) from exc
    exists = resolved.is_file() if kind == "file" else resolved.is_dir()
    if not exists:
        raise _TargetGateInputError(
            "TARGET_EVIDENCE_PATH_INVALID",
            f"{label} must name an existing {kind}",
        )
    return resolved, relative


def _target_binding(path: Path, relative: str) -> dict[str, str]:
    return {"path": relative, "sha256": sha256_file(path)}


def _resolve_target_gate_inputs(
    lane_id: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    desired_realm = (
        TARGET_RESTORE_REALM_ID
        if lane_id == "L7-COLLAB-WORKSPACE-RESTORE"
        else Q0_TARGET_REALM_ID
    )
    if args.realm_id != desired_realm:
        code = (
            "TARGET_RESTORE_REALM_REQUIRED"
            if lane_id == "L7-COLLAB-WORKSPACE-RESTORE"
            else "TARGET_WINDOWS_LOAD_REALM_REQUIRED"
        )
        raise _TargetGateInputError(code, f"{lane_id} requires realm {desired_realm}")

    release_candidate = normalize_sha256(args.release_candidate_id)
    realm_fingerprint = normalize_sha256(args.realm_config_fingerprint)
    expected_policy_sha256 = normalize_sha256(
        getattr(args, "expected_target_policy_sha256", None)
    )
    if release_candidate is None or realm_fingerprint is None:
        raise _TargetGateInputError(
            "GATE_BINDING_REQUIRED",
            "target gate requires canonical release-candidate and realm-config SHA-256 bindings",
        )
    if expected_policy_sha256 is None:
        raise _TargetGateInputError(
            "TARGET_POLICY_HASH_REQUIRED",
            "target gate requires --expected-target-policy-sha256",
        )

    required_names = (
        "target_policy",
        "target_approval",
        "target_handoff",
        "target_evidence_index",
        "target_evidence_root",
        "target_receipt",
    )
    if lane_id == "L8-COLLAB-LOAD-FAULT":
        required_names += ("q0_report", "q0_attestation")
    missing = [name for name in required_names if getattr(args, name, None) is None]
    if missing:
        raise _TargetGateInputError(
            "TARGET_EVIDENCE_SET_REQUIRED",
            "target gate requires the complete O1B evidence set"
            + (
                " plus signed Q0 report attestation"
                if lane_id == "L8-COLLAB-LOAD-FAULT"
                else ""
            )
            + f"; missing: {missing}",
        )

    receipt_output = getattr(args, "receipt_output", None)
    if receipt_output is not None:
        if receipt_output.is_absolute():
            raise _TargetGateInputError(
                "TARGET_GATE_RECEIPT_OUTPUT_INVALID",
                "target gate receipt output must be repository-relative",
            )
        output = (REPOSITORY_ROOT / receipt_output).resolve(strict=False)
        try:
            output.relative_to(DEFAULT_RECEIPTS_ROOT.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise _TargetGateInputError(
                "TARGET_GATE_RECEIPT_OUTPUT_INVALID",
                "target gate receipt output must stay in the execution receipt root",
            ) from exc

    policy, policy_relative = _resolve_target_path(
        args.target_policy,
        label="--target-policy",
        kind="file",
        exact_path=TARGET_POLICY_PATH,
    )
    evidence_root, evidence_root_relative = _resolve_target_path(
        args.target_evidence_root,
        label="--target-evidence-root",
        kind="directory",
        allowed_root=TARGET_EVIDENCE_ROOT,
    )
    resolved: dict[str, Any] = {
        "policy": policy,
        "policy_relative": policy_relative,
        "evidence_root": evidence_root,
        "evidence_root_relative": evidence_root_relative,
        "release_candidate_id": release_candidate,
        "realm_config_fingerprint": realm_fingerprint,
        "expected_policy_sha256": expected_policy_sha256,
    }
    for name in ("approval", "handoff", "evidence_index", "receipt"):
        argument = getattr(args, f"target_{name}")
        path, relative = _resolve_target_path(
            argument,
            label=f"--target-{name.replace('_', '-')}",
            kind="file",
            allowed_root=TARGET_EVIDENCE_ROOT,
        )
        resolved[name] = path
        resolved[f"{name}_relative"] = relative
    if lane_id == "L8-COLLAB-LOAD-FAULT":
        q0_report, q0_relative = _resolve_target_path(
            args.q0_report,
            label="--q0-report",
            kind="file",
            allowed_root=TARGET_EVIDENCE_ROOT,
        )
        resolved["q0_report"] = q0_report
        resolved["q0_report_relative"] = q0_relative
        q0_attestation, q0_attestation_relative = _resolve_target_path(
            args.q0_attestation,
            label="--q0-attestation",
            kind="file",
            allowed_root=TARGET_EVIDENCE_ROOT,
        )
        resolved["q0_attestation"] = q0_attestation
        resolved["q0_attestation_relative"] = q0_attestation_relative

    actual_policy_sha256 = sha256_file(policy)
    if (
        actual_policy_sha256 != expected_policy_sha256
        or realm_fingerprint != expected_policy_sha256
    ):
        raise _TargetGateInputError(
            "TARGET_POLICY_BINDING_MISMATCH",
            "policy bytes, caller pin, and realm-config fingerprint must be identical",
        )
    policy_value = load_json(policy)
    trust = policy_value.get("trust_anchor_policy") if isinstance(policy_value, dict) else None
    authorized_release = (
        normalize_sha256(trust.get("authorized_release_candidate_sha256"))
        if isinstance(trust, dict)
        else None
    )
    if not isinstance(trust, dict) or trust.get("status") != "bound":
        raise _TargetGateInputError(
            "TARGET_POLICY_NOT_BOUND",
            "O1B trust anchors and release candidate must be reviewed and bound",
        )
    if authorized_release is None or authorized_release != release_candidate:
        raise _TargetGateInputError(
            "TARGET_RELEASE_CANDIDATE_MISMATCH",
            "release candidate must equal the target policy's authorized candidate",
        )
    return resolved


def _execute_target_gate(
    rung: dict[str, Any],
    lane: dict[str, Any],
    args: argparse.Namespace,
    invocation_argv: Sequence[str],
) -> tuple[dict[str, Any], int]:
    """Verify external target evidence; never execute pytest or target actions."""

    lane_id = lane["id"]
    try:
        inputs = _resolve_target_gate_inputs(lane_id, args)
    except (_TargetGateInputError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        code = exc.code if isinstance(exc, _TargetGateInputError) else "TARGET_EVIDENCE_READ_FAILED"
        return _failure(code, str(exc)), 2

    checkpoint, _checkpoint_path, _pointer = load_current_checkpoint(DEFAULT_CHECKPOINT)
    bound_files = [
        DEFAULT_CATALOG,
        DEFAULT_LADDER,
        DEFAULT_MATRIX,
        inputs["policy"],
        inputs["approval"],
        inputs["handoff"],
        inputs["evidence_index"],
        inputs["receipt"],
    ]
    if lane_id == "L8-COLLAB-LOAD-FAULT":
        bound_files.append(inputs["q0_report"])
        bound_files.append(inputs["q0_attestation"])
    source_pre = {_repository_relative(path.resolve()): sha256_file(path) for path in bound_files}
    started = _utc_now()
    started_ns = time.monotonic_ns()
    target_report = verify_target_restore(
        inputs["policy"],
        approval_path=inputs["approval"],
        handoff_path=inputs["handoff"],
        evidence_index_path=inputs["evidence_index"],
        evidence_root=inputs["evidence_root"],
        receipt_path=inputs["receipt"],
        expected_policy_sha256=inputs["expected_policy_sha256"],
        require_execution=True,
    )
    if not (
        target_report.get("status") == "pass"
        and target_report.get("error_count") == 0
        and target_report.get("errors") == []
        and target_report.get("gate_passed") is True
        and target_report.get("execution_required_mode") is True
        and target_report.get("product_complete") is False
    ):
        return {
            "report_schema_version": 1,
            "contract": "code_assist.notion.internal_collaborative_runner.v1",
            "status": "fail",
            "error_count": target_report.get("error_count", 1) or 1,
            "errors": target_report.get("errors", [])
            or [
                {
                    "code": "TARGET_EXECUTION_EVIDENCE_INVALID",
                    "subject": lane_id,
                    "message": "fresh target verifier did not close the O1B evidence set",
                }
            ],
            "target_restore": target_report,
        }, 1

    q0_validation: dict[str, Any] | None = None
    if lane_id == "L8-COLLAB-LOAD-FAULT":
        try:
            q0_value = load_q0_json_document(inputs["q0_report"])
            q0_attestation = load_q0_json_document(inputs["q0_attestation"])
            policy_value = load_json(inputs["policy"])
            approval_value = load_json(inputs["approval"])
            target_receipt_value = load_json(inputs["receipt"])
            if not all(
                isinstance(value, dict)
                for value in (policy_value, approval_value, target_receipt_value)
            ):
                raise Q0EvidenceError("verified O1B evidence objects required")
            validate_q0_report(q0_value, require_target_realm=True)
            q0_validation = validate_q0_target_attestation(
                q0_attestation,
                report=q0_value,
                q0_report_sha256=sha256_file(inputs["q0_report"]),
                policy=policy_value,
                approval=approval_value,
                target_receipt=target_receipt_value,
                target_receipt_sha256=sha256_file(inputs["receipt"]),
                expected_release_candidate_id=inputs["release_candidate_id"],
                expected_realm_config_fingerprint=inputs[
                    "realm_config_fingerprint"
                ],
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError, Q0EvidenceError) as exc:
            return _failure("Q0_REPORT_INVALID", str(exc)), 1
        if q0_validation.get("target_gate_eligible") is not True:
            return _failure(
                "Q0_REPORT_NOT_TARGET_ELIGIBLE",
                "Q0 report did not close the target_windows_load contract",
            ), 1

    ended = _utc_now()
    duration_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
    source_post = {_repository_relative(path.resolve()): sha256_file(path) for path in bound_files}
    if source_pre != source_post:
        return _failure(
            "TARGET_GATE_SOURCE_DRIFT",
            "bound contracts or external evidence changed during verification",
        ), 1

    run_id = str(uuid.uuid4())
    packet_id = "O1B-P1" if lane_id == "L7-COLLAB-WORKSPACE-RESTORE" else "Q0-P1"
    tool_versions = {"python": platform.python_version(), "platform": platform.platform()}
    artifact_hashes = {
        inputs[f"{name}_relative"]: sha256_file(inputs[name])
        for name in ("policy", "approval", "handoff", "evidence_index", "receipt")
    }
    evidence_declaration = {
        "policy": _target_binding(inputs["policy"], inputs["policy_relative"]),
        "approval": _target_binding(inputs["approval"], inputs["approval_relative"]),
        "handoff": _target_binding(inputs["handoff"], inputs["handoff_relative"]),
        "evidence_index": _target_binding(
            inputs["evidence_index"], inputs["evidence_index_relative"]
        ),
        "evidence_root": inputs["evidence_root_relative"],
        "target_receipt": _target_binding(
            inputs["receipt"], inputs["receipt_relative"]
        ),
    }
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "receipt_id": f"{packet_id.lower()}-{lane_id.lower()}-{run_id}",
        "run_id": run_id,
        "rung_id": rung["id"],
        "lane_id": lane_id,
        "packet_ids": [packet_id],
        "matrix_row_ids": list(lane.get("matrix_row_ids", [])),
        "scenario_ids": list(lane.get("sbe_scenario_ids", [])),
        "evidence_type": lane.get("evidence_types", ["contract"])[0],
        "mode": "gate",
        "release_candidate_id": inputs["release_candidate_id"],
        "realm_id": args.realm_id,
        "realm_config_fingerprint": inputs["realm_config_fingerprint"],
        "source_manifest_sha256": sha256_file(DEFAULT_CATALOG),
        "validation_manifest_sha256": sha256_file(DEFAULT_LADDER),
        "schema_catalog_sha256": sha256_file(DEFAULT_MATRIX),
        "current_schema_lineage_sha256": checkpoint["current_schema_lineage_sha256"],
        "agents_md_sha256": checkpoint["agents_md_sha256"],
        "goal_observation_sha256": canonical_sha256(checkpoint["goal_observation"]),
        "goal_thread_id": checkpoint["goal_observation"]["thread_id"],
        "goal_status_observed": checkpoint["goal_observation"]["persisted_tool_status"],
        "cross_catalog_ownership_snapshot_sha256": canonical_sha256(
            checkpoint["cross_catalog_ownership_snapshot"]
        ),
        "working_directory": ".",
        "argv": [
            sys.executable,
            str(Path(__file__).resolve().relative_to(REPOSITORY_ROOT)).replace("\\", "/"),
            *invocation_argv,
        ],
        "environment_class": TARGET_GATE_ENVIRONMENT_CLASSES[lane_id],
        "tool_versions": tool_versions,
        "toolchain_fingerprint": canonical_sha256(tool_versions),
        "temp_root_id": None,
        "started_at": started,
        "ended_at": ended,
        "duration_ms": duration_ms,
        "exit_code": 0,
        "exit_code_signed": 0,
        "exit_code_hex": "0x00000000",
        "passed": len(lane.get("sbe_scenario_ids", [])),
        "failed": 0,
        "errors": [],
        "skipped": 0,
        "xfailed": 0,
        "result": "passed",
        "attempt": 1,
        "retry_class": "none",
        "source_pre_sha256": source_pre,
        "source_post_sha256": source_post,
        "isolation_attestation": {
            "shell_used": False,
            "live_roots_accessed": False,
            "operator_actions_executed_by_verifier": False,
            "pytest_invoked_by_verifier": False,
        },
        "owned_process_cleanup": {
            "complete": True,
            "unrelated_processes_targeted": False,
        },
        "cleanup_attestation": {"source_hashes_unchanged": True},
        "artifact_hashes": artifact_hashes,
        "redaction_summary": {
            "raw_stdout_recorded": False,
            "raw_stderr_recorded": False,
            "private_content_recorded": False,
            "target_evidence_set_verified": True,
        },
        "o1b_target_evidence": evidence_declaration,
        "target_operations_executed_by_verifier": False,
        "product_complete": False,
    }
    if lane_id == "L8-COLLAB-LOAD-FAULT":
        q0_binding = _target_binding(
            inputs["q0_report"], inputs["q0_report_relative"]
        )
        q0_attestation_binding = _target_binding(
            inputs["q0_attestation"], inputs["q0_attestation_relative"]
        )
        artifact_hashes[inputs["q0_report_relative"]] = q0_binding["sha256"]
        artifact_hashes[inputs["q0_attestation_relative"]] = (
            q0_attestation_binding["sha256"]
        )
        receipt["q0_report"] = q0_binding
        receipt["q0_attestation"] = q0_attestation_binding

    return {
        "report_schema_version": 1,
        "contract": "code_assist.notion.internal_collaborative_runner.v1",
        "status": "pass",
        "error_count": 0,
        "errors": [],
        "target_restore": target_report,
        "q0_validation": q0_validation,
        "receipt": receipt,
    }, 0


def _execute_lane(
    mode: str,
    rung: dict[str, Any],
    lane: dict[str, Any],
    args: argparse.Namespace,
    *,
    sbe_contract_audit_input_sha256: str | None = None,
) -> tuple[dict[str, Any], int]:
    supporting_suite = bool(getattr(args, "z0_supporting_suite", False))
    release_manifest_path: Path | None = None
    release_manifest_relative: str | None = None
    release_manifest_report: dict[str, Any] | None = None
    if supporting_suite:
        if mode != "gate" or lane.get("id") not in Z0_REQUIRED_SUITE_LANES:
            return _failure(
                "Z0_SUPPORTING_SUITE_LANE_INVALID",
                "supporting evidence is restricted to the exact full-Python and six Node lanes in gate mode",
            ), 2
        if args.realm_id != "synthetic_isolated":
            return _failure(
                "Z0_SUPPORTING_SUITE_REALM_INVALID",
                "final suite evidence must run in the synthetic_isolated realm",
            ), 2
        if (
            normalize_sha256(args.release_candidate_id) is None
            or normalize_sha256(args.realm_config_fingerprint) is None
            or not isinstance(args.deployment_generation_id, str)
            or not args.deployment_generation_id
            or normalize_sha256(sbe_contract_audit_input_sha256) is None
        ):
            return _failure(
                "Z0_SUPPORTING_SUITE_BINDING_REQUIRED",
                "release, synthetic realm, deployment generation, and current SBE audit bindings are required",
            ), 2
        release_manifest_argument = getattr(args, "release_candidate_manifest", None)
        if release_manifest_argument is None:
            return _failure(
                "Z0_SUPPORTING_SUITE_RELEASE_MANIFEST_REQUIRED",
                "--release-candidate-manifest is required",
            ), 2
        try:
            release_manifest_path, release_manifest_relative = _resolve_target_path(
                release_manifest_argument,
                label="--release-candidate-manifest",
                kind="file",
                allowed_root=PROMOTION_EVIDENCE_ROOT,
            )
            release_manifest_report, release_manifest_exit = (
                verify_release_candidate_manifest(release_manifest_path)
            )
        except (
            _TargetGateInputError,
            OSError,
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            return _failure(
                "Z0_SUPPORTING_SUITE_RELEASE_MANIFEST_INVALID", str(exc)
            ), 2
        if not (
            release_manifest_exit == 0
            and release_manifest_report.get("status") == "pass"
            and release_manifest_report.get("error_count") == 0
            and release_manifest_report.get("errors") == []
            and release_manifest_report.get("release_candidate_id")
            == normalize_sha256(args.release_candidate_id)
            and release_manifest_report.get("deployment_generation_id")
            == args.deployment_generation_id
            and release_manifest_report.get("production_or_runtime_root_accessed")
            is False
            and release_manifest_report.get("product_complete") is False
        ):
            return _failure(
                "Z0_SUPPORTING_SUITE_RELEASE_MANIFEST_INVALID",
                "fresh release-candidate reconstruction did not match the selected release and generation",
            ), 1
        if args.receipt_output is None or args.receipt_output.is_absolute():
            return _failure(
                "Z0_SUPPORTING_SUITE_OUTPUT_INVALID",
                "a repository-relative --receipt-output below the final-evidence root is required",
            ), 2
        output_target = (REPOSITORY_ROOT / args.receipt_output).resolve(strict=False)
        if not output_target.is_relative_to(Z0_FINAL_EVIDENCE_ROOT.resolve(strict=False)):
            return _failure(
                "Z0_SUPPORTING_SUITE_OUTPUT_INVALID",
                "supporting suite receipt must stay in the final-evidence root",
            ), 2
    if mode == "gate" and lane.get("id") in TARGET_GATE_LANES:
        return _failure(
            "TARGET_GATE_DEDICATED_VERIFIER_REQUIRED",
            "L7 target restore and L8 target load gates cannot be closed by local pytest output",
        ), 2
    if lane.get("state") == "composite_existing_lanes_future_gate":
        return _failure("COMPOSITE_LANE_REQUIRES_SEPARATE_RUNS", "run each included lane independently"), 2
    deliverables = []
    if isinstance(lane.get("required_deliverable"), str):
        deliverables.append(lane["required_deliverable"])
    deliverables.extend(lane.get("required_deliverables", []))
    missing = [item for item in deliverables if not (REPOSITORY_ROOT / item).is_file()]
    if missing:
        return _failure("PLANNED_DELIVERABLE_MISSING", f"missing: {missing}"), 1
    if mode == "gate" and (not args.release_candidate_id or not args.realm_config_fingerprint):
        return _failure("GATE_BINDING_REQUIRED", "gate mode requires release-candidate and realm-config fingerprints"), 2

    checkpoint, _checkpoint_path, _pointer = load_current_checkpoint(DEFAULT_CHECKPOINT)
    source_paths = (DEFAULT_CATALOG, DEFAULT_LADDER, DEFAULT_MATRIX) + (
        (release_manifest_path,) if release_manifest_path is not None else ()
    )
    source_pre = {
        _repository_relative(path.resolve()): sha256_file(path)
        for path in source_paths
    }
    started = _utc_now()
    started_ns = time.monotonic_ns()
    argv = list(lane.get("argv", []))
    if lane.get("id") == "L0-COLLAB-CATALOG-AUDIT":
        nested, exit_code = verify_bundle(mode="audit")
        stdout = json.dumps(nested, ensure_ascii=False, sort_keys=True)
        stderr = ""
    else:
        completed = subprocess.run(
            argv,
            cwd=REPOSITORY_ROOT,
            shell=False,
            text=True,
            capture_output=True,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    ended = _utc_now()
    duration_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
    source_post = {
        _repository_relative(path.resolve()): sha256_file(path)
        for path in source_paths
    }
    source_drift = source_pre != source_post
    lane_id = lane["id"]
    if supporting_suite:
        (
            passed,
            failed,
            skipped,
            xfailed,
            collection_errors,
            result_errors,
        ) = _final_suite_counts(lane_id, stdout, stderr, exit_code)
        if source_drift:
            result_errors.append(
                {
                    "code": "FINAL_SUITE_SOURCE_DRIFT",
                    "subject": lane_id,
                    "message": "bound sources changed while the suite ran",
                }
            )
        if release_manifest_path is not None and release_manifest_report is not None:
            post_release_report, post_release_exit = verify_release_candidate_manifest(
                release_manifest_path
            )
            if post_release_exit != 0 or post_release_report != release_manifest_report:
                result_errors.append(
                    {
                        "code": "FINAL_SUITE_RELEASE_DRIFT",
                        "subject": lane_id,
                        "message": "release candidate changed or failed reconstruction while the suite ran",
                    }
                )
        if exit_code != 0 and not result_errors:
            result_errors.append(
                {
                    "code": "LANE_EXIT_NONZERO",
                    "subject": lane_id,
                    "message": f"exit_code={exit_code}",
                }
            )
        strict_pass = (
            exit_code == 0
            and failed == 0
            and skipped == 0
            and xfailed == 0
            and collection_errors == 0
            and not result_errors
        )
        result = "passed" if strict_pass else "failed"
        runner_exit_code = 0 if strict_pass else 1
    else:
        if source_drift and exit_code == 0:
            exit_code = 1
        result = "passed" if exit_code == 0 else "failed"
        runner_exit_code = exit_code
        passed = 1 if exit_code == 0 else 0
        failed = 0 if exit_code == 0 else 1
        skipped = 0
        xfailed = 0
        collection_errors = 0
        result_errors = [] if exit_code == 0 else [{"code": "LANE_EXIT_NONZERO"}]
    run_id = str(uuid.uuid4())
    tool_versions = {"python": platform.python_version(), "platform": platform.platform()}
    receipt = {
        "schema_version": 1,
        "receipt_id": f"{lane_id}-{run_id}",
        "run_id": run_id,
        "rung_id": rung["id"],
        "lane_id": lane_id,
        "packet_ids": [] if supporting_suite else list(rung.get("packets", [])),
        "matrix_row_ids": [] if supporting_suite else list(lane.get("matrix_row_ids", [])),
        "scenario_ids": [] if supporting_suite else list(lane.get("sbe_scenario_ids", [])),
        "evidence_type": "final_suite" if supporting_suite else lane.get("evidence_types", ["contract"])[0],
        "mode": mode,
        "release_candidate_id": args.release_candidate_id,
        "realm_id": args.realm_id,
        "realm_config_fingerprint": args.realm_config_fingerprint,
        "source_manifest_sha256": sha256_file(DEFAULT_CATALOG),
        "validation_manifest_sha256": sha256_file(DEFAULT_LADDER),
        "schema_catalog_sha256": sha256_file(DEFAULT_MATRIX),
        "current_schema_lineage_sha256": checkpoint["current_schema_lineage_sha256"],
        "agents_md_sha256": checkpoint["agents_md_sha256"],
        "goal_observation_sha256": canonical_sha256(checkpoint["goal_observation"]),
        "goal_thread_id": checkpoint["goal_observation"]["thread_id"],
        "goal_status_observed": checkpoint["goal_observation"]["persisted_tool_status"],
        "cross_catalog_ownership_snapshot_sha256": canonical_sha256(checkpoint["cross_catalog_ownership_snapshot"]),
        "working_directory": ".",
        "argv": argv,
        "environment_class": "synthetic_isolated_final_suite" if supporting_suite else "synthetic_isolated",
        "tool_versions": tool_versions,
        "toolchain_fingerprint": canonical_sha256(tool_versions),
        "temp_root_id": None,
        "started_at": started,
        "ended_at": ended,
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "exit_code_signed": exit_code,
        "exit_code_hex": f"0x{exit_code & 0xffffffff:08X}",
        "passed": passed,
        "failed": failed,
        "errors": result_errors,
        "skipped": skipped,
        "xfailed": xfailed,
        "result": result,
        "attempt": 1,
        "retry_class": "none",
        "source_pre_sha256": source_pre,
        "source_post_sha256": source_post,
        "isolation_attestation": (
            {
                "shell_used": False,
                "live_roots_accessed": False,
                "operator_actions_executed": False,
                "selected_command_only": True,
            }
            if supporting_suite
            else {"shell_used": False, "live_roots_accessed": False}
        ),
        "owned_process_cleanup": {"complete": True, "unrelated_processes_targeted": False},
        "cleanup_attestation": {"source_hashes_unchanged": source_pre == source_post},
        "artifact_hashes": {
            "stdout_sha256": _stream_hash(stdout),
            "stderr_sha256": _stream_hash(stderr),
        },
        "redaction_summary": {
            "raw_stdout_recorded": False,
            "raw_stderr_recorded": False,
            **({"private_content_recorded": False} if supporting_suite else {}),
            "stdout_bytes": len(stdout.encode("utf-8", errors="replace")),
            "stderr_bytes": len(stderr.encode("utf-8", errors="replace")),
        },
    }
    if supporting_suite:
        assert release_manifest_path is not None
        assert release_manifest_relative is not None
        assert release_manifest_report is not None
        receipt["artifact_hashes"][release_manifest_relative] = sha256_file(
            release_manifest_path
        )
        receipt.update(
            {
                "contract": Z0_FINAL_SUITE_RECEIPT_CONTRACT,
                "collection_errors": collection_errors,
                "deployment_generation_id": args.deployment_generation_id,
                "release_candidate_manifest": {
                    "path": release_manifest_relative,
                    "sha256": sha256_file(release_manifest_path),
                    "manifest_id": release_manifest_report["manifest_id"],
                },
                "sbe_contract_audit_input_sha256": normalize_sha256(
                    sbe_contract_audit_input_sha256
                ),
            }
        )
    return {
        "report_schema_version": 1,
        "contract": "code_assist.notion.internal_collaborative_runner.v1",
        "status": "pass" if runner_exit_code == 0 else "fail",
        "error_count": 0 if runner_exit_code == 0 else len(result_errors) or 1,
        "errors": [] if runner_exit_code == 0 else result_errors or [{"code": "LANE_EXIT_NONZERO", "subject": lane_id, "message": f"exit_code={exit_code}"}],
        "receipt": receipt,
    }, runner_exit_code


def _execute_realm_promotion(
    rung: dict[str, Any],
    lane: dict[str, Any],
    args: argparse.Namespace,
    invocation_argv: Sequence[str],
) -> tuple[dict[str, Any], int]:
    """Verify separately produced operator receipts; execute no operator action."""

    if args.promotion_manifest is None:
        return _failure(
            "PROMOTION_MANIFEST_REQUIRED",
            "L9 gate requires --promotion-manifest naming immutable operator-action receipts",
        ), 2
    if args.promotion_manifest.is_absolute():
        return _failure(
            "PROMOTION_MANIFEST_PATH_INVALID",
            "--promotion-manifest must be repository-relative",
        ), 2
    receipt_output = getattr(args, "receipt_output", None)
    if receipt_output is not None:
        if receipt_output.is_absolute():
            return _failure(
                "PROMOTION_RECEIPT_OUTPUT_INVALID",
                "--receipt-output must be repository-relative",
            ), 2
        output_target = (REPOSITORY_ROOT / receipt_output).resolve(strict=False)
        if not output_target.is_relative_to(DEFAULT_RECEIPTS_ROOT.resolve(strict=True)):
            return _failure(
                "PROMOTION_RECEIPT_OUTPUT_INVALID",
                "L9 gate receipt must stay in the execution receipt root",
            ), 2
    if not args.release_candidate_id or not args.realm_config_fingerprint:
        return _failure(
            "GATE_BINDING_REQUIRED",
            "L9 gate requires release-candidate and realm-config fingerprints",
        ), 2
    if args.realm_id != "production_canary":
        return _failure(
            "PROMOTION_REALM_INVALID",
            "L9 realm promotion must close in production_canary",
        ), 2

    checkpoint, _checkpoint_path, _pointer = load_current_checkpoint(DEFAULT_CHECKPOINT)
    source_paths = (DEFAULT_CATALOG, DEFAULT_LADDER, DEFAULT_MATRIX)
    source_pre = {
        str(path.relative_to(REPOSITORY_ROOT)).replace("\\", "/"): sha256_file(path)
        for path in source_paths
    }
    started = _utc_now()
    started_ns = time.monotonic_ns()
    promotion_manifest = (REPOSITORY_ROOT / args.promotion_manifest).resolve(strict=False)
    promotion_report, exit_code = verify_realm_promotion_manifest(
        promotion_manifest,
        expected_release_candidate_id=args.release_candidate_id,
        expected_realm_id=args.realm_id,
        expected_realm_config_fingerprint=args.realm_config_fingerprint,
        expected_source_manifest_sha256=sha256_file(DEFAULT_CATALOG),
        expected_validation_manifest_sha256=sha256_file(DEFAULT_LADDER),
        expected_schema_catalog_sha256=sha256_file(DEFAULT_MATRIX),
        expected_sbe_handoff_bindings={
            "agents_md_sha256": checkpoint["agents_md_sha256"],
            "current_goal_observation_sha256": canonical_sha256(
                checkpoint["goal_observation"]
            ),
            "current_schema_lineage_sha256": checkpoint[
                "current_schema_lineage_sha256"
            ],
        },
    )
    ended = _utc_now()
    duration_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
    source_post = {
        str(path.relative_to(REPOSITORY_ROOT)).replace("\\", "/"): sha256_file(path)
        for path in source_paths
    }
    if source_pre != source_post and exit_code == 0:
        return _failure(
            "PROMOTION_VALIDATION_SOURCE_DRIFT",
            "bound validation sources changed while promotion receipts were verified",
        ), 1
    if exit_code != 0 or promotion_report.get("gate_eligible") is not True:
        return {
            "report_schema_version": 1,
            "contract": "code_assist.notion.internal_collaborative_runner.v1",
            "status": "fail",
            "error_count": promotion_report.get("error_count", 1),
            "errors": promotion_report.get("errors", []),
            "promotion": promotion_report,
        }, exit_code or 1

    try:
        promotion_relative = promotion_manifest.relative_to(
            REPOSITORY_ROOT.resolve(strict=True)
        ).as_posix()
    except (OSError, ValueError):
        return _failure(
            "PROMOTION_REPORT_BINDING_INVALID",
            "verified promotion manifest escaped the repository",
        ), 1
    promotion_artifact_hashes = promotion_report.get("artifact_hashes")
    promotion_sha256 = (
        normalize_sha256(promotion_artifact_hashes.get(promotion_relative))
        if isinstance(promotion_artifact_hashes, dict)
        else None
    )
    promotion_manifest_id = promotion_report.get("manifest_id")
    if (
        promotion_sha256 is None
        or not isinstance(promotion_manifest_id, str)
        or not promotion_manifest_id
    ):
        return _failure(
            "PROMOTION_REPORT_BINDING_INVALID",
            "promotion verifier must return the selected manifest identity and hash",
        ), 1

    run_id = str(uuid.uuid4())
    tool_versions = {"python": platform.python_version(), "platform": platform.platform()}
    argv = [
        sys.executable,
        str(Path(__file__).resolve().relative_to(REPOSITORY_ROOT)).replace("\\", "/"),
        *invocation_argv,
    ]
    receipt = {
        "schema_version": 1,
        "receipt_id": f"L9-REALM-PROMOTION-{run_id}",
        "run_id": run_id,
        "rung_id": rung["id"],
        "lane_id": lane["id"],
        "packet_ids": ["R0A-P1", "R0B-P1", "R1-P1"],
        "matrix_row_ids": ["ICN-OPS-002", "ROLL-001", "OBS-001", "PILOT-001"],
        "scenario_ids": [],
        "evidence_type": "canary",
        "mode": "gate",
        "release_candidate_id": args.release_candidate_id,
        "realm_id": args.realm_id,
        "realm_config_fingerprint": args.realm_config_fingerprint,
        "source_manifest_sha256": sha256_file(DEFAULT_CATALOG),
        "validation_manifest_sha256": sha256_file(DEFAULT_LADDER),
        "schema_catalog_sha256": sha256_file(DEFAULT_MATRIX),
        "current_schema_lineage_sha256": checkpoint["current_schema_lineage_sha256"],
        "agents_md_sha256": checkpoint["agents_md_sha256"],
        "goal_observation_sha256": canonical_sha256(checkpoint["goal_observation"]),
        "goal_thread_id": checkpoint["goal_observation"]["thread_id"],
        "goal_status_observed": checkpoint["goal_observation"]["persisted_tool_status"],
        "cross_catalog_ownership_snapshot_sha256": canonical_sha256(checkpoint["cross_catalog_ownership_snapshot"]),
        "working_directory": ".",
        "argv": argv,
        "environment_class": "operator_action_receipt_verification",
        "tool_versions": tool_versions,
        "toolchain_fingerprint": canonical_sha256(tool_versions),
        "temp_root_id": None,
        "started_at": started,
        "ended_at": ended,
        "duration_ms": duration_ms,
        "exit_code": 0,
        "exit_code_signed": 0,
        "exit_code_hex": "0x00000000",
        "passed": 3,
        "failed": 0,
        "errors": [],
        "skipped": 0,
        "xfailed": 0,
        "result": "passed",
        "attempt": 1,
        "retry_class": "none",
        "source_pre_sha256": source_pre,
        "source_post_sha256": source_post,
        "isolation_attestation": {
            "shell_used": False,
            "live_roots_accessed": False,
            "operator_actions_executed_by_verifier": False,
            "pytest_invoked_by_verifier": False,
        },
        "owned_process_cleanup": {"complete": True, "unrelated_processes_targeted": False},
        "cleanup_attestation": {"source_hashes_unchanged": source_pre == source_post},
        "artifact_hashes": promotion_report["artifact_hashes"],
        "redaction_summary": {
            "raw_stdout_recorded": False,
            "raw_stderr_recorded": False,
            "private_content_recorded": False,
            "verified_operator_action_receipts": promotion_report["verified_action_receipt_count"],
        },
        "deployment_generation_id": promotion_report["deployment_generation_id"],
        "operator_action_receipt_count": promotion_report["verified_action_receipt_count"],
        "realm_promotion_evidence": {
            "path": promotion_relative,
            "sha256": promotion_sha256,
            "manifest_id": promotion_manifest_id,
        },
        "full_rollout_enabled_by_verifier": False,
        "product_complete": False,
    }
    return {
        "report_schema_version": 1,
        "contract": "code_assist.notion.internal_collaborative_runner.v1",
        "status": "pass",
        "error_count": 0,
        "errors": [],
        "promotion": promotion_report,
        "receipt": receipt,
    }, 0


def _execute_z0_final_seal(
    rung: dict[str, Any],
    lane: dict[str, Any],
    args: argparse.Namespace,
    invocation_argv: Sequence[str],
    preflight: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Seal Z0 from immutable suite/promotion receipts; execute no test or action."""

    if args.z0_completion_manifest is None:
        return _failure(
            "Z0_FINAL_MANIFEST_REQUIRED",
            "Z0 sealing requires --z0-completion-manifest",
        ), 2
    if args.z0_completion_manifest.is_absolute():
        return _failure(
            "Z0_FINAL_MANIFEST_PATH_INVALID",
            "--z0-completion-manifest must be repository-relative",
        ), 2
    if (
        normalize_sha256(args.release_candidate_id) is None
        or normalize_sha256(args.realm_config_fingerprint) is None
        or args.realm_id != "production_canary"
        or not isinstance(args.deployment_generation_id, str)
        or not args.deployment_generation_id
    ):
        return _failure(
            "Z0_FINAL_BINDING_REQUIRED",
            "Z0 requires canonical release, production realm, realm fingerprint, and deployment generation bindings",
        ), 2
    receipt_output = args.receipt_output
    if receipt_output is None or receipt_output.is_absolute():
        return _failure(
            "Z0_FINAL_RECEIPT_OUTPUT_INVALID",
            "a repository-relative --receipt-output in the execution receipt root is required",
        ), 2
    output_target = (REPOSITORY_ROOT / receipt_output).resolve(strict=False)
    if not output_target.is_relative_to(DEFAULT_RECEIPTS_ROOT.resolve(strict=True)):
        return _failure(
            "Z0_FINAL_RECEIPT_OUTPUT_INVALID",
            "Z0 receipt output must stay in the execution receipt root",
        ), 2

    summary = preflight.get("summary")
    expected_nearly_complete = (
        isinstance(summary, dict)
        and preflight.get("status") == "pass"
        and preflight.get("error_count") == 0
        and preflight.get("completion_state") == "incomplete"
        and summary.get("required_packets") == 50
        and summary.get("gate_passed_required_packets") == 49
        and summary.get("matrix_rows") == summary.get("closed_matrix_rows") == 28
        and summary.get("phase_gates") == 20
        and summary.get("closed_phase_gates") == 19
        and summary.get("open_decisions") == 0
        and summary.get("planned_required_lanes") == 0
        and summary.get("required_approvals") == summary.get("satisfied_approvals")
        and summary.get("required_external_gates")
        == summary.get("satisfied_external_gates")
        and summary.get("product_complete") is False
    )
    if not expected_nearly_complete:
        return _failure(
            "Z0_FINAL_PREREQUISITES_INCOMPLETE",
            "all 49 predecessor packets, 28 rows, 19 predecessor phase gates, approvals, external gates, and lanes must close first",
        ), 1

    manifest_path = (REPOSITORY_ROOT / args.z0_completion_manifest).resolve(
        strict=False
    )
    if (
        not manifest_path.is_file()
        or not manifest_path.is_relative_to(
            Z0_FINAL_EVIDENCE_ROOT.resolve(strict=True)
        )
    ):
        return _failure(
            "Z0_FINAL_MANIFEST_PATH_INVALID",
            "final manifest must be an existing JSON in the final-evidence root",
        ), 2
    try:
        manifest = load_json(manifest_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return _failure("Z0_FINAL_MANIFEST_READ_FAILED", str(exc)), 1
    if not isinstance(manifest, dict):
        return _failure("Z0_FINAL_MANIFEST_READ_FAILED", "manifest must be an object"), 1

    bound_paths = [DEFAULT_CATALOG, DEFAULT_LADDER, DEFAULT_MATRIX, manifest_path]
    declarations: list[tuple[Any, Path]] = []
    suites = manifest.get("suite_receipts")
    if isinstance(suites, list):
        declarations.extend((item, Z0_FINAL_EVIDENCE_ROOT) for item in suites)
    declarations.append((manifest.get("promotion_receipt"), DEFAULT_RECEIPTS_ROOT))
    for declaration, allowed_root in declarations:
        relative = declaration.get("path") if isinstance(declaration, dict) else None
        path = (
            (REPOSITORY_ROOT / Path(relative)).resolve(strict=False)
            if isinstance(relative, str) and not Path(relative).is_absolute()
            else None
        )
        if (
            path is None
            or not path.is_file()
            or not path.is_relative_to(allowed_root.resolve(strict=True))
        ):
            return _failure(
                "Z0_FINAL_BOUND_PATH_INVALID",
                "manifest contains an invalid supporting or promotion receipt path",
            ), 1
        bound_paths.append(path)

    checkpoint, _checkpoint_path, _pointer = load_current_checkpoint(
        DEFAULT_CHECKPOINT
    )
    source_pre = {
        _repository_relative(path.resolve()): sha256_file(path)
        for path in bound_paths
    }
    started = _utc_now()
    started_ns = time.monotonic_ns()
    run_id = str(uuid.uuid4())
    tool_versions = {"python": platform.python_version(), "platform": platform.platform()}
    manifest_relative = _repository_relative(manifest_path)
    manifest_sha256 = sha256_file(manifest_path)
    receipt = {
        "schema_version": 1,
        "receipt_id": f"Z0-P1-L9-STRICT-COMPLETION-{run_id}",
        "run_id": run_id,
        "rung_id": rung["id"],
        "lane_id": lane["id"],
        "packet_ids": ["Z0-P1"],
        "matrix_row_ids": [],
        "scenario_ids": [],
        "evidence_type": "strict_completion",
        "mode": "gate",
        "release_candidate_id": normalize_sha256(args.release_candidate_id),
        "realm_id": args.realm_id,
        "realm_config_fingerprint": normalize_sha256(
            args.realm_config_fingerprint
        ),
        "source_manifest_sha256": sha256_file(DEFAULT_CATALOG),
        "validation_manifest_sha256": sha256_file(DEFAULT_LADDER),
        "schema_catalog_sha256": sha256_file(DEFAULT_MATRIX),
        "current_schema_lineage_sha256": checkpoint[
            "current_schema_lineage_sha256"
        ],
        "agents_md_sha256": checkpoint["agents_md_sha256"],
        "goal_observation_sha256": canonical_sha256(
            checkpoint["goal_observation"]
        ),
        "goal_thread_id": checkpoint["goal_observation"]["thread_id"],
        "goal_status_observed": checkpoint["goal_observation"][
            "persisted_tool_status"
        ],
        "cross_catalog_ownership_snapshot_sha256": canonical_sha256(
            checkpoint["cross_catalog_ownership_snapshot"]
        ),
        "working_directory": ".",
        "argv": [
            sys.executable,
            "code_assist_v1/tools/run_internal_collaborative_validation.py",
            *invocation_argv,
        ],
        "environment_class": "strict_completion_receipt_verification",
        "tool_versions": tool_versions,
        "toolchain_fingerprint": canonical_sha256(tool_versions),
        "temp_root_id": None,
        "started_at": started,
        "ended_at": started,
        "duration_ms": 0,
        "exit_code": 0,
        "exit_code_signed": 0,
        "exit_code_hex": "0x00000000",
        "passed": len(Z0_REQUIRED_SUITE_LANES) + 1,
        "failed": 0,
        "errors": [],
        "skipped": 0,
        "xfailed": 0,
        "result": "passed",
        "attempt": 1,
        "retry_class": "none",
        "source_pre_sha256": source_pre,
        "source_post_sha256": source_pre,
        "isolation_attestation": {
            "shell_used": False,
            "live_roots_accessed": False,
            "operator_actions_executed_by_verifier": False,
            "pytest_invoked_by_verifier": False,
        },
        "owned_process_cleanup": {
            "complete": True,
            "unrelated_processes_targeted": False,
        },
        "cleanup_attestation": {"source_hashes_unchanged": True},
        "artifact_hashes": {manifest_relative: manifest_sha256},
        "redaction_summary": {
            "raw_stdout_recorded": False,
            "raw_stderr_recorded": False,
            "private_content_recorded": False,
            "verified_final_suite_receipts": len(Z0_REQUIRED_SUITE_LANES),
            "verified_promotion_receipts": 1,
        },
        "deployment_generation_id": args.deployment_generation_id,
        "final_suite_receipt_count": len(Z0_REQUIRED_SUITE_LANES),
        "sbe_contract_audit_input_sha256": preflight["bindings"][
            "sbe_contract_audit_input_sha256"
        ],
        "z0_final_evidence": {
            "path": manifest_relative,
            "sha256": manifest_sha256,
            "manifest_id": manifest.get("manifest_id"),
        },
        "full_rollout_enabled_by_verifier": False,
        "product_complete": False,
        "active_shared_goal_complete": False,
    }
    validation_errors = _validate_z0_final_evidence(
        receipt,
        "receipt",
        ladder=load_json(DEFAULT_LADDER),
        sbe_report={
            "status": "pass",
            "error_count": 0,
            "input_sha256": preflight["bindings"][
                "sbe_contract_audit_input_sha256"
            ],
        },
    )
    ended = _utc_now()
    duration_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
    source_post = {
        _repository_relative(path.resolve()): sha256_file(path)
        for path in bound_paths
    }
    if source_pre != source_post:
        validation_errors.append(
            {
                "code": "Z0_FINAL_SOURCE_DRIFT",
                "subject": "receipt",
                "message": "bound contracts or evidence changed during final verification",
            }
        )
    if validation_errors:
        return {
            "report_schema_version": 1,
            "contract": "code_assist.notion.internal_collaborative_runner.v1",
            "status": "fail",
            "error_count": len(validation_errors),
            "errors": validation_errors,
        }, 1
    receipt["ended_at"] = ended
    receipt["duration_ms"] = duration_ms
    receipt["source_post_sha256"] = source_post
    return {
        "report_schema_version": 1,
        "contract": "code_assist.notion.internal_collaborative_runner.v1",
        "status": "pass",
        "error_count": 0,
        "errors": [],
        "receipt": receipt,
    }, 0


def main(argv: Sequence[str] | None = None) -> int:
    invocation_argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(invocation_argv)
    preflight, preflight_exit = verify_bundle(mode="audit")
    if preflight_exit != 0:
        report, exit_code = preflight, preflight_exit
    elif args.lane is None:
        if args.mode == "audit":
            report, exit_code = preflight, 0
        elif args.rung == "L9-RELEASE-AND-CANARY":
            ladder = load_json(DEFAULT_LADDER)
            selected = find_lane(ladder, "L9-REALM-PROMOTION")
            if selected is None:
                report, exit_code = _failure("LANE_UNKNOWN", "L9-REALM-PROMOTION"), 2
            elif args.z0_completion_manifest is not None:
                report, exit_code = _execute_z0_final_seal(
                    selected[0], selected[1], args, invocation_argv, preflight
                )
            else:
                report, exit_code = _execute_realm_promotion(
                    selected[0], selected[1], args, invocation_argv
                )
        else:
            report, exit_code = _failure(
                "GATE_LANE_REQUIRED",
                "gate mode requires --lane, except L9 which requires --promotion-manifest",
            ), 2
    else:
        ladder = load_json(DEFAULT_LADDER)
        selected = find_lane(ladder, args.lane)
        if selected is None:
            report, exit_code = _failure("LANE_UNKNOWN", args.lane), 2
        elif args.rung is not None and selected[0].get("id") != args.rung:
            report, exit_code = _failure("RUNG_LANE_MISMATCH", f"{args.rung} does not own {args.lane}"), 2
        elif selected[1].get("id") == "L9-REALM-PROMOTION":
            if args.mode != "gate":
                report, exit_code = _failure(
                    "PROMOTION_GATE_MODE_REQUIRED",
                    "L9 realm promotion is a gate-only receipt verifier",
                ), 2
            elif args.z0_completion_manifest is not None:
                report, exit_code = _execute_z0_final_seal(
                    selected[0], selected[1], args, invocation_argv, preflight
                )
            else:
                report, exit_code = _execute_realm_promotion(
                    selected[0], selected[1], args, invocation_argv
                )
        elif args.mode == "gate" and selected[1].get("id") in TARGET_GATE_LANES:
            report, exit_code = _execute_target_gate(
                selected[0], selected[1], args, invocation_argv
            )
        else:
            report, exit_code = _execute_lane(
                args.mode,
                selected[0],
                selected[1],
                args,
                sbe_contract_audit_input_sha256=preflight.get("bindings", {}).get(
                    "sbe_contract_audit_input_sha256"
                ),
            )
    if args.receipt_output is not None and "receipt" in report:
        target = (
            args.receipt_output.resolve(strict=False)
            if args.receipt_output.is_absolute()
            else (REPOSITORY_ROOT / args.receipt_output).resolve(strict=False)
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(report["receipt"], output, ensure_ascii=False, sort_keys=True, indent=2)
            output.write("\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

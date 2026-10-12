from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import code_assist_v1.tools.internal_collaborative_validation as validation
import code_assist_v1.tools.run_internal_collaborative_validation as runner


_HASH_A = "sha256:" + "a" * 64
_HASH_B = "sha256:" + "b" * 64
_HASH_C = "sha256:" + "c" * 64
_HASH_D = "sha256:" + "d" * 64
_GENERATION = "deployment-generation-z0-test"


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _supporting_receipt(
    lane_id: str,
    *,
    rung_id: str,
    argv: list[str],
) -> dict:
    return {
        "schema_version": 1,
        "contract": validation.Z0_FINAL_SUITE_RECEIPT_CONTRACT,
        "receipt_id": "support-" + lane_id.lower(),
        "run_id": "run-" + lane_id.lower(),
        "rung_id": rung_id,
        "lane_id": lane_id,
        "packet_ids": [],
        "matrix_row_ids": [],
        "scenario_ids": [],
        "evidence_type": "final_suite",
        "mode": "gate",
        "release_candidate_id": _HASH_A,
        "realm_id": "synthetic_isolated",
        "realm_config_fingerprint": _HASH_B,
        "source_manifest_sha256": _HASH_C,
        "validation_manifest_sha256": _HASH_D,
        "schema_catalog_sha256": _HASH_A,
        "current_schema_lineage_sha256": _HASH_B,
        "agents_md_sha256": _HASH_C,
        "goal_observation_sha256": _HASH_D,
        "goal_thread_id": "goal-thread-z0-test",
        "goal_status_observed": "active",
        "cross_catalog_ownership_snapshot_sha256": _HASH_A,
        "working_directory": ".",
        "argv": argv,
        "environment_class": "synthetic_isolated_final_suite",
        "tool_versions": {"python": "test", "platform": "test"},
        "toolchain_fingerprint": _HASH_B,
        "temp_root_id": None,
        "started_at": "2026-09-09T00:00:00Z",
        "ended_at": "2026-09-09T00:00:01Z",
        "duration_ms": 1000,
        "exit_code": 0,
        "exit_code_signed": 0,
        "exit_code_hex": "0x00000000",
        "passed": 1,
        "failed": 0,
        "errors": [],
        "skipped": 0,
        "xfailed": 0,
        "collection_errors": 0,
        "result": "passed",
        "attempt": 1,
        "retry_class": "none",
        "source_pre_sha256": {"source": _HASH_A},
        "source_post_sha256": {"source": _HASH_A},
        "isolation_attestation": {
            "shell_used": False,
            "live_roots_accessed": False,
            "operator_actions_executed": False,
            "selected_command_only": True,
        },
        "owned_process_cleanup": {
            "complete": True,
            "unrelated_processes_targeted": False,
        },
        "cleanup_attestation": {"source_hashes_unchanged": True},
        "artifact_hashes": {
            "stdout_sha256": _HASH_C,
            "stderr_sha256": _HASH_D,
        },
        "redaction_summary": {
            "raw_stdout_recorded": False,
            "raw_stderr_recorded": False,
            "private_content_recorded": False,
        },
        "deployment_generation_id": _GENERATION,
        "sbe_contract_audit_input_sha256": _HASH_D,
    }


@pytest.fixture
def z0_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    evidence_root = tmp_path / "evidence" / "internal_collaboration_completion"
    receipt_root = tmp_path / "evidence" / "internal_collaboration_receipts"
    promotion_root = tmp_path / "evidence" / "internal_collaboration_promotion"
    evidence_root.mkdir(parents=True)
    receipt_root.mkdir(parents=True)
    promotion_root.mkdir(parents=True)
    monkeypatch.setattr(validation, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(validation, "Z0_FINAL_EVIDENCE_ROOT", evidence_root)
    monkeypatch.setattr(validation, "DEFAULT_RECEIPTS_ROOT", receipt_root)
    monkeypatch.setattr(validation, "PROMOTION_EVIDENCE_ROOT", promotion_root)
    monkeypatch.setattr(validation, "_validate_realm_promotion_evidence", lambda *args: [])
    release_manifest = {"manifest_id": "release-manifest-z0-test"}
    release_path = _write_json(promotion_root / "release.json", release_manifest)
    release_relative = release_path.relative_to(tmp_path).as_posix()
    monkeypatch.setattr(
        validation,
        "verify_selected_z0_release_candidate",
        lambda path: (
            {
                "status": "pass",
                "error_count": 0,
                "errors": [],
                "manifest_id": release_manifest["manifest_id"],
                "manifest_sha256": validation.sha256_file(path),
                "release_candidate_id": _HASH_A,
                "deployment_generation_id": _GENERATION,
                "production_or_runtime_root_accessed": False,
                "product_complete": False,
            },
            0,
        ),
    )

    ladder = validation.load_json(validation.DEFAULT_LADDER)
    lane_lookup = {
        lane["id"]: (rung["id"], lane["argv"])
        for rung in ladder["rungs"]
        for lane in rung["lanes"]
        if lane.get("id") in validation.Z0_REQUIRED_SUITE_LANES
    }
    suite_bindings = []
    suite_paths = {}
    for lane_id in validation.Z0_REQUIRED_SUITE_LANES:
        rung_id, argv = lane_lookup[lane_id]
        value = _supporting_receipt(lane_id, rung_id=rung_id, argv=argv)
        value["release_candidate_manifest"] = {
            "path": release_relative,
            "sha256": validation.sha256_file(release_path),
            "manifest_id": release_manifest["manifest_id"],
        }
        value["artifact_hashes"][release_relative] = validation.sha256_file(
            release_path
        )
        path = _write_json(evidence_root / (lane_id.lower() + ".json"), value)
        suite_paths[lane_id] = path
        suite_bindings.append(
            {
                "lane_id": lane_id,
                "path": path.relative_to(tmp_path).as_posix(),
                "sha256": validation.sha256_file(path),
                "receipt_id": value["receipt_id"],
            }
        )

    promotion = {
        "receipt_id": "promotion-z0-test",
        "packet_ids": ["R0A-P1", "R0B-P1", "R1-P1"],
        "release_candidate_id": _HASH_A,
        "deployment_generation_id": _GENERATION,
        "realm_config_fingerprint": _HASH_C,
    }
    promotion_path = _write_json(receipt_root / "promotion.json", promotion)
    manifest = {
        "schema_version": 1,
        "contract": validation.Z0_FINAL_MANIFEST_CONTRACT,
        "manifest_id": "z0-final-manifest-test",
        "release_candidate_id": _HASH_A,
        "deployment_generation_id": _GENERATION,
        "source_manifest_sha256": _HASH_C,
        "validation_manifest_sha256": _HASH_D,
        "schema_catalog_sha256": _HASH_A,
        "current_schema_lineage_sha256": _HASH_B,
        "agents_md_sha256": _HASH_C,
        "goal_observation_sha256": _HASH_D,
        "goal_thread_id": "goal-thread-z0-test",
        "cross_catalog_ownership_snapshot_sha256": _HASH_A,
        "sbe_contract_audit_input_sha256": _HASH_D,
        "synthetic_realm_config_fingerprint": _HASH_B,
        "production_realm_config_fingerprint": _HASH_C,
        "suite_receipts": suite_bindings,
        "promotion_receipt": {
            "path": promotion_path.relative_to(tmp_path).as_posix(),
            "sha256": validation.sha256_file(promotion_path),
            "receipt_id": promotion["receipt_id"],
        },
        "claims": copy.deepcopy(validation.Z0_FINAL_MANIFEST_CLAIMS),
    }
    manifest_path = _write_json(evidence_root / "manifest.json", manifest)
    manifest_relative = manifest_path.relative_to(tmp_path).as_posix()
    receipt = {
        "packet_ids": ["Z0-P1"],
        "lane_id": "L9-REALM-PROMOTION",
        "realm_id": "production_canary",
        "environment_class": "strict_completion_receipt_verification",
        "matrix_row_ids": [],
        "scenario_ids": [],
        "deployment_generation_id": _GENERATION,
        "final_suite_receipt_count": 7,
        "release_candidate_id": _HASH_A,
        "realm_config_fingerprint": _HASH_C,
        "source_manifest_sha256": _HASH_C,
        "validation_manifest_sha256": _HASH_D,
        "schema_catalog_sha256": _HASH_A,
        "current_schema_lineage_sha256": _HASH_B,
        "agents_md_sha256": _HASH_C,
        "goal_observation_sha256": _HASH_D,
        "goal_thread_id": "goal-thread-z0-test",
        "cross_catalog_ownership_snapshot_sha256": _HASH_A,
        "sbe_contract_audit_input_sha256": _HASH_D,
        "artifact_hashes": {
            manifest_relative: validation.sha256_file(manifest_path),
        },
        "isolation_attestation": {
            "operator_actions_executed_by_verifier": False,
            "pytest_invoked_by_verifier": False,
        },
        "full_rollout_enabled_by_verifier": False,
        "product_complete": False,
        "active_shared_goal_complete": False,
        "z0_final_evidence": {
            "path": manifest_relative,
            "sha256": validation.sha256_file(manifest_path),
            "manifest_id": manifest["manifest_id"],
        },
    }
    sbe = {"status": "pass", "error_count": 0, "input_sha256": _HASH_D}
    return SimpleNamespace(
        receipt=receipt,
        manifest=manifest,
        manifest_path=manifest_path,
        suite_paths=suite_paths,
        ladder=ladder,
        release_path=release_path,
        sbe=sbe,
    )


@pytest.mark.unit
def test_z0_reconstructs_exact_full_suite_and_promotion(z0_bundle: SimpleNamespace) -> None:
    assert validation._validate_z0_final_evidence(
        z0_bundle.receipt,
        "receipt",
        ladder=z0_bundle.ladder,
        sbe_report=z0_bundle.sbe,
    ) == []


@pytest.mark.unit
def test_z0_rejects_generic_gate_receipt_without_final_evidence() -> None:
    receipt = validation.load_json(
        validation.DEFAULT_RECEIPTS_ROOT
        / "t0b-p3-l4-cross-process-browser-20260824.json"
    )
    receipt.update(
        {
            "rung_id": "L9-RELEASE-AND-CANARY",
            "lane_id": "L9-REALM-PROMOTION",
            "packet_ids": ["Z0-P1"],
            "matrix_row_ids": [],
            "scenario_ids": [],
            "mode": "gate",
            "result": "passed",
            "exit_code": 0,
            "skipped": 0,
            "xfailed": 0,
            "release_candidate_id": _HASH_A,
            "realm_config_fingerprint": _HASH_B,
            "source_manifest_sha256": validation.sha256_file(validation.DEFAULT_CATALOG),
            "validation_manifest_sha256": validation.sha256_file(validation.DEFAULT_LADDER),
            "schema_catalog_sha256": validation.sha256_file(validation.DEFAULT_MATRIX),
            "current_schema_lineage_sha256": validation.sha256_file(
                validation.KNOWLEDGE_DOCS_ROOT
                / "second_brain_evolution"
                / "FORWARD_SCHEMA.md"
            ),
            "agents_md_sha256": validation.sha256_file(validation.PROJECT_ROOT / "AGENTS.md"),
            "source_pre_sha256": {},
            "source_post_sha256": {},
        }
    )
    valid, errors = validation.validate_execution_receipts(
        [receipt],
        validation.load_json(validation.DEFAULT_CATALOG),
        validation.load_json(validation.DEFAULT_LADDER),
        validation.load_json(validation.DEFAULT_MATRIX),
        sbe_report={"status": "pass", "error_count": 0, "input_sha256": _HASH_D},
    )
    assert valid == []
    assert "Z0_FINAL_EVIDENCE_REQUIRED" in {item["code"] for item in errors}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("skipped", 1, "Z0_SUITE_RESULT_INVALID"),
        ("xfailed", 1, "Z0_SUITE_RESULT_INVALID"),
        ("collection_errors", 1, "Z0_SUITE_RESULT_INVALID"),
        ("retry_class", "host_flaky", "Z0_SUITE_RESULT_INVALID"),
        ("release_candidate_id", _HASH_D, "Z0_SUITE_BINDING_DRIFT"),
    ],
)
def test_z0_rejects_non_strict_or_drifted_suite_receipt(
    z0_bundle: SimpleNamespace,
    field: str,
    value: object,
    code: str,
) -> None:
    lane_id = validation.Z0_REQUIRED_SUITE_LANES[0]
    path = z0_bundle.suite_paths[lane_id]
    suite = validation.load_json(path)
    suite[field] = value
    _write_json(path, suite)
    binding = next(
        item for item in z0_bundle.manifest["suite_receipts"] if item["lane_id"] == lane_id
    )
    binding["sha256"] = validation.sha256_file(path)
    _write_json(z0_bundle.manifest_path, z0_bundle.manifest)
    manifest_relative = z0_bundle.manifest_path.relative_to(
        validation.REPOSITORY_ROOT
    ).as_posix()
    manifest_hash = validation.sha256_file(z0_bundle.manifest_path)
    z0_bundle.receipt["z0_final_evidence"]["sha256"] = manifest_hash
    z0_bundle.receipt["artifact_hashes"] = {manifest_relative: manifest_hash}

    errors = validation._validate_z0_final_evidence(
        z0_bundle.receipt,
        "receipt",
        ladder=z0_bundle.ladder,
        sbe_report=z0_bundle.sbe,
    )
    assert code in {item["code"] for item in errors}


@pytest.mark.unit
def test_final_suite_count_parser_is_fail_closed() -> None:
    counts = runner._final_suite_counts(
        "L9-FULL-PYTHON-SUITE",
        "124 passed, 0 skipped in 10.1s",
        "",
        0,
    )
    assert counts == (124, 0, 0, 0, 0, [])

    missing = runner._final_suite_counts(
        "L9-FULL-PYTHON-SUITE", "warnings only", "", 0
    )
    assert missing[0] == 0
    assert missing[-1][0]["code"] == "FINAL_SUITE_SUMMARY_MISSING"

    node = runner._final_suite_counts(
        "L3-NODE-EDITOR-CORE", "notion_editor_core: ok\n", "", 0
    )
    assert node == (1, 0, 0, 0, 0, [])


@pytest.mark.unit
def test_supporting_suite_runner_emits_no_packet_credit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ladder = validation.load_json(validation.DEFAULT_LADDER)
    selected = validation.find_lane(ladder, "L3-NODE-EDITOR-CORE")
    assert selected is not None
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="notion_editor_core: ok\n",
            stderr="",
        ),
    )
    release_path = _write_json(
        tmp_path / "release.json", {"manifest_id": "release-manifest-test"}
    )
    release_report = {
        "status": "pass",
        "error_count": 0,
        "errors": [],
        "manifest_id": "release-manifest-test",
        "release_candidate_id": _HASH_A,
        "deployment_generation_id": _GENERATION,
        "production_or_runtime_root_accessed": False,
        "product_complete": False,
    }
    monkeypatch.setattr(
        runner,
        "_resolve_target_path",
        lambda *args, **kwargs: (release_path, "release.json"),
    )
    monkeypatch.setattr(
        runner,
        "verify_release_candidate_manifest",
        lambda *args, **kwargs: (release_report, 0),
    )
    original_repository_relative = runner._repository_relative
    monkeypatch.setattr(
        runner,
        "_repository_relative",
        lambda path: "release.json"
        if Path(path) == release_path.resolve()
        else original_repository_relative(path),
    )
    output = (
        validation.Z0_FINAL_EVIDENCE_ROOT.relative_to(validation.REPOSITORY_ROOT)
        / "support-test.json"
    )
    args = SimpleNamespace(
        z0_supporting_suite=True,
        realm_id="synthetic_isolated",
        release_candidate_id=_HASH_A,
        realm_config_fingerprint=_HASH_B,
        deployment_generation_id=_GENERATION,
        release_candidate_manifest=Path("release.json"),
        receipt_output=output,
    )
    report, exit_code = runner._execute_lane(
        "gate",
        selected[0],
        selected[1],
        args,
        sbe_contract_audit_input_sha256=_HASH_D,
    )
    assert exit_code == 0, report
    receipt = report["receipt"]
    assert receipt["contract"] == validation.Z0_FINAL_SUITE_RECEIPT_CONTRACT
    assert receipt["packet_ids"] == []
    assert receipt["passed"] == 1
    assert receipt["skipped"] == receipt["xfailed"] == receipt["collection_errors"] == 0
    manifest = {
        key: receipt[key]
        for key in (
            "release_candidate_id",
            "source_manifest_sha256",
            "validation_manifest_sha256",
            "schema_catalog_sha256",
            "current_schema_lineage_sha256",
            "agents_md_sha256",
            "goal_observation_sha256",
            "goal_thread_id",
            "cross_catalog_ownership_snapshot_sha256",
            "sbe_contract_audit_input_sha256",
            "deployment_generation_id",
        )
    }
    manifest["synthetic_realm_config_fingerprint"] = receipt[
        "realm_config_fingerprint"
    ]
    monkeypatch.setattr(validation, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(validation, "PROMOTION_EVIDENCE_ROOT", tmp_path)
    monkeypatch.setattr(
        validation,
        "verify_selected_z0_release_candidate",
        lambda path: (
            {
                **release_report,
                "manifest_sha256": validation.sha256_file(path),
            },
            0,
        ),
    )
    assert validation._validate_z0_suite_receipt(
        receipt,
        lane_id="L3-NODE-EDITOR-CORE",
        manifest=manifest,
        ladder=ladder,
        subject="receipt",
    ) == []


@pytest.mark.unit
def test_z0_seal_refuses_to_run_before_only_z0_remains() -> None:
    ladder = validation.load_json(validation.DEFAULT_LADDER)
    selected = validation.find_lane(ladder, "L9-REALM-PROMOTION")
    assert selected is not None
    args = SimpleNamespace(
        z0_completion_manifest=Path("missing.json"),
        release_candidate_id=_HASH_A,
        realm_config_fingerprint=_HASH_B,
        realm_id="production_canary",
        deployment_generation_id=_GENERATION,
        receipt_output=Path("receipt.json"),
    )
    report, exit_code = runner._execute_z0_final_seal(
        selected[0],
        selected[1],
        args,
        [],
        {
            "status": "pass",
            "error_count": 0,
            "completion_state": "incomplete",
            "summary": {
                "required_packets": 50,
                "gate_passed_required_packets": 48,
            },
        },
    )
    assert exit_code == 2 or exit_code == 1
    assert report["errors"][0]["code"] in {
        "Z0_FINAL_RECEIPT_OUTPUT_INVALID",
        "Z0_FINAL_PREREQUISITES_INCOMPLETE",
    }


@pytest.mark.unit
def test_z0_seal_verifies_evidence_without_executing_suites_or_actions(
    z0_bundle: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog_path = _write_json(
        tmp_path / "contracts" / "catalog.json",
        validation.load_json(validation.DEFAULT_CATALOG),
    )
    ladder_path = _write_json(
        tmp_path / "contracts" / "ladder.json", z0_bundle.ladder
    )
    matrix_path = _write_json(
        tmp_path / "contracts" / "matrix.json",
        validation.load_json(validation.DEFAULT_MATRIX),
    )
    authority_hashes = {
        "source_manifest_sha256": validation.sha256_file(catalog_path),
        "validation_manifest_sha256": validation.sha256_file(ladder_path),
        "schema_catalog_sha256": validation.sha256_file(matrix_path),
    }
    z0_bundle.manifest.update(authority_hashes)
    for item in z0_bundle.manifest["suite_receipts"]:
        path = tmp_path / item["path"]
        suite = validation.load_json(path)
        suite.update(authority_hashes)
        _write_json(path, suite)
        item["sha256"] = validation.sha256_file(path)
    _write_json(z0_bundle.manifest_path, z0_bundle.manifest)

    monkeypatch.setattr(runner, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(runner, "DEFAULT_CATALOG", catalog_path)
    monkeypatch.setattr(runner, "DEFAULT_LADDER", ladder_path)
    monkeypatch.setattr(runner, "DEFAULT_MATRIX", matrix_path)
    monkeypatch.setattr(runner, "Z0_FINAL_EVIDENCE_ROOT", z0_bundle.manifest_path.parent)
    receipt_root = tmp_path / "evidence" / "internal_collaboration_receipts"
    monkeypatch.setattr(runner, "DEFAULT_RECEIPTS_ROOT", receipt_root)
    monkeypatch.setattr(
        runner,
        "load_current_checkpoint",
        lambda *args: (
            {
                "current_schema_lineage_sha256": _HASH_B,
                "agents_md_sha256": _HASH_C,
                "goal_observation": {
                    "thread_id": "goal-thread-z0-test",
                    "persisted_tool_status": "active",
                },
                "cross_catalog_ownership_snapshot": {"snapshot": "test"},
            },
            Path("checkpoint.json"),
            None,
        ),
    )
    goal_hash = validation.canonical_sha256(
        {"thread_id": "goal-thread-z0-test", "persisted_tool_status": "active"}
    )
    ownership_hash = validation.canonical_sha256({"snapshot": "test"})
    z0_bundle.manifest["goal_observation_sha256"] = goal_hash
    z0_bundle.manifest["cross_catalog_ownership_snapshot_sha256"] = ownership_hash
    for item in z0_bundle.manifest["suite_receipts"]:
        path = tmp_path / item["path"]
        suite = validation.load_json(path)
        suite["goal_observation_sha256"] = goal_hash
        suite["cross_catalog_ownership_snapshot_sha256"] = ownership_hash
        _write_json(path, suite)
        item["sha256"] = validation.sha256_file(path)
    _write_json(z0_bundle.manifest_path, z0_bundle.manifest)

    selected = validation.find_lane(z0_bundle.ladder, "L9-REALM-PROMOTION")
    assert selected is not None
    manifest_relative = z0_bundle.manifest_path.relative_to(tmp_path)
    output_relative = receipt_root.relative_to(tmp_path) / "z0-final.json"
    args = SimpleNamespace(
        z0_completion_manifest=manifest_relative,
        release_candidate_id=_HASH_A,
        realm_config_fingerprint=_HASH_C,
        realm_id="production_canary",
        deployment_generation_id=_GENERATION,
        receipt_output=output_relative,
    )
    preflight = {
        "status": "pass",
        "error_count": 0,
        "completion_state": "incomplete",
        "bindings": {"sbe_contract_audit_input_sha256": _HASH_D},
        "summary": {
            "required_packets": 50,
            "gate_passed_required_packets": 49,
            "matrix_rows": 28,
            "closed_matrix_rows": 28,
            "phase_gates": 20,
            "closed_phase_gates": 19,
            "open_decisions": 0,
            "planned_required_lanes": 0,
            "required_approvals": 6,
            "satisfied_approvals": 6,
            "required_external_gates": 3,
            "satisfied_external_gates": 3,
            "product_complete": False,
        },
    }
    report, exit_code = runner._execute_z0_final_seal(
        selected[0], selected[1], args, ["--mode", "gate"], preflight
    )
    assert exit_code == 0, report
    receipt = report["receipt"]
    assert receipt["packet_ids"] == ["Z0-P1"]
    assert receipt["passed"] == 8
    assert receipt["isolation_attestation"]["pytest_invoked_by_verifier"] is False
    assert receipt["isolation_attestation"]["operator_actions_executed_by_verifier"] is False
    assert receipt["product_complete"] is False


@pytest.mark.unit
def test_z0_receipt_uses_status_integrator_pre_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = validation.load_json(
        validation.DEFAULT_RECEIPTS_ROOT
        / "t0b-p3-l4-cross-process-browser-20260824.json"
    )
    pre_catalog = "sha256:" + "1" * 64
    pre_ladder = "sha256:" + "2" * 64
    receipt.update(
        {
            "rung_id": "L9-RELEASE-AND-CANARY",
            "lane_id": "L9-REALM-PROMOTION",
            "packet_ids": ["Z0-P1"],
            "matrix_row_ids": [],
            "scenario_ids": [],
            "mode": "gate",
            "result": "passed",
            "exit_code": 0,
            "skipped": 0,
            "xfailed": 0,
            "release_candidate_id": _HASH_A,
            "realm_config_fingerprint": _HASH_B,
            "source_manifest_sha256": pre_catalog,
            "validation_manifest_sha256": pre_ladder,
            "schema_catalog_sha256": validation.sha256_file(validation.DEFAULT_MATRIX),
            "current_schema_lineage_sha256": validation.sha256_file(
                validation.KNOWLEDGE_DOCS_ROOT
                / "second_brain_evolution"
                / "FORWARD_SCHEMA.md"
            ),
            "agents_md_sha256": validation.sha256_file(validation.PROJECT_ROOT / "AGENTS.md"),
            "source_pre_sha256": {},
            "source_post_sha256": {},
        }
    )
    checkpoint = {
        "packet_id": "Z0-P1",
        "pre_edit_sha256": {
            validation.EXPECTED_HOT_PATHS[0]: pre_catalog,
            validation.EXPECTED_HOT_PATHS[1]: pre_ladder,
        },
        "post_edit_sha256": {
            validation.EXPECTED_HOT_PATHS[0]: validation.sha256_file(validation.DEFAULT_CATALOG),
            validation.EXPECTED_HOT_PATHS[1]: validation.sha256_file(validation.DEFAULT_LADDER),
        },
        "current_schema_lineage_sha256": receipt["current_schema_lineage_sha256"],
        "agents_md_sha256": receipt["agents_md_sha256"],
    }
    monkeypatch.setattr(validation, "_validate_z0_final_evidence", lambda *args, **kwargs: [])
    valid, errors = validation.validate_execution_receipts(
        [receipt],
        validation.load_json(validation.DEFAULT_CATALOG),
        validation.load_json(validation.DEFAULT_LADDER),
        validation.load_json(validation.DEFAULT_MATRIX),
        [checkpoint],
        sbe_report={"status": "pass", "error_count": 0, "input_sha256": _HASH_D},
    )
    assert errors == []
    assert valid == [receipt]

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256
from code_assist_v1.tools.verify_notion_np2c_archive_characterization import (
    DEFAULT_MATRIX,
    load_matrix,
)
from code_assist_v1.tools.verify_notion_np2c_archive_evidence_bundle import (
    BUNDLE_CONTRACT,
    REPOSITORY_ROOT,
    _expected_pairs,
    validate_bundle,
    verify_bundle,
)


def _raw_sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict, dict, str]:
    root = tmp_path / "external-archive-evidence"
    root.mkdir()
    matrix = load_matrix()
    matrix_sha256 = _raw_sha256(DEFAULT_MATRIX.read_bytes())
    profiles = {item["id"]: item for item in matrix["profiles"]}
    scenarios = {item["id"]: item for item in matrix["scenarios"]}
    runs: list[dict] = []

    for index, (scenario_id, profile_id) in enumerate(_expected_pairs(matrix), start=1):
        profile = profiles[profile_id]
        scenario = scenarios[scenario_id]
        tenant_id = (
            "tenant-enterprise"
            if profile["plan"] == "enterprise"
            else "tenant-business"
        )
        result_path = f"artifacts/{index:03d}.result.json"
        redaction_path = f"artifacts/{index:03d}.redaction.json"
        fixture_path = f"fixtures/{index:03d}.json"
        action_path = f"actions/{index:03d}.json"
        result_payload = json.dumps(
            {
                "schema_version": 1,
                "scenario_id": scenario_id,
                "profile_id": profile_id,
                "observations": [
                    {
                        "dimension": dimension,
                        "status": "observed",
                        "observation_sha256": "sha256:" + f"{index:064x}"[-64:],
                    }
                    for dimension in scenario["observation_dimensions"]
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        result_sha256 = _raw_sha256(result_payload)
        redaction_payload = json.dumps(
            {
                "schema_version": 1,
                "status": "passed",
                "reviewer_id": "reviewer-01",
                "reviewed_at": "2026-08-14T09:55:00+09:00",
                "artifact_sha256s": [result_sha256],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fixture_payload = json.dumps(
            {
                "schema_version": 1,
                "scenario_id": scenario_id,
                "profile_id": profile_id,
                "fixture_id": scenario["fixture_id"],
                "synthetic": True,
                "real_user_or_organization_content": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        action_payload = json.dumps(
            {
                "schema_version": 1,
                "scenario_id": scenario_id,
                "profile_id": profile_id,
                "normal_public_ui_only": True,
                "actions": scenario["actions"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        (root / result_path).parent.mkdir(parents=True, exist_ok=True)
        (root / result_path).write_bytes(result_payload)
        (root / redaction_path).write_bytes(redaction_payload)
        (root / fixture_path).parent.mkdir(parents=True, exist_ok=True)
        (root / fixture_path).write_bytes(fixture_payload)
        (root / action_path).parent.mkdir(parents=True, exist_ok=True)
        (root / action_path).write_bytes(action_payload)
        action_sha256 = _raw_sha256(action_payload)
        manifest = {
            "schema_version": 1,
            "scenario_id": scenario_id,
            "observation_state": "live-characterized",
            "promotion_status": "live_characterized",
            "reference_date": "2026-08-14",
            "reference_sources": [
                source["url"]
                for source in matrix["reference_sources"]
                if source["id"] in scenario["reference_source_ids"]
            ],
            "plan": profile["plan"],
            "role": profile["role"],
            "platform": profile["platform"],
            "app_or_browser_version": "Synthetic Browser 1.0",
            "locale": profile["locale"],
            "input_sha256": action_sha256,
            "result_sha256": result_sha256,
            "evidence": [result_path, redaction_path],
            "reference_conflicts": [],
            "reviewer": "reviewer-01",
            "reviewed_at": "2026-08-14T10:00:00+09:00",
        }
        manifest_path = f"manifests/{index:03d}.json"
        _write_json(root / manifest_path, manifest)
        runs.append(
            {
                "scenario_id": scenario_id,
                "profile_id": profile_id,
                "tenant_id": tenant_id,
                "operator_id": "operator-01",
                "manifest_path": manifest_path,
                "manifest_sha256": canonical_sha256(manifest),
                "fixture_manifest_path": fixture_path,
                "fixture_manifest_sha256": _raw_sha256(fixture_payload),
                "action_script_path": action_path,
                "action_script_sha256": action_sha256,
                "redaction_report_sha256": _raw_sha256(redaction_payload),
                "evidence_files": [
                    {
                        "path": result_path,
                        "sha256": _raw_sha256(result_payload),
                        "kind": "result",
                    },
                    {
                        "path": redaction_path,
                        "sha256": _raw_sha256(redaction_payload),
                        "kind": "redaction_report",
                    },
                ],
            }
        )

    bundle = {
        "schema_version": 1,
        "contract": BUNDLE_CONTRACT,
        "status": "reviewed_complete",
        "matrix_sha256": matrix_sha256,
        "authorization": {
            "authorization_id": "authorization-01",
            "approved": True,
            "operator_id": "operator-01",
            "reviewer_id": "reviewer-01",
            "approved_at": "2026-08-14T08:00:00+09:00",
            "valid_from": "2026-08-14T09:00:00+09:00",
            "valid_until": "2026-08-14T18:00:00+09:00",
            "approved_tenants": [
                {
                    "tenant_id": "tenant-business",
                    "plan": "business",
                    "synthetic": True,
                    "approved": True,
                },
                {
                    "tenant_id": "tenant-enterprise",
                    "plan": "enterprise",
                    "synthetic": True,
                    "approved": True,
                },
            ],
            "normal_public_ui_only": True,
            "synthetic_fixture_only": True,
            "external_evidence_root_approved": True,
            "cleanup_approved": True,
            "public_link_scenario_approved": True,
            "plan_transition_scenario_approved": True,
            "production_or_personal_workspace_allowed": False,
            "api_private_endpoint_network_capture_allowed": False,
            "source_catalog_or_runtime_mutation_authorized": False,
        },
        "runs": runs,
        "unknown_exceptions": [],
        "reference_conflicts": [],
        "decision": {
            "semantic_decision_sha256": "",
            "semantic_decision_path": "decisions/archive.json",
            "reviewer_id": "reviewer-01",
            "reviewed_at": "2026-08-14T11:00:00+09:00",
            "all_required_runs_reviewed": True,
            "reference_conflicts_resolved": True,
            "unobserved_dimensions_remaining": 0,
            "source_schema_semantics_frozen": True,
            "implementation_authorized": False,
            "next_gate": "NP2C-5b-exact-v4-to-v5-archive-source-schema-and-route-unregistered-adapter",
        },
        "claims": {
            "live_characterized": True,
            "np2c5a_complete": True,
            "differential_passed": False,
            "source_v5_implemented": False,
            "workspace_1318_implemented": False,
            "runtime_archive_enabled": False,
            "np2c_complete": False,
            "overall_parity_complete": False,
        },
    }
    semantic_decision = {
        "schema_version": 1,
        "contract": "np2c5a_archive_semantic_decision_v1",
        "reviewer_id": "reviewer-01",
        "reviewed_at": bundle["decision"]["reviewed_at"],
        "scenario_decisions": [
            {
                "scenario_id": scenario["id"],
                "decision": {"contract_sha256": "sha256:" + f"{index:064x}"[-64:]},
            }
            for index, scenario in enumerate(matrix["scenarios"], start=1)
        ],
        "implementation_authorized": False,
        "next_gate": bundle["decision"]["next_gate"],
    }
    decision_path = root / bundle["decision"]["semantic_decision_path"]
    _write_json(decision_path, semantic_decision)
    bundle["decision"]["semantic_decision_sha256"] = _raw_sha256(
        decision_path.read_bytes()
    )
    bundle_path = root / "bundle.json"
    _write_json(bundle_path, bundle)
    return root, bundle_path, matrix, bundle, matrix_sha256


@pytest.mark.unit
def test_complete_external_archive_bundle_is_accepted_without_implementation_authority(
    tmp_path: Path,
) -> None:
    root, bundle_path, matrix, bundle, _ = _fixture(tmp_path)
    approved_authorization_sha256 = canonical_sha256(bundle["authorization"])

    report = verify_bundle(
        bundle_path,
        root,
        approved_authorization_sha256=approved_authorization_sha256,
    )

    assert report == {
        "valid": True,
        "accepted": True,
        "error_count": 0,
        "errors": [],
        "run_count": len(_expected_pairs(matrix)),
        "expected_run_count": len(_expected_pairs(matrix)),
        "reference_conflict_count": 0,
        "approval_digest_matched": True,
        "implementation_authorized": False,
    }
    assert len(bundle["runs"]) > 16

    wrong_digest = verify_bundle(
        bundle_path,
        root,
        approved_authorization_sha256="sha256:" + "0" * 64,
    )
    assert wrong_digest["valid"] is False
    assert wrong_digest["accepted"] is False
    assert wrong_digest["approval_digest_matched"] is False


@pytest.mark.unit
def test_bundle_validator_rejects_incomplete_unsafe_or_premature_claims(
    tmp_path: Path,
) -> None:
    root, _, matrix, bundle, matrix_sha256 = _fixture(tmp_path)
    approved_authorization_sha256 = canonical_sha256(bundle["authorization"])
    cases: list[tuple[str, dict]] = []

    changed = deepcopy(bundle)
    changed["runs"].pop()
    cases.append(("missing_run", changed))

    changed = deepcopy(bundle)
    changed["runs"][0], changed["runs"][1] = changed["runs"][1], changed["runs"][0]
    cases.append(("reordered_run", changed))

    changed = deepcopy(bundle)
    changed["unknown_exceptions"] = [
        {
            "scenario_id": "NP2-ARCHIVE-PLATFORM-001",
            "profile_id": "business_member_ios_ko",
            "reason_code": "platform_unavailable",
        }
    ]
    cases.append(("unknown_exception", changed))

    changed = deepcopy(bundle)
    changed["reference_conflicts"] = [
        {
            "id": "RC-NP2-ARCHIVE-PUBLIC-001",
            "scenario_id": "NP2-ARCHIVE-PUBLIC-001",
        }
    ]
    cases.append(("unresolved_conflict", changed))

    changed = deepcopy(bundle)
    changed["authorization"]["operator_id"] = "reviewer-01"
    cases.append(("same_reviewer", changed))

    changed = deepcopy(bundle)
    changed["authorization"]["public_link_scenario_approved"] = False
    cases.append(("public_not_approved", changed))

    changed = deepcopy(bundle)
    changed["authorization"]["plan_transition_scenario_approved"] = False
    cases.append(("plan_transition_not_approved", changed))

    changed = deepcopy(bundle)
    changed["authorization"]["api_private_endpoint_network_capture_allowed"] = True
    cases.append(("private_capture", changed))

    changed = deepcopy(bundle)
    changed["authorization"]["source_catalog_or_runtime_mutation_authorized"] = True
    cases.append(("implementation_authority", changed))

    changed = deepcopy(bundle)
    changed["authorization"]["valid_until"] = "2026-08-14T09:30:00+09:00"
    cases.append(("review_outside_window", changed))

    changed = deepcopy(bundle)
    changed["matrix_sha256"] = "sha256:" + "0" * 64
    cases.append(("stale_matrix", changed))

    changed = deepcopy(bundle)
    changed["runs"][0]["tenant_id"] = "tenant-unapproved"
    cases.append(("unapproved_tenant", changed))

    changed = deepcopy(bundle)
    changed["runs"][0]["manifest_sha256"] = "sha256:" + "0" * 64
    cases.append(("manifest_hash", changed))

    changed = deepcopy(bundle)
    changed["runs"][0]["evidence_files"][0]["sha256"] = "sha256:" + "0" * 64
    cases.append(("artifact_hash", changed))

    changed = deepcopy(bundle)
    changed["runs"][0]["redaction_report_sha256"] = "sha256:" + "0" * 64
    cases.append(("redaction_hash", changed))

    changed = deepcopy(bundle)
    changed["runs"][0]["fixture_manifest_sha256"] = "sha256:" + "0" * 64
    cases.append(("fixture_hash", changed))

    changed = deepcopy(bundle)
    changed["runs"][0]["action_script_sha256"] = "sha256:" + "0" * 64
    cases.append(("action_hash", changed))

    changed = deepcopy(bundle)
    changed["runs"][1]["evidence_files"] = deepcopy(
        changed["runs"][0]["evidence_files"]
    )
    cases.append(("cross_run_artifact_reuse", changed))

    changed = deepcopy(bundle)
    changed["runs"][0]["manifest_path"] = "../outside.json"
    cases.append(("path_escape", changed))

    changed = deepcopy(bundle)
    changed["decision"]["implementation_authorized"] = True
    cases.append(("decision_authority", changed))

    changed = deepcopy(bundle)
    changed["decision"]["unobserved_dimensions_remaining"] = 1
    cases.append(("unobserved_dimension", changed))

    changed = deepcopy(bundle)
    changed["decision"]["reviewed_at"] = "2026-08-14T11:01:00+09:00"
    cases.append(("decision_digest_drift", changed))

    changed = deepcopy(bundle)
    changed["claims"]["source_v5_implemented"] = True
    cases.append(("source_v5_claim", changed))

    changed = deepcopy(bundle)
    changed["authorization"]["operator_id"] = "operator@example.test"
    cases.append(("sensitive_operator", changed))

    for name, candidate in cases:
        report = validate_bundle(
            candidate,
            evidence_root=root,
            matrix=matrix,
            matrix_sha256=matrix_sha256,
            approved_authorization_sha256=approved_authorization_sha256,
        )
        assert report["valid"] is False, name
        assert report["implementation_authorized"] is False


@pytest.mark.unit
def test_manifest_policy_and_evidence_content_are_revalidated(
    tmp_path: Path,
) -> None:
    root, _, matrix, bundle, matrix_sha256 = _fixture(tmp_path)
    approved_authorization_sha256 = canonical_sha256(bundle["authorization"])
    run = bundle["runs"][0]
    manifest_path = root / run["manifest_path"]
    original_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    invalid_manifest = deepcopy(original_manifest)
    invalid_manifest["observation_state"] = "unknown"
    invalid_manifest["promotion_status"] = None
    _write_json(manifest_path, invalid_manifest)
    changed = deepcopy(bundle)
    changed["runs"][0]["manifest_sha256"] = canonical_sha256(invalid_manifest)
    assert validate_bundle(
        changed,
        evidence_root=root,
        matrix=matrix,
        matrix_sha256=matrix_sha256,
        approved_authorization_sha256=approved_authorization_sha256,
    )["valid"] is False

    _write_json(manifest_path, original_manifest)
    artifact_path = root / run["evidence_files"][0]["path"]
    original_artifact = artifact_path.read_bytes()
    artifact_path.write_text('{"tampered":true}', encoding="utf-8")
    assert validate_bundle(
        bundle,
        evidence_root=root,
        matrix=matrix,
        matrix_sha256=matrix_sha256,
        approved_authorization_sha256=approved_authorization_sha256,
    )["valid"] is False
    artifact_path.write_bytes(original_artifact)

    fixture_path = root / run["fixture_manifest_path"]
    sensitive_payload = b'{"email":"person@example.test"}'
    fixture_path.write_bytes(sensitive_payload)
    changed = deepcopy(bundle)
    changed["runs"][0]["fixture_manifest_sha256"] = _raw_sha256(
        sensitive_payload
    )
    assert validate_bundle(
        changed,
        evidence_root=root,
        matrix=matrix,
        matrix_sha256=matrix_sha256,
        approved_authorization_sha256=approved_authorization_sha256,
    )["valid"] is False


@pytest.mark.unit
def test_verifier_rejects_repository_root_and_malformed_bundle(
    tmp_path: Path,
) -> None:
    root, bundle_path, _, bundle, _ = _fixture(tmp_path)
    approved_authorization_sha256 = canonical_sha256(bundle["authorization"])
    inside_report = verify_bundle(
        bundle_path,
        REPOSITORY_ROOT,
        approved_authorization_sha256=approved_authorization_sha256,
    )
    assert inside_report["valid"] is False
    assert inside_report["implementation_authorized"] is False

    duplicate = root / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":1,"schema_version":1}', encoding="utf-8"
    )
    first = verify_bundle(
        duplicate,
        root,
        approved_authorization_sha256=approved_authorization_sha256,
    )
    second = verify_bundle(
        duplicate,
        root,
        approved_authorization_sha256=approved_authorization_sha256,
    )
    assert first == second
    assert first["valid"] is False
    assert first["errors"] == ["evidence bundle could not be verified"]


@pytest.mark.unit
def test_reviewed_observations_redaction_and_semantic_decision_are_exact(
    tmp_path: Path,
) -> None:
    root, _, matrix, bundle, matrix_sha256 = _fixture(tmp_path)
    approved_authorization_sha256 = canonical_sha256(bundle["authorization"])
    run = bundle["runs"][0]
    result_path = root / run["evidence_files"][0]["path"]
    redaction_path = root / run["evidence_files"][1]["path"]
    manifest_path = root / run["manifest_path"]
    original_result = result_path.read_bytes()
    original_redaction = redaction_path.read_bytes()
    original_manifest = manifest_path.read_bytes()

    incomplete_result = json.loads(original_result)
    incomplete_result["observations"].pop()
    _write_json(result_path, incomplete_result)
    result_sha256 = _raw_sha256(result_path.read_bytes())
    changed = deepcopy(bundle)
    changed_run = changed["runs"][0]
    changed_run["evidence_files"][0]["sha256"] = result_sha256
    manifest = json.loads(original_manifest)
    manifest["result_sha256"] = result_sha256
    _write_json(manifest_path, manifest)
    changed_run["manifest_sha256"] = canonical_sha256(manifest)
    redaction = json.loads(original_redaction)
    redaction["artifact_sha256s"] = [result_sha256]
    _write_json(redaction_path, redaction)
    redaction_sha256 = _raw_sha256(redaction_path.read_bytes())
    changed_run["evidence_files"][1]["sha256"] = redaction_sha256
    changed_run["redaction_report_sha256"] = redaction_sha256
    assert validate_bundle(
        changed,
        evidence_root=root,
        matrix=matrix,
        matrix_sha256=matrix_sha256,
        approved_authorization_sha256=approved_authorization_sha256,
    )["valid"] is False

    result_path.write_bytes(original_result)
    redaction_path.write_bytes(original_redaction)
    manifest_path.write_bytes(original_manifest)
    changed = deepcopy(bundle)
    redaction = json.loads(original_redaction)
    redaction["artifact_sha256s"] = []
    _write_json(redaction_path, redaction)
    redaction_sha256 = _raw_sha256(redaction_path.read_bytes())
    changed["runs"][0]["evidence_files"][1]["sha256"] = redaction_sha256
    changed["runs"][0]["redaction_report_sha256"] = redaction_sha256
    assert validate_bundle(
        changed,
        evidence_root=root,
        matrix=matrix,
        matrix_sha256=matrix_sha256,
        approved_authorization_sha256=approved_authorization_sha256,
    )["valid"] is False

    redaction_path.write_bytes(original_redaction)
    decision_path = root / bundle["decision"]["semantic_decision_path"]
    decision_record = json.loads(decision_path.read_text(encoding="utf-8"))
    decision_record["scenario_decisions"].pop()
    _write_json(decision_path, decision_record)
    changed = deepcopy(bundle)
    changed["decision"]["semantic_decision_sha256"] = _raw_sha256(
        decision_path.read_bytes()
    )
    assert validate_bundle(
        changed,
        evidence_root=root,
        matrix=matrix,
        matrix_sha256=matrix_sha256,
        approved_authorization_sha256=approved_authorization_sha256,
    )["valid"] is False


@pytest.mark.unit
def test_bundle_validation_does_not_scan_evidence_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, bundle_path, matrix, bundle, _ = _fixture(tmp_path)
    approved_authorization_sha256 = canonical_sha256(bundle["authorization"])

    def forbidden_scan(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("directory scan is forbidden")

    monkeypatch.setattr(Path, "iterdir", forbidden_scan)
    monkeypatch.setattr(Path, "glob", forbidden_scan)
    monkeypatch.setattr(Path, "rglob", forbidden_scan)

    report = verify_bundle(
        bundle_path,
        root,
        approved_authorization_sha256=approved_authorization_sha256,
    )
    assert report["valid"] is True
    assert report["run_count"] == len(_expected_pairs(matrix))


@pytest.mark.unit
def test_verifier_import_isolated_from_app_and_has_no_filesystem_side_effect(
    tmp_path: Path,
) -> None:
    script = """
import json
from pathlib import Path
import sys
import code_assist_v1.tools.verify_notion_np2c_archive_evidence_bundle
forbidden = sorted(
    name for name in sys.modules
    if name in {
        'code_assist_v1.app_code',
        'code_assist_v1.app_notion',
        'code_assist_v1.auth',
        'code_assist_v1.config',
        'code_assist_v1.notion_store',
        'code_assist_v1.routes_notion',
        'code_assist_v1.routes_notion_bridge',
        'code_assist_v1.routes_notion_workspace',
    }
)
print(json.dumps({'forbidden': forbidden, 'entries': sorted(p.name for p in Path.cwd().iterdir())}))
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"forbidden": [], "entries": []}

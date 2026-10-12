from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from code_assist_v1.tools.verify_notion_parity_catalog import (
    DEFAULT_CATALOG_DIR,
    DEFAULT_MANIFEST,
    DEFAULT_SCHEMA,
    TOP_KEYS,
    load_json,
    validate_catalog_data,
    verify_catalog,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _schema() -> dict[str, Any]:
    value = load_json(DEFAULT_SCHEMA)
    assert isinstance(value, dict)
    return value


def _manifest() -> dict[str, Any]:
    value = load_json(DEFAULT_MANIFEST)
    assert isinstance(value, dict)
    return value


def _catalog() -> dict[str, Any]:
    profile = {
        "id": "desktop_web_free_owner",
        "plan": "free",
        "role": "workspace_owner",
        "platform": "desktop_web",
        "locale": "ko-KR",
    }
    evidence = {
        "observation_state": "reference-only",
        "promotion_status": None,
        "live_characterization": "unknown",
        "differential": "unknown",
        "unverified_reason": "Only an official reference is available.",
        "records": [
            {
                "id": "EV-NP2-SIDEBAR-COLLAPSE-001-REFERENCE",
                "kind": "official_reference",
                "observation_state": "reference-only",
                "promotion_status": None,
                "source_id": "SRC-SIDEBAR",
                "description": "Dated official sidebar reference.",
            }
        ],
    }
    return {
        "schema_version": 1,
        "catalog_id": "workspace-editor-core",
        "title": "NP-2 and NP-3 reference catalog",
        "baseline": {
            "reference_date": "2026-08-13",
            "timezone": "Asia/Seoul",
            "source_priority": [
                "official_help",
                "official_developer",
                "official_release",
                "approved_live_characterization",
            ],
            "live_observation": {
                "availability": "unavailable",
                "observation_state": "unknown",
                "reason": "No approved live session is attached.",
            },
        },
        "scope": {
            "phases": ["NP-2", "NP-3"],
            "catalog_completeness": "partial",
            "phase_completion_claim": False,
            "description": "Partial NP-2 and NP-3 seed catalog.",
        },
        "claims": {
            "live_characterized": False,
            "differential_passed": False,
            "np2_gate_complete": False,
            "np3_gate_complete": False,
        },
        "profiles": [profile],
        "sources": [
            {
                "id": "SRC-SIDEBAR",
                "kind": "official_help",
                "title": "Navigate with the sidebar",
                "url": "https://www.notion.com/help/navigate-with-the-sidebar",
                "reference_date": "2026-08-13",
                "observation_state": "reference-only",
                "promotion_status": None,
                "live_observation_status": "unknown",
                "claim_summary": "Official documentation only.",
            }
        ],
        "reference_conflicts": [],
        "second_brain_hard_gates": [
            {
                "id": "SB-AGENT-LINK",
                "source_manifest_gate_id": "SB-AGENT-LINK",
                "title": "Agent link invariant",
                "blocking": True,
                "applies_to_phases": ["NP-2", "NP-3"],
                "assertions": ["Existing agent links remain unchanged."],
                "evidence": {
                    "observation_state": "unknown",
                    "promotion_status": None,
                    "live_characterization": "unknown",
                    "differential": "unknown",
                    "unverified_reason": "No regression run is attached.",
                    "records": [],
                },
            }
        ],
        "scenarios": [
            {
                "id": "NP2-SIDEBAR-COLLAPSE-001",
                "feature_id": "sidebar.workspace_navigation",
                "phase": "NP-2",
                "title": "Collapse a sidebar tree branch",
                "profile_ids": [profile["id"]],
                "source_ids": ["SRC-SIDEBAR"],
                "reference_date": "2026-08-13",
                "plan": [profile["plan"]],
                "role": [profile["role"]],
                "platform": [profile["platform"]],
                "locale": [profile["locale"]],
                "preconditions": [
                    {"id": "P1", "statement": "A branch contains one child page."}
                ],
                "actions": [
                    {
                        "step": 1,
                        "actor": "workspace_owner",
                        "operation": "collapse",
                        "target": "sidebar branch",
                        "input": None,
                    }
                ],
                "assertions": [
                    {
                        "id": "A1",
                        "channel": "state",
                        "comparison": "reference_to_characterize",
                        "expected": "The child page is no longer visible.",
                    }
                ],
                "reference_conflict_ids": [],
                "second_brain_hard_gate_ids": ["SB-AGENT-LINK"],
                "evidence": evidence,
                "notes": "No live behavior is claimed.",
            }
        ],
    }


def _validate(catalog: dict[str, Any], *, project_root: Path = PROJECT_ROOT):
    return validate_catalog_data(
        {"fixture.json": catalog},
        schema=_schema(),
        manifest=_manifest(),
        project_root=project_root,
    )


def _codes(report: dict[str, Any]) -> set[str]:
    return {str(item["code"]) for item in report["errors"]}


@pytest.mark.unit
def test_np0_valid_reference_only_catalog_is_not_a_live_claim() -> None:
    report, exit_code = _validate(_catalog())

    assert exit_code == 0, report
    assert report["status"] == "pass"
    assert report["summary"]["scenario_count"] == 1
    assert report["summary"]["observation_state_counts"] == {
        "reference-only": 1
    }


@pytest.mark.unit
def test_np0_schema_and_semantics_reject_unknown_and_missing_fields() -> None:
    catalog = _catalog()
    scenario = catalog["scenarios"][0]
    scenario["unexpected"] = True
    del scenario["source_ids"]

    report, exit_code = _validate(catalog)

    assert exit_code == 2
    assert {"UNKNOWN_FIELD", "REQUIRED_FIELD_MISSING"}.issubset(_codes(report))


@pytest.mark.unit
def test_np0_phase_id_profile_source_and_required_dimensions_fail_closed() -> None:
    catalog = _catalog()
    scenario = catalog["scenarios"][0]
    scenario["id"] = "NP3-SIDEBAR-COLLAPSE-001"
    scenario["phase"] = "NP-4"
    scenario["profile_ids"] = ["missing_profile"]
    scenario["source_ids"] = ["missing_source"]
    scenario["plan"] = []
    scenario["role"] = []
    scenario["platform"] = []
    scenario["locale"] = []

    report, exit_code = _validate(catalog)

    assert exit_code == 2
    assert {
        "PHASE_ID_MISMATCH",
        "PHASE_INVALID",
        "PROFILE_REFERENCE_UNKNOWN",
        "SOURCE_REFERENCE_UNKNOWN",
        "ARRAY_INVALID",
    }.issubset(_codes(report))


@pytest.mark.unit
def test_np0_invalid_status_progression_and_reference_only_live_claim_are_rejected() -> None:
    catalog = _catalog()
    scenario = catalog["scenarios"][0]
    scenario["evidence"].update(
        {
            "promotion_status": "live_characterized",
            "live_characterization": "captured",
            "differential": "passed",
        }
    )
    scenario["evidence"]["records"].append(
        {
            "id": "EV-NP2-SIDEBAR-COLLAPSE-001-LIVE",
            "kind": "live_capture",
            "observation_state": "live-characterized",
            "promotion_status": "live_characterized",
            "path": "../escape.json",
            "sha256": "not-a-hash",
            "description": "Invalid synthetic live record.",
        }
    )
    catalog["claims"]["live_characterized"] = True

    report, exit_code = _validate(catalog)

    assert exit_code == 2
    assert {
        "STATUS_PROGRESSION_INVALID",
        "LIVE_CLAIM_FORBIDDEN",
        "EVIDENCE_PATH_ESCAPE",
        "EVIDENCE_HASH_INVALID",
        "LIVE_CLAIM_UNSUPPORTED",
    }.issubset(_codes(report))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "promotion", "live", "differential", "missing_code"),
    [
        (
            "live-characterized",
            "live_characterized",
            "captured",
            "unknown",
            "LIVE_EVIDENCE_MISSING",
        ),
        (
            "differential-passed",
            "differential_passed",
            "captured",
            "passed",
            "DIFFERENTIAL_EVIDENCE_MISSING",
        ),
    ],
)
def test_np0_live_and_differential_states_require_hashed_artifact_records(
    state: str,
    promotion: str,
    live: str,
    differential: str,
    missing_code: str,
) -> None:
    catalog = _catalog()
    scenario = catalog["scenarios"][0]
    scenario["evidence"].update(
        {
            "observation_state": state,
            "promotion_status": promotion,
            "live_characterization": live,
            "differential": differential,
        }
    )

    report, exit_code = _validate(catalog)

    assert exit_code == 2
    assert missing_code in _codes(report)


@pytest.mark.unit
def test_np0_duplicate_json_keys_and_errors_are_deterministic(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "docs" / "notion_parity" / "catalog"
    catalog_dir.mkdir(parents=True)
    (catalog_dir / "schema.json").write_text(
        json.dumps(_schema(), sort_keys=True), encoding="utf-8"
    )
    (catalog_dir / "broken.json").write_text(
        '{"schema_version":1,"schema_version":1}', encoding="utf-8"
    )
    manifest_path = tmp_path / "docs" / "notion_parity_manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")

    first, first_exit = verify_catalog(
        catalog_dir, manifest_path=manifest_path, project_root=tmp_path
    )
    second, second_exit = verify_catalog(
        catalog_dir, manifest_path=manifest_path, project_root=tmp_path
    )

    assert first_exit == second_exit == 2
    assert first == second
    assert _codes(first) == {"CATALOG_EMPTY", "DUPLICATE_JSON_KEY"}
    assert first["errors"] == sorted(
        first["errors"],
        key=lambda item: (item["code"], item["subject"], item["message"]),
    )


@pytest.mark.unit
def test_np0_catalog_verifier_dependency_boundary_is_read_only() -> None:
    source = Path(
        sys.modules[
            "code_assist_v1.tools.verify_notion_parity_catalog"
        ].__file__
    ).read_text(encoding="utf-8")

    assert "sqlite3" not in source
    assert "requests" not in source
    assert "routes_notion" not in source
    assert "notion_store" not in source
    assert "user_data" not in source
    assert "knowledge_core" not in source


@pytest.mark.unit
def test_np0_checked_in_catalog_and_cli_pass_when_catalog_is_present() -> None:
    assert DEFAULT_SCHEMA.is_file(), "NP-0 catalog schema must be checked in"
    assert any(
        path.name != "schema.json" for path in DEFAULT_CATALOG_DIR.glob("*.json")
    ), "NP-0 catalog document must be checked in"

    report, exit_code = verify_catalog(DEFAULT_CATALOG_DIR)
    assert exit_code == 0, report
    assert report["status"] == "pass"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.verify_notion_parity_catalog",
            "--compact",
        ],
        cwd=PROJECT_ROOT.parent,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["status"] == "pass"

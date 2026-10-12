from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256, load_json
from code_assist_v1.tools.notion_parity.ui_actions import (
    action_log_sha256,
    build_action_log,
    validate_action_sequence,
)
from code_assist_v1.tools.notion_parity.ui_capture import (
    capture_sha256,
    compare_precalibration_captures,
    validate_capture,
)
from code_assist_v1.tools.notion_parity.ui_evidence import (
    validate_evidence_manifest,
    verify_evidence_file,
)
from code_assist_v1.tools.notion_parity.ui_redaction import (
    RedactionPolicy,
    validate_redacted_payload,
)
from code_assist_v1.tools.verify_notion_core_free_ui_plan import DEFAULT_PLAN


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHA = "sha256:" + ("1" * 64)
SHA2 = "sha256:" + ("2" * 64)
SHA3 = "sha256:" + ("3" * 64)


def _scenario(scenario_id: str) -> dict[str, object]:
    plan = load_json(DEFAULT_PLAN)
    return next(
        item for item in plan["required_scenarios"] if item["scenario_id"] == scenario_id
    )


def _capture(side: str = "local") -> dict[str, object]:
    return {
        "capture_schema_version": 1,
        "scenario_id": "UI-SHELL-002",
        "side": side,
        "profile": "free-owner-chrome-desktop-ko-KR",
        "viewport": {"width": 1440, "height": 900, "scale": 1},
        "state": {"visible": True, "workspace_alias": "REF-FREE-A"},
        "keyboard": {"result": "success"},
        "focus": {"name": "SIDEBAR-RESTORE", "state": "focused"},
        "a11y": {"role": "BUTTON", "name": "SIDEBAR-RESTORE", "state": "enabled"},
        "semantic_visual": {"region": "SIDEBAR", "state": "visible"},
        "error_recovery": {"result": "unchanged"},
        "geometry": {"SIDEBAR": {"x": 0, "y": 0, "width": 240, "height": 900}},
        "latency_ms": 12.5,
        "action_log_sha256": SHA,
        "screenshot_sha256": None,
        "source_build_sha256": SHA2,
        "redaction": {"status": "pass", "rule_set": "core-free-ui-v1"},
    }


def _artifact(kind: str, name: str, digest: str = SHA) -> dict[str, str]:
    return {"kind": kind, "path": f"evidence/ui/{name}", "sha256": digest}


def _evidence(state: str = "differential-passed") -> dict[str, object]:
    live = state in {"live-characterized", "differential-failed", "differential-passed"}
    differential = None
    if state in {"differential-failed", "differential-passed"}:
        differential = {
            "kind": "differential",
            "path": "evidence/ui/differential.json",
            "sha256": SHA3,
            "status": "pass" if state == "differential-passed" else "fail",
            "reference_sha256": SHA,
            "candidate_sha256": SHA2,
            "policy_sha256": SHA3,
        }
    promotion = {
        "unknown": None,
        "contract-tested": "contract_tested",
        "live-characterized": "live_characterized",
        "differential-failed": "live_characterized",
        "differential-passed": "differential_passed",
    }[state]
    return {
        "schema_version": 1,
        "contract": "notion_core_free_ui_evidence_v1",
        "scenario_id": "UI-SHELL-002",
        "run_id": "ui1-test-run",
        "observation_state": state,
        "promotion_status": promotion,
        "profile": "free-owner-chrome-desktop-ko-KR",
        "source_build_sha256": SHA2,
        "action_log": _artifact("action_log", "actions.json"),
        "captures": {
            "reference": _artifact("capture", "reference.json") if live else None,
            "local": _artifact("capture", "local.json", SHA2) if live else None,
        },
        "differential": differential,
        "screenshots": [],
        "reviewer": "REVIEWER-XHIGH" if live else None,
        "reviewed_at": "2026-08-14T22:00:00+09:00" if live else None,
        "redaction": {"status": "pass", "rule_set": "core-free-ui-v1", "allowlist_sha256": SHA},
        "raw_artifact_deletion": {"status": "not_applicable", "record_sha256": None},
    }


def _codes(errors: list[dict[str, str]]) -> set[str]:
    return {item["code"] for item in errors}


@pytest.mark.unit
def test_checked_in_ui1_actions_build_a_deterministic_log() -> None:
    scenario = _scenario("UI-SHELL-002")
    actions = copy.deepcopy(scenario["actions"])

    assert validate_action_sequence(actions, scenario_id="UI-SHELL-002") == []
    log = build_action_log(
        scenario_id="UI-SHELL-002",
        side="local",
        run_id="ui1-test-run",
        actions=actions,
    )
    assert log["profile_id"] == "core_free_owner_chrome_desktop_ko_kr"
    assert action_log_sha256(log) == canonical_sha256(log)


@pytest.mark.unit
def test_action_contract_rejects_extra_fields_forward_recovery_and_secrets() -> None:
    actions = copy.deepcopy(_scenario("UI-SHELL-002")["actions"])
    actions[0]["unexpected"] = True
    actions[0]["recovery_action_id"] = "UI-SHELL-002-A99"
    actions[0]["input_alias"] = "person@example.test"

    codes = _codes(validate_action_sequence(actions, scenario_id="UI-SHELL-002"))
    assert {"UNKNOWN_FIELD", "RECOVERY_FORWARD_REFERENCE", "ALIAS", "SENSITIVE_EMAIL"}.issubset(codes)


@pytest.mark.unit
def test_capture_contract_accepts_redacted_normalized_data() -> None:
    capture = _capture()
    assert validate_capture(capture) == []
    assert capture_sha256(capture) == canonical_sha256(capture)


@pytest.mark.unit
def test_capture_contract_rejects_extra_fields_nonfinite_geometry_and_personal_text() -> None:
    capture = _capture()
    capture["unexpected"] = True
    capture["latency_ms"] = math.nan
    capture["geometry"]["SIDEBAR"]["width"] = True
    capture["state"]["name"] = "Private personal page title"

    codes = _codes(validate_capture(capture))
    assert {"UNKNOWN_FIELD", "LATENCY", "NON_FINITE_NUMBER", "GEOMETRY_NUMBER", "VALUE_NOT_ALLOWLISTED"}.issubset(codes)


@pytest.mark.unit
def test_precalibration_comparison_is_exact_but_never_promotes() -> None:
    reference = _capture("reference")
    candidate = _capture("local")
    candidate["geometry"]["SIDEBAR"]["width"] = 480
    candidate["latency_ms"] = 9000

    report = compare_precalibration_captures(reference, candidate)
    assert report["status"] == "pass"
    assert report["promotion_allowed"] is False
    assert report["measurements"]["candidate_latency_ms"] == 9000

    candidate["focus"]["name"] = "PAGE-CANVAS"
    report = compare_precalibration_captures(reference, candidate)
    assert report["status"] == "fail"
    assert report["differences"] == [
        {"section": "focus", "path": "$.focus", "kind": "value_mismatch"}
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "expected_promotion"),
    [
        ("unknown", None),
        ("contract-tested", "contract_tested"),
        ("live-characterized", "live_characterized"),
        ("differential-failed", "live_characterized"),
        ("differential-passed", "differential_passed"),
    ],
)
def test_evidence_state_machine_accepts_only_exact_envelopes(
    state: str,
    expected_promotion: str | None,
) -> None:
    manifest = _evidence(state)
    assert manifest["promotion_status"] == expected_promotion
    assert validate_evidence_manifest(manifest) == []


@pytest.mark.unit
def test_evidence_cannot_promote_without_capture_review_and_passing_diff() -> None:
    manifest = _evidence("differential-passed")
    manifest["captures"]["reference"] = None
    manifest["reviewer"] = None
    manifest["differential"]["status"] = "fail"

    codes = _codes(validate_evidence_manifest(manifest))
    assert {"LIVE_CAPTURES", "REVIEWER", "DIFFERENTIAL_REQUIRED"}.issubset(codes)


@pytest.mark.unit
def test_evidence_paths_and_strings_fail_closed() -> None:
    manifest = _evidence("differential-passed")
    manifest["action_log"]["path"] = "C:\\Users\\person\\actions.json"

    codes = _codes(validate_evidence_manifest(manifest))
    assert {"ARTIFACT_PATH", "SENSITIVE_WINDOWS_PATH"}.issubset(codes)


@pytest.mark.unit
def test_evidence_loader_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    report, exit_code = verify_evidence_file(path)
    assert exit_code == 1
    assert "MANIFEST_READ_FAILED" in _codes(report["errors"])


@pytest.mark.unit
def test_allowlist_redactor_rejects_unknown_text_even_without_secret_pattern() -> None:
    policy = RedactionPolicy(
        allowed_keys=frozenset({"state", "name"}),
        allowed_strings=frozenset({"합성 문장"}),
    )
    assert validate_redacted_payload(
        {"state": {"name": "합성 문장"}},
        policy=policy,
    ) == []
    errors = validate_redacted_payload(
        {"state": {"name": "개인 페이지 제목"}},
        policy=policy,
    )
    assert _codes(errors) == {"VALUE_NOT_ALLOWLISTED"}


@pytest.mark.unit
def test_normalized_restore_and_unfocused_states_are_redaction_safe() -> None:
    policy = RedactionPolicy(
        allowed_keys=frozenset({"state", "name"}),
    )

    assert validate_redacted_payload(
        {"state": {"name": "restored", "state": "not_focused"}},
        policy=policy,
    ) == []


@pytest.mark.unit
def test_ui_harness_modules_import_without_app_or_filesystem_side_effects(tmp_path: Path) -> None:
    script = """
import json
from pathlib import Path
import sys

root = Path.cwd()
before = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
import code_assist_v1.tools.notion_parity.ui_actions  # noqa: F401
import code_assist_v1.tools.notion_parity.ui_capture  # noqa: F401
import code_assist_v1.tools.notion_parity.ui_evidence  # noqa: F401
import code_assist_v1.tools.notion_parity.ui_redaction  # noqa: F401
after = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
forbidden = sorted(
    name for name in sys.modules
    if name in {
        'code_assist_v1.app_code',
        'code_assist_v1.auth',
        'code_assist_v1.config',
        'code_assist_v1.data_paths',
        'code_assist_v1.knowledge_core',
    }
    or name.startswith('code_assist_v1.routes_')
    or name.startswith('code_assist_v1.notion_workspace')
)
print(json.dumps({'forbidden': forbidden, 'changed': before != after}, sort_keys=True))
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"changed": False, "forbidden": []}

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256
from code_assist_v1.tools.notion_parity.evidence import load_json
from code_assist_v1.tools.notion_parity.ui_actions import action_log_sha256, build_action_log
from code_assist_v1.tools.notion_parity.ui_reference_guard import (
    ReferenceC0Observation,
    evaluate_reference_c0,
)
from code_assist_v1.tools.notion_parity.ui_reference_intake import (
    RECEIPT_CONTRACT,
    SUBMISSION_CONTRACT,
    ReferenceIntakeError,
    accept_manual_reference_submission,
    build_manual_reference_submission_draft,
    build_manual_reference_worksheet,
    validate_manual_reference_submission,
)
from code_assist_v1.tools.verify_notion_core_free_ui_plan import DEFAULT_PLAN


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "ui1-manual-reference"
SCENARIO_ID = "UI-SHELL-002"
SHA = "sha256:" + ("1" * 64)


def _decision(**overrides: object):
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "browser_connection_visible": True,
        "chrome_session_confirmed": True,
        "signed_in_visible": True,
        "workspace_switcher_opened": True,
        "ref_free_a_confirmed": True,
        "ref_free_b_confirmed": True,
        "other_workspace_labels_visible": True,
    }
    values.update(overrides)
    return evaluate_reference_c0(ReferenceC0Observation(**values))


def _submission() -> dict[str, object]:
    worksheet = build_manual_reference_worksheet(
        scenario_id=SCENARIO_ID,
        run_id=RUN_ID,
    )
    action_log = build_action_log(
        scenario_id=SCENARIO_ID,
        side="reference",
        run_id=RUN_ID,
        actions=worksheet["actions"],
        synthetic_allowlist=("sidebar",),
    )
    captures = []
    for index, action_id in enumerate(worksheet["capture_after_action_ids"]):
        captures.append(
            {
                "action_id": action_id,
                "capture": {
                    "capture_schema_version": 1,
                    "scenario_id": SCENARIO_ID,
                    "side": "reference",
                    "profile": "free-owner-chrome-desktop-ko-KR",
                    "viewport": {"width": 1440, "height": 900, "scale": 1},
                    "state": {
                        "visible": True,
                        "workspace_alias": "REF-FREE-A",
                        "state": "collapsed" if index == 0 else "expanded",
                    },
                    "keyboard": {"result": "success"},
                    "focus": {
                        "name": "SIDEBAR-RESTORE" if index == 0 else "SIDEBAR-COLLAPSE",
                        "state": "focused",
                    },
                    "a11y": {
                        "role": "BUTTON",
                        "name": "SIDEBAR-RESTORE" if index == 0 else "SIDEBAR-COLLAPSE",
                        "state": "enabled",
                    },
                    "semantic_visual": {"region": "SIDEBAR", "state": "visible"},
                    "error_recovery": {"result": "unchanged"},
                    "geometry": {"SIDEBAR": {"x": 0, "y": 0, "width": 240, "height": 900}},
                    "latency_ms": 12.5,
                    "action_log_sha256": action_log_sha256(action_log),
                    "screenshot_sha256": None,
                    "source_build_sha256": SHA,
                    "redaction": {"status": "pass", "rule_set": "core-free-ui-v1"},
                },
            }
        )
    return {
        "contract": SUBMISSION_CONTRACT,
        "worksheet_sha256": canonical_sha256(worksheet),
        "c0_decision": _decision().descriptor(),
        "action_log": action_log,
        "captures": captures,
        "operator_attestation": {
            "reviewer_alias": "USER-OPERATOR",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "synthetic_workspace_only": True,
            "synthetic_fixture_confirmed": True,
            "personal_workspace_opened": False,
            "personal_page_opened": False,
            "raw_dom_captured": False,
            "network_log_captured": False,
            "cookies_or_storage_captured": False,
            "external_side_effect_attempted": False,
            "public_posting_attempted": False,
            "capture_reviewed_redacted": True,
        },
    }


@pytest.mark.unit
def test_worksheet_is_exact_identifier_free_and_cli_repeatable(tmp_path: Path) -> None:
    first = build_manual_reference_worksheet(scenario_id=SCENARIO_ID, run_id=RUN_ID)
    second = build_manual_reference_worksheet(scenario_id=SCENARIO_ID, run_id=RUN_ID)
    assert first == second
    assert first["workspace_aliases"] == ["REF-FREE-A"]
    assert first["promotion_allowed"] is False
    assert first["capture_after_action_ids"] == [
        "UI-SHELL-002-A01",
        "UI-SHELL-002-A02",
    ]
    rendered = json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert "PERSONAL-FORBIDDEN" not in rendered
    assert "@" not in rendered

    output_path = tmp_path / "worksheet.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.notion_parity.ui_reference_intake",
            "worksheet",
            "--scenario",
            SCENARIO_ID,
            "--run-id",
            RUN_ID,
            "--output",
            str(output_path),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == first
    assert json.loads(output_path.read_text(encoding="utf-8")) == first

    repeated = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.notion_parity.ui_reference_intake",
            "worksheet",
            "--scenario",
            SCENARIO_ID,
            "--run-id",
            RUN_ID,
            "--output",
            str(output_path),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert repeated.returncode == 1
    assert json.loads(repeated.stdout)["error"] == "output must be a new file"

    forbidden_output = PROJECT_ROOT / ".notion-reference-output-forbidden-test.json"
    assert not forbidden_output.exists()
    outside_temp = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.notion_parity.ui_reference_intake",
            "worksheet",
            "--scenario",
            SCENARIO_ID,
            "--run-id",
            RUN_ID,
            "--output",
            str(forbidden_output),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert outside_temp.returncode == 1
    assert json.loads(outside_temp.stdout)["error"] == (
        "output must remain below the OS temporary root"
    )
    assert not forbidden_output.exists()


@pytest.mark.unit
def test_every_required_scenario_has_an_exact_manual_capture_denominator() -> None:
    plan = load_json(DEFAULT_PLAN)
    scenarios = plan["required_scenarios"]
    assert len(scenarios) == 25
    for scenario in scenarios:
        worksheet = build_manual_reference_worksheet(
            scenario_id=scenario["scenario_id"],
            run_id=RUN_ID,
        )
        expected = [
            action["action_id"]
            for action in scenario["actions"]
            if action["capture_after"]
        ]
        assert worksheet["capture_after_action_ids"] == expected
        assert len(expected) >= 1
        assert worksheet["promotion_allowed"] is False


@pytest.mark.unit
def test_valid_manual_reference_submission_returns_non_promoting_receipt(
    tmp_path: Path,
) -> None:
    submission = _submission()
    assert validate_manual_reference_submission(submission) == []
    receipt = accept_manual_reference_submission(submission)
    assert receipt["contract"] == RECEIPT_CONTRACT
    assert receipt["status"] == "accepted_reference_observation"
    assert receipt["scenario_id"] == SCENARIO_ID
    assert receipt["capture_count"] == 2
    assert receipt["screenshot_count"] == 0
    assert receipt["personal_workspace_accessed"] is False
    assert receipt["external_side_effect_attempted"] is False
    assert receipt["path_disclosed"] is False
    assert receipt["promotion_allowed"] is False

    submission_path = tmp_path / "submission.json"
    receipt_path = tmp_path / "receipt.json"
    submission_path.write_text(json.dumps(submission), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.notion_parity.ui_reference_intake",
            "verify",
            str(submission_path),
            "--synthetic-only-confirmed",
            "--output",
            str(receipt_path),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == receipt
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt


@pytest.mark.unit
def test_verify_cli_requires_synthetic_confirmation_before_file_read(tmp_path: Path) -> None:
    missing = tmp_path / "must-not-be-opened.json"
    output_path = tmp_path / "must-not-be-written.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.notion_parity.ui_reference_intake",
            "verify",
            str(missing),
            "--output",
            str(output_path),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 1
    result = json.loads(completed.stdout)
    assert result["status"] == "rejected"
    assert result["error"] == (
        "synthetic-only confirmation is required before reading a submission"
    )
    assert "No such file" not in completed.stdout
    assert not output_path.exists()


@pytest.mark.unit
def test_c0_cli_and_submission_draft_are_safe_and_deliberately_incomplete(
    tmp_path: Path,
) -> None:
    c0_path = tmp_path / "c0.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.notion_parity.ui_reference_intake",
            "c0",
            "--run-id",
            RUN_ID,
            "--browser-connection-visible",
            "--chrome-session-confirmed",
            "--signed-in-visible",
            "--workspace-switcher-opened",
            "--ref-free-a-confirmed",
            "--ref-free-b-confirmed",
            "--other-workspace-labels-visible",
            "--output",
            str(c0_path),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    decision = json.loads(completed.stdout)
    assert json.loads(c0_path.read_text(encoding="utf-8")) == decision
    assert decision["status"] == "pass"
    assert decision["capture_allowed"] is True
    draft = build_manual_reference_submission_draft(
        scenario_id=SCENARIO_ID,
        run_id=RUN_ID,
        c0_decision=decision,
    )
    assert len(draft["captures"]) == 2
    assert draft["operator_attestation"]["capture_reviewed_redacted"] is False
    assert draft["operator_attestation"]["recorded_at"] is None
    codes = {item["code"] for item in validate_manual_reference_submission(draft)}
    assert {"ATTESTATION_SAFETY", "RECORDED_AT", "CAPTURE_REDACTION"}.issubset(codes)

    draft_path = tmp_path / "draft.json"
    drafted = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.notion_parity.ui_reference_intake",
            "draft",
            str(c0_path),
            "--scenario",
            SCENARIO_ID,
            "--run-id",
            RUN_ID,
            "--synthetic-only-confirmed",
            "--output",
            str(draft_path),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert drafted.returncode == 0, drafted.stdout + drafted.stderr
    assert json.loads(drafted.stdout) == draft
    assert json.loads(draft_path.read_text(encoding="utf-8")) == draft

    failed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.notion_parity.ui_reference_intake",
            "c0",
            "--run-id",
            RUN_ID,
            "--browser-connection-visible",
            "--chrome-session-confirmed",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert failed.returncode == 1
    assert "signed_in_state_unconfirmed" in json.loads(failed.stdout)["failure_codes"]


@pytest.mark.unit
def test_failed_or_cross_run_c0_is_rejected() -> None:
    failed = _submission()
    failed["c0_decision"] = _decision(signed_in_visible=False).descriptor()
    codes = {item["code"] for item in validate_manual_reference_submission(failed)}
    assert "C0_NOT_PASSED" in codes

    cross_run = _submission()
    cross_run["c0_decision"] = evaluate_reference_c0(
        ReferenceC0Observation(
            run_id="ui1-other-reference",
            browser_connection_visible=True,
            chrome_session_confirmed=True,
            signed_in_visible=True,
            workspace_switcher_opened=True,
            ref_free_a_confirmed=True,
            ref_free_b_confirmed=True,
        )
    ).descriptor()
    codes = {item["code"] for item in validate_manual_reference_submission(cross_run)}
    assert "C0_RUN_MISMATCH" in codes


@pytest.mark.unit
def test_action_capture_and_worksheet_drift_are_rejected() -> None:
    changed = _submission()
    changed["action_log"]["actions"][0]["expected_focus_alias"] = "PAGE-CANVAS"  # type: ignore[index]
    codes = {item["code"] for item in validate_manual_reference_submission(changed)}
    assert {"ACTION_LOG_DRIFT", "CAPTURE_ACTION_HASH"}.issubset(codes)

    changed = _submission()
    changed["worksheet_sha256"] = "sha256:" + ("0" * 64)
    assert "WORKSHEET_HASH" in {
        item["code"] for item in validate_manual_reference_submission(changed)
    }

    changed = _submission()
    changed["captures"][0]["capture"]["state"]["workspace_alias"] = "person@example.test"  # type: ignore[index]
    codes = {item["code"] for item in validate_manual_reference_submission(changed)}
    assert "CAPTURE_SENSITIVE_EMAIL" in codes

    changed = _submission()
    changed["captures"].pop()  # type: ignore[union-attr]
    assert "CAPTURE_DENOMINATOR" in {
        item["code"] for item in validate_manual_reference_submission(changed)
    }

    changed = _submission()
    changed["captures"][1]["capture"]["source_build_sha256"] = "sha256:" + ("2" * 64)  # type: ignore[index]
    assert "CAPTURE_BUILD_DRIFT" in {
        item["code"] for item in validate_manual_reference_submission(changed)
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("synthetic_workspace_only", False),
        ("personal_workspace_opened", True),
        ("personal_page_opened", True),
        ("raw_dom_captured", True),
        ("network_log_captured", True),
        ("cookies_or_storage_captured", True),
        ("external_side_effect_attempted", True),
        ("public_posting_attempted", True),
        ("capture_reviewed_redacted", False),
    ],
)
def test_operator_safety_attestation_is_fail_closed(field: str, value: bool) -> None:
    submission = _submission()
    submission["operator_attestation"][field] = value  # type: ignore[index]
    codes = {item["code"] for item in validate_manual_reference_submission(submission)}
    assert "ATTESTATION_SAFETY" in codes
    with pytest.raises(ReferenceIntakeError):
        accept_manual_reference_submission(submission)


@pytest.mark.unit
def test_import_is_side_effect_free_and_does_not_load_application_modules(tmp_path: Path) -> None:
    script = (
        "import json,sys;"
        "from pathlib import Path;"
        "before=sorted(p.name for p in Path('.').iterdir());"
        "import code_assist_v1.tools.notion_parity.ui_reference_intake;"
        "after=sorted(p.name for p in Path('.').iterdir());"
        "blocked=['code_assist_v1.app_code','code_assist_v1.app_notion','code_assist_v1.auth','code_assist_v1.config','code_assist_v1.knowledge_core'];"
        "print(json.dumps({'same':before==after,'loaded':[x for x in blocked if x in sys.modules]}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={"PYTHONPATH": str(PROJECT_ROOT)},
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {"same": True, "loaded": []}

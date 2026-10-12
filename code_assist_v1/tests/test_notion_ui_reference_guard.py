from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256
from code_assist_v1.tools.notion_parity.ui_reference_guard import (
    ACTION_AUTH_CONTRACT,
    C0_CONTRACT,
    ReferenceC0Decision,
    ReferenceC0Observation,
    authorize_reference_action,
    evaluate_reference_c0,
    reference_c0_decision_from_descriptor,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _passing_observation(**overrides: object) -> ReferenceC0Observation:
    values: dict[str, object] = {
        "run_id": "ui1-reference-c0",
        "browser_connection_visible": True,
        "chrome_session_confirmed": True,
        "signed_in_visible": True,
        "workspace_switcher_opened": True,
        "ref_free_a_confirmed": True,
        "ref_free_b_confirmed": True,
        "other_workspace_labels_visible": True,
    }
    values.update(overrides)
    return ReferenceC0Observation(**values)


@pytest.mark.unit
def test_c0_pass_is_identifier_free_and_authorizes_only_synthetic_entry() -> None:
    decision = evaluate_reference_c0(_passing_observation())
    descriptor = decision.descriptor()

    assert descriptor == {
        "contract": C0_CONTRACT,
        "run_id": "ui1-reference-c0",
        "profile": "free-owner-chrome-desktop-ko-KR",
        "phase": "C0",
        "status": "pass",
        "failure_codes": [],
        "connection_verified": True,
        "workspace_boundary_verified": True,
        "read_only_verified": True,
        "capture_allowed": True,
        "synthetic_workspace_entry_allowed": True,
        "other_workspace_labels_visible": True,
        "personal_workspace_opened": False,
        "personal_page_opened": False,
        "artifact_created": False,
        "mutation_attempted": False,
        "external_side_effect_attempted": False,
        "raw_label_disclosed": False,
    }
    assert decision.sha256() == canonical_sha256(descriptor)
    assert "workspace name" not in json.dumps(descriptor, sort_keys=True)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"browser_connection_visible": False}, "connection_unavailable"),
        ({"chrome_session_confirmed": False}, "wrong_or_unconfirmed_browser_session"),
        ({"signed_in_visible": False}, "signed_in_state_unconfirmed"),
        ({"workspace_switcher_opened": False}, "workspace_switcher_unobserved"),
        ({"ref_free_a_confirmed": False}, "synthetic_workspace_boundary_unconfirmed"),
        ({"ref_free_b_confirmed": False}, "synthetic_workspace_boundary_unconfirmed"),
        ({"personal_workspace_opened": True}, "personal_workspace_opened"),
        ({"personal_page_opened": True}, "personal_page_opened"),
        ({"unknown_label_captured": True}, "unknown_label_captured"),
        ({"screenshot_created": True}, "c0_screenshot_created"),
        ({"raw_artifact_created": True}, "c0_raw_artifact_created"),
        ({"mutation_attempted": True}, "c0_mutation_attempted"),
        ({"external_side_effect_attempted": True}, "external_side_effect_attempted"),
    ],
)
def test_c0_hazards_fail_closed(
    overrides: dict[str, object],
    expected_code: str,
) -> None:
    decision = evaluate_reference_c0(_passing_observation(**overrides))

    assert decision.status == "fail"
    assert expected_code in decision.failure_codes
    assert decision.capture_allowed is False
    assert decision.synthetic_workspace_entry_allowed is False
    descriptor = decision.descriptor()
    if expected_code == "personal_workspace_opened":
        assert descriptor["personal_workspace_opened"] is True
    if expected_code == "personal_page_opened":
        assert descriptor["personal_page_opened"] is True
    if expected_code in {"c0_screenshot_created", "c0_raw_artifact_created"}:
        assert descriptor["artifact_created"] is True
    if expected_code == "unknown_label_captured":
        assert descriptor["raw_label_disclosed"] is True


@pytest.mark.unit
def test_c0_rejects_non_boolean_and_wrong_profile_inputs() -> None:
    with pytest.raises(TypeError, match="browser_connection_visible"):
        _passing_observation(browser_connection_visible=1)
    with pytest.raises(ValueError, match="profile"):
        _passing_observation(profile="business-owner-chrome-desktop-ko-KR")
    with pytest.raises(ValueError, match="run id"):
        _passing_observation(run_id="actual-workspace-name")


@pytest.mark.unit
def test_c0_decision_cannot_be_forged_into_a_passing_authorization() -> None:
    with pytest.raises(ValueError, match="status"):
        ReferenceC0Decision(
            run_id="ui1-forged-c0",
            status="pass",
            failure_codes=(),
            connection_verified=False,
            workspace_boundary_verified=True,
            read_only_verified=True,
            capture_allowed=True,
            synthetic_workspace_entry_allowed=True,
            other_workspace_labels_visible=False,
            personal_workspace_opened=False,
            personal_page_opened=False,
            artifact_created=False,
            mutation_attempted=False,
            external_side_effect_attempted=False,
            raw_label_disclosed=False,
        )


@pytest.mark.unit
def test_c0_descriptor_round_trips_exactly_and_rejects_schema_drift() -> None:
    decision = evaluate_reference_c0(_passing_observation())
    assert reference_c0_decision_from_descriptor(decision.descriptor()) == decision

    with_extra = decision.descriptor()
    with_extra["workspace_name"] = "not allowed"
    with pytest.raises(ValueError, match="fields are not exact"):
        reference_c0_decision_from_descriptor(with_extra)

    contradictory = decision.descriptor()
    contradictory["connection_verified"] = False
    with pytest.raises(ValueError, match="inconsistent"):
        reference_c0_decision_from_descriptor(contradictory)


@pytest.mark.unit
def test_reference_action_authorization_allows_bounded_synthetic_actions() -> None:
    decision = evaluate_reference_c0(_passing_observation())
    read_only = authorize_reference_action(
        decision,
        scenario_id="UI-SHELL-002",
        action_id="UI-SHELL-002-A01",
        workspace_alias="REF-FREE-A",
        mutation_class="read_only",
        synthetic_fixture_confirmed=False,
    )
    mutation = authorize_reference_action(
        decision,
        scenario_id="UI-EDITOR-001",
        action_id="UI-EDITOR-001-A01",
        workspace_alias="REF-FREE-B",
        mutation_class="synthetic_mutation",
        synthetic_fixture_confirmed=True,
    )

    assert read_only.allowed is True
    assert mutation.allowed is True
    assert read_only.descriptor()["contract"] == ACTION_AUTH_CONTRACT
    assert read_only.descriptor()["raw_label_disclosed"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("decision_passes", "workspace", "mutation_class", "fixture", "personal", "external", "reason"),
    [
        (False, "REF-FREE-A", "read_only", False, False, False, "c0_not_passed"),
        (True, "PERSONAL-FORBIDDEN", "read_only", False, False, False, "workspace_not_synthetic"),
        (True, "REF-FREE-A", "read_only", False, True, False, "personal_workspace_forbidden"),
        (True, "REF-FREE-A", "read_only", False, False, True, "external_side_effect_forbidden"),
        (True, "REF-FREE-A", "local_second_brain_mutation", True, False, False, "reference_second_brain_mutation_forbidden"),
        (True, "REF-FREE-A", "synthetic_mutation", False, False, False, "synthetic_fixture_unconfirmed"),
    ],
)
def test_reference_action_authorization_denies_unsafe_paths(
    decision_passes: bool,
    workspace: str,
    mutation_class: str,
    fixture: bool,
    personal: bool,
    external: bool,
    reason: str,
) -> None:
    observation = _passing_observation(
        browser_connection_visible=decision_passes,
    )
    decision = evaluate_reference_c0(observation)
    result = authorize_reference_action(
        decision,
        scenario_id="UI-SHELL-002",
        action_id="UI-SHELL-002-A01",
        workspace_alias=workspace,
        mutation_class=mutation_class,
        synthetic_fixture_confirmed=fixture,
        opens_personal_workspace=personal,
        creates_external_side_effect=external,
    )

    assert result.allowed is False
    assert result.reason_code == reason


@pytest.mark.unit
def test_reference_action_authorization_rejects_raw_labels_and_cross_scenario_ids() -> None:
    decision = evaluate_reference_c0(_passing_observation())
    with pytest.raises(ValueError, match="safe declared alias"):
        authorize_reference_action(
            decision,
            scenario_id="UI-SHELL-002",
            action_id="UI-SHELL-002-A01",
            workspace_alias="actual workspace name",
            mutation_class="read_only",
            synthetic_fixture_confirmed=False,
        )
    with pytest.raises(ValueError, match="does not belong"):
        authorize_reference_action(
            decision,
            scenario_id="UI-SHELL-002",
            action_id="UI-EDITOR-001-A01",
            workspace_alias="REF-FREE-A",
            mutation_class="read_only",
            synthetic_fixture_confirmed=False,
        )


@pytest.mark.unit
def test_reference_guard_import_has_no_application_or_filesystem_side_effects(
    tmp_path: Path,
) -> None:
    script = r'''
import json
from pathlib import Path
import sys

root = Path.cwd()
before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
import code_assist_v1.tools.notion_parity.ui_reference_guard  # noqa: F401
after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
forbidden = sorted(
    name for name in sys.modules
    if name in {
        "code_assist_v1.app_code",
        "code_assist_v1.auth",
        "code_assist_v1.config",
        "code_assist_v1.data_paths",
        "code_assist_v1.knowledge_core",
    }
    or name.startswith("code_assist_v1.routes_")
    or name.startswith("code_assist_v1.notion_workspace")
)
print(json.dumps({"forbidden": forbidden, "changed": before != after}, sort_keys=True))
'''
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

from __future__ import annotations

from code_assist_v1 import notion_core_ui_pilot as pilot


def _configuration(**overrides: str) -> pilot.CoreUiPilotConfiguration:
    environment = {
        pilot.PILOT_ENABLE_ENV: "1",
        pilot.PILOT_KILL_SWITCH_ENV: "0",
        pilot.PILOT_SUBJECTS_ENV: "owner",
        pilot.PILOT_WORKSPACES_ENV: "ws_alpha,ws_beta",
    }
    environment.update(overrides)
    return pilot.load_core_ui_pilot_configuration(environment)


def test_pilot_is_default_off_and_descriptor_is_identifier_free() -> None:
    decision = pilot.evaluate_core_ui_pilot(
        pilot.load_core_ui_pilot_configuration({}),
        actor_subject_id="owner",
        visible_workspace_ids=("ws_alpha",),
        substrate_ready=True,
    )

    assert decision.public_descriptor() == {
        "contract": "code_assist.notion.core_free_ui.pilot_control.v1",
        "default_off": True,
        "enabled": False,
        "ready": False,
        "allowlist_required": True,
        "kill_switch_available": True,
        "status": "not_ready",
    }
    assert "owner" not in str(decision.public_descriptor())
    assert "ws_alpha" not in str(decision.public_descriptor())


def test_pilot_requires_exact_subject_workspace_and_substrate() -> None:
    configuration = _configuration()

    assert pilot.evaluate_core_ui_pilot(
        configuration,
        actor_subject_id="owner",
        visible_workspace_ids=("ws_alpha", "ws_beta"),
        substrate_ready=True,
    ).enabled is True
    assert pilot.evaluate_core_ui_pilot(
        configuration,
        actor_subject_id="outsider",
        visible_workspace_ids=("ws_alpha",),
        substrate_ready=True,
    ).enabled is False
    assert pilot.evaluate_core_ui_pilot(
        configuration,
        actor_subject_id="owner",
        visible_workspace_ids=("ws_alpha", "ws_unlisted"),
        substrate_ready=True,
    ).enabled is False
    assert pilot.evaluate_core_ui_pilot(
        configuration,
        actor_subject_id="owner",
        visible_workspace_ids=(),
        substrate_ready=True,
    ).enabled is False
    assert pilot.evaluate_core_ui_pilot(
        configuration,
        actor_subject_id="owner",
        visible_workspace_ids=("ws_alpha",),
        substrate_ready=False,
    ).enabled is False


def test_pilot_configuration_fails_closed_for_non_exact_values() -> None:
    invalid_configurations = (
        _configuration(**{pilot.PILOT_ENABLE_ENV: "true"}),
        _configuration(**{pilot.PILOT_KILL_SWITCH_ENV: "true"}),
        _configuration(**{pilot.PILOT_KILL_SWITCH_ENV: "1"}),
        _configuration(**{pilot.PILOT_SUBJECTS_ENV: " owner"}),
        _configuration(**{pilot.PILOT_SUBJECTS_ENV: "owner,owner"}),
        _configuration(**{pilot.PILOT_WORKSPACES_ENV: "ws_alpha, ws_beta"}),
        _configuration(**{pilot.PILOT_WORKSPACES_ENV: "workspace-alpha"}),
    )

    assert all(configuration.preflight_ready is False for configuration in invalid_configurations)


def test_pilot_rejects_duplicate_or_malformed_visible_inventory() -> None:
    configuration = _configuration()

    for workspace_ids in (
        ("ws_alpha", "ws_alpha"),
        ("bad",),
    ):
        assert pilot.evaluate_core_ui_pilot(
            configuration,
            actor_subject_id="owner",
            visible_workspace_ids=workspace_ids,
            substrate_ready=True,
        ).enabled is False

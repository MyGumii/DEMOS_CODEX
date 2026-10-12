"""Fail-closed controls for the Core Free Notion UI pilot.

This module is deliberately side-effect free.  It parses an explicit runtime
configuration and evaluates already-authorized workspace identifiers; it never
opens the workspace catalog, imports the application, or enables the pilot.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Mapping, Sequence


CONTRACT = "code_assist.notion.core_free_ui.pilot_control.v1"
PILOT_ENABLE_ENV = "CODE_ASSIST_NOTION_CORE_UI_PILOT"
PILOT_KILL_SWITCH_ENV = "CODE_ASSIST_NOTION_CORE_UI_PILOT_KILL_SWITCH"
PILOT_SUBJECTS_ENV = "CODE_ASSIST_NOTION_CORE_UI_PILOT_SUBJECTS"
PILOT_WORKSPACES_ENV = "CODE_ASSIST_NOTION_CORE_UI_PILOT_WORKSPACES"

MAX_ALLOWLIST_ENTRIES = 128
MAX_ALLOWLIST_BYTES = 16 * 1024

_SUBJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")
_WORKSPACE_RE = re.compile(r"^ws_[A-Za-z0-9_-]{1,92}$")


@dataclass(frozen=True, slots=True)
class CoreUiPilotConfiguration:
    requested: bool
    kill_switch_clear: bool
    valid: bool
    subjects: frozenset[str]
    workspaces: frozenset[str]

    @property
    def preflight_ready(self) -> bool:
        return (
            self.requested
            and self.kill_switch_clear
            and self.valid
            and bool(self.subjects)
            and bool(self.workspaces)
        )


@dataclass(frozen=True, slots=True)
class CoreUiPilotDecision:
    enabled: bool

    def public_descriptor(self) -> dict[str, object]:
        """Return one identifier-free, non-oracle readiness envelope."""

        return {
            "contract": CONTRACT,
            "default_off": True,
            "enabled": self.enabled,
            "ready": self.enabled,
            "allowlist_required": True,
            "kill_switch_available": True,
            "status": "ready" if self.enabled else "not_ready",
        }


def _parse_allowlist(raw: str, pattern: re.Pattern[str]) -> frozenset[str] | None:
    if not raw or len(raw.encode("utf-8")) > MAX_ALLOWLIST_BYTES:
        return None
    if raw != raw.strip():
        return None
    values = raw.split(",")
    if not values or len(values) > MAX_ALLOWLIST_ENTRIES:
        return None
    if any(not value or value != value.strip() for value in values):
        return None
    if len(set(values)) != len(values):
        return None
    if any(pattern.fullmatch(value) is None for value in values):
        return None
    return frozenset(values)


def load_core_ui_pilot_configuration(
    environ: Mapping[str, str] | None = None,
) -> CoreUiPilotConfiguration:
    """Parse the four exact environment controls without raising or doing I/O."""

    values = os.environ if environ is None else environ
    enable = str(values.get(PILOT_ENABLE_ENV, ""))
    kill_switch = str(values.get(PILOT_KILL_SWITCH_ENV, ""))
    subjects = _parse_allowlist(
        str(values.get(PILOT_SUBJECTS_ENV, "")),
        _SUBJECT_RE,
    )
    workspaces = _parse_allowlist(
        str(values.get(PILOT_WORKSPACES_ENV, "")),
        _WORKSPACE_RE,
    )
    return CoreUiPilotConfiguration(
        requested=enable == "1",
        kill_switch_clear=kill_switch in {"", "0"},
        valid=(
            enable in {"", "0", "1"}
            and kill_switch in {"", "0", "1"}
            and subjects is not None
            and workspaces is not None
        ),
        subjects=subjects or frozenset(),
        workspaces=workspaces or frozenset(),
    )


def evaluate_core_ui_pilot(
    configuration: CoreUiPilotConfiguration,
    *,
    actor_subject_id: str,
    visible_workspace_ids: Sequence[str],
    substrate_ready: bool,
) -> CoreUiPilotDecision:
    """Evaluate one authorized workspace inventory against the frozen controls.

    The entire visible inventory must be allowlisted because the current web UI
    exposes one global capability rather than a per-workspace capability.  An
    empty inventory is not a pilot target.
    """

    if not configuration.preflight_ready:
        return CoreUiPilotDecision(enabled=False)
    if _SUBJECT_RE.fullmatch(actor_subject_id) is None:
        return CoreUiPilotDecision(enabled=False)
    if actor_subject_id not in configuration.subjects:
        return CoreUiPilotDecision(enabled=False)
    if substrate_ready is not True:
        return CoreUiPilotDecision(enabled=False)
    workspace_ids = tuple(visible_workspace_ids)
    if not workspace_ids:
        return CoreUiPilotDecision(enabled=False)
    if len(workspace_ids) > MAX_ALLOWLIST_ENTRIES:
        return CoreUiPilotDecision(enabled=False)
    if len(set(workspace_ids)) != len(workspace_ids):
        return CoreUiPilotDecision(enabled=False)
    if any(_WORKSPACE_RE.fullmatch(value) is None for value in workspace_ids):
        return CoreUiPilotDecision(enabled=False)
    return CoreUiPilotDecision(
        enabled=all(value in configuration.workspaces for value in workspace_ids)
    )

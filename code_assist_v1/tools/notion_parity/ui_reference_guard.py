"""Fail-closed C0 guard for synthetic Notion reference observation.

This module records booleans and synthetic aliases only.  It never opens a
browser, discovers profiles, stores visible workspace labels, or touches the
filesystem.  Browser automation must obtain a passing C0 decision before any
reference capture or synthetic mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import re
from typing import Any

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256


C0_CONTRACT = "notion_core_free_ui_reference_c0_v1"
ACTION_AUTH_CONTRACT = "notion_core_free_ui_reference_action_authorization_v1"
PROFILE = "free-owner-chrome-desktop-ko-KR"
REFERENCE_WORKSPACES = frozenset({"REF-FREE-A", "REF-FREE-B"})
WORKSPACE_INPUT_ALIASES = REFERENCE_WORKSPACES | frozenset(
    {"PERSONAL-FORBIDDEN", "UNRECOGNIZED"}
)
RUN_ID_RE = re.compile(r"^ui[0-9]+-[a-z0-9]+(?:-[a-z0-9]+)*$")
SCENARIO_RE = re.compile(r"^UI-[A-Z0-9]+(?:-[A-Z0-9]+)*-[0-9]{3}$")
ACTION_RE = re.compile(r"^UI-[A-Z0-9]+(?:-[A-Z0-9]+)*-A[0-9]{2}$")
MUTATION_CLASSES = frozenset(
    {"read_only", "synthetic_mutation", "local_second_brain_mutation"}
)
C0_DESCRIPTOR_KEYS = frozenset(
    {
        "contract",
        "run_id",
        "profile",
        "phase",
        "status",
        "failure_codes",
        "connection_verified",
        "workspace_boundary_verified",
        "read_only_verified",
        "capture_allowed",
        "synthetic_workspace_entry_allowed",
        "other_workspace_labels_visible",
        "personal_workspace_opened",
        "personal_page_opened",
        "artifact_created",
        "mutation_attempted",
        "external_side_effect_attempted",
        "raw_label_disclosed",
    }
)


def _require_bool_fields(
    value: object,
    *,
    skip: frozenset[str] = frozenset({"run_id", "profile"}),
) -> None:
    for field in fields(value):
        item = getattr(value, field.name)
        if field.name not in skip and type(item) is not bool:
            raise TypeError(f"{field.name} must be a boolean")


@dataclass(frozen=True, slots=True)
class ReferenceC0Observation:
    run_id: str
    profile: str = PROFILE
    browser_connection_visible: bool = False
    chrome_session_confirmed: bool = False
    signed_in_visible: bool = False
    workspace_switcher_opened: bool = False
    ref_free_a_confirmed: bool = False
    ref_free_b_confirmed: bool = False
    other_workspace_labels_visible: bool = False
    personal_workspace_opened: bool = False
    personal_page_opened: bool = False
    unknown_label_captured: bool = False
    screenshot_created: bool = False
    raw_artifact_created: bool = False
    mutation_attempted: bool = False
    external_side_effect_attempted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not RUN_ID_RE.fullmatch(self.run_id):
            raise ValueError("invalid synthetic run id")
        if self.profile != PROFILE:
            raise ValueError("C0 requires the fixed Core Free profile")
        _require_bool_fields(self)


@dataclass(frozen=True, slots=True)
class ReferenceC0Decision:
    run_id: str
    status: str
    failure_codes: tuple[str, ...]
    connection_verified: bool
    workspace_boundary_verified: bool
    read_only_verified: bool
    capture_allowed: bool
    synthetic_workspace_entry_allowed: bool
    other_workspace_labels_visible: bool
    personal_workspace_opened: bool
    personal_page_opened: bool
    artifact_created: bool
    mutation_attempted: bool
    external_side_effect_attempted: bool
    raw_label_disclosed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not RUN_ID_RE.fullmatch(self.run_id):
            raise ValueError("invalid synthetic run id")
        if self.status not in {"pass", "fail"}:
            raise ValueError("invalid C0 status")
        if (
            not isinstance(self.failure_codes, tuple)
            or any(not isinstance(code, str) or not code for code in self.failure_codes)
            or tuple(sorted(set(self.failure_codes))) != self.failure_codes
        ):
            raise ValueError("failure codes must be canonical")
        _require_bool_fields(
            self,
            skip=frozenset({"run_id", "status", "failure_codes"}),
        )
        passed = bool(
            not self.failure_codes
            and self.connection_verified
            and self.workspace_boundary_verified
            and self.read_only_verified
            and not self.personal_workspace_opened
            and not self.personal_page_opened
            and not self.artifact_created
            and not self.mutation_attempted
            and not self.external_side_effect_attempted
            and not self.raw_label_disclosed
        )
        if self.status != ("pass" if passed else "fail"):
            raise ValueError("C0 status does not match the observed safety state")
        if self.capture_allowed != passed or self.synthetic_workspace_entry_allowed != passed:
            raise ValueError("C0 authorization does not match the observed safety state")

    def descriptor(self) -> dict[str, Any]:
        return {
            "contract": C0_CONTRACT,
            "run_id": self.run_id,
            "profile": PROFILE,
            "phase": "C0",
            "status": self.status,
            "failure_codes": list(self.failure_codes),
            "connection_verified": self.connection_verified,
            "workspace_boundary_verified": self.workspace_boundary_verified,
            "read_only_verified": self.read_only_verified,
            "capture_allowed": self.capture_allowed,
            "synthetic_workspace_entry_allowed": self.synthetic_workspace_entry_allowed,
            "other_workspace_labels_visible": self.other_workspace_labels_visible,
            "personal_workspace_opened": self.personal_workspace_opened,
            "personal_page_opened": self.personal_page_opened,
            "artifact_created": self.artifact_created,
            "mutation_attempted": self.mutation_attempted,
            "external_side_effect_attempted": self.external_side_effect_attempted,
            "raw_label_disclosed": self.raw_label_disclosed,
        }

    def sha256(self) -> str:
        return canonical_sha256(self.descriptor())


def evaluate_reference_c0(observation: ReferenceC0Observation) -> ReferenceC0Decision:
    """Evaluate C0 without retaining any raw workspace or page label."""

    failures: list[str] = []
    if not observation.browser_connection_visible:
        failures.append("connection_unavailable")
    if not observation.chrome_session_confirmed:
        failures.append("wrong_or_unconfirmed_browser_session")
    if not observation.signed_in_visible:
        failures.append("signed_in_state_unconfirmed")
    if not observation.workspace_switcher_opened:
        failures.append("workspace_switcher_unobserved")
    if not observation.ref_free_a_confirmed or not observation.ref_free_b_confirmed:
        failures.append("synthetic_workspace_boundary_unconfirmed")
    if observation.personal_workspace_opened:
        failures.append("personal_workspace_opened")
    if observation.personal_page_opened:
        failures.append("personal_page_opened")
    if observation.unknown_label_captured:
        failures.append("unknown_label_captured")
    if observation.screenshot_created:
        failures.append("c0_screenshot_created")
    if observation.raw_artifact_created:
        failures.append("c0_raw_artifact_created")
    if observation.mutation_attempted:
        failures.append("c0_mutation_attempted")
    if observation.external_side_effect_attempted:
        failures.append("external_side_effect_attempted")

    failure_codes = tuple(sorted(set(failures)))
    connection_verified = bool(
        observation.browser_connection_visible
        and observation.chrome_session_confirmed
        and observation.signed_in_visible
    )
    boundary_verified = bool(
        observation.workspace_switcher_opened
        and observation.ref_free_a_confirmed
        and observation.ref_free_b_confirmed
        and not observation.personal_workspace_opened
        and not observation.personal_page_opened
    )
    read_only_verified = not any(
        (
            observation.screenshot_created,
            observation.raw_artifact_created,
            observation.mutation_attempted,
            observation.external_side_effect_attempted,
            observation.unknown_label_captured,
        )
    )
    passed = not failure_codes
    return ReferenceC0Decision(
        run_id=observation.run_id,
        status="pass" if passed else "fail",
        failure_codes=failure_codes,
        connection_verified=connection_verified,
        workspace_boundary_verified=boundary_verified,
        read_only_verified=read_only_verified,
        capture_allowed=passed,
        synthetic_workspace_entry_allowed=passed,
        other_workspace_labels_visible=observation.other_workspace_labels_visible,
        personal_workspace_opened=observation.personal_workspace_opened,
        personal_page_opened=observation.personal_page_opened,
        artifact_created=bool(
            observation.screenshot_created or observation.raw_artifact_created
        ),
        mutation_attempted=observation.mutation_attempted,
        external_side_effect_attempted=observation.external_side_effect_attempted,
        raw_label_disclosed=observation.unknown_label_captured,
    )


def reference_c0_decision_from_descriptor(value: Any) -> ReferenceC0Decision:
    """Reconstruct only an exact canonical C0 descriptor from a packet seal."""

    if not isinstance(value, dict) or set(value) != C0_DESCRIPTOR_KEYS:
        raise ValueError("C0 descriptor fields are not exact")
    if value.get("contract") != C0_CONTRACT or value.get("profile") != PROFILE:
        raise ValueError("C0 descriptor contract or profile is invalid")
    if value.get("phase") != "C0":
        raise ValueError("C0 descriptor phase is invalid")
    failure_codes = value.get("failure_codes")
    if not isinstance(failure_codes, list):
        raise ValueError("C0 descriptor failure codes are invalid")
    try:
        decision = ReferenceC0Decision(
            run_id=value["run_id"],
            status=value["status"],
            failure_codes=tuple(failure_codes),
            connection_verified=value["connection_verified"],
            workspace_boundary_verified=value["workspace_boundary_verified"],
            read_only_verified=value["read_only_verified"],
            capture_allowed=value["capture_allowed"],
            synthetic_workspace_entry_allowed=value[
                "synthetic_workspace_entry_allowed"
            ],
            other_workspace_labels_visible=value["other_workspace_labels_visible"],
            personal_workspace_opened=value["personal_workspace_opened"],
            personal_page_opened=value["personal_page_opened"],
            artifact_created=value["artifact_created"],
            mutation_attempted=value["mutation_attempted"],
            external_side_effect_attempted=value["external_side_effect_attempted"],
            raw_label_disclosed=value["raw_label_disclosed"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("C0 descriptor is inconsistent") from exc
    if decision.descriptor() != value:
        raise ValueError("C0 descriptor is not canonical")
    return decision


@dataclass(frozen=True, slots=True)
class ReferenceActionAuthorization:
    decision_sha256: str
    scenario_id: str
    action_id: str
    workspace_alias: str
    mutation_class: str
    allowed: bool
    reason_code: str

    def descriptor(self) -> dict[str, Any]:
        return {
            "contract": ACTION_AUTH_CONTRACT,
            "decision_sha256": self.decision_sha256,
            "scenario_id": self.scenario_id,
            "action_id": self.action_id,
            "workspace_alias": self.workspace_alias,
            "mutation_class": self.mutation_class,
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "raw_label_disclosed": False,
        }


def authorize_reference_action(
    decision: ReferenceC0Decision,
    *,
    scenario_id: str,
    action_id: str,
    workspace_alias: str,
    mutation_class: str,
    synthetic_fixture_confirmed: bool,
    opens_personal_workspace: bool = False,
    creates_external_side_effect: bool = False,
) -> ReferenceActionAuthorization:
    """Authorize one synthetic reference action after a passing C0 decision."""

    if not isinstance(decision, ReferenceC0Decision):
        raise TypeError("decision must be a ReferenceC0Decision")
    if not isinstance(scenario_id, str) or not SCENARIO_RE.fullmatch(scenario_id):
        raise ValueError("invalid UI scenario id")
    if not isinstance(action_id, str) or not ACTION_RE.fullmatch(action_id):
        raise ValueError("invalid UI action id")
    if workspace_alias not in WORKSPACE_INPUT_ALIASES:
        raise ValueError("workspace_alias must be a safe declared alias")
    if not action_id.startswith(scenario_id + "-A"):
        raise ValueError("action id does not belong to the scenario")
    if mutation_class not in MUTATION_CLASSES:
        raise ValueError("unsupported mutation class")
    for name, value in (
        ("synthetic_fixture_confirmed", synthetic_fixture_confirmed),
        ("opens_personal_workspace", opens_personal_workspace),
        ("creates_external_side_effect", creates_external_side_effect),
    ):
        if type(value) is not bool:
            raise TypeError(f"{name} must be a boolean")

    reason = "allowed"
    if (
        decision.status != "pass"
        or not decision.capture_allowed
        or not decision.synthetic_workspace_entry_allowed
        or not decision.connection_verified
        or not decision.workspace_boundary_verified
        or not decision.read_only_verified
    ):
        reason = "c0_not_passed"
    elif workspace_alias not in REFERENCE_WORKSPACES:
        reason = "workspace_not_synthetic"
    elif opens_personal_workspace:
        reason = "personal_workspace_forbidden"
    elif creates_external_side_effect:
        reason = "external_side_effect_forbidden"
    elif mutation_class == "local_second_brain_mutation":
        reason = "reference_second_brain_mutation_forbidden"
    elif mutation_class == "synthetic_mutation" and not synthetic_fixture_confirmed:
        reason = "synthetic_fixture_unconfirmed"
    allowed = reason == "allowed"
    return ReferenceActionAuthorization(
        decision_sha256=decision.sha256(),
        scenario_id=scenario_id,
        action_id=action_id,
        workspace_alias=workspace_alias,
        mutation_class=mutation_class,
        allowed=allowed,
        reason_code=reason,
    )


__all__ = [
    "ACTION_AUTH_CONTRACT",
    "C0_CONTRACT",
    "PROFILE",
    "REFERENCE_WORKSPACES",
    "WORKSPACE_INPUT_ALIASES",
    "ReferenceActionAuthorization",
    "ReferenceC0Decision",
    "ReferenceC0Observation",
    "authorize_reference_action",
    "evaluate_reference_c0",
    "reference_c0_decision_from_descriptor",
]

"""Product-render-only screenshot collector for the V0B-P1 visual gate.

The candidate bitmap is captured before reference pixels are read.  The page is
never given reference pixels, capture-only geometry, text, colors, or rasters,
and the candidate is not rewritten after ``page.screenshot``.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from code_assist_v1.tools.notion_parity.exact_pixel import (
    compare_pngs_exact,
    validate_capture_environment,
)
from code_assist_v1.tools.notion_parity.visual_similarity import (
    compare_pngs_similarity,
    validate_visual_similarity_policy,
)
from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_execution import (
    EXPECTED_STATES,
)
from code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt import (
    CANDIDATE_SCREENSHOT_ROOT,
    DIFF_ROOT,
    REFERENCE_SCREENSHOT_ROOT,
    VISUAL_SIMILARITY_POLICY_PATH,
    VISUAL_SIMILARITY_POLICY_REPO_PATH,
    current_source_binding,
)

from .approved_dev_pair import ApprovedDevPairError
from .v0b_p1_product_runtime import ACCESSIBILITY_SCRIPT


ADOPTED_VIEWPORTS = ((1440, 900), (1280, 800))
CAPTURE_PIPELINE_CONTRACT = (
    "code_assist.notion.internal_collaborative_v0b_p1.product_capture.v5"
)
COLLECTOR_REPO_PATH = (
    "code_assist_v1/notion_collaboration/v0b_p1_product_capture.py"
)
CAPTURE_TRANSITIVE_REPO_PATHS = (
    "code_assist_v1/tools/notion_parity/exact_pixel.py",
    "code_assist_v1/tools/notion_parity/visual_similarity.py",
    "code_assist_v1/notion_collaboration/approved_dev_pair.py",
    "code_assist_v1/notion_collaboration/v0b_p1_product_runtime.py",
    "code_assist_v1/notion_collaboration/v0b_p1_iam_browser.py",
    "code_assist_v1/notion_collaboration/c0_p2_browser.py",
    "code_assist_v1/notion_collaboration/e1_p2_browser.py",
    "code_assist_v1/notion_collaboration/f0_p5_browser.py",
)
EXPECTED_STATE_MAP = {
    state[0]: {
        "workspace_alias": state[1],
        "actor_alias": state[2],
        "surface": state[3],
        "expected_state": state[4],
    }
    for state in EXPECTED_STATES
}
EXPECTED_ACTOR_SUBJECTS = {
    "REF-OWNER": frozenset({"admin"}),
    "REF-COLLABORATOR": frozenset({"user01"}),
    "REF-OWNER+REF-COLLABORATOR": frozenset({"admin", "user01"}),
}

_FREEZE_CAPTURE_ENVIRONMENT = """epoch => {
  if (!window.__v0bProductOriginalDateNow) {
    window.__v0bProductOriginalDateNow = Date.now.bind(Date);
  }
  Date.now = () => epoch;
}"""

_RESTORE_CAPTURE_ENVIRONMENT = """() => {
  if (window.__v0bProductOriginalDateNow) {
    Date.now = window.__v0bProductOriginalDateNow;
    delete window.__v0bProductOriginalDateNow;
  }
}"""

_CAPTURE_PURITY_SCRIPT = """() => {
  const forbiddenIds = [
    'v0b-capture-freeze',
    'v0b-remaining-full-reference-overlay',
    'v0b-state001-002-004-residual-overlay',
    'v0b-state002-wide-supplemental-overlay',
    'v0b-wide-table-lines-style',
    'v0b-korean-inbox-filter-style',
  ];
  const violations = forbiddenIds
    .filter(id => document.getElementById(id))
    .map(id => `id:${id}`);
  for (const element of document.querySelectorAll('*')) {
    for (const name of element.getAttributeNames()) {
      if (name.startsWith('data-v0b-')) violations.push(`attribute:${name}`);
    }
  }
  return [...new Set(violations)].sort();
}"""

_ACTIVE_KEYBOARD_TARGET_SCRIPT = """() => {
  const element = document.activeElement;
  const tag = String(element?.tagName || '').toLowerCase();
  const implicitRole = tag === 'button' ? 'button'
    : tag === 'select' ? 'combobox'
    : tag === 'textarea' || tag === 'input' ? 'textbox'
    : tag === 'a' && element?.hasAttribute('href') ? 'link'
    : tag === 'main' ? 'main'
    : 'generic';
  const style = element ? getComputedStyle(element) : null;
  const visible = Boolean(element)
    && !element.hidden
    && style?.display !== 'none'
    && style?.visibility !== 'hidden';
  const name = String(
    element?.getAttribute?.('aria-label')
    || element?.getAttribute?.('title')
    || element?.getAttribute?.('placeholder')
    || element?.textContent
    || element?.id
    || 'unnamed'
  ).replace(/\\s+/g, ' ').trim().slice(0, 240) || 'unnamed';
  return {
    name,
    role: String(element?.getAttribute?.('role') || implicitRole),
    state: visible ? 'visible,focused' : 'hidden',
  };
}"""


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256(path.read_bytes())


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _reference_transport_eligible(receipt: Mapping[str, Any]) -> bool:
    transport = receipt.get("capture_transport")
    if transport is None:
        return True
    return (
        isinstance(transport, dict)
        and transport.get("source_encoding_lossless") is True
        and transport.get("zero_RGBA_equivalence_claimed") is True
    )


def _reference_map(
    receipt: Mapping[str, Any],
) -> dict[tuple[str, int, int], dict[str, Any]]:
    result: dict[tuple[str, int, int], dict[str, Any]] = {}
    for item in receipt.get("captures") or []:
        if not isinstance(item, dict) or not isinstance(item.get("viewport"), dict):
            continue
        viewport = item["viewport"]
        key = (item.get("state_id"), viewport.get("width"), viewport.get("height"))
        if isinstance(key[0], str) and isinstance(key[1], int) and isinstance(key[2], int):
            result[key] = item
    return result


def capture_pipeline_binding(repository_root: Path | None = None) -> dict[str, Any]:
    root = repository_root or Path(__file__).resolve().parents[2]
    collector_path = root / Path(*COLLECTOR_REPO_PATH.split("/"))
    driver_repo_path = "code_assist_v1/notion_collaboration/v0b_p1_browser.py"
    driver_path = root / Path(*driver_repo_path.split("/"))
    transitive_files = [
        {
            "path": relative,
            "sha256": _sha256_file(root / Path(*relative.split("/"))),
        }
        for relative in CAPTURE_TRANSITIVE_REPO_PATHS
    ]
    policy = _load_json(root / Path(*VISUAL_SIMILARITY_POLICY_REPO_PATH.split("/")))
    normalized_policy = validate_visual_similarity_policy(policy)
    return {
        "contract": CAPTURE_PIPELINE_CONTRACT,
        "mode": "product_render_only",
        "collector_path": COLLECTOR_REPO_PATH,
        "collector_sha256": _sha256_file(collector_path),
        "browser_driver_path": driver_repo_path,
        "browser_driver_sha256": _sha256_file(driver_path),
        "transitive_files": transitive_files,
        "visual_similarity_policy": {
            "path": VISUAL_SIMILARITY_POLICY_REPO_PATH,
            "file_sha256": _sha256_file(
                root / Path(*VISUAL_SIMILARITY_POLICY_REPO_PATH.split("/"))
            ),
            "policy_id": normalized_policy["policy_id"],
            "policy_sha256": normalized_policy["policy_sha256"],
        },
        "reference_pixels_available_to_page": False,
        "capture_only_visual_mutation": False,
        "post_capture_reference_matte": False,
    }


class V0BP1ProductCollector:
    """Collect unmodified product pixels plus bounded accessibility facts."""

    def __init__(
        self,
        *,
        reference_receipt_path: Path,
        reference_screenshot_root: Path = REFERENCE_SCREENSHOT_ROOT,
        reference_mask_root: Path | None = None,
        candidate_screenshot_root: Path = CANDIDATE_SCREENSHOT_ROOT,
        diff_root: Path = DIFF_ROOT,
        visual_similarity_policy_path: Path = VISUAL_SIMILARITY_POLICY_PATH,
        browser_version: str,
        exact_gate_required: bool = True,
    ) -> None:
        receipt = _load_json(reference_receipt_path)
        if not isinstance(receipt, dict):
            raise ApprovedDevPairError("V0B_REFERENCE_RECEIPT_INVALID")
        self.reference_transport_gate_eligible = _reference_transport_eligible(receipt)
        if exact_gate_required and not self.reference_transport_gate_eligible:
            raise ApprovedDevPairError("V0B_REFERENCE_CAPTURE_TRANSPORT_INELIGIBLE")
        self.reference_map = _reference_map(receipt)
        expected_keys = {
            (state_id, width, height)
            for state_id in EXPECTED_STATE_MAP
            for width, height in ADOPTED_VIEWPORTS
        }
        if set(self.reference_map) != expected_keys:
            raise ApprovedDevPairError("V0B_REFERENCE_CAPTURE_DENOMINATOR_INVALID")
        policy = _load_json(visual_similarity_policy_path)
        if not isinstance(policy, dict):
            raise ApprovedDevPairError("V0B_VISUAL_SIMILARITY_POLICY_INVALID")
        try:
            self.visual_similarity_policy = validate_visual_similarity_policy(
                policy,
                expected_keys=expected_keys,
            )
        except (TypeError, ValueError) as exc:
            raise ApprovedDevPairError(
                f"V0B_VISUAL_SIMILARITY_POLICY_INVALID:{exc}"
            ) from exc
        self.reference_screenshot_root = reference_screenshot_root
        self.reference_mask_root = reference_mask_root
        self.candidate_screenshot_root = candidate_screenshot_root
        self.diff_root = diff_root
        self.browser_major = str(browser_version).split(".", 1)[0]
        if not self.browser_major.isdigit():
            raise ApprovedDevPairError("V0B_BROWSER_VERSION_INVALID")
        self.source_binding = current_source_binding()
        self.captures: list[dict[str, Any]] = []
        self._seen: set[tuple[str, int, int]] = set()
        self._page_subject_bindings: dict[int, str] = {}

    def capture(
        self,
        state_id: str,
        page: Any,
        *,
        selectors: Mapping[str, str],
        focus_selector: str,
        keyboard_path: Sequence[str],
        live_region_expected: bool,
        observation: str,
    ) -> list[dict[str, Any]]:
        if state_id not in EXPECTED_STATE_MAP:
            raise ApprovedDevPairError(f"V0B_STATE_UNKNOWN:{state_id}")
        if not selectors or not focus_selector or not keyboard_path or not observation:
            raise ApprovedDevPairError(f"V0B_STATE_EVIDENCE_INCOMPLETE:{state_id}")
        parsed = urlsplit(page.url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != 10112:
            raise ApprovedDevPairError(f"V0B_CANDIDATE_ORIGIN_INVALID:{state_id}")

        expected = EXPECTED_STATE_MAP[state_id]
        runtime_binding = page.evaluate(
            """() => {
              const workspaceId = State.navigation?.workspaceId || '';
              const workspace = (State.navigation?.workspaces || [])
                .find(item => item.id === workspaceId);
              return {
                workspace_name: String(workspace?.name || document.querySelector('#workspaceTitle')?.textContent || '').trim(),
                active_actor_subject_id: String(State.user?.id || '').trim(),
                active_page_title: String(State.pageData?.page?.title || document.querySelector('#pageTitle')?.value || '').trim(),
              };
            }"""
        )
        if not isinstance(runtime_binding, dict):
            raise ApprovedDevPairError(f"V0B_RUNTIME_BINDING_MISMATCH:{state_id}")
        current_subject = str(runtime_binding.get("active_actor_subject_id") or "")
        page_key = id(page)
        if current_subject:
            self._page_subject_bindings[page_key] = current_subject
        resolved_subject = current_subject or self._page_subject_bindings.get(page_key, "")
        runtime_binding["active_actor_subject_id"] = resolved_subject
        runtime_binding["current_surface_subject_id"] = current_subject
        runtime_binding["subject_binding_source"] = (
            "current_surface_state"
            if current_subject
            else "same_ephemeral_browser_context_pre_revoke"
        )
        allowed_subjects = EXPECTED_ACTOR_SUBJECTS.get(expected["actor_alias"], frozenset())
        if (
            (expected["surface"] != "access_boundary" and runtime_binding.get("workspace_name") != expected["workspace_alias"])
            or resolved_subject not in allowed_subjects
            or (not current_subject and expected["surface"] != "access_boundary")
        ):
            raise ApprovedDevPairError(
                f"V0B_RUNTIME_BINDING_MISMATCH:{state_id}:{runtime_binding!r}"
            )

        rows: list[dict[str, Any]] = []
        for width, height in ADOPTED_VIEWPORTS:
            key = (state_id, width, height)
            if key in self._seen:
                raise ApprovedDevPairError(
                    f"V0B_CAPTURE_DUPLICATE:{state_id}:{width}x{height}"
                )
            reference = self.reference_map[key]
            reference_relative = str(reference.get("screenshot_relative_path") or "")
            reference_path = self.reference_screenshot_root / reference_relative
            if not reference_path.is_file():
                raise ApprovedDevPairError(
                    f"V0B_REFERENCE_SCREENSHOT_MISSING:{state_id}:{width}x{height}"
                )
            environment = reference.get("environment")
            if not isinstance(environment, dict):
                raise ApprovedDevPairError(f"V0B_REFERENCE_ENVIRONMENT_INVALID:{state_id}")
            epoch_ms = environment.get("time", {}).get("epoch_ms")

            page.set_viewport_size({"width": width, "height": height})
            purity_violations = page.evaluate(_CAPTURE_PURITY_SCRIPT)
            if purity_violations:
                raise ApprovedDevPairError(
                    f"V0B_CAPTURE_ONLY_MUTATION_DETECTED:{state_id}:"
                    + ",".join(str(item) for item in purity_violations)
                )
            page.evaluate(_FREEZE_CAPTURE_ENVIRONMENT, epoch_ms)
            try:
                page.evaluate("() => document.fonts?.ready || Promise.resolve()")
                focus = page.locator(focus_selector).first
                focus.wait_for(state="visible", timeout=10_000)
                focus.evaluate("element => element.focus({preventScroll: true})")
                page.wait_for_timeout(50)
                accessibility = page.evaluate(
                    ACCESSIBILITY_SCRIPT,
                    {"selectors": dict(selectors), "liveExpected": live_region_expected},
                )
                focus_row = accessibility.get("focus") if isinstance(accessibility, dict) else None
                if not isinstance(focus_row, dict):
                    raise ApprovedDevPairError(
                        f"V0B_ACCESSIBILITY_CAPTURE_FAILED:{state_id}"
                    )
                focus_row["sequence"] = list(keyboard_path)
                accessibility["keyboard_path"] = list(keyboard_path)
                candidate_png = page.screenshot(
                    type="png",
                    full_page=False,
                    animations="disabled",
                    omit_background=False,
                )
                keyboard_steps: list[dict[str, str]] = []
                for keyboard_chord in ("Tab", "Shift+Tab"):
                    page.keyboard.press(keyboard_chord)
                    target = page.evaluate(_ACTIVE_KEYBOARD_TARGET_SCRIPT)
                    keyboard_steps.append({"key": keyboard_chord, **target})
                keyboard_execution = {
                    "kind": "focus_navigation_probe",
                    "status": (
                        "pass"
                        if all(step.get("state") == "visible,focused" for step in keyboard_steps)
                        else "fail"
                    ),
                    "sequence": ["Tab", "Shift+Tab"],
                    "steps": keyboard_steps,
                }
                if keyboard_execution["status"] != "pass":
                    raise ApprovedDevPairError(
                        f"V0B_KEYBOARD_NAVIGATION_FAILED:{state_id}"
                    )
                accessibility["keyboard_execution"] = keyboard_execution
            finally:
                page.evaluate(_RESTORE_CAPTURE_ENVIRONMENT)

            # Read reference pixels only after the browser has returned the
            # candidate bitmap.  Nothing below can affect that bitmap.
            reference_png = reference_path.read_bytes()
            relative = f"{state_id}__{width}x{height}.png"
            candidate_path = self.candidate_screenshot_root / relative
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_path.write_bytes(candidate_png)

            candidate_environment = copy.deepcopy(environment)
            candidate_environment["source"] = {
                "kind": "candidate",
                "build_sha256": self.source_binding["build_sha256"],
            }
            normalized_candidate = validate_capture_environment(candidate_environment)
            if normalized_candidate["browser"]["major_version"] != self.browser_major:
                raise ApprovedDevPairError(
                    f"V0B_BROWSER_MAJOR_MISMATCH:{self.browser_major}:"
                    f"{normalized_candidate['browser']['major_version']}"
                )

            scenario_id = f"V0B-P1-{state_id}-{width}X{height}"
            mask_relative = reference.get("mask_relative_path")
            if (
                self.reference_mask_root is None
                or not isinstance(mask_relative, str)
                or not mask_relative
                or "\\" in mask_relative
                or Path(mask_relative).name != mask_relative
            ):
                raise ApprovedDevPairError(
                    f"V0B_CORRECTIVE_MASK_BINDING_REQUIRED:{state_id}:{width}x{height}"
                )
            mask_path = self.reference_mask_root / mask_relative
            if not mask_path.is_file() or _sha256_file(mask_path) != reference.get("mask_sha256"):
                raise ApprovedDevPairError(
                    f"V0B_CORRECTIVE_MASK_HASH_DRIFT:{state_id}:{width}x{height}"
                )
            mask = _load_json(mask_path)
            if not isinstance(mask, dict):
                raise ApprovedDevPairError(
                    f"V0B_CORRECTIVE_MASK_INVALID:{state_id}:{width}x{height}"
                )
            mask["scenario_id"] = scenario_id
            comparison = compare_pngs_exact(
                reference_png,
                candidate_png,
                scenario_id=scenario_id,
                mask_manifest=mask,
                reference_environment=environment,
                candidate_environment=candidate_environment,
            )
            visual_similarity = compare_pngs_similarity(
                reference_png,
                candidate_png,
                state_id=state_id,
                scenario_id=scenario_id,
                mask_manifest=mask,
                policy=self.visual_similarity_policy,
                reference_environment=environment,
                candidate_environment=candidate_environment,
            )
            diff_path = self.diff_root / relative
            diff_path.parent.mkdir(parents=True, exist_ok=True)
            diff_path.write_bytes(comparison.diff_png)

            row = {
                "state_id": state_id,
                **expected,
                "runtime_binding": dict(runtime_binding),
                "viewport": {"width": width, "height": height},
                "reference_screenshot_relative_path": reference_relative,
                "reference_screenshot_sha256": _sha256(reference_png),
                "candidate_screenshot_relative_path": relative,
                "candidate_screenshot_sha256": _sha256(candidate_png),
                "diff_relative_path": relative,
                "diff_sha256": _sha256(comparison.diff_png),
                "candidate_environment": candidate_environment,
                "candidate_environment_sha256": normalized_candidate["environment_sha256"],
                "mask_manifest": mask,
                "capture_matte": None,
                "capture_provenance": {
                    "mode": "product_render_only",
                    "browser_screenshot_before_reference_pixel_read": True,
                    "reference_pixels_injected": False,
                    "capture_only_visual_mutation": False,
                    "post_capture_reference_matte": False,
                },
                "reference_transport_gate_eligible": self.reference_transport_gate_eligible,
                "exact_pixel_report": comparison.report,
                "visual_similarity_report": visual_similarity.report,
                "accessibility": accessibility,
                "interaction_outcome": {
                    "status": "pass",
                    "expected_state": expected["expected_state"],
                    "observation": observation,
                },
                "captured_at": _utc_now(),
                "raw_identifier_persisted": False,
                "personal_content_visible": False,
            }
            self.captures.append(row)
            rows.append(row)
            self._seen.add(key)
        return rows

    def assert_complete(self) -> None:
        if not self.reference_transport_gate_eligible:
            raise ApprovedDevPairError("V0B_REFERENCE_CAPTURE_TRANSPORT_INELIGIBLE")
        expected = {
            (state_id, width, height)
            for state_id in EXPECTED_STATE_MAP
            for width, height in ADOPTED_VIEWPORTS
        }
        if self._seen != expected or len(self.captures) != len(expected):
            missing = sorted(expected - self._seen)
            raise ApprovedDevPairError(f"V0B_CAPTURE_INCOMPLETE:{missing!r}")


__all__ = [
    "CAPTURE_PIPELINE_CONTRACT",
    "CAPTURE_TRANSITIVE_REPO_PATHS",
    "COLLECTOR_REPO_PATH",
    "V0BP1ProductCollector",
    "capture_pipeline_binding",
]

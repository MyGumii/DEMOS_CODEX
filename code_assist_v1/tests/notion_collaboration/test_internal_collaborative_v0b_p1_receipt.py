from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt as v0b_verifier
from code_assist_v1.tools.notion_parity.exact_pixel import (
    CAPTURE_ENVIRONMENT_CONTRACT,
    EXACT_PIXEL_CONTRACT,
    MASK_MANIFEST_CONTRACT,
    canonical_sha256,
    validate_capture_environment,
)
from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_execution import (
    EXPECTED_STATES,
)
from code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt import (
    CANDIDATE_SCREENSHOT_REPO_ROOT,
    CONTRACT,
    DIFF_REPO_ROOT,
    REFERENCE_REPO_PATH,
    _cached_comparison,
    current_capture_pipeline_binding,
    current_runtime_source_binding,
    current_source_binding,
    verify,
)


TIME = "2026-08-27T00:15:00+09:00"
HASH_FONT = "sha256:" + "f" * 64
HASH_REFERENCE_BUILD = "sha256:" + "e" * 64


@pytest.mark.unit
def test_v0b_comparison_cache_is_bound_to_bytes_kwargs_and_comparator() -> None:
    calls: list[tuple[bytes, bytes, dict[str, object]]] = []

    def compare(reference: bytes, candidate: bytes, **kwargs: object) -> object:
        calls.append((reference, candidate, kwargs))
        return SimpleNamespace(report={"call": len(calls)}, diff_png=b"diff")

    kwargs = {"scenario_id": "V0B-P1-CACHE", "mask_manifest": {"masks": []}}
    first = _cached_comparison("exact", compare, b"reference", b"candidate", **kwargs)
    repeated = _cached_comparison("exact", compare, b"reference", b"candidate", **kwargs)
    changed = _cached_comparison("exact", compare, b"reference", b"changed", **kwargs)

    assert repeated is first
    assert changed is not first
    assert len(calls) == 2


@pytest.mark.unit
def test_v0b_parallel_results_populate_parent_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(v0b_verifier, "_COMPARISON_CACHE", {})
    submitted: list[object] = []

    class Future:
        def __init__(self, task: object) -> None:
            self.task = task

        def result(self) -> tuple[int, object, object]:
            index = self.task[0]
            return index, f"exact-{index}", f"visual-{index}"

    class Executor:
        def __init__(self, *, max_workers: int) -> None:
            assert max_workers == 2

        def __enter__(self) -> "Executor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def submit(self, function: object, task: object) -> Future:
            assert function is v0b_verifier._default_comparison_task
            submitted.append(task)
            return Future(task)

    monkeypatch.setattr(v0b_verifier, "ProcessPoolExecutor", Executor)
    monkeypatch.setattr(v0b_verifier, "as_completed", lambda futures: futures)

    reference_root = tmp_path / "reference"
    candidate_root = tmp_path / "candidate"
    captures: list[dict[str, object]] = []
    reference_map: dict[tuple[str, int, int], dict[str, object]] = {}
    for index, state_id in enumerate(("V0R-MU-001", "V0R-MU-002")):
        name = f"capture-{index}.png"
        reference_root.mkdir(parents=True, exist_ok=True)
        candidate_root.mkdir(parents=True, exist_ok=True)
        (reference_root / name).write_bytes(f"reference-{index}".encode("ascii"))
        (candidate_root / name).write_bytes(f"candidate-{index}".encode("ascii"))
        reference_map[(state_id, 1440, 900)] = {"environment": {"side": "reference"}}
        captures.append(
            {
                "state_id": state_id,
                "viewport": {"width": 1440, "height": 900},
                "reference_screenshot_relative_path": name,
                "candidate_screenshot_relative_path": name,
                "candidate_environment": {"side": "candidate"},
                "mask_manifest": {"masks": []},
                "visual_similarity_report": {"status": "pass"},
            }
        )

    kwargs = {
        "reference_map": reference_map,
        "reference_screenshot_root": reference_root,
        "candidate_screenshot_root": candidate_root,
        "visual_similarity_policy": {"policy": "unit"},
    }
    first = v0b_verifier._parallel_default_comparisons(captures, **kwargs)
    second = v0b_verifier._parallel_default_comparisons(captures, **kwargs)

    assert first == {
        0: ("exact-0", "visual-0"),
        1: ("exact-1", "visual-1"),
    }
    assert second == first
    assert len(submitted) == 2
    assert len(v0b_verifier._COMPARISON_CACHE) == 4


@pytest.mark.unit
def test_v0b_worker_reuses_each_decoded_image_for_exact_and_similarity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference_file = tmp_path / "reference.png"
    candidate_file = tmp_path / "candidate.png"
    reference_file.write_bytes(b"reference")
    candidate_file.write_bytes(b"candidate")
    reference_image = SimpleNamespace(width=1440, height=900, side="reference")
    candidate_image = SimpleNamespace(width=1440, height=900, side="candidate")
    decoded: list[bytes] = []
    compared: list[tuple[str, object, object]] = []

    def decode(payload: bytes) -> object:
        decoded.append(payload)
        return reference_image if payload == b"reference" else candidate_image

    def compare_exact(left: object, right: object, **kwargs: object) -> str:
        compared.append(("exact", left, right))
        assert kwargs["scenario_id"] == "V0B-P1-V0R-MU-001-1440X900"
        return "exact-result"

    def compare_visual(left: object, right: object, **kwargs: object) -> str:
        compared.append(("visual", left, right))
        assert kwargs["state_id"] == "V0R-MU-001"
        return "visual-result"

    environment = {
        "viewport": {"width": 1440, "height": 900},
        "device_scale_factor": 1,
        "source": {"kind": "unit"},
        "environment_sha256": "sha256:" + "0" * 64,
    }
    monkeypatch.setattr(v0b_verifier, "decode_png_srgb_rgba8", decode)
    monkeypatch.setattr(
        v0b_verifier,
        "validate_capture_environment",
        lambda value: value,
    )
    monkeypatch.setattr(v0b_verifier, "compare_decoded_rgba8", compare_exact)
    monkeypatch.setattr(
        v0b_verifier,
        "compare_decoded_rgba8_similarity",
        compare_visual,
    )

    result = v0b_verifier._default_comparison_task(
        (
            7,
            reference_file,
            candidate_file,
            {
                "scenario_id": "V0B-P1-V0R-MU-001-1440X900",
                "mask_manifest": {"masks": []},
                "reference_environment": environment,
                "candidate_environment": environment,
            },
            {
                "state_id": "V0R-MU-001",
                "scenario_id": "V0B-P1-V0R-MU-001-1440X900",
                "mask_manifest": {"masks": []},
                "policy": {"policy": "unit"},
                "reference_environment": environment,
                "candidate_environment": environment,
            },
        )
    )

    assert result == (7, "exact-result", "visual-result")
    assert decoded == [b"reference", b"candidate"]
    assert compared == [
        ("exact", reference_image, candidate_image),
        ("visual", reference_image, candidate_image),
    ]


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _environment(
    width: int,
    height: int,
    *,
    kind: str,
    build_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": CAPTURE_ENVIRONMENT_CONTRACT,
        "viewport": {"width": width, "height": height},
        "device_scale_factor": 1,
        "zoom": 1,
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
        "color_scheme": "light",
        "reduced_motion": "reduce",
        "time": {"frozen": True, "epoch_ms": 1787760900000},
        "animations": {
            "disabled": True,
            "transition_duration_ms": 0,
            "caret_hidden": True,
        },
        "font_manifest": [
            {
                "family": "Segoe UI",
                "style": "normal",
                "weight": 400,
                "sha256": HASH_FONT,
            }
        ],
        "browser": {"engine": "chromium", "channel": "chrome", "major_version": "151"},
        "capture": {"full_page": False, "omit_background": False},
        "source": {"kind": kind, "build_sha256": build_sha256},
    }


def _mask(scenario_id: str, width: int, height: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": MASK_MANIFEST_CONTRACT,
        "scenario_id": scenario_id,
        "image": {"width": width, "height": height},
        "content_independent": True,
        "review": {
            "status": "approved",
            "receipt_id": "V0B-P1-UNIT-MASK-REVIEW",
            "reviewer": "unit-contract-reviewer",
            "reviewed_at": "2026-08-26T15:15:00Z",
        },
        "masks": [],
    }


def _report(
    *,
    scenario_id: str,
    width: int,
    height: int,
    reference_payload: bytes,
    candidate_payload: bytes,
    diff_payload: bytes,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": EXACT_PIXEL_CONTRACT,
        "evidence_type": "exact_pixel",
        "status": "pass",
        "scenario_id": scenario_id,
        "viewport": {"width": width, "height": height},
        "reference_image_sha256": _sha256(reference_payload),
        "candidate_image_sha256": _sha256(candidate_payload),
        "mask_manifest_sha256": "sha256:" + "a" * 64,
        "diff_image_sha256": _sha256(diff_payload),
        "environment_sha256": "sha256:" + "b" * 64,
        "differing_pixel_count": 0,
        "masked_differing_pixel_count": 0,
        "total_differing_pixel_count": 0,
        "difference_bounding_boxes": [],
    }


def _scenario_ids() -> list[str]:
    path = Path("code_assist_v1/docs/notion_parity/core_free_web_ui/scenarios.v1.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    return [item["scenario_id"] for item in value["required_scenarios"]]


def _fixture(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    reference_root = root / "reference"
    candidate_root = root / "candidate"
    diff_root = root / "diffs"
    source = current_source_binding()
    runtime_source = current_runtime_source_binding()
    reference_captures: list[dict[str, object]] = []
    candidate_captures: list[dict[str, object]] = []
    for state in EXPECTED_STATES:
        state_id, workspace, actor, surface, expected_state = state
        for width, height in ((1440, 900), (1280, 800)):
            key = f"{state_id}__{width}x{height}.png"
            reference_payload = f"reference:{key}".encode("ascii")
            candidate_payload = f"candidate:{key}".encode("ascii")
            diff_payload = f"diff:{key}".encode("ascii")
            (reference_root / key).parent.mkdir(parents=True, exist_ok=True)
            (reference_root / key).write_bytes(reference_payload)
            (candidate_root / key).parent.mkdir(parents=True, exist_ok=True)
            (candidate_root / key).write_bytes(candidate_payload)
            (diff_root / key).parent.mkdir(parents=True, exist_ok=True)
            (diff_root / key).write_bytes(diff_payload)
            reference_environment = _environment(
                width,
                height,
                kind="reference",
                build_sha256=HASH_REFERENCE_BUILD,
            )
            candidate_environment = _environment(
                width,
                height,
                kind="candidate",
                build_sha256=source["build_sha256"],
            )
            reference_captures.append(
                {
                    "state_id": state_id,
                    "viewport": {"width": width, "height": height},
                    "environment": reference_environment,
                    "screenshot_relative_path": key,
                    "screenshot_sha256": _sha256(reference_payload),
                }
            )
            scenario_id = f"V0B-P1-{state_id}-{width}X{height}"
            candidate_captures.append(
                {
                    "state_id": state_id,
                    "workspace_alias": workspace,
                    "actor_alias": actor,
                    "surface": surface,
                    "expected_state": expected_state,
                    "runtime_binding": {
                        "workspace_name": workspace,
                        "active_actor_subject_id": (
                            "user01" if actor == "REF-COLLABORATOR" else "admin"
                        ),
                        "active_page_title": "synthetic page",
                    },
                    "viewport": {"width": width, "height": height},
                    "reference_screenshot_relative_path": key,
                    "reference_screenshot_sha256": _sha256(reference_payload),
                    "candidate_screenshot_relative_path": key,
                    "candidate_screenshot_sha256": _sha256(candidate_payload),
                    "diff_relative_path": key,
                    "diff_sha256": _sha256(diff_payload),
                    "candidate_environment": candidate_environment,
                    "candidate_environment_sha256": validate_capture_environment(
                        candidate_environment
                    )["environment_sha256"],
                    "capture_matte": None,
                    "capture_provenance": {
                        "mode": "product_render_only",
                        "browser_screenshot_before_reference_pixel_read": True,
                        "reference_pixels_injected": False,
                        "capture_only_visual_mutation": False,
                        "post_capture_reference_matte": False,
                    },
                    "mask_manifest": _mask(scenario_id, width, height),
                    "exact_pixel_report": _report(
                        scenario_id=scenario_id,
                        width=width,
                        height=height,
                        reference_payload=reference_payload,
                        candidate_payload=candidate_payload,
                        diff_payload=diff_payload,
                    ),
                    "accessibility": {
                        "status": "pass",
                        "focus": {
                            "active_alias": "PAGE-CANVAS",
                            "name": "synthetic page",
                            "role": "main",
                            "state": "focused",
                            "sequence": ["PAGE-CANVAS"],
                        },
                        "keyboard_path": ["Tab", "Enter"],
                        "keyboard_execution": {
                            "kind": "focus_navigation_probe",
                            "status": "pass",
                            "sequence": ["Tab", "Shift+Tab"],
                            "steps": [
                                {"key": "Tab", "name": "next", "role": "button", "state": "visible,focused"},
                                {"key": "Shift+Tab", "name": "synthetic page", "role": "main", "state": "visible,focused"},
                            ],
                        },
                        "name_role_state": [
                            {
                                "alias": "PAGE-CANVAS",
                                "name": "synthetic page",
                                "role": "main",
                                "state": "visible",
                            }
                        ],
                        "live_regions": {"expected": False, "nodes": []},
                        "scroll_geometry": {
                            "x": 0,
                            "y": 0,
                            "width": width,
                            "height": height,
                            "scroll_x": 0,
                            "scroll_y": 0,
                        },
                    },
                    "interaction_outcome": {
                        "status": "pass",
                        "expected_state": expected_state,
                        "observation": "unit verifier fixture",
                    },
                    "captured_at": TIME,
                    "raw_identifier_persisted": False,
                    "personal_content_visible": False,
                }
            )
    reference_receipt = _write_json(
        root / "reference-receipt.json", {"captures": reference_captures}
    )
    cleanup: dict[str, object] = {
        "status": "complete",
        "synthetic_only": True,
        "active_test_grant_count": 0,
        "active_test_member_count": 0,
        "active_fixture_root_count": 0,
        "component_cleanup_verified": True,
        "browser_closed": True,
        "permanent_delete_used": False,
        "completed_at": TIME,
    }
    cleanup["cleanup_receipt_sha256"] = canonical_sha256(cleanup)
    receipt = {
        "schema_version": 1,
        "contract": CONTRACT,
        "packet_id": "V0B-P1",
        "receipt_id": "V0B-P1-L6-FINAL-CANDIDATE-UNIT-TEST",
        "recorded_at": TIME,
        "reference_receipt": {
            "path": REFERENCE_REPO_PATH,
            "sha256": _sha256(reference_receipt.read_bytes()),
            "capture_count": 36,
            "capture_state_count": 18,
        },
        "release_candidate": {
            "source_build_sha256": source["build_sha256"],
            "source_files": source["files"],
        },
        "runtime_candidate": {
            "source_build_sha256": runtime_source["build_sha256"],
            "source_files": runtime_source["files"],
        },
        "core_25": {
            "status": "pass",
            "source_build_sha256": source["build_sha256"],
            "collected_test_count": 21,
            "passed_test_count": 21,
            "failed_test_count": 0,
            "started_at": TIME,
            "ended_at": TIME,
            "scenario_results": [
                {"scenario_id": scenario_id, "status": "pass"}
                for scenario_id in _scenario_ids()
            ],
        },
        "performance": {
            "scenario_id": "UI-PERF-001",
            "status": "pass",
            "source_build_sha256": source["build_sha256"],
            "observed_p95_ms": 1000,
            "approved_budget_ms": 1370,
            "quiet_host_verified": True,
            "started_at": TIME,
            "ended_at": TIME,
        },
        "run": {
            "run_id": "v0b-p1-unit-test",
            "browser": "chrome_two_independent_ephemeral_contexts",
            "chrome_major_version": "151",
            "capture_pipeline": current_capture_pipeline_binding(),
            "candidate_screenshot_root": CANDIDATE_SCREENSHOT_REPO_ROOT,
            "diff_root": DIFF_REPO_ROOT,
            "started_at": TIME,
            "ended_at": TIME,
        },
        "component_journeys": {
            "I0-P2": {
                "status": "pass",
                "test_root_moved_to_trash": True,
                "synthetic_member_absent_after_cleanup": True,
                "browser": {"browser_closed": True},
            },
            **{
                name: {
                    "status": "pass",
                    "test_page_moved_to_trash": True,
                    "synthetic_member_absent_after_cleanup": True,
                    "browser": {"browser_closed": True},
                }
                for name in ("C0-P2", "E1-P2", "F0-P5")
            },
        },
        "captures": candidate_captures,
        "cleanup": cleanup,
        "claims": {
            "V0R_P1_gate_passed": True,
            "final_release_candidate_core_25_rerun": True,
            "all_adopted_multiuser_states_captured": True,
            "zero_unmasked_RGBA_difference": True,
            "accessibility_evidence_complete": True,
            "PIX_001_closed": True,
            "A11Y_001_closed": True,
            "UI_CORE_25_closed": True,
            "V0B_P1_gate_passed": True,
            "production_changed": False,
            "second_brain_changed": False,
            "product_complete": False,
            "active_shared_goal_complete": False,
        },
    }
    receipt_path = _write_json(root / "receipt.json", receipt)
    return receipt_path, reference_receipt, reference_root, candidate_root, diff_root


@pytest.fixture
def verifier_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt.verify_reference_receipt",
        lambda *args, **kwargs: {"status": "pass"},
    )

    def compare(
        reference_png: bytes,
        candidate_png: bytes,
        *,
        scenario_id: str,
        mask_manifest: object,
        reference_environment: object,
        candidate_environment: object,
    ) -> SimpleNamespace:
        _, state_id, viewport = scenario_id.rsplit("-", 2)
        del state_id, mask_manifest, reference_environment, candidate_environment
        width, height = (int(value) for value in viewport.split("X"))
        diff = f"diff:{scenario_id.removeprefix('V0B-P1-').replace('-', '__', 1).replace('-', '')}.png"
        # The fixture names are easier and safer to derive from the candidate payload.
        key = candidate_png.decode("ascii").removeprefix("candidate:")
        diff_payload = f"diff:{key}".encode("ascii")
        return SimpleNamespace(
            report=_report(
                scenario_id=scenario_id,
                width=width,
                height=height,
                reference_payload=reference_png,
                candidate_payload=candidate_png,
                diff_payload=diff_payload,
            ),
            diff_png=diff_payload,
        )

    monkeypatch.setattr(
        "code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt.compare_pngs_exact",
        compare,
    )


@pytest.mark.unit
def test_v0b_p1_receipt_requires_core25_perf_all_36_exact_and_accessibility(
    tmp_path: Path,
    verifier_dependencies: None,
) -> None:
    receipt, reference, reference_root, candidate_root, diff_root = _fixture(tmp_path)

    report = verify(
        receipt,
        reference_receipt_path=reference,
        reference_screenshot_root=reference_root,
        candidate_screenshot_root=candidate_root,
        diff_root=diff_root,
    )

    assert report["status"] == "pass"
    assert report["error_count"] == 0
    assert report["core_scenario_count"] == 25
    assert report["capture_count"] == 36
    assert report["capture_state_count"] == 18
    assert report["viewport_count"] == 2
    assert report["exact_pixel_pass_count"] == 36
    assert report["accessibility_pass_count"] == 36
    assert report["gate_passed"] is True
    assert report["product_complete"] is False


@pytest.mark.unit
def test_v0b_visual_audit_mode_rechecks_all_evidence_but_does_not_close_runtime_gate(
    tmp_path: Path,
    verifier_dependencies: None,
) -> None:
    receipt, reference, reference_root, candidate_root, diff_root = _fixture(tmp_path)
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["runtime_candidate"]["source_build_sha256"] = "sha256:" + "0" * 64
    _write_json(receipt, value)

    report = verify(
        receipt,
        reference_receipt_path=reference,
        reference_screenshot_root=reference_root,
        candidate_screenshot_root=candidate_root,
        diff_root=diff_root,
        allow_receipt_bound_runtime_for_visual_audit=True,
    )

    assert report["status"] == "pass"
    assert report["error_count"] == 0
    assert report["errors"] == []
    assert report["exact_pixel_pass_count"] == 36
    assert report["visual_similarity_pass_count"] == 36
    assert report["accessibility_pass_count"] == 36
    assert report["runtime_compatibility_required"] is True
    assert report["gate_passed"] is False


@pytest.mark.unit
def test_v0b_p1_receipt_accepts_inexact_candidates_only_with_all_36_visual_reports(
    tmp_path: Path,
    verifier_dependencies: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt, reference, reference_root, candidate_root, diff_root = _fixture(tmp_path)

    def failed_exact(
        reference_png: bytes,
        candidate_png: bytes,
        *,
        scenario_id: str,
        mask_manifest: object,
        reference_environment: object,
        candidate_environment: object,
    ) -> SimpleNamespace:
        del mask_manifest, reference_environment, candidate_environment
        width, height = (int(value) for value in scenario_id.rsplit("-", 1)[1].split("X"))
        key = candidate_png.decode("ascii").removeprefix("candidate:")
        diff_payload = f"diff:{key}".encode("ascii")
        report = _report(
            scenario_id=scenario_id,
            width=width,
            height=height,
            reference_payload=reference_png,
            candidate_payload=candidate_png,
            diff_payload=diff_payload,
        )
        report.update(
            status="fail",
            differing_pixel_count=1,
            total_differing_pixel_count=1,
            difference_bounding_boxes=[{"x": 0, "y": 0, "width": 1, "height": 1}],
        )
        return SimpleNamespace(report=report, diff_png=diff_payload)

    def passed_visual(
        _reference_png: bytes,
        _candidate_png: bytes,
        *,
        state_id: str,
        scenario_id: str,
        **_kwargs: object,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            report={"status": "pass", "state_id": state_id, "scenario_id": scenario_id}
        )

    monkeypatch.setattr(
        "code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt.compare_pngs_exact",
        failed_exact,
    )
    monkeypatch.setattr(
        "code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt.compare_pngs_similarity",
        passed_visual,
    )
    monkeypatch.setattr(
        "code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt.validate_visual_similarity_receipt",
        lambda value: value,
    )

    value = json.loads(receipt.read_text(encoding="utf-8"))
    for capture in value["captures"]:
        key = capture["candidate_screenshot_relative_path"]
        reference_payload = (reference_root / key).read_bytes()
        candidate_payload = (candidate_root / key).read_bytes()
        scenario_id = capture["exact_pixel_report"]["scenario_id"]
        width = capture["viewport"]["width"]
        height = capture["viewport"]["height"]
        diff_payload = (diff_root / key).read_bytes()
        failed = _report(
            scenario_id=scenario_id,
            width=width,
            height=height,
            reference_payload=reference_payload,
            candidate_payload=candidate_payload,
            diff_payload=diff_payload,
        )
        failed.update(
            status="fail",
            differing_pixel_count=1,
            total_differing_pixel_count=1,
            difference_bounding_boxes=[{"x": 0, "y": 0, "width": 1, "height": 1}],
        )
        capture["exact_pixel_report"] = failed
        capture["visual_similarity_report"] = {
            "status": "pass",
            "state_id": capture["state_id"],
            "scenario_id": scenario_id,
        }
    value["claims"]["zero_unmasked_RGBA_difference"] = False
    value["claims"]["strict_visual_similarity_passed"] = True
    _write_json(receipt, value)

    report = verify(
        receipt,
        reference_receipt_path=reference,
        reference_screenshot_root=reference_root,
        candidate_screenshot_root=candidate_root,
        diff_root=diff_root,
    )

    assert report["status"] == "pass", report
    assert report["exact_pixel_pass_count"] == 0
    assert report["visual_similarity_pass_count"] == 36
    assert report["gate_passed"] is True

    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["captures"][0]["visual_similarity_report"]["status"] = "fail"
    _write_json(receipt, value)
    rejected = verify(
        receipt,
        reference_receipt_path=reference,
        reference_screenshot_root=reference_root,
        candidate_screenshot_root=candidate_root,
        diff_root=diff_root,
    )
    assert rejected["status"] == "fail"
    assert any(error["code"] == "VISUAL_SIMILARITY_GATE" for error in rejected["errors"])


@pytest.mark.unit
def test_v0b_p1_receipt_rejects_declared_lossy_reference_transport(
    tmp_path: Path,
    verifier_dependencies: None,
) -> None:
    receipt, reference, reference_root, candidate_root, diff_root = _fixture(tmp_path)
    reference_value = json.loads(reference.read_text(encoding="utf-8"))
    reference_value["capture_transport"] = {
        "browser_capture_returned_codec": "jpeg",
        "stored_codec": "png_srgb_rgba8",
        "lossless_storage_after_decode": True,
        "source_encoding_lossless": False,
        "zero_RGBA_equivalence_claimed": False,
    }
    _write_json(reference, reference_value)
    receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
    receipt_value["reference_receipt"]["sha256"] = _sha256(reference.read_bytes())
    _write_json(receipt, receipt_value)

    report = verify(
        receipt,
        reference_receipt_path=reference,
        reference_screenshot_root=reference_root,
        candidate_screenshot_root=candidate_root,
        diff_root=diff_root,
    )

    assert report["status"] == "fail"
    assert any(
        error["code"] == "REFERENCE_CAPTURE_TRANSPORT"
        for error in report["errors"]
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate", "error_code"),
    [
        (lambda value: value["captures"].pop(), "CAPTURE_DENOMINATOR"),
        (
            lambda value: value["captures"][0]["exact_pixel_report"].__setitem__(
                "differing_pixel_count", 1
            ),
            "EXACT_PIXEL_RECOMPUTE",
        ),
        (
            lambda value: value["captures"][0]["accessibility"].__setitem__(
                "keyboard_path", []
            ),
            "ACCESSIBILITY_GATE",
        ),
        (
            lambda value: value["captures"][0]["accessibility"]["keyboard_execution"].__setitem__(
                "status", "fail"
            ),
            "ACCESSIBILITY_GATE",
        ),
        (
            lambda value: value["performance"].__setitem__("quiet_host_verified", False),
            "PERFORMANCE_GATE",
        ),
        (
            lambda value: value["captures"][0]["runtime_binding"].__setitem__(
                "workspace_name", "wrong-workspace"
            ),
            "RUNTIME_BINDING",
        ),
        (
            lambda value: value["runtime_candidate"].__setitem__(
                "source_build_sha256", "sha256:" + "0" * 64
            ),
            "RUNTIME_CANDIDATE_BINDING",
        ),
        (
            lambda value: value["run"]["capture_pipeline"].__setitem__(
                "reference_pixels_available_to_page", True
            ),
            "RUN_BOUNDARY",
        ),
        (
            lambda value: value["run"]["capture_pipeline"]["transitive_files"][0].__setitem__(
                "sha256", "sha256:" + "0" * 64
            ),
            "RUN_BOUNDARY",
        ),
        (
            lambda value: value["captures"][0]["capture_provenance"].__setitem__(
                "reference_pixels_injected", True
            ),
            "CAPTURE_PROVENANCE",
        ),
        (
            lambda value: value["component_journeys"]["E1-P2"].__setitem__(
                "synthetic_member_absent_after_cleanup", False
            ),
            "COMPONENT_JOURNEY_GATE",
        ),
        (
            lambda value: value["claims"].__setitem__("product_complete", True),
            "CLAIMS_GATE",
        ),
    ],
)
def test_v0b_p1_receipt_rejects_missing_pixel_a11y_perf_or_false_completion(
    tmp_path: Path,
    verifier_dependencies: None,
    mutate,
    error_code: str,
) -> None:
    receipt, reference, reference_root, candidate_root, diff_root = _fixture(tmp_path)
    value = json.loads(receipt.read_text(encoding="utf-8"))
    candidate = copy.deepcopy(value)
    mutate(candidate)
    _write_json(receipt, candidate)

    report = verify(
        receipt,
        reference_receipt_path=reference,
        reference_screenshot_root=reference_root,
        candidate_screenshot_root=candidate_root,
        diff_root=diff_root,
    )

    assert report["status"] == "fail"
    assert error_code in {item["code"] for item in report["errors"]}
    assert report["gate_passed"] is False

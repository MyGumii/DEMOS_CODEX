from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.v0b_p1_product_capture import (
    V0BP1ProductCollector,
    capture_pipeline_binding,
)
from code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt import (
    EXPECTED_CAPTURE_KEYS,
    EXPECTED_CRITICAL_REGION_IDS,
    VISUAL_SIMILARITY_POLICY_PATH,
    current_capture_pipeline_binding,
)
from code_assist_v1.tools.notion_parity.visual_similarity import (
    validate_visual_similarity_policy,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_PATH = PROJECT_ROOT / "notion_collaboration" / "v0b_p1_product_capture.py"
DRIVER_PATH = PROJECT_ROOT / "notion_collaboration" / "v0b_p1_browser.py"
IAM_JOURNEY_PATH = PROJECT_ROOT / "notion_collaboration" / "v0b_p1_iam_browser.py"
EDIT_JOURNEY_PATH = PROJECT_ROOT / "notion_collaboration" / "e1_p2_browser.py"
DATABASE_JOURNEY_PATH = PROJECT_ROOT / "notion_collaboration" / "f0_p5_browser.py"
PRODUCT_CSS_PATH = PROJECT_ROOT / "notion_static" / "notion.css"
PRODUCT_JS_PATH = PROJECT_ROOT / "notion_static" / "notion.js"


def _capture_function() -> ast.FunctionDef:
    tree = ast.parse(COLLECTOR_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "V0BP1ProductCollector":
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name == "capture":
                    return member
    raise AssertionError("V0BP1ProductCollector.capture is missing")


@pytest.mark.unit
def test_v0b_driver_uses_the_product_render_only_collector() -> None:
    source = DRIVER_PATH.read_text(encoding="utf-8")
    collector_source = COLLECTOR_PATH.read_text(encoding="utf-8")

    assert "from .v0b_p1_product_capture import" in source
    assert "from .v0b_p1_product_runtime import" in source
    assert "from .v0b_p1_capture import" not in source
    assert "from .v0b_p1_product_runtime import ACCESSIBILITY_SCRIPT" in collector_source
    assert "from .v0b_p1_capture import" not in collector_source
    assert "compare_pngs_similarity" in collector_source
    assert '"visual_similarity_report": visual_similarity.report' in collector_source
    assert "V0BP1ProductCollector(" in source
    assert "V0BP1Collector(" not in source
    assert "\"capture_pipeline\": capture_pipeline_binding()" in source


@pytest.mark.unit
def test_product_collector_captures_before_reading_reference_pixels() -> None:
    function = _capture_function()
    screenshot_lines: list[int] = []
    reference_read_lines: list[int] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "screenshot":
            screenshot_lines.append(node.lineno)
        if (
            node.func.attr == "read_bytes"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "reference_path"
        ):
            reference_read_lines.append(node.lineno)

    assert len(screenshot_lines) == 1
    assert len(reference_read_lines) == 1
    assert screenshot_lines[0] < reference_read_lines[0]


@pytest.mark.unit
def test_keyboard_probe_does_not_overwrite_the_capture_identity_key() -> None:
    source = COLLECTOR_PATH.read_text(encoding="utf-8")

    assert 'key = (state_id, width, height)' in source
    assert 'for keyboard_chord in ("Tab", "Shift+Tab")' in source
    assert 'for key in ("Tab", "Shift+Tab")' not in source


@pytest.mark.unit
def test_product_collector_contains_no_reference_pixel_injection_or_matte() -> None:
    source = COLLECTOR_PATH.read_text(encoding="utf-8")

    forbidden = (
        "base64",
        "data:image",
        "caret-color",
        "createElement('style')",
        "_apply_reference_capture_matte",
        "remainingFullReferenceOverlay",
        "ResidualOverlay",
        "RasterImage",
        "insertAdjacentHTML",
    )
    assert not [token for token in forbidden if token in source]


@pytest.mark.unit
def test_product_capture_path_contains_no_v0b_visual_state_hooks() -> None:
    sources = {
        "iam": IAM_JOURNEY_PATH.read_text(encoding="utf-8"),
        "edit": EDIT_JOURNEY_PATH.read_text(encoding="utf-8"),
        "database": DATABASE_JOURNEY_PATH.read_text(encoding="utf-8"),
        "css": PRODUCT_CSS_PATH.read_text(encoding="utf-8"),
        "js": PRODUCT_JS_PATH.read_text(encoding="utf-8"),
    }
    forbidden = (
        "v0bShareSurfaceLock",
        "v0bRemoteCaretStart",
        "v0bF0Reference",
        "v0b-reference-text-base",
        "v0b-reference-block-base",
        "#v0b-capture-freeze",
        "data-v0b-f0-reference",
        "data-v0b-remote-caret-start",
        "v0b-remaining-full-reference-overlay",
        "v0b-state001-002-004-residual-overlay",
        "v0b-state002-wide-supplemental-overlay",
        "v0b-wide-table-lines-style",
        "v0b-korean-inbox-filter-style",
    )
    violations = {
        name: [token for token in forbidden if token in source]
        for name, source in sources.items()
    }
    assert not {name: tokens for name, tokens in violations.items() if tokens}
    assert "page.iam_role = 'comment'" not in sources["database"]
    assert "renderPageCommentPreview()" not in sources["database"]


@pytest.mark.unit
def test_capture_pipeline_binding_is_independently_reconstructed() -> None:
    binding = capture_pipeline_binding()
    assert binding == current_capture_pipeline_binding()
    assert binding == {
        "contract": "code_assist.notion.internal_collaborative_v0b_p1.product_capture.v5",
        "mode": "product_render_only",
        "collector_path": "code_assist_v1/notion_collaboration/v0b_p1_product_capture.py",
        "collector_sha256": binding["collector_sha256"],
        "browser_driver_path": "code_assist_v1/notion_collaboration/v0b_p1_browser.py",
        "browser_driver_sha256": binding["browser_driver_sha256"],
        "transitive_files": binding["transitive_files"],
        "visual_similarity_policy": binding["visual_similarity_policy"],
        "reference_pixels_available_to_page": False,
        "capture_only_visual_mutation": False,
        "post_capture_reference_matte": False,
    }
    assert {item["path"] for item in binding["transitive_files"]} == {
        "code_assist_v1/tools/notion_parity/exact_pixel.py",
        "code_assist_v1/tools/notion_parity/visual_similarity.py",
        "code_assist_v1/notion_collaboration/approved_dev_pair.py",
        "code_assist_v1/notion_collaboration/v0b_p1_product_runtime.py",
        "code_assist_v1/notion_collaboration/v0b_p1_iam_browser.py",
        "code_assist_v1/notion_collaboration/c0_p2_browser.py",
        "code_assist_v1/notion_collaboration/e1_p2_browser.py",
        "code_assist_v1/notion_collaboration/f0_p5_browser.py",
    }
    assert binding["visual_similarity_policy"] == {
        "path": (
            "code_assist_v1/docs/notion_parity/core_free_web_ui/"
            "internal_collaborative_v0b_p1_visual_similarity_policy.v2.json"
        ),
        "file_sha256": binding["visual_similarity_policy"]["file_sha256"],
        "policy_id": "V0B-P1-STRICT-SIMILARITY-CRITICAL-R2-20260907",
        "policy_sha256": (
            "sha256:8b05efac4c107c7a681ba4e1f124e6784eeaf04cb108e3c2c5e8a783a7b714d0"
        ),
    }


@pytest.mark.unit
def test_production_similarity_policy_has_one_bounded_critical_region_per_state() -> None:
    policy = validate_visual_similarity_policy(
        json.loads(VISUAL_SIMILARITY_POLICY_PATH.read_text(encoding="utf-8")),
        expected_keys=EXPECTED_CAPTURE_KEYS,
    )
    broad_ids = {"APP-CHROME", "PRIMARY-CONTENT", "SIDEBAR"}
    for row in policy["states"]:
        expected = EXPECTED_CRITICAL_REGION_IDS[row["state_id"]]
        regions = {region["id"]: region for region in row["required_regions"]}
        assert set(regions) == broad_ids | {expected}
        rect = regions[expected]["rect"]
        area = rect["width"] * rect["height"]
        viewport_area = row["viewport"]["width"] * row["viewport"]["height"]
        assert 1_000 <= area
        assert area * 100 <= viewport_area * 36


def test_product_collector_public_type_is_distinct_from_legacy_collector() -> None:
    assert V0BP1ProductCollector.__module__.endswith("v0b_p1_product_capture")

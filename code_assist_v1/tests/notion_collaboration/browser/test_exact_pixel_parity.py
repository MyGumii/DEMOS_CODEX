from __future__ import annotations

import pytest

from code_assist_v1.tools.notion_parity.exact_pixel import (
    CAPTURE_ENVIRONMENT_CONTRACT,
    MASK_MANIFEST_CONTRACT,
    compare_pngs_exact,
    sha256_bytes,
)


FROZEN_EPOCH_MS = 1787500800000
FONT_MANIFEST_SHA256 = "sha256:" + "f" * 64


def _html(marker_color: str) -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<style>
  html, body {{ width: 100%; height: 100%; margin: 0; overflow: hidden; background: #ffffff; }}
  *, *::before, *::after {{ animation: none !important; transition: none !important; caret-color: transparent !important; }}
  #shell {{ position: absolute; inset: 0; background: #ffffff; }}
  #sidebar {{ position: absolute; left: 0; top: 0; bottom: 0; width: 240px; background: #f7f7f5; }}
  #topbar {{ position: absolute; left: 240px; right: 0; top: 0; height: 45px; border-bottom: 1px solid #ededeb; }}
  #canvas {{ position: absolute; left: 320px; top: 120px; width: 640px; height: 360px; background: #ffffff; border: 1px solid #e4e4e2; }}
  #block-a {{ position: absolute; left: 24px; top: 24px; width: 320px; height: 32px; background: #ececea; }}
  #block-b {{ position: absolute; left: 24px; top: 72px; width: 480px; height: 16px; background: #f1f1ef; }}
  #marker {{ position: absolute; left: 40px; top: 40px; width: 1px; height: 1px; background: {marker_color}; }}
</style>
</head>
<body>
  <main id="shell" aria-label="SYNTHETIC-PIXEL-SHELL">
    <aside id="sidebar"></aside><header id="topbar"></header>
    <section id="canvas"><div id="block-a"></div><div id="block-b"></div></section>
    <div id="marker"></div>
  </main>
</body>
</html>"""


def _environment(
    *,
    width: int,
    height: int,
    browser_major: str,
    source_kind: str,
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
        "time": {"frozen": True, "epoch_ms": FROZEN_EPOCH_MS},
        "animations": {
            "disabled": True,
            "transition_duration_ms": 0,
            "caret_hidden": True,
        },
        "font_manifest": [
            {
                "family": "Arial",
                "style": "normal",
                "weight": 400,
                "sha256": FONT_MANIFEST_SHA256,
            }
        ],
        "browser": {
            "engine": "chromium",
            "channel": "chrome",
            "major_version": browser_major,
        },
        "capture": {"full_page": False, "omit_background": False},
        "source": {"kind": source_kind, "build_sha256": build_sha256},
    }


def _empty_mask(scenario_id: str, width: int, height: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": MASK_MANIFEST_CONTRACT,
        "scenario_id": scenario_id,
        "image": {"width": width, "height": height},
        "content_independent": True,
        "review": {
            "status": "approved",
            "receipt_id": "V0A-P1-BROWSER-MASK-REVIEW",
            "reviewer": "synthetic-contract-reviewer",
            "reviewed_at": "2026-08-23T16:25:32Z",
        },
        "masks": [],
    }


@pytest.mark.integration
def test_v0a_p1_exact_pixel_browser_contract_at_both_adopted_viewports() -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            browser_major = browser.version.split(".", 1)[0]
            for width, height in ((1440, 900), (1280, 800)):
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                    locale="ko-KR",
                    timezone_id="Asia/Seoul",
                    color_scheme="light",
                    reduced_motion="reduce",
                )
                try:
                    page = context.new_page()
                    page.add_init_script(
                        f"Date.now = () => {FROZEN_EPOCH_MS};"
                    )
                    reference_html = _html("#202124")
                    page.set_content(reference_html, wait_until="load")
                    reference = page.screenshot(
                        type="png", full_page=False, animations="disabled"
                    )
                    page.set_content(reference_html, wait_until="load")
                    candidate = page.screenshot(
                        type="png", full_page=False, animations="disabled"
                    )
                    scenario_id = f"V0A-P1-BROWSER-{width}X{height}"
                    mask = _empty_mask(scenario_id, width, height)
                    reference_env = _environment(
                        width=width,
                        height=height,
                        browser_major=browser_major,
                        source_kind="reference",
                        build_sha256=sha256_bytes(reference_html.encode("utf-8")),
                    )
                    candidate_env = _environment(
                        width=width,
                        height=height,
                        browser_major=browser_major,
                        source_kind="candidate",
                        build_sha256=sha256_bytes(reference_html.encode("utf-8")),
                    )
                    identical = compare_pngs_exact(
                        reference,
                        candidate,
                        scenario_id=scenario_id,
                        mask_manifest=mask,
                        reference_environment=reference_env,
                        candidate_environment=candidate_env,
                    )
                    assert identical.report["status"] == "pass"
                    assert identical.report["differing_pixel_count"] == 0

                    changed_html = _html("#202125")
                    page.set_content(changed_html, wait_until="load")
                    changed = page.screenshot(
                        type="png", full_page=False, animations="disabled"
                    )
                    changed_env = _environment(
                        width=width,
                        height=height,
                        browser_major=browser_major,
                        source_kind="candidate",
                        build_sha256=sha256_bytes(changed_html.encode("utf-8")),
                    )
                    difference = compare_pngs_exact(
                        reference,
                        changed,
                        scenario_id=scenario_id,
                        mask_manifest=mask,
                        reference_environment=reference_env,
                        candidate_environment=changed_env,
                    )
                    assert difference.report["status"] == "fail"
                    assert difference.report["differing_pixel_count"] == 1
                    assert difference.report["difference_bounding_boxes"] == [
                        {"x": 40, "y": 40, "width": 1, "height": 1}
                    ]
                    reports.append(identical.report)
                finally:
                    context.close()
        finally:
            browser.close()
    assert [report["viewport"] for report in reports] == [
        {"width": 1440, "height": 900},
        {"width": 1280, "height": 800},
    ]

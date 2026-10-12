from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import math
from pathlib import Path
import time

import pytest

from code_assist_v1.tools.verify_notion_core_free_ui_performance_budget import (
    load_active_performance_budget,
)
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import (
    PROFILE_ID,
    _canonical_publication_state,
    _isolated_server,
    _open_publication_preview,
    _scenario_action_hash,
    _ui_source_build_sha256,
    _wait_for_publication_action,
)


_METRIC_NAMES = (
    "create_page_ready_ms",
    "editor_input_ack_ms",
    "page_ready_ms",
    "reload_ready_ms",
    "save_ready_ms",
    "search_open_ms",
    "search_result_ms",
    "second_brain_preview_ready_ms",
    "shell_ready_ms",
)
_SAMPLES_PER_ROOT = 3


def _elapsed_ms(action: Callable[[], None]) -> float:
    started = time.perf_counter_ns()
    action()
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    if not math.isfinite(elapsed) or elapsed < 0:
        raise AssertionError("performance measurement is not finite and nonnegative")
    return round(elapsed, 3)


def _nearest_rank(values: list[float], percentile: int) -> float:
    if not values or percentile < 1 or percentile > 100:
        raise ValueError("nearest-rank percentile input is invalid")
    ordered = sorted(values)
    rank = math.ceil((percentile / 100) * len(ordered))
    return ordered[max(0, rank - 1)]


def _distribution(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    return {
        "max": ordered[-1],
        "min": ordered[0],
        "p50": _nearest_rank(ordered, 50),
        "p95": _nearest_rank(ordered, 95),
        "p99": _nearest_rank(ordered, 99),
        "sample_count": len(ordered),
    }


def _exercise_performance_sample(
    context: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
    sample_index: int,
) -> tuple[dict[str, float], dict[str, int | bool]]:
    from playwright.sync_api import expect

    page = context.new_page()
    external_requests: list[str] = []
    internal_response_failures: list[tuple[str, int]] = []

    def record_request(request: object) -> None:
        if not request.url.startswith(origin):
            external_requests.append(request.url)

    page.on("request", record_request)

    def record_response(response: object) -> None:
        if response.url.startswith(origin) and response.status >= 400:
            path = response.url[len(origin) :].split("?", 1)[0]
            internal_response_failures.append((path, response.status))

    page.on("response", record_response)
    paragraph_value = f"UI-PERF-001 합성 문단 {sample_index}"

    def reach_shell() -> None:
        page.goto(origin, wait_until="domcontentloaded")
        page.get_by_label("사번", exact=True).fill(user_id)
        page.get_by_label("비밀번호", exact=True).fill(password)
        page.get_by_role("button", name="로그인", exact=True).click()
        home_tab = page.get_by_role("tab", name="홈", exact=True)
        expect(home_tab).to_be_visible(timeout=45_000)
        expect(
            page.get_by_role("button", name="Second Brain", exact=True)
        ).to_be_visible(timeout=45_000)
        if home_tab.get_attribute("aria-selected") != "true":
            home_tab.click()
        expect(page.locator("#homeView")).to_be_visible(timeout=45_000)
        expect(page.locator("body[data-notion-ready='true']")).to_have_count(
            1, timeout=45_000
        )

    metrics = {"shell_ready_ms": _elapsed_ms(reach_shell)}

    title = page.locator("#pageTitle")
    page_rows = page.locator("#ownPages .page-row")
    expect(page_rows.first).to_be_visible(timeout=45_000)
    before_page_ids = page_rows.evaluate_all(
        "rows => rows.map(row => row.dataset.pageId).filter(Boolean)"
    )
    if not before_page_ids or len(set(before_page_ids)) != len(before_page_ids):
        raise AssertionError("performance fixture page identities are invalid")
    existing_page_id = before_page_ids[0]

    def create_independent_page() -> None:
        page.locator("#sidebarNewBtn").click()
        new_page_menu = page.locator("#newPageMenu")
        expect(new_page_menu).to_be_visible(timeout=45_000)
        new_page_menu.locator('[data-new-page-type="page"]').click()
        expect(title).to_be_visible(timeout=45_000)
        expect(title).to_be_focused(timeout=45_000)
        expect(page.locator("#status")).to_have_text(
            "페이지 생성됨", timeout=45_000
        )
        expect(page_rows).to_have_count(len(before_page_ids) + 1, timeout=45_000)

    metrics["create_page_ready_ms"] = _elapsed_ms(create_independent_page)
    after_page_ids = page_rows.evaluate_all(
        "rows => rows.map(row => row.dataset.pageId).filter(Boolean)"
    )
    created_page_ids = set(after_page_ids) - set(before_page_ids)
    if len(after_page_ids) != len(before_page_ids) + 1:
        raise AssertionError("page creation did not add exactly one tree row")
    if len(created_page_ids) != 1:
        raise AssertionError("page creation did not produce one new identity")
    created_page_id = created_page_ids.pop()
    if created_page_id == existing_page_id:
        raise AssertionError("page creation reused the source page identity")
    active_rows = page.locator("#ownPages .page-row.active")
    expect(active_rows).to_have_count(1, timeout=45_000)
    if active_rows.get_attribute("data-page-id") != created_page_id:
        raise AssertionError("newly created page is not the active tree row")
    created_title = f"UI-PERF-001 생성 페이지 {sample_index}"
    title.fill(created_title)
    expect(page.locator("#status")).to_have_text("저장됨", timeout=45_000)
    expect(page_rows.filter(has_text=created_title)).to_have_count(
        1, timeout=45_000
    )

    page_row = page.locator(
        f'#ownPages .page-row[data-page-id="{existing_page_id}"]'
    )
    expect(page_row).to_be_visible(timeout=45_000)

    def open_page() -> None:
        page_row.click()
        expect(title).to_be_visible(timeout=45_000)
        expect(page.locator("#status")).to_have_text("Ready", timeout=45_000)

    metrics["page_ready_ms"] = _elapsed_ms(open_page)
    title_value = title.input_value()
    if not title_value.strip():
        raise AssertionError("performance fixture page title is empty")
    editor = page.locator(".block-editor[contenteditable='true']").first
    expect(editor).to_be_visible(timeout=45_000)

    def acknowledge_editor_input() -> None:
        editor.fill(paragraph_value)
        expect(editor).to_have_text(paragraph_value, timeout=45_000)

    metrics["editor_input_ack_ms"] = _elapsed_ms(acknowledge_editor_input)
    # The input-ack metric stops at the DOM acknowledgement.  Let that edit
    # reach one stable server revision before timing the next save so the
    # measurement does not intentionally overlap two autosave generations.
    try:
        expect(page.locator("#status")).to_have_text("저장됨", timeout=45_000)
    except AssertionError as error:
        raise AssertionError(
            "initial performance edit did not reach a stable revision; "
            f"internal_failures={internal_response_failures!r}"
        ) from error
    saved_paragraph = paragraph_value + " 저장"

    def save_page() -> None:
        editor.fill(saved_paragraph)
        expect(page.locator("#status")).to_have_text("저장됨", timeout=45_000)

    metrics["save_ready_ms"] = _elapsed_ms(save_page)

    def reload_page() -> None:
        page.reload(wait_until="domcontentloaded")
        expect(title).to_have_value(title_value, timeout=45_000)
        restored_editor = page.locator(
            ".block-editor[contenteditable='true']"
        ).first
        expect(restored_editor).to_have_text(saved_paragraph, timeout=45_000)

    metrics["reload_ready_ms"] = _elapsed_ms(reload_page)
    search_trigger = page.get_by_role("button", name="검색", exact=True)
    search_input = page.locator("#globalSearchInput")
    search_overlay = page.locator("#searchOverlay")

    def open_search() -> None:
        search_trigger.click()
        expect(search_overlay).to_be_visible(timeout=45_000)
        expect(search_input).to_be_focused(timeout=45_000)

    metrics["search_open_ms"] = _elapsed_ms(open_search)

    def find_page() -> None:
        search_input.fill(title_value)
        result = page.locator(".search-result-row").filter(
            has_text=title_value
        ).first
        expect(result).to_be_visible(timeout=45_000)

    metrics["search_result_ms"] = _elapsed_ms(find_page)
    search_input.press("Escape")
    expect(search_overlay).to_be_hidden(timeout=45_000)
    before_publication = _canonical_publication_state(data_root)
    publication_action: dict[str, str] = {}

    def open_preview() -> None:
        publication_action["label"] = _open_publication_preview(page)

    metrics["second_brain_preview_ready_ms"] = _elapsed_ms(open_preview)
    _wait_for_publication_action(page, "취소").click()
    expect(page.locator("#modalBg")).to_be_hidden(timeout=45_000)
    after_publication = _canonical_publication_state(data_root)
    page.close()

    assert tuple(sorted(metrics)) == _METRIC_NAMES
    assert external_requests == []
    assert publication_action["label"] in {"게시", "승인 후 업로드"}
    state = {
        "created_page_count_delta": 1,
        "external_request_count": len(external_requests),
        "independent_page_created": True,
        "page_restored": True,
        "publication_item_delta": (
            after_publication["items"] - before_publication["items"]
        ),
        "publication_revision_delta": (
            after_publication["revisions"] - before_publication["revisions"]
        ),
        "synthetic_only": True,
    }
    return metrics, state


def _prepare_performance_page(
    context: object,
    origin: str,
    user_id: str,
    password: str,
) -> None:
    from playwright.sync_api import expect

    page = context.new_page()
    try:
        page.goto(origin, wait_until="domcontentloaded")
        page.get_by_label("사번", exact=True).fill(user_id)
        page.get_by_label("비밀번호", exact=True).fill(password)
        page.get_by_role("button", name="로그인", exact=True).click()
        expect(page.get_by_role("tab", name="홈", exact=True)).to_be_visible(
            timeout=45_000
        )
        expect(
            page.get_by_role("button", name="Second Brain", exact=True)
        ).to_be_visible(timeout=45_000)
        expect(page.locator("#homeView")).to_be_visible(timeout=45_000)
        expect(page.locator("body[data-notion-ready='true']")).to_have_count(
            1, timeout=45_000
        )
        page.get_by_role("button", name="새 페이지", exact=True).click()
        new_page_menu = page.locator("#newPageMenu")
        expect(new_page_menu).to_be_visible(timeout=45_000)
        new_page_menu.locator('[data-new-page-type="page"]').click()
        title = page.locator("#pageTitle")
        expect(title).to_be_visible(timeout=45_000)
        expect(page.locator("#status")).to_have_text("페이지 생성됨", timeout=45_000)
    finally:
        page.close()


def _run_performance_root(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            samples: list[dict[str, float]] = []
            states: list[dict[str, int | bool]] = []
            setup_context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                _prepare_performance_page(
                    setup_context,
                    origin,
                    user_id,
                    password,
                )
            finally:
                setup_context.close()
            for sample_index in range(_SAMPLES_PER_ROOT):
                context = browser.new_context(
                    locale="ko-KR",
                    viewport={"height": 900, "width": 1440},
                )
                try:
                    metrics, state = _exercise_performance_sample(
                        context,
                        origin,
                        user_id,
                        password,
                        data_root,
                        sample_index,
                    )
                    samples.append(metrics)
                    states.append(state)
                finally:
                    context.close()
            return {
                "browser_version": str(browser.version),
                "samples": samples,
                "states": states,
            }
        finally:
            browser.close()


def _run_ui_perf_001_measurements(
    playwright: object,
    roots: tuple[Path, Path],
) -> dict[str, object]:
    root_reports = [_run_performance_root(playwright, root) for root in roots]
    browser_versions = {
        str(root_report["browser_version"]) for root_report in root_reports
    }
    if len(browser_versions) != 1:
        raise AssertionError("performance roots used different Chrome versions")

    metric_samples: dict[str, list[float]] = {name: [] for name in _METRIC_NAMES}
    states: list[dict[str, int | bool]] = []
    for root_report in root_reports:
        for sample in root_report["samples"]:
            for name in _METRIC_NAMES:
                metric_samples[name].append(float(sample[name]))
        states.extend(root_report["states"])

    expected_state = {
        "created_page_count_delta": 1,
        "external_request_count": 0,
        "independent_page_created": True,
        "page_restored": True,
        "publication_item_delta": 0,
        "publication_revision_delta": 0,
        "synthetic_only": True,
    }
    if any(state != expected_state for state in states):
        raise AssertionError("performance journey state or isolation contract drifted")

    report = {
        "action_log_sha256": _scenario_action_hash("UI-PERF-001"),
        "browser": {
            "family": "chrome",
            "version": browser_versions.pop(),
        },
        "contract": "code_assist.notion.core_free_ui.performance_measurement.v1",
        "fixture_alias": "UI-PARITY-RUN-EDITOR",
        "measurement_policy": "precalibration_measurement_only",
        "metrics": {
            name: _distribution(metric_samples[name]) for name in _METRIC_NAMES
        },
        "profile": PROFILE_ID,
        "root_count": len(roots),
        "sample_count": len(states),
        "samples_per_root": _SAMPLES_PER_ROOT,
        "scenario_id": "UI-PERF-001",
        "source_build_sha256": _ui_source_build_sha256(),
        "state": expected_state,
        "unit": "ms",
    }
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
    for root in roots:
        assert str(root) not in encoded
    return report


def _report_sha256(report: dict[str, object]) -> str:
    encoded = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@pytest.mark.integration
def test_ui_perf_001_measurements_are_isolated_bounded_and_precalibration_only(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    roots = (tmp_path / "run-a", tmp_path / "run-b")
    with sync_playwright() as playwright:
        report = _run_ui_perf_001_measurements(playwright, roots)

    assert report["contract"] == (
        "code_assist.notion.core_free_ui.performance_measurement.v1"
    )
    assert report["measurement_policy"] == "precalibration_measurement_only"
    assert report["root_count"] == 2
    assert report["samples_per_root"] == _SAMPLES_PER_ROOT
    assert report["sample_count"] == 2 * _SAMPLES_PER_ROOT
    assert tuple(sorted(report["metrics"])) == _METRIC_NAMES
    for distribution in report["metrics"].values():
        assert distribution["sample_count"] == 2 * _SAMPLES_PER_ROOT
        assert 0 <= distribution["min"] <= distribution["p50"]
        assert distribution["p50"] <= distribution["p95"]
        assert distribution["p95"] <= distribution["p99"]
        assert distribution["p99"] <= distribution["max"]
    budgets = load_active_performance_budget()
    assert set(report["metrics"]) - set(budgets) == {"create_page_ready_ms"}
    for metric_name, budget_ms in budgets.items():
        assert report["metrics"][metric_name]["p95"] <= budget_ms, (
            f"{metric_name} p95 exceeded the approved isolated-local budget: "
            f"{report['metrics'][metric_name]['p95']} > {budget_ms}"
        )
    assert report["state"] == {
        "created_page_count_delta": 1,
        "external_request_count": 0,
        "independent_page_created": True,
        "page_restored": True,
        "publication_item_delta": 0,
        "publication_revision_delta": 0,
        "synthetic_only": True,
    }
    assert report["action_log_sha256"].startswith("sha256:")
    assert report["source_build_sha256"].startswith("sha256:")
    assert _report_sha256(report).startswith("sha256:")

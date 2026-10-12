from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterator

import pytest

from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import (
    PROJECT_ROOT,
    _canonical_publication_state,
    _free_port,
    _isolated_server,
    _open_publication_preview,
    _wait_for_health,
    _wait_for_publication_action,
)
from code_assist_v1.tools.notion_parity.ui_pilot import (
    PilotDenied,
    authorize_isolated_pilot,
    build_pilot_report,
    create_recovery_snapshot,
    initialize_synthetic_pilot_root,
    restore_recovery_snapshot,
)


_BASELINE_BODY = "UI-PILOT 합성 기준 문단"
_CANARY_BODY = "UI-PILOT 합성 canary 문단"


def _readiness() -> dict[str, bool]:
    return {
        "backup_ready": True,
        "browser_suite_green": True,
        "redaction_ready": True,
        "restore_ready": True,
        "scenario_contract_valid": True,
        "second_brain_gate_green": True,
    }


@contextmanager
def _serve_existing(root: Path) -> Iterator[str]:
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    command = [
        sys.executable,
        "-m",
        "code_assist_v1.tools.notion_parity.ui_local_fixture",
        "serve",
        "--data-root",
        os.fspath(root),
        "--port",
        str(port),
        "--repository-root",
        os.fspath(PROJECT_ROOT),
        "--test-mode-confirmed",
    ]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.fspath(PROJECT_ROOT)
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=(
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32"
            else 0
        ),
    )
    try:
        _wait_for_health(origin, process)
        yield origin
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def _login(page: object, origin: str, user_id: str, password: str) -> None:
    from playwright.sync_api import expect

    page.goto(origin, wait_until="domcontentloaded")
    page.get_by_label("사번", exact=True).fill(user_id)
    page.get_by_label("비밀번호", exact=True).fill(password)
    page.get_by_role("button", name="로그인", exact=True).click()
    expect(page.get_by_role("tab", name="홈", exact=True)).to_be_visible(
        timeout=45_000
    )


def _save_and_reload(page: object, body: str) -> None:
    from playwright.sync_api import expect

    editor = page.locator(".block-editor[contenteditable='true']").first
    expect(editor).to_be_visible(timeout=45_000)
    editor.fill(body)
    # The production editor deliberately debounces atomic save. Waiting past
    # the debounce is part of this recovery journey, not a performance budget.
    page.wait_for_timeout(1_800)
    expect(page.locator("#status")).to_have_text("저장됨", timeout=45_000)
    page.reload(wait_until="domcontentloaded")
    expect(
        page.locator(".block-editor[contenteditable='true']").first
    ).to_have_text(body, timeout=45_000)


def _open_only_page(page: object) -> None:
    from playwright.sync_api import expect

    page.get_by_role("tab", name="홈", exact=True).click()
    expect(page.locator("#homeView")).to_be_visible(timeout=45_000)
    row = page.locator("#ownPages .page-row").first
    expect(row).to_be_visible(timeout=45_000)
    row.click()
    expect(page.locator("#pageTitle")).to_be_visible(timeout=45_000)


def _report_sha256(report: dict[str, object]) -> str:
    encoded = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@pytest.mark.integration
def test_h9_isolated_opt_in_canary_kill_switch_and_copy_on_write_recovery(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    active_root = tmp_path / "pilot-active"
    permit = authorize_isolated_pilot(
        data_root=active_root,
        test_root=tmp_path,
        readiness=_readiness(),
        opt_in_confirmed=True,
        kill_switch_engaged=False,
        synthetic_only_confirmed=True,
    )
    initialize_synthetic_pilot_root(permit)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            with _isolated_server(active_root) as (origin, user_id, password):
                context = browser.new_context(
                    locale="ko-KR",
                    viewport={"height": 900, "width": 1440},
                )
                try:
                    page = context.new_page()
                    _login(page, origin, user_id, password)
                    page.get_by_role(
                        "button", name="새 페이지", exact=True
                    ).first.click()
                    new_page_menu = page.locator("#newPageMenu")
                    expect(new_page_menu).to_be_visible(timeout=45_000)
                    new_page_menu.locator('[data-new-page-type="page"]').click()
                    expect(page.locator("#pageTitle")).to_be_visible(timeout=45_000)
                    _save_and_reload(page, _BASELINE_BODY)
                    before_preview = _canonical_publication_state(active_root)
                    _open_publication_preview(page)
                    _wait_for_publication_action(page, "취소").click()
                    expect(page.locator("#modalBg")).to_be_hidden(timeout=45_000)
                    baseline_publication = _canonical_publication_state(active_root)
                    assert baseline_publication == before_preview == {
                        "items": 0,
                        "revisions": 0,
                    }
                finally:
                    context.close()

            snapshot = create_recovery_snapshot(
                permit,
                destination=tmp_path / "pilot-backup",
                quiesced_confirmed=True,
                kill_switch_engaged=False,
            )

            with pytest.raises(PilotDenied, match="kill switch"):
                authorize_isolated_pilot(
                    data_root=active_root,
                    test_root=tmp_path,
                    readiness=_readiness(),
                    opt_in_confirmed=True,
                    kill_switch_engaged=True,
                    synthetic_only_confirmed=True,
                )

            with _serve_existing(active_root) as origin:
                context = browser.new_context(
                    locale="ko-KR",
                    viewport={"height": 900, "width": 1440},
                )
                try:
                    page = context.new_page()
                    _login(page, origin, user_id, password)
                    _open_only_page(page)
                    _save_and_reload(page, _CANARY_BODY)
                    approval_label = _open_publication_preview(page)
                    _wait_for_publication_action(page, approval_label).click()
                    expect(page.locator("#modalTitle")).to_have_text(
                        re.compile(r"^(게시 결과|Published)$"),
                        timeout=45_000,
                    )
                    canary_publication = _canonical_publication_state(active_root)
                    assert canary_publication == {"items": 1, "revisions": 1}
                finally:
                    context.close()

            restored_permit = restore_recovery_snapshot(
                snapshot,
                destination=tmp_path / "pilot-restored",
                quiesced_confirmed=True,
                kill_switch_engaged=False,
            )
            with _serve_existing(restored_permit.root) as origin:
                context = browser.new_context(
                    locale="ko-KR",
                    viewport={"height": 900, "width": 1440},
                )
                try:
                    page = context.new_page()
                    _login(page, origin, user_id, password)
                    _open_only_page(page)
                    expect(
                        page.locator(
                            ".block-editor[contenteditable='true']"
                        ).first
                    ).to_have_text(_BASELINE_BODY, timeout=45_000)
                    restored_publication = _canonical_publication_state(
                        restored_permit.root
                    )
                    assert restored_publication == {"items": 0, "revisions": 0}
                    _open_publication_preview(page)
                    _wait_for_publication_action(page, "취소").click()
                    expect(page.locator("#modalBg")).to_be_hidden(timeout=45_000)
                finally:
                    context.close()
        finally:
            browser.close()

    report = build_pilot_report(
        backup_file_count=snapshot.file_count,
        baseline_publication_items=baseline_publication["items"],
        baseline_publication_revisions=baseline_publication["revisions"],
        canary_publication_items=canary_publication["items"],
        canary_publication_revisions=canary_publication["revisions"],
        restored_publication_items=restored_publication["items"],
        restored_publication_revisions=restored_publication["revisions"],
        baseline_restored=True,
        kill_switch_denial_verified=True,
        second_brain_gate_green=True,
    )
    assert active_root.is_dir()
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        str(tmp_path),
        str(active_root),
        user_id,
        password,
        _BASELINE_BODY,
        _CANARY_BODY,
    ):
        assert forbidden not in encoded
    assert report["publication_counts"] == {
        "baseline_items": 0,
        "baseline_revisions": 0,
        "canary_items": 1,
        "canary_revisions": 1,
        "restored_items": 0,
        "restored_revisions": 0,
    }
    assert report["recovery"] == {
        "baseline_restored": True,
        "changed_root_preserved": True,
        "new_root": True,
        "verified": True,
    }
    assert _report_sha256(report).startswith("sha256:")

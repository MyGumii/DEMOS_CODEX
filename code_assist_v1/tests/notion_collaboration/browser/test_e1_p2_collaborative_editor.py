from __future__ import annotations

import os

import pytest

from code_assist_v1.notion_collaboration.e1_p2_browser import run_e1_p2_browser_journey


@pytest.mark.browser
@pytest.mark.integration
def test_e1_p2_real_browser_encrypted_recovery_convergence_and_remote_cursor() -> None:
    if os.environ.get("CODE_ASSIST_E1_P2_BROWSER_LIVE") != "1":
        pytest.skip("requires the explicitly approved live 10111/10112/10113 pair")
    pytest.importorskip("playwright.sync_api")
    receipt = run_e1_p2_browser_journey(
        agent_origin="http://127.0.0.1:10111",
        notion_origin="http://127.0.0.1:10112",
        credentials={"admin": "admin1234", "user01": "user1234"},
    )
    assert receipt["status"] == "pass"
    assert receipt["same_workspace_convergence"] is True
    assert receipt["close_reopen_recovery"] is True
    assert receipt["plaintext_scope_or_content_leak_count"] == 0
    assert receipt["remote_cursor_visible"] is True
    assert receipt["test_page_moved_to_trash"] is True

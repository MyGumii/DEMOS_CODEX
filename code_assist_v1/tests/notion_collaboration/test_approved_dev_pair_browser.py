from __future__ import annotations

import os

import pytest

from code_assist_v1.notion_collaboration.approved_dev_pair_browser import (
    run_approved_dev_pair_browser_isolation,
)


@pytest.mark.browser
def test_d0b_approved_development_pair_two_actual_chrome_contexts() -> None:
    if os.environ.get("CODE_ASSIST_D0B_BROWSER_LIVE") != "1":
        pytest.skip("requires the explicitly approved live 10111/10112 development pair")
    receipt = run_approved_dev_pair_browser_isolation(
        agent_origin="http://127.0.0.1:10111",
        notion_origin="http://127.0.0.1:10112",
        credentials={"admin": "admin1234", "user01": "user1234"},
    )
    assert receipt["status"] == "pass"
    assert receipt["principal_ids"] == ["admin", "user01"]
    assert receipt["same_subject_agent_to_notion"] is True
    assert receipt["cross_context_subject_crossover_count"] == 0
    assert receipt["anonymous_observer_authenticated"] is False
    assert receipt["unapproved_network_target_escape_count"] == 0
    assert receipt["browser"]["browser_closed"] is True

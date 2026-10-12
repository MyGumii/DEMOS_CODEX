from __future__ import annotations

import os

import pytest

from code_assist_v1.notion_collaboration.c0_p2_browser import (
    run_c0_p2_browser_collaboration,
)


@pytest.mark.browser
def test_c0_p2_live_two_principal_presence_comments_inbox_and_revoke() -> None:
    if os.environ.get("CODE_ASSIST_C0_P2_BROWSER_LIVE") != "1":
        pytest.skip("requires the explicitly approved live 10111/10112/10113 pair")
    receipt = run_c0_p2_browser_collaboration(
        agent_origin="http://127.0.0.1:10111",
        notion_origin="http://127.0.0.1:10112",
        credentials={"admin": "admin1234", "user01": "user1234"},
    )
    assert receipt["status"] == "pass"
    assert receipt["presence_delivery_verified"] is True
    assert receipt["presence_ttl_verified"] is True
    assert receipt["reconnect_verified"] is True
    assert receipt["comment_delivery_without_reload_verified"] is True
    assert receipt["reaction_delivery_without_reload_verified"] is True
    assert receipt["mention_inbox_verified"] is True
    assert receipt["durable_before_live_verified"] is True
    assert (
        receipt[
            "post_revoke_http_websocket_replay_cache_search_inbox_zero_verified"
        ]
        is True
    )

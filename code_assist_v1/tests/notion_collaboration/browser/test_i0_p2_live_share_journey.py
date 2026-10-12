from __future__ import annotations

import os

import pytest

from code_assist_v1.notion_collaboration.i0_p2_browser import (
    run_i0_p2_browser_sharing,
)


@pytest.mark.browser
def test_i0_p2_live_two_principal_invite_inherit_role_change_and_revoke() -> None:
    if os.environ.get("CODE_ASSIST_I0_P2_BROWSER_LIVE") != "1":
        pytest.skip("requires the explicitly approved live 10111/10112 pair")
    receipt = run_i0_p2_browser_sharing(
        agent_origin="http://127.0.0.1:10111",
        notion_origin="http://127.0.0.1:10112",
        credentials={"admin": "admin1234", "user01": "user1234"},
    )
    assert receipt["status"] == "pass"
    assert receipt["principal_ids"] == ["admin", "user01"]
    assert receipt["workspace_invite_verified"] is True
    assert receipt["inherited_edit_verified"] is True
    assert receipt["inherited_access_explanation_verified"] is True
    assert receipt["role_change_to_view_verified"] is True
    assert receipt["page_revoke_http_404_verified"] is True
    assert receipt["workspace_revoke_visibility_fence_verified"] is True
    assert len(receipt["screenshot_sha256"]) == 4

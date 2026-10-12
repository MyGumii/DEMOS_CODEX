from __future__ import annotations

import os

import pytest

from code_assist_v1.notion_collaboration.f0_p5_browser import (
    run_f0_p5_browser_journey,
)


@pytest.mark.browser
@pytest.mark.integration
def test_f0_p5_two_principal_adopted_database_surface_round_trip() -> None:
    if os.environ.get("CODE_ASSIST_F0_P5_BROWSER_LIVE") != "1":
        pytest.skip("requires the explicitly approved live 10111/10112 pair")
    receipt = run_f0_p5_browser_journey(
        agent_origin="http://127.0.0.1:10111",
        notion_origin="http://127.0.0.1:10112",
        credentials={"admin": "admin1234", "user01": "user1234"},
    )
    assert receipt["status"] == "pass"
    assert receipt["principal_ids"] == ["admin", "user01"]
    assert receipt["adopted_database_views"] == [
        "table", "board", "list", "gallery", "calendar", "timeline"
    ]
    assert receipt["multi_principal_cell_round_trip"] is True
    assert receipt["schema_role_controls_fenced"] is True
    assert receipt["canonical_reload_convergence"] is True
    assert receipt["nonadopted_controls_and_deep_links_fenced"] is True
    assert receipt["test_page_moved_to_trash"] is True
    assert receipt["browser"]["browser_closed"] is True


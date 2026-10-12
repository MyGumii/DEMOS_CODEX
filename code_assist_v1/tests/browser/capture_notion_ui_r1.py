"""Emit redacted current-source R1 captures to stdout."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from code_assist_v1.tests.browser import test_notion_ui1_golden_journey as ui1


def _scenario(report: dict[str, object], action_ids: tuple[str, ...]) -> dict[str, object]:
    captures = report["action_captures"]
    hashes = report.get("action_capture_sha256", report.get("capture_sha256"))
    assert isinstance(captures, dict)
    assert isinstance(hashes, dict)
    return {
        "action_captures": {action_id: captures[action_id] for action_id in action_ids},
        "action_capture_sha256": {action_id: hashes[action_id] for action_id in action_ids},
    }


def _database_multi_view_capture(root: Path) -> dict[str, object]:
    """Allow one fresh-process-equivalent retry for the known save-response race."""

    for attempt in (1, 2):
        try:
            with sync_playwright() as playwright:
                return ui1._run_database_multi_view_round_trip_journey(
                    playwright,
                    root / f"attempt-{attempt}",
                )
        except PlaywrightTimeoutError:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def main() -> int:
    with TemporaryDirectory(prefix="code-assist-pytest-r1-") as temp_dir:
        root = Path(temp_dir)
        with sync_playwright() as playwright:
            db_001 = ui1._run_typed_database_round_trip_journey(playwright, root / "db-001")
        with sync_playwright() as playwright:
            db_002 = ui1._run_database_view_state_round_trip_journey(playwright, root / "db-002")
        db_003 = _database_multi_view_capture(root / "db-003")
        with sync_playwright() as playwright:
            search_001, _, _ = ui1._run_chrome_journey(
                playwright, root / "search-001", include_second_brain=False
            )
        with sync_playwright() as playwright:
            search_002 = ui1._run_in_page_find_and_backlink_navigation_journey(
                playwright, root / "search-002"
            )
        with sync_playwright() as playwright:
            collab_001 = ui1._run_comment_thread_round_trip_journey(
                playwright, root / "collab-001"
            )
        with sync_playwright() as playwright:
            share_001 = ui1._run_share_dialog_read_only_journey(
                playwright, root / "share-001"
            )

        payload = {
            "contract": "code_assist.notion.core_free_ui.r1_current_capture.v1",
            "recorded_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
            "profile": ui1.PROFILE_ID,
            "repeatability": 3,
            "synthetic_only": True,
            "isolated_data_root": True,
            "scenarios": {
                "UI-DB-001": _scenario(db_001, ("UI-DB-001-A01", "UI-DB-001-A02")),
                "UI-DB-002": _scenario(db_002, ("UI-DB-002-A01", "UI-DB-002-A02")),
                "UI-DB-003": _scenario(db_003, ("UI-DB-003-A01",)),
                "UI-SEARCH-001": _scenario(
                    search_001,
                    ("UI-SEARCH-001-A01", "UI-SEARCH-001-A02", "UI-SEARCH-001-A04"),
                ),
                "UI-SEARCH-002": _scenario(
                    search_002, ("UI-SEARCH-002-A01", "UI-SEARCH-002-A02")
                ),
                "UI-COLLAB-001": _scenario(
                    collab_001, ("UI-COLLAB-001-A01", "UI-COLLAB-001-A02")
                ),
                "UI-SHARE-001": _scenario(
                    share_001, ("UI-SHARE-001-A01", "UI-SHARE-001-A02")
                ),
            },
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

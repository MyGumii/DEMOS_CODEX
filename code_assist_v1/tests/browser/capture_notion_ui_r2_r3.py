"""Emit redacted current-source R2/R3 captures to stdout.

This development-only harness reuses the exact real-Chrome journeys asserted
by ``test_notion_ui1_golden_journey``.  It writes no repository artifacts and
uses only an OS-temporary synthetic data root.  Review tooling can bind the
canonical stdout payload without exposing credentials, paths, DOM, network
payloads, or screenshots.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

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


def main() -> int:
    with TemporaryDirectory(prefix="code-assist-pytest-r2-r3-") as temp_dir:
        root = Path(temp_dir)
        with sync_playwright() as playwright:
            editor_001, _, _ = ui1._run_chrome_journey(
                playwright,
                root / "editor-001",
                include_second_brain=False,
            )
            shell_001 = ui1._run_workspace_switch_journey(
                playwright,
                root / "shell-001",
            )
            editor_002 = ui1._run_slash_menu_block_transform_journey(
                playwright,
                root / "editor-002",
            )
            editor_005 = ui1._run_editor_surface_parity_journey(
                playwright,
                root / "editor-005",
            )

        payload = {
            "contract": "code_assist.notion.core_free_ui.r2_r3_current_capture.v1",
            "recorded_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
            "profile": ui1.PROFILE_ID,
            "repeatability": 3,
            "synthetic_only": True,
            "isolated_data_root": True,
            "scenarios": {
                "UI-SHELL-001": _scenario(
                    shell_001,
                    ("UI-SHELL-001-A01", "UI-SHELL-001-A02"),
                ),
                "UI-EDITOR-001": _scenario(
                    editor_001,
                    ("UI-EDITOR-001-A03", "UI-EDITOR-001-A04"),
                ),
                "UI-EDITOR-002": _scenario(
                    editor_002,
                    ("UI-EDITOR-002-A01", "UI-EDITOR-002-A02"),
                ),
                "UI-EDITOR-005": _scenario(
                    editor_005,
                    ("UI-EDITOR-005-A01", "UI-EDITOR-005-A02"),
                ),
            },
        }
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

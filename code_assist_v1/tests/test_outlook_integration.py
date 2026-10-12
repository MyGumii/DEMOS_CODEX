from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from code_assist_v1 import notion_store as ns
from code_assist_v1 import outlook_integration as outlook


class OutlookIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_outlook_data_dir = outlook.USER_DATA_DIR
        self.original_notion_data_dir = ns.USER_DATA_DIR
        outlook.USER_DATA_DIR = self.temp_dir.name
        ns.USER_DATA_DIR = self.temp_dir.name
        self.user_id = "outlook_tester"
        outlook.update_settings(self.user_id, {
            "enabled": True,
            "connector": "classic_outlook",
            "daily_digest_enabled": True,
            "daily_digest_time": "09:00",
        })
        self.message = {
            "entry_id": "entry-1",
            "internet_message_id": "<message-1@example.com>",
            "conversation_id": "conversation-1",
            "subject": "Project review requested today",
            "sender_name": "Project Owner",
            "sender_address": "owner@example.com",
            "to": "tester@example.com",
            "cc": "",
            "received_at": "2026-08-02T00:10:00+00:00",
            "modified_at": "2026-08-02T00:10:00+00:00",
            "unread": True,
            "importance": 2,
            "body": "Please review the project proposal and reply by today.",
            "attachments": [{"name": "proposal.pdf", "size": 2048}],
        }
        self.analysis = {
            "summary": "프로젝트 제안서 검토 요청 메일입니다.",
            "expected_actions": ["오늘 제안서를 검토", "검토 결과를 회신"],
            "priority": "high",
            "reply_needed": True,
            "model_kind": "codex",
            "model_used": "Codex",
            "fallback_errors": [],
        }

    def tearDown(self) -> None:
        outlook.USER_DATA_DIR = self.original_outlook_data_dir
        ns.USER_DATA_DIR = self.original_notion_data_dir
        self.temp_dir.cleanup()

    def test_sync_creates_pages_and_is_idempotent(self) -> None:
        payload = {"available": True, "messages": [self.message], "total_count": 1}
        with (
            patch.object(outlook, "_run_bridge", return_value=payload),
            patch.object(outlook, "_analyze_message", return_value=self.analysis),
        ):
            first = outlook.sync_user(self.user_id)
            second = outlook.sync_user(self.user_id)

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["unchanged"], 1)
        titles = [item["title"] for item in ns.list_pages(self.user_id)]
        self.assertEqual(titles.count("Outlook Mail"), 1)
        self.assertEqual(titles.count("[Outlook] Project review requested today"), 1)
        page = ns.get_page(self.user_id, first["page_ids"][0])
        content = "\n".join(str(block.get("content") or "") for block in page["blocks"])
        self.assertIn("프로젝트 제안서 검토 요청", content)
        self.assertIn("오늘 제안서를 검토", content)
        self.assertIn("tester@example.com", content)

    def test_daily_digest_creates_notification_once_per_day(self) -> None:
        payload = {"available": True, "messages": [self.message], "total_count": 7}
        with (
            patch.object(outlook, "_run_bridge", return_value=payload),
            patch.object(outlook, "_analyze_message", return_value=self.analysis),
        ):
            outlook.sync_user(self.user_id)
            created = outlook.create_daily_digest(
                self.user_id,
                now=datetime.fromisoformat("2026-08-02T09:00:00+09:00"),
            )
            repeated = outlook.create_daily_digest(
                self.user_id,
                now=datetime.fromisoformat("2026-08-02T11:00:00+09:00"),
            )

        self.assertEqual(created["status"], "created")
        self.assertEqual(created["total_unread"], 7)
        self.assertEqual(repeated["status"], "already_created")
        notifications = outlook.list_notifications(self.user_id)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["kind"], "outlook_digest")
        self.assertTrue(notifications[0]["due"])

    def test_digest_due_runs_at_or_after_nine_and_catches_up(self) -> None:
        settings = outlook.get_settings(self.user_id)
        state = {"last_digest_date": ""}
        self.assertFalse(outlook._digest_due(settings, state, datetime.fromisoformat("2026-08-02T08:59:00+09:00")))
        self.assertTrue(outlook._digest_due(settings, state, datetime.fromisoformat("2026-08-02T09:00:00+09:00")))
        self.assertTrue(outlook._digest_due(settings, state, datetime.fromisoformat("2026-08-02T13:00:00+09:00")))
        state["last_digest_date"] = "2026-08-02"
        self.assertFalse(outlook._digest_due(settings, state, datetime.fromisoformat("2026-08-02T13:00:00+09:00")))

    def test_ai_provider_is_explicit_and_falls_back(self) -> None:
        with (
            patch.object(
                outlook,
                "default_provider_order",
                return_value=["hynix_api", "gguf", "codex"],
            ),
            patch.object(outlook, "run_internal_text", return_value=(None, {"error": "offline"})) as runner,
        ):
            result = outlook._analyze_message(self.message)
        self.assertEqual(
            runner.call_args.kwargs["provider_order"],
            ["hynix_api", "gguf", "codex"],
        )
        self.assertNotIn("model_ids", runner.call_args.kwargs)
        self.assertEqual(result["model_kind"], "local_extractive")
        self.assertTrue(result["expected_actions"])

    def test_default_ai_provider_uses_company_api_then_local(self) -> None:
        with (
            patch.object(
                outlook,
                "default_provider_order",
                return_value=["hynix_api", "gguf"],
            ),
            patch.object(outlook.time, "monotonic", return_value=9999),
            patch.object(outlook, "_HYNIX_OUTLOOK_UNAVAILABLE_UNTIL", 0.0),
            patch.object(outlook, "run_internal_text", return_value=(None, {"error": "offline"})) as runner,
        ):
            result = outlook._analyze_message(self.message)
        self.assertEqual(
            runner.call_args.kwargs["provider_order"], ["hynix_api", "gguf"]
        )
        self.assertNotIn("model_ids", runner.call_args.kwargs)
        self.assertEqual(runner.call_args.kwargs["api_timeout_s"], 5)
        self.assertEqual(result["model_kind"], "local_extractive")

    def test_graph_connector_uses_per_user_delta_cursor(self) -> None:
        outlook.update_settings(self.user_id, {"connector": "microsoft_graph", "enabled": True})
        first_payload = {
            "available": True,
            "connector": "microsoft_graph",
            "messages": [self.message],
            "cursor_url": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$deltatoken=opaque",
            "cursor_complete": True,
        }
        second_payload = {
            "available": True,
            "connector": "microsoft_graph",
            "messages": [],
            "cursor_url": first_payload["cursor_url"],
            "cursor_complete": True,
        }
        with (
            patch("code_assist_v1.outlook_graph.fetch_delta", side_effect=[first_payload, second_payload]) as fetcher,
            patch.object(outlook, "_analyze_message", return_value=self.analysis),
        ):
            created = outlook.sync_user(self.user_id)
            repeated = outlook.sync_user(self.user_id)

        self.assertEqual(created["created"], 1)
        self.assertEqual(repeated["count"], 0)
        self.assertEqual(fetcher.call_args_list[1].kwargs["cursor_url"], first_payload["cursor_url"])


if __name__ == "__main__":
    unittest.main()

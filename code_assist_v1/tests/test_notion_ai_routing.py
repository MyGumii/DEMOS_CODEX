from __future__ import annotations

import unittest
from unittest.mock import patch

from code_assist_v1 import routes_notion


class NotionAiRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        routes_notion._HYNIX_NOTION_UNAVAILABLE_UNTIL = 0.0

    def test_codex_mode_keeps_codex_as_final_fallback(self) -> None:
        with (
            patch.object(
                routes_notion,
                "default_provider_order",
                return_value=["hynix_api", "gguf", "codex"],
            ),
            patch.object(routes_notion, "run_internal_text", return_value=("Codex answer", {"model_kind": "codex", "model_used": "Codex"})) as runner,
        ):
            result = routes_notion._call_notion_ai("요약", "# Page\nContent")
        self.assertEqual(result["model_kind"], "codex")
        self.assertEqual(
            runner.call_args.kwargs["provider_order"],
            ["hynix_api", "gguf", "codex"],
        )
        self.assertNotIn("model_ids", runner.call_args.kwargs)

    def test_default_mode_uses_company_api_then_local(self) -> None:
        with (
            patch.object(
                routes_notion,
                "default_provider_order",
                return_value=["hynix_api", "gguf"],
            ),
            patch.object(routes_notion, "run_internal_text", return_value=("Gemma answer", {"model_kind": "hynix_api", "model_used": "Gemma-4"})) as runner,
        ):
            result = routes_notion._call_notion_ai("질문", "# Page\nContent")
        self.assertEqual(result["model_kind"], "hynix_api")
        self.assertEqual(
            runner.call_args.kwargs["provider_order"], ["hynix_api", "gguf"]
        )
        self.assertNotIn("model_ids", runner.call_args.kwargs)
        self.assertEqual(runner.call_args.kwargs["api_timeout_s"], 5)

    def test_client_model_cannot_override_hynix_gemma4(self) -> None:
        with (
            patch.object(
                routes_notion,
                "default_provider_order",
                return_value=["hynix_api", "gguf"],
            ),
            patch.object(routes_notion, "run_internal_text", return_value=("Gemma answer", {"model_kind": "hynix_api"})) as runner,
        ):
            routes_notion._call_notion_ai("질문", "context", model_id="some-other-model")
        self.assertEqual(
            runner.call_args.kwargs["provider_order"], ["hynix_api", "gguf"]
        )
        self.assertNotIn("model_ids", runner.call_args.kwargs)

    def test_unavailable_hynix_returns_deterministic_fallback(self) -> None:
        with (
            patch.object(
                routes_notion,
                "default_provider_order",
                return_value=["hynix_api", "gguf"],
            ),
            patch.object(routes_notion, "run_internal_text", return_value=(None, {"fallback_errors": ["offline"]})),
        ):
            result = routes_notion._call_notion_ai("핵심 요약", "# Page\nFirst point\nSecond point")
        self.assertEqual(result["model_kind"], "fallback")
        self.assertIn("First point", result["answer"])
        self.assertEqual(result["fallback_errors"], ["AI provider unavailable"])

    def test_recent_hynix_failure_uses_immediate_circuit_breaker_fallback(self) -> None:
        with (
            patch.object(
                routes_notion,
                "default_provider_order",
                return_value=["hynix_api", "gguf"],
            ),
            patch.object(routes_notion, "run_internal_text", return_value=(None, {"fallback_errors": ["offline"]})) as runner,
        ):
            first = routes_notion._call_notion_ai("요약", "First point")
            second = routes_notion._call_notion_ai("요약", "Second point")
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(
            runner.call_args_list[1].kwargs["provider_order"], ["gguf"]
        )
        self.assertEqual(first["model_kind"], "fallback")
        self.assertEqual(second["model_kind"], "fallback")
        self.assertEqual(second["fallback_errors"], ["AI provider unavailable"])

    def test_action_item_fallback_returns_checklist_without_inventing_tasks(self) -> None:
        answer = routes_notion._notion_ai_fallback(
            "액션 아이템을 찾아줘\n\n=== 편집할 블록 ===\n보고서를 금요일까지 제출해야 합니다. 배경 설명입니다.",
            "",
        )
        self.assertTrue(answer.startswith("- [ ] "))
        self.assertIn("제출해야", answer)
        self.assertNotIn("배경 설명", answer)


if __name__ == "__main__":
    unittest.main()

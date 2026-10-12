"""Check real original-function execution, capture safety and mode isolation."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

RUNTIME_PATH = Path(__file__).resolve().parents[1] / "runtime" / "runner.py"
SPEC = importlib.util.spec_from_file_location("atlas_runtime_runner", RUNTIME_PATH)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class OriginalRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = {}
        for target in ("code-text", "code-image", "hermes-api"):
            output = []
            result, events = runner.run(target, "Flask 에러를 수정해줘", "record", "unit-" + target, lambda prefix, value: output.append((prefix, value)))
            cls.records[target] = (result, events, output)

    def test_text_and_image_flow_preserves_actual_original_messages(self):
        text = self.records["code-text"][0]
        image = self.records["code-image"][0]
        self.assertEqual([msg["role"] for msg in text["messages"]], ["system", "system", "user"])
        self.assertEqual(text["messages"][-1]["content"], "Flask 에러를 수정해줘")
        content = image["messages"][-1]["content"]
        self.assertEqual([part["type"] for part in content], ["text", "image_url"])
        self.assertEqual(content[0]["text"], text["messages"][-1]["content"])
        self.assertEqual(content[1]["image_url"]["url"]["mime"], "image/png")
        self.assertTrue(content[1]["image_url"]["url"]["redacted"])
        self.assertTrue(image["hasImages"])
        self.assertFalse(text["hasImages"])
        self.assertFalse(image["ocrPerformed"])
        self.assertEqual(image["context"]["message_count"], 3)
        self.assertEqual(image["http"][0]["json"]["tool_choice"], "none")

    def test_trace_comes_from_original_function_frames_with_valid_lines(self):
        result, events, _ = self.records["code-text"]
        visited = {event["fileId"] for event in events if event["kind"] == "line"}
        self.assertEqual(visited, set(result["sourceHashes"]))
        self.assertTrue(any(event["function"] == "_assemble_final_messages" and event["line"] == 1340 for event in events))
        self.assertTrue(any(event["function"] == "_stream_api" and event["line"] == 700 and event["kind"] == "line" for event in events))
        self.assertFalse(any(event["function"] == "<module>" for event in events))
        for event in events:
            path = runner.ROOT / event["fileId"]
            self.assertGreater(event["line"], 0)
            self.assertLessEqual(event["line"], len(path.read_text(encoding="utf-8-sig").splitlines()))
            self.assertEqual(event["sourceHash"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(event["source"], "runtime")
            if event["stack"]:
                self.assertEqual(event["stack"][-1]["function"], event["function"])

    def test_outputs_sequence_return_snapshot_and_request_isolation(self):
        for target, (result, events, output) in self.records.items():
            emitted = [value for prefix, value in output if prefix == "ATLAS_EVENT"]
            self.assertEqual([event["seq"] for event in emitted], list(range(1, len(events) + 1)))
            self.assertEqual(result["eventCount"], len(events))
            self.assertEqual(output[-1][0], "ATLAS_RESULT")
            self.assertTrue(any(event["kind"] == "return" and "$return" in event["variables"] for event in emitted))
            self.assertTrue(all(event["requestId"] == "unit-" + target for event in events))
            self.assertTrue(all(event["runId"] == "unit-" + target for event in events))
            self.assertEqual(result["externalCalls"], 0)
            self.assertFalse(result["truncated"])
            self.assertGreater(result["durationMs"], 0)

    def test_hermes_original_headers_filter_and_raw_sse(self):
        result, events, _ = self.records["hermes-api"]
        headers = result["http"][0]["headers"]
        self.assertNotIn("Host", headers)
        self.assertNotIn("Content-Length", headers)
        self.assertNotIn("Connection", headers)
        self.assertTrue(headers["Authorization"]["redacted"])
        self.assertEqual(result["http"][0]["json"], result["body"])
        self.assertTrue(result["rawRelay"])
        self.assertEqual(result["sse"][-1], "data: [DONE]\n\n")
        upstream = runner.FixtureUpstream(["Hermes 프록시가 ", "원본 SSE bytes를 중계했습니다."])
        self.assertEqual(result["sse"], [piece.decode("utf-8") for piece in upstream.iter_content()])
        self.assertTrue(any(event["function"] == "_build_headers" and event["kind"] == "line" for event in events))
        self.assertTrue(any(event["function"] == "gen" and event["kind"] == "line" for event in events))
        self.assertEqual(len([event for event in events if event["kind"] == "http"]), 1)

    def test_safe_snapshots_mask_boundaries_and_do_not_invoke_repr(self):
        class DangerousObject:
            def __repr__(self):
                raise AssertionError("Snapshot must not invoke arbitrary repr")
        circular = []
        circular.append(circular)
        snapshot = runner.safe_snapshot({"Authorization": "Bearer secret-value", "api_key": "secret-key", "cookie": "session-secret", "image": runner.PNG, "object": DangerousObject(), "loop": circular, "large": "x" * 2500, "items": list(range(1000)), "max_tokens": 4096, "text": "Bearer inline-secret and sk-1234567890"})
        encoded = json.dumps(snapshot)
        for secret in ("secret-value", "secret-key", "session-secret", "inline-secret", "sk-1234567890", "iVBOR"):
            self.assertNotIn(secret, encoded)
        self.assertTrue(snapshot["Authorization"]["redacted"])
        self.assertEqual(snapshot["image"]["type"], "image")
        self.assertEqual(snapshot["max_tokens"], 4096)
        self.assertTrue(snapshot["large"]["truncated"])
        self.assertEqual(snapshot["items"][-1]["omittedItems"], 984)
        self.assertTrue(snapshot["loop"][0]["circular"])
        for _, events, output in self.records.values():
            wire = json.dumps(output, ensure_ascii=False)
            self.assertNotIn("atlas-fixture-secret", wire)
            self.assertNotIn("caller-fixture-secret", wire)
            self.assertNotIn("iVBORw0", wire)

    def test_debug_mode_never_installs_or_disables_trace(self):
        output = []
        with patch.object(sys, "settrace", side_effect=AssertionError("Debug mode must leave debugpy tracing alone")):
            result, events = runner.run("code-image", "테스트", "debug", "debug-unit", lambda prefix, value: output.append((prefix, value)))
        self.assertFalse(any(event["kind"] in {"line", "call", "return", "exception"} for event in events))
        self.assertTrue(any(event["kind"] == "http" for event in events))
        self.assertTrue(any(event["kind"] == "sse" for event in events))
        self.assertEqual(result["mode"], "debug")
        self.assertNotIn("code_assist_v1.config", sys.modules)

    def test_sensitive_query_is_masked_in_result_as_well_as_frames(self):
        for target in ("code-text", "hermes-api"):
            output = []
            runner.run(target, "Bearer user-sensitive-secret sk-1234567890", "debug", "mask-query-" + target, lambda prefix, value: output.append((prefix, value)))
            wire = json.dumps(output, ensure_ascii=False)
            self.assertNotIn("user-sensitive-secret", wire)
            self.assertNotIn("sk-1234567890", wire)


if __name__ == "__main__":
    unittest.main()

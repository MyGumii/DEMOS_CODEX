from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from code_assist_v1.tools.openai_compatible_probe import (
    ProbeConfigurationError,
    load_targets,
    main,
    probe_targets,
    report_contract_errors,
)


class _CompatibleHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    requests: list[dict] = []

    def log_message(self, _format: str, *_args) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "payload": payload,
            }
        )
        instruction = payload["messages"][-1]["content"]
        marker = instruction.split("CODEASSIST-PROBE-", 1)[1]
        marker = "CODEASSIST-PROBE-" + marker.split("'", 1)[0].split(" ", 1)[0]
        if "JSON object" in instruction:
            content = json.dumps({"marker": marker, "ok": True}, separators=(",", ":"))
        else:
            content = marker

        if payload["stream"]:
            chunks = [content[: len(content) // 2], content[len(content) // 2 :]]
            body = "".join(
                f'data: {json.dumps({"choices": [{"delta": {"content": chunk}}]})}\n\n'
                for chunk in chunks
            ) + "data: [DONE]\n\n"
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return

        encoded = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@pytest.fixture
def compatible_server():
    _CompatibleHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CompatibleHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _write_config(path: Path, endpoint: str) -> None:
    path.write_text(
        json.dumps(
            {
                "models": {
                    "gemma4": {
                        "runtime": "api",
                        "model": "gemma-4",
                        "url": endpoint,
                    },
                    "local": {
                        "runtime": "gguf",
                        "model": "local.gguf",
                    },
                }
            }
        ),
        encoding="utf-8",
    )


def test_probe_exercises_current_stream_nonstream_and_json_contract(
    tmp_path: Path, compatible_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "api-config.json"
    endpoint = f"http://127.0.0.1:{compatible_server.server_port}/v1/chat/completions"
    _write_config(config, endpoint)
    monkeypatch.setenv("COMPANY_TEST_TOKEN", "sentinel-token-must-not-leak")

    report = probe_targets(
        config_path=config,
        registry_ids=["gemma4"],
        token_env="COMPANY_TEST_TOKEN",
        timeout_s=5,
        verify_tls=True,
    )

    assert report_contract_errors(report) == ()
    assert report["summary"] == {
        "model_count": 1,
        "passed_count": 1,
        "failed_count": 0,
        "passed": True,
    }
    assert report["models"][0]["passed"] is True
    assert set(report["models"][0]["checks"]) == {"nonstream", "stream", "json_object"}
    assert len(_CompatibleHandler.requests) == 3
    for request in _CompatibleHandler.requests:
        assert request["path"] == "/v1/chat/completions"
        assert request["authorization"] == "Bearer sentinel-token-must-not-leak"
        assert request["payload"]["model"] == "gemma-4"
        assert request["payload"]["tool_choice"] == "none"
        assert request["payload"]["temperature"] == 0
    assert "sentinel-token-must-not-leak" not in json.dumps(report)
    assert "CODEASSIST-PROBE" not in json.dumps(report)


def test_duplicate_members_and_non_api_targets_fail_closed(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"models":{},"models":{}}', encoding="utf-8")
    with pytest.raises(ProbeConfigurationError, match="duplicate JSON member"):
        load_targets(duplicate, ["gemma4"])

    config = tmp_path / "api-config.json"
    _write_config(config, "http://127.0.0.1:1/v1/chat/completions")
    with pytest.raises(ProbeConfigurationError, match="not an API model"):
        load_targets(config, ["local"])

    secret = tmp_path / "secret.json"
    value = json.loads(config.read_text(encoding="utf-8"))
    value["models"]["gemma4"]["metadata"] = {
        "client_secret": "sentinel-must-not-be-read"
    }
    secret.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ProbeConfigurationError, match="credential data"):
        load_targets(secret, ["gemma4"])


def test_explicit_missing_token_environment_fails_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "api-config.json"
    _write_config(config, "http://127.0.0.1:1/v1/chat/completions")
    monkeypatch.delenv("COMPANY_MISSING_TOKEN", raising=False)

    with pytest.raises(ProbeConfigurationError, match="environment variable is unavailable"):
        probe_targets(
            config_path=config,
            registry_ids=["gemma4"],
            token_env="COMPANY_MISSING_TOKEN",
            timeout_s=5,
            verify_tls=True,
        )


def test_report_hash_and_summary_tampering_are_rejected(
    tmp_path: Path, compatible_server
) -> None:
    config = tmp_path / "api-config.json"
    _write_config(
        config,
        f"http://127.0.0.1:{compatible_server.server_port}/v1/chat/completions",
    )
    report = probe_targets(
        config_path=config,
        registry_ids=["gemma4"],
        token_env=None,
        timeout_s=5,
        verify_tls=True,
    )
    report["summary"]["passed_count"] = 0
    assert set(report_contract_errors(report)) == {"report_hash_binding", "report_summary"}


def test_cli_writes_create_only_and_does_not_overwrite(
    tmp_path: Path, compatible_server, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "api-config.json"
    output = tmp_path / "report.json"
    _write_config(
        config,
        f"http://127.0.0.1:{compatible_server.server_port}/v1/chat/completions",
    )
    argv = [
        "--config",
        str(config),
        "--model",
        "gemma4",
        "--timeout",
        "5",
        "--output",
        str(output),
    ]
    assert main(argv) == 0
    original = output.read_bytes()
    assert main(argv) == 3
    assert output.read_bytes() == original
    assert "OUTPUT_EXISTS" in capsys.readouterr().err

    assert main(["--config", str(config), "--verify-report", str(output)]) == 0
    verification = json.loads(capsys.readouterr().out)
    assert verification["status"] == "pass"
    assert verification["error_codes"] == []


def test_offline_verifier_rejects_fully_rehashed_report_config_drift(
    tmp_path: Path, compatible_server, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "api-config.json"
    output = tmp_path / "report.json"
    tampered = tmp_path / "tampered-report.json"
    _write_config(
        config,
        f"http://127.0.0.1:{compatible_server.server_port}/v1/chat/completions",
    )
    assert main(
        ["--config", str(config), "--model", "gemma4", "--output", str(output)]
    ) == 0
    capsys.readouterr()
    report = json.loads(output.read_text(encoding="utf-8"))
    report["models"][0]["model"] = "forged-model"
    payload = dict(report)
    payload.pop("report_sha256")
    canonical = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    report["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    tampered.write_bytes(
        (
            json.dumps(
                report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode("utf-8")
    )

    assert main(
        ["--config", str(config), "--verify-report", str(tampered)]
    ) == 2
    verification = json.loads(capsys.readouterr().out)
    assert verification["status"] == "fail"
    assert verification["error_codes"] == ["report_config_binding"]


def test_report_validator_is_total_for_arbitrary_json_shape() -> None:
    assert report_contract_errors(
        {
            "models": [["not", "a", "row"]],
            "summary": [],
            "report_sha256": [],
        }
    )

"""Isolated local speech service contracts; no models/devices/company calls."""
import io
import threading
from unittest.mock import patch

import pytest
from werkzeug.serving import make_server

from code_assist_v1 import notion_audio as audio
from code_assist_v1 import notion_local_stt as local
from code_assist_v1.tests.test_notion_audio import wav_bytes

TOKEN = "test-only-token-" + "x" * 32


class Engine:
    ready = True
    calls = 0
    error = None

    def transcribe(self, data, language):
        self.calls += 1
        assert data == wav_bytes() and language == "ko"
        if self.error:
            raise self.error
        return {"text": "합성 회의 원문", "model": local.MODEL_ID}


def post(client, **options):
    return client.post("/v1/audio/transcriptions", headers=options.pop("headers", {"Authorization": "Bearer " + TOKEN}),
                       data=options.pop("data", {"model": local.MODEL_ID, "language": "ko", "response_format": "json",
                                                "file": (io.BytesIO(wav_bytes()), "user-file.wav")}), **options)


def test_service_auth_origin_network_and_health():
    engine = Engine()
    client = local.create_app(engine, TOKEN).test_client()
    assert post(client, headers={}).status_code == 401
    assert post(client, headers={"Authorization": "Bearer incorrect"}).status_code == 401
    assert post(client, headers={"Authorization": "Bearer " + TOKEN, "Origin": "http://evil.example"}).status_code == 403
    assert post(client, environ_overrides={"REMOTE_ADDR": "10.0.0.2"}).status_code == 403
    assert engine.calls == 0
    response = post(client)
    assert response.status_code == 200 and engine.calls == 1
    assert response.get_json()["text"] == "합성 회의 원문"
    assert response.headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in response.headers
    engine.ready = False
    health = client.get("/healthz", headers={"Authorization": "Bearer " + TOKEN})
    assert health.status_code == 503 and health.get_json()["ready"] is False
    assert TOKEN not in str(health.get_json())


@pytest.mark.parametrize("change", ["model", "language", "format", "two_files", "missing_file", "empty"])
def test_service_invalid_requests_do_not_run(change):
    engine = Engine()
    data = {"model": local.MODEL_ID, "language": "ko", "file": (io.BytesIO(wav_bytes()), "x.wav")}
    if change == "model": data["model"] = "not-this-model"
    if change == "language": data["language"] = "korean"
    if change == "format": data["response_format"] = "srt"
    if change == "two_files": data["other"] = (io.BytesIO(wav_bytes()), "y.wav")
    if change == "missing_file": del data["file"]
    if change == "empty": data["file"] = (io.BytesIO(), "empty.wav")
    assert post(local.create_app(engine, TOKEN).test_client(), data=data).status_code in {400, 413}
    assert engine.calls == 0


def test_service_busy_worker_error_and_limits(monkeypatch):
    engine = Engine()
    client = local.create_app(engine, TOKEN).test_client()
    engine.error = local.SpeechError("busy", 429)
    assert post(client).status_code == 429
    engine.error = local.SpeechError("transcription_timeout_restart_required", 504)
    assert post(client).status_code == 504
    monkeypatch.setattr(local, "MAX_BYTES", 50)
    assert post(client).status_code == 413
    assert client.post("/v1/audio/transcriptions", json={}, headers={"Authorization": "Bearer " + TOKEN}).status_code == 400


def test_worker_timeout_stops_process_and_releases_slot(monkeypatch):
    transcriber = local.LocalTranscriber("unused", timeout=0.01)
    class Process:
        def is_alive(self): return True
    class Pipe:
        def send(self, value): pass
        def poll(self, timeout): return False
    transcriber.process, transcriber.connection = Process(), Pipe()
    stopped = []
    monkeypatch.setattr(transcriber, "close", lambda: stopped.append(True))
    with pytest.raises(local.SpeechError, match="transcription_timeout"):
        transcriber.transcribe(wav_bytes(), "ko")
    assert stopped == [True] and not transcriber.lock.locked()
    transcriber.lock.acquire()
    try:
        with pytest.raises(local.SpeechError, match="busy"):
            transcriber.transcribe(wav_bytes(), "ko")
    finally:
        transcriber.lock.release()


@pytest.fixture
def local_gateway(monkeypatch):
    engine = Engine()
    server = make_server("127.0.0.1", 0, local.create_app(engine, TOKEN))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    settings = {"MODE": "local_test", "LOCAL_URL": f"http://127.0.0.1:{server.server_port}/v1/audio/transcriptions",
                "LOCAL_MODEL": local.MODEL_ID, "LOCAL_API_KEY": TOKEN}
    for key, value in settings.items():
        monkeypatch.setenv(audio.PREFIX + key, value)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    try:
        yield engine
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=3)


def test_notion_local_mode_health_transcribe_and_no_company_fallback(local_gateway):
    with patch.object(audio.providers, "company_models", side_effect=AssertionError("company must not be consulted")):
        caps = audio.capabilities()
        assert caps["enabled"] and caps["local_only"] and caps["auto_summary_default"] is False
        assert caps["providers"] == ["local_faster_whisper"] and caps["codex_audio"] is False
        assert TOKEN not in str(caps) and "http://" not in str(caps)
        assert audio.transcribe(wav_bytes(), language="ko", guard=lambda: None)["text"] == "합성 회의 원문"
        local_gateway.error = local.SpeechError("busy", 429)
        with pytest.raises(audio.NotesUnavailable):
            audio.transcribe(wav_bytes(), language="ko", guard=lambda: None)
        local_gateway.ready = False
        assert audio.capabilities()["enabled"] is False


@pytest.mark.parametrize("url", ["https://external.example/v1/audio/transcriptions", "http://127.0.0.1/wrong"])
def test_notion_local_mode_rejects_nonlocal_target(monkeypatch, url):
    for key, value in {"MODE": "local_test", "LOCAL_URL": url, "LOCAL_MODEL": local.MODEL_ID, "LOCAL_API_KEY": TOKEN}.items():
        monkeypatch.setenv(audio.PREFIX + key, value)
    with patch.object(audio.requests.Session, "send") as send:
        assert audio.capabilities()["enabled"] is False
        send.assert_not_called()


def test_default_mode_does_not_select_installed_local_model(monkeypatch):
    monkeypatch.delenv(audio.PREFIX + "MODE", raising=False)
    with patch.object(audio.providers, "company_models", return_value=[]):
        assert audio.capabilities()["enabled"] is False

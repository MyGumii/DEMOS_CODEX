"""Synthetic audio and loopback transport only; never opens a microphone/provider."""
import io
import json
import wave
from unittest.mock import patch

import pytest

from code_assist_v1 import notion_audio as audio, notion_store as ns
from code_assist_v1 import routes_notion_ai_notes as routes
from code_assist_v1.tests.test_notion_ai_notes import client


def wav_bytes(frames=1600):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * frames)
    return stream.getvalue()


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    for suffix in ("URL", "MODEL", "API_KEY", "API_KEY_ENV", "CA_BUNDLE"):
        monkeypatch.delenv(audio.PREFIX + suffix, raising=False)


def upload(http, page_id, **fields):
    return http.post("/api/notion/ai-notes/transcribe", data={
        "page_id": page_id, "segment_id": "synthetic-1", "consent": "true", "language": "ko",
        "file": (io.BytesIO(wav_bytes()), "private-original-name.wav"), **fields,
    })


def test_unconfigured_and_private_capabilities(client):
    http, page_id = client
    caps = http.get("/api/notion/ai-notes/capabilities").get_json()
    assert caps["audio_transcription"] is False
    assert upload(http, page_id).status_code == 503


@pytest.mark.parametrize("role,status", [("edit", 200), ("comment", 403), ("view", 403)])
def test_audio_share_permissions_and_no_server_save(client, monkeypatch, role, status):
    http, page_id = client
    ns.share_page("owner", page_id, "guest", role=role)
    monkeypatch.setattr(routes, "current_user_id", lambda: "guest")
    before = ns.get_page("owner", page_id)
    with patch.object(audio, "transcribe", return_value={"text": "합성 회의록", "model": "test"}) as runner:
        response = upload(http, page_id)
    assert response.status_code == status
    assert runner.call_count == (status == 200)
    assert ns.get_page("owner", page_id) == before
    if status == 200:
        assert response.get_json()["segment_id"] == "synthetic-1"
        assert response.headers["Cache-Control"] == "no-store"


def test_audio_consent_validation_auth_lock_limits_and_busy(client, monkeypatch):
    http, page_id = client
    with patch.object(audio, "transcribe") as runner:
        assert upload(http, page_id, consent="false").status_code == 400
        assert upload(http, page_id, segment_id="").status_code == 400
        assert http.post("/api/notion/ai-notes/transcribe", json={}).status_code == 400
        assert upload(http, page_id, file=[(io.BytesIO(wav_bytes()), "a.wav"), (io.BytesIO(wav_bytes()), "b.wav")]).status_code == 400
        routes._audio_slots.acquire(); routes._audio_slots.acquire()
        try:
            assert upload(http, page_id).status_code == 429
        finally:
            routes._audio_slots.release(); routes._audio_slots.release()
        ns.update_page("owner", page_id, {"settings": {"locked": True}})
        assert upload(http, page_id).status_code == 403
        monkeypatch.setattr(routes, "current_user_id", lambda: "stranger")
        assert upload(http, page_id).status_code == 404
        monkeypatch.setattr(routes, "current_user_id", lambda: None)
        assert upload(http, page_id).status_code == 401
        runner.assert_not_called()


def test_audio_revocation_after_transcription_discards_text(client, monkeypatch):
    http, page_id = client
    ns.share_page("owner", page_id, "guest", role="edit")
    monkeypatch.setattr(routes, "current_user_id", lambda: "guest")

    def revoke(*args, **kwargs):
        ns.revoke_share("owner", page_id, "guest")
        return {"text": "never-return-this", "model": "test"}

    with patch.object(audio, "transcribe", side_effect=revoke):
        response = upload(http, page_id)
    assert response.status_code == 404
    assert "never-return-this" not in response.get_data(as_text=True)


def test_audio_oversized_request_and_normal_large_file(client):
    http, page_id = client
    with patch.object(audio, "transcribe", return_value={"text": "", "model": "test"}) as runner:
        assert upload(http, page_id, file=(io.BytesIO(wav_bytes(160000)), "normal.wav")).status_code == 200
        assert runner.call_args.args[0] == wav_bytes(160000)
        runner.reset_mock()
        assert upload(http, page_id, file=(io.BytesIO(b"x" * (audio.MAX_AUDIO_BYTES + 65536)), "large.wav")).status_code == 413
        runner.assert_not_called()


@pytest.mark.parametrize("url", ["file:///private", "https://user:pass@invalid.local/v1", "https://invalid.local/v1?key=secret", "http://[bad"])
def test_invalid_config_no_sensitive_details(monkeypatch, url):
    monkeypatch.setenv(audio.PREFIX + "URL", url)
    monkeypatch.setenv(audio.PREFIX + "MODEL", "test")
    caps = audio.capabilities()
    assert caps["enabled"] is False
    assert url not in str(caps)


@pytest.mark.parametrize("data,language", [(b"invalid", "ko"), (wav_bytes(), "zzzz"), (b"x" * (audio.MAX_AUDIO_BYTES + 1), "ko")], ids=["invalid-file", "invalid-language", "oversized-file"])
def test_invalid_audio_or_language_never_sends(data, language):
    with patch.object(audio.requests.Session, "send") as send:
        with pytest.raises(ValueError):
            audio.transcribe(data, language=language, guard=lambda: None)
        send.assert_not_called()


@pytest.mark.parametrize("status,result", [(200, {"text": " 합성 음성 "}), (200, {"text": ""}), (200, {}), (200, {"text": "x" * 80001}), (200, {"text": "x" * (audio.MAX_RESPONSE_BYTES + 1)}), (302, {"text": "private upstream error"}), (401, {"text": "private upstream error"})])
def test_actual_loopback_multipart_and_bounded_redacted_responses(monkeypatch, status, result):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append((self.path, dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Location", "/must-not-follow")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True); worker.start()
    monkeypatch.setenv(audio.PREFIX + "URL", f"http://127.0.0.1:{server.server_port}/v1/audio/transcriptions")
    monkeypatch.setenv(audio.PREFIX + "MODEL", "synthetic-stt")
    monkeypatch.setenv(audio.PREFIX + "API_KEY", "synthetic-secret")
    checks = []
    try:
        caps = audio.capabilities()
        assert caps["enabled"] is True
        assert "synthetic-secret" not in str(caps)
        if status == 200 and isinstance(result.get("text"), str) and len(result["text"]) <= 80000:
            assert audio.transcribe(wav_bytes(), language="ko", guard=lambda: checks.append(True))["text"] == result["text"].strip()
        else:
            with pytest.raises(audio.NotesUnavailable) as exc:
                audio.transcribe(wav_bytes(), language="ko", guard=lambda: checks.append(True))
            assert "private upstream" not in str(exc.value)
            assert "synthetic-secret" not in str(exc.value)
        assert len(received) == 1
        path, headers, body = received[0]
        assert path == "/v1/audio/transcriptions"
        assert headers["Authorization"] == "Bearer synthetic-secret"
        assert b'filename="meeting.wav"' in body
        assert b"synthetic-stt" in body and b"\r\nko\r\n" in body
        assert wav_bytes() in body
        assert len(checks) >= 4
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=5)


def test_guard_blocks_before_transport_and_after_return(monkeypatch):
    monkeypatch.setenv(audio.PREFIX + "URL", "https://invalid.local/transcribe")
    monkeypatch.setenv(audio.PREFIX + "MODEL", "test")
    checks = []

    def guard():
        checks.append(True)
        if len(checks) >= 2:
            raise PermissionError("revoked")

    with patch.object(audio.requests.Session, "send") as send:
        with pytest.raises(PermissionError, match="revoked"):
            audio.transcribe(wav_bytes(), language="ko", guard=guard)
        send.assert_not_called()

    response = audio.requests.Response()
    response.status_code = 200
    response._content = b'{"text":"must-not-return"}'
    response._content_consumed = True
    checks.clear()

    def guard_after():
        checks.append(True)
        if len(checks) >= 4:
            raise PermissionError("revoked after send")

    with patch.object(audio.requests.Session, "send", return_value=response) as send:
        with pytest.raises(PermissionError, match="revoked after send"):
            audio.transcribe(wav_bytes(), language="ko", guard=guard_after)
        assert send.call_count == 1

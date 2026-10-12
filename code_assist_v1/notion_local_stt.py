"""Optional loopback-only CPU STT service. Run in the isolated STT venv.

No application data access, provider calls, persistent audio storage, or transcript logging.
The model is pre-downloaded explicitly; inference is offline and process-bounded.
"""
from __future__ import annotations

import argparse
import hmac
import io
import multiprocessing
import os
from pathlib import Path
import re
import threading

from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

MAX_BYTES = 20 * 1024 * 1024
MAX_SECONDS = 120
MODEL_ID = "faster-whisper-base"


class SpeechError(Exception):
    def __init__(self, code, status=503):
        super().__init__(code)
        self.code, self.status = code, status


def decode_bounded(data, max_seconds=MAX_SECONDS):
    """Stream-decode with a decoded-sample limit, including compressed uploads."""
    import av
    import numpy as np

    chunks, count = [], 0
    with av.open(io.BytesIO(data), mode="r") as container:
        if not container.streams.audio:
            raise ValueError("no_audio")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)

        def collect(frames):
            nonlocal count
            for frame in frames:
                count += frame.samples
                if count > max_seconds * 16000:
                    raise ValueError("audio_too_long")
                chunks.append(frame.to_ndarray().flatten())

        for frame in container.decode(audio=0):
            collect(resampler.resample(frame))
        collect(resampler.resample(None))
    if not chunks or not count:
        raise ValueError("no_audio")
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def _worker(connection, model_dir):
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(model_dir, device="cpu", compute_type="int8",
                             cpu_threads=min(4, os.cpu_count() or 1), num_workers=1,
                             local_files_only=True)
        connection.send({"ready": True})
        while True:
            data, language = connection.recv()
            try:
                samples = decode_bounded(data)
                duration = len(samples) / 16000
                segments, info = model.transcribe(
                    samples, language=language or None, task="transcribe", beam_size=5,
                    vad_filter=True, condition_on_previous_text=False,
                    vad_parameters={"min_silence_duration_ms": 500},
                )
                parts, length = [], 0
                for segment in segments:
                    text = segment.text.strip()
                    length += len(text) + 1
                    if length > 80000:
                        raise ValueError("transcript_too_long")
                    if text:
                        parts.append(text)
                connection.send({"text": " ".join(parts), "language": info.language,
                                 "duration": round(duration, 3), "model": MODEL_ID})
            except ValueError as exc:
                code = str(exc) if str(exc) in {"audio_too_long", "no_audio", "transcript_too_long"} else "invalid_audio"
                connection.send({"error": code, "status": 400})
            except Exception:
                connection.send({"error": "transcription_failed", "status": 503})
            finally:
                data = None
                samples = None
    except (EOFError, BrokenPipeError):
        pass
    except Exception:
        try:
            connection.send({"error": "model_load_failed"})
        except (EOFError, BrokenPipeError):
            pass
    finally:
        connection.close()


class LocalTranscriber:
    def __init__(self, model_dir, timeout=65):
        self.model_dir, self.timeout = str(model_dir), timeout
        self.lock = threading.Lock()
        self.process = self.connection = None

    @property
    def ready(self):
        try:
            return bool(self.process and self.process.is_alive() and self.connection)
        except ValueError:
            return False

    def close(self):
        if self.process:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=5)
            self.process.close()
        if self.connection:
            self.connection.close()
        self.process = self.connection = None

    def warmup(self):
        if self.ready:
            return
        self.close()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        self.connection = parent
        self.process = context.Process(target=_worker, args=(child, self.model_dir), daemon=True)
        self.process.start()
        child.close()
        try:
            if not parent.poll(45) or parent.recv() != {"ready": True}:
                raise SpeechError("model_load_failed")
        except (EOFError, OSError):
            self.close()
            raise SpeechError("model_load_failed") from None
        except Exception:
            self.close()
            raise

    def transcribe(self, data, language):
        if not self.lock.acquire(blocking=False):
            raise SpeechError("busy", 429)
        try:
            if not self.ready:
                # Do not spend an unbounded model-reload time inside a request.
                raise SpeechError("worker_unavailable_restart_required")
            self.connection.send((data, language))
            if not self.connection.poll(self.timeout):
                self.close()
                raise SpeechError("transcription_timeout_restart_required", 504)
            result = self.connection.recv()
            if "error" in result:
                raise SpeechError(result["error"], result.get("status", 503))
            return result
        except (EOFError, BrokenPipeError, OSError):
            self.close()
            raise SpeechError("worker_unavailable_restart_required") from None
        finally:
            self.lock.release()


def create_app(transcriber, token):
    if not isinstance(token, str) or len(token) < 32 or not token.isascii():
        raise ValueError("A server-owned random token is required")
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=MAX_BYTES + 65536, MAX_FORM_MEMORY_SIZE=131072,
                      MAX_FORM_PARTS=5)

    @app.before_request
    def authorize():
        # Called only by the authenticated Notion server, never directly by a tab.
        if request.remote_addr not in {"127.0.0.1", "::1"} or request.headers.get("Origin"):
            return jsonify(error="forbidden"), 403
        supplied = request.headers.get("Authorization", "").encode("utf-8")
        if not hmac.compare_digest(supplied, ("Bearer " + token).encode("ascii")):
            return jsonify(error="unauthorized"), 401

    @app.after_request
    def private(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/healthz")
    def health():
        return jsonify(service="notion_local_stt", ready=transcriber.ready, model=MODEL_ID,
                       device="cpu", compute_type="int8", max_audio_seconds=MAX_SECONDS), (200 if transcriber.ready else 503)

    @app.post("/v1/audio/transcriptions")
    def transcribe():
        if request.mimetype != "multipart/form-data":
            return jsonify(error="multipart_required"), 400
        if request.form.get("model") != MODEL_ID or request.form.get("response_format", "json") != "json":
            return jsonify(error="invalid_model_or_format"), 400
        language = request.form.get("language", "")
        if language and not re.fullmatch(r"[a-z]{2}", language):
            return jsonify(error="invalid_language"), 400
        files = list(request.files.items(multi=True))
        if len(files) != 1 or files[0][0] != "file":
            return jsonify(error="one_file_required"), 400
        upload = files[0][1]
        try:
            data = upload.read(MAX_BYTES + 1)
            if not data or len(data) > MAX_BYTES:
                return jsonify(error="audio_size_limit"), 413
            return jsonify(transcriber.transcribe(data, language))
        except SpeechError as exc:
            return jsonify(error=exc.code), exc.status
        finally:
            upload.close()

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_exc):
        return jsonify(error="audio_size_limit"), 413

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--port", type=int, default=10323)
    args = parser.parse_args()
    directory = Path(args.model_dir).resolve(strict=True)
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        if not (directory / name).is_file():
            raise SystemExit("Local model files are incomplete; download explicitly first")
    transcriber = LocalTranscriber(directory)
    app = create_app(transcriber, os.environ.get("NOTION_LOCAL_STT_TOKEN", ""))
    try:
        transcriber.warmup()
        print("Local Whisper base ready: CPU int8, offline inference, max 120 seconds/file", flush=True)
        from waitress import serve
        serve(app, host="127.0.0.1", port=args.port, threads=4,
              max_request_body_size=MAX_BYTES + 65536, channel_timeout=90,
              connection_limit=12)
    finally:
        transcriber.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()

"""Preparation-to-submission races using real adapters, no network/model IO."""
from contextlib import contextmanager
import io
import json
import threading
from types import SimpleNamespace

import pytest
import requests

from code_assist_v1 import internal_llm, codex_bridge
from code_assist_v1.model_invocation import InvocationGuard


class Stale(RuntimeError):
    code = "SCOPE_STALE"


def _candidates(monkeypatch, cfg):
    monkeypatch.setattr(internal_llm, "_candidate_configs", lambda *a, **kw: [
        ("first", {**cfg, "model": "first"}), ("fallback", {**cfg, "model": "fallback"}),
    ])


def _response(request, status=200, location=None):
    response = requests.Response()
    response.status_code = status
    response.url = request.url
    response.request = request
    response._content = json.dumps({"choices": [{"message": {"content": "fixture answer"}}]}).encode()
    response.raw = io.BytesIO(response._content)
    if location:
        response.headers["Location"] = location
    return response


@pytest.mark.parametrize("when", ["token", "serialization", "prepared", "return", "redirect"])
def test_http_fences_after_preparation_and_before_every_send(monkeypatch, when):
    revoked = False
    sends = []
    failure = Stale("fixture changed after adapter precheck")
    _candidates(monkeypatch, {"url": "https://example.invalid/chat/completions"})

    def guard():
        if revoked:
            raise failure

    def token(cfg):
        nonlocal revoked
        revoked |= when == "token"
        return None, None

    encode = internal_llm.encode_chat_payload

    def encoded(payload):
        nonlocal revoked
        result = encode(payload)
        revoked |= when == "serialization"
        return result

    prepare = requests.Session.prepare_request

    def prepared(session, request):
        nonlocal revoked
        result = prepare(session, request)
        revoked |= when == "prepared"
        return result

    rebuild = requests.Session.rebuild_proxies

    def redirect_prepared(session, request, proxies):
        nonlocal revoked
        result = rebuild(session, request, proxies)
        revoked |= when == "redirect"
        return result

    def send(adapter, request, **kwargs):
        nonlocal revoked
        sends.append(request)
        assert "attempt_guard" not in json.loads(request.body)
        revoked |= when == "return"
        return _response(request, 307, "/redirected") if when == "redirect" else _response(request)

    monkeypatch.setattr(internal_llm, "resolve_api_token", token)
    monkeypatch.setattr(internal_llm, "encode_chat_payload", encoded)
    monkeypatch.setattr(requests.Session, "prepare_request", prepared)
    monkeypatch.setattr(requests.Session, "rebuild_proxies", redirect_prepared)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    with pytest.raises(Stale) as raised:
        internal_llm.run_internal_text(
            "fixture", {"body": "허가된 긴 Wiki 본문"}, provider_order=("hynix_api",),
            capture_request_receipt=True, attempt_guard=guard,
        )
    assert raised.value is failure
    assert len(sends) == (1 if when in {"return", "redirect"} else 0)


@pytest.mark.parametrize("when", ["slot", "load", "messages", "return"])
def test_gguf_fences_after_slot_model_load_and_message_preparation(monkeypatch, when):
    from code_assist_v1 import concurrency, gguf_loader, model_runtime, routes_chat_stream, utils

    revoked = False
    submissions = []
    failure = Stale("fixture local inference authority changed")
    _candidates(monkeypatch, {"_gguf_path": "fixture.gguf"})

    def guard():
        if revoked:
            raise failure

    @contextmanager
    def slot(key):
        nonlocal revoked
        revoked |= when == "slot"
        yield

    def load(*args, **kwargs):
        nonlocal revoked
        revoked |= when == "load"
        return True

    def prepare(messages, cfg):
        nonlocal revoked
        revoked |= when == "messages"
        return messages

    def infer(**kwargs):
        nonlocal revoked
        submissions.append(kwargs)
        assert "attempt_guard" not in kwargs
        revoked |= when == "return"
        return {"choices": [{"message": {"content": "fixture answer"}}]}

    monkeypatch.setattr(model_runtime, "assert_local_inference_allowed", lambda: None)
    monkeypatch.setattr(internal_llm, "_resolve_gguf_path", lambda cfg: cfg)
    monkeypatch.setattr(concurrency, "gguf_inference_slot", slot)
    monkeypatch.setattr(gguf_loader, "is_gguf_model_ready", lambda *a, **kw: False)
    monkeypatch.setattr(gguf_loader, "load_gguf_model", load)
    monkeypatch.setattr(routes_chat_stream, "_gguf_load_config_for_request", lambda cfg, messages: cfg)
    monkeypatch.setattr(routes_chat_stream, "_prepare_gguf_messages", prepare)
    monkeypatch.setattr(utils, "gguf_model", SimpleNamespace(create_chat_completion=infer))
    with pytest.raises(Stale) as raised:
        internal_llm.run_internal_text("fixture", {}, provider_order=("gguf",), attempt_guard=guard)
    assert raised.value is failure
    assert len(submissions) == (1 if when == "return" else 0)


class _Child:
    def __init__(self, on_write):
        self.closed = threading.Event()
        self.killed = threading.Event()
        self.writes = []
        self.stdin = SimpleNamespace(write=self.write, close=self.closed.set)
        self.stdout = self.output()
        self.stderr = []
        self.on_write = on_write

    def write(self, prompt):
        self.writes.append(prompt)
        self.on_write()

    def output(self):
        assert self.closed.wait(3)
        yield json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "fixture"}})

    def poll(self):
        return 0 if self.closed.is_set() or self.killed.is_set() else None

    def kill(self):
        self.killed.set()
        self.closed.set()

    def wait(self, timeout=None):
        assert self.closed.wait(timeout or 3)
        return 0


@pytest.mark.parametrize("when", ["prompt", "spawn", "write", "success"])
def test_codex_fences_prepared_prompt_and_writer_thread_without_silent_fallback(tmp_path, monkeypatch, when):
    revoked = False
    spawns = []
    failure = Stale("fixture CLI authority changed")
    _candidates(monkeypatch, {"codex_cwd": str(tmp_path), "codex_sandbox": "read-only"})

    def guard():
        if revoked:
            raise failure

    build = codex_bridge.build_codex_prompt

    def built(*args, **kwargs):
        nonlocal revoked
        result = build(*args, **kwargs)
        revoked |= when == "prompt"
        return result

    def written():
        nonlocal revoked
        revoked |= when == "write"

    child = _Child(written)

    def popen(args, **kwargs):
        nonlocal revoked
        spawns.append(args)
        assert args[-1] == "-" and "fixture private body" not in str(args)
        revoked |= when == "spawn"
        return child

    monkeypatch.setattr(codex_bridge, "_resolve_codex_bin", lambda: "codex")
    monkeypatch.setattr(codex_bridge, "_codex_cli_authenticated", lambda _: True)
    monkeypatch.setattr(codex_bridge, "build_codex_prompt", built)
    monkeypatch.setattr(codex_bridge.subprocess, "Popen", popen)
    kwargs = dict(provider_order=("codex",), attempt_guard=guard, preserve_codex_input=True)
    if when == "success":
        result, meta = internal_llm.run_internal_text("fixture", {"body": "fixture private body"}, **kwargs)
        assert result == "fixture" and meta["model_id"] == "first"
    else:
        with pytest.raises(Stale) as raised:
            internal_llm.run_internal_text("fixture", {"body": "fixture private body"}, **kwargs)
        assert raised.value is failure
    assert len(spawns) == (0 if when == "prompt" else 1)
    assert len(child.writes) == (1 if when in {"write", "success"} else 0)
    if spawns:
        assert child.closed.wait(3) and child.poll() is not None


def test_guard_latches_a_writer_thread_failure_when_callback_later_recovers():
    calls = []
    failure = Stale("first failure must not recover")

    def callback():
        calls.append(True)
        if len(calls) == 1:
            raise failure

    guard = InvocationGuard(callback)
    observed = []

    def writer():
        try:
            guard()
        except BaseException as exc:
            observed.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive() and observed == [failure]
    with pytest.raises(Stale) as raised:
        guard()
    assert raised.value is failure and len(calls) == 1


@pytest.mark.parametrize("error_type", [Stale, KeyboardInterrupt])
def test_guarded_codex_discards_early_delta_and_cleans_blocked_pipe_workers(tmp_path, monkeypatch, error_type):
    revoked = threading.Event()
    started = threading.Event()
    early_delta = threading.Event()
    killed = threading.Event()
    writer_finished = threading.Event()
    stderr_finished = threading.Event()
    closed = set()
    delivered = []
    errors = []
    waits = []
    failure = error_type("revoked while the input pipe was busy")

    def guard():
        if revoked.is_set():
            raise failure

    def write(prompt):
        started.set()
        try:
            assert killed.wait(3)
            raise BrokenPipeError("killed child")
        finally:
            writer_finished.set()

    class Output:
        def __iter__(self):
            assert started.wait(3)
            early_delta.set()
            yield json.dumps({"type": "item.completed", "item": {
                "type": "agent_message", "text": "PRIVATE_DRAFT_MUST_NOT_ESCAPE",
            }})
            assert killed.wait(3)

        def close(self):
            closed.add("stdout")

    class Stderr:
        def __iter__(self):
            assert killed.wait(3)
            stderr_finished.set()
            return
            yield "unreachable"

        def close(self):
            closed.add("stderr")

    def wait(timeout=None):
        waits.append(timeout)
        assert killed.wait(timeout or 3)
        return 1

    child = SimpleNamespace(
        stdin=SimpleNamespace(write=write, close=lambda: closed.add("stdin")),
        stdout=Output(), stderr=Stderr(),
        poll=lambda: 1 if killed.is_set() else None, kill=killed.set, wait=wait,
    )
    monkeypatch.setattr(codex_bridge, "_resolve_codex_bin", lambda: "codex")
    monkeypatch.setattr(codex_bridge, "_codex_cli_authenticated", lambda _: True)
    monkeypatch.setattr(codex_bridge.subprocess, "Popen", lambda *a, **kw: child)

    def consume():
        try:
            for event in codex_bridge.stream_codex_exec(
                [{"role": "user", "content": "fixture private body"}],
                {"codex_cwd": str(tmp_path), "codex_sandbox": "read-only"},
                timeout_s=1, attempt_guard=guard,
            ):
                delivered.append(event)
        except BaseException as exc:
            errors.append(exc)

    consumer = threading.Thread(target=consume)
    consumer.start()
    try:
        assert early_delta.wait(3)
        revoked.set()
        consumer.join(timeout=5)
        assert not consumer.is_alive()
        assert errors == [failure] and delivered == []
        assert killed.is_set() and writer_finished.is_set() and stderr_finished.is_set()
        assert closed == {"stdin", "stdout", "stderr"}
        assert waits and waits[-1] == 2  # Exceptional cleanup waited/reaped too.
    finally:
        killed.set()
        consumer.join(timeout=3)

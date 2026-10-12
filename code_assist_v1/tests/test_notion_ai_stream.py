"""Streaming observable timing, ownership, cancellation and final-save fences."""
import json
from unittest.mock import patch
from uuid import uuid4

import pytest

from code_assist_v1 import notion_ai_chat as chat, notion_store as ns, routes_notion_ai_chat as routes
from code_assist_v1 import notion_ai_chat_stream as transport
from test_notion_ai_home import setup, create, ARTIFACTS


def send_stream(http, record, **fields):
    return http.post(f'/api/notion/ai-home/conversations/{record["id"]}/messages/stream',
        json={"operation_id": str(uuid4()), "expected_revision": record["revision"],
              "message": "무슨 내용들이 정리되어있어?", "mode": "chat", **fields}, buffered=False)


def event(frame):
    return json.loads(frame.decode().removeprefix("data: ").strip())


def legacy_research_stream(http, record, **fields):
    """Unit-test retained v1 validation; the durable API has separate acceptance."""
    service = http.application.extensions[routes.SERVICE_KEY]
    data = {"operation_id": str(uuid4()), "expected_revision": record["revision"], "message": "계획을 정리해줘", "mode": "research", **fields}
    output = []
    with http.application.test_request_context():
        try:
            for frame in service.ask_events("owner", record["id"], data):
                output.append("data: " + json.dumps(frame) + "\n\n")
        except (routes.ChatConflict, routes.DocumentChanged) as exc:
            output.append(routes.frame_error(str(exc), "chat_conflict", 409))
        except routes.ChatGenerationFailed as exc:
            output.append(routes.frame_error(str(exc), exc.code, 503))
    return http.application.response_class([frame.encode() for frame in output], content_type="text/event-stream")


def collect(response):
    return [event(frame) for frame in response.response]


def test_first_delta_is_visible_before_generation_and_commit_finish(setup):
    http, service, _, pid = setup
    record = create(http)
    stages = []
    def upstream(*args):
        if not stages:
            assert service.store("owner").get(record["id"])["messages"] == []
        stages.append("first")
        yield "금요일에 "
        stages.append("second")
        yield "보고서를 제출합니다."
        stages.append("finished")
    with patch.object(chat, "provider_chunks", side_effect=upstream) as model:
        response = send_stream(http, record)
        assert response.content_type.startswith("text/event-stream")
        iterator = iter(response.response)
        assert event(next(iterator))["type"] == "started"
        assert event(next(iterator))["type"] == "model"
        assert event(next(iterator)) == {"type": "delta", "text": "금요일에 "}
        assert stages == ["first"]
        assert service.store("owner").get(record["id"])["messages"] == []
        remaining = [event(frame) for frame in iterator]
        saved = remaining[-1]["conversation"]
        assert stages[-1] == "finished" and saved["revision"] == 1
        assert saved["messages"][-1]["text"] == "금요일에 보고서를 제출합니다."
        assert saved["messages"][-1]["context_info"]["notion_workspace"]["page_count"] == 1
        assert pid in [s["id"] for s in saved["messages"][-1]["sources"]]
        # Repeating the completed operation returns the original result without inference.
        op = saved["messages"][-1]["operation_id"]
        replay = collect(send_stream(http, record, operation_id=op))
        assert replay[-1]["type"] == "done" and model.call_count == 1
        followup = collect(send_stream(http, saved, message="그 내용 다시 알려줘."))
        assert followup[-1]["conversation"]["revision"] == 2
        assert model.call_args.args[2]["references"]


def test_disconnect_closes_upstream_and_never_saves_partial_answer(setup):
    http, service, _, _ = setup
    record = create(http)
    closed = []
    def upstream(*args):
        try:
            yield "아직 생성 중인 문장"
            pytest.fail("client cancelled before second provider chunk")
        finally:
            closed.append(True)
    with patch.object(chat, "provider_chunks", side_effect=upstream):
        response = send_stream(http, record)
        iterator = iter(response.response)
        while event(next(iterator))["type"] != "delta":
            pass
        response.close()
    assert closed == [True]
    saved = service.store("owner").get(record["id"])
    assert saved["messages"] == [] and saved["revision"] == 0 and not saved["pending"]
    # Cancellation releases the shared slot rather than blocking later requests.
    def fresh(*args):
        yield "새 답변"
    with patch.object(chat, "provider_chunks", side_effect=fresh):
        assert collect(send_stream(http, record))[-1]["type"] == "done"


@pytest.mark.parametrize("change", ["archive", "edit"])
def test_reference_change_midstream_aborts_before_next_delta_or_commit(setup, change):
    http, service, _, pid = setup
    record = create(http)
    def upstream(*args):
        yield "확인된 첫 문장"
        if change == "archive":
            ns.archive_page("owner", pid)
        else:
            ns.replace_blocks("owner", pid, [{"type": "paragraph", "content": "변경된 내용"}])
        yield "변경 후에는 노출하면 안 되는 문장"
    with patch.object(chat, "provider_chunks", side_effect=upstream):
        frames = collect(send_stream(http, record))
    assert frames[-1]["type"] == "error" and frames[-1]["status"] == 409
    assert "변경 후" not in str(frames)
    assert service.store("owner").get(record["id"])["messages"] == []


def test_stream_owner_boundary_and_validation_errors_release_slots(setup, monkeypatch):
    http, service, _, _ = setup
    record = create(http)
    for _ in range(4):
        frames = collect(send_stream(http, record, expected_revision=-1))
        assert frames[-1]["status"] == 400
    monkeypatch.setattr(routes, "current_user_id", lambda: "stranger")
    with patch.object(chat, "provider_chunks") as runner:
        frames = collect(send_stream(http, record))
        assert frames[-1]["status"] == 404 and runner.call_count == 0


def test_real_cookie_origin_csrf_gate_applies_to_new_stream_route(monkeypatch):
    from code_assist_v1.tests.knowledge_spaces.integration.test_auth_flask_security import (
        _make_app, _create_user, _login, _ORIGIN)
    app = _make_app()
    routes.register_notion_ai_chat_routes(app)
    uid, password = _create_user()
    client = app.test_client()
    logged = _login(client, uid, password)
    assert logged.status_code == 200
    token = logged.json["csrf_token"]
    headers = {"Origin": _ORIGIN, "X-CSRF-Token": token}
    monkeypatch.setattr(routes.providers, "text_models", lambda: [("test", "hynix_api", {})])
    created = client.post("/api/notion/ai-home/conversations", base_url=_ORIGIN,
        headers=headers, json={"operation_id": str(uuid4()), "page_ids": [], "workspace_id": "", "knowledge_space_id": ""})
    assert created.status_code == 200, created.json
    path = f'/api/notion/ai-home/conversations/{created.json["id"]}/messages/stream'
    body = {"operation_id": str(uuid4()), "expected_revision": 0, "message": "일반 질문", "mode": "chat"}
    def upstream(*args):
        yield "정상 답변"
    with patch.object(chat, "provider_chunks", side_effect=upstream) as model:
        assert client.post(path, base_url=_ORIGIN, json=body, headers={"Origin": _ORIGIN}).status_code == 403
        assert client.post(path, base_url=_ORIGIN, json=body, headers={**headers, "Origin": "https://attacker.example.test"}).status_code == 403
        assert model.call_count == 0
        response = client.post(path, base_url=_ORIGIN, json=body, headers=headers)
        assert response.status_code == 200
        assert collect(response)[-1]["type"] == "done" and model.call_count == 1
    save_path = f'/api/notion/ai-home/conversations/{created.json["id"]}/save-report'
    with patch.object(app.extensions[routes.SERVICE_KEY], "save_report", return_value={"page_id": "synthetic"}) as save:
        assert client.post(save_path, base_url=_ORIGIN, json={}, headers={"Origin": _ORIGIN}).status_code == 403
        assert client.post(save_path, base_url=_ORIGIN, json={}, headers={**headers, "Origin": "https://attacker.example.test"}).status_code == 403
        assert save.call_count == 0
        assert client.post(save_path, base_url=_ORIGIN, json={}, headers=headers).status_code == 200
        assert save.call_count == 1


def test_buffered_chat_also_accepts_grounded_natural_text_without_model_citation_json(setup):
    from code_assist_v1 import internal_llm
    from test_notion_ai_home import send
    http, _, _, _ = setup
    record = create(http)
    with patch.object(internal_llm, "run_internal_text", return_value=("금요일에 보고서를 제출합니다.", {})) as model:
        response = send(http, record, message="무슨 내용들이 정리되어있어?")
    assert response.status_code == 200
    assert response.json["messages"][-1]["sources"]
    assert model.call_args.kwargs["output_format"] == "text"


def test_provider_disconnect_does_not_commit_and_reports_generation_error(setup):
    http, service, _, _ = setup
    record = create(http)
    def upstream(*args):
        yield "미완성"
        raise transport.StreamInterrupted("synthetic upstream disconnect")
    with patch.object(chat, "provider_chunks", side_effect=upstream):
        frames = collect(send_stream(http, record))
    assert frames[-1]["code"] == "ai_generation_unavailable"
    assert "호스트 설정" not in frames[-1]["message"]
    assert service.store("owner").get(record["id"])["messages"] == []


def test_structured_stream_preserves_citation_and_artifact_validation(setup):
    http, service, _, pid = setup
    record = create(http, page_ids=[pid])
    def upstream(lane, cfg, payload, *args):
        ref = payload["references"][0]
        raw = json.dumps({"answer": "표를 만들었습니다.\n한글 😀", "citations": [
            {"source_id": ref["source_id"], "quote": ref["excerpt"][:4]}],
            "artifact": ARTIFACTS["spreadsheet"]}, ensure_ascii=True)
        yield from raw  # Includes split escapes and a split Unicode surrogate pair.
    with patch.object(chat, "provider_chunks", side_effect=upstream):
        frames = collect(send_stream(http, record, mode="spreadsheet"))
    answer = frames[-1]["conversation"]["messages"][-1]
    assert "".join(f["text"] for f in frames if f["type"] == "delta") == answer["text"]
    assert answer["artifact"]["kind"] == "spreadsheet" and answer["citations"]
    assert "artifact" not in str([f for f in frames if f["type"] == "delta"])


def test_invalid_structured_stream_is_retried_reset_and_not_saved(setup):
    http, service, _, pid = setup
    record = create(http, page_ids=[pid])
    bad = json.dumps({"answer": "unsupported", "citations": [
        {"source_id": "fake", "quote": "fake"}], "artifact": None})
    def upstream(*args):
        yield bad
    with patch.object(chat, "provider_chunks", side_effect=upstream) as runner:
        frames = collect(legacy_research_stream(http, record, mode="research", model_id="gemma4"))
    assert runner.call_count == 2 and any(f["type"] == "reset" for f in frames)
    assert frames[-1]["code"] == "ai_invalid_response"
    assert service.store("owner").get(record["id"])["messages"] == []


@pytest.mark.parametrize("mode", ["slides", "spreadsheet", "visualization", "research"])
def test_invalid_json_escape_during_stream_retries_same_model_and_commits_only_valid_result(setup, mode):
    http, service, _, pid = setup
    record = create(http, page_ids=[pid])
    attempts, closed = [], []
    def upstream(lane, cfg, payload, *args):
        attempts.append(payload)
        try:
            if len(attempts) == 1:
                # A common mathematical answer emitted as invalid JSON, split at the escape.
                yield '{"answer":"GAT '
                yield '\\alpha'
                pytest.fail("the invalid stream must close before reading more provider chunks")
            ref = payload["references"][0]
            raw = json.dumps({"answer": r"GAT $\alpha_{ij}$ 한글 😀",
                "citations": [{"source_id": ref["source_id"], "quote": ref["excerpt"][:4]}] if mode == "research" else [],
                "artifact": ARTIFACTS.get(mode)}, ensure_ascii=True)
            yield from raw
        finally:
            closed.append(len(attempts))
    with patch.object(chat, "provider_chunks", side_effect=upstream) as runner:
        sender = legacy_research_stream if mode == "research" else send_stream
        frames = collect(sender(http, record, mode=mode, model_id="gemma4"))
    assert runner.call_count == 2 and closed == [1, 2]
    assert "response_contract_feedback" in attempts[1]
    assert attempts[0]["references"] == attempts[1]["references"]
    reset = next(i for i, frame in enumerate(frames) if frame["type"] == "reset")
    saved = frames[-1]["conversation"]
    assert frames[-1]["type"] == "done" and saved["revision"] == 1
    answer = saved["messages"][-1]
    assert "".join(f["text"] for f in frames[reset:] if f["type"] == "delta") == answer["text"]
    assert answer["text"] == r"GAT $\alpha_{ij}$ 한글 😀"
    assert service.store("owner").get(record["id"])["messages"] == saved["messages"]


def test_repeated_decoder_failure_is_bounded_and_reports_format_error_without_partial_save(setup):
    http, service, _, pid = setup
    record = create(http, page_ids=[pid])
    def upstream(*args):
        yield '{"answer":"invalid \\alpha'
    with patch.object(chat, "provider_chunks", side_effect=upstream) as runner:
        frames = collect(send_stream(http, record, mode="slides", model_id="gemma4"))
    assert runner.call_count == 2
    assert frames[-1]["code"] == "ai_invalid_response"
    stored = service.store("owner").get(record["id"])
    assert stored["revision"] == 0 and stored["messages"] == [] and not stored["pending"]


class Upstream:
    status_code = 200
    closed = False
    def __init__(self, lines): self.lines = lines
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
    def close(self): self.closed = True
    def iter_lines(self, **kwargs):
        assert kwargs["chunk_size"] == 1 and self.encoding == "utf-8"
        yield from self.lines


def wire(delta=None, reason=None):
    return "data: " + json.dumps({"choices": [{"delta": {"content": delta}, "finish_reason": reason}]})


@pytest.mark.parametrize("ending,ok", [([wire(reason="stop"), "", "data: [DONE]", ""], True),
    ([wire(reason="length"), ""], False), ([], False), (["data: [DONE]", ""], False)])
def test_native_provider_requests_true_stream_and_requires_complete_terminal(ending, ok):
    upstream = Upstream([wire("한글 "), "", wire("😀"), "", *ending])
    request = []
    def send(session, prepared, **kwargs):
        request.append(json.loads(prepared.body))
        assert kwargs["stream"] is True
        return upstream
    with patch.object(transport.requests.Session, "send", send):
        iterator = transport.provider_chunks("hynix_api", {"url": "http://synthetic.test/v1", "model": "test"},
            {"request": "question"}, "system", "chat", lambda: None)
        assert next(iterator) == "한글 " and not upstream.closed
        assert next(iterator) == "😀"
        if ok:
            assert list(iterator) == []
        else:
            with pytest.raises(transport.StreamInterrupted): list(iterator)
    assert upstream.closed and request[0]["stream"] is True


@pytest.mark.parametrize('read_wait', [60, 180])
def test_research_can_wait_for_a_busy_provider_without_changing_normal_stream_timeout(read_wait):
    def send(session, prepared, **kwargs):
        assert kwargs['timeout'] == (10, read_wait)
        return Upstream([wire('answer'), '', wire(reason='stop'), '', 'data: [DONE]', ''])
    with patch.object(transport.requests.Session, 'send', send):
        options = {} if read_wait == 60 else {'read_timeout': read_wait}
        assert list(transport.provider_chunks('hynix_api',
            {'url':'http://synthetic.test/v1','model':'test'}, {'request':'synthetic'},
            'system','chat',lambda:None, **options)) == ['answer']


def test_control_ends_only_this_completion_and_never_streams_private_reasoning():
    event={'id':'owned-completion','choices':[{'delta':{'reasoning_content':'PRIVATE reasoning text'},'finish_reason':None}]}
    upstream=Upstream(['data: '+json.dumps(event),'',wire('answer'),'',wire(reason='stop'),'','data: [DONE]',''])
    calls=[]
    class Control(Upstream):
        def json(self): return {'success':True}
    def send(session, prepared, **kwargs):
        data=json.loads(prepared.body); calls.append((prepared.url,data))
        if prepared.url.endswith('/control'):
            assert data=={'id':'owned-completion','action':'reasoning_end'}
            assert kwargs['allow_redirects'] is False
            return Control([])
        assert data['chat_template_kwargs']=={'enable_thinking':True} and data['reasoning_control'] is True
        return upstream
    with patch.object(transport.requests.Session,'send',send):
        delivered=list(transport.provider_chunks('hynix_api',{'url':'http://synthetic.test/v1/chat/completions','model':'test'},
            {'request':'test'},'system','chat',lambda:None,reasoning_char_budget=10))
    assert delivered==['answer'] and len(calls)==2 and upstream.closed


def test_cancel_during_private_reasoning_closes_stream_before_control_or_answer():
    revoked=[False]
    event={'id':'owned-completion','choices':[{'delta':{'reasoning_content':'PRIVATE reasoning text'},'finish_reason':None}]}
    class RevokingStream(Upstream):
        def iter_lines(self, **kwargs):
            revoked[0]=True
            yield 'data: '+json.dumps(event)
            yield ''
    upstream=RevokingStream([])
    calls=[]
    def send(session, prepared, **kwargs): calls.append(prepared.url); return upstream
    def guard():
        if revoked[0]: raise RuntimeError('cancelled')
    with patch.object(transport.requests.Session,'send',send):
        with pytest.raises(RuntimeError,match='cancelled'):
            list(transport.provider_chunks('hynix_api',{'url':'http://synthetic.test/v1/chat/completions','model':'test'},
                {'request':'test'},'system','chat',guard,reasoning_char_budget=10))
    assert len(calls)==1 and upstream.closed


@pytest.mark.parametrize("mode", ["slides", "spreadsheet", "visualization", "research", "chat"])
def test_constrained_output_is_host_opt_in_and_keeps_authorized_source_ids(monkeypatch, mode):
    requests = []
    def send(session, prepared, **kwargs):
        requests.append(json.loads(prepared.body))
        return Upstream([wire("answer"), "", wire(reason="stop"), "", "data: [DONE]", ""])
    payload = {"request": "synthetic", "references": [{"source_id": "Pallowed", "excerpt": "verified text"}]}
    cfg = {"url": "http://synthetic.test/v1", "model": "test"}
    with patch.object(transport.requests.Session, "send", send):
        monkeypatch.delenv("CODE_ASSIST_NOTION_AI_JSON_SCHEMA", raising=False)
        assert list(transport.provider_chunks("hynix_api", cfg, payload, "system", mode, lambda: None)) == ["answer"]
        assert "response_format" not in requests[-1]
        monkeypatch.setenv("CODE_ASSIST_NOTION_AI_JSON_SCHEMA", "1")
        assert list(transport.provider_chunks("hynix_api", cfg, payload, "system", mode, lambda: None)) == ["answer"]
    if mode == "chat":
        assert "response_format" not in requests[-1]
        return
    fmt = requests[-1]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert schema["additionalProperties"] is False and schema["required"] == ["answer", "citations", "artifact"]
    if mode == "research":
        choices = schema["properties"]["citations"]["items"]["anyOf"]
        assert len(choices) == 1
        assert choices[0]["properties"]["source_id"]["const"] == "Pallowed"
        assert choices[0]["properties"]["quote"]["enum"] == ["verified text"]
    else:
        assert schema["properties"]["citations"]["maxItems"] == 0


def test_buffered_artifact_client_uses_constrained_native_provider_path_when_enabled(setup, monkeypatch):
    from test_notion_ai_home import send
    http, _, _, pid = setup
    record = create(http, page_ids=[pid])
    monkeypatch.setenv("CODE_ASSIST_NOTION_AI_JSON_SCHEMA", "1")
    raw = json.dumps({"answer": "자료를 만들었습니다.", "citations": [], "artifact": ARTIFACTS["slides"]})
    def upstream(*args):
        yield from raw
    with patch.object(chat, "provider_chunks", side_effect=upstream) as model:
        response = send(http, record, mode="slides")
    assert response.status_code == 200 and response.json["messages"][-1]["artifact"]["kind"] == "slides"
    assert model.call_count == 1


def test_visualization_can_request_missing_numbers_then_generate_chart_in_same_chat(setup):
    http, service, _, _ = setup
    record = create(http)
    clarification = {"answer": "비교할 항목 이름과 숫자, 단위를 알려 주세요.", "citations": [], "artifact": None}
    def upstream(*args):
        yield from json.dumps(clarification)
    with patch.object(chat, "provider_chunks", side_effect=upstream):
        frames = collect(send_stream(http, record, mode="visualization", message="GAT 구조를 설명해줘."))
    record = frames[-1]["conversation"]
    answer = record["messages"][-1]
    assert record["revision"] == 1 and answer["artifact"] is None
    path = f'/api/notion/ai-home/conversations/{record["id"]}/messages/{answer["operation_id"]}/download/svg'
    assert http.get(path).status_code == 400
    def chart(*args):
        yield from json.dumps({"answer": "차트를 만들었습니다.", "citations": [], "artifact": ARTIFACTS["visualization"]})
    with patch.object(chat, "provider_chunks", side_effect=chart):
        frames = collect(send_stream(http, record, mode="visualization", message="증가 3건, 감소 -2건"))
    saved = frames[-1]["conversation"]
    assert saved["revision"] == 2 and saved["messages"][-1]["artifact"]["values"] == [3, -2]
    assert len(service.store("owner").get(saved["id"])["messages"]) == 4


def test_missing_required_slide_artifact_still_fails_validation():
    with pytest.raises(ValueError):
        chat.normalize_answer(json.dumps({"answer": "슬라이드입니다.", "citations": [], "artifact": None}), [], "slides")

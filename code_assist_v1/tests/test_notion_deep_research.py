"""Durable research acceptance: restart, fencing, source authority and evidence."""
import json
import multiprocessing
import re
from uuid import uuid4

import pytest

from code_assist_v1 import notion_ai_deep_research as deep, notion_store as ns
from code_assist_v1.notion_ai_chat_store import ChatStore, ChatConflict
from code_assist_v1.notion_ai_research_jobs import ResearchJobs
from code_assist_v1.notion_ai_web import PublicWebSearch
from test_notion_ai_home import setup, create, SECRET


class ResearchWeb(PublicWebSearch):
    def __init__(self):
        self.searches, self.reads = [], []
    def capability(self):
        return {"enabled": True, "provider": "synthetic"}
    def search(self, query, guard, **kwargs):
        guard(); self.searches.append(query)
        assert "PRIVATE" not in query
        return [{"url": f"https://example.com/paper-{n}", "title": f"Synthetic original paper {n}",
                 "excerpt": "Synthetic source overview.", "content_status": "search_excerpt"} for n in (1, 2)]
    def read_document(self, result, guard):
        guard(); self.reads.append(result["url"])
        return {**result, "excerpt": "The original method is a synthetic baseline with score 70 under condition A.\n"
                "The revised method obtains score 75 on the same dataset under condition A.\n"
                "Results do not establish a field-wide trend; other conditions remain untested.",
                "content_status": "document_text", "source_format": "pdf"}


@pytest.fixture(autouse=True)
def research_web(setup):
    setup[1].web_reader = ResearchWeb()


def step_reply(lane, cfg, payload, system, mode, guard, **kwargs):
    guard()
    schema = kwargs["response_schema"]
    keys = set(schema["properties"])
    if keys == {"title", "scope", "sections"}:
        assert "PRIVATE" not in json.dumps(payload)
        result = {"title": "합성 비교 조사", "scope": "기초부터 개선과 비교까지", "sections": [
            {"title": title, "question": title, "queries": [f"public query {i}"]}
            for i, title in enumerate(("기초 원리", "후속 개선", "실험과 한계"))]}
    elif keys == {"findings"}:
        quote = payload["quote_choices"][0]
        result = {"findings": [{"statement": re.split(r'(?<=[.!?])\s+',quote['text'])[0], "quote_id": quote['id'], "category": "method", "section_ids": ["S1", "S2", "S3"]}]}
    elif keys == {"selected_urls"}:
        assert "PRIVATE" not in json.dumps(payload)
        result = {"selected_urls": [r["url"] for r in payload["results"]]}
    elif keys == {"within_scope", "reason"}:
        result = {"within_scope": True, "reason": "Synthetic original source matches the requested scope."}
    elif keys == {'subsections'}:
        result = {'subsections':[{'title':'근거 비교','question':payload['chapter']['question']+' 근거'},
                                {'title':'조건과 한계','question':payload['chapter']['question']+' 조건과 한계'}]}
    elif keys == {'answerability','evidence_ids','missing_aspects'}:
        result={'answerability':'direct','evidence_ids':[e['id'] for e in payload['evidence']], 'missing_aspects':[]}
    elif keys in ({'table','figure'},{'table'},{'figure'}):
        words=re.sub(r'\s+',' ',payload['evidence'][0]['quote']).split()
        labels=[' '.join(words[:4]),' '.join(words[-4:])]
        result={'table':{'title':'합성 자료 비교','columns':['자료','조건'],
            'rows':[{'cells':[e['title'],'조건 A'],'evidence_ids':[e['id']]} for e in payload['evidence'][:2]]},
            'figure':{'kind':'comparison','title':'합성 조사 범위','caption':'출처가 설명한 동일 조건의 비교입니다.',
                'lanes':[{'label':'조건 A','steps':labels,
                    'step_evidence_ids':[[payload['evidence'][0]['id']],[payload['evidence'][0]['id']]],
                    'evidence_ids':[payload['evidence'][0]['id']]}]}}
        result={k:result[k] for k in keys}
        if 'figure' in result and 'caption' not in schema['properties']['figure']['properties']:
            result['figure'].pop('caption')
    elif keys == {"coverage"}:
        assert "PRIVATE" not in json.dumps(payload)
        result = {"coverage": [{"section_id": s["id"], "status": "sufficient", "reason": "서로 다른 원문을 확보했습니다.", "queries": []}
                                for s in payload["plan"]["sections"]]}
    elif keys == {'status','reason'}:
        assert 'queries' not in payload
        result = {'status':'sufficient','reason':'완성된 보고서와 원문 근거로 확인했습니다.'}
    elif keys == {"answer", "used_evidence_ids"}:
        selected = payload["evidence"][:2]
        result = {"answer": "\n\n".join(f"{e['statement']} [{e['id']}]" for e in selected),
                  "used_evidence_ids": [e["id"] for e in selected]}
    elif keys == {"supported", "issues"}:
        result = {"supported": True, "issues": []}
    elif keys == {'checks'}:
        result={'checks':[{'claim_id':c['id'],'claim_object':'other','proof_object':'other',
            'claim_variable':'not_applicable','proof_variable':'not_applicable',
            'proof_ids':[payload['original_proofs'][0]['id']],'reason':'Grounded claim.','supported':True}
            for c in payload['claims']]}
    else:
        raise AssertionError(keys)
    yield from json.dumps(result, ensure_ascii=False)


def submit(http, record, **fields):
    op = fields.pop("operation_id", str(uuid4()))
    body = {"message": "공개 주제의 기초부터 발전과 비교까지 조사", "expected_revision": record["revision"],
            "operation_id": op, "source_options": {"notion": False, "web": True}, **fields}
    response = http.post(f'/api/notion/ai-home/conversations/{record["id"]}/research', json=body)
    assert response.status_code == 200, response.json
    return op, body, response.json


def finish(service, cid, op):
    for _ in range(150):
        row = service.store("owner").research_job(cid, op)
        if row["state"] not in {"queued", "processing"}:
            break
        service.research_jobs.run_one("owner", cid, op)
    row = service.store("owner").research_job(cid, op)
    assert row["state"] == "completed", row["job"].get("error")
    return service.get("owner", cid)


def test_report_survives_new_manager_and_stores_exact_traceable_evidence(setup, monkeypatch):
    http, service, _, pid = setup
    service.web_reader = ResearchWeb()
    monkeypatch.setattr(deep, "provider_chunks", step_reply)
    record = create(http)
    op, body, queued = submit(http, record)
    assert queued["pending"] and queued["messages"] == []
    service.research_jobs.run_one("owner", record["id"], op)  # Persist the plan.
    service.research_jobs = ResearchJobs(service)  # Simulate server replacement.
    result = finish(service, record["id"], op)
    message = result["messages"][-1]
    assert result["revision"] == 1 and message["context_info"]["research"]["depth"] == "deep"
    assert len(service.web_reader.searches) == 3 and len(service.web_reader.reads) == 2
    assert "요약과 결론" in message["text"] and "조사 범위 점검" in message["text"]
    assert "[E" not in message["text"] and "[1]" in message["text"]
    cp = service.store("owner").research_job(record["id"], op)["job"]["checkpoint"]
    refs = {s["source_id"]: s for s in cp["sources"]}
    assert all(c["quote"] in refs[c["evidence_source_id"]]["excerpt"] for c in message["citations"])
    replay = http.post(f'/api/notion/ai-home/conversations/{record["id"]}/research', json=body)
    assert replay.status_code == 200 and replay.json["revision"] == 1
    changed = http.post(f'/api/notion/ai-home/conversations/{record["id"]}/research', json={**body, "message": "다른 질문"})
    assert changed.status_code == 409
    assert not service.store("owner").path.read_bytes().find(b"Synthetic original paper") >= 0


def test_pause_resume_cancel_and_pending_controls(setup, monkeypatch):
    http, service, _, _ = setup
    monkeypatch.setattr(deep, "provider_chunks", step_reply)
    service.web_reader = ResearchWeb()
    record = create(http)
    op, _, _ = submit(http, record)
    path = f'/api/notion/ai-home/conversations/{record["id"]}/research'
    pause = http.post(path, json={"operation_id": op, "action": "pause"})
    assert not pause.json["pending"] and pause.json["research_job"]["state"] == "paused"
    assert not service.research_jobs.run_one("owner", record["id"], op)
    resume = http.post(path, json={"operation_id": op, "action": "resume"})
    assert resume.json["pending"]
    cancel = http.post(path, json={"operation_id": op, "action": "cancel"})
    assert cancel.json["research_job"]["state"] == "cancelled"
    assert cancel.json["messages"] == []


def test_source_changed_after_checkpoint_cannot_resume_or_publish(setup, monkeypatch):
    http, service, _, pid = setup
    monkeypatch.setattr(deep, "provider_chunks", step_reply)
    monkeypatch.setattr(deep.research, "plan", lambda *a, **k: {"queries": [], "database_queries": [], "focus": []})
    record = create(http, page_ids=[pid])
    op, _, _ = submit(http, record, source_options={"notion": True, "web": False})
    service.research_jobs.run_one("owner", record["id"], op)
    service.research_jobs.run_one("owner", record["id"], op)
    ns.replace_blocks("owner", pid, [{"type": "paragraph", "content": "수정된 내용"}])
    service.research_jobs.run_one("owner", record["id"], op)
    assert service.store("owner").research_job(record["id"], op)["state"] == "failed"
    assert service.store("owner").get(record["id"])["messages"] == []
    response = http.post(f'/api/notion/ai-home/conversations/{record["id"]}/research', json={"action": "resume", "operation_id": op})
    assert response.status_code == 409


def _claim_worker(path, cid, op, start, output):
    store = ChatStore(path, SECRET, "owner")
    start.wait(15)
    result = store.claim_research(cid, op)
    output.put(result[0] if result else None)


def test_real_process_claim_fence_allows_only_one_worker_and_stale_commit_fails(setup):
    http, service, _, _ = setup
    record = create(http)
    op, _, _ = submit(http, record)
    store = service.store("owner")
    ctx = multiprocessing.get_context("spawn")
    start, output = ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=_claim_worker, args=(store.path, record["id"], op, start, output)) for _ in range(2)]
    for process in processes:
        process.start()
    start.set()
    results = [output.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(30)
        assert process.exitcode == 0
    assert sum(r is not None for r in results) == 1
    winner = next(r for r in results if r)
    job = store.research_job(record["id"], op)["job"]
    store.checkpoint_research(record["id"], op, winner, job, release=True)
    replacement, _ = store.claim_research(record["id"], op)
    assert replacement != winner
    with pytest.raises(ChatConflict):
        store.finish(record["id"], op, winner, "obsolete", {"answer": "stale"})


def test_research_lease_is_renewed_after_ten_minutes_without_extending_dead_worker(setup, monkeypatch):
    from code_assist_v1 import notion_ai_chat_store as storage
    http, service, _, _ = setup
    record = create(http)
    op, _, _ = submit(http, record)
    clock = [storage.time.time()]
    monkeypatch.setattr(storage.time, "time", lambda: clock[0])
    store = service.store("owner")
    claim, job = store.claim_research(record["id"], op)
    for _ in range(12):
        clock[0] += 60
        store.heartbeat_research(record["id"], op, claim)
    store.assert_claim(record["id"], op, claim)
    clock[0] += 181
    assert store.claim_research(record["id"], op)[0] != claim
    with pytest.raises(ChatConflict):
        store.heartbeat_research(record["id"], op, claim)
    with pytest.raises(ChatConflict):
        store.stop_research(record["id"], op, "failed", claim=claim, job=job)


def test_account_cannot_read_another_accounts_checkpoint(setup, monkeypatch):
    from code_assist_v1 import routes_notion_ai_chat as routes
    http, service, _, _ = setup
    record = create(http)
    op, _, _ = submit(http, record)
    monkeypatch.setattr(routes, "current_user_id", lambda: "stranger")
    assert http.get(f'/api/notion/ai-home/conversations/{record["id"]}').status_code == 404
    assert http.post(f'/api/notion/ai-home/conversations/{record["id"]}/research', json={"action": "cancel", "operation_id": op}).status_code == 404
    assert not service.store("stranger").path.exists()


def test_original_pdf_read_and_all_ranges_retained(monkeypatch):
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    from code_assist_v1 import notion_ai_web as web
    output = BytesIO(); pdf = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    for text in ("Original synthetic experiment baseline 70 and method score 75 under condition A.", "Second page limitations remain untested for other datasets and conditions."):
        page = pdf.add_blank_page(600, 800)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): pdf._add_object(font)})})
        stream = DecodedStreamObject(); stream.set_data(f"BT /F1 12 Tf 40 740 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = pdf._add_object(stream)
    pdf.write(output)
    requests = []
    def fetch(url, guard, **kwargs):
        requests.append(url); guard()
        return output.getvalue(), "application/pdf", url
    monkeypatch.setattr(web, "public_request", fetch)
    result = web.PublicWebSearch().read_document({"url": "https://arxiv.org/abs/2501.00001v2", "title": "Synthetic"}, lambda: None)
    assert requests == ["https://arxiv.org/pdf/2501.00001v2"]
    assert result["read_pages"] == result["page_count"] == 2
    assert "Second page limitations" in result["excerpt"] and result["content_status"] == "document_text"


def test_document_deduplication_keeps_arxiv_versions_together():
    assert deep.identity({"source_id": "W1", "url": "https://arxiv.org/abs/2105.14491v3"}) == deep.identity({"source_id": "W2", "url": "https://arxiv.org/pdf/2105.14491v1"})


def test_missing_coverage_triggers_new_searches_before_report(setup, monkeypatch):
    http, service, _, _ = setup
    reviews = []
    def runner(*args, **kwargs):
        payload = args[2]
        if set(kwargs["response_schema"]["properties"]) == {"coverage"}:
            reviews.append(payload)
            state = "partial" if len(reviews) == 1 else "sufficient"
            yield json.dumps({"coverage": [{"section_id": s["id"], "status": state,
                "reason": "Independent comparison needed" if state == "partial" else "checked",
                "queries": ["additional independent comparison " + s["id"]] if state == "partial" else []}
                for s in payload["plan"]["sections"]]})
        else:
            yield from step_reply(*args, **kwargs)
    monkeypatch.setattr(deep, "provider_chunks", runner)
    record = create(http); op, _, _ = submit(http, record)
    result = finish(service, record["id"], op)
    assert len(service.web_reader.searches) == 4 and len(reviews) == 6
    assert all(len(p["plan"]["sections"]) == 1 for p in reviews)
    assert result["messages"][-1]["context_info"]["research"]["gap_rounds"] == 1


def test_unsupported_chapter_is_rewritten_then_excluded_instead_of_published(setup, monkeypatch):
    http, service, _, _ = setup
    def runner(*args, **kwargs):
        keys = set(kwargs["response_schema"]["properties"])
        if keys == {"answer", "used_evidence_ids"}:
            item = args[2]["evidence"][0]
            yield json.dumps({"answer": "UNSUPPORTED: all models reach 99% accuracy. [" + item["id"] + "]",
                              "used_evidence_ids": [item["id"]]})
        elif keys == {"supported", "issues"}:
            yield json.dumps({"supported": False, "issues": ["99% and field-wide claim are unsupported."]})
        else:
            yield from step_reply(*args, **kwargs)
    monkeypatch.setattr(deep, "provider_chunks", runner)
    record = create(http); op, _, _ = submit(http, record)
    result = finish(service, record["id"], op)
    answer = result["messages"][-1]
    assert "UNSUPPORTED" not in answer["text"] and "99%" not in answer["text"]
    assert answer["context_info"]["research"]["partial"]
    cp = service.store("owner").research_job(record["id"], op)["job"]["checkpoint"]
    assert cp["chapter_retries"] == {f'S{i}.P{j}':2 for i in range(1,4) for j in (1,2)}
    assert next(s for s in cp['sections'] if s['id']=='SUMMARY')['verification']=='missing'


def test_web_off_never_executes_external_search_or_document_fetch(setup, monkeypatch):
    http, service, _, pid = setup
    monkeypatch.setattr(deep, "provider_chunks", step_reply)
    monkeypatch.setattr(deep.research, "plan", lambda *a, **k: {"queries": [], "database_queries": [], "focus": []})
    record = create(http, page_ids=[pid]); op, _, _ = submit(http, record, source_options={"notion": True, "web": False})
    result = finish(service, record["id"], op)
    assert service.web_reader.searches == service.web_reader.reads == []
    assert not result["messages"][-1]["context_info"]["web_search"]


def test_private_internal_evidence_is_not_sent_to_public_query_planning(setup, monkeypatch):
    http, service, _, pid = setup
    ns.replace_blocks("owner", pid, [{"type": "paragraph", "content": "PRIVATE-company-7319 confidential internal comparison evidence."}])
    final_private_checks = []
    def reply(*args, **kwargs):
        if set(kwargs['response_schema']['properties']) == {'status','reason'}:
            final_private_checks.append('PRIVATE-company-7319' in json.dumps(args[2]))
            assert 'queries' not in kwargs['response_schema']['properties']
        yield from step_reply(*args, **kwargs)
    monkeypatch.setattr(deep, "provider_chunks", reply)
    monkeypatch.setattr(deep.research, "plan", lambda *a, **k: {"queries": [], "database_queries": [], "focus": []})
    record = create(http, page_ids=[pid]); op, _, _ = submit(http, record, source_options={"notion": True, "web": True})
    result = finish(service, record["id"], op)
    assert any(s["kind"] == "page" for s in result["messages"][-1]["sources"])
    # step_reply asserts plan, public selection and gap queries contain no PRIVATE.
    assert len(service.web_reader.searches) == 3
    assert any(final_private_checks), 'Final coverage must use authorized internal evidence too.'


def test_permission_revocation_during_a_job_hides_drafts_and_blocks_resume(setup, monkeypatch):
    http, service, reader, _ = setup
    monkeypatch.setattr(deep, "provider_chunks", step_reply)
    monkeypatch.setattr(deep.research, "plan", lambda *a, **k: {"queries": [], "database_queries": [], "focus": []})
    record = create(http, knowledge_space_id="wiki1"); op, _, _ = submit(http, record, source_options={"notion": True, "web": False})
    service.research_jobs.run_one("owner", record["id"], op)
    service.research_jobs.run_one("owner", record["id"], op)
    reader.revoked = True
    service.research_jobs.run_one("owner", record["id"], op)
    assert service.store("owner").research_job(record["id"], op)["state"] == "failed"
    assert http.get(f'/api/notion/ai-home/conversations/{record["id"]}').status_code == 403
    assert http.post(f'/api/notion/ai-home/conversations/{record["id"]}/research', json={"action": "resume", "operation_id": op}).status_code == 403


def test_cancel_fences_late_model_result_before_checkpoint(setup, monkeypatch):
    http, service, _, _ = setup
    record = create(http); op, _, _ = submit(http, record)
    def cancelled(*args, **kwargs):
        service.store("owner").stop_research(record["id"], op, "cancelled")
        yield from step_reply(*args, **kwargs)
    monkeypatch.setattr(deep, "provider_chunks", cancelled)
    service.research_jobs.run_one("owner", record["id"], op)
    row = service.store("owner").research_job(record["id"], op)
    assert row["state"] == "cancelled" and "plan" not in row["job"]["checkpoint"]
    assert service.store("owner").get(record["id"])["messages"] == []


def test_scope_selection_rejects_unrelated_search_hit_before_reading(setup, monkeypatch):
    http, service, _, _ = setup
    def runner(*args, **kwargs):
        if set(kwargs["response_schema"]["properties"]) == {"selected_urls"}:
            yield json.dumps({"selected_urls": [args[2]["results"][0]["url"]]})
        else:
            yield from step_reply(*args, **kwargs)
    monkeypatch.setattr(deep, "provider_chunks", runner)
    record = create(http); op, _, _ = submit(http, record)
    finish(service, record["id"], op)
    assert service.web_reader.reads == ["https://example.com/paper-1"]


def test_abstracts_alone_cannot_pass_full_coverage_gate(setup, monkeypatch):
    http, service, _, _ = setup
    monkeypatch.setattr(deep, "provider_chunks", step_reply)
    original = service.web_reader.read_document
    monkeypatch.setattr(service.web_reader, "read_document", lambda *args: {**original(*args), "content_status": "abstract_text"})
    record = create(http); op, _, _ = submit(http, record)
    result = finish(service, record["id"], op)
    info = result["messages"][-1]["context_info"]["research"]
    assert info["partial"] and all(c["status"] == "partial" for c in info["coverage"])


def test_model_call_reserves_output_using_declared_context_estimate(monkeypatch):
    observed = []
    def runner(lane, cfg, payload, system, mode, guard, **kwargs):
        estimate = (len(kwargs["user_prompt"].encode()) + len(system.encode())) // 2 + 512
        observed.append(estimate + kwargs["max_tokens"])
        yield '{"answer":"checked"}'
    monkeypatch.setattr(deep, "provider_chunks", runner)
    result = deep.model_call(("small", "test", {"context_window": 4096}), "Check", {"text": "a" * 5000}, deep.obj({"answer": deep.string()}), lambda: None, tokens=3000)
    assert result["answer"] == "checked" and observed[0] <= 4096


def test_completed_deep_report_has_replay_safe_page_save(setup, monkeypatch):
    http, service, _, _ = setup
    monkeypatch.setattr(deep, "provider_chunks", step_reply)
    record = create(http); op, _, _ = submit(http, record)
    result = finish(service, record["id"], op)
    body = {"operation_id": str(uuid4()), "message_operation_id": result["messages"][-1]["operation_id"]}
    path = f'/api/notion/ai-home/conversations/{record["id"]}/save-report'
    first = http.post(path, json=body)
    assert first.status_code == 200, first.json
    assert http.post(path, json=body).json == first.json
    blocks = ns.get_page("owner", first.json["page_id"])["blocks"]
    assert any("요약과 결론" in b.get("content", "") for b in blocks)
    assert any(b.get('content','').startswith('[1] Synthetic original paper') for b in blocks)
    exported = http.get(f'/api/notion/ai-home/conversations/{record["id"]}/messages/{op}/download/md')
    assert exported.status_code == 200
    markdown = exported.data.decode('utf-8-sig')
    assert '[E' not in markdown and '- [1] [Synthetic original paper' in markdown
    assert 'https://example.com/paper-' in markdown


def test_deep_internal_page_retains_body_beyond_chat_excerpt_budget(setup, monkeypatch):
    http, service, _, pid = setup
    body = "A synthetic paragraph with relevant baseline conditions.\n" * 600 + "TAIL-EVIDENCE-7319"
    ns.replace_blocks("owner", pid, [{"type": "paragraph", "content": body}])
    monkeypatch.setattr(deep, "provider_chunks", step_reply)
    monkeypatch.setattr(deep.research, "plan", lambda *a, **k: {"queries": [], "database_queries": [], "focus": []})
    record = create(http); op, _, _ = submit(http, record, message="내 문서의 baseline conditions 조사", source_options={"notion": True, "web": False})
    service.research_jobs.run_one("owner", record["id"], op)
    service.research_jobs.run_one("owner", record["id"], op)
    cp = service.store("owner").research_job(record["id"], op)["job"]["checkpoint"]
    assert "TAIL-EVIDENCE-7319" in cp["sources"][0]["excerpt"]
    assert cp["sources"][0]["content_status"] == "document_text"


def test_wrapped_pdf_quote_preserves_numeric_result_and_baseline_context():
    text = "Dataset A\nMethod       Accuracy\nBaseline     70%\nRevised      75%\nUnder the same train/test split."
    choices = deep.quote_choices(text)
    assert text in choices
    assert all(quote in text for quote in choices)


def test_real_cookie_origin_csrf_gate_also_protects_research_controls(monkeypatch):
    from code_assist_v1.tests.knowledge_spaces.integration.test_auth_flask_security import _make_app, _create_user, _login, _ORIGIN
    from code_assist_v1 import routes_notion_ai_chat as routes
    app = _make_app(); routes.register_notion_ai_chat_routes(app)
    uid, password = _create_user(); http = app.test_client()
    token = _login(http, uid, password).json["csrf_token"]
    headers = {"Origin": _ORIGIN, "X-CSRF-Token": token}
    monkeypatch.setattr(routes.providers, "text_models", lambda: [("test", "test", {})])
    record = http.post('/api/notion/ai-home/conversations', base_url=_ORIGIN, headers=headers,
        json={"operation_id":str(uuid4()), "page_ids":[], "workspace_id":"", "knowledge_space_id":""}).json
    path = f'/api/notion/ai-home/conversations/{record["id"]}/research'
    body = {"message":"내 자료 조사", "operation_id":str(uuid4()), "expected_revision":0,
            "source_options":{"notion":True,"web":False}}
    assert http.post(path, base_url=_ORIGIN, json=body, headers={"Origin":_ORIGIN}).status_code == 403
    assert http.post(path, base_url=_ORIGIN, json=body, headers={**headers,"Origin":"https://attacker.example.test"}).status_code == 403
    assert http.post(path, base_url=_ORIGIN, json=body, headers=headers).status_code == 200
    cancel = {"operation_id":body["operation_id"], "action":"cancel"}
    assert http.post(path, base_url=_ORIGIN, json=cancel, headers={"Origin":_ORIGIN}).status_code == 403
    assert http.post(path, base_url=_ORIGIN, json=cancel, headers=headers).json["research_job"]["state"] == "cancelled"


def test_read_source_scope_audit_excludes_an_unrelated_admitted_search_hit(setup, monkeypatch):
    http, service, _, _ = setup
    def runner(*args, **kwargs):
        if set(kwargs["response_schema"]["properties"]) == {"within_scope", "reason"}:
            yield json.dumps({"within_scope": not args[2]["document"]["url"].endswith("paper-2"), "reason":"Scope audit"})
        else:
            yield from step_reply(*args, **kwargs)
    monkeypatch.setattr(deep, "provider_chunks", runner)
    record = create(http); op, _, _ = submit(http, record)
    result = finish(service, record["id"], op)
    assert len(result["messages"][-1]["sources"]) == 1
    assert any(step["kind"] == "source_review" and not step["within_scope"] for step in result["messages"][-1]["context_info"]["research"]["steps"])


def test_pending_job_history_is_hidden_after_automatic_page_revocation(setup, monkeypatch):
    http, service, _, pid = setup
    monkeypatch.setattr(deep, "provider_chunks", step_reply)
    monkeypatch.setattr(deep.research, "plan", lambda *a, **k: {"queries":[],"database_queries":[],"focus":[]})
    record = create(http); op, _, _ = submit(http, record, message="내 문서의 보고서 제출 조사", source_options={"notion":True,"web":False})
    service.research_jobs.run_one("owner", record["id"], op)
    service.research_jobs.run_one("owner", record["id"], op)
    ns.archive_page("owner", pid)
    assert service.list("owner", "", False, 0)["items"] == []
    assert http.get(f'/api/notion/ai-home/conversations/{record["id"]}').status_code == 403


def test_cancelled_research_does_not_replace_later_normal_chat_state(setup, monkeypatch):
    from code_assist_v1 import internal_llm
    from test_notion_ai_home import send, model_reply
    http, service, _, _ = setup
    record = create(http); op, _, _ = submit(http, record)
    service.store("owner").stop_research(record["id"], op, "cancelled")
    monkeypatch.setattr(internal_llm, "run_internal_text", model_reply)
    response = send(http, record, message="금요일 제출 계획을 알려줘")
    assert response.status_code == 200, response.json
    assert "research_job" not in response.json and response.json["messages"][-1]["mode"] == "chat"


def test_small_chapter_context_keeps_conditions_and_limits_from_both_original_papers():
    evidence = []
    for sid, fmt in (("summary", "html"), ("original-a", "pdf"), ("original-b", "pdf")):
        for index, statement in enumerate(("어텐션 메커니즘 소개", "실험 조건: 같은 데이터와 같은 학습 설정", "실험 한계: 다른 조건에서의 결과는 미확인")):
            evidence.append({"id":sid+str(index),"source_id":sid,"statement":statement,"quote":statement,
                "title":sid,"location":"p.1","section_ids":["S1"],"content_status":"document_text",
                "category":"limitation" if index==2 else "method","source_format":fmt})
    selected = deep.DeepResearch.select_evidence(evidence, 1450, "비교 실험의 조건과 한계")
    assert {"original-a", "original-b"}.issubset({e["source_id"] for e in selected})
    assert all(any(e["source_id"] == sid and "실험 조건" in e["statement"] for e in selected) for sid in ("original-a","original-b"))
    assert all('실험' in e['statement'] for e in selected)


def test_long_review_feedback_does_not_exhaust_retry_context(setup, monkeypatch):
    http, service, _, _ = setup
    reviews, retries = [], []
    def runner(*args, **kwargs):
        payload = args[2]
        keys = set(kwargs["response_schema"]["properties"])
        if keys == {"supported", "issues"}:
            reviews.append(payload)
            if len(reviews) <= 2:
                length=kwargs['response_schema']['properties']['issues']['items']['maxLength']
                yield json.dumps({"supported": False, "issues": ["근" * length] * 3}, ensure_ascii=False)
                return
        if keys == {"answer", "used_evidence_ids"}:
            assert "request" not in payload and "chapter" in payload
            feedback = payload["previous_review_issues"]
            assert len(feedback.encode()) <= 1600
            if feedback:
                retries.append(payload)
        yield from step_reply(*args, **kwargs)
    monkeypatch.setattr(deep, "provider_chunks", runner)
    record = create(http); op, _, _ = submit(http, record)
    result = finish(service, record["id"], op)
    assert retries and result["messages"][-1]["context_info"]["research"]["verification"] == "claim-review"


def test_evidence_search_considers_relevant_quotes_late_in_a_long_original():
    evidence = [{'id':f'E{i:010x}','source_id':'original','statement':'A general description.',
        'quote':'An original paragraph about background and methods.', 'title':'Original',
        'location':f'p.{i+1}','section_ids':['S1'],'content_status':'document_text',
        'category':'method','source_format':'pdf'} for i in range(22)]
    evidence[-1]['quote'] = 'Query-specific ranking can depend on the query.'
    selected = deep.DeepResearch.select_evidence(evidence, 700, 'query-specific ranking')
    assert evidence[-1]['id'] in {e['id'] for e in selected}


def test_relevant_later_proceedings_text_is_not_displaced_by_unrelated_seminal_pdfs():
    base={'id':'E0000000001','source_id':'old','statement':'Basic attention mechanism.',
        'quote':'A long original passage about local attention. '*10,'title':'Seminal method',
        'location':'p.1','section_ids':['S1'],'content_status':'document_text','category':'method','source_format':'pdf'}
    current={**base,'id':'E0000000002','source_id':'new','title':'Synthetic scalable transformer 2025',
        'statement':'Sparse structure improves scalability.','quote':'Sparse attention uses bounded edges for scalability.',
        'source_format':'html','section_ids':['S2']}
    selected=deep.DeepResearch.select_evidence([base,current],650,'scalable transformer sparse scalability',section_id='S2')
    assert selected==[current]


def test_blocked_public_paper_requests_one_alternate_search_without_private_context():
    from types import SimpleNamespace
    web=ResearchWeb()
    def blocked(*args): raise ValueError('public paper unavailable')
    web.read_document=blocked
    task={'kind':'read','section_id':'S1','result':{'title':'Public original method','url':'https://example.com/original',
        'excerpt':'Public search snippet.','content_status':'search_excerpt'}}
    engine=object.__new__(deep.DeepResearch)
    engine.cp={'tasks':[task],'searched':[],'sources':[],'limitations':[]}
    engine.request='Public research'; engine.job={'private_history':'PRIVATE'}
    engine.service=SimpleNamespace(web_reader=web); engine.guard=lambda:None; engine.status=lambda *a,**kw:None
    engine.collect(task)
    alternate=engine.cp['tasks'][1]
    assert alternate['kind']=='web_search' and 'PRIVATE' not in alternate['query']
    assert alternate['query']=='"Public original method" original paper PDF'
    engine.cp['searched']=[alternate['query']]; engine.cp['tasks']=[task]
    engine.collect(task)
    assert engine.cp['tasks']==[task]


@pytest.mark.parametrize('question',['수식과 연산 순서를 비교','Compare mathematical formulas and operation order'])
def test_equation_queries_prioritize_actual_equations_over_many_topic_summaries(question):
    evidence=[{'id':f'E{i:010x}','source_id':'original','statement':'수식과 연산 순서의 구조적 개선을 소개합니다.',
        'quote':'The improved method is introduced with a simple change in its internal operations.',
        'title':'Original','location':f'p.{i+1}','section_ids':['S2'],'content_status':'document_text',
        'category':'method','source_format':'pdf'} for i in range(20)]
    evidence[-1]['statement']='Original operator definitions.'
    evidence[-1]['quote']='Original: f(x,y) = ReLU(a⊤[Wx ∥ Wy]); revised: g(x,y) = a⊤ReLU(W[x ∥ y]).'
    selected=deep.DeepResearch.select_evidence(evidence,500,question,section_id='S2')
    assert evidence[-1]['id'] in {e['id'] for e in selected}


def test_missing_successor_evidence_cannot_be_filled_with_basic_method_or_marked_complete(setup,monkeypatch):
    http,service,_,_=setup
    written=[]
    def runner(*args,**kwargs):
        payload=args[2]; keys=set(kwargs['response_schema']['properties'])
        if keys=={'answerability','evidence_ids','missing_aspects'} and payload['chapter']['title']=='후속 개선':
            yield json.dumps({'answerability':'missing','evidence_ids':[], 'missing_aspects':['후속 연구의 실제 개선점과 발표 시기']})
        else:
            if keys=={'answer','used_evidence_ids'}: written.append(payload['chapter']['id'])
            yield from step_reply(*args,**kwargs)
    monkeypatch.setattr(deep,'provider_chunks',runner)
    record=create(http); op,_,_=submit(http,record)
    result=finish(service,record['id'],op)
    cp=service.store('owner').research_job(record['id'],op)['job']['checkpoint']
    chapter=next(s for s in cp['sections'] if s['id']=='S2')
    assert chapter['verification']=='missing' and 'synthetic baseline' not in chapter['answer']
    assert not any(x.startswith('S2.') for x in written)
    assert next(c for c in cp['coverage'] if c['section_id']=='S2')['status']=='missing'
    assert cp['section_visuals']['S2']['status']=='missing' and not cp['section_visuals']['S2']['text']
    assert '요청한 조사 범위:' in result['messages'][-1]['text'] and '추가 확인 필요' in result['messages'][-1]['text']


def test_section_visuals_resume_individually_and_stay_inside_their_own_chapter(setup,monkeypatch):
    http,service,_,_=setup
    visuals=[]
    def runner(*args,**kwargs):
        if set(kwargs['response_schema']['properties']) in ({'table'},{'figure'}):
            visuals.append((args[2]['chapter']['title'],args[2]['component']))
        yield from step_reply(*args,**kwargs)
    monkeypatch.setattr(deep,'provider_chunks',runner)
    record=create(http); op,_,_=submit(http,record)
    for _ in range(100):
        service.research_jobs.run_one('owner',record['id'],op)
        cp=service.store('owner').research_job(record['id'],op)['job']['checkpoint']
        if cp.get('section_visuals',{}).get('S1',{}).get('finished'): break
    assert cp['section_visuals']['S1']['text']
    service.research_jobs=ResearchJobs(service)
    report=finish(service,record['id'],op)['messages'][-1]
    titles=['기초 원리','후속 개선','실험과 한계']
    assert visuals==[(title,kind) for title in titles for kind in ('figure','table')]
    for title in titles:
        match=re.search(r'## \d+\. '+re.escape(title)+r'\n(.*?)(?=\n## |\Z)',report['text'],re.S)
        assert match and '### 그림.' in match[1] and '### 표.' in match[1]
    assert len(report['context_info']['research']['section_visuals'])==3


def test_wrong_oversmoothing_translation_is_rejected_despite_model_review_acceptance(setup,monkeypatch):
    http,service,_,_=setup
    def runner(*args,**kwargs):
        if set(kwargs['response_schema']['properties'])=={'answer','used_evidence_ids'}:
            eid=args[2]['evidence'][0]['id']
            yield json.dumps({'answer':'과적합(over-smoothing)으로 설명됩니다. ['+eid+']', 'used_evidence_ids':[eid]})
        else:
            yield from step_reply(*args,**kwargs)
    monkeypatch.setattr(deep,'provider_chunks',runner)
    record=create(http); op,_,_=submit(http,record)
    report=finish(service,record['id'],op)['messages'][-1]
    assert '과적합(over-smoothing)' not in report['text'] and report['context_info']['research']['partial']


def test_writer_reuses_related_other_chapter_evidence_without_stale_gap_reason(monkeypatch):
    evidence=[{'id':'E0000000001','source_id':'W1','chunk_id':'C1','statement':'Operator definition.',
        'quote':'g(x,y) = a⊤ReLU(W[x ∥ y]).','title':'Synthetic original','location':'p.1',
        'section_ids':['S3'],'category':'method','content_status':'document_text','source_format':'pdf'}]
    for i in range(2,12):
        evidence.append({**evidence[0],'id':f'E{i:010x}','source_id':'W2','section_ids':['S2'],
            'statement':'두 방법이 어떻게 구분되는지 소개합니다. '+'배경 정보입니다. '*50,
            'quote':'The models differ in their attention mechanisms. '*12})
    evidence[0]['quote']='Original: f(x,y) = ReLU(a⊤[Wx ∥ Wy]); revised: g(x,y) = a⊤ReLU(W[x ∥ y]). '+'Original definitions. '*25
    seen=[]
    def runner(*args, **kwargs):
        payload=args[2]
        if 'answer' in kwargs['response_schema']['properties']:
            seen.append(payload)
            proof=next(e for e in payload['evidence'] if 'g(x,y)' in e['quote'])
            assert 'STALE-MISSING-FORMULA' not in json.dumps(payload)
            yield json.dumps({'answer':f"관련 원문의 수식 정의를 확인했습니다. [{proof['id']}]",'used_evidence_ids':[proof['id']]})
        else:
            yield from step_reply(*args, **kwargs)
    monkeypatch.setattr(deep,'provider_chunks',runner)
    engine=object.__new__(deep.DeepResearch)
    engine.cp={'evidence':evidence,'sources':[{'source_id':sid,'excerpt':'\n'.join(e['quote'] for e in evidence if e['source_id']==sid),'title':'Synthetic original'} for sid in ('W1','W2')],
        'coverage':[{'section_id':'S2','status':'partial','reason':'STALE-MISSING-FORMULA'}],
        'plan':{'title':'Equations','scope':'Synthetic comparison'},'limitations':[]}
    engine.job={}; engine.candidate=('test','test',{}); engine.guard=lambda:None
    engine.persist=lambda:None; engine.status=lambda *a,**k:None
    # The chapter's cited subset does not remove other already-authorized
    # original passages from its detailed questions.
    evidence.append({**evidence[0],'id':'E00000000ff','statement':'Revised method introduction.',
        'quote':'The revised method is introduced.'})
    engine.cp['section_support']={'S2':{'answerability':'direct','evidence_ids':['E00000000ff'],'missing_aspects':[]}}
    result=engine.write_piece({'id':'S2.P1','parent_id':'S2','title':'수식과 연산 순서','question':'두 방법이 어떻게 구분되나요?'})
    assert seen and result['verification']=='reviewed' and '[E0000000001]' in result['answer']
    assert all(re.fullmatch(r'E\d{1,2}',e['id']) for e in seen[0]['evidence'])


def test_missing_experiment_subquestion_never_becomes_baseline_filler(monkeypatch):
    evidence={'id':'E0000000001','source_id':'W1','chunk_id':'C1',
        'statement':'Masked neighbor attention.','quote':'Neighbors receive learned attention weights.',
        'title':'Original mechanism','location':'p.1','section_ids':['S1'],
        'category':'method','content_status':'document_text','source_format':'pdf'}
    calls=[]
    def runner(*args,**kwargs):
        keys=set(kwargs['response_schema']['properties'])
        calls.append(keys)
        assert keys=={'answerability','evidence_ids','missing_aspects'}
        assert 'statement' not in args[2]['evidence'][0]
        yield json.dumps({'answerability':'missing','evidence_ids':[],
            'missing_aspects':['동일 데이터셋 정확도와 실험 조건']})
    monkeypatch.setattr(deep,'provider_chunks',runner)
    engine=object.__new__(deep.DeepResearch)
    engine.cp={'evidence':[evidence],'sources':[],'coverage':[],
        'section_support':{'S1':{'answerability':'direct','evidence_ids':[evidence['id']],'missing_aspects':[]}}}
    engine.job={'draft':{'text':'Old unsupported filler'}}
    engine.candidate=('test','test',{}); engine.guard=lambda:None
    saved=[]; engine.persist=lambda:saved.append(True); engine.status=lambda *a,**k:None
    section={'id':'S1.P2','parent_id':'S1','title':'초기 실험 결과',
        'question':'동일 데이터셋에서 얻은 정확도와 실험 조건은 무엇인가?'}
    first=engine.write_piece(section); second=engine.write_piece(section)
    assert first==second and first['verification']=='missing'
    assert '근거 미확보' in first['answer'] and 'Masked' not in first['answer']
    assert len(calls)==len(saved)==1 and 'draft' not in engine.job


def test_parent_citations_do_not_exclude_a_related_experimental_original(monkeypatch):
    evidence=[{'id':'E0000000001','source_id':'W1','chunk_id':'C1',
        'statement':'Method definition.','quote':'Masked neighbor attention.',
        'title':'Baseline mechanism','location':'p.1','section_ids':['S1'],
        'category':'method','content_status':'document_text','source_format':'pdf'}]
    evidence.append({**evidence[0],'id':'E0000000002','source_id':'W2','category':'result',
        'title':'Original experiment','quote':'Dataset A accuracy 80% using split B.'})
    def runner(*args,**kwargs):
        payload=args[2]; keys=set(kwargs['response_schema']['properties'])
        if keys=={'answerability','evidence_ids','missing_aspects'}:
            proof=next(e for e in payload['evidence'] if 'Dataset A' in e['quote'])
            yield json.dumps({'answerability':'direct','evidence_ids':[proof['id']],'missing_aspects':[]})
        elif keys=={'answer','used_evidence_ids'}:
            proof=next(e for e in payload['evidence'] if 'Dataset A' in e['quote'])
            yield json.dumps({'answer':f"Dataset A accuracy 80% using split B. [{proof['id']}]",
                'used_evidence_ids':[proof['id']]})
        else:
            yield from step_reply(*args,**kwargs)
    monkeypatch.setattr(deep,'provider_chunks',runner)
    engine=object.__new__(deep.DeepResearch)
    engine.cp={'evidence':evidence,'sources':[{'source_id':e['source_id'],'excerpt':e['quote'],'title':e['title']} for e in evidence],
        'coverage':[],'plan':{'title':'Experiments','scope':'Original experiments'},'limitations':[],
        'section_support':{'S1':{'answerability':'partial','evidence_ids':['E0000000001'],
            'missing_aspects':['Initial experiments']}}}
    engine.job={}; engine.candidate=('test','test',{}); engine.guard=lambda:None
    engine.persist=lambda:None; engine.status=lambda *a,**k:None
    result=engine.write_piece({'id':'S1.P2','parent_id':'S1','title':'Dataset A experiment',
        'question':'What accuracy was measured for Dataset A with split B?'})
    assert result['verification']=='reviewed' and '[E0000000002]' in result['answer']


def test_legacy_partial_piece_resume_keeps_verified_work_and_original_collection():
    good={'id':'S1.P1','title':'Mechanism','answer':'An accepted mechanism.','verification':'reviewed'}
    bad={'id':'S1.P2','title':'Experiments','answer':'현재 증거가 부족하여 확인되지 않습니다. Baseline filler.',
        'verification':'reviewed'}
    cp={'phase':'visuals','verification_version':18,'sources':[],'evidence':[],
        'plan':{'sections':[{'id':'S1','question':'Mechanism and experiments',
            'parts':[good,bad],'parts_plan':[{'id':'S1.P1'},{'id':'S1.P2','previous_failed_draft':'Old filler'}]}]},
        'sections':[{'id':'S1','answer':'Old composed chapter'},{'id':'S2','answer':'Unchanged chapter'}],
        'section_visuals':{'S1':{'finished':True},'S2':{'finished':True}},
        'searched':['public query'],'limitations':[]}
    job={'request':{'message':'Research','source_options':{'web':True,'notion':False}},'checkpoint':cp}
    deep.DeepResearch(None,'owner',None,job,('test','test',{}),lambda:None,lambda:None)
    assert cp['phase']=='write' and cp['verification_version']==19
    assert cp['plan']['sections'][0]['parts']==[good]
    assert cp['sections']==[{'id':'S2','answer':'Unchanged chapter'}]
    assert cp['section_visuals']=={'S2':{'finished':True}}
    assert cp['searched']==['public query'] and not cp['sources']
    assert 'previous_failed_draft' not in cp['plan']['sections'][0]['parts_plan'][1]


def test_valid_figure_survives_bad_table_retry_and_alias_reordering(monkeypatch):
    count=[0]
    def runner(*args,**kwargs):
        if set(kwargs['response_schema']['properties'])=={'table'}:
            item=json.loads(''.join(step_reply(*args,**kwargs)))
            count[0]+=1
            if count[0]==1: item['table']['rows'][0]['cells']=['모델 표현력','보편적 근사기']
            yield json.dumps(item)
        else:
            yield from step_reply(*args,**kwargs)
    monkeypatch.setattr(deep,'provider_chunks',runner)
    evidence=[{'id':f'E{i:010x}','source_id':f'W{i}','chunk_id':'C1','statement':'Original method comparison.',
        'quote':'Original and revised methods under condition A.','title':f'Original {i}', 'location':'p.1',
        'section_ids':['S1'],'category':'method','content_status':'document_text','source_format':'pdf'} for i in (1,2)]
    engine=object.__new__(deep.DeepResearch)
    engine.cp={'evidence':evidence,'sources':[{'source_id':e['source_id'],'excerpt':e['quote'],'title':e['title']} for e in evidence],
        'plan':{'sections':[{'id':'S1','title':'Compare methods','question':'Which methods differ?'}]},'limitations':[],'steps':[]}
    engine.job={}; engine.candidate=('test','test',{}); engine.guard=lambda:None
    engine.persist=lambda:None; engine.status=lambda *a,**k:None
    engine.visuals()
    first=engine.cp['section_visuals']['S1']
    assert not first.get('finished',False)
    assert 'figure' in first['components'] and 'table' not in first['components']
    old=first['components']['figure']['lanes'][0]['evidence_ids']
    engine.cp['evidence'].reverse()
    engine.visuals()
    assert 'table' not in engine.cp['section_visuals']['S1']['components']
    engine.visuals()
    final=engine.cp['section_visuals']['S1']
    assert final['finished'] and set(final['components'])=={'figure','table'}
    assert final['components']['figure']['lanes'][0]['evidence_ids']==old==['E0000000001']
    assert '보편적 근사기' not in final['text'] and '### 그림.' in final['text']


@pytest.mark.parametrize('review',[
    {'supported':False,'issues':[]},
    {'supported':True,'issues':['원문 근거가 해당 비교를 지지하지 않습니다.']},
])
def test_visual_review_requires_unambiguous_approval_and_only_original_quotes(monkeypatch,review):
    proposed={'table':{'title':'방법 비교','columns':['방법','조건'],
        'rows':[{'cells':['원래 방법','조건 A'],'evidence_ids':['E1']}]},
        'figure':{'title':'독립 특성','caption':'원문에 있는 두 특성','kind':'comparison',
            'lanes':[{'label':'원래 방법','steps':['A and B','condition A'],'evidence_ids':['E1'],
                'step_evidence_ids':[['E1'],['E1']]}]}}
    def call(candidate,system,payload,schema,guard,**kwargs):
        assert all('statement' not in x for x in payload['evidence'])
        keys=set(schema['properties'])
        value={k:proposed[k] for k in keys} if keys in ({'figure'},{'table'}) else review
        return json.loads(json.dumps(value))
    monkeypatch.setattr(deep,'model_call',call)
    engine=object.__new__(deep.DeepResearch)
    evidence={'id':'E0000000001','source_id':'W1','quote':'Original method has A and B under condition A.',
        'statement':'An extractor summary that must not be supplied as proof.','title':'Original',
        'content_status':'document_text','location':'p.1','section_ids':['S1'],'category':'method','source_format':'pdf'}
    engine.cp={'evidence':[evidence],'sources':[{'source_id':'W1','excerpt':evidence['quote'],'title':'Original'}],
        'plan':{'sections':[{'id':'S1','title':'Compare methods','question':'Which methods differ?'}]},'limitations':[],'steps':[]}
    engine.job={}; engine.candidate=('test','test',{}); engine.guard=lambda:None
    engine.persist=lambda:None; engine.status=lambda *a,**k:None
    for _ in range(4): engine.visuals()
    entry=engine.cp['section_visuals']['S1']
    assert entry['finished'] and entry['status']=='unverified' and entry['text']==''


def test_search_engine_failure_preserves_sources_and_completes_other_research_tasks(setup,monkeypatch):
    http,service,_,_=setup
    from code_assist_v1.notion_ai_web import WebUnavailable
    class LimitedWeb(ResearchWeb):
        def search(self,query,guard,**kwargs):
            if query=='public query 0': raise WebUnavailable('search engine unavailable')
            return super().search(query,guard,**kwargs)
    service.web_reader=LimitedWeb()
    monkeypatch.setattr(deep,'provider_chunks',step_reply)
    record=create(http); op,_,_=submit(http,record)
    result=finish(service,record['id'],op)['messages'][-1]
    cp=service.store('owner').research_job(record['id'],op)['job']['checkpoint']
    assert any(x.get('status')=='unavailable' for x in cp['steps'])
    assert cp['sources'] and cp['evidence']
    assert '일부 추가 웹 검색의 검색엔진 응답을 받지 못했습니다.' in result['text']


def test_failed_public_korean_search_has_one_bounded_translation_without_private_context(monkeypatch):
    from types import SimpleNamespace
    from code_assist_v1.notion_ai_web import WebUnavailable
    calls=[]
    def search(query,guard,**kwargs): raise WebUnavailable('temporary upstream block')
    def model(candidate,system,payload,schema,guard,**kwargs):
        calls.append(payload)
        return {'query':'Graph Attention Networks original paper limitations experiments'}
    monkeypatch.setattr(deep,'model_call',model)
    engine=object.__new__(deep.DeepResearch)
    task={'kind':'web_search','query':'GAT의 기원과 실험 한계','section_id':'S1'}
    engine.cp={'tasks':[task],'searched':[],'steps':[],'limitations':[]}
    engine.job={}; engine.candidate=('test','test',{})
    engine.service=SimpleNamespace(web_reader=SimpleNamespace(search=search))
    engine.guard=lambda:None; engine.status=lambda *a,**k:None; engine.persist=lambda:None
    engine.collect(task); engine.cp['tasks'].pop(0)
    assert engine.cp['tasks'][0]['kind']=='public_query_translation'
    engine.collect(engine.cp['tasks'][0]); engine.cp['tasks'].pop(0)
    translated=engine.cp['tasks'][0]
    assert translated['translated'] and calls==[{'public_query':task['query']}]
    engine.collect(translated); engine.cp['tasks'].pop(0)
    assert not engine.cp['tasks'] and len(engine.cp['searched'])==2


def test_search_failure_does_not_swallow_revoked_permission():
    from types import SimpleNamespace
    from code_assist_v1.notion_ai_web import WebUnavailable
    def search(*args,**kwargs): raise WebUnavailable('unavailable')
    def guard(): raise PermissionError('revoked')
    engine=object.__new__(deep.DeepResearch)
    engine.cp={'searched':[],'steps':[],'limitations':[],'tasks':[]}
    engine.service=SimpleNamespace(web_reader=SimpleNamespace(search=search))
    engine.guard=guard; engine.status=lambda *a,**k:None
    with pytest.raises(PermissionError):
        engine.collect({'kind':'web_search','query':'public paper','section_id':'S1'})
    assert engine.cp['searched']==[] and engine.cp['steps']==[]


def test_numeric_review_receives_previous_table_header_not_only_next_caption():
    body = ("Table 2: Transductive classification accuracy.\nModel Cora Citeseer Pubmed\n"
            "GCN-64 81.4 70.9 79.0\nGAT 83.0 72.5 79.0\n"
            "Table 3: Inductive PPI micro-F1.\nGraphSAGE 61.2\nGAT 97.3\n")
    quote = "GAT 83.0 72.5 79.0\nTable 3: Inductive PPI micro-F1."
    item = {"id":"E1234567890", "source_id":"original", "chunk_id":"C1", "quote":quote,"title":"original"}
    engine = object.__new__(deep.DeepResearch)
    engine.cp = {"sources":[{"source_id":"original","excerpt":body}]}
    context = engine.review_context([item])[0]["surrounding_original_text"]
    assert "Model Cora Citeseer Pubmed" in context and "GAT 97.3" in context
    assert quote in context


def test_review_keeps_the_source_author_separate_from_authors_it_cites():
    header = 'A later analysis\nLater Author, 2022\n'
    quote = 'We show that the model introduced by Earlier Author (2018) has a limitation.'
    body = header + 'Original background. ' * 120 + quote
    item = {'id':'E1234567890','source_id':'later','chunk_id':'C2','quote':quote,'title':'A later analysis'}
    engine = object.__new__(deep.DeepResearch)
    engine.cp = {'sources':[{'source_id':'later','excerpt':body}]}
    context = engine.review_context([item])[0]
    assert header in context['document_identity_excerpt']
    assert quote in context['surrounding_original_text']
    claim = ('Earlier Author (2018) established this limitation in that original paper. ' * 5 + '[E1234567890].')
    separate = 'This later paper attributes the original model to Earlier Author. [Eabcdef1234].'
    parts = deep.review_parts(claim + ' ' + separate, width=430)
    assert any('[E1234567890]' in p for p in parts)
    assert any('[Eabcdef1234]' in p for p in parts)
    assert not any('[E1234567890]' in p and '[Eabcdef1234]' in p for p in parts)


def test_grouped_citations_and_unused_metadata_are_canonicalized_by_host(setup, monkeypatch):
    http, service, _, _ = setup
    def runner(*args, **kwargs):
        if set(kwargs["response_schema"]["properties"]) == {"answer", "used_evidence_ids"}:
            evidence = args[2]["evidence"]
            chosen = evidence[:2]
            yield json.dumps({"answer": "확인한 비교 근거 " + '[' + ', '.join(e['id'] for e in chosen) + ']',
                              "used_evidence_ids": [e['id'] for e in evidence]}, ensure_ascii=False)
        else:
            yield from step_reply(*args, **kwargs)
    monkeypatch.setattr(deep, "provider_chunks", runner)
    record = create(http); op, _, _ = submit(http, record)
    result = finish(service, record["id"], op)
    message = result['messages'][-1]
    assert "[1] [2]" in message['text'] and "[E" not in message['text']
    assert len(message['citations']) == 2


@pytest.mark.parametrize('unknown', ['[E0000000000]', '[E99] [E1]'])
def test_unknown_visible_citation_cannot_be_replaced_by_valid_metadata(setup, monkeypatch, unknown):
    http, service, _, _ = setup
    def runner(*args, **kwargs):
        if set(kwargs["response_schema"]["properties"]) == {"answer", "used_evidence_ids"}:
            yield json.dumps({"answer":"Unsupported source "+unknown,
                              "used_evidence_ids":[args[2]['evidence'][0]['id']]})
        else:
            yield from step_reply(*args, **kwargs)
    monkeypatch.setattr(deep, "provider_chunks", runner)
    record = create(http); op, _, _ = submit(http, record)
    for _ in range(100):
        if service.store('owner').research_job(record['id'], op)['state'] == 'failed':
            break
        service.research_jobs.run_one('owner', record['id'], op)
    assert service.store('owner').research_job(record['id'], op)['state'] == 'failed'
    assert service.get('owner', record['id'])['messages'] == []


def test_existing_message_routes_submit_durable_research_without_waiting_for_model(setup, monkeypatch):
    http, service, _, _ = setup
    monkeypatch.setattr(deep, 'provider_chunks', lambda *a, **k: pytest.fail('HTTP submit must not call the model'))
    for suffix in ('messages','messages/stream'):
        record = create(http)
        op = str(uuid4())
        response = http.post(f'/api/notion/ai-home/conversations/{record["id"]}/{suffix}', json={
            'mode':'research','message':'public question','operation_id':op,'expected_revision':0,
            'source_options':{'web':True,'notion':False}})
        assert response.status_code == 200
        pending = json.loads(response.data.decode()[6:])['conversation'] if suffix.endswith('stream') else response.json
        assert pending['pending'] and pending['messages'] == []
        assert service.store('owner').research_job(record['id'],op)['state'] == 'queued'


def test_resume_restores_transient_retry_allowance(setup, monkeypatch):
    http, service, _, _ = setup
    def broken(*args, **kwargs):
        raise ValueError('synthetic transient error')
        yield
    monkeypatch.setattr(deep, 'provider_chunks', broken)
    record = create(http); op, _, _ = submit(http, record)
    for _ in range(3):
        service.research_jobs.run_one('owner', record['id'], op)
    assert service.store('owner').research_job(record['id'],op)['state'] == 'failed'
    response = http.post(f'/api/notion/ai-home/conversations/{record["id"]}/research',
                         json={'operation_id':op,'action':'resume'})
    assert response.status_code == 200
    service.research_jobs.run_one('owner', record['id'], op)
    assert service.store('owner').research_job(record['id'],op)['state'] == 'queued'
    monkeypatch.setattr(deep, 'provider_chunks', step_reply)
    assert finish(service, record['id'],op)['revision'] == 1


def test_review_segmentation_preserves_citations_and_table_column_headers():
    citation = '[E1234567890]'
    paragraph = 'Supported explanation. ' * 90 + citation
    table = '| Model | Dataset | Accuracy |\n|---|---|---|\n' + '\n'.join(
        f'| Model {i} | Cora | {83+i}.0 {citation} |' for i in range(25))
    parts = deep.review_parts(paragraph + '\n\n' + table, width=250)
    assert sum(p.count(citation) for p in parts) == 26
    assert not any(re.search(r'\[E[a-f0-9]{0,9}$', p) for p in parts)
    table_parts = [p for p in parts if '| Model ' in p]
    assert table_parts and all('| Model | Dataset | Accuracy |' in p for p in table_parts)


def test_verified_subsection_survives_restart_and_summary_uses_finished_chapters(setup, monkeypatch):
    http, service, _, _ = setup
    writes, summaries = [], []
    def runner(*args, **kwargs):
        if set(kwargs['response_schema']['properties']) == {'answer','used_evidence_ids'}:
            payload = args[2]
            writes.append(payload['chapter']['id'])
            if payload['chapter']['id']=='SUMMARY':
                summaries.append(payload['chapter_conclusions'])
        yield from step_reply(*args, **kwargs)
    monkeypatch.setattr(deep,'provider_chunks',runner)
    record = create(http); op, _, _ = submit(http, record)
    for _ in range(80):
        service.research_jobs.run_one('owner',record['id'],op)
        cp = service.store('owner').research_job(record['id'],op)['job']['checkpoint']
        if any(s.get('parts') for s in cp.get('plan',{}).get('sections',[])):
            break
    completed_part = cp['plan']['sections'][0]['parts'][0]['id']
    interim = service.get('owner',record['id'])['research_job']
    assert interim['chapters'] and interim['chapters'][0]['verification']=='reviewed'
    service.research_jobs = ResearchJobs(service)
    result = finish(service,record['id'],op)
    assert writes.count(completed_part)==1 and result['revision']==1
    assert len(summaries[0])==6 and all(c['verification']=='reviewed' for c in summaries[0])


def test_invalid_critic_objection_is_checked_without_bypassing_real_rejection(setup, monkeypatch):
    http, service, _, _ = setup
    adjudications=[]
    def runner(*args, **kwargs):
        if set(kwargs['response_schema']['properties'])=={'supported','issues'}:
            if 'criticism' in args[2]:
                adjudications.append(args[2])
                yield json.dumps({'supported':True,'issues':[]})
            else:
                yield json.dumps({'supported':False,'issues':['A stylistic preference, not a factual error.']})
        else:
            yield from step_reply(*args,**kwargs)
    monkeypatch.setattr(deep,'provider_chunks',runner)
    record=create(http); op,_,_=submit(http,record)
    result=finish(service,record['id'],op)
    assert adjudications and not result['messages'][-1]['context_info']['research']['partial']
    cp=service.store('owner').research_job(record['id'],op)['job']['checkpoint']
    assert all(s['verification']=='reviewed' for s in cp['sections'])


@pytest.mark.parametrize('expression',[r'$f(Wx)$',r'`f(Wx)`'])
def test_equation_order_contradiction_is_rejected_even_when_source_review_passes(setup, monkeypatch, expression):
    http, service, _, _ = setup
    audits = []
    def runner(*args, **kwargs):
        payload = args[2]
        keys = set(kwargs['response_schema']['properties'])
        if keys == {'answer','used_evidence_ids'}:
            eid = payload['evidence'][0]['id']
            yield json.dumps({'answer':expression+' applies the input transform W after f. '+f'[{eid}]',
                              'used_evidence_ids':[eid]})
        elif keys == {'supported','issues'} and 'text' in payload:
            audits.append(payload)
            yield json.dumps({'supported':False,'issues':['The stated operation order contradicts f(Wx).']})
        else:
            yield from step_reply(*args, **kwargs)
    monkeypatch.setattr(deep,'provider_chunks',runner)
    record = create(http); op, _, _ = submit(http,record)
    result = finish(service,record['id'],op)
    assert audits and 'after f.' not in result['messages'][-1]['text']
    assert result['messages'][-1]['context_info']['research']['partial']


def test_scope_inference_audit_rejects_constant_values_from_fixed_rankings(monkeypatch):
    evidence=[{'id':'E0000000001','source_id':'W1','chunk_id':'C1',
        'statement':'The ranking is fixed.','quote':'The ranking is fixed, but the numerical values may vary.',
        'title':'Synthetic original','location':'p.1','section_ids':['S1'],'category':'method',
        'content_status':'document_text','source_format':'pdf'}]
    wrong='A fixed ranking means its weights are always constant. [E1]'
    semantic_calls, prior_drafts = [], []
    def runner(*args, **kwargs):
        payload=args[2]
        keys=set(kwargs['response_schema']['properties'])
        if 'answerability' in keys:
            yield from step_reply(*args,**kwargs)
        elif 'answer' in keys:
            prior_drafts.append(payload['draft_to_correct'])
            yield json.dumps({'answer':wrong,'used_evidence_ids':['E1']})
        elif keys == {'checks'}:
            semantic_calls.append(payload)
            yield json.dumps({'checks':[{'claim_id':c['id'],'claim_object':'numeric_values','proof_object':'ordering',
                'claim_variable':'query','proof_variable':'query',
                'proof_ids':['P1'],'supported':True,'reason':'A fixed ranking.'} for c in payload['claims']]})
        else:
            yield json.dumps({'supported':True,'issues':[]})
    monkeypatch.setattr(deep,'provider_chunks',runner)
    engine=object.__new__(deep.DeepResearch)
    engine.cp={'evidence':evidence,'sources':[{'source_id':'W1','excerpt':evidence[0]['quote'],'title':'Synthetic original'}],
        'coverage':[],'plan':{'title':'Invariance','scope':'Synthetic comparison'},'limitations':[]}
    engine.job={}; engine.candidate=('test','test',{}); engine.guard=lambda:None
    engine.persist=lambda:None; engine.status=lambda *a,**k:None
    part={'id':'S1.P1','parent_id':'S1','title':'Invariance','question':'What is fixed?'}
    assert engine.write_piece(part) is None
    assert engine.write_piece(part) is None
    result=engine.write_piece(part)
    assert semantic_calls and prior_drafts[0]=='' and 'weights are always constant' in prior_drafts[1]
    assert result['verification']=='partial' and 'weights are always constant' not in result['answer']
    assert evidence[0]['quote'] in result['answer']


def test_unsupported_numeric_invariance_is_not_overridden_by_a_later_acceptance(monkeypatch):
    calls=[]
    def runner(*args, **kwargs):
        calls.append(args[2])
        yield json.dumps({'checks':[{'claim_id':'C1','claim_object':'numeric_values','proof_object':'numeric_values',
            'claim_variable':'query','proof_variable':'query',
            'proof_ids':['P1'],'supported':len(calls)>1,'reason':'Only rankings are proven invariant.'}]})
    monkeypatch.setattr(deep,'provider_chunks',runner)
    result=deep.review_conceptual_claims(('test','test',{}),['Weights always remain constant.'],
        [{'quote':'Their ranking is fixed, but values may change.'}],[],lambda:None)
    assert len(calls)==2 and not result['supported']
    assert result['issues']==['Only rankings are proven invariant.']


@pytest.mark.parametrize('claim_variable,proof_variable,supported',[
    ('neighborhood','query',False),('query','query',True),('query','other',True)])
def test_conceptual_audit_preserves_the_variable_in_the_original_condition(monkeypatch,claim_variable,proof_variable,supported):
    def runner(*args,**kwargs):
        yield json.dumps({'checks':[{'claim_id':'C1','claim_object':'ordering','proof_object':'ordering',
            'claim_variable':claim_variable,'proof_variable':proof_variable,'proof_ids':['P1'],
            'supported':True,'reason':'Ranking is independent of the query with keys fixed.'}]})
    monkeypatch.setattr(deep,'provider_chunks',runner)
    result=deep.review_conceptual_claims(('test','test',{}),['Dynamic ranking changes with the neighborhood.'],
        [{'quote':'Dynamic attention conditions the ranking on the query with fixed keys.'}],[],lambda:None)
    assert result['supported'] is supported
    if not supported:
        assert '입력 변화의 조건' in result['issues'][0]


def test_neighbor_presence_independence_does_not_define_static_attention():
    wrong='각 이웃 노드의 점수가 다른 이웃의 존재 여부와 무관하게 독립적으로 결정되게 하여 정적(static) 어텐션으로 분류된다.'
    assert deep.static_attention_clarity_issues(wrong)
    correct='GAT의 정적 어텐션은 키를 고정했을 때 쿼리 변화에 대해 이웃 순위가 독립적이라는 뜻이다.'
    assert not deep.static_attention_clarity_issues(correct)
    assert 'Dynamic ranking changes with the neighborhood.' in deep.conceptual_claims('Dynamic ranking changes with the neighborhood.')


@pytest.mark.parametrize('quote,expected',[
    ('25\n\n[PDF page 26]\nGATv2 equation.','PDF pp.25–26'),
    ('GATv2 equation.','PDF p.26'),
    ('Missing quoted text.','PDF p.25')])
def test_exact_pdf_quote_location_includes_cross_page_context(quote,expected):
    text='[PDF page 25]\nPrior table.\n25\n\n[PDF page 26]\nGATv2 equation.\n[PDF page 27]\nAppendix.'
    assert deep.pdf_quote_location(text,quote,'PDF p.25')==expected


@pytest.mark.parametrize('bad',['The original question is partially covered, but the remaining',
    'partial','일부 사항은 부분적_coverage_로 평가됩니다.'])
def test_deep_coverage_presentation_is_korean_without_rewriting_stored_answers(bad):
    from copy import deepcopy
    from code_assist_v1.notion_ai_report_format import present_report
    from code_assist_v1.notion_ai_chat_artifacts import export
    original={'text':'# Report\n\n원문 인용: '+bad+' [1]\n\n## 조사 범위 점검\n\n- 비교: 일부 확인 — '+bad+
        '\n\n## 확인 범위와 한계\n\n- Figures were not read.',
        'context_info':{'research':{'depth':'deep','coverage':[{'section_id':'S1','status':'partial','reason':bad}]}},
        'citations':[{'source_id':'E0000000001','title':'Original','url':'https://example.org/paper',
                      'quote':bad,'location':'PDF pp.25–26'}]}
    baseline=deepcopy(original)
    shown=present_report(original)
    assert original==baseline and shown==present_report(shown)
    assert '원문 인용: '+bad+' [1]' in shown['text']
    assert '비교: 일부 확인 — 확보한 원문은 있으나' in shown['text']
    assert shown['context_info']['research']['coverage'][0]['reason'].startswith('확보한 원문')
    downloaded=export(original,'md')[0].decode('utf-8-sig')
    assert shown['text'] in downloaded and '[1] [Original](https://example.org/paper)' in downloaded
    assert bad in downloaded and 'PDF pp.25–26' in downloaded


@pytest.mark.parametrize('bad',[
    '상수 어텐션으로 비교했습니다. [E1]\n\n이 설정은 모델 용량을 향상'+('\n'*120),
    '현재 제공된 증거(E1, E2)는 이 실험 설정을 설명합니다. [E1]',
    ('상수 어텐션을 적용한 동일한 아키텍처와 비교했습니다. [E1]\n\n'*6)+'모델의 고유한',
    ('The paper used an otherwise matched constant-attention control. [E1]\n\n'*5)+'This isolates the contribution of'])
def test_padded_incomplete_output_and_provenance_aliases_cannot_be_verified(monkeypatch,bad):
    evidence=[{'id':'E0000000001','source_id':'W1','chunk_id':'C1','statement':'A matched constant-attention control.',
        'quote':'We also compare an otherwise matched architecture with constant attention.',
        'title':'Synthetic original','location':'p.1','section_ids':['S1'],'category':'method',
        'content_status':'document_text','source_format':'pdf'}]
    writes=[]
    def runner(*args,**kwargs):
        if 'answer' in kwargs['response_schema']['properties']:
            writes.append(args[2])
            yield json.dumps({'answer':bad if len(writes)==1 else '상수 어텐션을 대조군으로 비교했습니다. [E1]',
                              'used_evidence_ids':['E1']})
        else:
            yield from step_reply(*args,**kwargs)
    monkeypatch.setattr(deep,'provider_chunks',runner)
    engine=object.__new__(deep.DeepResearch)
    engine.cp={'evidence':evidence,'sources':[{'source_id':'W1','excerpt':evidence[0]['quote'],'title':'Synthetic original'}],
        'coverage':[],'plan':{'title':'Comparison','scope':'Synthetic comparison'},'limitations':[]}
    engine.job={}; engine.candidate=('test','test',{}); engine.guard=lambda:None
    engine.persist=lambda:None; engine.status=lambda *a,**k:None
    part={'id':'S1.P1','parent_id':'S1','title':'Comparison','question':'What was the control?'}
    assert engine.write_piece(part) is None
    result=engine.write_piece(part)
    assert result['verification']=='reviewed' and not deep.draft_structure_issues(result['answer'])
    assert not deep.draft_structure_issues(('상수 어텐션을 대조군으로 비교했습니다. [E1]\n\n'*12).strip())
    assert writes[1]['previous_review_issues'] and result['answer']=='상수 어텐션을 대조군으로 비교했습니다. [E0000000001]'


def test_static_attention_without_named_invariant_requires_clarification(monkeypatch):
    evidence=[{'id':'E0000000001','source_id':'W1','chunk_id':'C1','statement':'Static means ranking invariance.',
        'quote':'Static attention has a query-independent ranking, but numerical values may change.',
        'title':'Synthetic original','location':'p.1','section_ids':['S1'],'category':'method',
        'content_status':'document_text','source_format':'pdf'}]
    writes=[]
    def runner(*args, **kwargs):
        if 'answer' in kwargs['response_schema']['properties']:
            writes.append(args[2])
            answer=('정적 어텐션은 어텐션 가중치가 동적으로 재계산되지 못하게 합니다.' if len(writes)==1
                else '정적 어텐션은 어텐션 가중치의 순위가 고정됨을 말하며, 수치는 변할 수 있습니다.')
            yield json.dumps({'answer':answer+' [E1]','used_evidence_ids':['E1']})
        else:
            yield from step_reply(*args, **kwargs)
    monkeypatch.setattr(deep,'provider_chunks',runner)
    engine=object.__new__(deep.DeepResearch)
    engine.cp={'evidence':evidence,'sources':[{'source_id':'W1','excerpt':evidence[0]['quote'],'title':'Synthetic original'}],
        'coverage':[],'plan':{'title':'Invariance','scope':'Synthetic comparison'},'limitations':[]}
    engine.job={}; engine.candidate=('test','test',{}); engine.guard=lambda:None
    engine.persist=lambda:None; engine.status=lambda *a,**k:None
    part={'id':'S1.P1','parent_id':'S1','title':'Invariance','question':'What is fixed?'}
    assert engine.write_piece(part) is None
    result=engine.write_piece(part)
    assert result['verification']=='reviewed' and '순위가 고정' in result['answer']
    assert '재계산되지' not in result['answer']


@pytest.mark.parametrize('expression,correct,wrong',[
    ('e=LeakyReLU(a^T [Wx||Wy])','먼저 내적을 계산하고, 마지막으로 LeakyReLU를 적용합니다.',
     '먼저 LeakyReLU를 적용하고, 마지막으로 가중치 벡터와의 내적을 계산합니다.'),
    ('e=a^T LeakyReLU(W[x||y])','먼저 LeakyReLU를 적용하고, 마지막으로 가중치 벡터와의 내적을 계산합니다.',
     '먼저 내적을 계산하고, 마지막으로 LeakyReLU를 적용합니다.')])
@pytest.mark.parametrize('delimiter',['$','`'])
def test_explicit_operator_order_is_enforced_when_model_audits_wrongly_accept(monkeypatch,expression,correct,wrong,delimiter):
    evidence=[{'id':'E0000000001','source_id':'W1','chunk_id':'C1','statement':'Original equation.',
        'quote':expression,'title':'Synthetic original','location':'p.1','section_ids':['S1'],
        'category':'method','content_status':'document_text','source_format':'pdf'}]
    writes=[]
    def runner(*args, **kwargs):
        if 'answer' in kwargs['response_schema']['properties']:
            writes.append(args[2])
            answer='스코어 $e_{ij}$에 대해 '+(wrong if len(writes)==1 else correct)+' '+delimiter+expression+delimiter+' [E1]'
            yield json.dumps({'answer':answer,'used_evidence_ids':['E1']})
        else:
            yield from step_reply(*args, **kwargs)
    monkeypatch.setattr(deep,'provider_chunks',runner)
    engine=object.__new__(deep.DeepResearch)
    engine.cp={'evidence':evidence,'sources':[{'source_id':'W1','excerpt':expression,'title':'Synthetic original'}],
        'coverage':[],'plan':{'title':'Equations','scope':'Operator order'},'limitations':[]}
    engine.job={}; engine.candidate=('test','test',{}); engine.guard=lambda:None
    engine.persist=lambda:None; engine.status=lambda *a,**k:None
    part={'id':'S1.P1','parent_id':'S1','title':'Equation','question':'Explain operator order.'}
    assert engine.write_piece(part) is None
    result=engine.write_piece(part)
    assert result['verification']=='reviewed' and correct in result['answer'] and wrong not in result['answer']


def test_tokenizer_uses_only_the_configured_local_model_and_preserves_guards(monkeypatch):
    monkeypatch.setenv('CODE_ASSIST_RESEARCH_LOCAL_REASONING','1')
    monkeypatch.setattr(deep.internal_llm,'resolve_api_token',lambda cfg:('synthetic-token',None))
    calls, checks = [], []
    class Response:
        def __init__(self, data): self.data=data
        def raise_for_status(self): pass
        def json(self): return self.data
    def post(url, **kwargs):
        calls.append(url)
        assert kwargs['allow_redirects'] is False
        assert kwargs['headers']['Authorization']=='Bearer synthetic-token'
        return Response({'prompt':'local model prompt'} if url.endswith('/apply-template') else {'tokens':[1,2,3,4]})
    monkeypatch.setattr(deep.requests,'post',post)
    cfg={'url':'http://127.0.0.1:8080/v1/chat/completions','model':'qwen3.6'}
    assert deep.local_prompt_tokens(cfg,'system','PRIVATE text',lambda:checks.append(True))==4
    assert calls==['http://127.0.0.1:8080/apply-template','http://127.0.0.1:8080/tokenize']
    assert len(checks)==3
    assert deep.local_prompt_tokens({**cfg,'url':'https://external.example/v1/chat/completions'},'system','PRIVATE text',lambda:None) is None
    assert len(calls)==2


def test_korean_extraction_counts_local_tokens_without_enabling_reasoning(monkeypatch):
    monkeypatch.setenv('CODE_ASSIST_RESEARCH_LOCAL_REASONING','1')
    counted=[]; wire=[]
    monkeypatch.setattr(deep,'local_prompt_tokens',lambda *args:counted.append(args) or 800)
    def runner(*args,**kwargs):
        wire.append(kwargs); yield '{"findings":[]}'
    monkeypatch.setattr(deep,'provider_chunks',runner)
    cfg={'url':'http://127.0.0.1:8080/v1/chat/completions','model':'qwen3.6','context_window':8192}
    assert deep.model_call(('test','test',cfg),'Extract.',{'quotes':'한글 원문 '*3000},
        deep.obj({'findings':deep.array(deep.obj({'statement':deep.string(400)}),4)}),lambda:None)=={'findings':[]}
    assert counted and wire[0]['reasoning_char_budget'] is None and wire[0]['max_tokens']==2400


def test_oversized_analysis_splits_durable_ranges_without_losing_original_text(monkeypatch):
    engine=object.__new__(deep.DeepResearch)
    original='한글 구간의 원문입니다. '*200
    source={'source_id':'P1','title':'합성 한글 자료','kind':'notion','excerpt':original,
        'content_status':'document_text','chunks':[{'id':'C1','text':original,'location':'text 0:3600'}],'analyzed':[]}
    engine.cp={'plan':{'sections':[{'id':'S1','question':'합성 설명'}]},'sources':[source],'evidence':[]}
    engine.candidate=('test','test',{}); engine.guard=lambda:None; engine.status=lambda *a,**k:None
    calls=[]
    def reply(candidate,task,payload,schema,guard,**kwargs):
        calls.append(payload)
        assert 'text' not in payload['source']
        assert schema['properties']['findings']['items']['properties']['quote_id']['enum'][0]=='Q1'
        if sum(len(q['text']) for q in payload['quote_choices'])>1800:
            raise deep.ResearchContextTooLarge('research step context exceeds model capacity')
        q=payload['quote_choices'][0]
        return {'findings':[{'statement':'합성 원문 구간의 설명입니다.','quote_id':q['id'],'category':'method','section_ids':['S1']}]}
    monkeypatch.setattr(deep,'model_call',reply)
    for _ in range(30):
        pending=next((c for c in source['chunks'] if c['id'] not in source['analyzed']),None)
        if pending is None: break
        engine.analyze(source,pending)
    assert len(source['chunks'])>1 and ''.join(c['text'] for c in source['chunks'])==original
    assert set(source['analyzed'])=={c['id'] for c in source['chunks']}
    assert engine.cp['evidence'] and all(e['quote'] in original and 'quote_id' not in e for e in engine.cp['evidence'])


def test_reasoning_reserves_space_for_a_closed_answer_with_actual_input_count(monkeypatch):
    monkeypatch.setenv('CODE_ASSIST_RESEARCH_LOCAL_REASONING','1')
    monkeypatch.setattr(deep,'local_prompt_tokens',lambda *args:800)
    wire=[]
    def runner(*args, **kwargs):
        wire.append(kwargs)
        yield json.dumps({'answer':'완성된 답변입니다.'},ensure_ascii=False)
    monkeypatch.setattr(deep,'provider_chunks',runner)
    cfg={'url':'http://127.0.0.1:8080/v1/chat/completions','model':'qwen3.6','context_window':4096}
    result=deep.model_call(('test','test',cfg),'Write a grounded answer.',{'data':'가'*4000},
        deep.obj({'answer':deep.string(1200)}),lambda:None,reasoning=True)
    assert result['answer'] and wire[0]['reasoning_char_budget']==3300
    assert wire[0]['max_tokens']==2400
    assert 24 < wire[0]['response_schema']['properties']['answer']['maxLength'] < 1200


def test_review_never_drops_a_cited_proof_from_the_bounded_writer_pool(monkeypatch):
    evidence=[]
    for i in (1,2):
        evidence.append({'id':f'E{i:010x}','source_id':f'W{i}','chunk_id':'C1','statement':'s'*400,
            'quote':'q'*1400,'title':f'Source {i}','location':'p.1','section_ids':['S1'],
            'category':'method','source_format':'pdf','content_status':'document_text'})
    assert 3500 < sum(len(json.dumps(e,ensure_ascii=False).encode()) for e in deep.DeepResearch.prompt_evidence(evidence)) < 4500
    calls=[]
    def runner(*args, **kwargs):
        payload=args[2]
        if 'answerability' in kwargs['response_schema']['properties']:
            yield from step_reply(*args,**kwargs)
        elif 'answer' in kwargs['response_schema']['properties']:
            ids=[e['id'] for e in payload['evidence']]
            yield json.dumps({'answer':'A comparison with both proofs. '+' '.join(f'[{eid}]' for eid in ids),
                              'used_evidence_ids':ids})
        else:
            calls.append(payload)
            assert {e['id'] for e in payload['evidence']}=={e['id'] for e in evidence}
            yield json.dumps({'supported':True,'issues':[]})
    monkeypatch.setattr(deep,'provider_chunks',runner)
    engine=object.__new__(deep.DeepResearch)
    engine.cp={'evidence':evidence,'sources':[{'source_id':e['source_id'],'excerpt':e['quote'],'title':e['title']} for e in evidence],
        'coverage':[],'plan':{'title':'Comparison','scope':'Both originals'},'limitations':[]}
    engine.job={}; engine.candidate=('test','test',{}); engine.guard=lambda:None
    engine.persist=lambda:None; engine.status=lambda *args,**kwargs:None
    result=engine.write_piece({'id':'S1.P1','parent_id':'S1','title':'Comparison','question':'Compare both originals.'})
    assert calls and result['verification']=='reviewed'

from __future__ import annotations

import json
import multiprocessing
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from code_assist_v1 import internal_llm, notion_ai_notes as notes, routes_notion_ai_notes as routes
from code_assist_v1.notion_notes_comparison import ComparisonService, ComparisonStore, VERSION
from code_assist_v1.notion_notes_knowledge import KnowledgeReader, search_query
from code_assist_v1.tests.test_notion_ai_notes import client, SOURCE, RESULT, _request

REFERENCE = {"id": "ref-1", "title": "출시 기준", "revision_id": "revision-1", "space_id": "wiki-1",
             "excerpt": "출시 전 보고서 검토가 필요합니다.", "excerpt_sha256": "synthetic"}
ENRICHED = {**RESULT, "knowledge_insights": [{"text": "기존 지식에는 출시 전 보고서 검토가 필요하다고 명시되어 있습니다.",
                                            "source_id": "ref-1", "source_quote": REFERENCE["excerpt"]}]}


class Reader:
    allowed = True
    empty = False

    def authorize(self, uid, space):
        if not self.allowed or uid != "owner" or space != "wiki-1":
            raise PermissionError("scope revoked")

    def scopes(self, uid, cursor=None):
        self.authorize(uid, "wiki-1")
        return {"items": [{"space_id": "wiki-1", "display_name": "내 Wiki"}], "next_cursor": None}

    def retrieve(self, uid, space, query):
        self.authorize(uid, space)
        return {"items": [] if self.empty else [REFERENCE], "excerpt_bytes": 100, "truncated": False, "mode": "synthetic"}

    def revalidate(self, uid, space, snapshot):
        self.authorize(uid, space)


@pytest.fixture
def comparison(client, tmp_path):
    http, page_id = client
    store = ComparisonStore(tmp_path / "host-private" / "compare.db", "synthetic host key that is long enough")
    store.initialize()
    reader = Reader()
    service = ComparisonService(http.application, store, reader, routes.require_edit)
    http.application.extensions[VERSION] = service
    return http, page_id, service


def submit(http, page_id, **updates):
    data = dict(page_id=page_id, transcript=SOURCE, knowledge_space_id="wiki-1", comparison_consent=True,
                operation_id=str(uuid4()), output_language="한국어와 영어 병기", model_id="gemma4")
    data.update(updates)
    return http.post("/api/notion/ai-notes/comparisons", json=data)


def model(*args, **kwargs):
    result = ENRICHED if "knowledge_references" in args[1] else RESULT
    return json.dumps(result), {"model_id": kwargs["model_ids"][0], "model_used": "Synthetic model", "model_kind": "hynix_api"}


def test_host_pair_hidden_encrypted_same_model_replay_and_owner_isolation(comparison, monkeypatch):
    http, page_id, service = comparison
    operation = str(uuid4())
    with patch.object(internal_llm, "run_internal_text", side_effect=model) as runner:
        response = submit(http, page_id, operation_id=operation)
        assert response.status_code == 202 and set(response.json) == {"id", "state", "message"}
        runner.assert_not_called()
        assert submit(http, page_id, operation_id=operation).json == response.json
        assert service.process_one()
        assert len(runner.call_args_list) == 2
        assert all(call.kwargs["model_ids"] == ["gemma4"] for call in runner.call_args_list)
        assert "knowledge_references" not in runner.call_args_list[0].args[1]
        assert runner.call_args_list[1].args[1]["knowledge_references"] == [REFERENCE]
        assert submit(http, page_id, operation_id=operation).json["state"] == "completed"
        assert not service.process_one()
    url = "/api/notion/ai-notes/comparisons/" + response.json["id"]
    status = http.get(url)
    assert status.json["state"] == "completed" and set(status.json) == {"id", "state", "message"}
    assert status.headers["Cache-Control"] == "no-store"
    assert RESULT["summary"] not in status.text and SOURCE not in status.text
    records = list(service.store.host_records())
    assert records[0]["baseline"]["result"] == RESULT and records[0]["enriched"]["result"] == ENRICHED
    assert records[0]["quality_verified"] is False
    assert records[0]["input"]["output_language"] == "한국어와 영어 병기"
    assert SOURCE.encode() not in service.store.path.read_bytes()
    assert RESULT["summary"].encode() not in service.store.path.read_bytes()
    assert submit(http, page_id, operation_id=operation, transcript=SOURCE+"변경").status_code == 400
    monkeypatch.setattr(routes, "current_user_id", lambda: "stranger")
    assert http.get(url).status_code == 404
    assert http.get("/api/notion/ai-notes/comparison-sources").status_code == 403


def test_no_evidence_and_revoked_scope_never_fake_comparison(comparison):
    http, page_id, service = comparison
    service.reader.empty = True
    response = submit(http, page_id)
    with patch.object(internal_llm, "run_internal_text") as runner:
        service.process_one()
        runner.assert_not_called()
    assert service.store.status("owner", response.json["id"])["state"] == "no_evidence"
    service.reader.empty = False
    response = submit(http, page_id)
    def revoke(*args, **kwargs):
        service.reader.allowed = False
        return model(*args, **kwargs)
    with patch.object(internal_llm, "run_internal_text", side_effect=revoke) as runner:
        service.process_one()
        assert runner.call_count == 1
    assert service.store.status("owner", response.json["id"])["state"] == "failed"
    assert "baseline" not in list(service.store.host_records())[-1]


def test_model_pair_fallback_restarts_both_arms(comparison):
    http, page_id, service = comparison
    response = submit(http, page_id, model_id="")
    def attempt(*args, **kwargs):
        if kwargs["model_ids"] == ["gemma4"] and "knowledge_references" in args[1]:
            return None, {}
        return model(*args, **kwargs)
    with patch.object(internal_llm, "run_internal_text", side_effect=attempt) as runner:
        service.process_one()
    assert [call.kwargs["model_ids"][0] for call in runner.call_args_list] == ["gemma4", "gemma4", "qwen3.6", "qwen3.6"]
    record = list(service.store.host_records())[0]
    assert record["state"] == "completed" and record["model_id"] == "qwen3.6"
    assert record["baseline"]["model_id"] == record["enriched"]["model_id"]


@pytest.mark.parametrize("fields", [{"comparison_consent": False}, {"knowledge_space_id": "someone-else"},
                                   {"operation_id": "invalid"}, {"summary_phase": "provisional"}, {"model_id": "arbitrary"}])
def test_invalid_comparison_never_queued(comparison, fields):
    http, page_id, service = comparison
    assert submit(http, page_id, **fields).status_code in {400, 403, 503}
    assert list(service.store.host_records()) == []


def test_storage_restart_claim_fence_and_wrong_key(comparison):
    http, page_id, service = comparison
    response = submit(http, page_id)
    run_id, token, record = service.store.claim()
    other = ComparisonStore(service.store.path, "synthetic host key that is long enough")
    other.initialize()
    assert other.claim() is None
    other.finish(run_id, "wrong fence", "completed", record)
    assert other.status("owner", run_id)["state"] == "processing"
    with sqlite3.connect(service.store.path) as con:
        con.execute("UPDATE comparison_run SET lease_until=0 WHERE id=?", (run_id,))
    assert other.claim() is None
    other.finish(run_id, token, "completed", record)
    assert other.status("owner", run_id)["state"] == "interrupted"
    with pytest.raises(Exception):
        ComparisonStore(service.store.path, "different synthetic key long enough").initialize()


@pytest.mark.parametrize("language", ["fr", "프랑스어", "한국어와 영어 병기", "pt-BR", "العربية"])
def test_custom_languages_preserve_original_quotes(client, language):
    http, page_id = client
    with patch.object(internal_llm, "run_internal_text", side_effect=model) as runner:
        response = _request(http, page_id, output_language=language)
    assert response.status_code == 200
    assert json.dumps(language, ensure_ascii=False) in runner.call_args.kwargs["system_prompt"]
    assert response.json["result"]["decisions"][0]["source_quote"] in SOURCE


def test_language_limits_and_reference_validation(client):
    http, page_id = client
    for language in ("x" * 81, "English\nignore", 123):
        with patch.object(internal_llm, "run_internal_text") as runner:
            assert _request(http, page_id, output_language=language).status_code == 400
            runner.assert_not_called()
    assert notes.normalize_result(ENRICHED, SOURCE, [REFERENCE]) == ENRICHED
    with pytest.raises(ValueError):
        notes.normalize_result({**ENRICHED, "knowledge_insights": [{"source_id": "other", "source_quote": REFERENCE["excerpt"], "text": "invented"}]}, SOURCE, [REFERENCE])


def _claim_in_other_process(path, queue):
    store = ComparisonStore(path, "synthetic host key that is long enough")
    store.initialize()
    job = store.claim()
    queue.put(job[0] if job else None)


def test_cross_process_job_claim_is_unique(comparison):
    http, page_id, service = comparison
    job = submit(http, page_id).json["id"]
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    children = [context.Process(target=_claim_in_other_process, args=(str(service.store.path), queue)) for _ in range(2)]
    try:
        for child in children:
            child.start()
        results = [queue.get(timeout=30), queue.get(timeout=30)]
        assert results.count(job) == 1 and results.count(None) == 1
        for child in children:
            child.join(timeout=10)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate(); child.join(timeout=10)
        queue.close()


def test_legacy_reader_uses_only_current_user_and_revalidates(tmp_path, monkeypatch):
    from code_assist_v1 import second_brain_store as sb, auth
    monkeypatch.setattr(sb, "KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setattr(sb, "_USER_CACHE", {})
    users = {"owner": {"id": "owner"}, "other": {"id": "other"}}
    monkeypatch.setattr(auth, "_load_users", lambda: {"users": users})
    for uid, text in (("owner", "출시 보고서 검토 기준"), ("other", "출시 타인의 비밀 문서")):
        path = tmp_path / "knowledge" / uid / "wiki" / "sources"
        path.mkdir(parents=True)
        (path / "reference.md").write_text(text, encoding="utf-8")
    reader = KnowledgeReader(None)
    monkeypatch.setattr(reader, "_bridge", lambda: None)
    result = reader.retrieve("owner", "legacy-personal", "출시 보고서")
    assert result["items"] and all("타인의" not in item["excerpt"] for item in result["items"])
    assert result["excerpt_bytes"] <= 98304
    reader.revalidate("owner", "legacy-personal", result)
    (tmp_path / "knowledge" / "owner" / "wiki" / "sources" / "reference.md").write_text("변경된 기준", encoding="utf-8")
    sb._invalidate_cache("owner")
    with pytest.raises(PermissionError):
        reader.revalidate("owner", "legacy-personal", result)
    users["owner"]["disabled"] = True
    with pytest.raises(PermissionError):
        reader.retrieve("owner", "legacy-personal", "출시")


def test_disabled_canonical_reader_never_falls_back_to_legacy(monkeypatch):
    from code_assist_v1 import knowledge_core
    core = SimpleNamespace(flags=SimpleNamespace(core_enabled=True, personal_reads=False))
    monkeypatch.setattr(knowledge_core, "get_knowledge_core", lambda app: core)
    with pytest.raises(notes.NotesUnavailable):
        KnowledgeReader(None).scopes("owner")
    assert "마지막안건" in search_query({"transcript": "처음안건 " * 100 + "마지막안건", "notes": ""})


def test_background_worker_runs_without_browser_session(comparison):
    http, page_id, service = comparison
    job = submit(http, page_id).json["id"]
    with patch.object(internal_llm, "run_internal_text", side_effect=model):
        service.start()
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and service.store.status("owner", job)["state"] in {"queued", "processing"}:
                time.sleep(.05)
            assert service.store.status("owner", job)["state"] == "completed"
        finally:
            service.stop.set(); service.wake.set(); service.thread.join(timeout=5)
            assert not service.thread.is_alive()

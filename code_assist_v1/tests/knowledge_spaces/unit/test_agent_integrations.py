"""AIK-001..012: external grants, evidence, retries and canonical adapters."""
import json
import multiprocessing
from types import SimpleNamespace
from uuid import uuid4

from flask import Flask
import pytest

from code_assist_v1.agent_integrations import authoring
from code_assist_v1.agent_integrations.contracts import IntegrationError, submission_input
from code_assist_v1.agent_integrations.store import IntegrationStore
from code_assist_v1.agent_integrations.service import IntegrationService
from code_assist_v1.agent_integrations.routes import register, bearer_auth_gate

pytestmark = pytest.mark.unit
READ, WRITE, FOREIGN = [str(uuid4()) for _ in range(3)]


class Bridge:
    def __init__(self):
        self.epoch, self.active, self.archived, self.publications = 1, True, set(), []

    def actor(self, owner):
        if not self.active or owner not in {"alice", "bob"}:
            raise IntegrationError("inactive", 401)
        return dict(id=owner, scope_epoch=self.epoch)

    def authorize(self, actor, space, write=False):
        if space not in {READ, WRITE} or space in self.archived or actor["id"] != "alice":
            raise IntegrationError("not found", 404)

    def search(self, actor, space, query):
        self.authorize(actor, space)
        return dict(items=[], space_id=space)

    def publish(self, actor, rid, space, title, markdown, verify, *, commit_guard=None):
        verify()
        self.authorize(actor, space, write=True)
        self.publications.append(markdown)
        return dict(results=[{"item_id": str(uuid4()), "space_id": space}])


def model_output(category="source_statement", quote="금요일까지 검토 부탁드립니다.", sid="mail-1"):
    return dict(summary="검토 요청에 대한 정리", claims=[dict(text="검토 요청을 받음", category=category,
                evidence=[dict(source_id=sid, quote=quote)])], warnings=[])


@pytest.fixture
def service(tmp_path):
    store = IntegrationStore(tmp_path / "integrations.db", b"x"*32)
    store.initialize()
    runner = lambda *a, **k: (json.dumps(model_output(), ensure_ascii=False), {"model_kind": "fixture"})
    return IntegrationService(store, Bridge(), runner=runner)


def connect(service, **extra):
    data = dict(operation_id=str(uuid4()), name="Hermes", read_space_id=READ, write_space_id=WRITE,
                scopes=["knowledge:read", "results:write"])
    data.update(extra)
    return service.create_connection("alice", data)


def submission(**extra):
    data = dict(operation_id=str(uuid4()), kind="email.triage", title="검토 요청", external_key="mailbox:123:v1",
                sources=[dict(id="mail-1", kind="email", title="협력사 메일", text="금요일까지 검토 부탁드립니다.")],
                result="회신을 권장합니다.", actions=[dict(description="회신", completion_condition="해당 스레드에 회신 발송")])
    data.update(extra)
    return data


def submitted(service, conn=None):
    conn = conn or connect(service)
    return conn, service.submit("alice", conn["id"], submission())["id"]


def test_aik_001_token_scope_expiry_revocation_and_no_secret_storage(service):
    conn = connect(service)
    assert service.authenticate(conn["token"])["owner_id"] == "alice"
    with pytest.raises(IntegrationError):
        service.authorize_connection("alice", conn["id"], "notion:write")
    with service.store.connection() as c:
        dumps = "\n".join(c.iterdump())
    assert conn["token"] not in dumps
    service.store.revoke("alice", conn["id"], str(uuid4()), lambda: None)
    with pytest.raises(IntegrationError):
        service.authenticate(conn["token"])


def test_aik_002_identity_and_wiki_changes_invalidate_grant(service):
    conn = connect(service)
    service.bridge.epoch = 2
    with pytest.raises(IntegrationError):
        service.authenticate(conn["token"])
    service.bridge.epoch = 1
    service.bridge.archived.add(READ)
    with pytest.raises(IntegrationError):
        service.authenticate(conn["token"])


def test_aik_003_connection_retry_and_changed_operation(service):
    data = dict(operation_id=str(uuid4()), name="Hermes", read_space_id=READ, write_space_id=WRITE,
                scopes=["knowledge:read"])
    first = service.create_connection("alice", data)
    assert service.create_connection("alice", data) == first
    with pytest.raises(IntegrationError):
        service.create_connection("alice", {**data, "name": "changed"})
    with pytest.raises(IntegrationError):
        service.create_connection("alice", {**data, "operation_id": str(uuid4()), "write_space_id": FOREIGN})


def test_aik_016_effect_receipts_freeze_queue_selection_and_result(service):
    conn, rid = submitted(service)
    operation = str(uuid4())
    one = service.effect("alice",conn["id"],operation,"process-next")
    second = service.submit("alice",conn["id"],submission(external_key="another"))["id"]
    assert service.effect("alice",conn["id"],operation,"process-next") == one
    assert service.store.run("alice",second)["state"] == "queued"
    with pytest.raises(IntegrationError):
        service.effect("alice",conn["id"],operation,"process",second)


def test_aik_017_publication_mode_is_frozen_across_retry(service):
    conn, rid = submitted(service, connect(service, auto_publish_sources=True))
    # Generate with auto temporarily bypassed to simulate a stopped publisher.
    run = service.store.run("alice", rid)
    claim = service.store.claim("alice",rid,lambda:None)
    draft = authoring.validate_draft(model_output(),run["submission"]["sources"])
    service.store.finish("alice",rid,claim,draft,lambda:None)
    started = service.store.begin_publish("alice",rid,lambda:None,automatic=True)
    assert started["publication"]["intent"]["automatic"]
    service.publish("alice",rid,automatic=False)
    assert "## 검토된 정리" not in service.bridge.publications[0]


def test_aik_018_notion_real_collaborative_snapshot_and_retry(tmp_path):
    from code_assist_v1.tests.notion_collaboration.test_collaborative_iam_authority import _environment
    from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
    from code_assist_v1.agent_integrations.notion import CollaborativeNotionConnector
    authority, identities, root, catalog, workspace, _, _ = _environment(tmp_path)
    store = CollaborativeWorkspaceStore(catalog,user_data_root=root,identity_directory=identities)
    connector = CollaborativeNotionConnector(lambda actor: store)
    assert "workspace:"+workspace in [t["id"] for t in connector.targets({"id":"owner"})["items"]]
    run = dict(id=str(uuid4()),row_version=1,state="queued",submission=submission_input(submission()),evidence=[],draft=None)
    result = connector.record({"id":"owner"},"workspace:"+workspace,run,lambda:None)
    replay = connector.record({"id":"owner"},"workspace:"+workspace,run,lambda:None)
    assert result["document_id"] == replay["document_id"]
    doc = authority.read_document("owner",result["document_id"])
    assert "검토 요청" in doc["title"]
    assert doc["blocks"][0]["type"]=="heading1"
    with pytest.raises(Exception):
        connector.record({"id":"page-guest"},"workspace:"+workspace,{**run,"id":str(uuid4())},lambda:None)


def test_aik_019_outlook_observations_require_sent_folder_and_exact_reply(service):
    from code_assist_v1.agent_integrations.outlook import OutlookConnector
    original=dict(id="original",internetMessageId="<original@example>",conversationId="thread")
    reply=dict(id="reply",internetMessageId="<reply@example>",conversationId="thread",isDraft=False,
               sentDateTime="2026-09-07T01:00:00Z",parentFolderId="sent",
               internetMessageHeaders=[dict(name="In-Reply-To",value="<original@example>")])
    values={"/me/messages/original":original,"/me/messages/reply":reply,"/me/mailFolders/sentitems":{"id":"sent"}}
    connector=OutlookConnector(SimpleNamespace(graph_get=lambda owner,path,**kw:values[path]))
    assert connector.observe_reply("alice","original","reply")["status"] == "provider_observed_reply"
    for change in [dict(isDraft=True),dict(parentFolderId="inbox"),dict(internetMessageHeaders=[]),dict(conversationId="wrong")]:
        values["/me/messages/reply"]={**reply,**change}
        assert connector.observe_reply("alice","original","reply")["status"] == "unknown"
    values["/me/messages/reply"]=reply
    service.outlook=connector
    conn=connect(service,scopes=["knowledge:read","results:write","mail:read"])
    data=submission()
    data["sources"][0]["external_id"]="original"
    data["actions"][0]["source_id"]="mail-1"
    rid=service.submit("alice",conn["id"],data)["id"]
    result=service.observe_reply("alice",conn["id"],rid,dict(operation_id=str(uuid4()),action_id="action-1",candidate_message_id="reply",expected_version=1))
    evidence=service.store.run("alice",rid)["evidence"]
    assert result["state"] == "queued" and evidence[0]["actor_id"] == "provider:microsoft_graph"
    with pytest.raises(IntegrationError):
        service.store.evidence("alice",rid,str(uuid4()),"action-1","provider_observed_reply","fake",2,lambda:None)


def test_aik_020_mail_cursor_and_client_credentials_cannot_redirect():
    from code_assist_v1.agent_integrations.outlook import OutlookConnector
    from code_assist_v1.agent_integrations.hermes_mcp import Client
    connector=OutlookConnector(SimpleNamespace())
    for cursor in ["http://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta", "https://evil.example/token", "https://graph.microsoft.com/v1.0/users/another/messages"]:
        with pytest.raises(IntegrationError):
            connector.inbox("alice","2026-09-07T01:00:00Z",cursor_url=cursor)
    for origin in ["http://remote.example", "https://user:pass@example.com", "https://example.com/path"]:
        with pytest.raises(ValueError):
            Client(origin,"sbai_fixture")


def test_aik_025_worker_restart_and_expired_claim_recovery(service):
    from code_assist_v1.agent_integrations.worker import IntegrationWorker
    conn,rid=submitted(service)
    service.store.claim("alice",rid,lambda:None)
    with service.store.connection() as c:
        c.execute("UPDATE integration_run SET lease_until=0 WHERE id=?",(rid,))
    restored=IntegrationService(IntegrationStore(service.store.path,b"x"*32),service.bridge,runner=service.runner)
    IntegrationWorker(restored).tick()
    run=restored.store.run("alice",rid)
    assert run["state"]=="review" and run["generation"]==2
    assert run["action_statuses"][0]["execution"]=="unknown"


def test_aik_026_backup_restore_and_schema_drift_fail_closed(service,tmp_path):
    import sqlite3
    conn,rid=submitted(service)
    backup=tmp_path/"backup.db"
    with service.store.connection() as source:
        target=sqlite3.connect(backup)
        source.backup(target);target.close()
    restored=IntegrationStore(backup,b"x"*32);restored.initialize()
    assert restored.authenticate(conn["token"])["owner_id"]=="alice"
    assert restored.run("alice",rid)["submission"]==service.store.run("alice",rid)["submission"]
    with restored.connection() as c:c.execute("CREATE TABLE unexpected(value TEXT)")
    with pytest.raises(RuntimeError,match="physical schema"):
        restored.initialize()


def test_aik_027_followup_preserves_lineage_and_rejects_foreign_grants(service):
    conn,rid=submitted(service)
    child=service.submit("alice",conn["id"],submission(followup_of=rid))["id"]
    assert service.store.run("alice",child)["submission"]["followup_of"]==rid
    assert service.store.run("alice",rid)["submission"]["followup_of"] is None
    with pytest.raises(IntegrationError):
        service.submit("alice",connect(service)["id"],submission(followup_of=rid))


def test_aik_028_legacy_notion_target_acl_snapshot_replay_and_collaboration_guard(monkeypatch,tmp_path):
    from code_assist_v1 import notion_store as ns
    from code_assist_v1.agent_integrations.notion import LegacyNotionConnector
    monkeypatch.setattr(ns,"USER_DATA_DIR",str(tmp_path/"user_data"))
    monkeypatch.delenv("CODE_ASSIST_I0_COLLABORATIVE_SHARING",raising=False)
    parent=ns.create_page("alice",title="Integration records")["page"]["id"]
    connector=LegacyNotionConnector()
    assert parent in [p["id"] for p in connector.targets({"id":"alice"})["items"]]
    run=dict(id=str(uuid4()),row_version=1,state="queued",submission=submission_input(submission()),evidence=[],draft=None)
    result=connector.record({"id":"alice"},parent,run,lambda:None)
    assert connector.record({"id":"alice"},parent,run,lambda:None)["document_id"]==result["document_id"]
    assert ns.get_page("alice",result["document_id"])["blocks"][0]["type"]=="heading1"
    with pytest.raises(Exception):
        connector.record({"id":"bob"},parent,run,lambda:None)
    monkeypatch.setenv("CODE_ASSIST_I0_COLLABORATIVE_SHARING","1")
    with pytest.raises(IntegrationError,match="협업"):
        connector.record({"id":"alice"},parent,run,lambda:None)


def test_aik_029_route_inventory_and_windows_ledger_metrics(service,tmp_path):
    import math
    import os
    from pathlib import Path
    import sqlite3
    import time
    app=app_for(service)
    routes=[r for r in app.url_map.iter_rules() if r.endpoint.startswith("agent_integration_")]
    assert len({r.rule for r in routes})==25
    assert sum(len(r.methods-{"HEAD","OPTIONS"}) for r in routes)==26
    assert len(app.extensions["agent_integrations"]["bearer_endpoints"])==13
    conn,rid=submitted(service)
    samples=[];busy=0
    for _ in range(100):
        started=time.perf_counter()
        try:service.authenticate(conn["token"])
        except sqlite3.OperationalError:
            busy+=1;raise
        samples.append((time.perf_counter()-started)*1000)
    samples.sort()
    checkpoint=time.perf_counter();backup=tmp_path/"profile-restore.db"
    with service.store.connection() as source:
        target=sqlite3.connect(backup)
        source.backup(target);target.close()
    restored=IntegrationStore(backup,b"x"*32);restored.initialize()
    assert restored.run("alice",rid)["id"]==rid
    report=dict(fixture="isolated Windows ledger; fake canonical/model adapters",samples=len(samples),
        auth_latency_ms={label:round(samples[math.ceil(len(samples)*p)-1],3) for label,p in (("p50",.50),("p95",.95),("p99",.99))},
        sqlite_busy=busy,backup_restore_seconds=round(time.perf_counter()-checkpoint,4),
        queue_before=1,queue_after=0,projection_lag="not applicable: ledger has no projection")
    service.process("alice",rid)
    assert service.store.run("alice",rid)["state"]=="review"
    report["queue_wait_ms"]=service.store.run("alice",rid)["updated_at"]-service.store.run("alice",rid)["created_at"]
    output=os.environ.get("AGENT_INTEGRATION_METRICS")
    if output:Path(output).write_text(json.dumps(report,indent=2),encoding="utf-8")


def test_aik_004_submission_replay_dedup_source_versions_and_isolation(service):
    conn = connect(service)
    data = submission()
    first = service.submit("alice", conn["id"], data)
    assert service.submit("alice", conn["id"], data) == first
    assert service.submit("alice", conn["id"], {**data, "operation_id": str(uuid4())})["id"] == first["id"]
    with pytest.raises(IntegrationError):
        service.submit("alice", conn["id"], {**data, "title": "changed"})
    with pytest.raises(IntegrationError):
        service.store.run("bob", first["id"])
    other = connect(service)
    with pytest.raises(IntegrationError):
        service.store.run("alice", first["id"], cid=other["id"])


@pytest.mark.parametrize("change", [dict(sources=[]), dict(sources=[{"id":"x","kind":"email","title":"a","text":"b","verified":True}]),
                                    dict(actions=[dict(description="x", completion_condition="y", state="completed")]), dict(kind="../path")])
def test_aik_005_submitted_authority_is_not_accepted(change):
    with pytest.raises(IntegrationError):
        submission_input(submission(**change))


def test_aik_006_generation_does_not_publish_before_review(service):
    conn, rid = submitted(service)
    result = service.process("alice", rid)
    assert result["state"] == "review" and not service.bridge.publications
    with pytest.raises(IntegrationError):
        service.publish("alice", rid)
    service.store.review("alice", rid, str(uuid4()), result["row_version"], result["draft_hash"], "approve", lambda: None)
    done = service.publish("alice", rid)
    assert done["state"] == "published"
    assert service.publish("alice", rid)["publication"] == done["publication"]
    assert len(service.bridge.publications) == 1


def test_aik_007_exact_quotes_missing_sources_and_completion_blockers(service):
    sources = submission_input(submission())["sources"]
    assert authoring.validate_draft(model_output(), sources)["automatic_eligible"]
    assert authoring.validate_draft(model_output(quote="없는 근거"), sources)["blockers"]
    assert authoring.validate_draft(model_output(category="action_result"), sources)["blockers"]
    assert authoring.validate_draft(model_output(sid="foreign"), sources)["blockers"]
    sources[0]["kind"] = "agent_output"
    assert not authoring.validate_draft(model_output(), sources)["automatic_eligible"]


def test_aik_008_automatic_record_excludes_model_paraphrase(service):
    conn = connect(service, auto_publish_sources=True)
    _, rid = submitted(service, conn)
    service.runner = lambda *a, **k: (json.dumps({**model_output(), "summary":"실제로 처리 완료했다는 잘못된 추정"}), {})
    run = service.process("alice", rid)
    assert run["state"] == "published"
    markdown = service.bridge.publications[0]
    assert "잘못된 추정" not in markdown and "금요일까지 검토 부탁드립니다." in markdown
    assert "원본:" in markdown


def test_aik_009_user_evidence_invalidates_draft_and_is_attributed(service):
    _, rid = submitted(service)
    before = service.process("alice", rid)
    service.store.evidence("alice", rid, str(uuid4()), "action-1", "user_reported_complete", "회신했습니다.", before["row_version"], lambda: None)
    current = service.store.run("alice", rid)
    assert current["state"] == "queued" and current["draft"] is None
    evidence = authoring.evidence_sources(current)[-1]
    assert evidence["trust"] == "user_reported"
    with pytest.raises(IntegrationError):
        service.store.review("alice", rid, str(uuid4()), before["row_version"], before["draft_hash"], "approve", lambda: None)


def test_aik_010_stale_worker_claim_cannot_overwrite(service):
    _, rid = submitted(service)
    first = service.store.claim("alice", rid, lambda: None)
    with pytest.raises(IntegrationError):
        service.store.claim("alice", rid, lambda: None)
    with service.store.connection() as c:
        c.execute("UPDATE integration_run SET lease_until=0 WHERE id=?", (rid,))
    second = service.store.claim("alice", rid, lambda: None)
    assert second[1] == first[1]+1
    with pytest.raises(IntegrationError):
        service.store.finish("alice", rid, first, {}, lambda: None)
    service.store.finish("alice", rid, second, {}, lambda: None)


def test_aik_011_failed_model_preserves_original_and_can_retry(service):
    _, rid = submitted(service)
    runner = service.runner
    service.runner = lambda *a, **k: ("not json", {})
    with pytest.raises(IntegrationError):
        service.process("alice", rid)
    assert service.store.run("alice", rid)["state"] == "failed"
    service.runner = runner
    assert service.process("alice", rid)["state"] == "review"


def app_for(service):
    app = Flask(__name__)
    app.secret_key = "x"*32
    register(app, service, lambda: {"id":"alice"})
    @app.before_request
    def gate():
        handled, response = bearer_auth_gate(app)
        if handled:
            return response
    return app


def test_aik_012_http_token_cannot_review_or_inject_user_or_foreign_wiki(service):
    conn, rid = submitted(service)
    app = app_for(service)
    client = app.test_client()
    headers = {"Authorization":"Bearer "+conn["token"]}
    assert client.post("/api/integrations/v1/knowledge/search", json={"query":"검토"}, headers=headers).status_code == 200
    assert client.post("/api/integrations/v1/knowledge/search", json={"query":"검토", "user_id":"bob"}, headers=headers).status_code == 400
    assert client.post(f"/api/integrations/v1/runs/{rid}/review", json={}, headers=headers).status_code == 403
    assert client.post("/api/integrations/v1/knowledge/search", json={"query":"검토"}, headers={**headers, "Origin":"http://localhost"}).status_code == 400
    assert client.post("/api/integrations/v1/knowledge/search", data='{"query":"a","query":"b"}', content_type="application/json", headers=headers).status_code == 400


def test_aik_013_generic_research_uses_same_pipeline(service):
    conn = connect(service)
    data = submission(kind="research.notes", title="조사 기록", sources=[dict(id="mail-1",kind="document",title="연구 노트",text="금요일까지 검토 부탁드립니다.")], actions=[])
    rid = service.submit("alice", conn["id"], data)["id"]
    assert service.process("alice", rid)["state"] == "review"


def _claim_process(path, rid, queue):
    store = IntegrationStore(path, b"x"*32)
    try:
        result = store.claim("alice", rid, lambda: None)
        queue.put("claimed" if result else "skip")
    except IntegrationError:
        queue.put("conflict")


def test_aik_014_real_process_claim_exclusion(service):
    _, rid = submitted(service)
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    workers = [ctx.Process(target=_claim_process, args=(str(service.store.path), rid, queue)) for _ in range(2)]
    for process in workers:
        process.start()
    for process in workers:
        process.join(20)
        assert process.exitcode == 0
    assert sorted(queue.get(timeout=5) for _ in workers) == ["claimed", "conflict"]

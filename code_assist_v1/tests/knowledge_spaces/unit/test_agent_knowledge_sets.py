"""AIK-038: real canonical grants, personal/shared retrieval and native publication."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from code_assist_v1.agent_integrations.bridge import KnowledgeBridge
from code_assist_v1.agent_integrations.contracts import IntegrationError, encoded
from code_assist_v1.agent_integrations.service import IntegrationService
from code_assist_v1.agent_integrations.store import IntegrationStore
from code_assist_v1.knowledge_core.agent_runtime import compose_canonical_agent_runtime
from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.services.higher_authority import HigherAuthorityService, SetHigherRoleCommand
from code_assist_v1.knowledge_core.services.personal_wikis import CreatePersonalWikiCommand
from code_assist_v1.tests.knowledge_spaces.unit.test_scope_resolver import _factory, _seed
from code_assist_v1.tests.knowledge_spaces.unit.test_workspace_features import Stager
from code_assist_v1.tests.knowledge_spaces.unit.test_agent_integrations import model_output, submission
from code_assist_v1.tools.full_second_brain_preview import _seed_published_preview_item

pytestmark = pytest.mark.unit
FLAGS = FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True,
    part_reads=True, part_writes=True, higher_spaces=True, team_writes=True, responsibility_writes=True,
    multi_wiki_schema=True, multi_wiki_reads=True, multi_wiki_writes=True, conversation_wiki_binding=True)


def actor_version(factory, owner):
    c = factory.open(read_only=True)
    try:
        return dict(c.execute("SELECT scope_epoch,row_version FROM app_user WHERE id=?", (owner,)).fetchone())
    finally:
        c.close()


@pytest.fixture
def sets_env(isolated_knowledge_layout, tmp_path):
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed(factory)
    scopes = ScopeResolver(FLAGS)
    higher = HigherAuthorityService(factory, scopes, cursor_secret=b"sets-higher-fixture"*2)
    for space, role, owner in ((spaces["team_space_a"], "team_leader", "team_leader"),
                               (spaces["team_space_b"], "team_leader", "unassigned"),
                               (spaces["responsibility_space"], "responsibility_executive", "responsibility_executive")):
        c = factory.open(read_only=True)
        try:
            version = c.execute("SELECT row_version FROM knowledge_space WHERE id=?", (space,)).fetchone()[0]
        finally:
            c.close()
        higher.set_role(SetHigherRoleCommand(actor_user_id="admin", actor_scope_epoch=actor_version(factory,"admin")["scope_epoch"],
            request_id=str(uuid4()), operation_id=str(uuid4()), org_unit_id=space, role=role,
            assignee_user_id=owner, expected_current_assignment_id=None,
            expected_current_assignment_generation=None, expected_current_assignment_row_version=None,
            expected_space_row_version=version, enable_writes=True, reason="Knowledge set fixture"))
    c = factory.open()
    try:
        with transaction(c):
            c.execute("UPDATE knowledge_space SET write_enabled=1,row_version=row_version+1,updated_at=updated_at+1 WHERE space_kind IN ('personal','part')")
    finally:
        c.close()
    runtime = compose_canonical_agent_runtime(factory, flags=FLAGS, principal_resolver=lambda:None,
        source_artifact_stager=Stager(), personal_wiki_cursor_secret=b"k"*32, higher_authority_cursor_secret=b"h"*32)
    version = actor_version(factory,"member")
    extra = runtime.services.personal_wikis.create(CreatePersonalWikiCommand(actor_user_id="member",
        actor_scope_epoch=version["scope_epoch"], operation_id=str(uuid4()),request_id=str(uuid4()),
        display_name="Project notes", expected_row_version=1))
    spaces["personal_extra"] = extra.resource_id
    c = factory.open()
    try:
        with transaction(c):
            for i, key in enumerate(("personal_member", "personal_extra", "personal_other", "part_space_a",
                "part_space_b", "team_space_a", "team_space_b", "responsibility_space")):
                owner={"personal_member":"member","personal_extra":"member","personal_other":"other",
                    "part_space_a":"member","part_space_b":"other","team_space_a":"team_leader",
                    "team_space_b":"unassigned","responsibility_space":"responsibility_executive"}[key]
                _seed_published_preview_item(c, actor_user_id=owner, space_id=spaces[key],
                    label=f"sets-{key}", title=f"Fixture {key}", body=f"# Fixture {key}\n\n"+(f"Fixture knowledge {key}. "*120), timestamp_ms=3000+i)
    finally:
        c.close()
    calls = []
    def runner(task, payload, **kw):
        calls.append((task,payload))
        if task == "integration_knowledge_ask":
            return json.dumps({"answer":"근거를 참고한 답변입니다. [S1]", "citations":["S1"]},ensure_ascii=False),{}
        return json.dumps(model_output(),ensure_ascii=False),{}
    store=IntegrationStore(tmp_path/"sets-ledger.db",b"s"*32);store.initialize()
    service=IntegrationService(store,KnowledgeBridge(runtime.services,runtime.route_bindings),runner=runner)
    return SimpleNamespace(service=service, spaces=spaces, factory=factory, calls=calls)


def grant(env, owner="member", **overrides):
    personal = env.spaces["personal_"+owner]
    data=dict(operation_id=str(uuid4()), name="Knowledge sets", read_space_id=personal, write_space_id=personal,
        scopes=["knowledge:read","knowledge:ask","results:write"], knowledge_sets_version=2,
        personal_space_ids=[personal],read_mode="personal",shared_space_id="",
        write_personal_space_ids=[personal],write_org_space_id="",write_modes=["personal"])
    data.update(overrides)
    return env.service.create_connection(owner,data)


def set_app(service):
    from flask import Flask
    from code_assist_v1.agent_integrations.routes import register, bearer_auth_gate
    app=Flask(__name__);app.secret_key="t"*32
    register(app,service,lambda:{"id":"member"})
    @app.before_request
    def gate():
        _, response = bearer_auth_gate(app)
        return response
    return app


@pytest.mark.parametrize("hybrid",[False,True])
def test_aik_038_real_personal_shared_selection_budget_and_read(sets_env,hybrid):
    e=sets_env;s=e.service;p=e.spaces
    expansion_calls=[]
    if hybrid:
        from dataclasses import replace
        def expansion(task,payload,**kw):
            expansion_calls.append(task)
            return json.dumps({"schema_version":2,"intent":"topic","search_terms":["Fixture"]}),{}
        runtime=compose_canonical_agent_runtime(e.factory,flags=replace(FLAGS,hybrid_rerank=True),
            principal_resolver=lambda:None,source_artifact_stager=Stager(),personal_wiki_cursor_secret=b"k"*32,
            higher_authority_cursor_secret=b"h"*32,model_runner=expansion)
        s.bridge=KnowledgeBridge(runtime.services,runtime.route_bindings)
    g=grant(e,personal_space_ids=[p["personal_member"],p["personal_extra"]],
        read_mode="personal_shared",shared_space_id=p["responsibility_space"])
    one=s.knowledge_search("member",g["id"],"Fixture",mode="personal")
    assert {h["source_space_id"] for h in one["items"]}=={p["personal_member"],p["personal_extra"]}
    narrowed=s.knowledge_search("member",g["id"],"Fixture",mode="personal",personal_space_ids=[p["personal_extra"]])
    assert {h["source_space_id"] for h in narrowed["items"]}=={p["personal_extra"]}
    both=s.knowledge_search("member",g["id"],"Fixture")
    assert {h["source_space_id"] for h in both["items"]}=={p[k] for k in ("personal_member","personal_extra",
        "part_space_a","part_space_b","team_space_a","team_space_b","responsibility_space")}
    assert len(both["views"])==3  # two personal Wikis and one shared aggregate, not descendant fan-out
    assert len(encoded(both).encode())<=16384
    small=s.knowledge_search("member",g["id"],"Fixture",max_bytes=4096)
    assert len(encoded(small).encode())<=4096 and small["truncated"] and small["omitted_items"]>0
    hit=next(h for h in both["items"] if h["source_space_id"]==p["part_space_b"])
    doc=s.knowledge_read("member",g["id"],hit["item_id"],view_space_id=hit["view_space_id"])
    assert doc["source_space_id"]==p["part_space_b"] and "Fixture knowledge" in doc["markdown"]
    with pytest.raises(IntegrationError):s.knowledge_read("member",g["id"],hit["item_id"],view_space_id=p["part_space_b"])
    with pytest.raises(IntegrationError):s.knowledge_read("member",g["id"],hit["item_id"],view_space_id=hit["view_space_id"],mode="personal")
    with pytest.raises(IntegrationError):s.knowledge_search("member",g["id"],"Fixture",mode="shared")
    with pytest.raises(IntegrationError):s.knowledge_search("member",g["id"],"Fixture",personal_space_ids=[])
    with pytest.raises(IntegrationError):s.knowledge_search("member",g["id"],"Fixture",personal_space_ids=[p["personal_other"]])
    if hybrid:assert expansion_calls==["canonical_multilingual_query_expansion"]


@pytest.mark.parametrize("override", ["foreign_personal", "foreign_shared", "member_team_write", "admin_team_write", "leader_part_write", "leader_both"])
def test_aik_038_grants_never_invent_organization_or_admin_authority(sets_env, override):
    e=sets_env;p=e.spaces;owner="member";data={}
    if override=="foreign_personal":data={"personal_space_ids":[p["personal_member"],p["personal_other"]]}
    if override=="foreign_shared":data={"read_mode":"personal_shared","shared_space_id":p["team_space_b"]}
    if override in {"member_team_write","admin_team_write"}:
        owner="admin" if override=="admin_team_write" else "member"
        data={"write_org_space_id":p["team_space_a"],"write_modes":["personal","organization"]}
    if override=="leader_part_write":owner="team_leader";data={"write_org_space_id":p["part_space_a"],"write_modes":["personal","organization"]}
    if override=="leader_both":owner="team_leader";data={"write_org_space_id":p["team_space_a"],"write_modes":["personal","both"]}
    with pytest.raises(Exception):grant(e,owner=owner,**data)
    assert e.service.store.list_connections(owner)==[]


@pytest.mark.parametrize("owner,mode,org",[("member","personal",None),("member","organization","part_space_a"),
    ("member","both","part_space_a"),("team_leader","organization","team_space_a"),
    ("responsibility_executive","organization","responsibility_space")])
def test_aik_039_real_native_and_dual_publication_receipts(sets_env,owner,mode,org):
    e=sets_env;s=e.service;p=e.spaces;personal=p["personal_"+owner]
    g=grant(e,owner=owner,write_org_space_id=p[org] if org else "",write_modes=["personal"]+([mode] if mode!="personal" else []))
    target={"mode":mode}
    if mode!="organization":target["personal_space_id"]=personal
    if mode!="personal":target["organization_space_id"]=p[org]
    request=submission(publication_target=target,title="Fixture task")
    rid=s.submit(owner,g["id"],request)["id"]
    assert s.submit(owner,g["id"],request)["id"]==rid
    run=s.process(owner,rid)
    assert not run["draft"]["blockers"]
    s.store.review(owner,rid,str(uuid4()),run["row_version"],run["draft_hash"],"approve",lambda:None)
    published=s.publish(owner,rid)
    assert published["state"]=="published"
    assert {t["space_id"] for t in published["publication"]["results"]}==set(target.values())-{mode}
    assert s.publish(owner,rid)["publication"]==published["publication"]
    if mode=="both":assert len({t["item_id"] for t in published["publication"]["results"]})==2


def test_aik_040_set_ask_single_model_call_replay_and_missing_evidence(sets_env):
    e=sets_env;s=e.service;p=e.spaces
    g=grant(e,personal_space_ids=[p["personal_member"],p["personal_extra"]],
        read_mode="personal_shared",shared_space_id=p["team_space_a"])
    operation=str(uuid4());answer=s.knowledge_ask("member",g["id"],operation,"Fixture")
    assert answer["state"]=="completed" and answer["conversation_id"] is None and "[S1]" in answer["answer"]
    assert len(e.calls)==1 and e.calls[0][0]=="integration_knowledge_ask"
    assert len({h["view_space_id"] for h in e.calls[0][1]["evidence"]})==3
    assert s.knowledge_ask("member",g["id"],operation,"Fixture")==answer and len(e.calls)==1
    with pytest.raises(IntegrationError):s.knowledge_ask("member",g["id"],operation,"Changed")
    with pytest.raises(IntegrationError):s.knowledge_ask("member",g["id"],operation,"Fixture",mode="personal")
    absent=s.knowledge_ask("member",g["id"],str(uuid4()),"zzzzunmatched987654")
    assert absent["response"]["sources"]==[] and len(e.calls)==1
    g2=grant(e)
    assert s.knowledge_ask("member",g2["id"],operation,"Fixture")["state"]=="completed" and len(e.calls)==2
    long_message="Fixture\n후속 질문을 참고해 주세요. "*100
    long_answer=s.knowledge_ask("member",g2["id"],str(uuid4()),long_message)
    assert long_answer["response"]["retrieval_query_truncated"]
    assert e.calls[-1][1]["question"]==long_message


def test_aik_040_revocation_during_set_answer_cannot_commit(sets_env):
    e=sets_env;s=e.service;g=grant(e);original=s.runner;operation=str(uuid4())
    def revoke(*a,**kw):
        s.store.revoke("member",g["id"],str(uuid4()),lambda:None)
        return original(*a,**kw)
    s.runner=revoke
    with pytest.raises(IntegrationError):s.knowledge_ask("member",g["id"],operation,"Fixture")
    with s.store.connection() as c:
        result=json.loads(c.execute("SELECT result_json FROM integration_operation WHERE actor_key=? AND operation_id=?",(g["id"],operation)).fetchone()[0])
    assert result["pending"]


def test_aik_038_http_modes_and_legacy_token_not_broadened(sets_env):
    e=sets_env;s=e.service;p=e.spaces
    app=set_app(s)
    client=app.test_client();g=grant(e);headers={"Authorization":"Bearer "+g["token"]}
    base="/api/integrations/v1"
    status=client.get(base+"/status",headers=headers)
    assert status.status_code==200 and status.json["knowledge_sets"]["version"]==2
    for mode,code in (("personal",200),("shared",400),("personal_shared",403),("",400),([],400),({},400)):
        response=client.post(base+"/knowledge/search",headers=headers,json={"query":"Fixture","read_mode":mode,"max_bytes":4096})
        assert response.status_code==code
        if code==200:
            assert response.data==encoded(response.json).encode("utf-8") and len(response.data)<=4096
    legacy=s.create_connection("member",dict(operation_id=str(uuid4()),name="Legacy",read_space_id=p["personal_member"],
        write_space_id=p["personal_member"],scopes=["knowledge:read"]))
    assert "knowledge_sets_version" not in legacy
    headers={"Authorization":"Bearer "+legacy["token"]}
    assert client.post(base+"/knowledge/search",headers=headers,json={"query":"Fixture","read_mode":"personal_shared"}).status_code==403


def test_aik_040_invalid_citation_and_frozen_publication_target(sets_env):
    e=sets_env;s=e.service;p=e.spaces;g=grant(e,write_org_space_id=p["part_space_a"],write_modes=["personal","organization","both"])
    request=submission(publication_target={"mode":"both","personal_space_id":p["personal_member"],"organization_space_id":p["part_space_a"]})
    rid=s.submit("member",g["id"],request)["id"]
    with pytest.raises(IntegrationError):
        s.submit("member",g["id"],{**request,"publication_target":{"mode":"personal","personal_space_id":p["personal_member"]}})
    follow=s.submit("member",g["id"],submission(operation_id=str(uuid4()),external_key="follow",followup_of=rid))
    assert s.store.run("member",follow["id"])["submission"]["publication_target"]==request["publication_target"]
    with pytest.raises(IntegrationError):
        s.submit("member",g["id"],submission(operation_id=str(uuid4()),publication_target={"mode":"organization","organization_space_id":p["part_space_b"]}))
    s.runner=lambda *a,**kw:(json.dumps({"answer":"Invented [S999]","citations":["S999"]}),{})
    with pytest.raises(IntegrationError,match="출처 형식"):
        s.knowledge_ask("member",g["id"],str(uuid4()),"Fixture")


def test_aik_041_real_browser_two_modes_and_selected_dual_target(sets_env,tmp_path):
    from pathlib import Path
    import os
    import threading
    from flask import send_file
    from werkzeug.serving import make_server
    from playwright.sync_api import sync_playwright, expect
    e=sets_env;s=e.service;p=e.spaces;app=set_app(s)
    evidence=Path(os.environ.get("AGENT_KNOWLEDGE_SETS_EVIDENCE",str(tmp_path)))
    evidence.mkdir(parents=True,exist_ok=True)
    static=Path(__file__).resolve().parents[3]/"static"
    @app.get("/")
    def index():
        return '<!doctype html><html lang="ko"><meta charset="utf-8"><link rel="stylesheet" href="/assets/app.css"><link rel="stylesheet" href="/assets/agent_integrations.css"><button id="agentIntegrationsBtn">에이전트 연결</button><script>window.errors=[];window.authenticatedFetch=fetch;window.toast=(m,t)=>{if(t==="error")errors.push(m)};</script><script src="/assets/agent_integrations.js"></script></html>'
    @app.get("/assets/<filename>")
    def asset(filename):
        assert filename in {"agent_integrations.js","agent_integrations.css","app.css"}
        return send_file(static/filename)
    server=make_server("127.0.0.1",0,app,threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel="msedge",headless=True)
            page=browser.new_page(viewport={"width":1280,"height":900});errors=[]
            page.on("pageerror",lambda error:errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.get_by_role("button",name="에이전트 연결",exact=True).click()
            mode=page.get_by_label("조회 모드",exact=True)
            expect(mode).to_be_visible()
            assert mode.locator("option").all_text_contents()==["내 지식","내 지식+공유 지식"]
            expect(page.get_by_label("공유 지식 범위",exact=True)).to_be_hidden()
            for checkbox in page.get_by_role("group",name="참고할 개인 Wiki · 복수 선택").get_by_role("checkbox").all():
                checkbox.check()
            mode.select_option("personal_shared")
            page.get_by_label("공유 지식 범위",exact=True).select_option(p["team_space_a"])
            page.get_by_label("본인 조직에 등록 허용",exact=True).check()
            page.get_by_label("등록할 조직",exact=True).select_option(p["part_space_a"])
            page.get_by_label("개인+파트 동시 등록 허용",exact=True).check()
            page.get_by_role("group",name="추가로 등록을 허용할 개인 Wiki").get_by_label("Project notes",exact=True).check()
            page.screenshot(path=str(evidence/"knowledge-sets-config.png"),full_page=True)
            page.get_by_role("button",name="연결 만들기",exact=True).click()
            expect(page.get_by_label("에이전트 토큰",exact=True)).to_be_visible()
            stored=s.store.list_connections("member")[0]
            assert set(stored["personal_space_ids"])=={p["personal_member"],p["personal_extra"]}
            assert stored["read_mode"]=="personal_shared" and stored["shared_space_id"]==p["team_space_a"]
            page.get_by_role("button",name="직접 자료 제출",exact=True).click()
            page.get_by_label("제목",exact=True).fill("Fixture browser submission")
            page.get_by_label("원본 자료",exact=True).fill("금요일까지 검토 부탁드립니다.")
            page.get_by_label("자료 정제 시 조회 모드",exact=True).select_option("personal")
            page.get_by_label("등록 대상",exact=True).select_option("both")
            page.get_by_label("등록할 개인 Wiki",exact=True).select_option(p["personal_extra"])
            page.screenshot(path=str(evidence/"knowledge-sets-submit.png"),full_page=True)
            page.get_by_role("button",name="자료 제출",exact=True).click()
            expect(page.get_by_role("heading",name="Fixture browser submission",exact=True)).to_be_visible()
            run=s.store.list_runs("member")["items"][0]
            assert run["submission"]["read_mode"]=="personal"
            assert run["submission"]["publication_target"]=={"mode":"both","personal_space_id":p["personal_extra"],"organization_space_id":p["part_space_a"]}
            assert not errors and not page.evaluate("errors")
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            browser.close()
    finally:
        server.shutdown();thread.join(10)


def test_aik_042_portable_mcp_real_sets_search_read_ask_and_submit(sets_env,tmp_path):
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import threading
    from werkzeug.serving import make_server
    python=Path("D:/Hermes/hermes-agent/venv/Scripts/python.exe")
    if not python.is_file():pytest.skip("Hermes environment not installed on this host")
    e=sets_env;p=e.spaces
    g=grant(e,personal_space_ids=[p["personal_member"],p["personal_extra"]],read_mode="personal_shared",
        shared_space_id=p["responsibility_space"],write_org_space_id=p["part_space_a"],write_modes=["personal","both"])
    server=make_server("127.0.0.1",0,set_app(e.service),threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    adapter=tmp_path/"hermes_mcp.py"
    shutil.copyfile(Path(__file__).resolve().parents[3]/"agent_integrations/hermes_mcp.py",adapter)
    env={k:v for k,v in os.environ.items() if k not in {"SECOND_BRAIN_TOKEN_FILE","SECOND_BRAIN_TOKEN","PYTHONPATH","PYTHONHOME"}}
    env.update(SECOND_BRAIN_URL=f"http://127.0.0.1:{server.server_port}",SECOND_BRAIN_TOKEN=g["token"])
    probe=tmp_path/"sets_probe.py"
    probe.write_text('''import asyncio, importlib.util, json, os, sys
from uuid import uuid4
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
assert importlib.util.find_spec("code_assist_v1") is None
spaces=json.loads(sys.argv[2])
async def main():
    async with stdio_client(StdioServerParameters(command=sys.executable,args=["-I",sys.argv[1]],env=dict(os.environ))) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            assert len((await session.list_tools()).tools)==10
            async def call(name,args):
                result=await session.call_tool("second_brain_"+name,args)
                assert not result.is_error, str(result)
                return json.loads(next(c.text for c in result.content if c.type=="text"))
            status=await call("status",{})
            assert status["knowledge_sets"]["version"]==2
            personal=await call("search",{"query":"Fixture","read_mode":"personal","personal_space_ids":[spaces["personal_extra"]]})
            assert len(personal["items"])==1 and personal["items"][0]["source_space_id"]==spaces["personal_extra"]
            shared=await call("search",{"query":"Fixture","read_mode":"personal_shared"})
            hit=next(h for h in shared["items"] if h["source_space_id"]==spaces["part_space_b"])
            doc=await call("read",{"item_id":hit["item_id"],"view_space_id":hit["view_space_id"],"read_mode":"personal_shared"})
            assert doc["source_space_id"]==spaces["part_space_b"]
            bad=await session.call_tool("second_brain_read",{"item_id":hit["item_id"],"view_space_id":hit["view_space_id"],"read_mode":"personal"})
            assert bad.is_error
            ask={"operation_id":str(uuid4()),"message":"Fixture","read_mode":"personal"}
            answer=await call("ask",ask)
            assert answer==await call("ask",ask) and answer["conversation_id"] is None
            target={"mode":"both","personal_space_id":spaces["personal_member"],"organization_space_id":spaces["part_space_a"]}
            run=await call("submit",{"operation_id":str(uuid4()),"kind":"research.fixture","title":"MCP fixture",
                "sources":[{"id":"source-1","kind":"document","title":"Fixture","text":"A source"}],
                "read_mode":"personal","publication_target":target})
            detail=await call("run",{"run_id":run["id"]})
            assert detail["submission"]["publication_target"]==target
            print("Portable MCP knowledge sets: ten tools, selection, inherited read, narrowing, cited Ask/replay, selected submission PASS")
asyncio.run(main())
''',encoding="utf-8")
    try:
        completed=subprocess.run([str(python),"-I",str(probe),str(adapter),json.dumps(p)],env=env,cwd=tmp_path,
            capture_output=True,text=True,encoding="utf-8",timeout=60)
        assert completed.returncode==0,completed.stdout+completed.stderr
        assert g["token"] not in completed.stdout+completed.stderr
        assert len(e.calls)==1
    finally:
        server.shutdown();thread.join(10)


def sets_publication_process(core_path,ledger_path,rid,output):
    from code_assist_v1.knowledge_core.db import ConnectionFactory
    from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
    factory=ConnectionFactory._for_isolated_tests(core_path,CANONICAL_MIGRATION_CATALOG)
    runtime=compose_canonical_agent_runtime(factory,flags=FLAGS,principal_resolver=lambda:None,
        source_artifact_stager=Stager(),personal_wiki_cursor_secret=b"k"*32,higher_authority_cursor_secret=b"h"*32)
    store=IntegrationStore(ledger_path,b"s"*32);store.initialize()
    service=IntegrationService(store,KnowledgeBridge(runtime.services,runtime.route_bindings))
    result=service.publish("member",rid)
    output.put(sorted((t["space_id"],t["item_id"]) for t in result["publication"]["results"]))


@pytest.mark.parametrize("lost_receipt",[False,True])
def test_aik_043_dual_publication_two_processes_and_receipt_recovery(sets_env,lost_receipt):
    import multiprocessing
    e=sets_env;s=e.service;p=e.spaces
    g=grant(e,write_org_space_id=p["part_space_a"],write_modes=["personal","both"])
    rid=s.submit("member",g["id"],submission(publication_target={"mode":"both","personal_space_id":p["personal_member"],"organization_space_id":p["part_space_a"]}))["id"]
    run=s.process("member",rid)
    s.store.review("member",rid,str(uuid4()),run["row_version"],run["draft_hash"],"approve",lambda:None)
    c=e.factory.open(read_only=True)
    try:before=c.execute("SELECT COUNT(*) FROM knowledge_item").fetchone()[0]
    finally:c.close()
    if lost_receipt:
        s.store.record_publication=lambda *a,**kw:(_ for _ in ()).throw(RuntimeError("lost fixture receipt"))
        with pytest.raises(RuntimeError):s.publish("member",rid)
    ctx=multiprocessing.get_context("spawn");output=ctx.Queue()
    processes=[ctx.Process(target=sets_publication_process,args=(e.factory.database_path,s.store.path,rid,output)) for _ in range(2)]
    try:
        for process in processes:process.start()
        for process in processes:
            process.join(30);assert process.exitcode==0
        one,two=output.get(timeout=5),output.get(timeout=5)
        assert one==two and len(one)==2
        c=e.factory.open(read_only=True)
        try:assert c.execute("SELECT COUNT(*) FROM knowledge_item").fetchone()[0]==before+2
        finally:c.close()
        assert s.store.run("member",rid)["state"]=="published"
    finally:
        for process in processes:
            if process.is_alive():process.terminate();process.join(5)
        output.close()


def test_aik_044_windows_set_search_metrics(sets_env,tmp_path):
    import math
    import os
    from pathlib import Path
    import time
    e=sets_env;s=e.service;p=e.spaces
    g=grant(e,personal_space_ids=[p["personal_member"],p["personal_extra"]],read_mode="personal_shared",shared_space_id=p["responsibility_space"])
    samples=[];busy=0
    for _ in range(20):
        start=time.perf_counter()
        try:result=s.knowledge_search("member",g["id"],"Fixture")
        except Exception as exc:
            if "busy" in str(exc).lower() or "locked" in str(exc).lower():busy+=1
            raise
        samples.append((time.perf_counter()-start)*1000)
    ordered=sorted(samples)
    metrics={"fixture":"2 personal Wikis + 1 responsibility view (2 teams, 2 parts), 7 relevant documents",
        "samples":len(samples),"latency_ms":{f"p{q}":round(ordered[math.ceil(len(ordered)*q/100)-1],2) for q in (50,95,99)},
        "sqlite_busy_errors":busy,"response_bytes":len(encoded(result).encode()),"budget_bytes":16384,
        "retrieved_documents":len(result["items"]),"queue_projection_restore":"not measured; read-only synthetic search"}
    destination=Path(os.environ.get("AGENT_KNOWLEDGE_SETS_EVIDENCE",str(tmp_path)))
    destination.mkdir(parents=True,exist_ok=True)
    (destination/"search-metrics.json").write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding="utf-8")
    assert metrics["response_bytes"]<=16384 and busy==0

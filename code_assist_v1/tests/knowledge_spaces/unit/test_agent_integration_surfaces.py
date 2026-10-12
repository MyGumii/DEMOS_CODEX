"""AIK-021..023: actual browser, auth gate and Hermes MCP interoperability."""
import json
from pathlib import Path
import subprocess
import threading

import pytest
from flask import send_file
from werkzeug.serving import make_server

from code_assist_v1.tests.knowledge_spaces.unit.test_agent_integrations import service, connect, app_for, model_output, READ, WRITE
from code_assist_v1.agent_integrations.routes import register

pytestmark=pytest.mark.unit
STATIC=Path(__file__).resolve().parents[3]/"static"


def test_aik_021_real_browser_connection_submission_review_and_revoke(service, tmp_path):
    from playwright.sync_api import sync_playwright, expect
    service.bridge.wikis=lambda actor,cursor=None:dict(items=[dict(space_id=READ,display_name="참고 Wiki",is_default=True),
        dict(space_id=WRITE,display_name="등록 Wiki",is_default=False)],next_cursor=None)
    service.runner=lambda task,payload,**kw:(json.dumps(model_output(sid="source-1",quote=payload["sources"][0]["text"]),ensure_ascii=False),{})
    app=app_for(service)
    @app.get("/")
    def index():
        return '<!doctype html><html lang="ko"><meta charset="utf-8"><link rel="stylesheet" href="/assets/app.css"><link rel="stylesheet" href="/assets/agent_integrations.css"><button id="agentIntegrationsBtn">에이전트 연결</button><script>window.errors=[]; window.authenticatedFetch=fetch; window.toast=(m,t)=>{if(t==="error")errors.push(m)};</script><script src="/assets/agent_integrations.js"></script></html>'
    @app.get("/assets/<filename>")
    def asset(filename):
        assert filename in {"agent_integrations.js","agent_integrations.css","app.css"}
        return send_file(STATIC/filename)
    server=make_server("127.0.0.1",0,app,threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel="msedge",headless=True)
            page=browser.new_page(viewport={"width":1280,"height":900})
            errors=[];page.on("pageerror",lambda error:errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.get_by_role("button",name="에이전트 연결",exact=True).click()
            page.get_by_label("지식을 등록할 Wiki",exact=True).select_option(WRITE)
            import os
            evidence=Path(os.environ.get("AGENT_INTEGRATION_UI_EVIDENCE",str(tmp_path)))
            evidence.mkdir(parents=True,exist_ok=True)
            for theme in ("light","dark"):
                page.evaluate("value=>document.documentElement.dataset.theme=value",theme)
                contrast=page.locator(".aik-dialog").evaluate('''el=>{
                    const css=getComputedStyle(el);
                    const lum=value=>value.match(/[\\d.]+/g).slice(0,3).map(Number).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
                    const a=lum(css.color),b=lum(css.backgroundColor);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
                }''')
                assert contrast>=4.5
                page.screenshot(path=str(evidence/("fixture-ui-"+theme+".png")),full_page=True)
            page.get_by_role("button",name="연결 만들기",exact=True).click()
            expect(page.get_by_label("에이전트 토큰",exact=True)).to_be_visible()
            issued_token=page.get_by_label("에이전트 토큰",exact=True).input_value()
            assert issued_token.startswith("sbai_")
            assert service.store.list_connections("alice")[0]["write_space_id"]==WRITE
            page.get_by_role("button",name="연결 목록",exact=True).click()
            expect(page.get_by_label("에이전트 토큰",exact=True)).to_have_count(0)
            page.get_by_role("button",name="토큰 보기",exact=True).click()
            expect(page.get_by_label("에이전트 토큰",exact=True)).to_have_value(issued_token)
            page.evaluate("Object.defineProperty(navigator.clipboard,'writeText',{value:async value=>{window.fixtureCopied=value}})")
            page.get_by_role("button",name="토큰 복사",exact=True).click()
            assert page.evaluate("window.fixtureCopied")==issued_token
            page.get_by_role("button",name="토큰 숨기기",exact=True).click()
            expect(page.get_by_label("에이전트 토큰",exact=True)).to_have_count(0)
            page.get_by_role("button",name="토큰 보기",exact=True).click()
            expect(page.get_by_label("에이전트 토큰",exact=True)).to_have_value(issued_token)
            page.get_by_role("button",name="직접 자료 제출",exact=True).click()
            expect(page.get_by_label("에이전트 토큰",exact=True)).to_have_count(0)
            page.get_by_label("제목",exact=True).fill("브라우저 검증 자료")
            page.get_by_label("원본 자료",exact=True).fill("금요일까지 검토 부탁드립니다.")
            page.get_by_label("에이전트 제안 또는 작업 결과",exact=True).fill("검토를 제안합니다.")
            page.get_by_role("button",name="자료 제출",exact=True).click()
            page.get_by_role("button",name="Second Brain으로 정제",exact=True).click()
            page.get_by_role("button",name="이 버전 검토 승인",exact=True).click()
            page.get_by_role("button",name="지정 Wiki에 등록",exact=True).click()
            expect(page.get_by_text("등록 제출 완료",exact=True)).to_be_visible()
            assert len(service.bridge.publications)==1
            page.get_by_role("button",name="연결 설정",exact=True).click()
            page.get_by_role("button",name="연결 폐기",exact=True).click()
            expect(page.get_by_text("폐기됨",exact=True)).to_be_visible()
            expect(page.get_by_role("button",name="토큰 보기",exact=True)).to_have_count(0)
            page.screenshot(path=str(tmp_path/"agent-connections.png"),full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            assert not errors and not page.evaluate("errors")
            browser.close()
    finally:
        server.shutdown();thread.join(10)


def test_aik_022_real_auth_gate_only_allows_exact_machine_routes(service):
    from code_assist_v1.tests.knowledge_spaces.integration.test_auth_flask_security import _make_app, _ORIGIN
    app=_make_app();register(app,service,lambda:{"id":"alice"})
    conn=connect(service)
    client=app.test_client()
    headers={"Authorization":"Bearer "+conn["token"]}
    assert client.get("/api/integrations/v1/status",base_url=_ORIGIN,headers=headers).status_code==200
    assert client.get("/api/integrations/v1/status",base_url="https://evil.example",headers=headers).status_code==403
    assert client.post("/api/probe/mutate",base_url=_ORIGIN,headers=headers,json={}).status_code==401
    assert client.get("/api/integrations/v1/connections",base_url=_ORIGIN,headers=headers).status_code==401
    assert client.get("/api/integrations/v1/status",base_url=_ORIGIN,headers={**headers,"Origin":_ORIGIN}).status_code==400


def test_aik_023_hermes_mcp_stdio_handshake_and_real_api(service,tmp_path):
    python=Path("D:/Hermes/hermes-agent/venv/Scripts/python.exe")
    if not python.is_file():
        pytest.skip("Hermes environment not installed on this host")
    conn=connect(service);app=app_for(service)
    server=make_server("127.0.0.1",0,app,threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    script=Path(__file__).resolve().parents[3]/"agent_integrations/hermes_mcp.py"
    import os
    env={**os.environ,"SECOND_BRAIN_URL":f"http://127.0.0.1:{server.server_port}","SECOND_BRAIN_TOKEN":conn["token"]}
    env.pop("SECOND_BRAIN_TOKEN_FILE",None)
    probe=tmp_path/"mcp_probe.py"
    probe.write_text('''import asyncio, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
    async with stdio_client(StdioServerParameters(command=sys.executable,args=[sys.argv[1]],env=dict(os.environ))) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            tools=await session.list_tools()
            assert len(tools.tools)==10, len(tools.tools)
            result=await session.call_tool("second_brain_status",{})
            assert not result.is_error
            print("MCP initialization, ten tools, authenticated status: PASS")
asyncio.run(main())
''',encoding="utf-8")
    try:
        completed=subprocess.run([str(python),str(probe),str(script)],env=env,capture_output=True,text=True,timeout=45)
        assert completed.returncode==0,completed.stdout+completed.stderr
    finally:
        server.shutdown();thread.join(10)

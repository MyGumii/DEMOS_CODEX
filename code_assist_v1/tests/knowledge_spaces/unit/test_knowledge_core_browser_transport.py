from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.unit
_ROOT = Path(__file__).resolve().parents[3]
_TRANSPORT = _ROOT / "static" / "knowledge_core_transport.js"
_BROWSER = _ROOT / "static" / "knowledge_core_browser.js"
_PUBLICATION = _ROOT / "static" / "knowledge_publication.js"
_SCOPE = _ROOT / "static" / "knowledge_scope.js"
_INDEX = _ROOT / "static" / "index.html"
_KNOWLEDGE = _ROOT / "static" / "knowledge.js"
_SESSIONS = _ROOT / "static" / "sessions.js"
_NOTION_INDEX = _ROOT / "notion_static" / "index.html"
_NOTION_PUBLICATION = _ROOT / "notion_static" / "notion_publication.js"
_NOTION_JS = _ROOT / "notion_static" / "notion.js"


def _run_node(source: str) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.fail("P3.7 browser transport proof requires Node.js.")
    completed = subprocess.run(
        [node, "-e", source],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_session_sidebar_describes_each_immutable_wiki_path() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const sessions=require({json.dumps(str(_SESSIONS))});
const paths={{
  basic:sessions._spacePath({{kind:'personal',name:'내 지식',org_path:[]}}),
  extra:sessions._spacePath({{kind:'personal',name:'프로젝트 연구',org_path:[]}}),
  part:sessions._spacePath({{kind:'part',name:'AMHS설계파트',org_path:['AMHS담당','AMHS지능화','AMHS설계파트']}}),
  team:sessions._spacePath({{kind:'team',name:'AMHS지능화',org_path:['AMHS담당','AMHS지능화']}}),
  responsibility:sessions._spacePath({{kind:'responsibility',name:'AMHS담당',org_path:['AMHS담당']}}),
}};
assert.deepEqual(paths,{{
  basic:'내 지식 · 기본',
  extra:'내 지식 · 프로젝트 연구',
  part:'Part · AMHS설계파트',
  team:'팀 · AMHS지능화',
  responsibility:'담당 · AMHS담당',
}});
console.log(JSON.stringify(paths));
"""
    )
    assert result["basic"] == "내 지식 · 기본"
    assert result["extra"] == "내 지식 · 프로젝트 연구"
    assert result["part"] == "Part · AMHS설계파트"
    assert result["team"] == "팀 · AMHS지능화"
    assert result["responsibility"] == "담당 · AMHS담당"


def test_management_list_hides_internal_source_space_uuid() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const personal='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const team='11111111-1111-4111-8111-111111111111';
const sourcePart='22222222-2222-4222-8222-222222222222';
const item='33333333-3333-4333-8333-333333333333';
const snapshot={{conversation_badge:null,scope_epoch:1,selected_space_id:team,spaces:[
  {{id:personal,kind:'personal',name:'내 지식',org_path:[],capabilities:['read'],selected:false}},
  {{id:team,kind:'team',name:'AMHS지능화',org_path:['AMHS담당','AMHS지능화'],capabilities:['read'],selected:true}},
]}};
function node(){{return {{className:'',dataset:{{}},textContent:'',children:[],appendChild(value){{this.children.push(value);return value;}},append(...values){{values.forEach(value=>this.appendChild(value));}},replaceChildren(...values){{this.children=[...values];}},addEventListener(){{}}}};}}
const documentRef={{createElement:node}};
const rendered=[];
const managementRoot={{ownerDocument:documentRef,appendChild(value){{rendered.push(value);return value;}},replaceChildren(...values){{rendered.length=0;rendered.push(...values);}}}};
const transport={{
  probe:async()=>true,
  readSnapshot:async()=>snapshot,
  createConversation:async()=>{{throw new Error('unused');}},
  management:async(_space,surface)=>{{
    if(surface==='list')return {{has_more:false,items:[{{id:item,title:'Part handbook',origin:'inherited',source_space_id:sourcePart}}],next_cursor:null}};
    if(surface==='graph')return {{generation:1,space_id:team,nodes:[],edges:[]}};
    if(surface==='index_log')return {{status:'ready',built_generation:1,desired_generation:1}};
    return {{items:[]}};
  }},
}};
const state={{session_id:null,messages:[],sessionCache:new Map()}};
const chat={{renderMessages:()=>{{}},cloneMessages:value=>value}};
(async()=>{{
  const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,managementRoot,operationIdFactory:()=> '44444444-4444-4444-8444-444444444444'}});
  await bridge.activate();
  await bridge.renderManagement();
  const text=rendered.map(value=>value.textContent).join('|');
  assert.match(text,/Part handbook · Part에서 상속/);
  assert.equal(text.includes(sourcePart),false);
  console.log(JSON.stringify({{text,uuidVisible:text.includes(sourcePart)}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result["uuidVisible"] is False
    assert "Part에서 상속" in str(result["text"])


@pytest.mark.scenario("CONV-001")
@pytest.mark.scenario("CONV-003")
@pytest.mark.scenario("CONV-004")
@pytest.mark.scenario("CONV-011")
def test_p37_browser_transport_uses_only_canonical_scope_conversation_and_sse() -> None:
    result = _run_node(
        f"""
const assert = require('node:assert/strict');
const api = require({json.dumps(str(_TRANSPORT))});
const personal = '11111111-1111-4111-8111-111111111111';
const part = '22222222-2222-4222-8222-222222222222';
const responsibility = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const team = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const oldConversation = '33333333-3333-4333-8333-333333333333';
const newConversation = '44444444-4444-4444-8444-444444444444';
const operation = '55555555-5555-4555-8555-555555555555';
const requestId = '66666666-6666-4666-8666-666666666666';
const turnId = '77777777-7777-4777-8777-777777777777';
const calls = [];
function json(value, status=200) {{ return new Response(JSON.stringify(value), {{status,headers:{{'Content-Type':'application/json'}}}}); }}
function conversation(id, spaceId, title, messages=[]) {{
  return {{request_id:requestId,context_bindings:[],messages,pins:[],summary_status:'absent',conversation:{{
    accessible:true,archived:false,created_at:'2026-08-14T00:00:00.000Z',id,row_version:2,
    space:{{id:spaceId,kind:spaceId===personal?'personal':'part',name:spaceId===personal?'Mine':'Part',org_path:spaceId===personal?[]:['Root','Team','Part'],capabilities:['read']}},
    title,updated_at:'2026-08-14T00:01:00.000Z'
  }}}};
}}
const fetchImpl = async (path, init) => {{
  calls.push({{path,init}});
  if (path === `/api/knowledge/v1/spaces?selected_space_id=${{part}}`) return json({{request_id:requestId,scope_epoch:9,items:[
    {{id:personal,kind:'personal',name:'Mine',org_path:[],capabilities:['read']}},
    {{id:responsibility,kind:'responsibility',name:'Responsibility',org_path:['Root'],capabilities:['read']}},
    {{id:team,kind:'team',name:'Team',org_path:['Root','Team'],capabilities:['read']}},
    {{id:part,kind:'part',name:'Part',org_path:['Root','Team','Part'],capabilities:['read']}}
  ]}});
  if (path === `/api/knowledge/v1/conversations/${{oldConversation}}`) return json(conversation(oldConversation,personal,'Old'));
  if (path === '/api/knowledge/v1/conversations' && init.method === 'POST') return json({{request_id:requestId,operation_id:operation,resource_id:newConversation,row_version:1,status:'created'}},201);
  if (path === `/api/knowledge/v1/conversations/${{newConversation}}/turns/stream`) {{
    const accepted = {{event_sequence:1,event_type:'accepted',operation_id:operation,payload:{{schema_version:1,state:'accepted'}},request_id:requestId,terminal:false,turn_id:turnId}};
    const completed = {{event_sequence:2,event_type:'completed',operation_id:operation,payload:{{schema_version:1,state:'completed'}},request_id:requestId,terminal:true,turn_id:turnId}};
    return new Response(`id: 1\nevent: accepted\ndata: ${{JSON.stringify(accepted)}}\n\nid: 2\nevent: completed\ndata: ${{JSON.stringify(completed)}}\n\n`,{{status:200,headers:{{'Content-Type':'text/event-stream'}}}});
  }}
  if (path === `/api/knowledge/v1/conversations/${{newConversation}}`) return json(conversation(newConversation,part,'New',[
    {{id:'88888888-8888-4888-8888-888888888888',role:'user',sequence_no:1,created_at:'2026-08-14T00:00:00.000Z',content:{{role:'user',content:{{text:'hello'}}}}}},
    {{id:'99999999-9999-4999-8999-999999999999',role:'assistant',sequence_no:2,created_at:'2026-08-14T00:00:01.000Z',content:{{role:'assistant',content:{{text:'part-only answer'}}}}}},
  ]));
  throw new Error('unexpected path: '+path);
}};
(async()=>{{
  const transport=api.createTransport({{fetchImpl,csrfToken:()=> 'csrf-token',operationIdFactory:()=>operation}});
  const snapshot=await transport.readSnapshot({{selectedSpaceId:part,conversationId:oldConversation}});
  assert.equal(snapshot.selected_space_id,part);
  assert.equal(snapshot.conversation_badge.space_id,personal);
  assert.equal(snapshot.conversation_badge.immutable,true);
  assert.equal(Object.hasOwn(snapshot,'actor_user_id'),false);
  const created=await transport.createConversation({{operationId:operation,spaceId:part}});
  assert.equal(created.id,newConversation);
  const turn=await transport.streamTurn({{conversationId:newConversation,message:'hello',operationId:operation}});
  assert.equal(turn.assistantText,'part-only answer');
  assert.deepEqual(turn.events.map(x=>x.event_type),['accepted','completed']);
  assert.ok(calls.every(call=>call.path.startsWith('/api/knowledge/v1/')));
  assert.ok(calls.every(call=>!call.path.includes('/api/code/')));
  const mutations=calls.filter(call=>call.init.method==='POST');
  assert.ok(mutations.every(call=>call.init.headers['X-CSRF-Token']==='csrf-token'));
  assert.ok(mutations.every(call=>call.init.headers['Idempotency-Key']===operation));
  assert.ok(mutations.every(call=>!Object.hasOwn(JSON.parse(call.init.body),'user_id')));
  console.log(JSON.stringify({{assistant:turn.assistantText,callCount:calls.length,selected:snapshot.selected_space_id}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "assistant": "part-only answer",
        "callCount": 5,
        "selected": "22222222-2222-4222-8222-222222222222",
    }


def test_p37_browser_sse_parser_rejects_reorder_binding_and_missing_terminal() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const api=require({json.dumps(str(_TRANSPORT))});
const op='11111111-1111-4111-8111-111111111111';
const req='22222222-2222-4222-8222-222222222222';
const turn='33333333-3333-4333-8333-333333333333';
function event(seq,type,operation=op) {{return {{event_sequence:seq,event_type:type,operation_id:operation,payload:{{}},request_id:req,terminal:type==='completed'||type==='failed',turn_id:turn}};}}
function block(seq,type,operation=op) {{return `id: ${{seq}}\nevent: ${{type}}\ndata: ${{JSON.stringify(event(seq,type,operation))}}\n\n`;}}
const codes=[];
for (const value of [
  block(2,'accepted')+block(1,'completed'),
  block(1,'accepted','44444444-4444-4444-8444-444444444444')+block(2,'completed'),
  block(1,'accepted'),
]) {{ try {{api.decodeSse(value,op);}} catch(error) {{codes.push(error.code);}} }}
assert.deepEqual(codes,['INVALID_EVENT_STREAM','INVALID_EVENT_STREAM','INCOMPLETE_EVENT_STREAM']);
console.log(JSON.stringify({{codes}}));
"""
    )
    assert result == {
        "codes": [
            "INVALID_EVENT_STREAM",
            "INVALID_EVENT_STREAM",
            "INCOMPLETE_EVENT_STREAM",
        ]
    }


def test_p37_browser_surfaces_stable_nested_codex_failure() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const api=require({json.dumps(str(_TRANSPORT))});
const conversation='11111111-1111-4111-8111-111111111111';
const operation='22222222-2222-4222-8222-222222222222';
const request='33333333-3333-4333-8333-333333333333';
const turn='44444444-4444-4444-8444-444444444444';
const fetchImpl=async()=>{{
 const accepted={{event_sequence:1,event_type:'accepted',operation_id:operation,payload:{{schema_version:1,state:'accepted'}},request_id:request,terminal:false,turn_id:turn}};
 const failed={{event_sequence:2,event_type:'failed',operation_id:operation,payload:{{error:{{code:'CODEX_EXEC_TIMEOUT',retryable:true,schema_version:1}},schema_version:1,state:'failed'}},request_id:request,terminal:true,turn_id:turn}};
 return new Response(`id: 1\nevent: accepted\ndata: ${{JSON.stringify(accepted)}}\n\nid: 2\nevent: failed\ndata: ${{JSON.stringify(failed)}}\n\n`,{{status:200,headers:{{'Content-Type':'text/event-stream'}}}});
}};
(async()=>{{
 const transport=api.createTransport({{fetchImpl,csrfToken:()=> 'csrf',operationIdFactory:()=>operation}});
 let observed=null;
 try{{await transport.streamTurn({{conversationId:conversation,message:'hello',operationId:operation}});}}
 catch(error){{observed={{code:error.code,message:error.message,retryable:error.retryable,terminal:error.terminal}};}}
 assert.deepEqual(observed,{{code:'CODEX_EXEC_TIMEOUT',message:'Codex 응답 시간이 초과되었습니다. 다시 시도해 주세요.',retryable:true,terminal:true}});
 console.log(JSON.stringify(observed));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "code": "CODEX_EXEC_TIMEOUT",
        "message": "Codex 응답 시간이 초과되었습니다. 다시 시도해 주세요.",
        "retryable": True,
        "terminal": True,
    }


@pytest.mark.scenario("WIKI-019")
def test_transport_requests_server_authorized_exact_personal_wiki_snapshot() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const api=require({json.dumps(str(_TRANSPORT))});
const defaultWiki='11111111-1111-4111-8111-111111111111';
const exactWiki='22222222-2222-4222-8222-222222222222';
const part='33333333-3333-4333-8333-333333333333';
const request='44444444-4444-4444-8444-444444444444';
const paths=[];
function json(value){{return new Response(JSON.stringify(value),{{status:200,headers:{{'Content-Type':'application/json'}}}});}}
const fetchImpl=async(path)=>{{
 paths.push(path);
 if(path===`/api/knowledge/v1/spaces?selected_space_id=${{exactWiki}}`)return json({{request_id:request,scope_epoch:3,items:[
  {{id:defaultWiki,kind:'personal',name:'Mine',org_path:[],capabilities:['read']}},
  {{id:part,kind:'part',name:'Part',org_path:['Root','Team','Part'],capabilities:['read']}},
  {{id:exactWiki,kind:'personal',name:'Project Wiki',org_path:[],capabilities:['read','publish']}},
 ]}});
 throw new Error('unexpected '+path);
}};
(async()=>{{
 const transport=api.createTransport({{fetchImpl,operationIdFactory:()=>request}});
 const snapshot=await transport.readSnapshot({{selectedSpaceId:exactWiki}});
 assert.equal(snapshot.selected_space_id,exactWiki);
 assert.equal(snapshot.spaces.filter(value=>value.kind==='personal').length,2);
 assert.equal(snapshot.spaces.find(value=>value.id===exactWiki).name,'Project Wiki');
 assert.deepEqual(paths,[`/api/knowledge/v1/spaces?selected_space_id=${{exactWiki}}`]);
 console.log(JSON.stringify({{selected:snapshot.selected_space_id,spaces:snapshot.spaces.length}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "selected": "22222222-2222-4222-8222-222222222222",
        "spaces": 3,
    }


def test_transport_accepts_versioned_finding_page_closure_hash() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const api=require({json.dumps(str(_TRANSPORT))});
const space='11111111-1111-4111-8111-111111111111';
const request='22222222-2222-4222-8222-222222222222';
const item='33333333-3333-4333-8333-333333333333';
const revision='44444444-4444-4444-8444-444444444444';
const finding='55555555-5555-4555-8555-555555555555';
const fetchImpl=async(path)=>new Response(JSON.stringify({{
 corpus_closure_sha256:'a'.repeat(64),has_more:false,items:[{{
  capabilities:['read'],confidence:0.8,created_at:'2026-08-25T00:00:00.000Z',
  detector_name:'detector',detector_version:'1',evidence:{{codes:['fixture']}},
  finding_kind:'duplicate',id:finding,inherited:false,model_id:null,origin:'native',
  overlay_grant_id:null,prompt_version:null,proposed_action:'review',
  required_reviewer:'owner',risk_tier:'T1',row_version:1,rule_version:'1',
  selected_view_space_id:space,severity:'low',source_item_id:item,
  source_revision_id:revision,source_space_id:space,space_id:space,status:'pending',
  target_item_id:null,target_revision_id:null,updated_at:'2026-08-25T00:00:00.000Z',
  visibility_effect:'none',
 }}],next_cursor:null,request_id:request,
}}),{{status:200,headers:{{'Content-Type':'application/json'}}}});
(async()=>{{
 const transport=api.createTransport({{fetchImpl,operationIdFactory:()=>request}});
 const page=await transport.management(space,'candidates');
 assert.equal(page.corpus_closure_sha256,'a'.repeat(64));
 assert.equal(page.items[0].selected_view_space_id,space);
 console.log(JSON.stringify({{hash:page.corpus_closure_sha256,items:page.items.length}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {"hash": "a" * 64, "items": 1}


@pytest.mark.scenario("WIKI-019")
def test_management_item_transport_preserves_bounded_cursor_pagination() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const api=require({json.dumps(str(_TRANSPORT))});
const space='11111111-1111-4111-8111-111111111111';
const cursor='eyJpZCI6IjIyMjIyMjIyLTIyMjItNDIyMi04MjIyLTIyMjIyMjIyMjIyMiJ9';
const calls=[];
const fetchImpl=async(path)=>{{
 calls.push(path);
 return new Response(JSON.stringify({{items:[],has_more:false,next_cursor:null,request_id:space}}),{{status:200,headers:{{'Content-Type':'application/json'}}}});
}};
(async()=>{{
 const transport=api.createTransport({{fetchImpl,operationIdFactory:()=>space}});
 await transport.management(space,'list');
 await transport.management(space,'list',{{cursor,limit:50}});
 assert.deepEqual(calls,[
  `/api/knowledge/v1/spaces/${{space}}/items`,
  `/api/knowledge/v1/spaces/${{space}}/items?limit=50&cursor=${{cursor}}`,
 ]);
 await assert.rejects(()=>transport.management(space,'list',{{limit:101}}),/limit is invalid/);
 console.log(JSON.stringify({{calls}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result["calls"] == [
        "/api/knowledge/v1/spaces/11111111-1111-4111-8111-111111111111/items",
        "/api/knowledge/v1/spaces/11111111-1111-4111-8111-111111111111/items?limit=50&cursor=eyJpZCI6IjIyMjIyMjIyLTIyMjItNDIyMi04MjIyLTIyMjIyMjIyMjIyMiJ9",
    ]


@pytest.mark.scenario("WIKI-019")
def test_management_sidebar_loads_every_item_page_without_duplicates() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const space='11111111-1111-4111-8111-111111111111';
const first='22222222-2222-4222-8222-222222222222';
const second='33333333-3333-4333-8333-333333333333';
const personal='44444444-4444-4444-8444-444444444444';
const cursor='cursor-1';
const snapshot={{conversation_badge:null,scope_epoch:1,selected_space_id:space,spaces:[
 {{id:personal,kind:'personal',name:'Mine',org_path:[],capabilities:['read'],native_authority:null,selected:false}},
 {{id:space,kind:'responsibility',name:'Responsibility',org_path:['Responsibility'],capabilities:['read'],native_authority:null,selected:true}},
]}};
function node(){{return {{className:'',dataset:{{}},textContent:'',children:[],listeners:{{}},appendChild(value){{this.children.push(value);return value;}},append(...values){{values.forEach(value=>this.appendChild(value));}},replaceChildren(...values){{this.children=[...values];}},addEventListener(name,handler){{this.listeners[name]=handler;}}}};}}
const documentRef={{createElement:node}};
const rendered=[];
const managementRoot={{ownerDocument:documentRef,appendChild(value){{rendered.push(value);return value;}},replaceChildren(...values){{rendered.length=0;rendered.push(...values);}}}};
const listCalls=[];
const transport={{
 probe:async()=>true,probePublication:async()=>false,readSnapshot:async()=>snapshot,
 createConversation:async()=>{{throw new Error('unused');}},
 management:async(_space,surface,options)=>{{
  if(surface==='list'){{
   listCalls.push(options||null);
   if(options&&options.cursor===cursor)return {{corpus_closure_sha256:'a'.repeat(64),has_more:false,items:[{{id:first,title:'first',origin:'native',source_space_id:space}},{{id:second,title:'second',origin:'native',source_space_id:space}}],next_cursor:null}};
   return {{corpus_closure_sha256:'a'.repeat(64),has_more:true,items:[{{id:first,title:'first',origin:'native',source_space_id:space}}],next_cursor:cursor}};
  }}
  if(surface==='graph')return {{generation:1,space_id:space,nodes:[],edges:[]}};
  if(surface==='index_log')return {{status:'ready',built_generation:1,desired_generation:1}};
  return {{items:[]}};
 }}
}};
const state={{session_id:null,messages:[],sessionCache:new Map()}};
const chat={{renderMessages:()=>{{}},cloneMessages:value=>value}};
(async()=>{{
 const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,managementRoot,operationIdFactory:()=>space,onError:error=>{{throw error;}}}});
 await bridge.activate();
 await bridge.renderManagement();
 const more=rendered.find(value=>String(value.className).includes('canonical-items-more'));
 assert.ok(more);
 more.listeners.click();
 for(let i=0;i<20&&listCalls.length<2;i++)await new Promise(resolve=>setImmediate(resolve));
 for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(listCalls,[null,{{cursor,limit:50}}]);
 assert.equal(rendered.filter(value=>value.dataset.canonicalItemId===first).length,1);
 assert.equal(rendered.filter(value=>value.dataset.canonicalItemId===second).length,1);
 assert.equal(rendered.some(value=>String(value.className).includes('canonical-items-more')),false);
 console.log(JSON.stringify({{calls:listCalls.length,items:rendered.filter(value=>value.dataset.canonicalItemId).map(value=>value.textContent)}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "calls": 2,
        "items": ["first · 현재 공간 문서", "second · 현재 공간 문서"],
    }


def test_p45_browser_transport_uses_only_canonical_publication_contracts() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const api=require({json.dumps(str(_TRANSPORT))});
const operation='11111111-1111-4111-8111-111111111111';
const preview='22222222-2222-4222-8222-222222222222';
const requestId='33333333-3333-4333-8333-333333333333';
const artifact='44444444-4444-4444-8444-444444444444';
const item='55555555-5555-4555-8555-555555555555';
const revision='66666666-6666-4666-8666-666666666666';
const space='77777777-7777-4777-8777-777777777777';
const calls=[];
function json(value,status=200){{return new Response(JSON.stringify(value),{{status,headers:{{'Content-Type':'application/json'}}}});}}
const ready={{request_id:requestId,operation_id:operation,preview_id:preview,job_id:null,status:'ready',source_snapshot_sha256:'a'.repeat(64),title:'Manual',size_bytes:7,warnings:[],available_target_modes:['personal','part','both'],deterministic_findings:[],ready_result_schema_version:1,expires_at:'2026-08-14T01:00:00.000Z',created_at:'2026-08-14T00:00:00.000Z',updated_at:'2026-08-14T00:00:00.000Z',completed_at:'2026-08-14T00:00:00.000Z'}};
const fetchImpl=async(path,init)=>{{
  calls.push({{path,init}});
  if(path==='/api/knowledge/v1/publication-previews'&&init.method==='POST')return json(ready);
  if(path===`/api/knowledge/v1/publication-previews/${{preview}}`&&init.method==='GET')return json(ready);
  if(path===`/api/knowledge/v1/publication-previews/${{preview}}`&&init.method==='DELETE')return json({{request_id:requestId,operation_id:operation,resource_id:preview,row_version:2,status:'cancelled'}});
  if(path==='/api/knowledge/v1/publications'&&init.method==='POST')return json({{request_id:requestId,operation_id:operation,results:[{{space_id:space,outcome:'created',source_artifact_id:artifact,item_id:item,resolved_revision_id:revision,created_revision_id:revision,finding_id:null}}]}},201);
  throw new Error('unexpected '+path);
}};
(async()=>{{
 const transport=api.createTransport({{fetchImpl,csrfToken:()=> 'csrf',operationIdFactory:()=>operation}});
 const created=await transport.createPublicationPreview({{sourceType:'manual',markdown:'# Manual\\n',operationId:operation}});
 assert.equal(created.preview_id,preview);
 await transport.getPublicationPreview(preview);
 const cancelled=await transport.cancelPublicationPreview({{previewId:preview,operationId:operation}});
 assert.equal(cancelled.status,'cancelled');
 const committed=await transport.commitPublication({{previewId:preview,sourceSnapshotSha256:'a'.repeat(64),targetMode:'both',personalSpaceId:space,operationId:operation}});
 assert.equal(committed.results[0].item_id,item);
 const publicationCall=calls.find(call=>call.path==='/api/knowledge/v1/publications');
 assert.equal(JSON.parse(publicationCall.init.body).personal_space_id,space);
 const mutations=calls.filter(call=>!['GET','HEAD','OPTIONS'].includes(call.init.method));
 assert.ok(mutations.every(call=>call.init.headers['X-CSRF-Token']==='csrf'));
 const casMutations=mutations.filter(call=>!call.path.endsWith('/search'));
 assert.ok(casMutations.every(call=>call.init.headers['Idempotency-Key']===operation));
 assert.ok(calls.every(call=>call.path.startsWith('/api/knowledge/v1/')));
 assert.ok(mutations.every(call=>!Object.hasOwn(JSON.parse(call.init.body),'actor_user_id')));
 console.log(JSON.stringify({{callCount:calls.length,outcome:committed.results[0].outcome}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {"callCount": 4, "outcome": "created"}


@pytest.mark.scenario("WIKI-019")
def test_personal_wiki_transport_uses_only_v2_owner_routes_and_exact_cas_payloads() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const api=require({json.dumps(str(_TRANSPORT))});
const request='11111111-1111-4111-8111-111111111111';
const operation='22222222-2222-4222-8222-222222222222';
const space='33333333-3333-4333-8333-333333333333';
const item='44444444-4444-4444-8444-444444444444';
const revision='55555555-5555-4555-8555-555555555555';
const calls=[];
function json(value,status=200){{return new Response(JSON.stringify(value),{{status,headers:{{'Content-Type':'application/json'}}}});}}
const profile={{archive_reason:null,archived_at:null,created_at:'2026-08-25T00:00:00.000Z',display_name:'Wiki',is_default:true,lifecycle_generation:1,lifecycle_status:'active',row_version:4,space_id:space,updated_at:'2026-08-25T00:00:00.000Z'}};
const fetchImpl=async(path,init)=>{{
 calls.push({{path,init}});
 if(path.startsWith('/api/knowledge/v2/personal-wikis?'))return json({{items:[profile],next_cursor:null,request_id:request}});
 if(path==='/api/knowledge/v2/personal-wikis/search')return json({{items:[{{item_id:item,provenance:[],revision_id:revision,score:1,snippet:'body',source_space_id:space,title:'Title',wiki_display_name:'Wiki'}}],next_cursor:null,request_id:request}});
 return json({{operation_id:operation,request_id:request,resource_id:space,row_version:5,status:'updated'}});
}};
(async()=>{{
 const transport=api.createTransport({{fetchImpl,csrfToken:()=> 'csrf',operationIdFactory:()=>operation}});
 const page=await transport.listPersonalWikis({{lifecycleStatus:'active',limit:50}});
 assert.equal(page.items[0].space_id,space);
 const search=await transport.searchPersonalWikis({{query:'needle'}});
 assert.equal(search.items[0].source_space_id,space);
 await transport.createPersonalWiki({{displayName:'New',expectedRowVersion:4,operationId:operation}});
 await transport.renamePersonalWiki({{spaceId:space,displayName:'Renamed',expectedRowVersion:4,operationId:operation}});
 await transport.setDefaultPersonalWiki({{spaceId:space,expectedRowVersion:4,operationId:operation}});
 await transport.archivePersonalWiki({{spaceId:space,expectedRowVersion:4,reason:'done',operationId:operation}});
 await transport.restorePersonalWiki({{spaceId:space,expectedRowVersion:5,makeDefault:true,operationId:operation}});
 assert.ok(calls.every(call=>call.path.startsWith('/api/knowledge/v2/personal-wikis')));
 assert.ok(calls.every(call=>!call.path.includes('/api/code/')));
 const mutations=calls.filter(call=>!['GET','HEAD','OPTIONS'].includes(call.init.method));
 assert.ok(mutations.every(call=>call.init.headers['X-CSRF-Token']==='csrf'));
 const casMutations=mutations.filter(call=>!call.path.endsWith('/search'));
 assert.ok(casMutations.every(call=>call.init.headers['Idempotency-Key']===operation));
 assert.equal(JSON.parse(calls.find(call=>call.path.endsWith('/restore')).init.body).expected_row_version,5);
 console.log(JSON.stringify({{calls:calls.length,searchSpace:search.items[0].source_space_id}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "calls": 7,
        "searchSpace": "33333333-3333-4333-8333-333333333333",
    }


def test_p45_publication_probe_distinguishes_absent_from_unknown_error_without_mutation() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const api=require({json.dumps(str(_TRANSPORT))});
const calls=[];
function transport(status){{return api.createTransport({{operationIdFactory:()=> '11111111-1111-4111-8111-111111111111',fetchImpl:async(path,init)=>{{calls.push([path,init.method,status]);return new Response('',{{status}});}}}});}}
(async()=>{{
 assert.equal(await transport(200).probePublication(),true);
 assert.equal(await transport(404).probePublication(),false);
 let status=0;
 try{{await transport(503).probePublication();}}catch(error){{status=error.status;}}
 assert.equal(status,503);
 assert.deepEqual(calls.map(value=>value[1]),['OPTIONS','OPTIONS','OPTIONS']);
 console.log(JSON.stringify({{calls:calls.length,status}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {"calls": 3, "status": 503}


def test_p45_shared_publication_controller_freezes_modes_warning_and_results() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const ui=require({json.dumps(str(_PUBLICATION))});
const ids={{preview:'11111111-1111-4111-8111-111111111111',personal:'22222222-2222-4222-8222-222222222222',part:'33333333-3333-4333-8333-333333333333',item:'44444444-4444-4444-8444-444444444444',operation:'55555555-5555-4555-8555-555555555555'}};
const calls=[];const rendered=[];
const transport={{
 createPublicationPreview:async source=>{{calls.push(['preview',source]);return {{preview_id:ids.preview,status:'queued'}};}},
 cancelPublicationPreview:async value=>{{calls.push(['cancel',value]);return {{status:'cancelled'}};}},
 getPublicationPreview:async id=>{{calls.push(['poll',id]);return {{preview_id:id,status:'ready',source_snapshot_sha256:'a'.repeat(64),title:'Canonical source',warnings:['lint warning'],available_target_modes:['personal','part','both']}};}},
 spaces:async()=>({{items:[{{id:ids.personal,kind:'personal',name:'내 지식'}},{{id:ids.part,kind:'part',name:'파트 A'}}]}}),
 commitPublication:async value=>{{calls.push(['commit',value]);return {{operation_id:value.operationId,results:[{{space_id:ids.part,outcome:'update_proposed',source_artifact_id:ids.personal,item_id:ids.item,resolved_revision_id:ids.item,created_revision_id:null,finding_id:ids.preview}}]}};}},
}};
const renderer={{showPreparing:()=>rendered.push('preparing'),showPreview:(view,commit)=>{{rendered.push(view);renderer.commit=commit;}},showResults:view=>rendered.push(view),showError:error=>{{throw error;}}}};
(async()=>{{
 const controller=ui.createController({{transport,renderer,operationIdFactory:()=>ids.operation,sleep:async()=>{{}},maxPolls:2}});
 const opened=await controller.open({{sourceType:'notion',pageId:'page-1',preferredTargetMode:'part'}});
 assert.deepEqual(opened.view.options.map(x=>[x.mode,x.label,x.disabled]),[['personal','개인',false],['part','파트 A',false],['both','개인 + 파트 A',false]]);
 assert.equal(opened.view.selectedMode,'part');
 assert.equal(Object.hasOwn(calls[0][1],'preferredTargetMode'),false);
 assert.equal(opened.view.bothWarning,'두 개의 독립 지식으로 등록되며 이후 자동 동기화되지 않습니다.');
 await renderer.commit('both');
 assert.equal(controller.state().view.targets[0].label,'기존 지식 변경 제안');
 assert.equal(calls.at(-1)[1].targetMode,'both');
 assert.equal(calls.at(-1)[1].sourceSnapshotSha256,'a'.repeat(64));
 console.log(JSON.stringify({{calls:calls.length,label:controller.state().view.targets[0].label,warning:opened.view.bothWarning}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "calls": 3,
        "label": "기존 지식 변경 제안",
        "warning": "두 개의 독립 지식으로 등록되며 이후 자동 동기화되지 않습니다.",
    }


@pytest.mark.scenario("UPLOAD-013")
@pytest.mark.scenario("UPLOAD-014")
def test_publication_controller_keeps_exact_personal_wiki_and_commit_id_across_retry() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const ui=require({json.dumps(str(_PUBLICATION))});
const ids={{preview:'11111111-1111-4111-8111-111111111111',defaultWiki:'22222222-2222-4222-8222-222222222222',selectedWiki:'33333333-3333-4333-8333-333333333333',previewOperation:'44444444-4444-4444-8444-444444444444',commitOperation:'55555555-5555-4555-8555-555555555555',item:'66666666-6666-4666-8666-666666666666'}};
let operationIndex=0;let attempts=0;const commits=[];const shown=[];
const transport={{
 createPublicationPreview:async()=>({{preview_id:ids.preview,status:'ready',source_snapshot_sha256:'c'.repeat(64),title:'Exact Wiki',warnings:[],available_target_modes:['personal','both']}}),
 cancelPublicationPreview:async()=>({{status:'cancelled'}}),getPublicationPreview:async()=>{{throw new Error('must not poll');}},
 spaces:async()=>({{items:[{{id:ids.defaultWiki,kind:'personal',name:'Default'}}]}}),
 commitPublication:async value=>{{commits.push(value);attempts++;if(attempts===1)throw new Error('unknown outcome');return {{operation_id:value.operationId,results:[{{space_id:ids.selectedWiki,outcome:'created',source_artifact_id:ids.item,item_id:ids.item,resolved_revision_id:ids.item,created_revision_id:ids.item,finding_id:null}}]}};}},
}};
const renderer={{showPreview:(view,commit)=>{{shown.push(view);renderer.commit=commit;}},showResults:view=>shown.push(view),showError:()=>{{}}}};
(async()=>{{
 const operations=[ids.previewOperation,ids.commitOperation];
 const controller=ui.createController({{transport,renderer,operationIdFactory:()=>operations[operationIndex++]}});
 const opened=await controller.open({{sourceType:'upload',file:'binary',personalSpaceId:ids.selectedWiki,personalWikiDisplayName:'Selected Wiki',preferredTargetMode:'personal'}});
 assert.equal(opened.view.personalWiki.spaceId,ids.selectedWiki);
 await assert.rejects(()=>renderer.commit('personal'),/unknown outcome/);
 await renderer.commit('personal');
 assert.deepEqual(commits.map(value=>value.operationId),[ids.commitOperation,ids.commitOperation]);
 assert.deepEqual(commits.map(value=>value.personalSpaceId),[ids.selectedWiki,ids.selectedWiki]);
 assert.equal(controller.state().view.publicationStatus,'게시 완료');
 assert.equal(controller.state().view.enrichmentStatus,'별도 처리');
 console.log(JSON.stringify({{attempts,operation:commits[0].operationId,space:commits[0].personalSpaceId}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "attempts": 2,
        "operation": "55555555-5555-4555-8555-555555555555",
        "space": "33333333-3333-4333-8333-333333333333",
    }


@pytest.mark.scenario("UPLOAD-013")
@pytest.mark.scenario("UPLOAD-014")
@pytest.mark.scenario("UPLOAD-015")
def test_batch_publication_is_bounded_independent_and_retries_the_exact_failed_commit() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const ui=require({json.dumps(str(_PUBLICATION))});
let operationCounter=0;
const operationId=()=>`00000000-0000-4000-8000-${{String(++operationCounter).padStart(12,'0')}}`;
const personal='11111111-1111-4111-8111-111111111111';
const previewNames=new Map();const commitAttempts=new Map();const commitOperations=new Map();
let activePrepare=0;let maxPrepare=0;let activeCommit=0;let maxCommit=0;
const transport={{
 spaces:async()=>({{items:[{{id:personal,kind:'personal',name:'Selected Wiki'}}]}}),
 createPublicationPreview:async source=>{{
   activePrepare++;maxPrepare=Math.max(maxPrepare,activePrepare);
   await new Promise(resolve=>setImmediate(resolve));
   activePrepare--;
   const number=Number(source.file.name.split('.')[0]);
   const id=`20000000-0000-4000-8000-${{String(number).padStart(12,'0')}}`;
   previewNames.set(id,source.file.name);
   return {{preview_id:id,status:'ready',source_snapshot_sha256:String(number).repeat(64).slice(0,64),title:source.file.name,warnings:[],available_target_modes:['personal']}};
 }},
 getPublicationPreview:async()=>{{throw new Error('must not poll');}},
 cancelPublicationPreview:async value=>({{preview_id:value.previewId,status:'cancelled'}}),
 commitPublication:async value=>{{
   activeCommit++;maxCommit=Math.max(maxCommit,activeCommit);
   await new Promise(resolve=>setImmediate(resolve));
   activeCommit--;
   const name=previewNames.get(value.previewId);
   const attempts=(commitAttempts.get(name)||0)+1;commitAttempts.set(name,attempts);
   if(!commitOperations.has(name))commitOperations.set(name,value.operationId);
   else assert.equal(value.operationId,commitOperations.get(name));
   if(name==='2.docx'&&attempts===1)throw new Error('unknown commit outcome');
   const number=Number(name.split('.')[0]);
   const item=`30000000-0000-4000-8000-${{String(number).padStart(12,'0')}}`;
   return {{operation_id:value.operationId,results:[{{space_id:personal,outcome:'created',source_artifact_id:item,item_id:item,resolved_revision_id:item,created_revision_id:item,finding_id:null}}]}};
 }},
}};
const renderer={{
 showBatchProgress:()=>{{}},showBatchCommitProgress:()=>{{}},
 showBatchPreview:(view,commit)=>{{renderer.preview=view;renderer.commit=commit;}},
 showBatchResults:(view,retry)=>{{renderer.result=view;renderer.retry=retry;}},
}};
(async()=>{{
 const controller=ui.createBatchController({{transport,renderer,operationIdFactory:operationId,concurrency:2}});
 const sources=[1,2,3,4].map(number=>({{sourceType:'upload',file:{{name:`${{number}}.docx`}},title:`${{number}}.docx`,preferredTargetMode:'personal',personalSpaceId:personal,personalWikiDisplayName:'Selected Wiki'}}));
 await controller.open(sources);
 assert.equal(renderer.preview.readyCount,4);
 assert.equal(maxPrepare,2);
 await renderer.commit();
 assert.equal(renderer.result.succeededCount,3);
 assert.equal(renderer.result.failedCount,1);
 assert.equal(maxCommit,2);
 await renderer.retry();
 assert.equal(renderer.result.succeededCount,4);
 assert.equal(renderer.result.failedCount,0);
 assert.equal(commitAttempts.get('2.docx'),2);
 assert.equal(new Set(commitOperations.values()).size,4);
 console.log(JSON.stringify({{maxPrepare,maxCommit,succeeded:renderer.result.succeededCount,retried:commitAttempts.get('2.docx')}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {"maxCommit": 2, "maxPrepare": 2, "retried": 2, "succeeded": 4}


def test_p45_publication_controller_disables_part_and_rejects_forced_both() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const ui=require({json.dumps(str(_PUBLICATION))});
const preview='11111111-1111-4111-8111-111111111111';
let committed=false;let selected;
const transport={{createPublicationPreview:async()=>({{preview_id:preview,status:'ready',source_snapshot_sha256:'b'.repeat(64),title:'Personal only',warnings:[],available_target_modes:['personal']}}),cancelPublicationPreview:async()=>({{status:'cancelled'}}),spaces:async()=>({{items:[{{id:'22222222-2222-4222-8222-222222222222',kind:'personal',name:'내 지식'}}]}}),getPublicationPreview:async()=>{{throw new Error('must not poll');}},commitPublication:async()=>{{committed=true;}}}};
const renderer={{showPreview:(view,commit)=>{{selected=view;renderer.commit=commit;}},showResults:()=>{{}},showError:()=>{{}}}};
(async()=>{{
 const controller=ui.createController({{transport,renderer,operationIdFactory:()=> '33333333-3333-4333-8333-333333333333'}});
 await controller.open({{sourceType:'manual',markdown:'# x'}});
 assert.deepEqual(selected.options.map(x=>[x.mode,x.disabled]),[['personal',false],['part',true],['both',true]]);
 await assert.rejects(()=>renderer.commit('both'),/선택할 수 없는/);
 assert.equal(committed,false);
 console.log(JSON.stringify({{committed,disabled:selected.options.filter(x=>x.disabled).map(x=>x.mode)}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {"committed": False, "disabled": ["part", "both"]}


def test_p45_publication_controller_rejects_an_initial_target_that_became_unavailable() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const ui=require({json.dumps(str(_PUBLICATION))});
const preview='11111111-1111-4111-8111-111111111111';
let rendered=false;
const transport={{createPublicationPreview:async source=>{{assert.equal(Object.hasOwn(source,'preferredTargetMode'),false);return {{preview_id:preview,status:'ready',source_snapshot_sha256:'b'.repeat(64),title:'Personal only',warnings:[],available_target_modes:['personal']}};}},cancelPublicationPreview:async()=>({{status:'cancelled'}}),spaces:async()=>({{items:[{{id:'22222222-2222-4222-8222-222222222222',kind:'personal',name:'내 지식'}}]}}),getPublicationPreview:async()=>{{throw new Error('must not poll');}},commitPublication:async()=>{{throw new Error('must not commit');}}}};
const renderer={{showPreview:()=>{{rendered=true;}},showResults:()=>{{}},showError:()=>{{}}}};
(async()=>{{
 const controller=ui.createController({{transport,renderer,operationIdFactory:()=> '33333333-3333-4333-8333-333333333333'}});
 await assert.rejects(()=>controller.open({{sourceType:'manual',markdown:'# x',preferredTargetMode:'part'}}),/처음 선택한 게시 대상/);
 assert.equal(rendered,false);
 console.log(JSON.stringify({{rendered}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {"rendered": False}


def test_p45_publication_controller_cancels_durable_pending_preview_and_stops_polling() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const ui=require({json.dumps(str(_PUBLICATION))});
const preview='11111111-1111-4111-8111-111111111111';
const cancelOperation='22222222-2222-4222-8222-222222222222';
let polls=0;let cancelRequest=null;let releaseSleep;
const transport={{
 createPublicationPreview:async()=>({{preview_id:preview,status:'queued'}}),
 cancelPublicationPreview:async value=>{{cancelRequest=value;return {{operation_id:value.operationId,preview_id:value.previewId,status:'cancelled'}};}},
 getPublicationPreview:async()=>{{polls++;throw new Error('poll after cancellation');}},
 spaces:async()=>{{throw new Error('spaces after cancellation');}},commitPublication:async()=>{{throw new Error('commit after cancellation');}},
}};
const renderer={{showPreparing:()=>{{}},showPending:(value,cancel)=>{{assert.equal(value.preview_id,preview);renderer.cancel=cancel;}},showPreview:()=>{{throw new Error('ready UI after cancellation');}},showResults:()=>{{}},showError:error=>{{throw error;}}}};
(async()=>{{
 const controller=ui.createController({{transport,renderer,operationIdFactory:()=>cancelOperation,sleep:()=>new Promise(resolve=>{{releaseSleep=resolve;}})}});
 const opening=controller.open({{sourceType:'upload',file:{{}}}});
 while(!renderer.cancel) await new Promise(resolve=>setImmediate(resolve));
 const cancelled=await renderer.cancel();
 assert.equal(cancelled.status,'cancelled');
 releaseSleep();
 const result=await opening;
 assert.equal(result.cancelled,true);
 assert.deepEqual(cancelRequest,{{operationId:cancelOperation,previewId:preview}});
 assert.equal(polls,0);
 assert.equal(controller.state().result.status,'cancelled');
 console.log(JSON.stringify({{cancelled:result.cancelled,polls,status:controller.state().result.status}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {"cancelled": True, "polls": 0, "status": "cancelled"}


def test_p45_main_ui_loads_shared_modal_before_browser_and_wires_two_sources() -> None:
    index = _INDEX.read_text(encoding="utf-8")
    knowledge = _KNOWLEDGE.read_text(encoding="utf-8")
    assert index.index("static/knowledge_core_transport.js") < index.index(
        "static/knowledge_publication.js"
    ) < index.index("static/knowledge_core_browser.js")
    assert "sourceType: \"chat\"" in knowledge
    assert "sourceType: \"upload\"" in knowledge
    assert "window.KnowledgeCoreBrowser?.active" in knowledge
    assert 'publicationAvailability === "available"' in knowledge
    assert 'publicationAvailability === "error"' in knowledge
    assert 'title: "지식에 추가"' in knowledge
    assert 'makeSourceTab("upload", "자료 업로드")' in knowledge
    assert 'makeSourceTab("chat", "대화 지식화")' in knowledge
    assert 'targetTitle.textContent = "게시 위치"' in knowledge
    assert 'for (const mode of ["personal", "part", "both", "explicit"])' in knowledge
    assert "preferredTargetMode: selectedTarget" in knowledge
    assert "fileInput.multiple = true" in knowledge
    assert 'fileDrop.addEventListener(eventName' in knowledge
    assert "KnowledgeCoreBrowser.publishSources(requests)" in knowledge
    assert "api/code/knowledge/chat/promote" in knowledge
    assert "api/code/knowledge/source/import_batch" in knowledge


def test_p45_notion_entry_uses_canonical_once_and_falls_back_only_when_probe_is_absent() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const bridge=require({json.dumps(str(_NOTION_PUBLICATION))});
(async()=>{{
 const calls=[];
 const canonical=await bridge.publish({{pageId:'page-1',probePublication:async()=>true,openCanonical:async id=>{{calls.push(['canonical',id]);return 'canonical';}},openLegacy:async id=>{{calls.push(['legacy',id]);return 'legacy';}}}});
 assert.equal(canonical,'canonical');
 assert.deepEqual(calls,[['canonical','page-1']]);
 const absent=await bridge.publish({{pageId:'page-2',probePublication:async()=>false,openCanonical:async()=>{{throw new Error('must not run');}},openLegacy:async id=>{{calls.push(['legacy',id]);return 'legacy';}}}});
 assert.equal(absent,'legacy');
 await assert.rejects(()=>bridge.publish({{pageId:'page-3',probePublication:async()=>{{throw new Error('probe failure');}},openCanonical:async()=>{{calls.push(['canonical-error']);}},openLegacy:async()=>{{calls.push(['legacy-error']);}}}}),/probe failure/);
 await assert.rejects(()=>bridge.publish({{pageId:'page-4',probePublication:async()=>true,openCanonical:async()=>{{calls.push(['canonical-failure']);throw new Error('unknown outcome');}},openLegacy:async()=>{{calls.push(['legacy-after-mutation']);}}}}),/unknown outcome/);
 assert.deepEqual(calls,[['canonical','page-1'],['legacy','page-2'],['canonical-failure']]);
 console.log(JSON.stringify({{calls,canonical,absent}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "absent": "legacy",
        "calls": [
            ["canonical", "page-1"],
            ["legacy", "page-2"],
            ["canonical-failure"],
        ],
        "canonical": "canonical",
    }


def test_p45_notion_page_loads_shared_publication_bridge_before_workspace_code() -> None:
    html = _NOTION_INDEX.read_text(encoding="utf-8")
    notion = _NOTION_JS.read_text(encoding="utf-8")
    assert html.index("/static/knowledge_core_transport.js") < html.index(
        "/static/knowledge_publication.js"
    ) < html.index("/notion_static/notion_publication.js") < html.index(
        "/notion_static/notion.js"
    )
    assert "window.NotionPublicationBridge.publish" in notion
    assert 'sourceType: "notion"' in notion
    assert "probePublication" in notion
    assert 'background.dataset.locked = "1"' in notion
    assert "closeModal(true)" in notion
    assert "/publish_preview" in notion
    assert "/publish_second_brain" in notion


def test_p45_browser_keeps_reads_active_but_never_legacy_falls_back_on_probe_error() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const state={{session_id:null,messages:[],sessionCache:new Map()}};
const chat={{renderMessages:()=>{{}},cloneMessages:value=>value}};
const scope={{CHAT_MODE:'chat',createController:()=>({{initialize:async()=>{{}},state:()=>({{currentConversationId:null,selectedSpaceId:'space',spaces:[]}})}})}};
let publicationControllers=0;
function options(probePublication){{return {{
 chat,scopeUI:scope,state,operationIdFactory:()=> '11111111-1111-4111-8111-111111111111',
 publicationUI:{{createController:()=>{{publicationControllers++;return {{open:async()=>{{}}}};}}}},publicationRenderer:{{}},
 transport:{{probe:async()=>({{}}),probePublication,readSnapshot:async()=>({{}})}},transportModule:{{messageText:()=>''}},
}};}}
(async()=>{{
 const absent=browser.createBridge(options(async()=>false));
 assert.equal(await absent.activate(),true);
 assert.equal(absent.publicationAvailability(),'absent');
 await assert.rejects(()=>absent.publishSource({{sourceType:'manual'}}),/unavailable/);
 const failed=browser.createBridge(options(async()=>{{throw new Error('probe failed');}}));
 assert.equal(await failed.activate(),true);
 assert.equal(failed.publicationAvailability(),'error');
 await assert.rejects(()=>failed.publishSource({{sourceType:'manual'}}),/probe failed/);
 assert.equal(publicationControllers,0);
 console.log(JSON.stringify({{absent:absent.publicationAvailability(),failed:failed.publicationAvailability(),publicationControllers}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "absent": "absent",
        "failed": "error",
        "publicationControllers": 0,
    }


def test_p45_target_result_link_opens_exact_space_and_highlights_exact_item() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const space='11111111-1111-4111-8111-111111111111';
const item='22222222-2222-4222-8222-222222222222';
const rows=[];const events=[];let focused=false;let scrolled=false;
function node(){{return {{className:'',dataset:{{}},textContent:'',classList:{{add:value=>{{if(value==='canonical-publication-target')rows.at(-1).highlighted=true;}}}},focus:()=>{{focused=true;}},scrollIntoView:()=>{{scrolled=true;}}}};}}
const documentRef={{createElement:node}};
const managementRoot={{ownerDocument:documentRef,replaceChildren:()=>{{rows.length=0;}},appendChild:value=>rows.push(value),querySelector:selector=>rows.find(value=>value.dataset.canonicalItemId===item)||null}};
const snapshot={{scope_epoch:1,selected_space_id:space,spaces:[{{id:space,kind:'personal',name:'Mine',org_path:[],capabilities:['read'],selected:true}}],conversation_badge:null}};
const transport={{probe:async()=>({{}}),probePublication:async()=>false,readSnapshot:async request=>{{assert.equal(request.selectedSpaceId===null||request.selectedSpaceId===space,true);return snapshot;}},management:async(_space,surface)=>surface==='list'?{{items:[{{id:item,title:'Published item'}}]}}:surface==='index_log'?{{status:'ready',built_generation:1,desired_generation:1}}:{{items:[]}},createConversation:async()=>{{throw new Error('unused');}}}};
const state={{session_id:null,messages:[],sessionCache:new Map()}};const chat={{renderMessages:()=>{{}},cloneMessages:value=>value}};
(async()=>{{
 const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,managementRoot,operationIdFactory:()=> '33333333-3333-4333-8333-333333333333',dispatchEvent:(name,detail)=>events.push([name,detail])}});
 assert.equal(await bridge.activate(),true);
 assert.equal(await bridge.openPublishedTarget({{spaceId:space,itemId:item}}),true);
 assert.equal(bridge.state().mode,scope.MANAGEMENT_MODE);
 assert.equal(bridge.state().selectedSpaceId,space);
 assert.equal(rows.find(value=>value.dataset.canonicalItemId===item).highlighted,true);
 assert.equal(focused,true);assert.equal(scrolled,true);
 assert.deepEqual(events.at(-1),['knowledge:publication-target',{{itemId:item,spaceId:space}}]);
 console.log(JSON.stringify({{event:events.at(-1)[0],focused,scrolled,selected:bridge.state().selectedSpaceId}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "event": "knowledge:publication-target",
        "focused": True,
        "scrolled": True,
        "selected": "11111111-1111-4111-8111-111111111111",
    }


def test_p54_transport_exposes_item_revision_and_compare_reads_with_bounded_queries() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const transportModule=require({json.dumps(str(_TRANSPORT))});
const space='11111111-1111-4111-8111-111111111111';
const item='22222222-2222-4222-8222-222222222222';
const older='33333333-3333-4333-8333-333333333333';
const newer='44444444-4444-4444-8444-444444444444';
const request='55555555-5555-4555-8555-555555555555';
const calls=[];
const responses=[
 {{request_id:request,item:{{id:item,space_id:space,source_space_id:space,selected_view_space_id:space,inherited:false,origin:'native'}},source_space_id:space,selected_view_space_id:space,inherited:false,origin:'native',references:[],timeline:{{content_age_seconds:4}},lifecycle:{{deactivation:null}},capabilities:['read'],markdown:'# body',lease:null}},
 {{request_id:request,items:[{{id:newer,item_id:item}},{{id:older,item_id:item}}],next_cursor:null,has_more:false}},
 {{request_id:request,revision:{{id:older,item_id:item,markdown:'# old'}}}},
 {{request_id:request,from_revision_id:older,to_revision_id:newer,diff_format:'unified-v1',hunks:[],diff_sha256:'a'.repeat(64)}},
];
const fetchImpl=async(url,options)=>{{calls.push([url,options.method]);return new Response(JSON.stringify(responses.shift()),{{status:200,headers:{{'Content-Type':'application/json'}}}});}};
(async()=>{{
 const transport=transportModule.createTransport({{fetchImpl,operationIdFactory:()=>request}});
 assert.equal((await transport.getItem(space,item)).item.id,item);
 assert.equal((await transport.listRevisions(space,item,{{limit:25}})).items.length,2);
 assert.equal((await transport.getRevision(space,item,older)).revision.id,older);
 assert.equal((await transport.compareRevisions(space,item,older,newer)).diff_format,'unified-v1');
 assert.deepEqual(calls.map(row=>row[0]),[
  `/api/knowledge/v1/spaces/${{space}}/items/${{item}}`,
  `/api/knowledge/v1/spaces/${{space}}/items/${{item}}/revisions?limit=25`,
  `/api/knowledge/v1/spaces/${{space}}/items/${{item}}/revisions/${{older}}`,
  `/api/knowledge/v1/spaces/${{space}}/items/${{item}}/compare?from=${{older}}&to=${{newer}}`,
 ]);
 await assert.rejects(()=>transport.listRevisions(space,item,{{limit:101}}),/limit is invalid/);
 await assert.rejects(()=>transport.getItem(space,'../escape'),/item_id/);
 const wrong=transportModule.createTransport({{fetchImpl:async()=>new Response(JSON.stringify({{request_id:request,item:{{id:'66666666-6666-4666-8666-666666666666',space_id:space}},timeline:{{}},lifecycle:{{}},capabilities:['read']}}),{{status:200,headers:{{'Content-Type':'application/json'}}}}),operationIdFactory:()=>request}});
 await assert.rejects(()=>wrong.getItem(space,item),/Item capabilities are invalid/);
 console.log(JSON.stringify({{calls:calls.length,remaining:responses.length}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {"calls": 4, "remaining": 0}


def test_p54_browser_renders_ages_inactive_reason_editor_history_and_structured_diff() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const space='11111111-1111-4111-8111-111111111111';
const item='22222222-2222-4222-8222-222222222222';
const older='33333333-3333-4333-8333-333333333333';
const newer='44444444-4444-4444-8444-444444444444';
const rendered=[];const events=[];let revisionReads=0;let comparisons=0;
function node(){{return {{className:'',dataset:{{}},textContent:'',listeners:{{}},addEventListener(name,handler){{this.listeners[name]=handler;}}}};}}
const documentRef={{createElement:node}};
const managementRoot={{ownerDocument:documentRef,replaceChildren:()=>{{rendered.length=0;}},appendChild:value=>rendered.push(value)}};
const snapshot={{scope_epoch:1,selected_space_id:space,spaces:[{{id:space,kind:'personal',name:'Mine',org_path:[],capabilities:['read'],selected:true}}],conversation_badge:null}};
    const detail={{item:{{id:item,space_id:space,source_space_id:space,selected_view_space_id:space,inherited:false,origin:'native',title:'Inactive guide',state:'inactive',curation_state:'published'}},source_space_id:space,selected_view_space_id:space,inherited:false,origin:'native',references:[],capabilities:['read','restore'],markdown:'# retired',lease:null,timeline:{{content_age_seconds:172800,review_age_seconds:3600,last_updated_at:'2026-08-14T00:00:00.000Z'}},lifecycle:{{edge_counts:{{total:2}},reference_counts:{{total:3,conversation_pins:1}},deactivation:{{inactive_duration_seconds:600,deactivated_by:{{display_name:'Editor'}},deactivated_at:'2026-08-13T00:00:00.000Z',reason:'Superseded guidance',purge_eligible_at:'2026-11-11T00:00:00.000Z'}}}}}};
const history={{items:[{{id:newer,item_id:item,revision_no:2,created_by_display:'Reviewer',created_at:'2026-08-12T00:00:00.000Z',reason:'Clarified safety'}},{{id:older,item_id:item,revision_no:1,created_by_display:'Editor',created_at:'2026-08-01T00:00:00.000Z',reason:'Initial publication'}}]}};
const transport={{probe:async()=>({{}}),probePublication:async()=>false,readSnapshot:async()=>snapshot,management:async()=>({{items:[]}}),getItem:async()=>detail,listRevisions:async()=>history,getRevision:async()=>{{revisionReads++;return {{revision:{{markdown:'# historical'}}}};}},compareRevisions:async()=>{{comparisons++;return {{hunks:[{{lines:['-old\\n','+new\\n']}}]}};}},createConversation:async()=>{{throw new Error('unused');}}}};
const state={{session_id:null,messages:[],sessionCache:new Map()}};const chat={{renderMessages:()=>{{}},cloneMessages:value=>value}};
(async()=>{{
 const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,managementRoot,operationIdFactory:()=> '55555555-5555-4555-8555-555555555555',dispatchEvent:(name,detail)=>events.push([name,detail]),onError:error=>{{throw error;}}}});
 await bridge.activate();
 await bridge.openItem(space,item);
 const initial=rendered.map(row=>row.textContent).join(' | ');
 assert.match(initial,/Content age 2d/);assert.match(initial,/review age 1h/);
 assert.match(initial,/Inactive 10m/);assert.match(initial,/Superseded guidance/);
 assert.match(initial,/2 edges/);assert.match(initial,/3 references/);assert.match(initial,/1 conversation pins/);
 assert.match(initial,/Revision 2 · Reviewer/);assert.match(initial,/Clarified safety/);
 const revisionRow=rendered.find(row=>row.dataset.canonicalRevisionId===newer);
 await revisionRow.listeners.click();
 const compareRow=rendered.find(row=>row.className.includes('canonical-revision-compare'));
 await compareRow.listeners.click();
 const finalText=rendered.map(row=>row.textContent).join(' | ');
 assert.match(finalText,/# historical/);assert.match(finalText,/-old/);assert.match(finalText,/[+]new/);
 assert.deepEqual(events.at(-1),['knowledge:item-opened',{{itemId:item,spaceId:space}}]);
 console.log(JSON.stringify({{comparisons,revisionReads,rows:rendered.length}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result["comparisons"] == 1
    assert result["revisionReads"] == 1
    assert result["rows"] >= 12


def test_wiki_item_opens_in_modal_without_replacing_the_management_list() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const space='11111111-1111-4111-8111-111111111111';
const item='22222222-2222-4222-8222-222222222222';
function node(tag='div') {{
  return {{tagName:tag,className:'',dataset:{{}},textContent:'',children:[],listeners:{{}},
    appendChild(value){{this.children.push(value);}},
    addEventListener(name,handler){{this.listeners[name]=handler;}}
  }};
}}
const documentRef={{createElement:node}};
const existing=node(); existing.textContent='management-list-sentinel';
const sidebar=[existing]; let replacements=0;
const managementRoot={{
  ownerDocument:documentRef,
  replaceChildren:()=>{{replacements++;sidebar.length=0;}},
  appendChild:value=>sidebar.push(value),
}};
let opened=null;
const modal={{open:config=>{{opened=config;}}}};
const snapshot={{scope_epoch:1,selected_space_id:space,spaces:[{{id:space,kind:'personal',name:'Mine',org_path:[],capabilities:['read'],selected:true}}],conversation_badge:null}};
const detail={{
  item:{{id:item,title:'Popup document',state:'active',curation_state:'published'}},
  source_space_id:space,selected_view_space_id:space,origin:'native',references:[],capabilities:['read'],
  markdown:'# Canonical body\\nModal content',lease:null,
  timeline:{{content_age_seconds:10,review_age_seconds:20,last_updated_at:'2026-08-27T00:00:00.000Z'}},
  lifecycle:{{edge_counts:{{total:0}},reference_counts:{{total:0,conversation_pins:0}},deactivation:null}},
}};
const history={{items:[{{id:'33333333-3333-4333-8333-333333333333',item_id:item,revision_no:1,created_by_display:'Writer',created_at:'2026-08-27T00:00:00.000Z',reason:'Uploaded'}}]}};
const transport={{probe:async()=>({{}}),probePublication:async()=>false,readSnapshot:async()=>snapshot,createConversation:async()=>{{throw new Error('unused');}},getItem:async()=>detail,listRevisions:async()=>history}};
const state={{session_id:null,messages:[],sessionCache:new Map()}};
const chat={{renderMessages:()=>{{}},cloneMessages:value=>value}};
function flattenText(value) {{ return [value.textContent,...(value.children||[]).flatMap(flattenText)].join(' | '); }}
(async()=>{{
  const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,managementRoot,modal,operationIdFactory:()=> '44444444-4444-4444-8444-444444444444'}});
  await bridge.activate();
  await bridge.openItem(space,item);
  assert.equal(replacements,0);
  assert.equal(sidebar.length,1);
  assert.equal(sidebar[0].textContent,'management-list-sentinel');
  assert.equal(opened.title,'Popup document');
  assert.equal(opened.body.className,'canonical-item-modal');
  assert.equal(opened.closeable,true);
  assert.match(flattenText(opened.body),/# Canonical body/);
  assert.match(flattenText(opened.body),/Revision 1 · Writer/);
  console.log(JSON.stringify({{replacements,sidebar:sidebar.length,title:opened.title,modalClass:opened.body.className}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "replacements": 0,
        "sidebar": 1,
        "title": "Popup document",
        "modalClass": "canonical-item-modal",
    }


@pytest.mark.scenario("CONV-012")
def test_p37_browser_retry_reuses_turn_operation_until_terminal() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const transportModule=require({json.dumps(str(_TRANSPORT))});
const space='11111111-1111-4111-8111-111111111111';
const conversation='22222222-2222-4222-8222-222222222222';
const operations=['33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444'];
let operationIndex=0; let attempts=0; const observed=[];
const snapshot={{scope_epoch:1,selected_space_id:space,spaces:[{{id:space,kind:'personal',name:'Mine',org_path:[],capabilities:['read'],selected:true}}],conversation_badge:{{access_status:'accessible',accessible:true,conversation_id:conversation,conversation_row_version:1,conversation_status:'active',conversation_title:'Chat',immutable:true,org_path:[],space_id:space,space_kind:'personal',space_name:'Mine'}}}};
const transport={{
 probe:async()=>({{}}),readSnapshot:async()=>snapshot,createConversation:async()=>{{throw new Error('must not create');}},management:async()=>({{items:[]}}),
 streamTurn:async request=>{{observed.push(request.operationId);attempts++;if(attempts===1){{const error=new Error('transient');error.terminal=false;throw error;}}return {{assistantText:'ok',terminalSequence:2}};}},
 getConversation:async()=>({{conversation:{{id:conversation,title:'Chat',row_version:1}},messages:[],pins:[],context_bindings:[],summary_status:'absent'}}),listConversations:async()=>({{items:[]}}),updateConversation:async()=>({{}}),compactConversation:async()=>({{}}),
}};
const state={{session_id:conversation,messages:[],sessionCache:new Map()}};
const chat={{cloneMessages:value=>value,renderMessages:()=>{{}}}};
(async()=>{{
 const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,operationIdFactory:()=>operations[operationIndex++],transportModule}});
 await bridge.activate();
 await assert.rejects(()=>bridge.chatStreamResponse({{conversationId:conversation,message:'same'}}),/transient/);
 const response=await bridge.chatStreamResponse({{conversationId:conversation,message:'same'}});
 assert.match(await response.text(),/"delta":"ok"/);
 await bridge.chatStreamResponse({{conversationId:conversation,message:'same'}});
 assert.deepEqual(observed,[operations[0],operations[0],operations[1]]);
 console.log(JSON.stringify({{attempts,observed}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "attempts": 3,
        "observed": [
            "33333333-3333-4333-8333-333333333333",
            "33333333-3333-4333-8333-333333333333",
            "44444444-4444-4444-8444-444444444444",
        ],
    }


@pytest.mark.scenario("CONV-001")
def test_p37_browser_lazy_create_reuses_operation_after_unknown_outcome() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const transportModule=require({json.dumps(str(_TRANSPORT))});
const space='11111111-1111-4111-8111-111111111111';
const conversation='22222222-2222-4222-8222-222222222222';
const operation='33333333-3333-4333-8333-333333333333';
let attempts=0;const observed=[];
function snapshot(id=null){{return {{scope_epoch:1,selected_space_id:space,spaces:[{{id:space,kind:'personal',name:'Mine',org_path:[],capabilities:['read'],selected:true}}],conversation_badge:id?{{access_status:'accessible',accessible:true,conversation_id:conversation,conversation_row_version:1,conversation_status:'active',conversation_title:'Chat',immutable:true,org_path:[],space_id:space,space_kind:'personal',space_name:'Mine'}}:null}};}}
const transport={{probe:async()=>({{}}),readSnapshot:async request=>snapshot(request.conversationId),createConversation:async request=>{{observed.push(request.operationId);attempts++;if(attempts===1)throw new Error('unknown outcome');return {{id:conversation,space_id:space}};}},management:async()=>({{items:[]}}),streamTurn:async()=>({{assistantText:'',terminalSequence:2}}),getConversation:async()=>({{conversation:{{id:conversation,title:'Chat',row_version:1}},messages:[],pins:[],context_bindings:[],summary_status:'absent'}}),listConversations:async()=>({{items:[]}}),updateConversation:async()=>({{}}),compactConversation:async()=>({{}})}};
const state={{session_id:null,messages:[],sessionCache:new Map()}};const chat={{cloneMessages:v=>v,renderMessages:()=>{{}}}};
(async()=>{{const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,operationIdFactory:()=>operation,transportModule}});await bridge.activate();await assert.rejects(()=>bridge.ensureConversation(),/unknown outcome/);assert.equal(await bridge.ensureConversation(),conversation);assert.deepEqual(observed,[operation,operation]);console.log(JSON.stringify({{attempts,observed}}));}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "attempts": 2,
        "observed": [
            "33333333-3333-4333-8333-333333333333",
            "33333333-3333-4333-8333-333333333333",
        ],
    }


@pytest.mark.scenario("WIKI-021")
def test_browser_fresh_conversation_creates_a_new_exact_wiki_bound_session() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const transportModule=require({json.dumps(str(_TRANSPORT))});
const space='11111111-1111-4111-8111-111111111111';
const oldConversation='22222222-2222-4222-8222-222222222222';
const newConversation='33333333-3333-4333-8333-333333333333';
const operation='44444444-4444-4444-8444-444444444444';
const created=[];
function snapshot(conversationId){{return {{scope_epoch:1,selected_space_id:space,spaces:[{{id:space,kind:'personal',name:'Selected Wiki',org_path:[],capabilities:['read'],selected:true}}],conversation_badge:conversationId?{{access_status:'accessible',accessible:true,conversation_id:conversationId,conversation_row_version:1,conversation_status:'active',conversation_title:'Chat',history_only:false,immutable:true,org_path:[],space_id:space,space_kind:'personal',space_name:'Selected Wiki',wiki_lifecycle_generation:1}}:null}};}}
const transport={{probe:async()=>({{}}),readSnapshot:async request=>snapshot(request.conversationId),createConversation:async request=>{{created.push(request);return {{id:newConversation,space_id:request.spaceId}};}},management:async()=>({{items:[]}}),streamTurn:async()=>({{assistantText:'',terminalSequence:2}}),getConversation:async()=>({{conversation:{{id:newConversation,title:'Chat',row_version:1}},messages:[],pins:[],context_bindings:[],summary_status:'absent'}}),listConversations:async()=>({{items:[]}}),updateConversation:async()=>({{}}),compactConversation:async()=>({{}})}};
const state={{session_id:oldConversation,messages:[{{role:'assistant',content:'old'}}],compactSummary:'old',compactedMessageCount:1,lastContextStatus:{{status:'old'}},sessionCache:new Map()}};let renders=0;
const chat={{cloneMessages:value=>value,renderMessages:()=>{{renders++;}}}};
(async()=>{{
 const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,operationIdFactory:()=>operation,transportModule}});
 await bridge.activate();
 const result=await bridge.startFreshConversation();
 assert.equal(result,newConversation);
 assert.deepEqual(created,[{{operationId:operation,spaceId:space}}]);
 assert.equal(state.session_id,newConversation);
 assert.equal(state.messages.length,0);
 assert.equal(state.compactSummary,'');
 assert.equal(bridge.state().conversationBadge.conversation_id,newConversation);
 console.log(JSON.stringify({{created:created.length,conversation:state.session_id,renders}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "created": 1,
        "conversation": "33333333-3333-4333-8333-333333333333",
        "renders": 1,
    }


@pytest.mark.scenario("CONV-003")
def test_p37_browser_bridge_binds_scope_and_lazy_conversation_without_actor_override() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const transportModule=require({json.dumps(str(_TRANSPORT))});
const personal='11111111-1111-4111-8111-111111111111';
const conversation='22222222-2222-4222-8222-222222222222';
const op='33333333-3333-4333-8333-333333333333';
let created=0;
const managementCalls=[];
function snapshot(conversationId=null) {{return {{scope_epoch:1,selected_space_id:personal,spaces:[{{id:personal,kind:'personal',name:'Mine',org_path:[],capabilities:['read'],selected:true}}],conversation_badge:conversationId?{{access_status:'accessible',accessible:true,conversation_id:conversation,conversation_row_version:1,conversation_status:'active',conversation_title:'New',immutable:true,org_path:[],space_id:personal,space_kind:'personal',space_name:'Mine'}}:null}};}}
const transport={{
  probe:async()=>({{}}),readSnapshot:async({{conversationId}})=>snapshot(conversationId),
  createConversation:async()=>{{created++;return {{id:conversation,space_id:personal}};}},
  streamTurn:async()=>({{assistantText:'answer',terminalSequence:2}}),
  management:async(spaceId,surface,options)=>{{managementCalls.push([spaceId,surface,options||null]);return surface==='index_log'?{{status:'ready',built_generation:1,desired_generation:1}}:{{items:[]}};}},
  getConversation:async()=>({{conversation:{{id:conversation,title:'New',row_version:1}},messages:[{{role:'assistant',content:{{content:{{text:'answer'}}}}}}],pins:[],context_bindings:[],summary_status:'absent'}}),
  listConversations:async()=>({{items:[]}}),updateConversation:async()=>({{}}),compactConversation:async()=>({{}}),
}};
const state={{session_id:null,messages:[],compactSummary:'',compactedMessageCount:0,lastContextStatus:null,sessionCache:new Map()}};
const renders=[]; const chat={{cloneMessages:x=>JSON.parse(JSON.stringify(x)),renderMessages:x=>renders.push(x)}};
const rendered=[];
const documentRef={{createElement:()=>({{className:'',dataset:{{}},textContent:''}})}};
const managementRoot={{ownerDocument:documentRef,replaceChildren:()=>{{rendered.length=0;}},appendChild:value=>rendered.push(value)}};
(async()=>{{
 const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,operationIdFactory:()=>op,transportModule,managementRoot}});
 assert.equal(await bridge.activate(),true);
 assert.equal(bridge.state().conversationBadge,null);
 assert.deepEqual(await Promise.all([bridge.ensureConversation(),bridge.ensureConversation()]),[conversation,conversation]);
 assert.equal(await bridge.ensureConversation(),conversation);
 assert.equal(created,1);
 assert.equal(state.session_id,conversation);
 state.session_id='s_stale_legacy_session';
 assert.equal(await bridge.ensureConversation(),conversation);
 assert.equal(state.session_id,conversation);
 assert.equal(bridge.state().conversationBadge.space_id,personal);
 const response=await bridge.chatStreamResponse({{conversationId:conversation,message:'hello'}});
 const text=await response.text();
 assert.match(text,/"delta":"answer"/);
 assert.ok(!text.includes('user_id'));
 await bridge.renderManagement('needle');
 assert.equal(bridge.state().mode,scope.MANAGEMENT_MODE);
 assert.deepEqual(managementCalls.map(value=>value[1]),['list','search','graph','index_log','activity','candidates']);
 assert.ok(rendered.length>=2);
 await bridge.enterChat();
 assert.equal(bridge.state().mode,scope.CHAT_MODE);
 console.log(JSON.stringify({{created,managementCalls:managementCalls.length,renders:renders.length,space:bridge.state().conversationBadge.space_id}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "created": 1,
        "managementCalls": 6,
        "renders": 0,
        "space": "11111111-1111-4111-8111-111111111111",
    }


def test_p37_index_loads_canonical_transport_before_scope_and_bootstrap_last() -> None:
    html = (_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    app = (_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    sessions = (_ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
    browser = (_ROOT / "static" / "knowledge_core_browser.js").read_text(encoding="utf-8")
    chat = (_ROOT / "static" / "chat.js").read_text(encoding="utf-8")
    assert '<div class="app-root right-collapsed">' in html
    assert "SidebarToggle.applyRight(true, false);" in app
    assert "await window.KnowledgeCoreBrowser.startFreshConversation();" in app
    assert "CANONICAL_SESSION_ID_RE" in sessions
    assert "${turnCount}턴${turnState}" in sessions
    assert "message_count: item.turn_count" in browser
    assert 'currentTab === "sessions"' in chat
    assert "await Sessions.render();" in chat
    assert html.index("static/knowledge_core_transport.js") < html.index(
        "static/knowledge_scope.js"
    )
    assert html.index("static/knowledge_scope.js") < html.index("static/app.js")
    assert html.index("static/sessions.js") < html.index(
        "static/knowledge_core_browser.js"
    )


@pytest.mark.scenario("HSPACE-016", "HSPACE-018", "AUTOEDGE-018")
def test_higher_distribution_and_semantic_correction_transport_is_exactly_bound() -> None:
    result = _run_node(
        f"""
const assert = require('node:assert/strict');
const api = require({json.dumps(str(_TRANSPORT))});
const space = '11111111-1111-4111-8111-111111111111';
const item = '22222222-2222-4222-8222-222222222222';
const revision = '33333333-3333-4333-8333-333333333333';
const targetId = '44444444-4444-4444-8444-444444444444';
const grant = '55555555-5555-4555-8555-555555555555';
const edge = '66666666-6666-4666-8666-666666666666';
const suppression = '77777777-7777-4777-8777-777777777777';
const request = '88888888-8888-4888-8888-888888888888';
const operations = [
  '99999999-9999-4999-8999-999999999991',
  '99999999-9999-4999-8999-999999999992',
  '99999999-9999-4999-8999-999999999993',
  '99999999-9999-4999-8999-999999999994',
];
const target = {{
  kind:'part', name:'Part A', org_path:['Responsibility','Team A','Part A'],
  org_unit_id:targetId, target_scope_id:targetId, target_scope_kind:'org_unit',
}};
const responses = [
  {{items:[target],request_id:request,source_space_id:space}},
  {{grant_id:grant,operation_id:operations[0],request_id:request,revoked:false,row_version:1,source_space_id:space,status:'created'}},
  {{grant_id:grant,operation_id:operations[1],request_id:request,revoked:true,row_version:2,source_space_id:space,status:'revoked'}},
  {{edge_id:edge,edge_kind:'auto',edge_status:'suppressed',operation_id:operations[2],reevaluation_queued:false,request_id:request,row_version:2,scope_space_id:space,suppression_id:suppression,suppression_status:'active'}},
  {{edge_id:edge,edge_kind:'auto',edge_status:'needs_revalidation',operation_id:operations[3],reevaluation_queued:true,request_id:request,row_version:3,scope_space_id:space,suppression_id:suppression,suppression_status:'lifted'}},
];
const calls = [];
const fetchImpl = async (url, init={{}}) => {{
  calls.push({{url,method:init.method||'GET',body:init.body ? JSON.parse(init.body) : null}});
  return new Response(JSON.stringify(responses.shift()), {{status:200,headers:{{'Content-Type':'application/json'}}}});
}};
(async()=>{{
  const transport = api.createTransport({{fetchImpl,operationIdFactory:()=>operations[0]}});
  const targets = await transport.listDistributionTargets({{spaceId:space}});
  assert.deepEqual(targets.items[0], target);
  await transport.createDistribution({{spaceId:space,itemId:item,revisionId:revision,target,reason:'reviewed share',operationId:operations[0]}});
  await transport.revokeDistribution({{spaceId:space,grantId:grant,expectedRowVersion:1,reason:'scope ended',operationId:operations[1]}});
  await transport.suppressSemanticEdge({{spaceId:space,edgeId:edge,expectedRowVersion:1,reason:'false relation',operationId:operations[2]}});
  await transport.liftSemanticEdgeSuppression({{spaceId:space,edgeId:edge,expectedRowVersion:2,reason:'evidence updated',operationId:operations[3]}});
  assert.deepEqual(calls.map(call=>call.url), [
    `/api/knowledge/v2/spaces/${{space}}/distribution-targets`,
    `/api/knowledge/v2/spaces/${{space}}/distributions`,
    `/api/knowledge/v2/spaces/${{space}}/distributions/${{grant}}/revoke`,
    `/api/knowledge/v2/spaces/${{space}}/edges/${{edge}}/suppress`,
    `/api/knowledge/v2/spaces/${{space}}/edges/${{edge}}/suppression/lift`,
  ]);
  assert.deepEqual(calls[1].body, {{operation_id:operations[0],reason:'reviewed share',source_item_id:item,source_revision_id:revision,target_org_unit_id:targetId,target_scope_id:targetId,target_scope_kind:'org_unit'}});
  assert.equal(calls[2].body.break_glass,false);
  assert.equal(calls[3].body.break_glass,false);
  assert.equal(calls[4].body.expected_row_version,2);
  console.log(JSON.stringify({{calls:calls.length,remaining:responses.length}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {"calls": 5, "remaining": 0}


@pytest.mark.scenario(
    "HSPACE-016",
    "HSPACE-017",
    "AGRAPH-015",
    "AGRAPH-016",
    "AGRAPH-017",
    "AUTOEDGE-017",
    "AUTOEDGE-018",
    "REF-007",
    "REF-008",
)
def test_higher_browser_renders_graph_provenance_references_and_distribution_controls() -> None:
    result = _run_node(
        f"""
const assert = require('node:assert/strict');
const browser = require({json.dumps(str(_BROWSER))});
const scope = require({json.dumps(str(_SCOPE))});
const space = '11111111-1111-4111-8111-111111111111';
const item = '22222222-2222-4222-8222-222222222222';
const targetItem = '33333333-3333-4333-8333-333333333333';
const revision = '44444444-4444-4444-8444-444444444444';
const targetRevision = '55555555-5555-4555-8555-555555555555';
const edge = '66666666-6666-4666-8666-666666666666';
const assignment = '77777777-7777-4777-8777-777777777777';
const targetPart = '88888888-8888-4888-8888-888888888888';
const grant = '99999999-9999-4999-8999-999999999999';
const partSpaceA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const partSpaceB = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const children = [];
let modalBody = null;
const modal = {{open: options => {{ modalBody = options.body; }}}};
function makeNode(tag) {{
  return {{
    tag, className:'', dataset:{{}}, textContent:'', value:'', listeners:{{}}, children:[],
    addEventListener(name, callback) {{ this.listeners[name] = callback; }},
    appendChild(value) {{ this.children.push(value); if (!this.value && value.value) this.value=value.value; return value; }},
    append(...values) {{ values.forEach(value => this.appendChild(value)); }},
    replaceChildren(...values) {{ this.children=[...values]; this.textContent=''; }},
    focus() {{}}, scrollIntoView() {{}}, setSelectionRange() {{}},
  }};
}}
const documentRef = {{createElement:makeNode}};
const managementRoot = {{
  ownerDocument:documentRef,
  appendChild(value) {{ children.push(value); return value; }},
  replaceChildren(...values) {{ children.length=0; children.push(...values); }},
  querySelector() {{ return null; }},
}};
const authority = {{
  capabilities:['read','edit','publish','distribution_grant','distribution_revoke','aggregate_edge_correct'],
  native_space_id:space,
  publication_target:{{capability:'publish',kind:'team',role_assignment_generation:2,role_assignment_id:assignment,space_id:space}},
  role_assignment_generation:2,role_assignment_id:assignment,
}};
const snapshot = {{conversation_badge:null,scope_epoch:3,selected_space_id:space,spaces:[
  {{id:'cccccccc-cccc-4ccc-8ccc-cccccccccccc',kind:'personal',name:'Mine',org_path:[],capabilities:['read'],native_authority:null,selected:false}},
  {{id:space,kind:'team',name:'Team A',org_path:['Responsibility','Team A'],capabilities:['read','edit'],native_authority:authority,selected:true}},
]}};
const graph = {{generation:9,space_id:space,nodes:[
  {{id:item,title:'Team guide',current_revision_id:revision,row_version:3,state:'active',source_space_id:partSpaceA,selected_view_space_id:space,inherited:true,origin:'inherited',overlay_grant_id:null,capabilities:['read']}},
  {{id:targetItem,title:'Other guide',current_revision_id:targetRevision,row_version:2,state:'active',source_space_id:partSpaceB,selected_view_space_id:space,inherited:true,origin:'inherited',overlay_grant_id:null,capabilities:['read']}},
],edges:[{{
  id:edge,source_item_id:item,target_item_id:targetItem,source_space_id:partSpaceA,target_space_id:partSpaceB,
  selected_view_space_id:space,home_space_id:space,inherited:false,origin:'native',capabilities:['read','aggregate_edge_correct'],
  direction:'undirected',edge_scope:'aggregate',edge_status:'needs_revalidation',edge_type:'related',row_version:4,
  confidence_ppm:875000,detector_generation:12,model_provider:'company',model_name:'reranker-v2',
  prompt_schema_version:'semantic-v1',scoring_version:'score-v2',semantic_evidence_sha256:'a'.repeat(64),
  source_evidence_location:'lines:1-3',source_evidence_revision_id:revision,source_evidence_span_sha256:'b'.repeat(64),
  target_evidence_location:'lines:2-4',target_evidence_revision_id:targetRevision,target_evidence_span_sha256:'c'.repeat(64),
}}]}};
const detail = {{
  item:{{id:item,title:'Team guide',state:'active',curation_state:'published',current_revision_id:revision,row_version:3}},
  source_space_id:space,selected_view_space_id:space,inherited:false,origin:'native',
  capabilities:['read','edit','distribution_grant','distribution_revoke'],markdown:'# Team guide',lease:null,
  timeline:{{content_age_seconds:60,review_age_seconds:120,last_updated_at:'2026-08-25T00:00:00.000Z'}},
  lifecycle:{{edge_counts:{{total:1}},reference_counts:{{total:2,conversation_pins:0}},deactivation:null}},
  references:[
    {{id:'dddddddd-dddd-4ddd-8ddd-dddddddddddd',ordinal:1,syntax_version:'item-ref-v1',availability_status:'available',unavailable_reason:null,target:{{item_id:targetItem,observed_revision_id:targetRevision,current_revision_id:revision,title:'Referenced guide',source_space_id:partSpaceA,origin:'inherited',inherited:true,newer_revision_available:true}}}},
    {{id:'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',ordinal:2,syntax_version:'item-ref-v1',availability_status:'unavailable',unavailable_reason:'target_unavailable',target:null}},
  ],
}};
const history = {{items:[{{id:revision,item_id:item,revision_no:2,created_by_display:'Leader',created_at:'2026-08-25T00:00:00.000Z',reason:'update'}}]}};
let suppressCalls = 0;
let liftCalls = 0;
const transport = {{
  probe:async()=>true, probePublication:async()=>false, readSnapshot:async()=>snapshot,
  createConversation:async()=>{{throw new Error('unused');}},
  management:async(_space,surface)=>({{list:{{items:[]}},graph,index_log:{{status:'ready',built_generation:9,desired_generation:9}},activity:{{items:[]}},candidates:{{items:[]}},pins:{{items:[]}},search:{{items:[{{item_id:targetItem,title:'Referenced guide',origin:'inherited',source_space_id:partSpaceA}}]}}}}[surface] || {{items:[]}}),
  getItem:async()=>detail, listRevisions:async()=>history,
  acquireItemLease:async()=>({{base_revision_id:revision,expires_at:'2026-08-25T01:00:00.000Z',fencing_generation:1,lease_id:assignment}}),
  releaseItemLease:async()=>({{released:true}}),
  listDistributionTargets:async()=>({{items:[{{kind:'part',name:'Part A',org_path:['Responsibility','Team A','Part A'],org_unit_id:targetPart,target_scope_id:targetPart,target_scope_kind:'org_unit'}}]}}),
  listDistributions:async()=>({{items:[{{grant_id:grant,target_scope_kind:'org_unit',target_scope_id:targetPart,source_revision_id:revision,reason:'approved',revoked_at:null,row_version:1}}]}}),
  suppressSemanticEdge:async request=>{{suppressCalls++;assert.equal(request.edgeId,edge);return {{row_version:5,reevaluation_queued:true}};}},
  liftSemanticEdgeSuppression:async request=>{{liftCalls++;assert.equal(request.expectedRowVersion,5);return {{row_version:6,reevaluation_queued:true}};}},
}};
const state={{session_id:null,messages:[],sessionCache:new Map()}};
const chat={{renderMessages:()=>{{}},cloneMessages:value=>value}};
(async()=>{{
  const bridge = browser.createBridge({{transport,scopeUI:scope,state,chat,managementRoot,modal,operationIdFactory:()=>assignment,leaseTokenFactory:()=> 'preview-token',promptText:()=> 'reviewed correction',onError:error=>{{throw error;}}}});
  assert.equal(await bridge.activate(),true);
  await bridge.renderManagement();
  const managementText = children.map(value=>value.textContent).join('|');
  assert.equal(managementText.includes('Native authority'),false);
  assert.equal(managementText.includes('confidence 87.50%'),false);
  await bridge.openGraphDetails();
  let graphChildren = modalBody.children;
  const graphText = graphChildren.map(value=>value.textContent).join('|');
  assert.match(graphText,/Native authority/);
  assert.match(graphText,/Cluster/);
  assert.equal(graphText.includes('confidence 87.50%'),true);
  assert.equal(graphText.includes('company/reranker-v2'),true);
  assert.equal(graphText.includes('needs_revalidation'),true);
  const suppressButton = graphChildren.find(value=>value.className==='canonical-semantic-edge-suppress');
  assert.ok(suppressButton);
  assert.equal(graphChildren.some(value=>value.className==='canonical-graph-edge-evidence' && value.textContent.includes('lines:1-3')),true);
  suppressButton.listeners.click();
  await new Promise(resolve=>setTimeout(resolve,10));
  await bridge.openGraphDetails();
  graphChildren = modalBody.children;
  const liftButton = graphChildren.find(value=>value.className==='canonical-semantic-edge-lift');
  assert.ok(liftButton);
  liftButton.listeners.click();
  await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(suppressCalls,1);
  assert.equal(liftCalls,1);
  await bridge.openItem(space,item);
  let itemChildren = modalBody.children;
  assert.equal(itemChildren.some(value=>value.className.includes('canonical-reference-title')),true);
  assert.equal(itemChildren.some(value=>value.className.includes('canonical-reference-title') && value.textContent==='직접 참조 문서'),true);
  assert.equal(itemChildren.some(value=>value.className.includes('canonical-reference-available') && value.textContent.includes('Referenced guide')),true);
  assert.equal(itemChildren.some(value=>value.className.includes('canonical-reference-unavailable')),true);
  assert.equal(itemChildren.some(value=>value.className.includes('canonical-automatic-relation-title') && value.textContent==='자동 연관 문서'),true);
  assert.equal(itemChildren.some(value=>value.className==='item canonical-automatic-relation' && value.textContent.includes('Other guide') && value.textContent.includes('87.50%')),true);
  assert.equal(itemChildren.some(value=>value.className==='canonical-distribution-target'),true);
  assert.equal(itemChildren.some(value=>value.className==='canonical-distribution-create'),true);
  assert.equal(itemChildren.some(value=>value.className==='canonical-distribution-revoke'),true);
  await bridge.beginEdit(space,item);
  itemChildren = modalBody.children;
  const pickerQuery = itemChildren.find(value=>value.className==='canonical-reference-picker-query');
  const pickerSearch = itemChildren.find(value=>value.className==='canonical-reference-picker-search');
  const draft = itemChildren.find(value=>value.className==='canonical-item-editor');
  assert.ok(pickerQuery);assert.ok(pickerSearch);assert.ok(draft);
  pickerQuery.value='Referenced';
  await pickerSearch.listeners.click();
  const pickerResults = itemChildren.find(value=>value.className==='canonical-reference-picker-results');
  const pickerResult = pickerResults && pickerResults.children.find(value=>value.className==='canonical-reference-picker-result');
  assert.ok(pickerResult);
  pickerResult.listeners.click();
  assert.equal(draft.value.includes(`[[item:${{targetItem}}]]`),true);
  await bridge.destroy();
  console.log(JSON.stringify({{graphChildren:graphText.split('|').length,itemChildren:itemChildren.length,suppressCalls,liftCalls}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result["graphChildren"] >= 6
    assert result["itemChildren"] >= 12
    assert result["suppressCalls"] == 1
    assert result["liftCalls"] == 1


def test_part_graph_permission_uses_capabilities_and_diagnostics_open_only_in_modal() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const space='11111111-1111-4111-8111-111111111111';
const snapshot={{conversation_badge:null,scope_epoch:1,selected_space_id:space,spaces:[
 {{id:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',kind:'personal',name:'Mine',org_path:[],capabilities:['read'],native_authority:null,selected:false}},
 {{id:space,kind:'part',name:'Part A',org_path:['Responsibility','Team A','Part A'],capabilities:['read','publish','edit','local_edge_correct'],native_authority:null,selected:true}},
]}};
function node(){{return {{className:'',dataset:{{}},textContent:'',children:[],appendChild(value){{this.children.push(value);return value;}},append(...values){{values.forEach(value=>this.appendChild(value));}},replaceChildren(...values){{this.children=[...values];}},addEventListener(){{}}}};}}
const documentRef={{createElement:node}};
const rendered=[];
const managementRoot={{ownerDocument:documentRef,appendChild(value){{rendered.push(value);return value;}},replaceChildren(...values){{rendered.length=0;rendered.push(...values);}}}};
let modalBody=null;
const modal={{open:options=>{{modalBody=options.body;}}}};
const graph={{generation:3,space_id:space,nodes:[],edges:[]}};
const transport={{probe:async()=>true,readSnapshot:async()=>snapshot,createConversation:async()=>{{throw new Error('unused');}},management:async(_space,surface)=>({{list:{{items:[]}},graph,index_log:{{status:'ready',built_generation:3,desired_generation:3}},activity:{{items:[]}},candidates:{{items:[]}}}}[surface]||{{items:[]}})}};
const state={{session_id:null,messages:[],sessionCache:new Map()}};
const chat={{renderMessages:()=>{{}},cloneMessages:value=>value}};
(async()=>{{
 const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,managementRoot,modal,operationIdFactory:()=> '22222222-2222-4222-8222-222222222222'}});
 await bridge.activate();
 await bridge.renderManagement();
 const mainText=rendered.map(value=>value.textContent).join('|');
 assert.equal(mainText.includes('foreign-home'),false);
 assert.equal(mainText.includes('Graph · generation'),false);
 await bridge.openGraphDetails();
 const modalText=modalBody.children.map(value=>value.textContent).join('|');
 assert.match(modalText,/현재 공간의 원본 문서를 작성·수정할 수 있습니다/);
 assert.equal(modalText.includes('이 범위는 읽기 전용입니다'),false);
 console.log(JSON.stringify({{mainText,modalText}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert "foreign-home" not in str(result["mainText"])
    assert "작성·수정" in str(result["modalText"])


def test_management_reload_is_parallel_and_discards_an_older_late_response() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const space='11111111-1111-4111-8111-111111111111';
const item1='22222222-2222-4222-8222-222222222222';
const item2='33333333-3333-4333-8333-333333333333';
const snapshot={{conversation_badge:null,scope_epoch:1,selected_space_id:space,spaces:[{{id:space,kind:'personal',name:'Mine',org_path:[],capabilities:['read'],native_authority:null,selected:true}}]}};
function node(){{return {{className:'',dataset:{{}},textContent:'',children:[],appendChild(value){{this.children.push(value);return value;}},replaceChildren(...values){{this.children=[...values];}},addEventListener(){{}}}};}}
const documentRef={{createElement:node}};
const rendered=[];
const managementRoot={{ownerDocument:documentRef,appendChild(value){{rendered.push(value);return value;}},replaceChildren(...values){{rendered.length=0;rendered.push(...values);}}}};
const pending=[];
const transport={{probe:async()=>true,readSnapshot:async()=>snapshot,createConversation:async()=>{{throw new Error('unused');}},getCurationShadowMetrics:async()=>{{throw Object.assign(new Error('optional unavailable'),{{status:404}});}},management:async(_space,surface)=>new Promise(resolve=>pending.push({{surface,resolve}}))}};
const state={{session_id:null,messages:[],sessionCache:new Map()}};
const chat={{renderMessages:()=>{{}},cloneMessages:value=>value}};
const surfaces=['list','graph','index_log','activity','candidates'];
function value(surface,label){{
 if(surface==='list') return {{items:[{{id:label==='first'?item1:item2,title:label,origin:'native',source_space_id:space}}]}};
 if(surface==='graph') return {{generation:1,space_id:space,nodes:[],edges:[]}};
 if(surface==='index_log') return {{status:'ready',built_generation:1,desired_generation:1}};
 return {{items:[]}};
}}
(async()=>{{
 const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,managementRoot,operationIdFactory:()=> '44444444-4444-4444-8444-444444444444'}});
 await bridge.activate();
 const first=bridge.reloadManagementViews({{spaceId:space,surfaces}});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(pending.length,5);
 const second=bridge.reloadManagementViews({{spaceId:space,surfaces}});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(pending.length,10);
 const secondPending=pending.slice(5);
 const secondList=secondPending.find(row=>row.surface==='list');
 secondList.resolve(value('list','second'));
 for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));
 assert.equal(rendered.some(row=>row.textContent.includes('second')),true);
 secondPending.filter(row=>row!==secondList).forEach(row=>row.resolve(value(row.surface,'second')));
 await second;
 assert.equal(rendered.some(row=>row.textContent.includes('second')),true);
 pending.slice(0,5).forEach(row=>row.resolve(value(row.surface,'first')));
 await first;
 assert.equal(rendered.some(row=>row.textContent.includes('second')),true);
 assert.equal(rendered.some(row=>row.textContent.includes('first')),false);
 console.log(JSON.stringify({{startedBeforeResolve:pending.length,rendered:rendered.map(row=>row.textContent)}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result["startedBeforeResolve"] == 10
    assert any("second" in str(value) for value in result["rendered"])


def test_visible_knowledge_panel_refreshes_only_frontend_selectable_scope_kinds() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const ids={{
  defaultWiki:'11111111-1111-4111-8111-111111111111',
  extraWiki:'22222222-2222-4222-8222-222222222222',
  part:'33333333-3333-4333-8333-333333333333',
  team:'44444444-4444-4444-8444-444444444444',
  responsibility:'55555555-5555-4555-8555-555555555555',
}};
const spaces=[
  {{id:ids.defaultWiki,kind:'personal',name:'내 지식',org_path:[],capabilities:['read'],native_authority:null}},
  {{id:ids.extraWiki,kind:'personal',name:'Renamed Lab',org_path:[],capabilities:['read'],native_authority:null}},
  {{id:ids.part,kind:'part',name:'Dynamic Part',org_path:['Dynamic Responsibility','Dynamic Team','Dynamic Part'],capabilities:['read'],native_authority:null}},
  {{id:ids.team,kind:'team',name:'Dynamic Team',org_path:['Dynamic Responsibility','Dynamic Team'],capabilities:['read'],native_authority:null}},
  {{id:ids.responsibility,kind:'responsibility',name:'Dynamic Responsibility',org_path:['Dynamic Responsibility'],capabilities:['read'],native_authority:null}},
];
const conversations=new Map();
let conversationNo=6;
function conversationId(){{const value=(conversationNo++).toString(16);return `${{value.repeat(8)}}-${{value.repeat(4)}}-4${{value.repeat(3)}}-8${{value.repeat(3)}}-${{value.repeat(12)}}`;}}
function snapshot(selected,conversation=null){{
  const bound=conversation&&conversations.get(conversation);
  const boundSpace=spaces.find(value=>value.id===bound);
  return {{
    conversation_badge:boundSpace?{{access_status:'accessible',accessible:true,conversation_id:conversation,conversation_row_version:1,conversation_status:'active',conversation_title:'Scope conversation',history_only:false,immutable:true,org_path:boundSpace.org_path,space_id:boundSpace.id,space_kind:boundSpace.kind,space_name:boundSpace.name,wiki_lifecycle_generation:boundSpace.kind==='personal'?1:null}}:null,
    scope_epoch:1,
    selected_space_id:selected,
    spaces:spaces.map(value=>({{...value,selected:value.id===selected}})),
  }};
}}
function node(){{return {{className:'',dataset:{{}},textContent:'',children:[],listeners:{{}},hidden:false,disabled:false,value:'',appendChild(value){{this.children.push(value);return value;}},append(...values){{values.forEach(value=>this.appendChild(value));}},replaceChildren(...values){{this.children=[...values];}},addEventListener(name,handler){{this.listeners[name]=handler;}}}};}}
const documentRef={{createElement:node}};
const select=node();select.ownerDocument=documentRef;
const scopeRoot=node();
const rendered=[];
const managementRoot={{ownerDocument:documentRef,appendChild(value){{rendered.push(value);return value;}},replaceChildren(...values){{rendered.length=0;rendered.push(...values);}}}};
const personalWikiRoot={{hidden:true}};
const listSpaces=[];
const errors=[];
const transport={{
  probe:async()=>true,
  readSnapshot:async request=>{{
    const bound=request.conversationId&&conversations.get(request.conversationId);
    return snapshot(request.selectedSpaceId||bound||ids.defaultWiki,request.conversationId||null);
  }},
  createConversation:async request=>{{const id=conversationId();conversations.set(id,request.spaceId);return {{id,space_id:request.spaceId}};}},
  getConversation:async()=>({{pins:[]}}),
  management:async(spaceId,surface)=>{{
    if(surface==='list'){{listSpaces.push(spaceId);return {{has_more:false,items:[{{id:spaceId,title:`Document-${{spaceId}}`,origin:'native',source_space_id:spaceId}}],next_cursor:null}};}}
    if(surface==='graph')return {{generation:1,space_id:spaceId,nodes:[],edges:[]}};
    if(surface==='index_log')return {{status:'ready',built_generation:1,desired_generation:1}};
    return {{items:[]}};
  }},
}};
const state={{session_id:null,messages:[],sessionCache:new Map()}};
const chat={{renderMessages:()=>{{}},cloneMessages:value=>value}};
(async()=>{{
  const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,managementRoot,personalWikiRoot,elements:{{root:scopeRoot,select}},operationIdFactory:()=> '99999999-9999-4999-8999-999999999999',onError:error=>errors.push(error)}});
  assert.equal(await bridge.activate(),true);
  bridge.setWikiNavigationVisible(true);
  const disabledOptions=select.children.filter(option=>option.disabled).map(option=>option.value);
  assert.deepEqual(disabledOptions,[ids.team,ids.responsibility]);
  for(const target of [ids.extraWiki,ids.part,ids.defaultWiki]){{
    select.value=target;
    select.listeners.change();
    for(let wait=0;wait<100;wait++){{
      if(bridge.state().selectedSpaceId===target && rendered.some(row=>String(row.textContent).includes(`Document-${{target}}`)))break;
      await new Promise(resolve=>setImmediate(resolve));
    }}
    assert.equal(bridge.state().selectedSpaceId,target);
    assert.equal(rendered.some(row=>String(row.textContent).includes(`Document-${{target}}`)),true);
    assert.equal(rendered.some(row=>String(row.textContent).includes('Document-')&&!String(row.textContent).includes(target)),false);
  }}
  assert.deepEqual(errors,[]);
  assert.deepEqual(listSpaces.slice(-3),[ids.extraWiki,ids.part,ids.defaultWiki]);
  console.log(JSON.stringify({{selected:bridge.state().selectedSpaceId,listSpaces:listSpaces.slice(-3),disabledOptions,errors:errors.length}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "selected": "11111111-1111-4111-8111-111111111111",
        "listSpaces": [
            "22222222-2222-4222-8222-222222222222",
            "33333333-3333-4333-8333-333333333333",
            "11111111-1111-4111-8111-111111111111",
        ],
        "disabledOptions": [
            "44444444-4444-4444-8444-444444444444",
            "55555555-5555-4555-8555-555555555555",
        ],
        "errors": 0,
    }


def test_original_source_download_is_wired_into_transport_and_item_modal() -> None:
    transport = _TRANSPORT.read_text(encoding="utf-8")
    browser = _BROWSER.read_text(encoding="utf-8")
    chat = (_ROOT / "static" / "chat.js").read_text(encoding="utf-8")
    index = _INDEX.read_text(encoding="utf-8")

    assert "async function listItemSourceArtifacts" in transport
    assert "source-artifacts/${artifactId}/download" in transport
    assert "transport.listItemSourceArtifacts(spaceId, itemId)" in browser
    features = (_BROWSER.parent / "workspace_features.js").read_text(encoding="utf-8")
    assert "원본 자료" not in browser
    assert "itemControls(spaceId, item" in browser
    assert "download(result.items[0].download_url)" in features
    assert "assistant-source-download" in chat
    assert "참조 원본 파일 다운로드" in chat
    assert "knowledge_core_transport.js?v=20260901a" in index
    assert "knowledge_core_browser.js?v=20260907a" in index
    assert "workspace_features.js?v=20260907c" in index

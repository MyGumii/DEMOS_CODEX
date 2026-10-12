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
_SCOPE = _ROOT / "static" / "knowledge_scope.js"


def _run_node(source: str) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.fail("P5.5 browser proof requires Node.js.")
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


@pytest.mark.scenario("LEASE-001")
@pytest.mark.scenario("LEASE-006")
@pytest.mark.scenario("LEASE-008")
@pytest.mark.scenario("LEASE-011")
def test_p55_transport_uses_header_only_token_fence_and_exact_body_bindings() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const api=require({json.dumps(str(_TRANSPORT))});
const space='11111111-1111-4111-8111-111111111111';
const item='22222222-2222-4222-8222-222222222222';
const session='33333333-3333-4333-8333-333333333333';
const lease='44444444-4444-4444-8444-444444444444';
const base='55555555-5555-4555-8555-555555555555';
const next='66666666-6666-4666-8666-666666666666';
const request='77777777-7777-4777-8777-777777777777';
const token='A'.repeat(43);const calls=[];let counter=0;
const op=()=>`88888888-8888-4888-8888-${{String(++counter).padStart(12,'0')}}`;
function response(value,status=200){{return new Response(JSON.stringify(value),{{status,headers:{{'Content-Type':'application/json'}}}});}}
const fetchImpl=async(path,init)=>{{
 calls.push({{path,init}});const body=JSON.parse(init.body);
 if(path.endsWith('/lease/acquire'))return response({{request_id:request,operation_id:body.operation_id,lease_id:lease,fencing_generation:3,base_revision_id:base,expires_at:'2026-08-14T00:05:00.000Z',absolute_expires_at:'2026-08-14T02:00:00.000Z'}},201);
 if(path.endsWith('/lease/heartbeat'))return response({{request_id:request,lease_id:lease,fencing_generation:3,expires_at:'2026-08-14T00:05:30.000Z'}});
 if(path.endsWith('/lease/release'))return response({{request_id:request,operation_id:body.operation_id,resource_id:item,row_version:10,status:'released'}});
 if(path.endsWith('/lease/force-release'))return response({{request_id:request,operation_id:body.operation_id,resource_id:item,row_version:10,status:'force_released'}});
 if(init.method==='PUT')return response({{request_id:request,operation_id:body.operation_id,item_id:item,revision_id:next,row_version:10,status:'updated'}});
 throw new Error('unexpected '+path);
}};
(async()=>{{
 const transport=api.createTransport({{fetchImpl,csrfToken:()=> 'csrf',operationIdFactory:op}});
 const acquireOp=op();
 const acquired=await transport.acquireItemLease({{spaceId:space,itemId:item,editorSessionId:session,rawToken:token,operationId:acquireOp}});
 await transport.heartbeatItemLease({{spaceId:space,itemId:item,editorSessionId:session,leaseId:lease,fencingGeneration:3,rawToken:token}});
 const releaseOp=op();
 await transport.releaseItemLease({{spaceId:space,itemId:item,editorSessionId:session,leaseId:lease,fencingGeneration:3,rawToken:token,operationId:releaseOp}});
 const saveOp=op();
 await transport.updateItem({{spaceId:space,itemId:item,editorSessionId:session,leaseId:lease,fencingGeneration:3,expectedRowVersion:9,expectedRevisionId:base,markdown:'# draft\\n',reason:'Done',rawToken:token,operationId:saveOp}});
 const forceOp=op();
 await transport.forceReleaseItemLease({{spaceId:space,itemId:item,reason:'Owner unavailable',confirmation:'FORCE_RELEASE',operationId:forceOp}});
 assert.equal(acquired.lease_id,lease);
 assert.equal(calls.length,5);
 for(const call of calls){{
   const body=JSON.parse(call.init.body);
   assert.equal(JSON.stringify(body).includes(token),false);
   assert.equal(call.init.headers['X-CSRF-Token'],'csrf');
 }}
 assert.equal(calls[0].init.headers['X-Knowledge-Lease-Token'],token);
 assert.equal(calls[1].init.headers['X-Knowledge-Fence'],'3');
 assert.equal(calls[1].init.headers['Idempotency-Key'],undefined);
 assert.equal(calls[2].init.headers['Idempotency-Key'],releaseOp);
 assert.equal(calls[3].init.headers['If-Match'],`"${{base}}"`);
 assert.deepEqual(JSON.parse(calls[1].init.body),{{editor_session_id:session,lease_id:lease}});
 assert.deepEqual(Object.keys(JSON.parse(calls[3].init.body)).sort(),['editor_session_id','expected_revision_id','expected_row_version','lease_id','markdown','operation_id','reason']);
 console.log(JSON.stringify({{calls:calls.length,tokenLength:token.length}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {"calls": 5, "tokenLength": 43}


@pytest.mark.scenario("LEASE-011")
@pytest.mark.scenario("LEASE-012")
def test_p55_transport_preserves_safe_recovery_details_and_generates_256_bit_token() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const api=require({json.dumps(str(_TRANSPORT))});
const bytes=[];const crypto={{getRandomValues:value=>{{for(let i=0;i<value.length;i++){{value[i]=i;bytes.push(i);}}return value;}}}};
const token=api.createLeaseToken(crypto);
assert.match(token,/^[A-Za-z0-9_-]{{43}}$/);assert.equal(bytes.length,32);
const transport=api.createTransport({{
 operationIdFactory:()=> '11111111-1111-4111-8111-111111111111',
 fetchImpl:async()=>new Response(JSON.stringify({{request_id:'22222222-2222-4222-8222-222222222222',error:{{code:'REVISION_CONFLICT',message:'changed',retryable:false,details:{{current_revision_id:'33333333-3333-4333-8333-333333333333',draft_recovery:{{actions:['view_current','copy_draft','reacquire_and_compare'],preserve_local_draft:true,force_overwrite_allowed:false}}}}}}}}),{{status:409,headers:{{'Content-Type':'application/json'}}}}),
}});
(async()=>{{
 let observed;
 try{{await transport.acquireItemLease({{spaceId:'44444444-4444-4444-8444-444444444444',itemId:'55555555-5555-4555-8555-555555555555',editorSessionId:'66666666-6666-4666-8666-666666666666',rawToken:token}});}}catch(error){{observed=error;}}
 assert.equal(observed.code,'REVISION_CONFLICT');
 assert.equal(observed.details.draft_recovery.preserve_local_draft,true);
 assert.equal(Object.values(observed).includes(token),false);
 console.log(JSON.stringify({{code:observed.code,token}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result["code"] == "REVISION_CONFLICT"
    assert len(result["token"]) == 43


@pytest.mark.scenario("LEASE-001")
@pytest.mark.scenario("LEASE-003")
@pytest.mark.scenario("LEASE-006")
@pytest.mark.scenario("LEASE-012")
def test_p55_editor_heartbeats_preserves_conflict_draft_reacquires_and_releases() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const transportModule=require({json.dumps(str(_TRANSPORT))});
const space='11111111-1111-4111-8111-111111111111';
const item='22222222-2222-4222-8222-222222222222';
const oldRevision='33333333-3333-4333-8333-333333333333';
const newRevision='44444444-4444-4444-8444-444444444444';
let revision=oldRevision,rowVersion=8,leaseCounter=0,updateCalls=0,heartbeatCalls=0,releaseCalls=0,compareCalls=0,operationCounter=0;
const operations=()=>`55555555-5555-4555-8555-${{String(++operationCounter).padStart(12,'0')}}`;
const rendered=[];const events=[];const copied=[];const timers=new Map();let timerCounter=0;
function node(){{return {{className:'',dataset:{{}},textContent:'',value:'',listeners:{{}},addEventListener(name,handler){{this.listeners[name]=handler;}}}};}}
const documentRef={{createElement:node}};
const managementRoot={{ownerDocument:documentRef,replaceChildren:()=>{{rendered.length=0;}},appendChild:value=>rendered.push(value)}};
const snapshot={{scope_epoch:1,selected_space_id:space,spaces:[{{id:space,kind:'personal',name:'Mine',org_path:[],capabilities:['read','edit'],selected:true}}],conversation_badge:null}};
const detail=()=>({{item:{{id:item,space_id:space,source_space_id:space,selected_view_space_id:space,inherited:false,origin:'native',title:'Editable',state:'active',curation_state:'published',row_version:rowVersion,current_revision_id:revision}},source_space_id:space,selected_view_space_id:space,inherited:false,origin:'native',references:[],capabilities:['read','edit'],markdown:'# current',lease:null,timeline:{{content_age_seconds:1,review_age_seconds:null,last_updated_at:'2026-08-14T00:00:00.000Z'}},lifecycle:{{edge_counts:{{total:0}},reference_counts:{{total:0,conversation_pins:0}},deactivation:null}}}});
const history=()=>({{items:[{{id:revision,item_id:item,revision_no:rowVersion,created_by_display:'Editor',created_at:'2026-08-14T00:00:00.000Z',reason:'Current'}}]}});
const transport={{
 probe:async()=>({{}}),probePublication:async()=>false,readSnapshot:async()=>snapshot,management:async()=>({{items:[]}}),createConversation:async()=>{{throw Error('unused');}},
 getItem:async()=>detail(),listRevisions:async()=>history(),getRevision:async()=>({{}}),
 acquireItemLease:async()=>{{leaseCounter++;return {{lease_id:`66666666-6666-4666-8666-${{String(leaseCounter).padStart(12,'0')}}`,fencing_generation:leaseCounter,base_revision_id:revision,expires_at:'2026-08-14T00:05:00.000Z',absolute_expires_at:'2026-08-14T02:00:00.000Z'}};}},
 heartbeatItemLease:async()=>{{heartbeatCalls++;return {{lease_id:'ignored',fencing_generation:leaseCounter,expires_at:'2026-08-14T00:05:30.000Z'}};}},
 releaseItemLease:async()=>{{releaseCalls++;return {{status:'released'}};}},
 updateItem:async()=>{{updateCalls++;revision=newRevision;rowVersion=9;const error=new transportModule.CanonicalTransportError('REVISION_CONFLICT','changed',409,false,false,{{draft_recovery:{{preserve_local_draft:true}}}});throw error;}},
 compareRevisions:async()=>{{compareCalls++;return {{hunks:[{{lines:['-old\\n','+new\\n']}}]}};}},
 forceReleaseItemLease:async()=>{{throw Error('unused');}},
}};
const state={{session_id:null,messages:[],sessionCache:new Map()}};const chat={{renderMessages:()=>{{}},cloneMessages:value=>value}};
(async()=>{{
 const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,managementRoot,operationIdFactory:operations,transportModule,cryptoImpl:{{getRandomValues:value=>{{value.fill(leaseCounter+1);return value;}}}},heartbeatIntervalMs:1000,setIntervalImpl:(fn)=>{{const id=++timerCounter;timers.set(id,fn);return id;}},clearIntervalImpl:id=>timers.delete(id),copyDraft:value=>copied.push(value),dispatchEvent:(name,detail)=>events.push([name,detail]),onError:()=>{{}}}});
 await bridge.activate();await bridge.openItem(space,item);
 await rendered.find(value=>value.className==='canonical-edit-start').listeners.click();
 assert.equal(bridge.editingState().status,'held');assert.equal(timers.size,1);
 await [...timers.values()][0]();assert.equal(heartbeatCalls,1);
 let draft=rendered.find(value=>value.className==='canonical-item-editor');draft.value='# private local draft';draft.listeners.input();
 await rendered.find(value=>value.className==='canonical-edit-complete').listeners.click();
 assert.equal(updateCalls,1);assert.equal(bridge.editingState().status,'conflict');assert.equal(bridge.editingState().draft_preserved,true);assert.equal(timers.size,0);
 assert.equal(rendered.find(value=>value.className==='canonical-item-editor').value,'# private local draft');
 await rendered.find(value=>value.className==='canonical-edit-copy').listeners.click();assert.deepEqual(copied,['# private local draft']);
 await rendered.find(value=>value.className==='canonical-edit-reacquire').listeners.click();
 assert.equal(bridge.editingState().status,'held');assert.equal(leaseCounter,2);assert.equal(compareCalls,1);
 assert.match(rendered.map(value=>value.textContent).join('|'),/Canonical changes/);
 await rendered.find(value=>value.className==='canonical-edit-cancel').listeners.click();
 await rendered.find(value=>value.className==='canonical-edit-copy-release').listeners.click();
 assert.equal(bridge.editingState(),null);assert.equal(releaseCalls,2);assert.deepEqual(copied,['# private local draft','# private local draft']);
 await bridge.beginEdit(space,item);assert.equal(timers.size,1);await bridge.destroy();
 assert.equal(bridge.editingState(),null);assert.equal(timers.size,0);assert.equal(releaseCalls,3);
 assert.ok(events.some(value=>value[0]==='knowledge:edit-conflict'&&value[1].draftPreserved===true));
 assert.ok(events.every(value=>!JSON.stringify(value).includes('# private local draft')));
 console.log(JSON.stringify({{compareCalls,heartbeatCalls,leaseCounter,releaseCalls,updateCalls}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "compareCalls": 1,
        "heartbeatCalls": 1,
        "leaseCounter": 3,
        "releaseCalls": 3,
        "updateCalls": 1,
    }


@pytest.mark.scenario("LEASE-002")
@pytest.mark.scenario("LEASE-007")
@pytest.mark.scenario("LEASE-008")
def test_p55_foreign_lock_is_read_only_and_force_release_is_explicit() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const transportModule=require({json.dumps(str(_TRANSPORT))});
const space='11111111-1111-4111-8111-111111111111';const item='22222222-2222-4222-8222-222222222222';
const rendered=[];const forceCalls=[];
function node(){{return {{className:'',dataset:{{}},textContent:'',value:'',listeners:{{}},addEventListener(name,handler){{this.listeners[name]=handler;}}}};}}
const documentRef={{createElement:node}};const managementRoot={{ownerDocument:documentRef,replaceChildren:()=>{{rendered.length=0;}},appendChild:value=>rendered.push(value)}};
const snapshot={{scope_epoch:1,selected_space_id:space,spaces:[{{id:'99999999-9999-4999-8999-999999999999',kind:'personal',name:'Mine',org_path:[],capabilities:['read','edit'],selected:false}},{{id:space,kind:'part',name:'Part',org_path:['Root','Team','Part'],capabilities:['read','edit','force_release'],selected:true}}],conversation_badge:null}};
const detail={{item:{{id:item,space_id:space,source_space_id:space,selected_view_space_id:space,inherited:false,origin:'native',title:'Locked',state:'active',curation_state:'published',row_version:3,current_revision_id:'33333333-3333-4333-8333-333333333333'}},source_space_id:space,selected_view_space_id:space,inherited:false,origin:'native',references:[],capabilities:['read','edit','force_release'],markdown:'# locked',lease:{{holder_display:'Other Editor',holder_user_id:'other',heartbeat_at:'2026-08-14T00:00:00.000Z',expires_at:'2026-08-14T00:05:00.000Z',fencing_generation:4}},timeline:{{content_age_seconds:1,review_age_seconds:null,last_updated_at:'2026-08-14T00:00:00.000Z'}},lifecycle:{{edge_counts:{{total:0}},reference_counts:{{total:0,conversation_pins:0}},deactivation:null}}}};
const history={{items:[]}};
const transport={{probe:async()=>({{}}),probePublication:async()=>false,readSnapshot:async()=>snapshot,management:async()=>({{items:[]}}),createConversation:async()=>{{}},getItem:async()=>detail,listRevisions:async()=>history,forceReleaseItemLease:async value=>{{forceCalls.push(value);return {{status:'force_released'}};}}}};
const state={{session_id:null,messages:[],sessionCache:new Map()}};const chat={{renderMessages:()=>{{}},cloneMessages:value=>value}};
(async()=>{{
 const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,managementRoot,operationIdFactory:()=> '44444444-4444-4444-8444-444444444444',transportModule,onError:()=>{{}}}});
 await bridge.activate();await bridge.openItem(space,item);
 assert.equal(rendered.some(value=>value.className==='canonical-edit-start'),false);
 assert.match(rendered.map(value=>value.textContent).join('|'),/Other Editor/);
 const reason=rendered.find(value=>value.className==='canonical-force-release-reason');
 const confirmation=rendered.find(value=>value.className==='canonical-force-release-confirmation');
 reason.value='Editor left unexpectedly';confirmation.value='FORCE_RELEASE';
 await rendered.find(value=>value.className==='canonical-force-release').listeners.click();
 assert.equal(forceCalls.length,1);assert.equal(forceCalls[0].reason,'Editor left unexpectedly');assert.equal(forceCalls[0].confirmation,'FORCE_RELEASE');
 console.log(JSON.stringify({{forceCalls:forceCalls.length,readOnly:true}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {"forceCalls": 1, "readOnly": True}


@pytest.mark.scenario("LEASE-006")
@pytest.mark.scenario("LEASE-012")
def test_p55_editor_releases_and_refuses_unbound_post_acquire_item_snapshot() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const transportModule=require({json.dumps(str(_TRANSPORT))});
const space='11111111-1111-4111-8111-111111111111';const item='22222222-2222-4222-8222-222222222222';
const stale='33333333-3333-4333-8333-333333333333';const acquired='44444444-4444-4444-8444-444444444444';let releases=0;
const snapshot={{scope_epoch:1,selected_space_id:space,spaces:[{{id:space,kind:'personal',name:'Mine',org_path:[],capabilities:['read','edit'],selected:true}}],conversation_badge:null}};
const detail={{item:{{id:item,space_id:space,title:'Race',state:'active',curation_state:'published',row_version:8,current_revision_id:stale}},capabilities:['read','edit'],markdown:'# stale',lease:null,timeline:{{content_age_seconds:1,review_age_seconds:null,last_updated_at:'2026-08-14T00:00:00.000Z'}},lifecycle:{{edge_counts:{{total:0}},reference_counts:{{total:0,conversation_pins:0}},deactivation:null}}}};
const transport={{probe:async()=>({{}}),probePublication:async()=>false,readSnapshot:async()=>snapshot,management:async()=>({{items:[]}}),createConversation:async()=>{{}},getItem:async()=>detail,listRevisions:async()=>({{items:[]}}),acquireItemLease:async()=>({{lease_id:'55555555-5555-4555-8555-555555555555',fencing_generation:2,base_revision_id:acquired,expires_at:'2026-08-14T00:05:00.000Z',absolute_expires_at:'2026-08-14T02:00:00.000Z'}}),releaseItemLease:async()=>{{releases++;}}}};
const state={{session_id:null,messages:[],sessionCache:new Map()}};const chat={{renderMessages:()=>{{}},cloneMessages:value=>value}};
(async()=>{{
 const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,operationIdFactory:()=> '66666666-6666-4666-8666-666666666666',transportModule,cryptoImpl:{{getRandomValues:value=>{{value.fill(9);return value;}}}}}});
 await bridge.activate();
 await assert.rejects(()=>bridge.beginEdit(space,item),/lease base is not bound/);
 assert.equal(releases,1);assert.equal(bridge.editingState(),null);
 console.log(JSON.stringify({{releases,state:bridge.editingState()}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {"releases": 1, "state": None}

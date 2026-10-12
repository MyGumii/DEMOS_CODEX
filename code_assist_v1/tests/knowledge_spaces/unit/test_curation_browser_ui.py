from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


_ROOT = Path(__file__).resolve().parents[3]
_BROWSER = _ROOT / "static" / "knowledge_core_browser.js"
_SCOPE = _ROOT / "static" / "knowledge_scope.js"


def _node(source: str) -> dict[str, object]:
    executable = shutil.which("node")
    if executable is None:
        pytest.fail("P6.7 browser review UI proof requires Node.js.")
    completed = subprocess.run(
        [executable, "-e", source],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        pytest.fail(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


def test_p67_review_queue_renders_evidence_metrics_lifecycle_and_explicit_actions() -> None:
    result = _node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const space='11111111-1111-4111-8111-111111111111';
const findingId='22222222-2222-4222-8222-222222222222';
const proposalId='33333333-3333-4333-8333-333333333333';
const operation='44444444-4444-4444-8444-444444444444';
let status='pending'; let rowVersion=1; let review=null; let findingReads=0;
let resolveReview; const reviewed=new Promise(resolve=>{{resolveReview=resolve;}});
let resolvePreview; const previewed=new Promise(resolve=>{{resolvePreview=resolve;}});
function summary(){{return {{id:findingId,finding_kind:'near_duplicate',status,risk_tier:'T2',severity:'warning',visibility_effect:'agent_hidden',required_reviewer:'space_editor'}};}}
function detail(){{return {{request_id:operation,finding:{{...summary(),space_id:space,confidence:0.8,created_at:'2026-08-15T00:00:00.000Z',decision_mode:'human',detector_name:'shadow-detector',detector_version:'1',evidence:{{codes:['fixed_revision'],spans:[{{sha256:'a'.repeat(64)}}]}},model_id:'model-shadow',prompt_version:'prompt-v1',proposed_action:'review_duplicate',proposed_action_evidence:{{proposal_id:proposalId}},review_reason:review&&review.reason,reviewed_at:review?'2026-08-15T00:01:00.000Z':null,reviewed_by:review?'reviewer':null,row_version:rowVersion,rule_version:'rule-v1',source_item_id:'55555555-5555-4555-8555-555555555555',source_revision_id:'66666666-6666-4666-8666-666666666666',target_item_id:'77777777-7777-4777-8777-777777777777',target_revision_id:'88888888-8888-4888-8888-888888888888',updated_at:'2026-08-15T00:00:00.000Z'}}}};}}
const transport={{
 probe:async()=>({{}}),probePublication:async()=>false,
 readSnapshot:async()=>({{scope_epoch:1,selected_space_id:space,spaces:[{{id:space,kind:'personal',name:'Mine',org_path:[],capabilities:['read','curate'],selected:true}}],conversation_badge:null}}),
 management:async(_space,surface)=>surface==='index_log'?{{status:'ready',built_generation:1,desired_generation:1}}:surface==='candidates'?{{items:[summary()]}}:{{items:[]}},
 getCurationShadowMetrics:async()=>({{precision_denominator:3,precision_numerator:2,precision_ppm:666666,model_automatic_execution_count:0,t3_model_automatic_execution_count:0,report_status:'pass'}}),
 getFinding:async()=>{{findingReads++;return detail();}},
 reviewFinding:async command=>{{review=command;status='approved';rowVersion=2;resolveReview(command);return {{status:'approved'}};}},
 previewChangeProposal:async()=>{{const value={{panes:[{{label:'fork_base',markdown:'# Base'}},{{label:'source_current',markdown:'# Source'}},{{label:'target_current',markdown:'# Target'}}]}};resolvePreview(value);return value;}},
 getConversation:async()=>({{conversation:{{}},messages:[],pins:[],context_bindings:[]}}),
 createConversation:async()=>{{throw new Error('unused');}},listConversations:async()=>({{items:[]}}),streamTurn:async()=>({{}}),updateConversation:async()=>({{}}),compactConversation:async()=>({{}}),
}};
const state={{session_id:null,messages:[],sessionCache:new Map()}};
const chat={{cloneMessages:value=>value,renderMessages:()=>{{}}}};
const rendered=[];
const documentRef={{createElement:tag=>({{tag,className:'',dataset:{{}},listeners:{{}},textContent:'',value:'',placeholder:'',addEventListener(name,callback){{this.listeners[name]=callback;}}}})}};
const managementRoot={{ownerDocument:documentRef,replaceChildren:()=>{{rendered.length=0;}},appendChild:value=>rendered.push(value)}};
const errors=[];
(async()=>{{
 const bridge=browser.createBridge({{transport,scopeUI:scope,state,chat,managementRoot,operationIdFactory:()=>operation,onError:error=>errors.push(error)}});
 await bridge.activate();
 await bridge.renderManagement();
 assert.ok(rendered.some(value=>value.className.includes('canonical-shadow-pass')&&value.textContent.includes('automatic effects 0')));
 assert.ok(rendered.some(value=>value.textContent.includes('T3 automatic effects 0')));
 const queueRow=rendered.find(value=>value.dataset.canonicalFindingId===findingId);
 assert.match(queueRow.textContent,/T2[/]warning/);
 assert.match(queueRow.textContent,/agent_hidden/);
 await bridge.openFinding(space,findingId);
 const evidence=rendered.find(value=>value.className.includes('canonical-finding-evidence'));
 assert.match(evidence.textContent,/fixed_revision/);
 assert.equal(evidence.textContent.includes('# Source body'),false);
 const previewButton=rendered.find(value=>value.textContent==='Preview proposal');
 previewButton.listeners.click(); await previewed; await new Promise(setImmediate);
 assert.ok(rendered.some(value=>value.className.includes('canonical-proposal-preview')&&value.textContent.includes('# Target')));
 const reason=rendered.find(value=>value.className==='canonical-finding-review-reason');
 reason.value='Human compared fixed evidence.';
 const approve=rendered.find(value=>value.textContent==='Approve');
 approve.listeners.click(); const command=await reviewed; await new Promise(setImmediate);
 assert.equal(command.expectedFindingRowVersion,1);
 assert.equal(command.findingId,findingId);
 assert.equal(command.operationId,operation);
 assert.equal(command.reason,'Human compared fixed evidence.');
 assert.ok(rendered.some(value=>value.className.includes('canonical-finding-review-lifecycle')&&value.textContent.includes('reviewer')));
 assert.equal(rendered.some(value=>value.textContent==='Approve'),false);
 for (const lifecycle of ['obsolete','rejected','ignored']) {{
   status=lifecycle;rowVersion++;
   review=lifecycle==='obsolete'?null:{{reason:`Human ${{lifecycle}} fixed evidence.`}};
   await bridge.openFinding(space,findingId);
   assert.ok(rendered.some(value=>value.className.includes('canonical-finding-title')&&value.textContent.includes(lifecycle)));
   assert.equal(rendered.some(value=>value.textContent==='Approve'),false);
   assert.equal(rendered.some(value=>value.className.includes('canonical-finding-review-lifecycle')),lifecycle!=='obsolete');
 }}
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({{findingReads,reviewDecision:command.decision,shadow:true}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "findingReads": 5,
        "reviewDecision": "approve",
        "shadow": True,
    }


def test_p67_read_only_management_degrades_without_exposing_curation_queue() -> None:
    result = _node(
        f"""
const assert=require('node:assert/strict');
const browser=require({json.dumps(str(_BROWSER))});
const scope=require({json.dumps(str(_SCOPE))});
const space='11111111-1111-4111-8111-111111111111';
const forbidden=Object.assign(new Error('forbidden'),{{status:403}});
const transport={{
 probe:async()=>({{}}),probePublication:async()=>false,
 readSnapshot:async()=>({{scope_epoch:1,selected_space_id:space,spaces:[{{id:space,kind:'personal',name:'Read only',org_path:[],capabilities:['read'],selected:true}}],conversation_badge:null}}),
 management:async(_space,surface)=>{{if(surface==='candidates')throw forbidden;return surface==='index_log'?{{status:'ready',built_generation:1,desired_generation:1}}:{{items:[]}};}},
 getCurationShadowMetrics:async()=>{{throw forbidden;}},
 createConversation:async()=>{{throw new Error('unused');}},listConversations:async()=>({{items:[]}}),streamTurn:async()=>({{}}),updateConversation:async()=>({{}}),compactConversation:async()=>({{}}),getConversation:async()=>({{messages:[],pins:[]}}),
}};
const rendered=[];const documentRef={{createElement:tag=>({{tag,className:'',dataset:{{}},textContent:'',addEventListener(){{}}}})}};const managementRoot={{ownerDocument:documentRef,replaceChildren:()=>{{rendered.length=0;}},appendChild:value=>rendered.push(value)}};
(async()=>{{
 const bridge=browser.createBridge({{transport,scopeUI:scope,state:{{session_id:null,messages:[],sessionCache:new Map()}},chat:{{cloneMessages:value=>value,renderMessages:()=>{{}}}},managementRoot,operationIdFactory:()=> '22222222-2222-4222-8222-222222222222'}});
 await bridge.activate();const values=await bridge.renderManagement();
 assert.equal(values.candidates.unavailable,true);assert.equal(values.candidates.items.length,0);assert.equal(values.shadow_metrics,null);
 assert.equal(rendered.some(value=>value.dataset.canonicalFindingId),false);
 console.log(JSON.stringify({{readOnly:true,rendered:rendered.length}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result["readOnly"] is True

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


_ROOT = Path(__file__).resolve().parents[3]
_TRANSPORT = _ROOT / "static" / "knowledge_core_transport.js"


def _node(source: str) -> dict[str, object]:
    executable = shutil.which("node")
    if executable is None:
        pytest.fail("P6.7 browser transport proof requires Node.js.")
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


def test_p67_transport_binds_review_proposal_merge_and_shadow_requests() -> None:
    result = _node(
        f"""
const assert=require('node:assert/strict');
const api=require({json.dumps(str(_TRANSPORT))});
const space='11111111-1111-4111-8111-111111111111';
const finding='22222222-2222-4222-8222-222222222222';
const proposal='33333333-3333-4333-8333-333333333333';
const sourceItem='44444444-4444-4444-8444-444444444444';
const sourceRevision='55555555-5555-4555-8555-555555555555';
const targetItem='66666666-6666-4666-8666-666666666666';
const targetRevision='77777777-7777-4777-8777-777777777777';
const operation='88888888-8888-4888-8888-888888888888';
const session='99999999-9999-4999-8999-999999999999';
const lease='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const request='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const token='A'.repeat(43);
const closure='d'.repeat(64);
const calls=[];
function response(value){{return new Response(JSON.stringify({{request_id:request,...value}}),{{status:200,headers:{{'Content-Type':'application/json'}}}});}}
async function fetchImpl(path,init){{
  const body=init.body?JSON.parse(init.body):null;
  calls.push({{path,method:init.method,headers:Object.fromEntries(Object.entries(init.headers)),body}});
  if(path.endsWith(`/curation/findings/${{finding}}`))return response({{finding:{{confidence:0.8,created_at:'2026-08-15T00:00:00.000Z',decision_mode:'human',detector_name:'shadow',detector_version:'1',evidence:{{}},finding_kind:'near_duplicate',id:finding,model_id:'model',prompt_version:'prompt',proposed_action:'review_duplicate',proposed_action_evidence:{{}},required_reviewer:'space_editor',review_reason:null,reviewed_at:null,reviewed_by:null,risk_tier:'T2',row_version:1,rule_version:'rule',severity:'warning',source_item_id:sourceItem,source_revision_id:sourceRevision,space_id:space,status:'pending',target_item_id:targetItem,target_revision_id:targetRevision,updated_at:'2026-08-15T00:00:00.000Z',visibility_effect:'agent_hidden'}}}});
  if(path.endsWith(`/curation/findings/${{finding}}/review`))return response({{finding_id:finding,operation_id:operation,outcome:'approved',row_version:2}});
  if(path.includes('/curation/shadow-metrics'))return response({{finding_count:1,human_reviewed_count:1,mode:'shadow_proposal_only',model_accepted_count:1,model_adjudicated_count:1,model_automatic_execution_count:0,model_ignored_count:0,model_obsolete_count:0,model_proposal_count:1,model_rejected_count:0,pending_count:0,precision_denominator:1,precision_numerator:1,precision_ppm:1000000,report_status:'pass',risky_model_automatic_execution_count:0,sample:[],schema_version:1,space_id:space,t3_model_automatic_execution_count:0}});
  if(path.endsWith(`/change-proposals/${{proposal}}/preview`))return response({{current_source_revision_id:sourceRevision,current_target_revision_id:targetRevision,current_target_row_version:1,finding_id:finding,finding_row_version:1,panes:[{{}},{{}},{{}}],preview_sha256:'a'.repeat(64),proposal_id:proposal,proposal_kind:'stable_source_update',proposal_row_version:1,proposal_version:1,proposed_result:{{}},stale:false,state:'pending'}});
  if(path.endsWith(`/change-proposals/${{proposal}}/rebase`))return response({{finding_id:finding,finding_row_version:1,old_proposal_id:proposal,operation_id:operation,preview_sha256:'b'.repeat(64),proposal_id:'cccccccc-cccc-4ccc-8ccc-cccccccccccc',proposal_row_version:1,proposal_series_id:'dddddddd-dddd-4ddd-8ddd-dddddddddddd',proposal_version:2,source_revision_id:sourceRevision,status:'pending_review',target_revision_id:targetRevision}});
  if(path.endsWith(`/change-proposals/${{proposal}}/apply`))return response({{finding_id:finding,finding_row_version:2,item_row_version:2,operation_id:operation,proposal_id:proposal,proposal_row_version:2,rescan_job_id:'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',result_revision_id:'ffffffff-ffff-4fff-8fff-ffffffffffff',status:'applied',target_item_id:targetItem}});
  if(path.endsWith('/merges/preview'))return response({{edge_impact:{{}},panes:[{{}},{{}},{{}}],preview_sha256:'c'.repeat(64),reference_impact:{{}},source_revision_id:sourceRevision,stale:false,target_revision_id:targetRevision}});
  if(path.endsWith('/merges/apply'))return response({{merge_event_id:'12121212-1212-4212-8212-121212121212',operation_id:operation,redirect_relation_id:'13131313-1313-4313-8313-131313131313',result_revision_id:'14141414-1414-4414-8414-141414141414',source_item_id:sourceItem,status:'applied',target_item_id:targetItem}});
  if(path.endsWith('/curation/findings'))return response({{corpus_closure_sha256:closure,has_more:false,items:[{{capabilities:[],confidence:0.8,created_at:'2026-08-15T00:00:00.000Z',detector_name:'shadow',detector_version:'1',evidence:{{}},finding_kind:'near_duplicate',id:finding,inherited:false,model_id:'model',origin:'native',overlay_grant_id:null,prompt_version:'prompt',proposed_action:'review_duplicate',required_reviewer:'space_editor',risk_tier:'T2',row_version:1,rule_version:'rule',selected_view_space_id:space,severity:'warning',source_item_id:sourceItem,source_revision_id:sourceRevision,source_space_id:space,space_id:space,status:'pending',target_item_id:targetItem,target_revision_id:targetRevision,updated_at:'2026-08-15T00:00:00.000Z',visibility_effect:'agent_hidden'}}],next_cursor:null}});
  throw new Error(path);
}}
const transport=api.createTransport({{fetchImpl,csrfToken:()=> 'csrf',operationIdFactory:()=>operation}});
(async()=>{{
 await transport.getFinding(space,finding);
 await transport.reviewFinding({{spaceId:space,findingId:finding,expectedFindingRowVersion:1,decision:'approve',reason:'fixed evidence',operationId:operation}});
 await transport.getCurationShadowMetrics(space,{{sampleSize:7}});
 await transport.previewChangeProposal({{spaceId:space,proposalId:proposal}});
 await transport.rebaseChangeProposal({{spaceId:space,proposalId:proposal,expectedProposalRowVersion:1,expectedFindingRowVersion:1,expectedSourceRevisionId:sourceRevision,expectedTargetRevisionId:targetRevision,proposedMarkdown:'# Rebased\\n',operationId:operation}});
 await transport.applyChangeProposal({{spaceId:space,proposalId:proposal,expectedProposalRowVersion:2,expectedFindingRowVersion:2,expectedTargetItemRowVersion:1,expectedTargetRevisionId:targetRevision,expectedSourceRevisionId:sourceRevision,previewSha256:'a'.repeat(64),editorSessionId:session,leaseId:lease,fencingGeneration:7,rawToken:token,operationId:operation}});
 await transport.previewMerge({{spaceId:space,sourceItemId:sourceItem,sourceRevisionId:sourceRevision,targetItemId:targetItem,targetRevisionId:targetRevision,resultMarkdown:'# Merge\\n'}});
 await transport.applyMerge({{spaceId:space,sourceItemId:sourceItem,sourceRevisionId:sourceRevision,targetItemId:targetItem,targetRevisionId:targetRevision,resultMarkdown:'# Merge\\n',previewSha256:'c'.repeat(64),sourceEditorSessionId:session,sourceLeaseId:lease,sourceFencingGeneration:3,sourceRawToken:token,targetEditorSessionId:'15151515-1515-4515-8515-151515151515',targetLeaseId:'16161616-1616-4616-8616-161616161616',targetFencingGeneration:5,targetRawToken:'B'.repeat(43),operationId:operation}});
 assert.equal((await transport.management(space,'candidates')).items[0].id,finding);
 assert.equal(calls.length,9);
 assert.equal(calls[1].headers['Idempotency-Key'],operation);
 assert.equal(calls[2].path.endsWith('sample_size=7'),true);
 assert.equal(calls[5].headers['X-Knowledge-Lease-Token'],token);
 assert.equal(calls[5].headers['X-Knowledge-Fence'],'7');
 assert.equal(calls[7].headers['X-Knowledge-Source-Lease-Token'],token);
 assert.equal(calls[7].headers['X-Knowledge-Source-Fence'],'3');
 assert.equal(calls[7].headers['X-Knowledge-Target-Lease-Token'],'B'.repeat(43));
 assert.equal(calls[7].headers['X-Knowledge-Target-Fence'],'5');
 assert.equal(JSON.stringify(calls).includes('csrf'),true);
 console.log(JSON.stringify({{callCount:calls.length,paths:calls.map(value=>value.path)}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result["callCount"] == 9


def test_p67_transport_rejects_malformed_precision_and_never_sends_secret_in_json() -> None:
    result = _node(
        f"""
const assert=require('node:assert/strict');
const api=require({json.dumps(str(_TRANSPORT))});
const space='11111111-1111-4111-8111-111111111111';
const proposal='33333333-3333-4333-8333-333333333333';
const operation='88888888-8888-4888-8888-888888888888';
const token='A'.repeat(43);
let sentBody='';
const transport=api.createTransport({{operationIdFactory:()=>operation,fetchImpl:async(path,init)=>{{sentBody=init.body||'';return new Response(JSON.stringify({{request_id:'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',finding_id:'22222222-2222-4222-8222-222222222222',finding_row_version:2,item_row_version:2,operation_id:operation,proposal_id:proposal,proposal_row_version:2,rescan_job_id:'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',result_revision_id:'ffffffff-ffff-4fff-8fff-ffffffffffff',status:'applied',target_item_id:'66666666-6666-4666-8666-666666666666'}}),{{status:200,headers:{{'Content-Type':'application/json'}}}});}}}});
(async()=>{{
 await assert.rejects(()=>transport.getCurationShadowMetrics(space,{{sampleSize:0}}),/sample_size/);
 await transport.applyChangeProposal({{spaceId:space,proposalId:proposal,expectedProposalRowVersion:2,expectedFindingRowVersion:2,expectedTargetItemRowVersion:1,expectedTargetRevisionId:'77777777-7777-4777-8777-777777777777',expectedSourceRevisionId:null,previewSha256:'a'.repeat(64),editorSessionId:'99999999-9999-4999-8999-999999999999',leaseId:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',fencingGeneration:7,rawToken:token,operationId:operation}});
 assert.equal(sentBody.includes(token),false);
 console.log(JSON.stringify({{secretInBody:sentBody.includes(token)}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {"secretInBody": False}

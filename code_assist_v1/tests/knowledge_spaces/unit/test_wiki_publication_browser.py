"""Node execution of real browser controllers/transport, not live browser QA."""
import json

import pytest

from code_assist_v1.tests.knowledge_spaces.unit.test_knowledge_core_browser_transport import (
    _run_node, _PUBLICATION, _TRANSPORT,
)

pytestmark = [pytest.mark.unit, pytest.mark.scenario("UPLOAD-005", "UPLOAD-013")]


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("generation", ["ready", "extraction_only", "unavailable"])
def test_browser_waits_past_extraction_and_exposes_truthful_generation(batch, generation):
    result = _run_node(f"""
const assert=require('node:assert/strict');
const ui=require({json.dumps(str(_PUBLICATION))});
const batch={json.dumps(batch)}, generation={json.dumps(generation)};
let polls=0, commits=0; const labels=[], perId=new Map();
const preview={{preview_id:'preview',status:'ready',source_snapshot_sha256:'a'.repeat(64),
 title:'Complex source',warnings:[],available_target_modes:['personal'],
 wiki_generation:{{schema_version:1,status:'pending'}},publication_ready:false}};
const transport={{
 createPublicationPreview:async source=>({{...preview,preview_id:source.file.name}}),
 getPublicationPreview:async id=>{{polls++;const count=(perId.get(id)||0)+1;perId.set(id,count);
   return count===1 ? {{...preview,preview_id:id}} : {{...preview,preview_id:id,
   wiki_generation:{{schema_version:1,status:generation}},publication_ready:generation!=='unavailable'}};}},
 cancelPublicationPreview:async()=>({{status:'cancelled'}}),
 spaces:async()=>({{items:[{{id:'personal',kind:'personal',name:'Personal'}}]}}),
 commitPublication:async value=>{{commits++;assert.equal(perId.get(value.previewId),2);assert.ok(!('authoredSnapshotId' in value));
   return {{operation_id:'operation',body_source:generation==='ready'?'authored_wiki':'extraction',
     results:[{{space_id:'personal',item_id:'item',outcome:'created'}}]}};}},
}};
const renderer={{showPreparing:()=>{{}},showPending:p=>labels.push(p.wiki_generation.status),
 showError:e=>{{renderer.error=e;}},showPreview:(v,c)=>{{renderer.view=v;renderer.commit=c;}},
 showResults:v=>{{renderer.result=v;}},
 showBatchProgress:v=>labels.push(...v.items.map(i=>i.statusLabel)),showBatchCommitProgress:()=>{{}},
 showBatchPreview:(v,c)=>{{renderer.view=v;renderer.commit=c;}},showBatchResults:v=>{{renderer.result=v;}}}};
(async()=>{{
 const controller=ui[batch?'createBatchController':'createController']({{transport,renderer,
   operationIdFactory:()=> 'operation',sleep:async()=>{{}},maxPolls:5}});
 const source={{sourceType:'upload',file:{{name:'complex.pdf'}},preferredTargetMode:'personal'}};
 let failed=false;
 try {{await controller.open(batch?[source,{{...source,file:{{name:'second.docx'}}}}]:source);}} catch(e) {{failed=true;}}
 assert.equal(polls,batch?4:2);assert.equal(commits,0);
 if(generation==='unavailable'){{
   if(batch) assert.equal(renderer.view.readyCount,0);else assert.equal(failed,true);
 }}else{{
   const warnings=batch?renderer.view.items[0].warnings:renderer.view.warnings;
   assert.ok(warnings.some(w=>w.includes(generation==='ready'?'Wiki 생성 완료':'보존된 추출 본문')));
   await renderer.commit('personal');assert.equal(commits,batch?2:1);
   const label=batch?renderer.result.items[0].statusLabel:renderer.result.publicationStatus;
   assert.equal(label,generation==='ready'?'Wiki 게시 완료':'추출 본문 게시 완료');
 }}
 if(batch)assert.ok(labels.includes('텍스트 추출 완료 · Wiki 생성 중'));
 else assert.ok(labels.includes('pending'));
 console.log(JSON.stringify({{polls,commits}}));
}})().catch(e=>{{console.error(e);process.exit(1);}});
""")
    assert result == {"polls": 4 if batch else 2, "commits": 0 if generation == "unavailable" else (2 if batch else 1)}


def test_transport_rejects_contradictory_readiness_and_accepts_only_public_source_enum():
    result = _run_node(f"""
const assert=require('node:assert/strict');const api=require({json.dumps(str(_TRANSPORT))});
const id='11111111-1111-4111-8111-111111111111';let value;
const base={{request_id:id,operation_id:id,preview_id:id,status:'ready',title:'Wiki',
 source_snapshot_sha256:'a'.repeat(64),available_target_modes:['personal']}};
const transport=api.createTransport({{fetchImpl:async()=>new Response(JSON.stringify(value),
 {{status:200,headers:{{'Content-Type':'application/json'}}}}),csrfToken:()=> 'csrf',operationIdFactory:()=>id}});
(async()=>{{
 let rejects=0;
 for(const bad of [
  {{wiki_generation:{{schema_version:1,status:'pending'}},publication_ready:true}},
  {{wiki_generation:{{schema_version:1,status:'unavailable'}},publication_ready:true}},
  {{wiki_generation:{{schema_version:1,status:'ready'}}}},
  {{publication_ready:true}},
  {{wiki_generation:{{schema_version:1,status:'ready',snapshot_id:id}},publication_ready:true}},
  {{wiki_generation:{{schema_version:1,status:'ready'}},publication_ready:true,status:'cancelled'}},
 ]){{value={{...base,...bad}};await assert.rejects(()=>transport.getPublicationPreview(id));rejects++;}}
 for(const state of ['pending','ready','extraction_only','unavailable']){{
   value={{...base,wiki_generation:{{schema_version:1,status:state}},publication_ready:['ready','extraction_only'].includes(state)}};
  assert.equal((await transport.getPublicationPreview(id)).wiki_generation.status,state);
 }}
 value={{...base,status:'consumed',wiki_generation:{{schema_version:1,status:'ready'}},publication_ready:false}};
 assert.equal((await transport.getPublicationPreview(id)).publication_ready,false);
 value={{...base,wiki_generation:{{schema_version:1,status:'ready'}},publication_ready:false}};
 assert.equal((await transport.getPublicationPreview(id)).publication_ready,false);
 const committed={{request_id:id,operation_id:id,results:[{{space_id:id,outcome:'created',source_artifact_id:id,
 item_id:id,resolved_revision_id:id,created_revision_id:id,finding_id:null}}]}};
 const command={{previewId:id,sourceSnapshotSha256:'a'.repeat(64),targetMode:'personal',operationId:id}};
 for(const body of ['authored_wiki','extraction']){{value={{...committed,body_source:body}};
   assert.equal((await transport.commitPublication(command)).body_source,body);}}
 value={{...committed,body_source:'unknown'}};await assert.rejects(()=>transport.commitPublication(command));
 value={{...committed,authored_snapshot_id:id}};await assert.rejects(()=>transport.commitPublication(command));
 console.log(JSON.stringify({{rejects}}));
}})().catch(e=>{{console.error(e);process.exit(1);}});
""")
    assert result == {"rejects": 6}


def test_ready_preview_does_not_promise_new_wiki_when_prior_extraction_is_reused():
    result = _run_node(f"""
const assert=require('node:assert/strict');const ui=require({json.dumps(str(_PUBLICATION))});
const view=ui.previewView({{preview_id:'preview',status:'ready',source_snapshot_sha256:'a'.repeat(64),
 title:'Same original',warnings:[],available_target_modes:['part'],wiki_generation:{{schema_version:1,status:'ready'}},
 publication_ready:true}},[{{id:'part',kind:'part',name:'Part'}}]);
assert.ok(view.warnings.some(w=>w.includes('기존 본문을 사용할 수 있습니다')));
assert.ok(!view.warnings.some(w=>w.includes('생성된 본문을 게시합니다')));
const committed=ui.resultView({{operation_id:'operation',body_source:'extraction',
 results:[{{item_id:'item',space_id:'part',outcome:'provenance_added'}}]}},[{{id:'part',name:'Part'}}]);
assert.equal(committed.publicationStatus,'추출 본문 게시 완료');
console.log(JSON.stringify({{outcome:committed.targets[0].outcome}}));
""")
    assert result == {"outcome": "provenance_added"}

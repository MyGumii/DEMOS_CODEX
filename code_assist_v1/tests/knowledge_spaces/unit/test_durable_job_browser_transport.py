from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.unit
_ROOT = Path(__file__).resolve().parents[3]
_TRANSPORT = _ROOT / "static" / "knowledge_core_transport.js"


def _run_node(source: str) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.fail("P7.1 browser transport proof requires Node.js.")
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


def test_p71_transport_reads_and_cancels_one_exact_actor_job() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const api=require({json.dumps(str(_TRANSPORT))});
const job='11111111-1111-4111-8111-111111111111';
const operation='22222222-2222-4222-8222-222222222222';
const request='33333333-3333-4333-8333-333333333333';
const calls=[];
const response=value=>new Response(JSON.stringify(value),{{status:200,headers:{{'Content-Type':'application/json'}}}});
const fetchImpl=async(path,options)=>{{
 calls.push({{path,method:options.method,headers:options.headers,body:options.body}});
 if(options.method==='GET') return response({{
  attempt_count:1,cancel_requested:false,cancellable:true,completed_at:null,
  created_at:'2026-08-15T00:00:00.000Z',error:null,handler_version:'v1',job_id:job,
  job_type:'fixture.public-job',max_attempts:3,progress_permille:250,request_id:request,
  result:null,state:'running',status_url:`/api/knowledge/v1/jobs/${{job}}`,
  updated_at:'2026-08-15T00:01:00.000Z'
 }});
 return response({{operation_id:operation,request_id:request,resource_id:job,row_version:null,status:'cancel_requested'}});
}};
(async()=>{{
 const transport=api.createTransport({{fetchImpl,csrfToken:()=> 'csrf-value',operationIdFactory:()=>operation}});
 const read=await transport.getJob(job);
 assert.equal(read.job_id,job);assert.equal(read.state,'running');assert.equal(read.progress_permille,250);
 const cancelled=await transport.cancelJob({{jobId:job,operationId:operation}});
 assert.equal(cancelled.status,'cancel_requested');assert.equal(cancelled.resource_id,job);
 assert.deepEqual(calls.map(call=>[call.path,call.method]),[
  [`/api/knowledge/v1/jobs/${{job}}`,'GET'],
  [`/api/knowledge/v1/jobs/${{job}}/cancel`,'POST'],
 ]);
 assert.equal(calls[1].headers['Idempotency-Key'],operation);
 assert.equal(calls[1].headers['X-CSRF-Token'],'csrf-value');
 assert.deepEqual(JSON.parse(calls[1].body),{{operation_id:operation}});
 console.log(JSON.stringify({{calls:calls.length,state:read.state,status:cancelled.status}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "calls": 2,
        "state": "running",
        "status": "cancel_requested",
    }


def test_p71_transport_rejects_job_shape_and_operation_binding_drift() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const api=require({json.dumps(str(_TRANSPORT))});
const job='11111111-1111-4111-8111-111111111111';
const other='22222222-2222-4222-8222-222222222222';
const operation='33333333-3333-4333-8333-333333333333';
const request='44444444-4444-4444-8444-444444444444';
const base={{
 attempt_count:0,cancel_requested:false,cancellable:true,completed_at:null,
 created_at:'2026-08-15T00:00:00.000Z',error:null,handler_version:'v1',job_id:job,
 job_type:'fixture.public-job',max_attempts:3,progress_permille:0,request_id:request,
 result:null,state:'queued',status_url:`/api/knowledge/v1/jobs/${{job}}`,
 updated_at:'2026-08-15T00:00:00.000Z'
}};
const response=value=>new Response(JSON.stringify(value),{{status:200,headers:{{'Content-Type':'application/json'}}}});
(async()=>{{
 const extra=api.createTransport({{fetchImpl:async()=>response({{...base,raw_input:'must-not-pass'}}),operationIdFactory:()=>operation}});
 await assert.rejects(()=>extra.getJob(job),error=>error.code==='INVALID_CANONICAL_RESPONSE');
 const terminal=api.createTransport({{fetchImpl:async()=>response({{...base,state:'succeeded'}}),operationIdFactory:()=>operation}});
 await assert.rejects(()=>terminal.getJob(job),error=>error.code==='INVALID_CANONICAL_RESPONSE');
 const mismatch=api.createTransport({{fetchImpl:async()=>response({{operation_id:operation,request_id:request,resource_id:other,row_version:null,status:'cancelled'}}),operationIdFactory:()=>operation}});
 await assert.rejects(()=>mismatch.cancelJob({{jobId:job,operationId:operation}}),error=>error.code==='INVALID_CANONICAL_RESPONSE');
 await assert.rejects(()=>mismatch.getJob('../escape'),error=>error.code==='INVALID_CANONICAL_RESPONSE');
 console.log(JSON.stringify({{checks:4}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {"checks": 4}

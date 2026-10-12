(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.NotionIndependentDatabaseCells=api;
})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const CONTRACT='code_assist.notion.database_sharing.v1';
  const KIND='independent.database.cells.v1';
  const clone=value=>structuredClone(value);
  const editable=data=>['edit_content','edit','full_access'].includes(data?.role);
  const key=scope=>JSON.stringify([scope.realm,scope.userId,scope.workspaceId,scope.databaseId]);
  const journalScope=scope=>({realm:scope.realm,subject_id:scope.userId,
    workspace_id:scope.workspaceId,page_id:`independent-database-cells:${scope.databaseId}`});
  const failure=error=>[401,403,404].includes(error?.status)
    ? '표 접근 권한을 다시 확인해 주세요. 입력은 보관되어 있습니다.'
    :error?.status===409
    ? '다른 변경과 충돌했습니다. 보관한 입력을 확인해 주세요.'
    :error?.message||'셀 저장을 확인하지 못했습니다. 입력은 보관되어 있습니다.';

  function create(host){
    const states=new Map();
    function state(scope){
      const id=key(scope);
      if(!states.has(id))states.set(id,{scope,data:null,loaded:false,loading:null,
        busy:false,pending:[],error:''});
      return states.get(id);
    }
    function notify(s){if(host.current(s.scope))host.state?.(s.scope,{
      busy:s.busy,pending:s.pending.length,error:s.error,
      values:s.pending.flatMap(entry=>entry.request.cells.map(cell=>cell.value)),
    });}
    function validate(result,s,envelope){
      const request=envelope.request;
      if(result?.contract!==CONTRACT||result?.workspace_id!==s.scope.workspaceId
          ||result?.database_id!==s.scope.databaseId
          ||result?.operation_id!==envelope.operation_id
          ||result?.document_sequence!==request.expected_document_sequence+1
          ||Object.hasOwn(result,'document_id')||Object.hasOwn(result,'content_sha256')
          ||!Array.isArray(result.cells)||result.cells.length!==request.cells.length)
        throw Error('셀 저장 응답이 요청과 일치하지 않습니다. 같은 요청으로 다시 확인해 주세요.');
      const expected=new Map(request.cells.map(cell=>[
        JSON.stringify([cell.row_id,cell.property_id]),cell.expected_target_version,
      ]));
      for(const cell of result.cells){
        const cellKey=JSON.stringify([cell.row_id,cell.property_id]);
        const version=expected.get(cellKey);
        if(!expected.delete(cellKey)||cell.target_version!==version+1)
          throw Error('셀 저장 버전을 확인하지 못했습니다.');
      }
    }
    async function send(s){
      if(s.busy)throw Error('셀 저장을 확인 중입니다.');
      if(!host.current(s.scope)||!host.canEdit(s.scope)||!editable(s.data))
        throw Error('표 편집 권한을 확인할 수 없습니다.');
      const envelope=s.pending[0];if(!envelope)return false;
      s.busy=true;s.error='';notify(s);
      try{
        const result=await host.send(s.scope,clone(envelope.request));
        validate(result,s,envelope);
        await host.journal.remove(journalScope(s.scope),envelope.operation_id);
        s.pending.shift();
        try{host.acknowledged?.(s.scope,clone(envelope.request));}catch{}
        if(host.current(s.scope)){
          try{await host.changed(s.scope,result);}
          catch{s.error='저장은 완료됐지만 최신 화면을 불러오지 못했습니다. 표를 다시 열어 확인해 주세요.';}
        }
        return true;
      }catch(error){s.error=failure(error);throw error;}
      finally{s.busy=false;notify(s);host.settled?.(s.scope);}
    }
    async function bind(scope,data){
      const s=state(scope),reopened=s.scope!==scope;s.scope=scope;s.data=clone(data);
      if(s.loaded){
        notify(s);
        if(reopened&&s.pending.length&&!s.busy&&host.current(scope)
            &&host.canEdit(scope)&&editable(data)){
          try{await send(s);}catch{}
        }
        return;
      }
      if(s.loading)return s.loading;
      s.loading=(async()=>{
        try{
          const entries=await host.journal.list(journalScope(scope));
          if(!host.current(scope))return;
          s.pending=entries.filter(entry=>entry.kind===KIND
            &&entry.database_id===scope.databaseId
            &&entry.request?.operation_id===entry.operation_id);
          s.loaded=true;notify(s);
          if(s.pending.length&&host.canEdit(scope)&&editable(data))await send(s);
        }catch(error){s.error=failure(error);notify(s);}
      })();
      try{await s.loading;}finally{s.loading=null;}
    }
    async function change(scope,cell){
      const s=state(scope);
      if(s.loading)await s.loading;
      if(!s.loaded||s.busy||s.pending.length)
        throw Error('보관한 셀 저장 요청을 먼저 확인해 주세요.');
      if(!host.current(scope)||!host.canEdit(scope)||!editable(s.data))
        throw Error('표 편집 권한을 확인할 수 없습니다.');
      const row=s.data.database?.rows?.find(item=>item.id===cell.row_id);
      const property=s.data.database?.properties?.find(item=>item.id===cell.property_id);
      if(!row||!property||Number(row.cell_versions?.[cell.property_id]||0)!==cell.expected_target_version)
        throw Error('셀이 변경되었습니다. 최신 값을 확인해 주세요.');
      const operation=host.operationId();
      const request={cells:[clone(cell)],
        expected_document_sequence:s.data.document_sequence,
        expected_workspace_epoch:s.data.workspace_epoch,
        expected_document_epoch:s.data.document_epoch,
        expected_database_epoch:s.data.database_epoch,operation_id:operation};
      const envelope={kind:KIND,database_id:scope.databaseId,operation_id:operation,
        created_at:Date.now(),request};
      s.busy=true;notify(s);
      try{await host.journal.put(journalScope(scope),envelope);s.pending.push(envelope);}
      finally{s.busy=false;notify(s);host.settled?.(s.scope);}
      if(!host.current(scope))return false;
      return send(s);
    }
    async function discard(scope){
      const s=state(scope);if(s.busy)throw Error('저장 중에는 입력을 지울 수 없습니다.');
      const envelope=s.pending[0];if(!envelope)return;
      await host.journal.remove(journalScope(scope),envelope.operation_id);
      s.pending.shift();s.error='';notify(s);
    }
    return {bind,change,retry:scope=>send(state(scope)),discard,
      status:scope=>{const s=state(scope);return {ready:s.loaded,busy:s.busy,
        pending:s.pending.length,error:s.error,
        values:s.pending.flatMap(entry=>entry.request.cells.map(cell=>cell.value)),
        cells:s.pending.flatMap(entry=>entry.request.cells.map(clone))};}};
  }
  return {create,editable,journalScope,KIND};
});

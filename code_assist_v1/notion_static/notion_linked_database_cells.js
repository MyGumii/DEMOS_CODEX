(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionLinkedDatabaseCells=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const clone=value=>structuredClone(value),cellKey=cell=>JSON.stringify([cell.row_id,cell.property_id]);
  const editable=data=>['edit_content','edit','full_access'].includes(data?.database?.effective_role);
  function errorMessage(error){
    if([401,403,404].includes(error?.status))return '원본 또는 연결 보기의 편집 권한을 다시 확인해 주세요.';
    if(error?.status===409)return '다른 변경과 충돌했습니다. 최신 값을 확인한 뒤 다시 입력해 주세요.';
    if(error?.status>=500||/Failed to fetch|NetworkError|Load failed/i.test(error?.message||''))return '연결을 확인하지 못해 입력을 보관했습니다. 다시 시도해 주세요.';
    return error?.message||'셀 저장을 확인하지 못했습니다.';
  }
  function journalScope(scope,owned=false){return {realm:scope.realm,subject_id:scope.userId,workspace_id:scope.workspaceId,page_id:`${owned?'owned':'linked'}-cells:${scope.pageId}`};}
  function key(scope,block){return JSON.stringify([scope.realm,scope.userId,scope.workspaceId,scope.pageId,block]);}
  function create(host,owned=false){
    const journalFor=scope=>journalScope(scope,owned),kind=owned?'owned.cells.v1':'linked.cells.v1';
    const states=new Map();
    function state(scope,block){const id=key(scope,block);if(!states.has(id))states.set(id,{scope,block,data:null,loaded:false,busy:false,pending:[],undo:[],redo:[],versions:new Map(),applied:new Set()});return states.get(id);}
    const notify=s=>{if(host.current(s.scope))host.state?.(s.scope,s.block,{busy:s.busy,pending:s.pending.length,error:s.error||'',values:s.pending[0]?.request.cells.map(c=>c.value)||[]});};
    function checked(s){if(!host.current(s.scope)||!host.canEdit(s.scope)||!editable(s.data))throw Error('원본과 이 페이지를 편집할 권한을 확인해 주세요.');}
    function validate(result,s,envelope){
      const source=envelope.request.source,cells=result?.cells;
      const scoped=!owned&&Object.hasOwn(envelope.request,'source_access');
      if(owned&&(result?.operation_id!==envelope.operation_id||result?.target_id!==source.database_id
          ||result?.contract!=='code_assist.notion.collaborative_database_cell_batch.v1'
          ||result?.status!=='database_command_committed'
          ||result?.document_sequence!==envelope.request.source_witness.document_sequence+1))
        throw Error('셀 저장 결과가 요청과 일치하지 않습니다. 같은 요청으로 다시 확인해 주세요.');
      if(scoped&&(result?.contract!=='code_assist.notion.database_sharing.v1'
          ||result?.operation_id!==envelope.operation_id
          ||result?.document_sequence!==envelope.request.source_access.document_sequence+1
          ||Object.hasOwn(result||{},'document_id')||result?.linked_instance!=null
          ||Object.hasOwn(source,'document_id')))
        throw Error('셀 저장 결과가 요청과 일치하지 않습니다. 같은 요청으로 다시 확인해 주세요.');
      if(result?.database_id!==source.database_id||result?.workspace_id!==s.scope.workspaceId
          ||(!scoped&&(result?.document_id!==source.document_id
            ||(owned ? source.document_id!==s.scope.pageId || result?.linked_instance!=null
              : result?.linked_instance?.document_id!==s.scope.pageId||result?.linked_instance?.block_id!==s.block)
            ||result?.command_kind!=='database.cells.set'))
          ||!Array.isArray(cells)||cells.length!==envelope.request.cells.length)
        throw Error('셀 저장 응답을 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.');
      const expected=new Map(envelope.request.cells.map(cell=>[cellKey(cell),cell.expected_target_version]));
      for(const cell of cells){
        const version=expected.get(cellKey(cell));
        if(!expected.delete(cellKey(cell))||!Number.isInteger(cell.target_version)||cell.target_version<1
            ||(owned||scoped)&&cell.target_version!==version+1)throw Error('셀 저장 버전을 확인하지 못했습니다.');
      }
    }
    async function send(s){
      if(s.busy)throw Error('셀 저장을 확인하고 있습니다.');
      checked(s);const envelope=s.pending[0];if(!envelope)return false;
      if(envelope.request.source.document_id!==s.data.source.document_id
          ||envelope.request.source.database_id!==s.data.source.database_id
          ||(!owned&&Object.hasOwn(envelope.request,'source_access')!==Boolean(s.data.access&&s.data.instance_witness)))
        throw Error('연결 원본이 변경되어 보관한 입력을 자동 저장할 수 없습니다.');
      const scope=s.scope;s.busy=true;s.error='';notify(s);
      try{
        const result=await host.send(scope,s.block,clone(envelope.request));validate(result,s,envelope);
        await host.journal.remove(journalFor(scope),envelope.operation_id);
        s.pending.shift();
        if(!s.applied.has(envelope.operation_id)){
          s.applied.add(envelope.operation_id);
          result.cells.forEach(cell=>s.versions.set(cellKey(cell),cell.target_version));
          if(envelope.direction<0){s.undo.pop();s.redo.push(envelope.entry);}
          else if(envelope.direction>0){s.redo.pop();s.undo.push(envelope.entry);}
          else{s.undo.push({before:envelope.before,after:envelope.request.cells.map(({expected_target_version,...cell})=>cell)});s.redo=[];if(s.undo.length>100)s.undo.shift();}
        }
        if(host.current(scope))await host.changed(scope,s.block,result,clone(envelope.request.cells));
        return true;
      }catch(error){s.error=errorMessage(error);throw error;}
      finally{s.busy=false;notify(s);}
    }
    async function bind(scope,block,data){
      const s=state(scope,block);s.scope=scope;s.data=clone(data);
      if(s.loaded){notify(s);return;}
      if(s.loading)return s.loading;
      s.loading=(async()=>{
        try{
          const entries=await host.journal.list(journalFor(scope));
          if(!host.current(scope))return;
          s.pending=entries.filter(e=>e.kind===kind&&e.block_id===block&&e.request?.operation_id===e.operation_id);
          s.loaded=true;notify(s);
          if(s.pending.length&&host.canEdit(scope)&&editable(data))await send(s);
        }catch(error){s.error=errorMessage(error);notify(s);}
      })();
      try{await s.loading;}finally{s.loading=null;}
    }
    async function change(scope,block,cells,direction=0){
      const s=state(scope,block);if(s.loading)await s.loading;
      checked(s);if(!s.loaded||s.busy||s.pending.length)throw Error('보관한 셀 저장 요청을 먼저 확인해 주세요.');
      const entry=direction<0?s.undo.at(-1):direction>0?s.redo.at(-1):null;
      if(direction&&!entry)return false;
      const requested=direction?(direction<0?entry.before:entry.after).map(cell=>({...clone(cell),expected_target_version:s.versions.get(cellKey(cell))})):clone(cells);
      if(!Array.isArray(requested)||!requested.length||requested.length>1000)throw Error('한 번에 1~1000개 셀을 변경할 수 있습니다.');
      const before=requested.map(cell=>{
        const row=s.data.database.rows.find(r=>r.id===cell.row_id);
        if(!row||Number(row.cell_versions?.[cell.property_id]||0)!==cell.expected_target_version)throw Error('셀이 변경되었습니다. 최신 값을 확인해 주세요.');
        return {row_id:cell.row_id,property_id:cell.property_id,value:clone(row.cells?.[cell.property_id]??null)};
      });
      const operation=host.operationId(),scoped=!owned&&Boolean(s.data.access&&s.data.instance_witness);
      const witness=document=>clone(s.data.witnesses.find(w=>w.document_id===document));
      const envelope={kind,operation_id:operation,created_at:Date.now(),block_id:block,before,direction,entry:clone(entry),
        request:scoped
          ?{source:clone(s.data.source),instance_witness:clone(s.data.instance_witness),source_access:clone(s.data.access),cells:requested,operation_id:operation}
          :{source:clone(s.data.source),instance_witness:witness(scope.pageId),source_witness:witness(s.data.source.document_id),cells:requested,operation_id:operation}};
      // Persist before sending; a lost response must retry the exact same body/ID.
      s.busy=true;notify(s);
      try{await host.journal.put(journalFor(scope),envelope);s.pending.push(envelope);}
      finally{s.busy=false;notify(s);}
      if(!host.current(scope))return false;
      return send(s);
    }
    async function discard(scope,block){const s=state(scope,block);if(s.busy)throw Error('저장 중에는 입력을 지울 수 없습니다.');const entry=s.pending[0];if(!entry)return;await host.journal.remove(journalFor(scope),entry.operation_id);s.pending.shift();s.error='';notify(s);if(host.current(scope))await host.changed(scope,block);}
    return {bind,change,undo:(scope,block)=>change(scope,block,null,-1),redo:(scope,block)=>change(scope,block,null,1),retry:(scope,block)=>send(state(scope,block)),discard,
      status:(scope,block)=>{const s=state(scope,block);return {busy:s.busy,pending:s.pending.length,ready:s.loaded,
        canUndo:s.loaded&&!s.busy&&!s.pending.length&&s.undo.length>0};}};
  }
  return {create,createOwned:host=>create(host,true),journalScope,editable,errorMessage};
});

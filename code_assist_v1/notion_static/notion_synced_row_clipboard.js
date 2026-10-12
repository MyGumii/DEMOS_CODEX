(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionSyncedRowClipboard=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const CONTRACT='code_assist.notion.synced_row_clipboard.v1',PREFIX='notion.synced-row-paste.v1:';
  const clone=value=>structuredClone(value);
  const exact=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
  const id=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(value);
  const positive=value=>Number.isSafeInteger(value)&&value>0;
  const requestKeys=['source','anchor_id','replace_empty','expected_target_version','expected_workspace_epoch','expected_document_epoch','expected_database_epoch','operation_id'];
  const invalid=()=>Error('보관한 동기화 붙여넣기 요청을 확인할 수 없습니다.');
  function validRequest(value){
    return exact(value,requestKeys)&&exact(value.source,['workspace_id','document_id','block_id'])&&Object.values(value.source).every(id)
      &&(value.anchor_id===null||id(value.anchor_id))&&typeof value.replace_empty==='boolean'&&(!value.replace_empty||value.anchor_id!==null)
      &&['expected_target_version','expected_workspace_epoch','expected_document_epoch'].every(key=>positive(value[key]))
      &&(value.expected_database_epoch===null||positive(value.expected_database_epoch))&&id(value.operation_id);
  }
  function key(scope){
    if(typeof scope.realm!=='string'||scope.realm.length>512||!['userId','workspaceId','pageId','databaseId','rowId'].every(field=>id(scope[field])))throw invalid();
    try{if(new URL(scope.realm).origin!==scope.realm)throw invalid();}catch{throw invalid();}
    return PREFIX+['realm','userId','workspaceId','pageId','databaseId','rowId'].map(field=>encodeURIComponent(scope[field])).join(':');
  }
  function validResult(result,scope,request){
    if(result?.contract!==CONTRACT||result.workspace_id!==scope.workspaceId||result.document_id!==scope.pageId
        ||result.database_id!==scope.databaseId||result.row_id!==scope.rowId||result.operation_id!==request.operation_id
        ||typeof result.replayed!=='boolean'||!id(result.reference_id)||result.target_version!==request.expected_target_version+1
        ||!positive(result.document_sequence)||!['workspace_id','document_id','block_id'].every(field=>result.source?.[field]===request.source[field])
        ||!exact(result.location,['kind','database_id','row_id'])||result.location.kind!=='row'
        ||result.location.database_id!==scope.databaseId||result.location.row_id!==scope.rowId)throw Error('동기화 붙여넣기 결과가 대상 항목과 일치하지 않습니다.');
    return result;
  }
  function create(scope,host){
    scope={...scope};const identity=key(scope);let flight=null,committing=false,error='';
    const storage=()=>{const value=host.storage();if(!value)throw invalid();return value;};
    function read(){
      const raw=storage().getItem(identity);if(raw===null)return null;
      if(raw.length>8192)throw invalid();let record;try{record=JSON.parse(raw);}catch{throw invalid();}
      if(!exact(record,['version','scope','request','confirmed'])||record.version!==1||typeof record.confirmed!=='boolean'
          ||JSON.stringify(record.scope)!==JSON.stringify(scope)||!validRequest(record.request))throw invalid();
      return record;
    }
    function notify(){try{host.changed?.();}catch{/* A repaint does not change the receipt. */}}
    function save(record){
      const raw=JSON.stringify(record),value=storage();value.setItem(identity,raw);
      if(value.getItem(identity)!==raw)throw Error('붙여넣기 요청을 보관하지 못했습니다.');notify();
    }
    function remove(){const value=storage();value.removeItem(identity);if(value.getItem(identity)!==null)throw invalid();notify();}
    function snapshot(){try{return {pending:Boolean(read()),busy:committing,error};}catch(problem){return {pending:true,busy:committing,error:problem.message};}}
    const live=hooks=>{if(!host.current()||!hooks.current())throw Error('붙여넣을 항목이나 입력 상태가 바뀌었습니다.');};
    const base=`/api/notion/workspaces/${encodeURIComponent(scope.workspaceId)}/pages/${encodeURIComponent(scope.pageId)}/databases/${encodeURIComponent(scope.databaseId)}/rows/${encodeURIComponent(scope.rowId)}`;
    function run(hooks){
      if(flight)return flight;
      flight=Promise.resolve().then(async()=>{
        const locks=host.locks();if(!locks?.request)throw Error('이 브라우저에서 붙여넣기 저장을 조정할 수 없습니다.');
        return locks.request(identity,{mode:'exclusive',ifAvailable:true},async lock=>{
          if(!lock)return {state:'busy'};
          live(hooks);let record=read();
          const committed=result=>{validResult(result,scope,record.request);record={...record,confirmed:true};save(record);return {state:'committed',result};};
          const lookup=async()=>{
            const status=await host.transport(base+'/synced-paste-result',{method:'POST',body:clone(record.request),cache:'no-store'});
            if(status?.state==='committed')return committed(status.result);
            if(status?.state!=='unknown'||record.confirmed)throw invalid();return null;
          };
          if(record){const known=await lookup();if(known)return known;live(hooks);if(!host.editable())throw Error('항목 편집 권한과 입력 상태를 확인해 주세요.');}
          else{
            if(!host.editable())throw Error('항목 편집 권한과 입력 상태를 확인해 주세요.');
            const request=await hooks.prepare();live(hooks);
            if(!host.editable()||!validRequest(request))throw invalid();
            record={version:1,scope,request:clone(request),confirmed:false};
            // Scope/target/input are checked before durable intent becomes a
            // blocking operation. No source body is retained in this journal.
            committing=true;save(record);
          }
          live(hooks);committing=true;notify();
          try{return committed(await host.transport(base+'/synced-paste',{method:'POST',body:clone(record.request),cache:'no-store'}));}
          catch(problem){
            if(problem?.status!==409||problem?.data?.code!=='synced_source_changed')throw problem;
            const known=await lookup();if(known)return known;remove();return {state:'stale'};
          }
        });
      }).catch(problem=>{error=problem.message;throw problem;}).finally(()=>{flight=null;committing=false;notify();});
      error='';return flight;
    }
    async function acknowledge(operationId){
      if(flight)return false;
      return host.locks().request(identity,{mode:'exclusive',ifAvailable:true},lock=>{
        if(!lock)return false;const record=read();if(!record?.confirmed||record.request.operation_id!==operationId)return false;
        remove();error='';return true;
      });
    }
    return {run,acknowledge,snapshot,pending:()=>snapshot().pending||committing};
  }
  return {CONTRACT,PREFIX,key,validRequest,validResult,create};
});

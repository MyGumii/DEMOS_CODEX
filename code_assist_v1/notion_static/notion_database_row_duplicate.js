(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionDatabaseRowDuplicate=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const kind='owned.row.duplicate.v1',contract='code_assist.notion.collaborative_database_row_duplicate_batch.v1';
  const copy=value=>structuredClone(value);
  const identity=(scope,database)=>JSON.stringify([scope.realm,scope.userId,scope.workspaceId,scope.pageId,database]);
  const journalScope=scope=>({realm:scope.realm,subject_id:scope.userId,workspace_id:scope.workspaceId,
    page_id:`row-duplicate:${scope.pageId}`});
  function valid(entry,scope,database){
    const body=entry?.request,rows=body?.payload?.rows;
    return entry?.kind===kind&&entry.operation_id===body?.operation_id
      &&typeof entry.operation_id==='string'&&entry.operation_id.length>0&&entry.operation_id.length<=200
      &&entry.identity===identity(scope,database)&&body?.database_id===database
      &&body?.target_id===database&&body?.command_kind==='database.rows.duplicate'
      &&Object.keys(body).sort().join(',')==='command_kind,database_id,expected_document_epoch,expected_document_sequence,expected_target_version,expected_workspace_epoch,operation_id,payload,target_id'
      &&body.expected_target_version===null
      &&Object.keys(body.payload||{}).sort().join(',')==='rows'
      &&Array.isArray(rows)&&rows.length>=1&&rows.length<=100
      &&rows.every(row=>row&&typeof row.row_id==='string'&&row.row_id
        &&Object.keys(row).sort().join(',')==='expected_target_version,row_id'
        &&Number.isSafeInteger(row.expected_target_version)&&row.expected_target_version>0)
      &&new Set(rows.map(row=>row.row_id)).size===rows.length
      &&['expected_document_sequence','expected_workspace_epoch','expected_document_epoch']
        .every(field=>Number.isSafeInteger(body[field])&&body[field]>0);
  }
  function verify(entry,result,scope,database){
    const request=entry.request,requested=new Set(request.payload.rows.map(row=>row.row_id));
    if(result?.contract!==contract||result.status!=='database_command_committed'
        ||result.operation_id!==entry.operation_id||result.workspace_id!==scope.workspaceId
        ||result.document_id!==scope.pageId||result.database_id!==database
        ||result.target_id!==database||result.command_kind!=='database.rows.duplicate'
        ||result.document_sequence!==request.expected_document_sequence+1
        ||!Array.isArray(result.rows)||result.rows.length!==requested.size
        ||result.rows.some(item=>!requested.has(item.source_row_id)
          ||typeof item.row_id!=='string'||!item.row_id||requested.has(item.row_id)
          ||item.target_version!==1||!Number.isSafeInteger(item.position)||item.position<0)
        ||new Set(result.rows.map(item=>item.source_row_id)).size!==requested.size
        ||new Set(result.rows.map(item=>item.row_id)).size!==requested.size)
      throw Error('복제 결과가 요청과 일치하지 않습니다. 같은 요청으로 다시 확인해 주세요.');
    return result;
  }
  function create(host){
    const flights=new Map();
    async function pending(scope,database){
      const entries=(await host.journal.list(journalScope(scope)))
        .filter(entry=>entry?.kind===kind&&entry.identity===identity(scope,database));
      if(entries.length>1||entries.some(entry=>!valid(entry,scope,database)))
        throw Error('보관한 복제 요청을 확인할 수 없습니다.');
      return entries[0]||null;
    }
    async function send(scope,database,entry,block){
      if(!host.current(scope)||!host.canEdit(scope))throw Error('열린 페이지나 편집 권한이 변경되었습니다.');
      const result=verify(entry,await host.send(scope,copy(entry.request)),scope,database);
      await host.journal.remove(journalScope(scope),entry.operation_id);
      if(host.current(scope))await host.changed(scope,block,result);
      return result;
    }
    async function run(scope,block,data,rowIds){
      const database=data?.source?.database_id,key=identity(scope,database);
      if(flights.has(key))throw Error('선택한 행을 복제하고 있습니다.');
      const flight=(async()=>{
        if(!host.current(scope)||!host.canEdit(scope)||!database
            ||!data.database?.capabilities?.row_duplicate_batch
            ||!['edit_content','edit','full_access'].includes(data.database.effective_role))
          throw Error('선택한 행을 복제할 수 없습니다.');
        const older=await pending(scope,database);
        if(older){
          await send(scope,database,older,block);
          if(JSON.stringify(older.request.payload.rows.map(row=>row.row_id))===JSON.stringify(rowIds))return true;
          throw Error('이전 복제 결과를 확인했습니다. 최신 화면에서 다시 선택해 주세요.');
        }
        if(!Array.isArray(rowIds)||!rowIds.length||rowIds.length>100
            ||new Set(rowIds).size!==rowIds.length)throw Error('선택한 행을 확인할 수 없습니다.');
        const witness=data.witnesses?.find(item=>item.document_id===scope.pageId);
        const rows=rowIds.map(id=>data.database.rows?.find(row=>row.id===id&&!row.archived));
        if(!witness||rows.some(row=>!row))throw Error('선택한 행의 최신 상태를 확인할 수 없습니다.');
        const operation=host.operationId();
        const entry={kind,identity:key,operation_id:operation,request:{
          database_id:database,target_id:database,command_kind:'database.rows.duplicate',
          payload:{rows:rows.map(row=>({row_id:row.id,expected_target_version:row.version}))},
          expected_document_sequence:witness.document_sequence,
          expected_workspace_epoch:witness.workspace_epoch,
          expected_document_epoch:witness.document_epoch,
          expected_target_version:null,operation_id:operation}};
        if(!valid(entry,scope,database))throw Error('복제 요청을 확인할 수 없습니다.');
        await host.journal.put(journalScope(scope),entry);
        await send(scope,database,entry,block);
        return true;
      })();
      flights.set(key,flight);
      try{return await flight;}finally{if(flights.get(key)===flight)flights.delete(key);}
    }
    return {run,pending};
  }
  return {create,journalScope,valid,verify};
});

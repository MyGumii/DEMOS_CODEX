(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionDatabaseRowToPage=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const kind='owned.row.move_to_page.v1';
  const contract='code_assist.notion.collaborative_database_row_to_page_batch.v1';
  const copy=value=>structuredClone(value);
  const positive=value=>Number.isSafeInteger(value)&&value>0;
  const identity=(scope,database)=>JSON.stringify([scope.realm,scope.userId,scope.workspaceId,scope.pageId,database]);
  const journalScope=scope=>({realm:scope.realm,subject_id:scope.userId,
    workspace_id:scope.workspaceId,page_id:`row-to-page:${scope.pageId}`});
  function valid(entry,scope,source){
    const body=entry?.request,payload=body?.payload,rows=payload?.rows;
    return entry?.kind===kind&&entry.identity===identity(scope,source)
      &&entry.operation_id===body?.operation_id&&typeof entry.operation_id==='string'
      &&entry.operation_id.length>0&&entry.operation_id.length<=200
      &&body?.database_id===source&&body.target_id===source
      &&body.command_kind==='database.rows.move_to_page'
      &&Object.keys(body).sort().join(',')==='command_kind,database_id,expected_document_epoch,expected_document_sequence,expected_target_version,expected_workspace_epoch,operation_id,payload,target_id'
      &&body.expected_target_version===null
      &&Object.keys(payload||{}).sort().join(',')==='expected_parent_epoch,expected_parent_sequence,parent_document_id,rows'
      &&typeof payload.parent_document_id==='string'&&payload.parent_document_id
      &&Array.isArray(rows)&&rows.length>=1&&rows.length<=100
      &&rows.every(row=>row&&typeof row.row_id==='string'&&row.row_id
        &&Object.keys(row).sort().join(',')==='expected_target_version,row_id'
        &&positive(row.expected_target_version))
      &&new Set(rows.map(row=>row.row_id)).size===rows.length
      &&['expected_document_sequence','expected_workspace_epoch','expected_document_epoch']
        .every(field=>positive(body[field]))
      &&positive(payload.expected_parent_sequence)&&positive(payload.expected_parent_epoch)
      &&(payload.parent_document_id!==scope.pageId
        ||(body.expected_document_sequence===payload.expected_parent_sequence
          &&body.expected_document_epoch===payload.expected_parent_epoch));
  }
  function verify(entry,result,scope,source){
    const body=entry.request,payload=body.payload;
    const versions=new Map(payload.rows.map(row=>[row.row_id,row.expected_target_version]));
    const same=payload.parent_document_id===scope.pageId;
    const sourceSequence=body.expected_document_sequence+(same?versions.size+2:1);
    const parentSequence=payload.expected_parent_sequence+(same?versions.size+2:versions.size+1);
    if(result?.contract!==contract||result.status!=='database_command_committed'
        ||result.operation_id!==entry.operation_id||result.workspace_id!==scope.workspaceId
        ||result.source_document_id!==scope.pageId||result.source_database_id!==source
        ||result.parent_document_id!==payload.parent_document_id
        ||result.command_kind!=='database.rows.move_to_page'
        ||result.document_sequence!==sourceSequence
        ||result.parent_document_sequence!==parentSequence
        ||!Array.isArray(result.rows)||result.rows.length!==versions.size
        ||result.rows.some(row=>!versions.has(row.row_id)||row.page_id!==row.row_id
          ||row.target_version!==versions.get(row.row_id)+1)
        ||new Set(result.rows.map(row=>row.row_id)).size!==versions.size)
      throw Error('페이지 이동 결과가 요청과 일치하지 않습니다. 같은 요청으로 다시 확인해 주세요.');
    return result;
  }
  function create(host){
    const flights=new Map();
    async function pendingAll(scope){
      const entries=(await host.journal.list(journalScope(scope))).filter(entry=>entry?.kind===kind);
      const sources=new Set();
      for(const entry of entries){
        const source=entry.request?.database_id;
        if(!valid(entry,scope,source)||sources.has(source))
          throw Error('보관한 페이지 이동 요청을 확인할 수 없습니다.');
        sources.add(source);
      }
      return entries;
    }
    async function pending(scope,source){
      return (await pendingAll(scope)).find(entry=>entry.request.database_id===source)||null;
    }
    async function send(scope,source,entry,block){
      if(!host.current(scope)||!host.canEdit(scope))throw Error('열린 페이지나 편집 권한이 변경되었습니다.');
      const result=verify(entry,await host.send(scope,copy(entry.request)),scope,source);
      await host.journal.remove(journalScope(scope),entry.operation_id);
      if(host.current(scope))await host.changed(scope,block,result);
      return result;
    }
    async function run(scope,block,sourceData,parentData,rowIds){
      const source=sourceData?.source?.database_id;
      const parentId=parentData?.page?.id;
      const key=identity(scope,source);
      if(flights.has(key))throw Error('선택한 행을 옮기고 있습니다.');
      const flight=(async()=>{
        if(!host.current(scope)||!host.canEdit(scope)||!source||!parentId
            ||sourceData.source.document_id!==scope.pageId
            ||!sourceData.database?.capabilities?.row_move_batch
            ||!['edit_content','edit','full_access'].includes(sourceData.database.effective_role)
            ||parentData.page.workspace_id!==scope.workspaceId||!parentData.page.can_edit)
          throw Error('선택한 행을 이 페이지로 옮길 수 없습니다.');
        const older=await pending(scope,source);
        if(older){
          await send(scope,source,older,block);
          if(older.request.payload.parent_document_id===parentId
              &&JSON.stringify(older.request.payload.rows.map(row=>row.row_id))===JSON.stringify(rowIds))return true;
          throw Error('이전 이동 결과를 확인했습니다. 최신 화면에서 다시 선택해 주세요.');
        }
        if(!Array.isArray(rowIds)||!rowIds.length||rowIds.length>100
            ||new Set(rowIds).size!==rowIds.length)throw Error('선택한 행을 확인할 수 없습니다.');
        const sourceWitness=sourceData.witnesses?.find(item=>item.document_id===scope.pageId);
        const parentWitness=parentData.witnesses?.find(item=>item.document_id===parentId);
        const rows=rowIds.map(id=>sourceData.database.rows?.find(row=>row.id===id&&!row.archived));
        if(!sourceWitness||!parentWitness||rows.some(row=>!row)
            ||sourceWitness.workspace_epoch!==parentWitness.workspace_epoch)
          throw Error('선택한 행의 최신 상태를 확인할 수 없습니다.');
        const operation=host.operationId();
        const entry={kind,identity:key,operation_id:operation,request:{
          database_id:source,target_id:source,command_kind:'database.rows.move_to_page',
          payload:{parent_document_id:parentId,
            expected_parent_sequence:parentWitness.document_sequence,
            expected_parent_epoch:parentWitness.document_epoch,
            rows:rows.map(row=>({row_id:row.id,expected_target_version:row.version}))},
          expected_document_sequence:sourceWitness.document_sequence,
          expected_workspace_epoch:sourceWitness.workspace_epoch,
          expected_document_epoch:sourceWitness.document_epoch,
          expected_target_version:null,operation_id:operation}};
        if(!valid(entry,scope,source))throw Error('이동 요청의 문서 상태를 확인할 수 없습니다.');
        await host.journal.put(journalScope(scope),entry);
        await send(scope,source,entry,block);
        return true;
      })();
      flights.set(key,flight);
      try{return await flight;}finally{if(flights.get(key)===flight)flights.delete(key);}
    }
    async function recover(scope){
      const entries=await pendingAll(scope);
      if(!entries.length)return 0;
      if(!host.current(scope)||!host.canEdit(scope))
        throw Error('현재 페이지나 편집 권한이 변경되어 이동 결과를 확인할 수 없습니다.');
      for(const entry of entries){
        const source=entry.request.database_id,key=identity(scope,source);
        if(flights.has(key))await flights.get(key);
        else{
          const flight=send(scope,source,entry,null);
          flights.set(key,flight);
          try{await flight;}finally{if(flights.get(key)===flight)flights.delete(key);}
        }
      }
      return entries.length;
    }
    return {run,pending,pendingAll,recover};
  }
  return {create,journalScope,valid,verify};
});

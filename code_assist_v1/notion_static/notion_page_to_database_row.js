(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionPageToDatabaseRow=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const kind='page.return_to_database.v1';
  const targetContract='code_assist.notion.page_to_database_row_target.v1';
  const contract='code_assist.notion.collaborative_page_to_database_row.v1';
  const fields=['destination_database_id','destination_document_id',
    'expected_destination_epoch','expected_destination_sequence',
    'expected_page_epoch','expected_page_sequence','expected_parent_epoch',
    'expected_parent_sequence','expected_row_version','expected_workspace_epoch',
    'operation_id'];
  const positive=value=>Number.isSafeInteger(value)&&value>0;
  const identity=(scope,page)=>JSON.stringify([scope.realm,scope.userId,scope.workspaceId,page]);
  const journalScope=scope=>({realm:scope.realm,subject_id:scope.userId,
    workspace_id:scope.workspaceId,page_id:`page-to-row:${scope.workspaceId}`});
  const copy=value=>structuredClone(value);
  function valid(entry,scope){
    const body=entry?.request;
    return entry?.kind===kind&&typeof entry.row_page_id==='string'&&entry.row_page_id
      &&entry.identity===identity(scope,entry.row_page_id)
      &&entry.operation_id===body?.operation_id
      &&typeof entry.operation_id==='string'&&entry.operation_id.length>0
      &&entry.operation_id.length<=200
      &&Object.keys(body).sort().join(',')===fields.slice().sort().join(',')
      &&typeof body.destination_document_id==='string'&&body.destination_document_id
      &&typeof body.destination_database_id==='string'&&body.destination_database_id
      &&typeof entry.parent_document_id==='string'
      &&['expected_destination_epoch','expected_destination_sequence',
        'expected_page_epoch','expected_page_sequence',
        'expected_row_version','expected_workspace_epoch']
        .every(field=>positive(body[field]))
      &&(entry.parent_document_id
        ?positive(body.expected_parent_epoch)&&positive(body.expected_parent_sequence)
        :body.expected_parent_epoch===0&&body.expected_parent_sequence===0);
  }
  function verify(entry,result,scope){
    const body=entry.request,same=entry.parent_document_id===body.destination_document_id;
    const destinationDelta=result?.document_sequence-body.expected_destination_sequence;
    const parentDelta=result?.parent_document_sequence-body.expected_parent_sequence;
    if(result?.contract!==contract||result.status!=='page_returned_to_database'
        ||result.operation_id!==entry.operation_id||result.workspace_id!==scope.workspaceId
        ||result.page_id!==entry.row_page_id||result.row_id!==entry.row_page_id
        ||result.parent_document_id!==entry.parent_document_id
        ||result.destination_document_id!==body.destination_document_id
        ||result.destination_database_id!==body.destination_database_id
        ||result.target_version!==body.expected_row_version+1
        ||(same?(![2,3].includes(destinationDelta)
          ||result.parent_document_sequence!==result.document_sequence)
          :destinationDelta!==1)
        ||(entry.parent_document_id
          ?!(same?parentDelta===destinationDelta:[1,2].includes(parentDelta))
          :result.parent_document_sequence!==0))
      throw Error('데이터베이스 복귀 결과가 요청과 일치하지 않습니다. 같은 요청으로 다시 확인해 주세요.');
    return result;
  }
  function create(host){
    const flights=new Map();
    async function pendingAll(scope){
      const entries=(await host.journal.list(journalScope(scope))).filter(entry=>entry?.kind===kind);
      const pages=new Set();
      for(const entry of entries){
        if(!valid(entry,scope)||pages.has(entry.row_page_id))
          throw Error('보관한 데이터베이스 복귀 요청을 확인할 수 없습니다.');
        pages.add(entry.row_page_id);
      }
      return entries;
    }
    async function send(scope,entry){
      if(!host.current(scope)||!host.canEdit(scope))
        throw Error('워크스페이스나 편집 권한이 변경되었습니다.');
      const result=verify(entry,await host.send(scope,entry.row_page_id,copy(entry.request)),scope);
      await host.journal.remove(journalScope(scope),entry.operation_id);
      if(host.current(scope))await host.changed(scope,entry.row_page_id,result);
      return result;
    }
    async function run(scope,target){
      if(target?.contract!==targetContract||target.workspace_id!==scope.workspaceId
          ||!target.page_id||target.row_id!==target.page_id
          ||typeof target.parent_document_id!=='string')
        throw Error('복귀할 데이터베이스를 확인할 수 없습니다.');
      const page=target.page_id,key=identity(scope,page);
      if(flights.has(key))throw Error('페이지를 데이터베이스로 옮기고 있습니다.');
      const flight=(async()=>{
        const older=(await pendingAll(scope)).find(entry=>entry.row_page_id===page);
        if(older)return send(scope,older);
        const operation=host.operationId();
        const request={operation_id:operation};
        for(const field of fields)if(field!=='operation_id')request[field]=target[field];
        const entry={kind,identity:key,row_page_id:page,
          parent_document_id:target.parent_document_id,
          operation_id:operation,request};
        if(!valid(entry,scope))throw Error('복귀 요청의 최신 문서 상태를 확인할 수 없습니다.');
        await host.journal.put(journalScope(scope),entry);
        return send(scope,entry);
      })();
      flights.set(key,flight);
      try{return await flight;}finally{if(flights.get(key)===flight)flights.delete(key);}
    }
    async function recover(scope){
      const entries=await pendingAll(scope);
      if(!host.current(scope)||!host.canEdit(scope))
        throw Error('워크스페이스나 편집 권한이 변경되어 복귀 결과를 확인할 수 없습니다.');
      for(const entry of entries){
        const key=identity(scope,entry.row_page_id);
        if(flights.has(key))await flights.get(key);
        else{
          const flight=send(scope,entry);
          flights.set(key,flight);
          try{await flight;}finally{if(flights.get(key)===flight)flights.delete(key);}
        }
      }
      return entries.length;
    }
    return {run,recover,pendingAll};
  }
  return {create,journalScope,valid,verify};
});

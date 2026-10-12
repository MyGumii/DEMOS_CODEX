(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionSyncedOwnerScope=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const key=(scope,block,data)=>JSON.stringify([scope.realm,scope.userId,scope.workspaceId,scope.pageId,block,data.source?.database_id,
    data.access?.workspace_epoch,data.access?.document_epoch,data.access?.database_epoch,data.database?.effective_role]);
  function create(host){
    const records=new Map();let generation=0;
    function get(scope,block,data){
      if(!host.current(scope))return null;
      const document=String(data.source?.document_id||'');
      if(document){
        const witness=data.witnesses?.find(item=>item.document_id===document);
        const workspace=witness?.workspace_id||(document===scope.pageId?scope.workspaceId:'');
        return workspace?{phase:'ready',documentId:document,workspaceId:workspace}:null;
      }
      if(!data.source?.database_id||!data.access)return null;
      for(const [identity,record] of records)if(!host.current(record.scope)){record.controller?.abort();records.delete(identity);}
      const identity=key(scope,block,data),old=records.get(identity);
      if(old?.scope===scope)return old;
      if(old){old.controller?.abort();records.delete(identity);}
      const record={scope,phase:'loading',controller:new AbortController()};records.set(identity,record);
      record.promise=(async()=>{
        try{
          const result=await host.resolve(scope,data.source.database_id,record.controller.signal);
          if(!host.current(scope)||records.get(identity)!==record)return;
          if(result?.workspace_id!==scope.workspaceId||result.source?.id!==data.source.database_id
              ||typeof result.source.document_id!=='string'||!result.source.document_id||result.source.document_id.length>160){record.phase='unavailable';}
          else{record.phase='ready';record.documentId=result.source.document_id;record.workspaceId=scope.workspaceId;}
        }catch{if(host.current(scope)&&records.get(identity)===record)record.phase='unavailable';}
        finally{if(host.current(scope)&&records.get(identity)===record)host.changed(scope,block);}
      })();
      return record;
    }
    function clear(){generation++;for(const record of records.values())record.controller?.abort();records.clear();}
    async function wait(scope,block,data){
      const started=generation,record=get(scope,block,data);if(record?.promise)await record.promise;
      if(started!==generation||!host.current(scope)||record?.phase!=='ready')return null;
      if(record.promise&&records.get(key(scope,block,data))!==record)return null;
      return record;
    }
    return {get,wait,clear};
  }
  return {create,key};
});

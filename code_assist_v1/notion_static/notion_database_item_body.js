(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionDatabaseItemBody=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const clone=value=>structuredClone(value);
  const stable=value=>value&&typeof value==='object'?(Array.isArray(value)?`[${value.map(stable).join(',')}]`:`{${Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+stable(value[key])).join(',')}}`):JSON.stringify(value);
  const normalize=blocks=>(blocks||[]).map((block,position)=>({id:block.id,position,type:block.type||'paragraph',content:String(block.content??''),props:clone(block.props||{})}));
  const equal=(a,b)=>stable(a)===stable(b);
  const identity=(scope,database,row)=>JSON.stringify([scope.realm,scope.userId,scope.workspaceId,scope.pageId,database,row]);
  async function journalScope(scope,database,row,cryptoApi=globalThis.crypto){
    const raw=new TextEncoder().encode(JSON.stringify([scope.pageId,database,row]));
    const digest=await cryptoApi.subtle.digest('SHA-256',raw);
    return {realm:scope.realm,subject_id:scope.userId,workspace_id:scope.workspaceId,
      page_id:'item-body-'+[...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('')};
  }
  function create(host){
    const states=new Map();
    const current=s=>host.current(s.scope);
    const editable=s=>current(s)&&host.canEdit(s.scope)&&s.data?.database?.capabilities?.row_content===true
      &&['edit_content','edit','full_access'].includes(s.data.database.effective_role)&&Boolean(s.server);
    const state=(scope,database,row)=>states.get(identity(scope,database,row));
    const view=s=>s?{blocks:clone(s.draft??s.base),ready:s.loaded,busy:Boolean(s.flight||s.discarding),dirty:s.draft!==null,
      pending:Boolean(s.request),composing:s.composing,editable:editable(s)&&!s.discarding,error:s.error||s.storageError||'',
      conflict:s.draft!==null&&s.server?.version!==s.baseVersion,version:s.baseVersion}:null;
    const notify=s=>{if(current(s))host.state?.(s.scope,s.database,s.row,view(s));};
    function persist(s){
      const entry=s.draft===null?null:{kind:'owned.body.draft.v1',operation_id:'draft',created_at:Date.now(),
        database_id:s.database,row_id:s.row,blocks:clone(s.draft),base_blocks:clone(s.base),base_version:s.baseVersion};
      s.persist=s.persist.catch(()=>{}).then(async()=>{
        if(entry)await host.journal.put(s.journal,entry);else await host.journal.remove(s.journal,'draft');
        s.storageError='';
      }).catch(error=>{s.storageError='본문을 이 브라우저에 보관하지 못했습니다. 연결과 저장 공간을 확인해 주세요.';notify(s);throw error;});
      s.persist.catch(()=>{});return s.persist;
    }
    function update(s,data){
      s.data=clone(data);s.server=s.data.database.rows.find(row=>row.id===s.row);
      if(s.loaded&&s.draft===null&&!s.request){s.base=normalize(s.server?.body?.blocks);s.baseVersion=s.server?.version;}
      if(!s.server)s.error='이 항목이 삭제되었거나 더 이상 접근할 수 없습니다.';
    }
    async function bind(scope,block,data,row){
      const database=data.source.database_id,id=identity(scope,database,row);
      let s=states.get(id);
      if(!s){s={scope,block,database,row,data:null,base:[],baseVersion:0,draft:null,request:null,loaded:false,composing:false,persist:Promise.resolve()};states.set(id,s);}
      s.scope=scope;s.block=block;update(s,data);
      if(s.loaded){notify(s);return view(s);}
      if(s.loading)return s.loading;
      s.loading=(async()=>{
        s.journal=await journalScope(scope,database,row,host.crypto);
        const entries=await host.journal.list(s.journal);
        if(!current(s))return;
        const drafts=entries.filter(entry=>entry.kind==='owned.body.draft.v1'),requests=entries.filter(entry=>entry.kind==='owned.body.request.v1');
        if(drafts.length>1||requests.length>1||drafts.length+requests.length!==entries.length)throw Error('보관한 본문 요청의 구성을 확인할 수 없습니다.');
        const draft=drafts[0],request=requests[0];
        if(draft&&(draft.operation_id!=='draft'||draft.database_id!==database||draft.row_id!==row||!Array.isArray(draft.blocks)||!Number.isInteger(draft.base_version)))throw Error('보관한 본문의 항목이 일치하지 않습니다.');
        if(request&&(request.request?.database_id!==database||request.request?.target_id!==row||request.request?.command_kind!=='database.row.content.set'||request.request?.operation_id!==request.operation_id))throw Error('보관한 저장 요청의 항목이 일치하지 않습니다.');
        s.base=normalize(draft?.base_blocks??s.server?.body?.blocks);s.baseVersion=draft?.base_version??s.server?.version;
        s.draft=draft?normalize(draft.blocks):null;s.request=request?clone(request):null;
        if(!s.request&&s.draft&&equal(s.draft,normalize(s.server?.body?.blocks))){s.draft=null;s.base=normalize(s.server?.body?.blocks);s.baseVersion=s.server?.version;await persist(s);}
        s.loaded=true;s.error='';notify(s);return view(s);
      })().catch(error=>{s.error=error.message;notify(s);return view(s);}).finally(()=>{s.loading=null;});
      return s.loading;
    }
    function draft(scope,database,row,blocks){
      const s=state(scope,database,row);if(!s||!s.loaded||s.discarding||!editable(s)||s.request&&!s.flight)return false;
      s.draft=normalize(blocks);if(equal(s.draft,s.base)&&!s.request)s.draft=null;
      s.error='';void persist(s);notify(s);return true;
    }
    function validate(s,envelope,result){
      const request=envelope.request;
      if(result?.contract!=='code_assist.notion.collaborative_database_surface_command.v1'||result?.status!=='database_command_committed'
          ||result?.operation_id!==envelope.operation_id||result?.workspace_id!==s.scope.workspaceId||result?.document_id!==s.scope.pageId
          ||result?.database_id!==s.database||result?.target_id!==s.row||result?.command_kind!==request.command_kind||result?.linked_instance!==undefined
          ||result?.target_version!==request.expected_target_version+1||result?.document_sequence!==request.expected_document_sequence+1
          ||!equal(result?.body?.blocks,normalize(request.payload.blocks)))throw Error('본문 저장 결과가 요청과 일치하지 않습니다. 같은 요청으로 다시 확인해 주세요.');
    }
    async function flush(scope,database,row){
      const s=state(scope,database,row);if(!s)return true;if(s.loading)await s.loading;if(s.discarding)return false;if(s.flight)return s.flight;
      if(s.composing){s.error='입력 중인 글자를 확정한 뒤 이동해 주세요.';notify(s);return false;}
      if(!s.loaded||!current(s))return false;
      if(s.draft===null&&!s.request)return true;
      s.flight=Promise.resolve().then(async()=>{
        for(let attempt=0;attempt<8;attempt++){
          if(!editable(s)||s.composing)throw Error('본문 편집 권한과 입력 상태를 확인해 주세요.');
          if(s.storageError)await persist(s);else await s.persist;
          if(!s.request){
            if(s.draft===null)return true;
            if(s.server.version!==s.baseVersion)throw Error('다른 곳에서 본문이 변경되었습니다. 최신 내용과 보관한 초안을 확인해 주세요.');
            const witness=s.data.witnesses.find(w=>w.document_id===scope.pageId),operation=host.operationId();
            s.request={kind:'owned.body.request.v1',operation_id:operation,created_at:Date.now(),request:{
              database_id:database,target_id:row,command_kind:'database.row.content.set',payload:{blocks:clone(s.draft)},
              expected_target_version:s.baseVersion,expected_document_sequence:witness.document_sequence,
              expected_workspace_epoch:witness.workspace_epoch,expected_document_epoch:witness.document_epoch,
              ...(witness.database_epoch==null?{}:{expected_database_epoch:witness.database_epoch}),
              operation_id:operation}};
          }
          const envelope=s.request;
          await host.journal.put(s.journal,envelope);
          if(!editable(s)||s.composing)return false;
          const result=await host.send(s.scope,clone(envelope.request));validate(s,envelope,result);
          s.base=normalize(result.body.blocks);s.baseVersion=result.target_version;
          if(equal(s.draft,s.base))s.draft=null;
          await persist(s);await host.journal.remove(s.journal,envelope.operation_id);s.request=null;s.error='';
          if(current(s))await host.changed(s.scope,s.block,result);
          if(!current(s))return true;
          if(s.draft===null)return true;
        }
        return false;
      }).catch(error=>{s.error=host.error?.(error)||error.message;return false;}).finally(()=>{s.flight=null;notify(s);});
      notify(s);return s.flight;
    }
    async function discard(scope,database,row){
      const s=state(scope,database,row);if(!s||s.flight||s.discarding||s.composing||!current(s))return false;
      s.discarding=true;notify(s);
      try{
        await s.persist.catch(()=>{});
        // Clear only this actor's exact row namespace, including invalid recovery entries.
        for(const entry of await host.journal.list(s.journal))await host.journal.remove(s.journal,entry.operation_id);
        s.request=null;s.draft=null;s.error='';s.storageError='';s.loaded=true;
        s.base=normalize(s.server?.body?.blocks);s.baseVersion=s.server?.version;
        if(current(s))await host.changed(s.scope,s.block);return true;
      }catch(error){s.error=host.error?.(error)||error.message;return false;}
      finally{s.discarding=false;notify(s);}
    }
    async function park(scope,database,row){
      // Access loss must never turn a local draft into a new remote request.
      const s=state(scope,database,row);if(!s)return true;
      if(s.loading)await s.loading;
      if(s.flight)await s.flight;
      if(s.composing||s.discarding||!s.loaded)return false;
      try{await s.persist.catch(()=>{});if(s.storageError&&s.draft!==null)await persist(s);}
      catch(_error){return false;}
      return !s.storageError;
    }
    return {bind,draft,flush,discard,park,snapshot:(scope,database,row)=>view(state(scope,database,row)),
      update:(scope,database,row,data)=>{const s=state(scope,database,row);if(s){update(s,data);notify(s);}},
      composing:(scope,database,row,value)=>{const s=state(scope,database,row);if(s){s.composing=value;notify(s);}},
      pending:(scope,database,row)=>{const s=state(scope,database,row);return Boolean(s&&(s.draft!==null||s.request||s.flight||s.discarding||s.composing||s.storageError));}};
  }
  return {create,journalScope,normalize,equal};
});

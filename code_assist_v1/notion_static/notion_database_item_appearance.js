(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionDatabaseItemAppearance=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const clone=value=>structuredClone(value),defaults={icon:'',cover:'',cover_position:.5};
  const equal=(a,b)=>JSON.stringify(normalize(a))===JSON.stringify(normalize(b));
  function updates(value){
    if(!value||typeof value!=='object'||Array.isArray(value)||!Object.keys(value).length||Object.keys(value).some(key=>!Object.hasOwn(defaults,key)))throw Error('아이콘과 커버 정보를 확인해 주세요.');
    const result={...value};
    for(const key of ['icon','cover'])if(key in result){if(typeof result[key]!=='string'||result[key].length>(key==='icon'?64:2048))throw Error('아이콘과 커버 정보를 확인해 주세요.');result[key]=result[key].trim();}
    if('cover_position' in result&&(typeof result.cover_position!=='number'||!Number.isFinite(result.cover_position)||result.cover_position<0||result.cover_position>1))throw Error('커버 위치를 확인해 주세요.');
    return result;
  }
  function normalize(value){return {...defaults,...updates({...defaults,...value})};}
  function exact(value){return value&&Object.keys(value).sort().join(',')==='cover,cover_position,icon'&&JSON.stringify(value)===JSON.stringify({...value,...normalize(value)});}
  function apply(before,value){const patch=updates(value),result=normalize(before);if('cover' in patch&&result.cover!==patch.cover)result.cover_position=.5;return {...result,...patch};}
  const key=(scope,database,row)=>JSON.stringify([scope.realm,scope.userId,scope.workspaceId,scope.pageId,database,row]);
  async function journalScope(scope,database,row,cryptoApi=globalThis.crypto){
    const bytes=await cryptoApi.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify([scope.pageId,database,row])));
    return {realm:scope.realm,subject_id:scope.userId,workspace_id:scope.workspaceId,page_id:'item-appearance-'+[...new Uint8Array(bytes)].map(x=>x.toString(16).padStart(2,'0')).join('')};
  }
  function create(host){
    const states=new Map(),find=(scope,database,row)=>states.get(key(scope,database,row));
    const editable=s=>host.current(s.scope)&&host.canEdit(s.scope)&&s.data?.database?.capabilities?.row_appearance===true&&['edit_content','edit','full_access'].includes(s.data.database.effective_role)&&Boolean(s.server);
    const view=s=>s?{ready:s.loaded,appearance:clone(s.request?.expected??normalize(s.server?.appearance)),pending:Boolean(s.request),busy:Boolean(s.flight||s.discarding),editable:editable(s)&&!s.discarding,error:s.error||''}:null;
    const notify=s=>{if(host.current(s.scope))host.state?.(s.scope,s.database,s.row,view(s));};
    function update(s,data){s.data=clone(data);s.server=s.data.database.rows.find(row=>row.id===s.row);if(!s.server)s.error='항목이 삭제되었거나 더 이상 접근할 수 없습니다.';}
    function valid(s,entry){
      const r=entry?.request,positive=n=>Number.isInteger(n)&&n>0;
      if(entry?.kind!=='owned.appearance.request.v1'||entry.identity!==key(s.scope,s.database,s.row)||!entry.operation_id||r?.operation_id!==entry.operation_id||r.database_id!==s.database||r.target_id!==s.row||r.command_kind!=='database.row.appearance.set'
          ||!['expected_target_version','expected_document_sequence','expected_workspace_epoch','expected_document_epoch'].every(k=>positive(r[k]))
          ||!exact(entry.before)||!exact(entry.expected)||!equal(entry.expected,apply(entry.before,r.payload)))throw Error('보관한 모양 변경 요청의 항목이 일치하지 않습니다.');
    }
    async function bind(scope,block,data,row){
      const database=data.source.database_id,id=key(scope,database,row);let s=states.get(id);
      if(!s){s={scope,block,database,row,request:null,loaded:false};states.set(id,s);}s.scope=scope;s.block=block;update(s,data);
      if(s.loaded){notify(s);return view(s);}if(s.loading)return s.loading;
      s.loading=(async()=>{s.journal=await journalScope(scope,database,row,host.crypto);const list=await host.journal.list(s.journal);if(!host.current(s.scope))return;
        if(list.length>1)throw Error('보관한 모양 변경 요청을 확인할 수 없습니다.');if(list[0]){valid(s,list[0]);s.request=clone(list[0]);}s.loaded=true;s.error='';notify(s);return view(s);
      })().catch(error=>{s.error=error.message;notify(s);return view(s);}).finally(()=>s.loading=null);return s.loading;
    }
    function validate(s,envelope,result){
      const request=envelope.request;
      if(result?.contract!=='code_assist.notion.collaborative_database_surface_command.v1'||result?.status!=='database_command_committed'
          ||result?.operation_id!==envelope.operation_id||result?.workspace_id!==s.scope.workspaceId||result?.document_id!==s.scope.pageId
          ||result?.database_id!==s.database||result?.target_id!==s.row||result?.command_kind!==request.command_kind||result?.linked_instance!==undefined
          ||result?.target_version!==request.expected_target_version+1||result?.document_sequence!==request.expected_document_sequence+1
          ||!exact(result.appearance)||!equal(result.appearance,envelope.expected))throw Error('모양 저장 결과가 요청과 일치하지 않습니다. 같은 요청으로 다시 확인해 주세요.');
    }
    async function flush(scope,database,row){
      const s=find(scope,database,row);if(!s)return true;if(s.loading)await s.loading;if(s.flight)return s.flight;
      if(!s.loaded||s.discarding||!host.current(s.scope))return false;if(!s.request)return true;
      s.flight=Promise.resolve().then(async()=>{
        if(!editable(s))throw Error('아이콘과 커버를 변경할 권한을 확인해 주세요.');const envelope=s.request;valid(s,envelope);
        await host.journal.put(s.journal,envelope);if(!editable(s))return false;
        const result=await host.send(s.scope,clone(envelope.request));validate(s,envelope,result);
        await host.journal.remove(s.journal,envelope.operation_id);s.request=null;s.error='';
        if(s.server.version<=result.target_version){s.server.appearance=clone(result.appearance);s.server.version=result.target_version;}
        if(host.current(s.scope))await host.changed(s.scope,s.block,result);return true;
      }).catch(error=>{s.error=host.error?.(error)||error.message;return false;}).finally(()=>{s.flight=null;notify(s);});notify(s);return s.flight;
    }
    async function set(scope,database,row,value){
      const s=find(scope,database,row);if(!s||!s.loaded||s.flight||s.discarding||s.request||!editable(s))return false;
      const patch=updates(value),before=normalize(s.server.appearance),expected=apply(before,patch);if(equal(before,expected))return true;
      const witness=s.data.witnesses.find(w=>w.document_id===scope.pageId);if(!witness)return false;
      const operation=host.operationId();s.request={kind:'owned.appearance.request.v1',identity:key(scope,database,row),operation_id:operation,created_at:Date.now(),before,expected,request:{
        database_id:database,target_id:row,command_kind:'database.row.appearance.set',payload:patch,expected_target_version:s.server.version,
        expected_document_sequence:witness.document_sequence,expected_workspace_epoch:witness.workspace_epoch,expected_document_epoch:witness.document_epoch,operation_id:operation}};
      s.error='';return flush(scope,database,row);
    }
    async function discard(scope,database,row){
      const s=find(scope,database,row);if(s?.loading)await s.loading;if(!s?.journal||s.flight||s.discarding||!host.current(s.scope))return false;s.discarding=true;notify(s);
      try{for(const entry of await host.journal.list(s.journal))await host.journal.remove(s.journal,entry.operation_id);s.request=null;s.loaded=true;s.error='';if(host.current(s.scope))await host.changed(s.scope,s.block);return true;}
      catch(error){s.error=host.error?.(error)||error.message;return false;}finally{s.discarding=false;notify(s);}
    }
    return {bind,set,flush,discard,snapshot:(scope,database,row)=>view(find(scope,database,row)),
      update:(scope,database,row,data)=>{const s=find(scope,database,row);if(s){update(s,data);notify(s);}},
      pending:(scope,database,row)=>{const s=find(scope,database,row);return Boolean(s&&(s.request||s.flight||s.discarding||!s.loaded&&s.error));}};
  }
  return {create,normalize,updates,apply,equal,journalScope};
});

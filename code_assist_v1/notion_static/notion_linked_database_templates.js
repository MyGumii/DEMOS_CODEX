(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.NotionLinkedDatabaseTemplates=api;
})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const CONTRACT='code_assist.notion.database_sharing.v1';
  const KIND='linked.database.template.create.v1';
  const UPDATE_KIND='linked.database.template.update.v1';
  const key=(scope,block)=>JSON.stringify([scope.realm,scope.userId,scope.workspaceId,scope.pageId,block]);
  const journalScope=scope=>({realm:scope.realm,subject_id:scope.userId,
    workspace_id:scope.workspaceId,page_id:`linked-database-templates:${scope.pageId}`});
  const editable=data=>Boolean(data?.source?.database_id&&data?.access
    &&data?.instance_witness&&['edit','full_access'].includes(data?.database?.effective_role));

  function create(host){
    const states=new Map();
    function state(scope,block){
      const id=key(scope,block);
      if(!states.has(id))states.set(id,{scope,block,data:null,loaded:false,
        loading:null,busy:false,pending:null,error:''});
      return states.get(id);
    }
    function notify(s){if(host.current(s.scope))host.state?.(s.scope,s.block,{
      ready:s.loaded,busy:s.busy,pending:Boolean(s.pending),error:s.error,
    });}
    function checked(s){
      if(!host.current(s.scope)||!host.canEdit(s.scope)||!editable(s.data))
        throw Error('원본 표와 연결 페이지의 템플릿 편집 권한을 확인해 주세요.');
    }
    function validate(result,s,envelope){
      const request=envelope.request;
      const updating=envelope.kind===UPDATE_KIND;
      if(result?.contract!==CONTRACT||result.workspace_id!==s.scope.workspaceId
          ||result.database_id!==request.source.database_id
          ||result.operation_id!==envelope.operation_id
          ||result.command_kind!==(updating?'database.template.update':'database.template.create')
          ||result.target_id!==request.target_id
          ||result.target_version!==(updating?request.expected_target_version+1:1)
          ||result.document_sequence!==request.source_access.document_sequence+1
          ||result.linked_instance?.document_id!==s.scope.pageId
          ||result.linked_instance?.block_id!==s.block
          ||Object.hasOwn(result,'document_id')||Object.hasOwn(result,'content_sha256'))
        throw Error('템플릿 생성 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.');
    }
    async function send(s){
      if(s.busy)throw Error('템플릿 생성을 확인 중입니다.');
      checked(s);
      const envelope=s.pending;if(!envelope)return false;
      if(envelope.request.source.database_id!==s.data.source.database_id)
        throw Error('연결 원본이 변경되어 보관한 요청을 자동 확인할 수 없습니다.');
      s.busy=true;s.error='';notify(s);
      try{
        const result=await (envelope.kind===UPDATE_KIND?host.sendUpdate:host.send)(
          s.scope,s.block,structuredClone(envelope.request));
        validate(result,s,envelope);
        await host.journal.remove(journalScope(s.scope),envelope.operation_id);
        s.pending=null;
        if(host.current(s.scope)){
          try{await host.changed(s.scope,s.block,result);}
          catch{s.error='템플릿은 생성됐지만 목록을 새로 읽지 못했습니다. 보기를 새로고침해 주세요.';}
        }
        return true;
      }catch(error){
        s.error=[401,403,404].includes(error?.status)
          ?'원본 또는 연결 보기의 권한을 다시 확인해 주세요. 생성 요청은 보관되어 있습니다.'
          :error?.status===409
          ?'다른 변경과 충돌했습니다. 보관한 생성 요청을 확인해 주세요.'
          :error?.message||'템플릿 생성을 확인하지 못했습니다. 같은 요청으로 다시 시도해 주세요.';
        throw error;
      }finally{s.busy=false;notify(s);}
    }
    async function bind(scope,block,data){
      const s=state(scope,block),reopened=s.scope!==scope;
      s.scope=scope;s.data=structuredClone(data);
      if(s.loaded){
        notify(s);
        if(reopened&&s.pending&&!s.busy&&host.current(scope)
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
          s.pending=entries.find(entry=>[KIND,UPDATE_KIND].includes(entry.kind)
            &&entry.block_id===block
            &&entry.request?.operation_id===entry.operation_id)||null;
          s.loaded=true;notify(s);
          if(s.pending&&host.canEdit(scope)&&editable(data))await send(s);
        }catch(error){s.error=error?.message||'보관한 생성 요청을 확인하지 못했습니다.';notify(s);}
      })();
      try{await s.loading;}finally{s.loading=null;}
    }
    async function createTemplate(scope,block,name){
      const s=state(scope,block);
      if(s.loading)await s.loading;
      if(!s.loaded||s.busy||s.pending)
        throw Error('보관한 템플릿 생성 요청을 먼저 확인해 주세요.');
      checked(s);
      const title=String(name||'').trim();
      if(!title||title.length>120)throw Error('템플릿 이름을 1~120자로 입력해 주세요.');
      const operation=host.operationId();
      const request={source:structuredClone(s.data.source),
        instance_witness:structuredClone(s.data.instance_witness),
        source_access:structuredClone(s.data.access),name:title,
        target_id:host.templateId(),operation_id:operation};
      const envelope={kind:KIND,block_id:block,operation_id:operation,
        created_at:Date.now(),request};
      s.busy=true;notify(s);
      try{await host.journal.put(journalScope(scope),envelope);s.pending=envelope;}
      finally{s.busy=false;notify(s);}
      return send(s);
    }
    async function updateTemplate(scope,block,templateId,{name,body,defaultValues,expectedVersion}){
      const s=state(scope,block);
      if(s.loading)await s.loading;
      if(!s.loaded||s.busy||s.pending)
        throw Error('보관한 템플릿 요청을 먼저 확인해 주세요.');
      checked(s);
      const template=s.data.database?.templates?.find(item=>item.id===templateId
        &&item.state==='active'&&!item.body_unavailable&&!item.default_unavailable);
      if(!template||!Number.isInteger(template.version)||template.version<1)
        throw Error('편집 가능한 템플릿을 다시 확인해 주세요.');
      if(expectedVersion!==undefined&&(!Number.isSafeInteger(expectedVersion)||expectedVersion<1
          ||expectedVersion!==template.version))
        throw Error('템플릿이 다른 곳에서 변경되었습니다. 변경 내용을 확인한 뒤 다시 열어 주세요.');
      const title=String(name||'').trim();
      if(!title||title.length>120)throw Error('템플릿 이름을 1~120자로 입력해 주세요.');
      if(!Array.isArray(body)||!defaultValues||typeof defaultValues!=='object'
          ||Array.isArray(defaultValues))throw Error('템플릿 내용을 확인해 주세요.');
      const operation=host.operationId();
      const request={source:structuredClone(s.data.source),
        instance_witness:structuredClone(s.data.instance_witness),
        source_access:structuredClone(s.data.access),name:title,
        body:structuredClone(body),default_values:structuredClone(defaultValues),
        expected_target_version:template.version,
        target_id:templateId,operation_id:operation};
      const envelope={kind:UPDATE_KIND,block_id:block,operation_id:operation,
        created_at:Date.now(),request};
      s.busy=true;notify(s);
      try{await host.journal.put(journalScope(scope),envelope);s.pending=envelope;}
      finally{s.busy=false;notify(s);}
      return send(s);
    }
    async function discard(scope,block){
      const s=state(scope,block);
      if(s.busy)throw Error('생성 확인 중에는 요청을 지울 수 없습니다.');
      if(!s.pending)return;
      await host.journal.remove(journalScope(scope),s.pending.operation_id);
      s.pending=null;s.error='';notify(s);
    }
    return {bind,createTemplate,updateTemplate,retry:(scope,block)=>send(state(scope,block)),discard,
      status:(scope,block)=>{const s=state(scope,block);return {
        ready:s.loaded,busy:s.busy,pending:Boolean(s.pending),error:s.error};}};
  }
  return {create,editable,journalScope,KIND,UPDATE_KIND};
});

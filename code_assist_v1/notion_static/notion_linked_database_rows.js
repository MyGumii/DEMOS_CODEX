(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.NotionLinkedDatabaseRows=api;
})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const CONTRACT='code_assist.notion.database_sharing.v1';
  const KIND='linked.database.row.create.v1';
  const key=(scope,block)=>JSON.stringify([scope.realm,scope.userId,scope.workspaceId,scope.pageId,block]);
  const journalScope=scope=>({realm:scope.realm,subject_id:scope.userId,
    workspace_id:scope.workspaceId,page_id:`linked-database-rows:${scope.pageId}`});
  const editable=data=>Boolean(data?.access&&data?.instance_witness
    &&data?.source?.database_id
    &&data?.database?.properties?.some(property=>property.type==='title')
    &&['edit_content','edit','full_access'].includes(data?.database?.effective_role));

  function create(host){
    const states=new Map();
    function state(scope,block){
      const id=key(scope,block);
      if(!states.has(id))states.set(id,{scope,block,data:null,loaded:false,loading:null,busy:false,pending:null,error:''});
      return states.get(id);
    }
    function notify(s){if(host.current(s.scope))host.state?.(s.scope,s.block,{
      ready:s.loaded,busy:s.busy,pending:Boolean(s.pending),error:s.error,
    });}
    function checked(s){
      if(!host.current(s.scope)||!host.canEdit(s.scope)||!editable(s.data))
        throw Error('원본과 이 페이지의 편집 권한을 확인해 주세요.');
    }
    function validate(result,s,envelope){
      const request=envelope.request;
      if(result?.contract!==CONTRACT||result.workspace_id!==s.scope.workspaceId
          ||result.database_id!==request.source.database_id
          ||result.operation_id!==envelope.operation_id
          ||result.command_kind!=='database.row.create'
          ||result.document_sequence!==request.source_access.document_sequence+1
          ||result.linked_instance?.document_id!==s.scope.pageId
          ||result.linked_instance?.block_id!==s.block
          ||typeof result.row_id!=='string'||!result.row_id
          ||Object.hasOwn(result,'document_id')||Object.hasOwn(result,'content_sha256'))
        throw Error('항목 생성 응답을 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.');
      if((request.template_id&&result.template_id!==request.template_id)
          ||(request.use_default_template===false&&Object.hasOwn(result,'template_id')))
        throw Error('선택한 템플릿과 생성 결과가 다릅니다. 같은 요청으로 다시 확인해 주세요.');
    }
    async function send(s){
      if(s.busy)throw Error('항목 생성을 확인 중입니다.');
      checked(s);
      const envelope=s.pending;if(!envelope)return false;
      if(envelope.request.source.database_id!==s.data.source.database_id)
        throw Error('연결 원본이 변경되어 보관한 요청을 자동 확인할 수 없습니다.');
      s.busy=true;s.error='';notify(s);
      try{
        const result=await host.send(s.scope,s.block,structuredClone(envelope.request));
        validate(result,s,envelope);
        await host.journal.remove(journalScope(s.scope),envelope.operation_id);
        s.pending=null;
        if(host.current(s.scope)){
          try{await host.changed(s.scope,s.block,result);}
          catch{s.error='항목은 생성됐지만 목록을 새로 읽지 못했습니다. 보기를 새로고침해 주세요.';}
        }
        return true;
      }catch(error){
        s.error=[401,403,404].includes(error?.status)
          ?'원본 또는 연결 보기의 권한을 다시 확인해 주세요. 생성 요청은 보관되어 있습니다.'
          :error?.status===409
          ?'다른 변경과 충돌했습니다. 보관한 생성 요청을 확인해 주세요.'
          :error?.message||'항목 생성을 확인하지 못했습니다. 같은 요청으로 다시 시도해 주세요.';
        throw error;
      }finally{s.busy=false;notify(s);}
    }
    async function bind(scope,block,data){
      const s=state(scope,block),reopened=s.scope!==scope;
      s.scope=scope;s.data=structuredClone(data);
      if(s.loaded){
        notify(s);
        if(reopened&&s.pending&&!s.busy&&host.current(scope)&&host.canEdit(scope)&&editable(data)){
          try{await send(s);}catch{}
        }
        return;
      }
      if(s.loading)return s.loading;
      s.loading=(async()=>{
        try{
          const entries=await host.journal.list(journalScope(scope));
          if(!host.current(scope))return;
          s.pending=entries.find(entry=>entry.kind===KIND&&entry.block_id===block
            &&entry.request?.operation_id===entry.operation_id)||null;
          s.loaded=true;notify(s);
          if(s.pending&&host.canEdit(scope)&&editable(data))await send(s);
        }catch(error){s.error=error?.message||'보관한 생성 요청을 확인하지 못했습니다.';notify(s);}
      })();
      try{await s.loading;}finally{s.loading=null;}
    }
    async function createRow(scope,block,{blank=false,templateId=null}={}){
      const s=state(scope,block);
      if(s.loading)await s.loading;
      if(!s.loaded||s.busy||s.pending)
        throw Error('보관한 항목 생성 요청을 먼저 확인해 주세요.');
      checked(s);
      if(!s.data.database?.properties?.some(item=>item.type==='title'))
        throw Error('이 표에는 제목 속성이 없습니다.');
      const templates=s.data.database?.templates||[];
      const defaultId=s.data.database?.default_template?.template_id;
      if(!blank&&templateId===null&&defaultId){
        const selected=templates.find(item=>item.id===defaultId&&item.state==='active');
        if(!selected||selected.body_unavailable||selected.default_unavailable)
          throw Error('기본 템플릿에 접근할 수 없습니다. 비어 있음을 선택해 주세요.');
      }
      if(templateId!==null){
        if(blank||typeof templateId!=='string'||!templateId
            ||!templates.some(item=>item.id===templateId&&item.state==='active'
              &&!item.body_unavailable&&!item.default_unavailable))
          throw Error('선택한 템플릿에 접근할 수 없습니다.');
      }
      const operation=host.operationId();
      const request={source:structuredClone(s.data.source),
        instance_witness:structuredClone(s.data.instance_witness),
        source_access:structuredClone(s.data.access),title:'',operation_id:operation};
      if(blank)request.use_default_template=false;
      if(templateId!==null)request.template_id=templateId;
      const envelope={kind:KIND,block_id:block,operation_id:operation,
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
    return {bind,createRow,retry:(scope,block)=>send(state(scope,block)),discard,
      status:(scope,block)=>{const s=state(scope,block);return {ready:s.loaded,busy:s.busy,
        pending:Boolean(s.pending),error:s.error};}};
  }
  return {create,editable,journalScope,KIND};
});

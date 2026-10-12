(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionSyncedText=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const clone=value=>structuredClone(value),same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  const types=new Set(['text','paragraph','synced_block','heading1','heading2','heading3','heading4','bullet','numbered','todo','toggle','toggle_heading1','toggle_heading2','toggle_heading3','toggle_heading4','callout','quote','code','equation']);
  const endpoint=(a,b)=>a?.document_id===b?.document_id&&a?.block_id===b?.block_id;
  const value=node=>({id:node.id,content:node.content||'',rich_text_html:node.props?.rich_text_html??null});
  function leaves(tree){
    const result=new Map();
    function visit(node){
      if(!node||node.type==='synced_block'&&node.props?.synced_from!==null)return;
      if(types.has(node.type))result.set(node.id,value(node));
      for(const child of node.children||[])visit(child);
    }
    visit(tree);return result;
  }
  const identity=(scope,block)=>JSON.stringify([scope.realm,scope.userId,scope.workspaceId,scope.pageId,block]);
  const originKey=origin=>JSON.stringify([origin.document_id,origin.block_id]);
  async function draftId(origin,cryptoApi=globalThis.crypto){
    const digest=await cryptoApi.subtle.digest('SHA-256',new TextEncoder().encode(originKey(origin)));
    return 'draft-'+[...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('');
  }
  async function journalScope(scope,block,cryptoApi=globalThis.crypto){
    const digest=await cryptoApi.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify([scope.pageId,block])));
    return {realm:scope.realm,subject_id:scope.userId,workspace_id:scope.workspaceId,
      page_id:'synced-text-'+[...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('')};
  }
  function create(host){
    const groups=new Map();
    const states=()=>[...groups.values()].flatMap(group=>[...group.states.values()]);
    const current=s=>host.current(s.scope)&&s.group.active===s,allowed=s=>current(s)&&s.available&&host.canEdit(s.scope)&&s.data?.capabilities?.edit_text===true;
    const get=(scope,block,origin)=>{const s=groups.get(identity(scope,block))?.active;return s&&host.current(scope)&&(!origin||endpoint(origin,s.origin))?s:null;};
    const dirty=s=>Boolean(s&&(s.edits.size||s.request||s.flight||s.composing||s.storageError));
    function conflicts(s){
      if(!s.available||s.request)return false;
      const now=leaves(s.data.tree);
      return [...s.edits].some(([id,edit])=>!same(edit.base,now.get(id)));
    }
    function snapshot(s){
      if(!s)return null;
      const tree=s.available?clone(s.data.tree):null;
      function patch(node){const edit=s.edits.get(node.id);if(edit){node.content=edit.value.content;node.props||={};if(edit.value.rich_text_html===null)delete node.props.rich_text_html;else node.props.rich_text_html=edit.value.rich_text_html;}for(const child of node.children||[])patch(child);}
      if(tree)patch(tree);
      const server=s.available?leaves(s.data.tree):new Map();
      const differences=s.available&&!s.request?[...s.edits].filter(([id,edit])=>!same(edit.base,server.get(id)))
        .map(([id,edit])=>({id,latest:server.get(id)?.content??null,draft:edit.value.content})):[];
      return {tree,origin:clone(s.origin),ready:s.loaded,available:s.available,editable:allowed(s)&&s.loaded&&!s.discarding&&(!s.request||Boolean(s.flight)),
        dirty:dirty(s),parked:[...s.group.states.values()].filter(other=>other!==s&&dirty(other)).length,
        busy:Boolean(s.flight||s.discarding),pending:Boolean(s.request),composing:s.composing,conflict:conflicts(s),differences,error:s.error||s.storageError||''};
    }
    const notify=s=>{const active=s.group.active;if(active&&current(active))host.state?.(active.scope,active.block,snapshot(active));};
    function persist(s){
      const entry=s.edits.size||s.request?{kind:'synced.text.draft.v1',operation_id:s.entryId,created_at:Date.now(),
        block_id:s.block,origin:clone(s.origin),edits:[...s.edits.values()].map(clone),request:clone(s.request)}:null;
      s.persistence=s.persistence.catch(()=>{}).then(async()=>{
        if(entry)await host.journal.put(s.journal,entry);else await host.journal.remove(s.journal,s.entryId);
        s.storageError='';
      }).catch(error=>{s.storageError='입력을 이 브라우저에 보관하지 못했습니다. 저장 공간을 확인해 주세요.';notify(s);throw error;});
      s.persistence.catch(()=>{});return s.persistence;
    }
    function update(s,data){
      if(!endpoint(data.origin,s.origin))throw Error('동기화 원본이 변경되었습니다. 보관한 입력을 먼저 확인해 주세요.');
      if(!data.tree)data={...data,tree:{...data.block,children:[]}};
      s.data=clone(data);s.available=true;
    }
    function makeState(group,scope,block,origin,entryId){
      return {group,scope,block,origin:clone(origin),entryId,journal:group.journal,data:null,available:false,loaded:false,
        edits:new Map(),request:null,composing:false,persistence:Promise.resolve()};
    }
    async function loadGroup(group,scope,block){
        group.journal=await journalScope(scope,block,host.crypto);
        const entries=await host.journal.list(group.journal),restoredStates=new Map();
        for(const entry of entries){
          if(entry.kind!=='synced.text.draft.v1'||entry.block_id!==block||!entry.origin?.document_id||!entry.origin?.block_id||!Array.isArray(entry.edits))throw Error('보관한 입력의 원본이 일치하지 않습니다.');
          const expected=await draftId(entry.origin,host.crypto);
          if(!['draft',expected].includes(entry.operation_id)||restoredStates.has(originKey(entry.origin)))throw Error('보관한 동기화 입력을 확인할 수 없습니다.');
          const s=makeState(group,scope,block,entry.origin,entry.operation_id);
          const restored=new Map();
          for(const edit of entry.edits){
            if(!edit?.value||!edit.base||typeof edit.value.id!=='string'||edit.base.id!==edit.value.id||restored.has(edit.value.id)
                ||[edit.base,edit.value].some(v=>typeof v.content!=='string'||!(v.rich_text_html===null||typeof v.rich_text_html==='string')))throw Error('보관한 글의 구성이 올바르지 않습니다.');
            restored.set(edit.value.id,clone(edit));
          }
          if(entry.request&&(!endpoint(entry.request.origin,s.origin)||typeof entry.request.operation_id!=='string'||!Array.isArray(entry.request.witnesses)
              ||!Array.isArray(entry.request.updates)||entry.request.updates.some(v=>!restored.has(v.id))))throw Error('보관한 저장 요청을 확인할 수 없습니다.');
          s.edits=restored;s.request=clone(entry.request);s.loaded=true;restoredStates.set(originKey(s.origin),s);
        }
        group.states=restoredStates;group.loaded=true;
    }
    async function bind(scope,block,data){
      const id=identity(scope,block);let group=groups.get(id);
      if(!group){group={states:new Map(),active:null,version:0,loaded:false};groups.set(id,group);}
      const version=++group.version;
      if(group.active&&!endpoint(group.active.origin,data.origin)){
        group.active.available=false;group.active.composing=false;notify(group.active);
      }
      try{
        if(!group.loaded){
          group.loading||=loadGroup(group,scope,block).finally(()=>{group.loading=null;});
          await group.loading;
        }
        const entryId=await draftId(data.origin,host.crypto);
        if(version!==group.version)return snapshot(group.active);
        let s=group.states.get(originKey(data.origin));
        if(!s){s=makeState(group,scope,block,data.origin,entryId);s.loaded=true;group.states.set(originKey(data.origin),s);}
        group.active=s;s.scope=scope;update(s,data);notify(s);return snapshot(s);
      }catch(error){
        if(version!==group.version)return snapshot(group.active);
        let s=group.active;
        if(!s){s=makeState(group,scope,block,data.origin,null);group.active=s;}
        s.available=false;s.error=error.message;notify(s);return snapshot(s);
      }
    }
    function draft(scope,block,next,origin){
      const s=get(scope,block,origin);if(!s||!snapshot(s).editable)return false;
      const now=leaves(s.data.tree),before=s.edits.get(next.id)?.base??now.get(next.id);
      if(!before||typeof next.content!=='string'||!(next.rich_text_html===null||typeof next.rich_text_html==='string'))return false;
      if(same(next,before)&&!s.request)s.edits.delete(next.id);else s.edits.set(next.id,{base:clone(before),value:clone(next)});
      s.error='';void persist(s);notify(s);return true;
    }
    function validate(s,request,result){
      const witness=request.witnesses.find(w=>w.document_id===request.origin.document_id);
      if(result?.contract!=='code_assist.notion.synced_text_edit.v1'||result.operation_id!==request.operation_id
          ||!endpoint(result.instance,{document_id:s.scope.pageId,block_id:s.block})||!endpoint(result.origin,request.origin)
          ||result.document_id!==request.origin.document_id||result.workspace_id!==witness?.workspace_id
          ||result.document_sequence!==witness?.document_sequence+1||result.workspace_epoch!==witness.workspace_epoch
          ||result.document_epoch!==witness.document_epoch||result.generation!==witness.generation
          ||!same(result.changed_block_ids,request.updates.map(v=>v.id)))throw Error('저장 응답을 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.');
    }
    async function flush(scope,block,origin){
      const s=get(scope,block,origin);if(!s)return true;if(s.loading)await s.loading;if(s.flight)return s.flight;
      if(s.composing||s.discarding)return false;
      if(!dirty(s))return true;
      s.flight=Promise.resolve().then(async()=>{
        for(let attempt=0;attempt<8;attempt++){
          if(!allowed(s)||!s.loaded||s.composing)throw Error('원본과 이 페이지의 편집 권한을 확인해 주세요. 입력은 보관했습니다.');
          if(s.storageError)await persist(s);else await s.persistence;
          if(!s.request){
            if(!s.edits.size)return true;
            if(conflicts(s))throw Error('원본의 같은 글이 변경되었습니다. 보관한 입력과 최신 내용을 확인해 주세요.');
            s.request={origin:clone(s.origin),witnesses:clone(s.data.witnesses),updates:[...s.edits.values()].map(e=>clone(e.value)),operation_id:host.operationId()};
          }
          const request=clone(s.request);await persist(s);
          if(!allowed(s)||s.composing)return false;
          const result=await host.send(s.scope,s.block,request);validate(s,request,result);
          const currentWitness=s.data.witnesses.find(w=>w.document_id===s.origin.document_id);
          if(currentWitness.document_sequence<=result.document_sequence){
            const committed=new Map(request.updates.map(v=>[v.id,v]));
            function apply(node){const v=committed.get(node.id);if(v){node.content=v.content;node.props||={};if(v.rich_text_html===null)delete node.props.rich_text_html;else node.props.rich_text_html=v.rich_text_html;}for(const child of node.children||[])apply(child);}
            apply(s.data.tree);currentWitness.document_sequence=result.document_sequence;
          }
          for(const sent of request.updates){const edit=s.edits.get(sent.id);if(!edit)continue;if(same(edit.value,sent))s.edits.delete(sent.id);else edit.base=clone(sent);}
          s.request=null;s.error='';await persist(s);
          if(current(s))await host.changed(s.scope,s.block,result);
          if(!current(s))return true;
          if(!s.edits.size)return true;
        }
        return false;
      }).catch(async error=>{
        s.error=error.status===409?'원본이 변경되어 저장하지 않았습니다. 최신 내용과 입력을 확인해 주세요.':error.message||'저장을 확인하지 못했습니다. 다시 시도해 주세요.';
        if(error.status===409){s.request=null;await persist(s).catch(()=>{});if(current(s))await host.changed(s.scope,s.block).catch(()=>{});}
        if([401,403,404].includes(error.status)){s.available=false;s.composing=false;}
        return false;
      }).finally(()=>{s.flight=null;notify(s);});
      notify(s);return s.flight;
    }
    async function discard(scope,block,origin){
      const s=get(scope,block,origin);if(!s||s.flight||s.composing||s.discarding)return false;
      // An uncertain request must be resolved first; discarding cannot cancel an
      // already committed remote operation whose response was lost.
      if(s.request){s.error='저장 여부를 먼저 다시 확인해 주세요.';notify(s);return false;}
      s.discarding=true;notify(s);
      try{await s.persistence.catch(()=>{});await host.journal.remove(s.journal,s.entryId);s.edits.clear();s.error=s.storageError='';return true;}
      catch(error){s.storageError=error.message;return false;}
      finally{s.discarding=false;notify(s);}
    }
    async function rebase(scope,block,origin){
      const s=get(scope,block,origin);if(!s||s.request||s.flight||s.composing||!allowed(s))return false;
      const now=leaves(s.data.tree);if([...s.edits.keys()].some(id=>!now.has(id))){s.error='삭제된 글에는 입력을 적용할 수 없습니다.';notify(s);return false;}
      for(const [id,edit] of s.edits)edit.base=clone(now.get(id));
      s.error='';await persist(s);return flush(scope,block);
    }
    return {bind,draft,flush,discard,rebase,snapshot:(scope,block)=>snapshot(get(scope,block)),
      composing(scope,block,value,origin){const s=get(scope,block,origin);if(s){s.composing=value;notify(s);}},
      unavailable(scope,block){if(!host.current(scope))return;const group=groups.get(identity(scope,block));if(group)group.version++;const s=group?.active;if(s){s.available=false;s.composing=false;notify(s);}},
      pending:scope=>states().some(s=>identity(s.scope,'')===identity(scope,'')&&dirty(s)),
      async flushPage(scope){
        let ok=true;
        for(const s of states())if(identity(s.scope,'')===identity(scope,'')&&dirty(s)){
          if(!allowed(s)&&s.loaded&&!s.composing&&!s.flight){
            // Losing access must not trap navigation or erase an unsent draft.
            // Park it in this actor's encrypted namespace without a remote write.
            try{await persist(s);}catch{ok=false;}
          }else ok=await flush(s.scope,s.block)&&ok;
        }
        return ok;
      },
    };
  }
  return {create,journalScope,leaves,types};
});

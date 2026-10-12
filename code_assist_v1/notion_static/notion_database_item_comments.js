(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionDatabaseItemComments=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const clone=value=>structuredClone(value),empty=()=>({content:'',parent:'',editing:'',version:0});
  const contract='code_assist.notion.live_collaboration_authority.v1';
  const databaseContract='code_assist.notion.database_sharing.v1';
  const positive=n=>Number.isInteger(n)&&n>0;
  const roles=new Set(['owner','comment','edit_content','edit','full_access']);
  const identity=c=>JSON.stringify([c.realm,c.userId,c.workspaceId,c.pageId,c.databaseId,c.rowId]);
  const target=c=>({kind:'database_row',database_id:c.databaseId,row_id:c.rowId});
  const sameTarget=(a,b)=>a?.kind==='database_row'&&a.database_id===b.database_id&&a.row_id===b.row_id&&Object.keys(a).length===3;
  const statuses={create:'comment_created',edit:'comment_updated',delete:'comment_deleted',resolve:'comment_resolution_set',react:'comment_reaction_toggled'};
  async function journalScope(c,cryptoApi=globalThis.crypto){
    const bytes=await cryptoApi.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify([c.pageId,c.databaseId,c.rowId])));
    return {realm:c.realm,subject_id:c.userId,workspace_id:c.workspaceId,page_id:'item-comments-'+[...new Uint8Array(bytes)].map(n=>n.toString(16).padStart(2,'0')).join('')};
  }
  function create(host){
    const states=new Map(),get=c=>states.get(identity(c));
    const current=s=>host.current(s.context),allowed=s=>current(s)&&s.ready&&roles.has(s.remote?.role);
    const editable=(s,item)=>allowed(s)&&(item.author_subject_id===s.context.userId||['owner','full_access'].includes(s.remote.role));
    const view=s=>s?{ready:s.ready,items:clone(s.remote?.items||[]),draft:clone(s.draft),canComment:allowed(s),role:s.remote?.role||'view',
      userId:s.context.userId,busy:Boolean(s.flight),pending:Boolean(s.request),composing:s.composing,error:s.error||'',savingDraft:s.writes>0,draftSaved:!s.storageError}:null;
    const notify=s=>{if(current(s))host.state?.(s.context,view(s));};
    const error=(s,e)=>{s.error=host.error?.(e)||e.message;notify(s);};
    const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
    const fingerprint=entry=>entry?JSON.stringify(canonical([entry.kind,entry.identity,entry.draft,entry.request])):null;
    function envelope(s,request=s.request,draft=s.draft){return {kind:'item.comments.state.v1',identity:identity(s.context),operation_id:'item-comment-state',created_at:Date.now(),draft:clone(draft),request:clone(request)};}
    function persist(s,value=envelope(s)){
      s.writes++;const write=async()=>{
        const list=await host.journal.list(s.journal);
        if(list.length>1||fingerprint(list[0])!==s.stored)throw Error('다른 탭에서 댓글 초안이나 요청이 변경되었습니다. 현재 입력을 보관한 뒤 새로고침해 주세요.');
        await host.journal.put(s.journal,value);s.stored=fingerprint(value);
      };
      const promise=s.queue.then(()=>host.exclusive?host.exclusive(s.journal.page_id,write):write());
      s.queue=promise.catch(()=>{});return promise.then(()=>{s.storageError=false;return true;},e=>{s.storageError=true;error(s,e);return false;}).finally(()=>{s.writes--;notify(s);});
    }
    function validateStored(s,entry){
      const d=entry?.draft,r=entry?.request;
      if(entry?.kind!=='item.comments.state.v1'||entry.operation_id!=='item-comment-state'||entry.identity!==identity(s.context)
          ||!d||!['content,editing,parent','content,editing,parent,version'].includes(Object.keys(d).sort().join(','))
          ||['content','editing','parent'].some(k=>typeof d[k]!=='string')||d.content.length>20000||d.editing&&(d.parent||!positive(d.version)))throw Error('보관한 댓글의 항목 정보를 확인할 수 없습니다.');
      if(!r)return;
      const databaseOnly=s.context.databaseOnly===true;
      const b=r.body,keys=['operation_id','expected_workspace_epoch','expected_document_epoch'];
      if(databaseOnly)keys.push('expected_database_epoch');
      if(!Object.hasOwn(statuses,r.action)||typeof r.comment!=='string'||!r.comment||typeof b?.operation_id!=='string'||!b.operation_id
          ||!positive(b.expected_workspace_epoch)||!positive(b.expected_document_epoch)
          ||databaseOnly&&!positive(b.expected_database_epoch))throw Error('보관한 댓글 요청을 확인할 수 없습니다.');
      if(r.action==='create'){
        keys.push('comment_id','content','parent_comment_id');
        if(!databaseOnly)keys.push('anchor','mention_subject_ids');
        else if(Object.hasOwn(b,'mention_subject_ids'))keys.push('mention_subject_ids');
        if(b.comment_id!==r.comment||!databaseOnly&&!sameTarget(b.anchor,target(s.context))||typeof b.parent_comment_id!=='string')throw Error('보관한 댓글 대상이 일치하지 않습니다.');
      }else{keys.push('expected_row_version');if(!positive(b.expected_row_version))throw Error('보관한 댓글 버전이 올바르지 않습니다.');}
      if(['create','edit'].includes(r.action)){
        if(r.action==='edit'){
          keys.push('content');
          if(!databaseOnly||Object.hasOwn(b,'mention_subject_ids'))keys.push('mention_subject_ids');
        }
        if(typeof b.content!=='string'||!b.content.trim()||b.content.length>20000
            ||(!databaseOnly||Object.hasOwn(b,'mention_subject_ids'))
              &&(!Array.isArray(b.mention_subject_ids)||b.mention_subject_ids.length>100
                ||b.mention_subject_ids.some(x=>typeof x!=='string')))throw Error('보관한 댓글 내용을 확인할 수 없습니다.');
      }
      if(r.action==='resolve'){keys.push('resolved');if(typeof b.resolved!=='boolean')throw Error('댓글 완료 상태가 올바르지 않습니다.');}
      if(r.action==='react'){keys.push('emoji');if(typeof b.emoji!=='string'||!b.emoji||b.emoji.length>32)throw Error('댓글 반응이 올바르지 않습니다.');}
      if(Object.keys(b).sort().join(',')!==keys.sort().join(','))throw Error('보관한 댓글 요청 형식이 올바르지 않습니다.');
    }
    function acceptRead(s,result){
      if(s.context.databaseOnly===true){
        if(result?.contract!==databaseContract||result.workspace_id!==s.context.workspaceId
            ||result.database_id!==s.context.databaseId||result.row_id!==s.context.rowId
            ||Object.hasOwn(result,'document_id')||!sameTarget(result.item_target,target(s.context))
            ||!positive(result.workspace_epoch)||!positive(result.document_epoch)||!positive(result.database_epoch)
            ||!Array.isArray(result.items)||result.items.some(item=>!sameTarget(item.anchor,target(s.context))
              ||Object.hasOwn(item,'document_id')||!positive(item.row_version)))
          throw Error('불러온 댓글이 현재 항목과 일치하지 않습니다.');
        s.remote=clone(result);return;
      }
      if(result?.contract!==contract||result.status!=='comments_loaded'||result.workspace_id!==s.context.workspaceId||result.document_id!==s.context.pageId
          ||!sameTarget(result.item_target,target(s.context))||!positive(result.workspace_epoch)||!positive(result.document_epoch)||!Array.isArray(result.items)
          ||result.items.some(item=>!sameTarget(item.anchor,target(s.context))||item.document_id!==s.context.pageId||!positive(item.row_version)))throw Error('불러온 댓글이 현재 항목과 일치하지 않습니다.');
      s.remote=clone(result);
    }
    async function refreshState(s){
      const token=++s.readGeneration;
      try{const result=await host.read(s.context);if(token!==s.readGeneration||!current(s))return false;acceptRead(s,result);if(!s.request&&!s.storageError)s.error='';return true;}
      catch(e){if(token===s.readGeneration&&current(s)){if([401,403,404].includes(e.status))s.remote=null;error(s,e);}return false;}
      finally{notify(s);}
    }
    async function bind(context){
      const id=identity(context);let s=states.get(id);
      if(!s){s={context,draft:empty(),request:null,ready:false,remote:null,queue:Promise.resolve(),writes:0,readGeneration:0};states.set(id,s);}else s.context=context;
      if(s.ready){await refreshState(s);return view(s);}if(s.loading)return s.loading;
      s.loading=(async()=>{
        s.journal=await journalScope(context,host.crypto);const list=await host.journal.list(s.journal);
        if(list.length>1)throw Error('보관한 댓글 상태를 확인할 수 없습니다.');
        s.stored=fingerprint(list[0]);if(list[0]){validateStored(s,list[0]);s.draft={...empty(),...clone(list[0].draft)};s.request=clone(list[0].request);}
        s.ready=true;await refreshState(s);return view(s);
      })().catch(e=>{error(s,e);return view(s);}).finally(()=>s.loading=null);return s.loading;
    }
    function draft(context,value){
      const s=get(context);if(!s?.ready||!current(s)||s.flight||s.request)return false;
      const next={...s.draft,...value};if(typeof next.content!=='string'||next.content.length>20000)return false;
      if(Object.hasOwn(value,'editing'))next.version=value.editing?s.remote?.items.find(item=>item.id===value.editing)?.row_version||0:0;
      s.draft=next;s.error='';void persist(s);return true;
    }
    function validateReceipt(s,request,result){
      const b=request.body;
      if(s.context.databaseOnly===true){
        if(result?.contract!==databaseContract||result.operation_id!==b.operation_id||result.comment_id!==request.comment
            ||result.workspace_id!==s.context.workspaceId||result.database_id!==s.context.databaseId||result.row_id!==s.context.rowId
            ||Object.hasOwn(result,'document_id')||!positive(result.document_sequence)
            ||result.workspace_epoch!==b.expected_workspace_epoch||result.document_epoch!==b.expected_document_epoch
            ||result.database_epoch!==b.expected_database_epoch
            ||['edit','resolve','react'].includes(request.action)&&result.row_version!==b.expected_row_version+1
            ||request.action==='delete'&&result.deleted!==true||request.action==='resolve'&&result.resolved!==b.resolved
            ||request.action==='react'&&(result.emoji!==b.emoji||typeof result.added!=='boolean'))
          throw Error('댓글 저장 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.');
        return;
      }
      if(result?.contract!==contract||result.status!==statuses[request.action]||result.operation_id!==b.operation_id||result.comment_id!==request.comment
          ||result.workspace_id!==s.context.workspaceId||result.document_id!==s.context.pageId||!positive(result.document_sequence)
          ||result.workspace_epoch!==b.expected_workspace_epoch||result.document_epoch!==b.expected_document_epoch
          ||['edit','resolve','react'].includes(request.action)&&result.row_version!==b.expected_row_version+1
          ||request.action==='delete'&&result.deleted!==true||request.action==='resolve'&&result.resolved!==b.resolved
          ||request.action==='react'&&(result.emoji!==b.emoji||typeof result.added!=='boolean'))throw Error('댓글 저장 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.');
    }
    async function deliver(s){
      const request=clone(s.request);validateStored(s,envelope(s));
      if(!allowed(s)||!(await persist(s))||!allowed(s))return false;
      const result=await host.send(s.context,clone(request));validateReceipt(s,request,result);
      const nextDraft=['create','edit'].includes(request.action)?empty():s.draft;
      // One durable replacement removes the request and its submitted draft together.
      if(!(await persist(s,envelope(s,null,nextDraft))))return false;
      s.request=null;s.draft=clone(nextDraft);s.error='';
      if(current(s)){
        try{await host.changed?.(s.context,result);}catch(e){error(s,e);}
        await refreshState(s);
      }
      return true;
    }
    function run(s,fn){
      if(s.flight)return s.flight;
      s.flight=Promise.resolve().then(fn).catch(e=>{error(s,e);return false;}).finally(()=>{s.flight=null;s.preparing=false;notify(s);});notify(s);return s.flight;
    }
    async function mutate(context,action,comment='',value){
      const s=get(context);if(!s||!allowed(s)||s.flight||s.request||s.composing||!Object.hasOwn(statuses,action))return false;
      const version=action==='edit'&&s.draft.editing===comment?s.draft.version:s.remote.items.find(item=>item.id===comment)?.row_version;
      return run(s,async()=>{
        s.preparing=true;if((await host.prepare?.(s.context))===false||!current(s))return false;s.preparing=false;
        if(!(await refreshState(s))||!allowed(s))return false;
        const item=s.remote.items.find(item=>item.id===comment);
        if(action!=='create'&&(!item||['edit','delete'].includes(action)&&!editable(s,item)))throw Error('이 댓글을 변경할 권한을 확인해 주세요.');
        if(action!=='create'&&item.row_version!==version)throw Error('다른 곳에서 댓글이 변경되었습니다. 작성한 내용을 보관한 뒤 최신 댓글을 확인해 주세요.');
        if(action==='create'&&s.draft.parent&&!s.remote.items.some(item=>item.id===s.draft.parent&&!item.parent_comment_id))throw Error('답글을 달 댓글이 없어졌습니다.');
        const body={operation_id:host.operationId(),expected_workspace_epoch:s.remote.workspace_epoch,expected_document_epoch:s.remote.document_epoch};
        if(s.context.databaseOnly===true)body.expected_database_epoch=s.remote.database_epoch;
        if(action==='create'){comment=host.commentId();Object.assign(body,{comment_id:comment,parent_comment_id:s.draft.parent});if(s.context.databaseOnly!==true)body.anchor=target(s.context);}
        else body.expected_row_version=item.row_version;
        if(['create','edit'].includes(action)){const content=s.draft.content.trim();if(!content)throw Error('댓글을 입력해 주세요.');body.content=content;body.mention_subject_ids=host.mentions?.(content)||[];}
        if(action==='resolve')body.resolved=Boolean(value);
        if(action==='react')body.emoji=value;
        s.request={action,comment,body};s.error='';notify(s);return deliver(s);
      });
    }
    async function beforeLeave(context){
      const s=get(context);if(!s)return true;if(s.loading)await s.loading;
      if(s.composing||s.request||s.flight&&!s.preparing){s.error='댓글 저장 확인을 마친 뒤 이동해 주세요.';notify(s);return false;}
      await s.queue;if(s.storageError)return persist(s);return true;
    }
    async function park(context){
      // Loss of source access removes the detail controls. Preserve the local
      // request without sending it, then allow the inaccessible pane to close.
      const s=get(context);if(!s)return true;if(s.loading)await s.loading;if(!s.ready)return false;
      if(s.flight)await s.flight;
      await s.queue;return s.storageError?persist(s):true;
    }
    function refresh(context){
      const s=get(context);if(!s||!current(s))return Promise.resolve(false);
      s.refreshRequested=true;if(s.refreshFlight)return s.refreshFlight;
      // A source witness may change while the initial journal/read or a save is
      // in flight. Queue that read instead of dropping the permission update.
      s.refreshFlight=Promise.resolve().then(async()=>{
        let result=false;
        do{
          if(s.loading)await s.loading;if(s.flight)await s.flight;
          if(!s.ready||!current(s))return false;
          s.refreshRequested=false;result=await refreshState(s);
        }while(s.refreshRequested);
        return result;
      }).finally(()=>s.refreshFlight=null);return s.refreshFlight;
    }
    return {bind,draft,mutate,snapshot:context=>view(get(context)),
      refresh,park,
      retry:context=>{const s=get(context);return s?.ready&&s.request&&allowed(s)?run(s,()=>deliver(s)):Promise.resolve(false);},
      discard:context=>{const s=get(context);if(!s?.journal||s.flight||s.loading||!current(s))return Promise.resolve(false);return run(s,async()=>{
        if(!(await persist(s,envelope(s,null,empty()))))return false;
        s.draft=empty();s.request=null;s.ready=true;s.error='';await refreshState(s);return true;
      });},
      beforeLeave,pending:context=>{const s=get(context);return Boolean(s&&(s.request||s.flight||s.writes||s.storageError||s.composing));},
      composing:(context,value)=>{const s=get(context);if(s)s.composing=value;},
      canEdit:(context,item)=>{const s=get(context);return Boolean(s&&editable(s,item));}};
  }
  return {create,identity,target,journalScope};
});

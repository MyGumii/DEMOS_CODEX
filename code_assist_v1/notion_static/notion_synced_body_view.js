(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionSyncedBodyView=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const sameLocation=(a,b)=>Boolean(a&&b&&['kind','database_id','row_id','template_id'].every(key=>(a[key]||'')===(b[key]||'')));
  function references(blocks){
    const result=new Map();
    function visit(list){for(const block of list||[]){
      if(block.type==='synced_block'&&block.props?.synced_from!==null){
        if(block.props?.synced_from?.document_id&&block.props.synced_from.block_id)result.set(block.id,block);
        continue; // A reference never owns cached children.
      }
      visit(block.props?.children);
    }}
    visit(blocks);return result;
  }
  function create(host){
    const scope=host.scope,slots=new Map();let disposed=false,canvas=null,detaching=false,sourceActions=[];
    const current=context=>!disposed&&context===scope&&host.current(context);
    const text=host.text.create({
      journal:host.journal,current,canEdit:()=>!detaching&&!detachPending()&&host.editable(),operationId:host.operationId,
      send:host.send,
      changed:async(context,id,result)=>{await host.changed(context,id,result);if(current(context))await views.refresh();},
      state:(context,id,view)=>{const slot=slots.get(id);if(slot&&current(context)){host.renderText(slot,view);host.state?.();}},
    });
    function detachPending(){return [...slots.keys()].some(id=>host.detachPending?.(id));}
    function paintDetach(){
      for(const slot of slots.values()){
        const button=slot.node.querySelector('[data-synced-detach]');if(!button)continue;
        const uncertain=Boolean(host.detachPending?.(slot.id));
        button.hidden=!host.detach||(!uncertain&&(!slot.data||!host.editable()));
        button.disabled=detaching;
        button.textContent=uncertain?'해제 결과 확인 · 재시도':'동기화 해제';
      }
    }
    async function detach(id,options={}){
      const slot=slots.get(id);if(!slot||!current(scope)||!host.detach)return;
      const recover=Boolean(options.lookupOnly||host.detachPending?.(id));
      if(detaching||(!recover&&!host.editable()))return {state:'busy'};
      // Finish encrypted shared-text drafts before blocking the body editor.
      if(!options.lookupOnly&&!(await text.flushPage(scope)))return;
      if(!current(scope))return;
      detaching=true;paintDetach();host.state?.();
      const flush=async()=>{
        if(!current(scope)||!(await text.flushPage(scope)))throw Error('저장되지 않은 입력이 있습니다.');
        if(!current(scope))throw Error('본문 편집이 종료되었습니다.');
      };
      try{
        const result=await host.detach(id,{
          ...options,current:()=>current(scope),beforeRetry:flush,
          validateResult:result=>{
            if(result?.storage!=='database_body'||!sameLocation(result.location,host.location)
                ||!Number.isSafeInteger(result.target_version)||result.target_version<1)throw Error('동기화 해제 결과의 본문 위치가 다릅니다.');
          },
          prepare:async()=>{
            await flush();const data=await host.load(scope,id);
            if(!current(scope)||!sameLocation(data?.locations?.instance,host.location)
                ||data.origin?.document_id!==slot.origin.document_id||data.origin?.block_id!==slot.origin.block_id)throw Error('동기화 참조의 본문 위치가 변경되었습니다.');
            return {origin:data.origin,witnesses:data.witnesses};
          },
        });
        if(!current(scope))return result;
        if(result?.state==='committed'){
          if(result.result?.storage!=='database_body'||!sameLocation(result.result.location,host.location))throw Error('동기화 해제 결과의 본문 위치가 다릅니다.');
          await host.changed(scope,id,result.result);
        }else if(result?.state==='stale'){await host.changed(scope,id);}
        return result;
      }finally{detaching=false;if(current(scope)){paintDetach();host.state?.();}}
    }
    const views=host.references.create({
      current,visible:slot=>!document.hidden&&current(scope)&&Boolean(canvas?.isConnected)&&(!slot||slot.node.isConnected&&!slot.node.closest('[hidden]')),
      load:async(context,id,signal)=>{
        const data=await host.load(context,id,signal);
        if(!sameLocation(data?.locations?.instance,host.location))throw Error('동기화 참조의 본문 위치가 변경되었습니다.');
        return data;
      },
      watch:host.watch,
      display(slot,state){
        if(!current(scope)||!slot.node.isConnected)return;
        const content=slot.node.querySelector('.synced-reference-content'),status=slot.node.querySelector('.synced-reference-status'),open=slot.node.querySelector('[data-synced-open]');
        slot.node.setAttribute('aria-busy',String(state.phase==='loading'));
        if(state.phase==='ready'){
          slot.data=state.data;open.hidden=false;
          host.describe?.(slot.node,state.data);
          slot.binding=(async()=>{
            await host.accept?.(state.data);
            if(!current(scope)||!slot.node.isConnected||slot.data!==state.data)return;
            await text.bind(scope,slot.id,state.data);
            if(current(scope)&&slot.node.isConnected&&slot.data===state.data){
              const value=text.snapshot(scope,slot.id);
              if(value){host.renderText(slot,value);open.hidden=!value.available;}
              paintDetach();
            }
          })().catch(host.error);return;
        }
        if(state.phase==='loading'&&content.childElementCount)return;
        if(state.phase==='error'||state.phase==='idle')text.unavailable(scope,slot.id);
        content.replaceChildren();slot.signature='';slot.data=null;open.hidden=true;status.hidden=false;paintDetach();
        status.textContent=state.phase==='loading'?'동기화된 내용 불러오는 중…':state.denied?'원본이 없거나 접근할 수 없습니다.':'동기화된 내용을 불러오지 못했습니다. 다시 시도해 주세요.';
      },
    });
    function mount(root,blocks){
      if(disposed)return;
      for(const slot of slots.values()){clearTimeout(slot.saveTimer);slot.actions?.dispose();}
      sourceActions.forEach(value=>value.dispose());sourceActions=[];
      // Clear old observers before binding replacement DOM, retaining encrypted drafts.
      views.dispose();slots.clear();canvas=root;
      const found=references(blocks);
      for(const node of root.querySelectorAll('[data-synced-reference]')){
        const block=found.get(node.dataset.syncedReference);if(!block)continue;
        const slot={id:block.id,origin:structuredClone(block.props.synced_from),node,scope,text,current:()=>current(scope),fileUrl:host.fileUrl,
          focus:host.focus,afterBlur:host.afterBlur};
        slots.set(slot.id,slot);host.bindText(slot);
        slot.actions=window.NotionSyncedActions.bind(node,{...host.locationHost?.(slot.id,slot.origin,()=>text.flushPage(scope)),current:()=>current(scope),identity:JSON.stringify([host.identity,slot.id,slot.origin])});
        node.querySelector('[data-synced-refresh]').onclick=()=>void views.refresh();
        const detachButton=node.querySelector('[data-synced-detach]');
        if(detachButton)detachButton.onclick=()=>void detach(slot.id).catch(host.error);
        node.querySelector('[data-synced-open]').onclick=()=>void (async()=>{
          if(!(await text.flushPage(scope))||!current(scope))return;
          // Reauthorize and resolve storage again immediately before navigation.
          const data=await host.load(scope,slot.id);
          if(!current(scope)||!sameLocation(data?.locations?.instance,host.location)
              ||data.origin?.document_id!==slot.origin.document_id||data.origin?.block_id!==slot.origin.block_id)throw Error('동기화 원본 위치가 변경되었습니다.');
          await host.open(data);
        })().catch(host.error);
      }
      const sources=new Map();
      function visit(list){for(const block of list||[]){if(block.type==='synced_block'&&block.props?.synced_from===null)sources.set(block.id,block);visit(block.props?.children);}}
      visit(blocks);
      for(const node of root.querySelectorAll('[data-synced-source]')){
        const block=sources.get(node.dataset.syncedSource);if(!block||!host.locationHost)continue;
        const origin={document_id:scope.pageId,block_id:block.id};
        sourceActions.push(window.NotionSyncedActions.bind(node,{...host.locationHost(block.id,origin,()=>text.flushPage(scope)),source:true,current:()=>current(scope),identity:JSON.stringify([host.identity,block.id,origin])}));
        node.querySelector('[data-synced-refresh]').onclick=()=>void refresh();
      }
      paintDetach();
      if(slots.size)void views.mount(scope,[...slots.values()]);
    }
    const refresh=async()=>{
      if(!current(scope))return;
      await views.refresh({afterFlight:true});
      if(current(scope))await Promise.all([...slots.values()].map(slot=>slot.binding));
    };
    window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',refresh);
    return {mount,refresh,clear:()=>views.clear(),flush:()=>!detaching&&!detachPending()&&text.flushPage(scope),pending:()=>detaching||detachPending()||text.pending(scope),
      detachBusy:()=>detaching||detachPending(),
      detach,hasReference:id=>slots.has(id),
      holdsFocus:blocks=>{
        // Pending text must not remount the toolbar between pointer focus and
        // its click, or replace an open original-location menu. Only retain the
        // exact current reference; structural changes still replace its DOM.
        const node=document.activeElement?.closest?.('[data-synced-reference]');
        if(!current(scope)||!node?.isConnected||!canvas?.contains(node))return false;
        const slot=slots.get(node.dataset.syncedReference),next=references(blocks).get(slot?.id);
        return Boolean(slot?.node===node&&slot.data&&next
          &&next.props.synced_from.document_id===slot.origin.document_id
          &&next.props.synced_from.block_id===slot.origin.block_id);
      },
      syncEditing:()=>{for(const slot of slots.values()){const value=text.snapshot(scope,slot.id);if(value)host.renderText(slot,value);}paintDetach();},
      dispose(){if(disposed)return;views.dispose();disposed=true;for(const slot of slots.values()){clearTimeout(slot.saveTimer);slot.actions?.dispose();}sourceActions.forEach(value=>value.dispose());sourceActions=[];slots.clear();window.removeEventListener('focus',refresh);document.removeEventListener('visibilitychange',refresh);},
    };
  }
  return {create,references,sameLocation};
});

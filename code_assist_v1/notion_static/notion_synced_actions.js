(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionSyncedActions=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const icon=path=>`<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
  const origin=icon('<path d="M17 7H7v10M7 7l10 10"/>'),more=icon('<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>');
  let active=null,serial=0,focusIntent=null,focusTimer=null;
  function clearFocusIntent(){
    focusIntent=null;clearTimeout(focusTimer);focusTimer=null;
    document.removeEventListener('pointerdown',clearFocusIntent,true);document.removeEventListener('focusin',cancelFocusedIntent,true);
  }
  function cancelFocusedIntent(event){if(![document.body,document.documentElement].includes(event.target))clearFocusIntent();}
  function saveFocusIntent(identity,kind){
    clearFocusIntent();focusIntent={identity,kind,expires:Date.now()+5000};focusTimer=setTimeout(clearFocusIntent,5000);
    document.addEventListener('pointerdown',clearFocusIntent,true);document.addEventListener('focusin',cancelFocusedIntent,true);
  }
  const sameEndpoint=(a,b)=>Boolean(a&&b&&a.document_id===b.document_id&&a.block_id===b.block_id);
  function inventory(data,instance,original){
    if(data?.contract!=='code_assist.notion.synced_locations.v1'||data.complete!==true
        ||!sameEndpoint(data.instance,instance)||!sameEndpoint(data.origin,original)||!Array.isArray(data.entries))throw Error('동기화 대상 위치를 확인할 수 없습니다.');
    const ids=new Set();let originals=0,found=false;
    for(const entry of data.entries){
      const endpoint=entry?.endpoint,location=entry?.location;
      if(!endpoint?.document_id||!endpoint.block_id||!entry.workspace_id||typeof entry.page_title!=='string'||typeof entry.body_title!=='string'
          ||!['page','row','template'].includes(location?.kind)||typeof entry.original!=='boolean'
          ||location.kind!=='page'&&(!location.database_id||!(location.kind==='row'?location.row_id:location.template_id)))throw Error('동기화 대상 위치가 올바르지 않습니다.');
      const id=JSON.stringify([endpoint.document_id,endpoint.block_id]);if(ids.has(id))throw Error('중복 동기화 대상입니다.');ids.add(id);
      if(entry.original){originals++;if(!sameEndpoint(endpoint,original))throw Error('동기화 원본이 변경되었습니다.');}
      found ||= sameEndpoint(endpoint,instance);
    }
    if(originals!==1||!found||data.other_count!==data.entries.filter(entry=>!sameEndpoint(entry.endpoint,instance)).length)throw Error('동기화 대상 개수가 올바르지 않습니다.');
    return data.entries;
  }
  function visibleLocations(entries,instance){
    const groups=new Map();
    for(const entry of entries){
      const location=entry.location,key=JSON.stringify([entry.endpoint.document_id,location.kind,location.database_id||'',location.row_id||'',location.template_id||'']);
      const previous=groups.get(key);
      if(!previous||entry.original||!previous.original&&sameEndpoint(entry.endpoint,instance))groups.set(key,entry);
    }
    return [...groups.values()].sort((a,b)=>Number(b.original)-Number(a.original));
  }
  function html({blockMenu=false,detach=false,enabled=true,source=false,copy=false}={}){
    const id=`synced-actions-${++serial}`;
    const refresh=`<button type="button" role="menuitem" data-synced-refresh${enabled?'':' hidden'}>새로고침</button>`;
    return `<div class="synced-reference-actions" role="toolbar" aria-label="동기화 블록 작업">
      <button type="button" data-synced-locations aria-label="동기화 원본 위치" title="동기화 대상 위치" aria-haspopup="menu" aria-controls="${id}-origin" aria-expanded="false" disabled>${origin}<span data-synced-location-count>원본 위치</span><span aria-hidden="true">⌄</span></button>
      ${copy?'<button type="button" data-synced-source-copy aria-label="동기화 복사" title="복사 후 붙여넣어 동기화">'+icon('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/>')+'</button>':''}
      <button type="button" data-synced-actions-menu aria-label="동기화 블록 작업" title="동기화 블록 작업" aria-haspopup="${blockMenu?'dialog':'menu'}" aria-controls="${blockMenu?'blockMenu':id+'-menu'}" aria-expanded="false"${enabled?'':' disabled'}>${more}</button>
      <div id="${id}-origin" class="synced-action-popover" data-synced-locations-panel role="menu" aria-label="동기화 원본" popover="manual" hidden><div class="synced-action-heading">동기화 대상</div><button type="button" role="menuitem" aria-label="원본 열기" data-synced-open hidden>${origin}<span><span data-synced-origin-name>원본 페이지</span><small data-synced-origin-kind>원본</small></span></button><div data-synced-peer-list></div><div data-synced-location-status role="status" class="synced-action-heading" hidden></div>${blockMenu?refresh:''}</div>
      ${blockMenu?'':`<div id="${id}-menu" class="synced-action-popover" data-synced-actions-panel role="menu" aria-label="동기화 블록 작업" popover="manual" hidden>${refresh}${detach?'<button type="button" role="menuitem" data-synced-detach hidden>동기화 해제</button>':''}</div>`}
    </div>`;
  }
  function describe(root,{title='원본 페이지',detail='원본'}={}){
    root.querySelector('[data-synced-origin-name]').textContent=title;
    root.querySelector('[data-synced-origin-kind]').textContent=detail;
  }
  function bind(root,host={}){
    const locations=root.querySelector('[data-synced-locations]'),moreButton=root.querySelector('[data-synced-actions-menu]');
    const open=root.querySelector('[data-synced-open]'),originPanel=root.querySelector('[data-synced-locations-panel]'),actionPanel=root.querySelector('[data-synced-actions-panel]');
    const peers=root.querySelector('[data-synced-peer-list]'),listStatus=root.querySelector('[data-synced-location-status]'),count=root.querySelector('[data-synced-location-count]');
    const copyButton=root.querySelector('[data-synced-source-copy]');
    let disposed=false,panel=null,trigger=null,lastFocus=null,listFlight=null,listed=[],copyFlight=false,composing=false;
    const current=()=>!disposed&&root.isConnected&&(!host.current||host.current());
    const copyCurrent=()=>host.copyCurrent?host.copyCurrent():current();
    const items=()=>[...panel.querySelectorAll('[role="menuitem"]')].filter(button=>!button.hidden&&!button.disabled);
    function place(){
      if(!panel)return;
      const rect=trigger.getBoundingClientRect(),width=Math.min(280,Math.max(0,window.innerWidth-16));
      panel.style.width=`${width}px`;panel.style.maxHeight=`${Math.max(40,window.innerHeight-16)}px`;
      const height=panel.getBoundingClientRect().height;
      panel.style.left=`${Math.max(8,Math.min(window.innerWidth-width-8,rect.right-width))}px`;
      panel.style.top=`${Math.max(8,Math.min(window.innerHeight-height-8,rect.bottom+4))}px`;
    }
    function close(restore=false){
      if(!panel)return;
      const previous=trigger;trigger.setAttribute('aria-expanded','false');
      listFlight?.abort();listFlight=null;listed=[];peers.replaceChildren();listStatus.hidden=true;
      try{panel.hidePopover?.();}catch(_){}panel.hidden=true;panel=null;trigger=null;
      if(active===controller)active=null;
      document.removeEventListener('pointerdown',outside,true);document.removeEventListener('focusin',blur,true);
      window.removeEventListener('resize',place);window.removeEventListener('scroll',scroll,true);
      if(restore&&current()&&!previous.disabled)previous.focus({preventScroll:true});
    }
    function outside(event){if(!panel?.contains(event.target)&&!trigger?.contains(event.target))close();}
    function blur(event){if(!panel?.contains(event.target)&&!trigger?.contains(event.target))close();}
    function scroll(event){if(!panel?.contains(event.target))close();}
    function show(button,next){
      if(!current()||button.disabled||!next)return;
      if(panel===next){close(true);return;}
      active?.close();close();panel=next;trigger=button;active=controller;
      next.hidden=false;try{next.showPopover?.();}catch(_){}button.setAttribute('aria-expanded','true');place();
      document.addEventListener('pointerdown',outside,true);document.addEventListener('focusin',blur,true);
      window.addEventListener('resize',place);window.addEventListener('scroll',scroll,true);
      items()[0]?.focus({preventScroll:true});
      if(next===originPanel)void loadLocations();
    }
    async function loadLocations(){
      if(!host.loadLocations||panel!==originPanel||!current())return;
      listFlight?.abort();const request=listFlight=new AbortController(),initialFocus=document.activeElement;listed=[];peers.replaceChildren();listStatus.hidden=false;listStatus.textContent='대상 불러오는 중…';
      try{
        const data=await host.loadLocations(request.signal);
        if(listFlight!==request||request.signal.aborted||!current()||panel!==originPanel)return;
        const entries=visibleLocations(inventory(data,host.instance,host.origin),host.instance);listed=entries;
        count.textContent=`${data.other_count}곳에 동기화`;
        const sourceEntry=data.entries.find(entry=>entry.original);
        describe(root,host.describeLocation(sourceEntry));
        open.hidden=false;
        for(const entry of entries.filter(value=>!value.original)){
          const button=document.createElement('button');button.type='button';button.setAttribute('role','menuitem');button.dataset.syncedPeer=JSON.stringify(entry.endpoint);
          const label=host.describeLocation(entry);const text=document.createElement('span');text.textContent=label.title;
          const detail=document.createElement('small');detail.textContent=label.detail;detail.className='synced-peer-detail';text.append(detail);button.append(text);peers.append(button);
        }
        listStatus.hidden=entries.length>0;listStatus.textContent=entries.length?'':'다른 동기화 대상이 없습니다.';place();
        if((!originPanel.contains(document.activeElement)||host.source&&document.activeElement===initialFocus)&&trigger===locations)items()[0]?.focus({preventScroll:true});
      }catch(error){
        if(listFlight!==request||request.signal.aborted||!current()||panel!==originPanel)return;
        listed=[];peers.replaceChildren();count.textContent='원본 위치';listStatus.hidden=false;listStatus.textContent='대상을 불러오지 못했습니다. 다시 열어 주세요.';place();
        if([401,403,404].includes(error.status)){describe(root);close();host.unavailable?.();}
      }finally{if(listFlight===request)listFlight=null;}
    }
    function sync(){
      locations.disabled=host.source?false:open.hidden;
      if(copyButton)copyButton.disabled=copyFlight||locations.disabled||!host.copy;
      if(open.hidden&&!host.source){describe(root);count.textContent='원본 위치';if(panel===originPanel)close();}
      if(panel&&!current())close();
      if(focusIntent){
        if(focusIntent.expires<Date.now())clearFocusIntent();
        else if(focusIntent.identity===host.identity&&current()){
          const button=focusIntent.kind==='locations'?locations:moreButton;
          const emptyFocus=[document.body,document.documentElement,null].includes(document.activeElement);
          if(!emptyFocus){clearFocusIntent();return;}
          if(!button.disabled){clearFocusIntent();button.focus({preventScroll:true});}
        }
      }
    }
    function rememberFocus(event){
      lastFocus=event.target===locations||originPanel.contains(event.target)?'locations'
        :event.target===moreButton||actionPanel?.contains(event.target)?'more':null;
    }
    function forgetFocus(event){
      if(root.contains(event.relatedTarget))return;
      if(event.relatedTarget){lastFocus=null;return;}
      // Removing focused markup can fire blur before isConnected changes.
      queueMicrotask(()=>{if(!disposed&&root.isConnected&&!root.contains(document.activeElement))lastFocus=null;});
    }
    function click(event){
      const button=event.target.closest('button');if(!button||!root.contains(button))return;
      if(button===copyButton){
        event.preventDefault();event.stopPropagation();
        if(copyFlight||button.disabled||composing||event.isComposing||!current())return;
        copyFlight=true;sync();
        void Promise.resolve().then(()=>host.copy(()=>copyCurrent()&&!composing)).catch(error=>{if(copyCurrent())host.error?.(error);}).finally(()=>{copyFlight=false;if(current())sync();});
      }else if(button===locations||button===moreButton){
        event.preventDefault();event.stopPropagation();
        if(!current())return;
        if(button===moreButton&&host.more){close();host.more(button);}
        else show(button,button===locations?originPanel:actionPanel);
      }else if(panel?.contains(button)){
        event.stopPropagation();
        const selected=button===open&&host.source?listed.find(entry=>entry.original):button.dataset.syncedPeer&&listed.find(entry=>JSON.stringify(entry.endpoint)===button.dataset.syncedPeer);
        close();if(selected&&current())void host.openLocation(selected).catch(host.error);
      }
    }
    function key(event){
      if(event.isComposing||event.keyCode===229)return;
      if(!panel&&['ArrowDown','ArrowUp'].includes(event.key)&&[locations,moreButton].includes(event.target)){
        event.preventDefault();event.stopPropagation();
        if(event.target===moreButton&&host.more){if(current())host.more(moreButton);}
        else show(event.target,event.target===locations?originPanel:actionPanel);
        return;
      }
      if(!panel)return;
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close(true);return;}
      if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
      const buttons=items();if(!buttons.length)return;
      event.preventDefault();event.stopPropagation();const index=buttons.indexOf(document.activeElement);
      buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length].focus();
    }
    const compositionStart=()=>{composing=true;},compositionEnd=()=>{composing=false;};
    const controller={close,sync,dispose(){
      // A render can remove the focused DOM before old bindings are disposed.
      // Only a freshly mounted current reader with the exact same identity may
      // consume this short-lived intent, and never after the user focuses elsewhere.
      if(lastFocus&&host.identity&&([document.body,document.documentElement,null].includes(document.activeElement)||root.contains(document.activeElement)))
        saveFocusIntent(host.identity,lastFocus);
      close();disposed=true;observer.disconnect();root.removeEventListener('click',click);root.removeEventListener('keydown',key);
      root.removeEventListener('focusin',rememberFocus);root.removeEventListener('focusout',forgetFocus);
      root.removeEventListener('compositionstart',compositionStart,true);root.removeEventListener('compositionend',compositionEnd,true);
    }};
    const observer=new MutationObserver(sync);observer.observe(open,{attributes:true,attributeFilter:['hidden']});
    root.addEventListener('click',click);root.addEventListener('keydown',key);
    root.addEventListener('focusin',rememberFocus);root.addEventListener('focusout',forgetFocus);
    root.addEventListener('compositionstart',compositionStart,true);root.addEventListener('compositionend',compositionEnd,true);sync();return controller;
  }
  return {html,bind,describe,inventory,visibleLocations,sameEndpoint};
});

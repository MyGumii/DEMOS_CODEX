(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionDatabaseItemEditor=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const clone=value=>structuredClone(value);
  const types=new Set(['paragraph','heading1','heading2','heading3','heading4','toggle_heading1','toggle_heading2','toggle_heading3','toggle_heading4','bullet','numbered','todo','toggle','quote','callout','code','divider','simple_table','tabs']);
  function create(root,host){
    const core=host.core,escape=host.escape;let model=null,blocks=[],last=null,composing=false,menu=null,menuIndex=0,timer=null,history=[],historyIndex=-1,lastInput=0,disposed=false,deferred=false,lastSyncedPending=false;
    root.innerHTML='<div class="item-body-notice" role="status"></div><div class="item-body-blocks"></div><div class="item-body-menu" role="listbox" aria-label="본문 블록 선택" hidden></div>';
    const notice=root.querySelector('.item-body-notice'),canvas=root.querySelector('.item-body-blocks'),popup=root.querySelector('.item-body-menu');
    const blockOps=host.blockOps,interaction=host.interaction,style=host.style,selection=new Set(),
      surface=host.surface||root.closest('#linkedItemPeek'),
      scroller=host.scroller||root.closest('[data-linked-item-body]');
    const tabs=host.tabs,tabViews=new Map();
    const colorMenu=document.createElement('div');colorMenu.className='item-body-menu item-body-color-menu';colorMenu.hidden=true;colorMenu.setAttribute('role','menu');colorMenu.setAttribute('aria-label','블록 색상');root.append(colorMenu);
    let anchorId='',focusId='',gesture=null,blockMove=null,inputRevision=0;
    canvas.tabIndex=0;canvas.setAttribute('aria-label','항목 본문 블록 선택');
    const selectionStatus=document.createElement('span');selectionStatus.className='item-body-selection-status';selectionStatus.setAttribute('role','status');root.append(selectionStatus);
    const canEdit=()=>Boolean(!disposed&&model?.ready&&model.editable&&!host.synced?.pending()&&!host.syncedPaste?.snapshot().pending&&(!model.pending||model.busy));
    const mediaTypes=host.resources?.types||new Set();
    const resources=host.resources?.create(root,{
      escape,html:host.html,fileUrl:host.fileUrl,place:interaction.place,editable:canEdit,
      get:id=>find(id)?.block,upload:host.uploadResource,
      apply:(id,value,signature)=>{
        const item=find(id);if(!canEdit()||!item||host.resources.signature(item.block)!==signature)return false;
        const before=item.block;item.list[item.index]=value;
        if(!changed(true)){item.list[item.index]=before;return false;}
        draw();focus(id);return true;
      },
    });
    const visibleRows=()=>[...canvas.querySelectorAll('[data-body-block]')].filter(row=>row.getClientRects().length);
    const visibleIds=()=>visibleRows().map(row=>row.dataset.bodyBlock);
    function paintSelection(){
      const available=new Set(visibleIds());for(const id of selection)if(!available.has(id))selection.delete(id);
      for(const row of canvas.querySelectorAll('[data-body-block]'))row.classList.toggle('item-body-selected',selection.has(row.dataset.bodyBlock));
      selectionStatus.textContent=selection.size?`선택한 본문 블록 ${selection.size}개`:'';
    }
    function select(ids,endpoints={}){selection.clear();ids.forEach(id=>selection.add(id));anchorId=endpoints.anchorId||ids[0]||'';focusId=endpoints.focusId||ids.at(-1)||'';paintSelection();}
    function clearDrop(){canvas.querySelectorAll('[data-body-drop]').forEach(row=>delete row.dataset.bodyDrop);}
    function startSelection(event){
      if(disposed||composing||!model?.ready||!surface||surface.hidden||!root.isConnected||!surface.contains(event.target))return null;
      if(event.target.closest('button,a,input,textarea,select,[contenteditable],.item-body-content,.side-peek-head,.side-peek-properties,.database-item-comments,.linked-item-save-status,.item-body-notice,.item-appearance-cover,.item-appearance-notice,.linked-item-resize,[role="menu"],[role="listbox"],[role="dialog"]'))return null;
      return {canvas:scroller,surface,scroller};
    }
    function applyBlocks(result){
      if(!result||!canEdit())return false;
      const previous=blocks;blocks=result.blocks;
      if(!changed(true)){blocks=previous;draw();return false;}
      if(!blocks.length)blocks=[core.createBlock()];draw();select(result.ids);canvas.focus({preventScroll:true});return true;
    }
    function restoreHistory(delta){
      if(!canEdit())return false;const next=historyIndex+delta;if(next<0||next>=history.length)return false;
      historyIndex=next;blocks=JSON.parse(history[next]);last=host.normalize(blocks);host.change(clone(blocks));
      if(!blocks.length)blocks=[core.createBlock()];draw();select([]);clearTimeout(timer);timer=setTimeout(()=>void host.save(),700);return true;
    }
    function find(id,list=blocks){for(let index=0;index<list.length;index++){const block=list[index];if(block.id===id)return {block,list,index};const child=find(id,Array.isArray(block.props?.children)?block.props.children:[]);if(child)return child;}return null;}
    function remember(force=false){const value=JSON.stringify(blocks);if(history[historyIndex]===value)return;const now=Date.now();if(!force&&historyIndex>0&&now-lastInput<600)history[historyIndex]=value;else{history=history.slice(0,historyIndex+1);history.push(value);if(history.length>100)history.shift();historyIndex=history.length-1;}lastInput=now;}
    function changed(force=false){if(!canEdit())return false;remember(force);last=host.normalize(blocks);if(!host.change(clone(blocks)))return false;clearTimeout(timer);timer=setTimeout(()=>{if(!disposed&&!composing)void host.save();},700);return true;}
    function focus(id,offset=0){const el=canvas.querySelector(`[data-body-text="${CSS.escape(id)}"]`);if(el){select([]);el.focus({preventScroll:true});host.setCaret(el,offset);}}
    function closeColors(){colorMenu.hidden=true;colorMenu.replaceChildren();popup.querySelector('[data-body-action="color"]')?.setAttribute('aria-expanded','false');}
    function closeMenu(){closeColors();menu=null;popup.hidden=true;popup.replaceChildren();}
    function openColors(){
      if(!menu||!canEdit()||composing)return;
      const item=find(menu.id),anchor=popup.querySelector('[data-body-action="color"]');if(!item||!anchor)return;
      const ids=selection.has(menu.id)?[...selection]:[menu.id],selected=ids.map(id=>find(id)?.block).filter(Boolean);
      colorMenu.innerHTML=[['text_color','텍스트 색상','텍스트','text'],['background_color','배경 색상','배경','bg']].map(([key,title,suffix,prefix])=>
        `<div role="group" aria-label="${title}"><div class="item-body-menu-label">${title}</div>`+style.colors.map(({value,label})=>{
          const checked=selected.length>0&&selected.every(block=>style.value(block.props,key)===value);
          return `<button type="button" role="menuitemradio" aria-checked="${checked}" aria-label="${label} ${suffix}" data-body-color="${value}" data-color-key="${key}"><span aria-hidden="true" class="color-swatch ${prefix}-${value}">${prefix==='text'?'A':''}</span><span>${label} ${suffix}</span><span class="item-color-check" aria-hidden="true">${checked?'✓':''}</span></button>`;
        }).join('')+'</div>').join('');
      colorMenu.hidden=false;anchor.setAttribute('aria-expanded','true');interaction.place(colorMenu,anchor,'right');colorMenu.querySelector('button')?.focus();
    }
    function save(){clearTimeout(timer);return host.save();}
    function sibling(block,type='paragraph'){
      const props={};for(const key of ['indent','layout_group','layout_column','layout_columns','tab_group','tab_id'])if(block.props?.[key]!=null)props[key]=block.props[key];
      return core.createBlock(type,{props});
    }
    function focusTab(id,tab){select([]);canvas.querySelector(`[data-body-tab-owner="${CSS.escape(id)}"][data-body-tab="${CSS.escape(tab)}"]`)?.focus({preventScroll:true});}
    function tabAction(id,action,options={}){
      const owner=find(id)?.block;if(!tabs||owner?.type!=='tabs'||composing)return false;
      if(action==='select'&&!canEdit()){
        if(!tabs.describe(owner).tabs.some(t=>t.id===options.tabId))return false;
        closeMenu();tabViews.set(id,options.tabId);draw();select([]);focusTab(id,options.tabId);return true;
      }
      if(!canEdit())return false;
      const result=tabs.mutate(blocks,id,action,{...options,createBlock:core.createBlock});if(!result)return false;
      closeMenu();tabViews.delete(id);if(!applyBlocks(result))return false;
      if(result.focusBlockId)focus(result.focusBlockId);else focusTab(id,result.focusTabId);return true;
    }
    function openTabMenu(id,tabId,target){
      const owner=find(id)?.block,info=owner&&tabs.describe(owner),tab=info?.tabs.find(t=>t.id===tabId);if(!tab||!canEdit()||composing)return;
      menu={id,tabId,tabMenu:true};closeColors();popup.hidden=false;popup.setAttribute('role','menu');popup.setAttribute('aria-label','탭 옵션');
      popup.innerHTML=`<label class="item-body-tab-name">탭 이름<input data-body-tab-name aria-label="탭 이름" value="${escape(tab.name)}" maxlength="80"></label>`+
        [['rename','이름 저장'],['duplicate','탭 복제'],['left','왼쪽으로 이동'],['right','오른쪽으로 이동'],['remove','탭 삭제']].map(([action,label])=>`<button type="button" role="menuitem" data-body-tab-action="${action}"${action==='remove'&&info.tabs.length<2||action==='left'&&info.tabs[0].id===tabId||action==='right'&&info.tabs.at(-1).id===tabId?' disabled':''}>${label}</button>`).join('');
      interaction.place(popup,target,'right');popup.querySelector('input')?.focus();popup.querySelector('input')?.select();
    }
    function options(){return core.filterBlockDefinitions(menu?.query||'').filter(option=>types.has(option.type)||mediaTypes.has(option.type));}
    function drawMenu(){
      if(!menu||composing||!canEdit()){popup.hidden=true;return;}
      closeColors();
      popup.setAttribute('role','listbox');popup.setAttribute('aria-label','본문 블록 선택');
      const all=options();menuIndex=Math.max(0,Math.min(menuIndex,all.length-1));
      popup.innerHTML='<div class="item-body-menu-label">기본 블록</div>'+all.map((option,index)=>`<button type="button" role="option" aria-selected="${index===menuIndex}" data-body-type="${option.type}"><span>${escape(option.icon)}</span><span>${escape(option.label)}</span></button>`).join('')+(all.length?'':'<span class="item-body-menu-label">일치하는 블록이 없습니다.</span>');
      const anchor=canvas.querySelector(`[data-body-block="${CSS.escape(menu.id)}"]`);
      popup.hidden=false;interaction.place(popup,anchor);popup.querySelector('[aria-selected="true"]')?.scrollIntoView({block:'nearest'});
    }
    function choose(type){
      if(!menu||!canEdit())return;const selected=find(menu.id),insert=menu.insert;if(!selected)return;
      let target=selected.block;
      if(insert){target=sibling(selected.block,type);if(type==='tabs')target=tabs.initialize(target,core.createBlock);selected.list.splice(selected.index+1,0,target);}
      else{const ids=menu.query==null&&selection.has(target.id)?blockOps.roots(blocks,[...selection]):[target.id];for(const id of ids){const entry=find(id);let block=entry.block;if(menu.query!=null){block.content='';delete block.props.rich_text_html;}
        if(type==='tabs'&&block.type!=='tabs')block=tabs.initialize(block,core.createBlock);
        else{if(block.type==='tabs'&&type!=='tabs')block=tabs.unwrap(block);block.type=type;block.props={...core.createBlock(type).props,...block.props};}
        entry.list[entry.index]=block;if(id===target.id)target=block;}}
      closeMenu();changed(true);draw();if(mediaTypes.has(type))resources?.open(target.id);else if(type==='tabs')focusTab(target.id,tabs.describe(target).active);else focus(target.id);
    }
    function listHtml(list,depth=0,parentProps={}){
      let output='',columns=null;const counters=new Map();
      const flush=()=>{if(columns){output+=`<div class="block-column-layout" data-layout-columns="${columns.items.length}" style="--layout-columns:${columns.items.length}">${columns.items.map(items=>`<div class="block-column">${items.join('')}</div>`).join('')}</div>`;columns=null;}};
      for(const block of list){
        const p=block.props||{},count=Math.max(2,Math.min(5,Math.trunc(Number(p.layout_columns)||2))),column=Math.max(0,Math.min(count-1,Math.trunc(Number(p.layout_column)||0)));
        const key=JSON.stringify([p.layout_group||'',column,p.tab_group||'',p.tab_id||'']);
        counters.set(key,block.type==='numbered'?(counters.get(key)||0)+1:0);
        const body=html(block,depth,counters.get(key));
        const inherited=p.layout_group&&p.layout_group===parentProps.layout_group&&column===Number(parentProps.layout_column||0);
        if(p.layout_group&&!inherited){
          const group=JSON.stringify([p.layout_group,count,p.tab_group||'',p.tab_id||'']);
          if(!columns||columns.key!==group){flush();columns={key:group,items:Array.from({length:count},()=>[])};}
          columns.items[column].push(body);
        }else{flush();output+=body;}
      }
      flush();return output;
    }
    function html(block,depth=0,ordinal=1){
      const id=escape(block.id),type=block.type,editable=canEdit()&&host.textEditable?.(block.id)!==false,props=block.props||{},label=core.BLOCK_DEFINITIONS.find(option=>option.type===type)?.label||type;
      if(core.isLayoutContainer(block)&&depth<32)return listHtml(props.children,depth+1,props);
      const children=type!=='tabs'&&!(type==='synced_block'&&props.synced_from!==null)&&Array.isArray(props.children)&&depth<32?`<div class="item-body-children" ${type.startsWith('toggle')&&props.open===false?'hidden':''}>${listHtml(props.children,depth+1,props)}${editable&&type.startsWith('toggle')?`<button type="button" class="item-body-child-add" data-body-child="${id}">하위 블록 추가</button>`:''}</div>`:'';
      let content='';
      if(type==='tabs'&&tabs){
        const info=tabs.describe(block,tabViews.get(block.id)),childList=Array.isArray(props.children)?props.children:[];
        content=`${block.content?`<div class="item-body-tab-caption">${host.html(block)}</div>`:''}<div class="item-body-tabs" role="tablist" aria-label="항목 본문 탭">`+
          info.tabs.map(tab=>{const key=`body-tab-${id}-${escape(tab.id)}`,selected=tab.id===info.active;return `<span class="item-body-tab-chip"><button type="button" role="tab" id="${key}" aria-controls="${key}-panel" aria-selected="${selected}" tabindex="${selected?0:-1}" data-body-tab-owner="${id}" data-body-tab="${escape(tab.id)}">${escape(tab.name)}</button>${editable?`<button type="button" class="item-body-tab-more" aria-label="${escape(tab.name)} 탭 옵션" data-body-tab-owner="${id}" data-body-tab-menu="${escape(tab.id)}">···</button>`:''}</span>`;}).join('')+
          `${editable?`<button type="button" aria-label="탭 추가" data-body-tab-owner="${id}" data-body-tab-add>+</button>`:''}</div>`+
          info.tabs.map(tab=>{const key=`body-tab-${id}-${escape(tab.id)}`,members=childList.filter(child=>tabs.belongs(child,block.id,tab.id));return `<div class="item-body-tab-panel" role="tabpanel" id="${key}-panel" aria-labelledby="${key}" data-body-tab-owner="${id}" data-body-tab-panel="${escape(tab.id)}"${tab.id===info.active?'':' hidden'}>${depth<32?listHtml(members,depth+1,props):''}${editable?`<button type="button" class="item-body-tab-content-add" data-body-tab-owner="${id}" data-body-tab-content="${escape(tab.id)}">내용 추가</button>`:''}</div>`;}).join('');
        const unassigned=childList.filter(child=>!info.tabs.some(tab=>tabs.belongs(child,block.id,tab.id)));if(unassigned.length&&depth<32)content+=`<div class="item-body-children">${listHtml(unassigned,depth+1,props)}</div>`;
      }
      else if(type==='synced_block'&&props.synced_from&&props.synced_from.document_id&&props.synced_from.block_id){
        content=`<div class="synced-block synced-reference" data-synced-reference="${id}"><div class="synced-reference-content"></div><div class="synced-reference-status" role="status">${host.synced?'동기화된 내용 불러오는 중…':'원본 문서에 접근할 수 없거나 위치를 확인하는 중입니다.'}</div>${window.NotionSyncedActions.html({detach:true,enabled:Boolean(host.synced),copy:Boolean(host.synced)})}</div>`;
      }
      else if(type==='synced_block'&&props.synced_from===null){content=`<div class="synced-block synced-source" data-synced-source="${id}"><div class="item-body-text" data-body-text="${id}" data-body-caption contenteditable="${editable}" role="textbox" aria-multiline="true" aria-label="동기화 원본 본문">${host.html(block)}</div>${host.synced?window.NotionSyncedActions.html({source:true,copy:true}):'<span class="synced-source-label">동기화 원본</span>'}</div>`;}
      else if(mediaTypes.has(type))content=resources.render(block);
      else if(type==='divider')content='<hr>';
      else if(type==='simple_table')content=`<table class="item-body-table"><tbody>${(props.rows||[]).map((row,r)=>`<tr>${row.map((cell,c)=>`<td contenteditable="${editable?'plaintext-only':'false'}" role="textbox" aria-label="표 ${r+1}행 ${c+1}열" data-body-cell="${id}" data-row="${r}" data-column="${c}">${escape(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table>${editable?`<div class="item-table-tools"><button type="button" data-body-table-row="${id}">행 추가</button><button type="button" data-body-table-column="${id}">열 추가</button></div>`:''}`;
      else if(types.has(type)){
        const marker=type==='todo'?`<input type="checkbox" data-body-check="${id}" aria-label="완료" ${props.checked?'checked':''} ${editable?'':'disabled'}>`:type==='bullet'?'<span class="item-body-marker">•</span>':type==='numbered'?`<span class="item-body-marker">${ordinal}.</span>`:type.startsWith('toggle')?`<button type="button" class="item-toggle" data-body-toggle="${id}" aria-label="하위 블록 ${props.open===false?'펼치기':'접기'}" aria-expanded="${props.open!==false}">${props.open===false?'▸':'▾'}</button>`:type==='callout'?`<span class="item-body-marker">${escape(props.icon||'💡')}</span>`:'';
        content=`${marker}${type==='callout'?'<div class="item-body-callout-body">':''}<div class="item-body-text" data-body-text="${id}" contenteditable="${editable}" role="textbox" aria-multiline="true" aria-label="${escape(label)} 본문" data-placeholder="입력하세요. '/'로 블록 선택">${host.html(block)}</div>${type==='callout'?children+'</div>':''}`;
      }else content=`<div class="item-body-preserved"><span>${escape(label)}</span>${escape(block.content||'')}</div>`;
      return `<div class="item-body-row" data-body-block="${id}" data-type="${escape(type)}" style="--item-indent:${Math.min(8,Math.max(0,Number(props.indent)||0))}"><div class="item-body-gutter">${editable?`<button type="button" data-body-add="${id}" aria-label="아래에 블록 추가">＋</button><button type="button" data-body-menu="${id}" aria-label="블록 동작" title="클릭하여 메뉴 열기 · 드래그하여 블록 이동"><svg viewBox="0 0 12 18" aria-hidden="true"><circle cx="4" cy="4" r="1.2"/><circle cx="8" cy="4" r="1.2"/><circle cx="4" cy="9" r="1.2"/><circle cx="8" cy="9" r="1.2"/><circle cx="4" cy="14" r="1.2"/><circle cx="8" cy="14" r="1.2"/></svg></button>`:''}</div><div class="item-body-content">${content}</div>${type==='callout'?'':children}</div>`;
    }
    function draw(){deferred=false;canvas.innerHTML=listHtml(blocks);host.synced?.mount(canvas,blocks);for(const row of canvas.querySelectorAll('[data-body-block]'))style.apply(row,find(row.dataset.bodyBlock).block);paintSelection();gesture?.refresh();resources?.refresh();}
    function applyHtml(block,value){const clean=host.sanitize(value),el=document.createElement('div');el.innerHTML=clean;el.querySelectorAll('br').forEach(br=>br.replaceWith('\n'));block.content=el.textContent;block.props={...block.props,rich_text_html:clean};}
    function contentInput(editor){const item=find(editor.dataset.bodyText);if(!item||!canEdit()||host.textEditable?.(item.block.id)===false)return;host.sync(editor,item.block);changed();if(!composing){const text=item.block.content;if(!editor.hasAttribute('data-body-caption')&&text.startsWith('/')&&host.caret(editor)===text.length){menu={id:item.block.id,query:text.slice(1)};menuIndex=0;drawMenu();}else closeMenu();}}
    function keydown(event){
      if(composing||event.isComposing||event.keyCode===229){event.stopPropagation();return;}
      const tabButton=event.target.closest('[data-body-tab]');
      if(tabButton&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
        event.preventDefault();event.stopPropagation();const owner=find(tabButton.dataset.bodyTabOwner)?.block,items=tabs.describe(owner).tabs,index=items.findIndex(t=>t.id===tabButton.dataset.bodyTab),next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowLeft'?-1:1)+items.length)%items.length;
        tabAction(owner.id,'select',{tabId:items[next].id});return;
      }
      if(menu?.tabMenu&&popup.contains(event.target)&&['Enter','Escape'].includes(event.key)&&event.target.matches('[data-body-tab-name]')){
        event.preventDefault();event.stopPropagation();const {id,tabId}=menu;
        if(event.key==='Enter')tabAction(id,'rename',{tabId,name:event.target.value});else{closeMenu();focusTab(id,tabId);}return;
      }
      if(menu&&(popup.contains(event.target)||colorMenu.contains(event.target))){
        if(event.target.matches('input,textarea')){event.stopPropagation();return;}
        const activeMenu=colorMenu.contains(event.target)?colorMenu:popup;
        if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
          event.preventDefault();event.stopPropagation();const buttons=[...activeMenu.querySelectorAll('button:not(:disabled)')],index=buttons.indexOf(document.activeElement);
          const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;
          buttons[next]?.focus();
        }else if(['Escape','ArrowLeft'].includes(event.key)&&activeMenu===colorMenu){event.preventDefault();event.stopPropagation();closeColors();popup.querySelector('[data-body-action="color"]')?.focus();}
        else if(event.key==='ArrowRight'&&event.target.closest('[data-body-action="color"]')){event.preventDefault();event.stopPropagation();openColors();}
        else if(event.key==='Escape'){event.preventDefault();event.stopPropagation();const {id,tabId,tabMenu}=menu;closeMenu();if(tabMenu)focusTab(id,tabId);else canvas.querySelector(`[data-body-menu="${CSS.escape(id)}"]`)?.focus();}
        else event.stopPropagation();return;
      }
      const typing=Boolean(event.target.closest('[contenteditable],input,textarea,select'));
      if(!typing&&(selection.size||event.target===canvas||tabButton)){
        const command=event.ctrlKey||event.metaKey,key=event.key.toLowerCase();
        const handle=()=>{event.preventDefault();event.stopPropagation();};
        if(event.key==='Escape'&&selection.size){handle();select([]);return;}
        if(command&&['z','y'].includes(key)){handle();restoreHistory(key==='y'||event.shiftKey?1:-1);canvas.focus({preventScroll:true});return;}
        if(command&&key==='s'){handle();void save();return;}
        if(command&&key==='a'){handle();select(visibleIds());return;}
        if(selection.size&&['Delete','Backspace'].includes(event.key)){handle();applyBlocks(blockOps.remove(blocks,[...selection]));return;}
        if(selection.size&&command&&key==='d'){handle();applyBlocks(blockOps.duplicate(blocks,[...selection],()=>core.createBlock().id));return;}
        if(['ArrowUp','ArrowDown'].includes(event.key)){
          handle();const delta=event.key==='ArrowUp'?-1:1,ids=visibleIds();
          if(command&&event.shiftKey)applyBlocks(blockOps.step(blocks,[...selection],ids,delta));
          else{const next=interaction.step(ids,focusId||anchorId,delta);select(event.shiftKey?interaction.range(ids,anchorId,next):[next],{anchorId:event.shiftKey?anchorId:next,focusId:next});}
          return;
        }
      }
      const editor=event.target.closest('[data-body-text],[data-body-cell]');if(!editor)return;
      const cell=editor.hasAttribute('data-body-cell');
      // Body shortcuts must never reach the source page's global editor.
      const command=event.ctrlKey||event.metaKey;
      if(menu&&['Escape','ArrowDown','ArrowUp','Enter'].includes(event.key)){
        event.preventDefault();event.stopPropagation();if(event.key==='Escape')closeMenu();else if(event.key==='Enter'){const option=options()[menuIndex];if(option)choose(option.type);}else{menuIndex+=event.key==='ArrowDown'?1:-1;drawMenu();}return;
      }
      if(command&&event.key.toLowerCase()==='s'){event.preventDefault();event.stopPropagation();void save();return;}
      if(!canEdit())return;
      if(host.textEditable?.(editor.dataset.bodyText||editor.dataset.bodyCell)===false)return;
      if(command&&['b','i','u'].includes(event.key.toLowerCase())){event.preventDefault();event.stopPropagation();if(!cell){document.execCommand({b:'bold',i:'italic',u:'underline'}[event.key.toLowerCase()]);contentInput(editor);}return;}
      if(command&&['z','y'].includes(event.key.toLowerCase())){
        event.preventDefault();event.stopPropagation();const next=historyIndex+(event.key.toLowerCase()==='y'||event.shiftKey?1:-1);
        if(next>=0&&next<history.length){restoreHistory(next-historyIndex);if(cell)canvas.querySelector(`[data-body-cell="${CSS.escape(editor.dataset.bodyCell)}"][data-row="${editor.dataset.row}"][data-column="${editor.dataset.column}"]`)?.focus();else focus(editor.dataset.bodyText);}return;
      }
      if(cell){event.stopPropagation();return;}
      const selected=find(editor.dataset.bodyText);if(!selected)return;const {block,list,index}=selected;
      if(editor.hasAttribute('data-body-caption')){
        // Caption edits must not split a media block or merge it into its neighbour.
        if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();event.stopPropagation();const next=sibling(block);list.splice(index+1,0,next);changed(true);draw();focus(next.id);return;}
        if(['Backspace','Delete','ArrowUp','ArrowDown'].includes(event.key)||command)event.stopPropagation();return;
      }
      if(event.key==='Tab'){event.preventDefault();event.stopPropagation();const offset=host.caret(editor);block.props.indent=Math.min(8,Math.max(0,Number(block.props.indent||0)+(event.shiftKey?-1:1)));changed(true);draw();focus(block.id,offset);return;}
      if(event.key==='Enter'){
        event.preventDefault();event.stopPropagation();
        if(event.shiftKey||block.type==='code'&&!command){document.execCommand('insertHTML',false,'<br>\n');contentInput(editor);return;}
        host.sync(editor,block);
        if(!block.content&&core.LIST_BLOCK_TYPES.has(block.type)){block.type='paragraph';changed(true);draw();focus(block.id);return;}
        const selection=window.getSelection();if(!selection?.rangeCount)return;const range=selection.getRangeAt(0);if(!editor.contains(range.startContainer)||!editor.contains(range.endContainer))return;
        range.deleteContents();const left=range.cloneRange(),right=range.cloneRange();left.selectNodeContents(editor);left.setEnd(range.startContainer,range.startOffset);right.selectNodeContents(editor);right.setStart(range.endContainer,range.endOffset);
        const a=document.createElement('div'),b=document.createElement('div');a.append(left.cloneContents());b.append(right.cloneContents());
        const pair=core.splitBlock(block,host.caret(editor));applyHtml(pair[0],a.innerHTML);applyHtml(pair[1],b.innerHTML);list.splice(index,1,...pair);changed(true);draw();focus(pair[1].id);return;
      }
      if(event.key==='Backspace'&&window.getSelection()?.isCollapsed&&host.caret(editor)===0){
        const scope=blockOps.tabScope(blocks,block.id),previousScope=index>0?blockOps.tabScope(blocks,list[index-1].id):null;
        if(index>0&&types.has(list[index-1].type)&&!['divider','simple_table','tabs'].includes(list[index-1].type)&&JSON.stringify(scope)===JSON.stringify(previousScope)){
          event.preventDefault();event.stopPropagation();const previous=list[index-1],offset=previous.content.length;applyHtml(previous,host.html(previous)+host.html(block));if(block.props?.children?.length)previous.props.children=[...(previous.props.children||[]),...clone(block.props.children)];list.splice(index,1);changed(true);draw();focus(previous.id,offset);
        }else if(!block.content&&block.type!=='paragraph'){event.preventDefault();event.stopPropagation();block.type='paragraph';changed(true);draw();focus(block.id);}return;
      }
      if(command||['ArrowUp','ArrowDown','Delete','Backspace'].includes(event.key))event.stopPropagation();
    }
    const onInput=event=>{inputRevision++;const editor=event.target.closest('[data-body-text]');if(editor)contentInput(editor);else if(event.target.matches('[data-body-cell]')&&canEdit()&&host.textEditable?.(event.target.dataset.bodyCell)!==false){const input=event.target,item=find(input.dataset.bodyCell);item.block.props.rows[Number(input.dataset.row)][Number(input.dataset.column)]=input.innerText;changed();}};
    const onCompositionStart=event=>{inputRevision++;if(event.target.closest('[data-synced-reference]'))return;composing=true;clearTimeout(timer);host.composing(true);if(!event.target.matches('[data-body-tab-name]'))closeMenu();};
    const onCompositionEnd=event=>{if(event.target.closest('[data-synced-reference]'))return;composing=false;host.composing(false);onInput(event);};
    const onPaste=event=>{
      event.stopPropagation();if(event.target.closest('.item-resource-picker'))return;
      const descriptor=window.NotionSyncedClipboard?.read(event.clipboardData);
      if(descriptor){
        event.preventDefault();
        if(!host.syncedPaste){notice.hidden=false;notice.textContent='이 본문에서는 동기화 블록 붙여넣기를 아직 지원하지 않습니다.';return;}
        const node=event.target,field=node.closest('[data-body-text]'),id=field?.dataset.bodyText||focusId||visibleIds().at(-1),item=find(id);
        if(!model?.ready||composing||event.isComposing||node.closest('[data-synced-reference]')||(!field&&node!==canvas&&!selection.size)){
          notice.hidden=false;notice.textContent='붙여넣을 항목 본문과 입력 상태를 확인해 주세요.';return;
        }
        const signature=JSON.stringify(blocks),selected=JSON.stringify([...selection]),revision=inputRevision;
        void host.syncedPaste.paste(descriptor.encoded,{anchorId:id||null,
          replaceEmpty:Boolean(item?.block.type==='paragraph'&&!item.block.content&&!item.block.props?.children?.length&&!item.block.props?.rich_text_html),
          current:()=>!disposed&&root.isConnected&&node.isConnected&&root.contains(node)&&!composing&&inputRevision===revision
            &&JSON.stringify(blocks)===signature&&JSON.stringify([...selection])===selected});
        return;
      }
      const editor=event.target.closest('[data-body-text]'),selected=selection.size>0||event.target===canvas;
      if((!editor&&!selected)||!canEdit())return;
      if(editor&&host.textEditable?.(editor.dataset.bodyText)===false)return;
      const encoded=host.clipboard?.read(event.clipboardData);
      if(encoded){
        event.preventDefault();
        try{
          const additions=host.clipboard.decode(encoded,{shape:'tree',allowedTypes:new Set([...types,...mediaTypes]),createId:()=>core.createBlock().id,
            sanitize:host.sanitize,usedIds:blockOps.entries(blocks).map(e=>e.block.id),origin:host.clipboardSource?.().origin});
          const target=editor?.dataset.bodyText||focusId||visibleIds().at(-1);
          if(!applyBlocks(blockOps.insert(blocks,target,additions)))throw Error('붙여넣을 블록의 저장 상태를 확인해 주세요.');
          closeMenu();
        }catch(error){notice.hidden=false;notice.textContent=error.message||'블록을 붙여넣지 못했습니다.';}
        return;
      }
      if(!editor)return;
      event.preventDefault();const html=event.clipboardData.getData('text/html'),plain=event.clipboardData.getData('text/plain');
      document.execCommand('insertHTML',false,html?host.sanitize(html):escape(plain).replace(/\n/g,'<br>'));contentInput(editor);
    };
    const onClick=event=>{
      const target=event.target.closest('button,input');if(!target)return;
      if(target.hasAttribute('data-body-tab')){tabAction(target.dataset.bodyTabOwner,'select',{tabId:target.dataset.bodyTab});return;}
      if(target.hasAttribute('data-body-tab-add')){tabAction(target.dataset.bodyTabOwner,'add');return;}
      if(target.hasAttribute('data-body-tab-menu')){openTabMenu(target.dataset.bodyTabOwner,target.dataset.bodyTabMenu,target);return;}
      if(target.hasAttribute('data-body-tab-content')){tabAction(target.dataset.bodyTabOwner,'content',{tabId:target.dataset.bodyTabContent});return;}
      if(target.hasAttribute('data-body-tab-action')){if(menu?.tabMenu)tabAction(menu.id,target.dataset.bodyTabAction,{tabId:menu.tabId,name:popup.querySelector('[data-body-tab-name]')?.value});return;}
      if(target.hasAttribute('data-body-color')){
        if(!menu||!canEdit()||composing||!find(menu.id))return;
        const ids=selection.has(menu.id)?[...selection]:[menu.id],result=style.paint(blocks,ids,target.dataset.colorKey,target.dataset.bodyColor);
        closeMenu();if(result)applyBlocks(result);else canvas.focus({preventScroll:true});return;
      }
      if(target.dataset.bodyType){choose(target.dataset.bodyType);return;}
      if(target.dataset.bodyAction){const item=find(menu?.id);if(!item||!canEdit())return;
        const action=target.dataset.bodyAction,ids=selection.has(item.block.id)?[...selection]:[item.block.id];
        if(action==='delete'||action==='duplicate'){closeMenu();applyBlocks(action==='delete'?blockOps.remove(blocks,ids):blockOps.duplicate(blocks,ids,()=>core.createBlock().id));return;}
        else if(action==='color'){openColors();return;}
        else{menu={id:item.block.id,query:null};menuIndex=0;drawMenu();popup.querySelector('button')?.focus();return;}}
      const id=target.dataset.bodyAdd||target.dataset.bodyMenu||target.dataset.bodyCheck||target.dataset.bodyToggle||target.dataset.bodyChild||target.dataset.bodyTableRow||target.dataset.bodyTableColumn,item=find(id);if(!item)return;
      if(target.hasAttribute('data-body-toggle')){const children=target.closest('.item-body-row').querySelector('.item-body-children');if(children){children.hidden=!children.hidden;target.setAttribute('aria-expanded',String(!children.hidden));target.textContent=children.hidden?'▸':'▾';paintSelection();gesture?.refresh();}return;}
      if(!canEdit())return;
      if(target.dataset.bodyAdd){menu={id,insert:true,query:null};menuIndex=0;drawMenu();popup.querySelector('button')?.focus();}
      else if(target.dataset.bodyMenu){if(!selection.has(id))select([id]);menu={id,query:null};drawMenu();popup.setAttribute('role','menu');popup.setAttribute('aria-label','블록 동작');popup.innerHTML=[['convert','전환'],['color','색'],['duplicate','복제'],['delete','삭제']].map(([action,text])=>`<button type="button" role="menuitem" data-body-action="${action}" ${action==='color'?'aria-haspopup="menu" aria-expanded="false"':''}>${text}${action==='color'?'<span class="item-menu-chevron" aria-hidden="true">›</span>':''}</button>`).join('');interaction.place(popup,target,'right');popup.querySelector('button')?.focus();}
      else if(target.dataset.bodyCheck){item.block.props.checked=target.checked;changed(true);}
      else if(target.dataset.bodyChild){const child=core.createBlock();(item.block.props.children??=[]).push(child);item.block.props.open=true;changed(true);draw();focus(child.id);}
      else{const rows=item.block.props.rows??=[];if(target.dataset.bodyTableRow)rows.push(Array(rows[0]?.length||2).fill(''));else rows.forEach(row=>row.push(''));changed(true);draw();}
    };
    const onDocumentDown=event=>{if(menu&&!popup.contains(event.target)&&!colorMenu.contains(event.target))closeMenu();if(selection.size&&!startSelection(event)&&!event.target.closest('[data-body-menu],.item-body-menu'))select([]);};
    const onClipboard=event=>{
      if(disposed||!root.isConnected)return;
      event.stopPropagation();if(!selection.size||event.target.closest('[contenteditable],input,textarea'))return;
      event.preventDefault();if(event.type==='cut'&&!canEdit())return;
      try{
        if(!event.clipboardData)return;
        const ids=new Set(blockOps.roots(blocks,[...selection])),chosen=blockOps.entries(blocks).filter(entry=>ids.has(entry.block.id)).map(entry=>entry.block);
        host.clipboard.write(event.clipboardData,chosen,{shape:'tree',source:host.clipboardSource?.(),sanitize:host.sanitize,fileUrl:host.fileUrl,text:blockOps.text(blocks,[...selection])});
        if(event.type==='cut')applyBlocks(blockOps.remove(blocks,[...selection]));
      }catch{notice.hidden=false;notice.textContent='클립보드에 복사하지 못했습니다. 선택한 블록은 유지됩니다.';}
    };
    function dropTarget(event){
      if(event.target.closest('.side-peek-head,.side-peek-properties,.database-item-comments,[data-linked-item-field],.item-body-notice,[role="dialog"]'))return null;
      const panel=event.target.closest('[data-body-tab-panel]'),row=event.target.closest('[data-body-block]');
      if(panel&&(!row||row.dataset.bodyBlock===panel.dataset.bodyTabOwner))return {id:panel.dataset.bodyTabOwner,placement:'inside'};
      if(!row){const next=[...canvas.children].find(row=>event.clientY<row.getBoundingClientRect().top);return next?{id:next.dataset.bodyBlock,placement:'before'}:blocks.length?{id:blocks.at(-1).id,placement:'after'}:null;}
      const rect=row.querySelector(':scope > .item-body-content').getBoundingClientRect(),id=row.dataset.bodyBlock;
      const inside=find(id)?.block.type.startsWith('toggle')&&event.clientX>rect.left+20&&event.clientY>rect.top+rect.height*.25&&event.clientY<rect.bottom-rect.height*.25;
      return {id,placement:inside?'inside':event.clientY<(rect.top+rect.bottom)/2?'before':'after'};
    }
    popup.addEventListener('mousedown',event=>{if(!event.target.closest('input'))event.preventDefault();});
    colorMenu.addEventListener('mousedown',event=>event.preventDefault());
    root.addEventListener('input',onInput);root.addEventListener('keydown',keydown);root.addEventListener('paste',onPaste);root.addEventListener('click',onClick);
    for(const type of ['copy','cut'])root.addEventListener(type,onClipboard);
    root.addEventListener('compositionstart',onCompositionStart);root.addEventListener('compositionend',onCompositionEnd);document.addEventListener('pointerdown',onDocumentDown);
    gesture=interaction.bindGutterSelection({
      pageKey:()=>!disposed&&root.isConnected&&model?.ready?root:null,start:startSelection,rows:visibleRows,
      rowId:row=>row.dataset.bodyBlock,rowContent:row=>row.querySelector(':scope > .item-body-content'),
      toolbarBottom:()=>surface?.querySelector('.side-peek-head,.modal-head')?.getBoundingClientRect().bottom||0,
      selected:()=>[...selection],select,marqueeClass:'item-body-marquee',
      finished:event=>{if(disposed||!root.isConnected)return;if(!event.cancelled&&!event.moving&&!event.additive)select([]);if(event.moving&&selection.size)canvas.focus({preventScroll:true});},
    });
    blockMove=interaction.bindBlockMove({
      surface,scroller,enabled:canEdit,signature:()=>JSON.stringify(blocks),
      handle:target=>target.closest('[data-body-menu]'),id:handle=>handle.dataset.bodyMenu,
      selected:()=>[...selection],select,started:()=>{closeMenu();gesture?.cancel();},
      destination:(point,ids)=>{const target=document.elementFromPoint(point.x,point.y);if(!target||!surface.contains(target))return null;const value=dropTarget({target,clientX:point.x,clientY:point.y});return value&&blockOps.move(blocks,ids,value.id,value.placement)?value:null;},
      preview:target=>{clearDrop();if(target){const row=canvas.querySelector(`[data-body-block="${CSS.escape(target.id)}"]`);if(row)row.dataset.bodyDrop=target.placement;}},
      move:(ids,target)=>applyBlocks(blockOps.move(blocks,ids,target.id,target.placement)),
    });
    function render(value){
      if(disposed||!value)return;const before=model;model=value;
      const syncedPending=Boolean(host.synced?.pending()),syncedChanged=lastSyncedPending!==syncedPending;lastSyncedPending=syncedPending;
      if(!canEdit())closeMenu();
      notice.replaceChildren();notice.hidden=!value.error&&!value.pending&&!value.busy&&!value.dirty;
      const text=document.createElement('span');text.textContent=value.error||(value.busy?'본문 저장 중…':value.pending?'보관한 본문 저장을 확인해 주세요.':value.dirty?'본문 저장 대기 중…':'');notice.append(text);
      if((value.error||value.pending)&&!value.busy){for(const [label,action] of [['다시 저장',host.save],['보관한 본문 지우기',host.discard]]){const button=document.createElement('button');button.type='button';button.textContent=label;button.onclick=()=>void action();notice.append(button);}}
      const paste=host.syncedPaste?.snapshot();
      if(paste?.pending||paste?.error){
        notice.hidden=false;notice.replaceChildren();const message=document.createElement('span');
        message.textContent=paste.error||(paste.busy?'동기화 붙여넣기 저장 중…':'보관한 동기화 붙여넣기 결과를 확인해 주세요.');notice.append(message);
        if(paste.pending&&!paste.busy){const button=document.createElement('button');button.type='button';button.textContent='붙여넣기 결과 확인 · 재시도';button.onclick=()=>void host.syncedPaste.retry();notice.append(button);}
      }
      if(!value.ready){resources?.refresh();canvas.textContent=value.error?'':'본문을 불러오는 중…';return;}
      if(!composing&&(deferred||syncedChanged||!host.equal(last,value.blocks)||before?.editable!==value.editable||Boolean(before?.pending&&!before?.busy)!==Boolean(value.pending&&!value.busy))){
        blockMove?.cancel();clearDrop();if(before?.editable!==value.editable)gesture?.cancel();
        const focused=canvas.contains(document.activeElement)?document.activeElement:null,id=focused?.dataset.bodyText,offset=id?host.caret(focused):0;
        if(!host.equal(last,value.blocks)){closeMenu();blocks=clone(value.blocks);last=clone(value.blocks);history=[JSON.stringify(blocks)];historyIndex=0;}
        if(!blocks.length)blocks=[core.createBlock()];
        if(host.synced?.holdsFocus(blocks)){
          // Update the backing body immediately, even while native IME/undo owns
          // a reference's DOM. A subsequent ordinary edit must use fresh text.
          deferred=true;
          for(const field of canvas.querySelectorAll('[data-body-text]')){
            const block=find(field.dataset.bodyText)?.block;if(block)field.innerHTML=host.html(block);
          }
        }else{draw();if(id)focus(id,offset);}
      }
      for(const field of canvas.querySelectorAll('[data-body-text],[data-body-cell]')){
        const editable=canEdit()&&host.textEditable?.(field.dataset.bodyText||field.dataset.bodyCell)!==false;
        field.contentEditable=editable?(field.hasAttribute('data-body-cell')?'plaintext-only':'true'):'false';
        field.setAttribute('aria-readonly',String(!editable));
      }
      host.synced?.syncEditing();resources?.refresh();
    }
    return {render,selectReference:id=>{if(!disposed&&find(id)){select([id]);canvas.focus({preventScroll:true});}},dispose:()=>{disposed=true;clearTimeout(timer);host.synced?.dispose();resources?.dispose();gesture?.dispose();blockMove?.dispose();clearDrop();document.removeEventListener('pointerdown',onDocumentDown);
      for(const [type,handler] of [['input',onInput],['keydown',keydown],['paste',onPaste],['click',onClick],['copy',onClipboard],['cut',onClipboard],['compositionstart',onCompositionStart],['compositionend',onCompositionEnd]])root.removeEventListener(type,handler);
      root.replaceChildren();},focus:()=>focus(blocks[0]?.id),snapshot:()=>clone(blocks)};
  }
  return {create,types};
});

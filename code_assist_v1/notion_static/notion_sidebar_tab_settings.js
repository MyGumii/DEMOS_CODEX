(function(root,factory){
  const api=factory();
  if(typeof module==="object"&&module.exports)module.exports=api;
  else root.NotionSidebarTabSettings=api.create({document:root.document,window:root,storage:root.localStorage});
})(typeof globalThis!=="undefined"?globalThis:this,function(){
  "use strict";
  const eye='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
  const hiddenEye='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m3 3 18 18M9 5a12 12 0 0 1 13 7 16 16 0 0 1-3 4M6 6a16 16 0 0 0-4 6s3 7 10 7a12 12 0 0 0 5-1"/></svg>';
  function changeSections(sections,key,action){
    const next=sections.map(item=>({...item})).sort((a,b)=>a.display_order-b.display_order);
    const index=next.findIndex(item=>item.section===key);
    if(index<0)return null;
    if(action==="visibility")next[index].hidden=!next[index].hidden;
    else if(action==="up"||action==="down"){
      const target=index+(action==="up"?-1:1);
      if(target<0||target>=next.length)return null;
      [next[index],next[target]]=[next[target],next[index]];
      next.forEach((item,position)=>{item.display_order=position;});
    }else return null;
    return next;
  }
  function create({document,window,storage}){
    let host,menu,editor,panel,anchor,menuScope,menuSession,customization,bound=false;
    const node=(tag,className,text)=>{const el=document.createElement(tag);if(className)el.className=className;if(text)el.textContent=text;return el;};
    const tabs=()=>[...document.querySelectorAll('[data-sidebar-tab]')];
    const current=session=>session&&session===customization&&session.scope===host.scope()&&host.mode()==="pages";
    const labelsHidden=()=>{try{return storage.getItem(host.labelKey())==="hidden";}catch{return false;}};
    function closeMenu(restore=false){
      const previous=anchor,valid=host&&menuScope===host.scope();
      if(menu){menu.hidden=true;menu.replaceChildren();}
      previous?.setAttribute('aria-expanded','false');anchor=null;menuScope=null;menuSession=null;
      if(restore&&valid&&previous?.isConnected)previous.focus({preventScroll:true});
    }
    function leave(restore=false){
      closeMenu();const valid=current(customization);customization=null;
      if(editor){editor.hidden=true;editor.replaceChildren();}
      panel?.classList.remove('customizing-sections');
      if(restore&&valid)document.getElementById('homeNavBtn')?.focus({preventScroll:true});
    }
    function sync(){
      if(!host)return;
      document.querySelector('.quick-icons')?.classList.toggle('sidebar-tab-labels-hidden',labelsHidden());
      tabs().forEach(tab=>{tab.setAttribute('aria-haspopup','menu');tab.setAttribute('aria-expanded',String(tab===anchor&&!menu.hidden));});
      if(anchor&&(menuScope!==host.scope()||anchor.dataset.sidebarTab!==host.mode()))closeMenu();
      if(customization&&!current(customization))leave();
    }
    function renderSections(focusKey=''){
      const session=customization;if(!current(session))return;
      const focused=document.activeElement,hadFocus=editor.contains(focused);
      const key=focused?.dataset?.sectionFocus||focusKey;
      const mayFocus=hadFocus||focused===document.body;
      editor.replaceChildren();
      const heading=node('h2','sidebar-customize-heading','홈 섹션 사용자 지정');editor.append(heading);
      const list=node('div','sidebar-customize-list');list.setAttribute('role','list');
      const sections=host.sections().sort((a,b)=>a.display_order-b.display_order);
      sections.forEach((item,index)=>{
        const row=node('div','sidebar-customize-row');row.setAttribute('role','listitem');
        const label=host.labels[item.section]||item.section;
        const toggle=node('button','sidebar-section-visibility');toggle.type='button';
        toggle.classList.toggle('section-is-hidden',Boolean(item.hidden));
        toggle.setAttribute('aria-label',`${label} ${item.hidden?'표시':'숨기기'}`);
        toggle.setAttribute('aria-pressed',String(!item.hidden));toggle.dataset.sectionFocus=`visibility:${item.section}`;
        toggle.append(node('span','',label));const symbol=node('span','sidebar-setting-icon');symbol.innerHTML=item.hidden?hiddenEye:eye;toggle.append(symbol);
        toggle.disabled=session.busy||host.locked();toggle.addEventListener('click',()=>current(session)&&toggle.isConnected?commit(item.section,'visibility',toggle.dataset.sectionFocus):false);row.append(toggle);
        ['up','down'].forEach(action=>{
          const move=node('button','sidebar-section-move',action==='up'?'↑':'↓');move.type='button';
          move.setAttribute('aria-label',`${label} ${action==='up'?'위로':'아래로'} 이동`);move.dataset.sectionFocus=`${action}:${item.section}`;
          move.disabled=session.busy||host.locked()||(action==='up'?index===0:index===sections.length-1);
          move.addEventListener('click',()=>current(session)&&move.isConnected?commit(item.section,action,move.dataset.sectionFocus):false);row.append(move);
        });
        list.append(row);
      });editor.append(list);
      const status=node('p','sidebar-customize-status',session.busy?'저장 중…':session.error||'');status.setAttribute('role','status');editor.append(status);
      const done=node('button','sidebar-customize-done','완료');done.type='button';done.disabled=session.busy;done.dataset.sectionFocus='done';
      done.addEventListener('click',()=>leave(true));editor.append(done);
      if(mayFocus&&key){
        const target=[...editor.querySelectorAll('[data-section-focus]')].find(el=>el.dataset.sectionFocus===key&&!el.disabled);
        (target||(session.busy?editor:null))?.focus({preventScroll:true});
      }
    }
    async function commit(key,action,focusKey){
      const session=customization;if(!current(session)||session.busy||host.locked())return false;
      const next=changeSections(host.sections(),key,action);if(!next)return false;
      session.busy=true;session.error='';renderSections(focusKey);
      try{
        const saved=await host.saveSections(next);
        if(current(session)&&!saved)session.error='설정을 저장하지 못했습니다. 다시 시도해 주세요.';
        return Boolean(saved);
      }catch{
        if(current(session))session.error='설정을 저장하지 못했습니다. 다시 시도해 주세요.';
        return false;
      }finally{
        session.busy=false;if(current(session))renderSections(focusKey);
      }
    }
    function customize(){
      closeMenu();if(host.mode()!=='pages'||host.locked())return;
      customization={scope:host.scope(),busy:false,error:''};editor.hidden=false;panel.classList.add('customizing-sections');renderSections();
      editor.querySelector('button:not(:disabled)')?.focus({preventScroll:true});
    }
    function activate(tab){
      if(!host||tab.dataset.sidebarTab!==host.mode())return false;
      if(anchor===tab&&!menu.hidden){closeMenu();return true;}
      host.closeMenus();closeMenu();anchor=tab;menuScope=host.scope();const session=menuSession={};
      const currentMenu=()=>menuSession===session&&anchor===tab&&menuScope===host.scope()&&tab.dataset.sidebarTab===host.mode();
      const add=(label,run)=>{const button=node('button','menu-item');button.type='button';button.setAttribute('role','menuitem');button.setAttribute('aria-label',label);
        const symbol=node('span','sidebar-setting-icon');symbol.innerHTML=label==='섹션 사용자 지정'
          ?'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M3 6h8m4 0h6M3 12h3m4 0h11M3 18h12m4 0h2"/><circle cx="13" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="17" cy="18" r="2"/></svg>'
          :labelsHidden()?eye:hiddenEye;
        button.append(symbol,node('span','',label));
        button.addEventListener('click',()=>{if(currentMenu())run();else if(menuSession===session)closeMenu();});menu.append(button);return button;};
      if(host.mode()==='pages'){
        menu.append(node('div','sidebar-tab-menu-heading','홈 설정'));
        add('섹션 사용자 지정',customize).disabled=host.locked();
      }
      add(labelsHidden()?'탭 이름 표시':'탭 이름 숨기기',()=>{
        try{storage.setItem(host.labelKey(),labelsHidden()?'visible':'hidden');}
        catch{const error=node('div','sidebar-customize-status','표시 설정을 저장하지 못했습니다.');error.setAttribute('role','status');menu.append(error);return;}
        closeMenu(true);sync();
      });
      menu.hidden=false;tab.setAttribute('aria-expanded','true');host.place(menu,tab);
      menu.querySelector('button:not(:disabled)')?.focus({preventScroll:true});return true;
    }
    function init(options){
      host=options;menu=document.getElementById('sidebarTabMenu');editor=document.getElementById('sidebarSectionsEditor');panel=document.getElementById('sidebarPagePanel');
      editor.tabIndex=-1;
      if(!bound){
        bound=true;
        document.addEventListener('pointerdown',event=>{if(anchor&&!menu.contains(event.target)&&!anchor.contains(event.target))closeMenu();},true);
        menu.addEventListener('keydown',event=>{
          if(event.isComposing||event.keyCode===229)return;
          if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeMenu(true);}
          else if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
            event.preventDefault();const items=[...menu.querySelectorAll('button:not(:disabled)')],index=items.indexOf(document.activeElement);
            const next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:items.length-1))%items.length;
            items[next]?.focus({preventScroll:true});
          }else if(event.key==='Tab')closeMenu(true);
        });
        editor.addEventListener('keydown',event=>{if(event.key==='Escape'&&!event.isComposing){event.preventDefault();event.stopPropagation();leave(true);}});
        window.addEventListener('storage',sync);
      }sync();
    }
    return {init,sync,activate,closeMenu,leave};
  }
  return {create,changeSections};
});

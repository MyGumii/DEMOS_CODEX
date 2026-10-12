(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.NotionRelationDisplay=api;
})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const prefix='code_assist.notion.relation_display.v1:';
  const grip='<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true"><circle cx="5" cy="4" r="1"/><circle cx="11" cy="4" r="1"/><circle cx="5" cy="8" r="1"/><circle cx="11" cy="8" r="1"/><circle cx="5" cy="12" r="1"/><circle cx="11" cy="12" r="1"/></svg>';
  const eye=shown=>`<svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M2 10c2-3 4.7-4.5 8-4.5s6 1.5 8 4.5c-2 3-4.7 4.5-8 4.5S4 13 2 10Z"/><circle cx="10" cy="10" r="2.2"/>${shown?'':'<path d="M3 17 17 3" stroke-width="2"/>'}</svg>`;

  function create({popover,section,properties,storageKey,onChange}){
    const fields=(Array.isArray(properties)?properties:[])
      .filter(item=>item&&item.id&&item.name).slice(0,100);
    if(!fields.length)return null;
    const byId=new Map(fields.map(item=>[String(item.id),item]));
    const ids=[...byId.keys()];
    const titleId=String(fields.find(item=>item.type==='title')?.id||ids[0]);
    let saved=null;
    try{saved=JSON.parse(localStorage.getItem(prefix+storageKey)||'null');}catch{}
    const order=[...new Set([...(Array.isArray(saved?.order)?saved.order:[])
      .map(String).filter(id=>byId.has(id)),...ids])];
    const visible=new Set(Array.isArray(saved?.visible)
      ?saved.visible.map(String).filter(id=>byId.has(id)):[titleId]);
    visible.add(titleId);
    let menu=null,dragged='';
    const more=document.createElement('button');more.type='button';
    more.className='personal-linked-relation-display-toggle';
    more.textContent='⋯';more.setAttribute('aria-label','표시할 속성을 선택하세요.');
    more.setAttribute('aria-haspopup','dialog');more.setAttribute('aria-expanded','false');
    section.append(more);
    const save=()=>{
      try{localStorage.setItem(prefix+storageKey,JSON.stringify({order,visible:[...visible]}));}catch{}
    };
    const outside=event=>{
      if(menu&&!menu.contains(event.target)&&event.target!==more)close(false);
    };
    const close=(restore=false)=>{
      document.removeEventListener('pointerdown',outside,true);
      window.removeEventListener('resize',position);
      window.removeEventListener('scroll',position,true);
      menu?.remove();menu=null;more.setAttribute('aria-expanded','false');
      if(restore&&more.isConnected)more.focus({preventScroll:true});
    };
    const position=()=>{
      if(!menu?.isConnected)return;
      const rect=more.getBoundingClientRect(),width=Math.min(225,innerWidth-16);
      menu.style.width=`${width}px`;
      const x=rect.right+width+4<=innerWidth-8?rect.right+4:rect.left-width-4;
      menu.style.left=`${Math.max(8,Math.min(x,innerWidth-width-8))}px`;
      menu.style.top=`${Math.max(8,Math.min(rect.top,innerHeight-menu.offsetHeight-8))}px`;
    };
    const move=(id,direction)=>{
      const group=order.filter(key=>visible.has(key)===visible.has(id));
      const from=group.indexOf(id),other=group[from+direction];
      if(!other)return;
      const a=order.indexOf(id),b=order.indexOf(other);
      [order[a],order[b]]=[order[b],order[a]];save();drawMenu();onChange?.();position();
      menu?.querySelector(`[data-relation-display-handle="${CSS.escape(id)}"]`)
        ?.focus({preventScroll:true});
    };
    const drawMenu=()=>{
      if(!menu)return;
      menu.replaceChildren();
      for(const [shown,heading] of [[true,'관계형에서 표시'],[false,'관계형에서 숨기기']]){
        const group=order.filter(id=>visible.has(id)===shown);
        if(!group.length)continue;
        const label=document.createElement('div');label.className='personal-linked-relation-display-heading';
        label.textContent=heading;menu.append(label);
        for(const id of group){
          const field=byId.get(id),row=document.createElement('div');
          row.className='personal-linked-relation-display-row';row.dataset.relationDisplayRow=id;
          const handle=document.createElement('button');handle.type='button';
          handle.className='personal-linked-relation-display-handle';handle.innerHTML=grip;
          handle.dataset.relationDisplayHandle=id;
          handle.setAttribute('aria-label',`${field.name} 표시 순서 이동`);
          handle.draggable=true;
          handle.onkeydown=event=>{
            if(!event.altKey||!['ArrowUp','ArrowDown'].includes(event.key))return;
            event.preventDefault();event.stopPropagation();move(id,event.key==='ArrowUp'?-1:1);
          };
          handle.ondragstart=event=>{
            dragged=id;event.dataTransfer.effectAllowed='move';
            event.dataTransfer.setData('text/plain','relation-display-order');
          };
          handle.ondragend=()=>{dragged='';};
          row.ondragover=event=>{
            if(!dragged||visible.has(dragged)!==shown)return;
            event.preventDefault();event.dataTransfer.dropEffect='move';
          };
          row.ondrop=event=>{
            if(!dragged||dragged===id||visible.has(dragged)!==shown)return;
            event.preventDefault();const from=order.indexOf(dragged),to=order.indexOf(id);
            order.splice(from,1);order.splice(to,0,dragged);dragged='';
            save();drawMenu();onChange?.();position();
          };
          const glyph=document.createElement('span');glyph.className='personal-linked-relation-display-glyph';
          glyph.textContent=({title:'Aa',text:'Aa',date:'▣',number:'#',checkbox:'☑',relation:'↗',files:'▱'})[field.type]||'◇';
          const name=document.createElement('span');name.className='personal-linked-relation-display-name';
          name.textContent=field.name;
          const toggle=document.createElement('button');toggle.type='button';
          toggle.className='personal-linked-relation-display-eye';
          toggle.dataset.relationDisplayToggle=id;
          toggle.innerHTML=eye(shown);toggle.setAttribute('aria-pressed',String(shown));
          toggle.setAttribute('aria-label',`${field.name} ${shown?'숨기기':'표시하기'}`);
          toggle.disabled=id===titleId;
          toggle.onclick=()=>{
            if(visible.has(id))visible.delete(id);else visible.add(id);
            save();drawMenu();onChange?.();position();
            menu?.querySelector(`[data-relation-display-toggle="${CSS.escape(id)}"]`)
              ?.focus({preventScroll:true});
          };
          row.append(handle,glyph,name,toggle);menu.append(row);
        }
      }
      position();
    };
    more.onclick=()=>{
      if(menu){close(true);return;}
      menu=document.createElement('div');
      menu.className='personal-linked-relation-display-menu';
      menu.setAttribute('role','dialog');
      menu.setAttribute('aria-label','관계형에서 표시할 속성');
      popover.append(menu);more.setAttribute('aria-expanded','true');
      menu.addEventListener('keydown',event=>{
        if(event.key==='Escape'){
          event.preventDefault();event.stopPropagation();close(true);
        }
      });
      window.addEventListener('resize',position);
      window.addEventListener('scroll',position,true);
      drawMenu();
      document.addEventListener('pointerdown',outside,true);
      menu.querySelector('.personal-linked-relation-display-handle')?.focus({preventScroll:true});
    };
    return {
      close,
      appendPreview(content,item){
        for(const id of order){
          if(id===titleId||!visible.has(id))continue;
          const value=String(item?.preview?.[id]||'');if(!value)continue;
          const line=document.createElement('small');
          line.className='personal-linked-relation-preview';
          line.textContent=`${byId.get(id).name}: ${value}`;content.append(line);
        }
      },
    };
  }
  return {create};
});

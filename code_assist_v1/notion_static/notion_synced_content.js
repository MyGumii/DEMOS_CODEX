(function(root,factory){const api=factory(typeof module==='object'&&module.exports?require('./notion_block_style.js'):root.NotionBlockStyle);if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionSyncedContent=api;})(typeof globalThis==='object'?globalThis:this,function(style){
  'use strict';
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const text=value=>escape(value).replace(/\n/g,'<br>');
  const array=value=>Array.isArray(value)?value:[];
  const number=(v,max=32)=>Number.isFinite(Number(v))?Math.max(0,Math.min(max,Number(v))):0;
  const databaseTypes=new Set(['table','board','list','gallery','calendar','timeline','chart','dashboard','form','feed','map']);
  const layoutContainer=block=>block?.type==='paragraph'&&block.props?.layout_container===true&&!block.content&&!block.props.rich_text_html&&array(block.children).length>0;

  function render(tree,host={}){
    let serial=0;
    const seen=new Set();
    const rich=block=>host.richHtml?host.richHtml(block):text(block.content);
    function group(nodes,depth,parent){
      let output='',columns=null;const counters=new Map();
      const flush=()=>{if(columns){output+=`<div class="synced-content-columns" data-layout-columns="${columns.items.length}" style="--synced-columns:${columns.items.length}">${columns.items.map(items=>`<div>${items.join('')}</div>`).join('')}</div>`;columns=null;}};
      for(const node of nodes){
        const p=node.props||{};
        const count=Math.max(2,Math.trunc(number(p.layout_columns||2,5))),column=Math.trunc(number(p.layout_column,count-1));
        const scope=JSON.stringify([p.layout_group||'',column,p.tab_group||'',p.tab_id||'']);
        counters.set(scope,node.type==='numbered'?(counters.get(scope)||0)+1:0);
        const html=draw(node,depth,counters.get(scope),parent);
        const inherited=p.layout_group&&p.layout_group===parent?.props?.layout_group&&column===number(parent.props.layout_column,4);
        if(p.layout_group&&!inherited){
          const key=JSON.stringify([p.layout_group,count,p.tab_group||'',p.tab_id||'']);
          if(!columns||columns.key!==key){flush();columns={key,items:Array.from({length:count},()=>[])};}
          columns.items[column].push(html);
        }else{flush();output+=html;}
      }
      flush();return output;
    }
    function draw(block,depth,ordinal=1,parent=null){
      if(!block||typeof block.id!=='string'||seen.has(block.id)||depth>64)throw Error('Invalid synced content hierarchy');
      seen.add(block.id);
      const p=block.props||{},kind=block.type||'paragraph',children=array(block.children);
      let body='',nested='';
      // Nested references require their own dual-authority read; never present
      // their stored content as if it were a resolved original.
      if(kind==='synced_block'&&Object.hasOwn(p,'synced_from')&&p.synced_from!==null){
        body='<p class="synced-content-unavailable">중첩된 동기화 참조는 원본에서 확인해 주세요.</p>';
      }else if(layoutContainer(block)){
        body=group(children,depth+1,block);
      }else if(kind==='tabs'){
        const tabs=array(p.tabs),active=tabs.some(t=>t.id===p.active_tab_id)?p.active_tab_id:tabs[0]?.id;
        const buttons=[],panels=[];
        for(const tab of tabs){
          const key=`synced-panel-${++serial}`,selected=tab.id===active;
          buttons.push(`<button type="button" role="tab" aria-selected="${selected}" tabindex="${selected?0:-1}" data-synced-tab="${key}" data-synced-choice="${escape(tab.id)}">${text(tab.name||'탭')}</button>`);
          panels.push(`<div role="tabpanel" data-synced-panel="${key}"${selected?'':' hidden'}>${group(children.filter(child=>child.props?.tab_id===tab.id),depth+1,block)}</div>`);
        }
        body=`<div class="synced-content-tabs"><div role="tablist">${buttons.join('')}</div>${panels.join('')}</div>`;
        nested=group(children.filter(child=>!tabs.some(tab=>tab.id===child.props?.tab_id)),depth+1,block);
      }else{
        nested=group(children,depth+1,block);
        const content=host.textEditor?host.textEditor(block,rich(block)):rich(block);
        if(kind==='callout'){body=`<aside class="synced-content-callout"><span aria-hidden="true">${text(p.icon||'💡')}</span><div>${content}${nested}</div></aside>`;nested='';}
        else if(/^toggle(?:_heading[123])?$/.test(kind)){body=`<details${p.open===false?'':' open'}><summary>${content}</summary><div class="synced-content-children">${nested}</div></details>`;nested='';}
        else if(/^heading[123]$/.test(kind))body=`<h${kind.slice(-1)}>${content}</h${kind.slice(-1)}>`;
        else if(kind==='bullet')body=`<ul><li>${content}</li></ul>`;
        else if(kind==='numbered')body=`<ol start="${ordinal}"><li>${content}</li></ol>`;
        else if(kind==='todo')body=`<div class="synced-content-todo"><input type="checkbox" disabled${p.checked?' checked':''} aria-label="${p.checked?'완료':'미완료'}"><span>${content}</span></div>`;
        else if(kind==='quote')body=`<blockquote>${content}</blockquote>`;
        else if(kind==='divider')body='<hr>';
        else if(kind==='code')body=`<figure><pre><code>${host.textEditor?host.textEditor(block,escape(block.content)):escape(block.content)}</code></pre>${p.caption?`<figcaption>${text(p.caption)}</figcaption>`:''}</figure>`;
        else if(kind==='equation')body=host.textEditor?`<pre>${host.textEditor(block,text(block.content))}</pre>`:host.mathHtml?.(block.content)||`<pre>${text(block.content)}</pre>`;
        else if(kind==='simple_table')body=`<div class="synced-content-table"><table><tbody>${array(p.rows).map((row,r)=>`<tr>${array(row).map((cell,c)=>{const tag=p.header_row&&r===0||p.header_column&&c===0?'th':'td';return `<${tag}>${text(cell)}</${tag}>`;}).join('')}</tr>`).join('')}</tbody></table></div>`;
        else if(['image','file','audio','video','pdf'].includes(kind)){
          const url=p.file_id?host.fileUrl?.(p.file_id):'',name=p.name||p.original_name||block.content||'첨부파일';
          const safe=typeof url==='string'&&url.startsWith('/api/notion/')?escape(url):'';
          const media=safe?(kind==='image'?`<img loading="lazy" src="${safe}" alt="${escape(name)}">`:['audio','video'].includes(kind)?`<${kind} controls preload="none" src="${safe}"></${kind}>`:`<a href="${safe}" target="_blank" rel="noopener noreferrer">${text(name)}</a>`):text(name);
          body=`<figure>${media}${block.content?`<figcaption>${content}</figcaption>`:''}</figure>`;
        }else if(databaseTypes.has(kind))body=`<div class="synced-content-unavailable">${content||'데이터베이스'} · 최신 보기는 원본에서 확인해 주세요.</div>`;
        else if(kind==='bookmark'||kind==='embed')body=`<div class="synced-content-link">${content}<small>${text(p.url||p.link_url||'')}</small></div>`;
        else body=`<p>${content||'<br>'}</p>`;
      }
      const color=style.value(p,'text_color'),background=style.value(p,'background_color');
      const step=parent?.type==='tabs'||layoutContainer(parent)?0:1;
      const gap=parent?Math.max(0,number(p.indent)-number(parent.props?.indent)-step):0;
      const css=[`margin-left:${(gap+number(p.visual_indent,8))*24}px`];
      if(color!=='default')css.push(`color:var(--block-text-${color})`);
      if(background!=='default')css.push(`--synced-bg:var(--block-bg-${background})`);
      if(layoutContainer(block))css.push('margin-top:0','margin-bottom:0');
      return `<div class="synced-content-node" data-synced-node="${escape(block.id)}" data-synced-type="${escape(kind)}" style="${css.join(';')}">${body}${nested?`<div class="${kind==='synced_block'?'synced-content-root-children':'synced-content-children'}">${nested}</div>`:''}</div>`;
    }
    return `<div class="synced-content-document">${draw(tree,0)}</div>`;
  }
  function activate(button){
      const tabs=button.closest('.synced-content-tabs');
      tabs.querySelectorAll('[data-synced-tab]').forEach(item=>{if(item.closest('.synced-content-tabs')!==tabs)return;const selected=item===button;item.setAttribute('aria-selected',String(selected));item.tabIndex=selected?0:-1;});
      tabs.querySelectorAll('[data-synced-panel]').forEach(panel=>{if(panel.closest('.synced-content-tabs')===tabs)panel.hidden=panel.dataset.syncedPanel!==button.dataset.syncedTab;});
  }
  function capture(root){
    return [...root.querySelectorAll('[data-synced-node]')].map(node=>({id:node.dataset.syncedNode,
      open:node.querySelector(':scope > details')?.open,
      tab:node.querySelector(':scope > .synced-content-tabs > [role="tablist"] > [aria-selected="true"]')?.dataset.syncedChoice}));
  }
  function restore(root,states=[]){
    const previous=new Map(states.map(state=>[state.id,state]));
    for(const node of root.querySelectorAll('[data-synced-node]')){
      const state=previous.get(node.dataset.syncedNode);if(!state)continue;
      const details=node.querySelector(':scope > details');if(details&&typeof state.open==='boolean')details.open=state.open;
      const buttons=[...node.querySelectorAll(':scope > .synced-content-tabs > [role="tablist"] > [data-synced-choice]')];
      const selected=buttons.find(button=>button.dataset.syncedChoice===state.tab);if(selected)activate(selected);
    }
  }
  function bind(root){
    root.onclick=event=>{const button=event.target.closest('[data-synced-tab]');if(button&&root.contains(button)){event.stopPropagation();activate(button);}};
    root.onkeydown=event=>{const button=event.target.closest('[data-synced-tab]');if(!button||!root.contains(button)||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      const items=[...button.parentElement.querySelectorAll('[data-synced-tab]')],index=items.indexOf(button);
      const next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowRight'?1:-1)+items.length)%items.length;
      event.preventDefault();event.stopPropagation();activate(items[next]);items[next].focus();};
  }
  return {render,bind,capture,restore};
});

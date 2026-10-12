(function(root,factory){
  const api=factory(typeof module==='object'&&module.exports?require('./notion_editor_core'):root.NotionEditorCore);
  if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionBlockStructure=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(core){
  'use strict';
  const placement=['layout_group','layout_column','layout_columns','tab_group','tab_id'];
  const clone=value=>structuredClone(value);
  function model(blocks){
    const body=blocks.map(block=>({...clone(block),props:clone(block.props||{})})),outline=core.blockOutline(body),nodes=new Map(),parents=new Map(),children=new Map([[null,[]]]);
    body.forEach((block,index)=>{
      nodes.set(block.id,{block,index,entry:outline[index]});
      parents.set(block.id,body[outline[index].parentIndex]?.id||null);
      children.set(block.id,outline[index].childIndices.map(i=>body[i].id));
      if(outline[index].parentIndex<0)children.get(null).push(block.id);
    });
    return {body,outline,nodes,parents,children};
  }
  function descendants(m,id){return [id,...m.children.get(id).flatMap(child=>descendants(m,child))];}
  function selectedRoots(m,indices){
    const selected=new Set(core.expandBlockSelection(m.body,indices).map(i=>m.body[i].id));
    const ordered=[];
    function visit(id){if(selected.has(id)&&!selected.has(m.parents.get(id)))ordered.push(id);else for(const child of m.children.get(id))visit(child);}
    m.children.get(null).forEach(visit);return ordered;
  }
  function layoutMembers(m,scope){
    return (m.children.get(scope.parent)||[]).filter(id=>{
      const p=m.nodes.get(id).block.props;
      return p.layout_group===scope.group&&String(p.tab_group||'')===scope.tabGroup&&String(p.tab_id||'')===scope.tabId;
    });
  }
  function captureLayouts(m,roots){
    const scopes=new Map();
    for(const root of roots)for(let id=root;id!==null;id=m.parents.get(id)){
      const p=m.nodes.get(id).block.props;if(!p.layout_group)continue;
      let carrier=id,parent=m.parents.get(id);
      while(parent!==null){
        const owner=m.nodes.get(parent).block.props;
        if(owner.layout_group!==p.layout_group||Number(owner.layout_column||0)!==Number(p.layout_column||0))break;
        carrier=parent;parent=m.parents.get(parent);
      }
      const context=m.nodes.get(carrier).block.props;
      const scope={parent,group:p.layout_group,tabGroup:String(context.tab_group||''),tabId:String(context.tab_id||'')};
      const key=JSON.stringify(scope);if(scopes.has(key))continue;
      scope.occupied=new Set(layoutMembers(m,scope).map(id=>Number(m.nodes.get(id).block.props.layout_column||0)));
      scopes.set(key,scope);
    }
    return [...scopes.values()];
  }
  function compactLayouts(m,scopes){
    for(const scope of scopes){
      const members=layoutMembers(m,scope);
      const occupied=[...new Set(members.map(id=>Number(m.nodes.get(id).block.props.layout_column||0)))].sort((a,b)=>a-b);
      // Only a column vacated by this operation is removed. Existing sparse
      // layouts elsewhere, and ordinary movement within one lane, stay intact.
      if(!occupied.length||[...scope.occupied].every(column=>occupied.includes(column)))continue;
      const inherited=scope.parent===null?{}:clone(m.nodes.get(scope.parent).block.props);
      for(const id of members){
        const oldColumn=Number(m.nodes.get(id).block.props.layout_column||0);
        const context=occupied.length===1?inherited:{layout_group:scope.group,layout_column:occupied.indexOf(oldColumn),layout_columns:occupied.length};
        for(const child of descendants(m,id)){
          const p=m.nodes.get(child).block.props;
          if(p.layout_group===scope.group&&Number(p.layout_column||0)===oldColumn)for(const field of placement.slice(0,3)){
            if(context[field]!==undefined)p[field]=clone(context[field]);else delete p[field];
          }
        }
      }
    }
  }
  function serialize(m,vacatedLayouts=[]){
    // A layout owner has no editable row. Once its last child has moved out,
    // keeping it would turn it into an unexpected visible blank paragraph.
    let pruned;
    do{
      pruned=false;
      for(const [id,node] of m.nodes)if(core.isLayoutContainer(node.block)&&!m.children.get(id).length){
        detach(m,id);m.nodes.delete(id);m.parents.delete(id);m.children.delete(id);pruned=true;
      }
    }while(pruned);
    compactLayouts(m,vacatedLayouts);
    const result=[],seen=new Set();
    function visit(id,depth=0){
      if(seen.has(id)||depth>64)throw Error('블록의 하위 구조를 유지할 수 없습니다.');
      seen.add(id);const block=m.nodes.get(id).block,children=m.children.get(id);
      if(children.length||Array.isArray(block.props.children)&&block.props.children.every(id=>typeof id==='string')){
        if(block.props.children?.some(child=>typeof child!=='string'))throw Error('인라인 하위 본문을 먼저 일반 블록으로 변환해야 합니다.');
        block.props.children=children.slice();
      }
      result.push(block);children.forEach(child=>visit(child,depth+1));
    }
    m.children.get(null).forEach(id=>visit(id));
    if(seen.size!==m.nodes.size)throw Error('이동 중 일부 블록의 연결이 끊겼습니다.');
    const outline=core.blockOutline(result);
    result.forEach((block,index)=>{
      if((result[outline[index].parentIndex]?.id||null)!==m.parents.get(block.id))throw Error('블록의 들여쓰기와 부모 연결이 일치하지 않습니다.');
    });
    return result;
  }
  function detach(m,id){
    const siblings=m.children.get(m.parents.get(id));siblings.splice(siblings.indexOf(id),1);
  }
  function rebase(m,id,context,indent){
    const root=m.nodes.get(id),old=root.block.props,oldPlacement=clone(old),base=root.entry.structuralIndent;
    for(const child of descendants(m,id)){
      const node=m.nodes.get(child),props=node.block.props;
      const next=indent+node.entry.structuralIndent-base;
      if(next<0||next>32)throw Error('이 위치에서는 블록의 들여쓰기를 유지할 수 없습니다.');
      props.indent=next;
      if(String(props.layout_group||'')===String(oldPlacement.layout_group||'')&&Number(props.layout_column||0)===Number(oldPlacement.layout_column||0)){
        for(const key of placement.slice(0,3)){if(context[key]!==undefined)props[key]=clone(context[key]);else delete props[key];}
      }
      if(String(props.tab_group||'')===String(oldPlacement.tab_group||'')&&String(props.tab_id||'')===String(oldPlacement.tab_id||'')){
        for(const key of placement.slice(3)){if(context[key]!==undefined)props[key]=clone(context[key]);else delete props[key];}
      }
    }
  }
  function moveRelative(blocks,indices,targetIndex,position='after'){
    const m=model(blocks),target=m.body[targetIndex];
    if(!target||!['before','after'].includes(position))return {blocks:m.body,ids:[]};
    const roots=selectedRoots(m,indices),owned=new Set(roots.flatMap(id=>descendants(m,id)));
    if(!roots.length||owned.has(target.id))return {blocks:m.body,ids:[]};
    const layouts=captureLayouts(m,roots);
    const parent=m.parents.get(target.id),context=clone(target.props),indent=m.nodes.get(target.id).entry.indent;
    roots.forEach(id=>{detach(m,id);rebase(m,id,context,indent);m.parents.set(id,parent);});
    const siblings=m.children.get(parent),at=siblings.indexOf(target.id)+(position==='after'?1:0);
    siblings.splice(at,0,...roots);
    return {blocks:serialize(m,layouts),ids:[...owned]};
  }
  function vertical(blocks,indices,delta){
    const m=model(blocks),roots=new Set(selectedRoots(m,indices));let changed=false;
    if(!delta)return m.body;
    for(const siblings of m.children.values()){
      const lanes=new Map();
      siblings.forEach(id=>{const e=m.nodes.get(id).entry,key=e.scope+'|'+e.indent;if(!lanes.has(key))lanes.set(key,[]);lanes.get(key).push(id);});
      for(const lane of lanes.values()){
        const positions=lane.map(id=>siblings.indexOf(id));
        if(delta<0){for(let i=1;i<lane.length;i++)if(roots.has(lane[i])&&!roots.has(lane[i-1])){[lane[i-1],lane[i]]=[lane[i],lane[i-1]];changed=true;}}
        else{for(let i=lane.length-2;i>=0;i--)if(roots.has(lane[i])&&!roots.has(lane[i+1])){[lane[i],lane[i+1]]=[lane[i+1],lane[i]];changed=true;}}
        positions.forEach((position,i)=>{siblings[position]=lane[i];});
      }
    }
    return changed?serialize(m):m.body;
  }
  function indent(blocks,indices,delta){
    const m=model(blocks),roots=selectedRoots(m,indices),selected=new Set(roots);let changed=false;
    const layouts=captureLayouts(m,roots);
    const afterParent=new Map();
    for(const id of roots){
      const node=m.nodes.get(id),parent=m.parents.get(id),siblings=m.children.get(parent),at=siblings.indexOf(id);
      if(delta>0){
        const previous=siblings.slice(0,at).reverse().find(other=>!selected.has(other)&&m.nodes.get(other).entry.scope===node.entry.scope);
        if(!previous)continue;
        const owner=m.nodes.get(previous).block,context=clone(owner.props);
        if(owner.type==='tabs'){
          const tab=owner.props.tabs?.find(tab=>tab.id===owner.props.active_tab_id);
          if(!tab)throw Error('선택된 탭을 찾지 못했습니다.');
          context.tab_group=owner.id;context.tab_id=tab.id;
        }
        detach(m,id);rebase(m,id,context,m.nodes.get(previous).entry.indent+(owner.type==='tabs'||core.isLayoutContainer(owner)?0:1));
        m.parents.set(id,previous);m.children.get(previous).push(id);changed=true;
      }else if(delta<0&&parent!==null){
        const owner=m.nodes.get(parent),grand=m.parents.get(parent),context=clone(owner.block.props);
        detach(m,id);rebase(m,id,context,owner.entry.indent);m.parents.set(id,grand);
        const target=m.children.get(grand),anchor=afterParent.get(parent)||parent;
        target.splice(target.indexOf(anchor)+1,0,id);afterParent.set(parent,id);changed=true;
      }else if(delta<0&&node.entry.indent>0){
        rebase(m,id,clone(node.block.props),Math.max(0,node.entry.indent-1));changed=true;
      }else if(delta<0&&core.setIndent(node.block,-1).props.visual_indent!==node.block.props.visual_indent){
        node.block=core.setIndent(node.block,-1);changed=true;
      }
    }
    return changed?serialize(m,layouts):m.body;
  }
  function removeColumnLayout(blocks,indices){
    const m=model(blocks),roots=selectedRoots(m,indices).filter(id=>m.nodes.get(id).block.props.layout_group),owned=new Set(roots.flatMap(id=>descendants(m,id))),plans=[];
    const layouts=captureLayouts(m,roots);
    for(const id of roots){
      const original=m.nodes.get(id).block.props,group=original.layout_group;
      if(!group)continue;
      // Find the level that owns this grid, including inherited column tags
      // on descendants. Removing an inner grid leaves the outer grid intact.
      let carrier=id,parent=m.parents.get(id);
      while(parent!==null){
        const p=m.nodes.get(parent).block.props;
        if(p.layout_group!==group||Number(p.layout_column||0)!==Number(original.layout_column||0))break;
        carrier=parent;parent=m.parents.get(parent);
      }
      const source=m.nodes.get(carrier),siblings=m.children.get(parent);
      let anchor=carrier,context=clone(source.block.props),indent=source.entry.indent;
      for(const other of siblings){
        const p=m.nodes.get(other).block.props;
        if(p.layout_group===group&&String(p.tab_group||'')===String(context.tab_group||'')&&String(p.tab_id||'')===String(context.tab_id||''))anchor=other;
      }
      for(const key of placement.slice(0,3))delete context[key];
      if(parent!==null&&core.isLayoutContainer(m.nodes.get(parent).block)){
        anchor=parent;const owner=m.nodes.get(parent);context=clone(owner.block.props);indent=owner.entry.indent;parent=m.parents.get(parent);
      }
      const target=m.children.get(parent),after=target.indexOf(anchor)+1;
      const before=target.slice(after).find(other=>!owned.has(other))||null;
      plans.push({id,parent,context,indent,before});
    }
    if(!plans.length)return m.body;
    plans.forEach(({id})=>detach(m,id));
    for(const {id,parent,context,indent,before} of plans){
      rebase(m,id,context,indent);m.parents.set(id,parent);
      const target=m.children.get(parent),at=before===null?target.length:target.indexOf(before);
      target.splice(at,0,id);
    }
    return serialize(m,layouts);
  }
  function insert(blocks,index,incoming,{replace=false,before=false}={}){
    const m=model(blocks),anchor=m.body[index];
    if(!anchor||!incoming.length)return m.body;
    let added=model(incoming);
    const parent=m.parents.get(anchor.id);
    const incomingGroups=new Map();
    added.children.get(null).forEach(id=>{const p=added.nodes.get(id).block.props;if(p.layout_group){if(!incomingGroups.has(p.layout_group))incomingGroups.set(p.layout_group,new Set());incomingGroups.get(p.layout_group).add(Number(p.layout_column||0));}});
    if(anchor.props.layout_group&&[...incomingGroups.values()].some(columns=>columns.size>1)){
      // An invisible owner carries the outer column; its children retain their
      // independent inner grid. No editable blank paragraph is added to the UI.
      const wrapper=core.createBlock('paragraph',{props:{layout_container:true,children:added.children.get(null).slice()}});
      added=model([wrapper,...added.body]);
    }
    const roots=added.children.get(null).slice();
    for(const id of added.nodes.keys())if(m.nodes.has(id))throw Error('추가할 블록의 번호가 이미 사용 중입니다.');
    for(const [id,node] of added.nodes){m.nodes.set(id,node);m.parents.set(id,added.parents.get(id));m.children.set(id,added.children.get(id));}
    const context=clone(anchor.props),base=m.nodes.get(anchor.id).entry.indent,groups=new Map();
    roots.forEach(id=>{const p=m.nodes.get(id).block.props;if(p.layout_group){if(!groups.has(p.layout_group))groups.set(p.layout_group,new Set());groups.get(p.layout_group).add(Number(p.layout_column||0));}});
    roots.forEach(id=>{
      const props=m.nodes.get(id).block.props,ownColumns=groups.get(props.layout_group)?.size>1;
      const nextContext=clone(context);
      if(ownColumns){
        if(context.layout_group){
          let owner=parent,carried=false;
          while(owner!==null){const p=m.nodes.get(owner).block.props;if(p.layout_group===context.layout_group&&Number(p.layout_column||0)===Number(context.layout_column||0)){carried=true;break;}owner=m.parents.get(owner);}
          if(!carried)throw Error('이 열 안에 여러 열을 붙여넣으려면 별도 배치 묶음이 필요합니다.');
        }
        for(const key of placement.slice(0,3)){if(props[key]!==undefined)nextContext[key]=clone(props[key]);else delete nextContext[key];}
      }
      rebase(m,id,nextContext,base);m.parents.set(id,parent);
    });
    const siblings=m.children.get(parent),at=siblings.indexOf(anchor.id);
    if(replace&&m.children.get(anchor.id).length)throw Error('하위 블록이 있는 문단은 바꿀 수 없습니다.');
    if(replace){siblings.splice(at,1,...roots);m.nodes.delete(anchor.id);m.parents.delete(anchor.id);m.children.delete(anchor.id);}
    else siblings.splice(at+(before?0:1),0,...roots);
    return serialize(m);
  }
  function replace(blocks,index,replacement){
    const m=model(blocks),old=m.body[index];
    if(!old)throw Error('변경할 블록을 찾지 못했습니다.');
    const next={...clone(replacement),id:old.id,props:clone(replacement.props||{})};
    // Placement and owned content belong to this position, not its presentation.
    for(const key of [...placement,'indent','visual_indent','text_color','background_color']){
      if(old.props[key]!==undefined)next.props[key]=clone(old.props[key]);else delete next.props[key];
    }
    delete next.props.children;
    if(Array.isArray(old.props.children))next.props.children=clone(old.props.children);
    if(old.type==='tabs'&&next.type!=='tabs'){
      for(const node of m.nodes.values())if(node.block.props.tab_group===old.id){
        delete node.block.props.tab_group;delete node.block.props.tab_id;
      }
    }
    m.nodes.get(old.id).block=next;
    return serialize(m);
  }
  function columns(blocks,index,count,{replace=false,clear=false,before=false}={}){
    const m=model(blocks),source=m.body[index];
    if(!source)throw Error('변경할 블록을 찾지 못했습니다.');
    count=Math.max(2,Math.min(5,Math.trunc(Number(count)||2)));
    const group=core.createBlock('paragraph').id.replace(/^tmp_/,'layout_');
    const context=clone(source.props),base=m.nodes.get(source.id).entry.indent;
    if(!replace){
      const incoming=Array.from({length:count},(_,column)=>core.createBlock('paragraph',{props:{layout_group:group,layout_column:column,layout_columns:count}}));
      const next=insert(blocks,index,incoming,{before});
      return {blocks:next,focusId:incoming[0].id};
    }
    const parent=m.parents.get(source.id),siblings=m.children.get(parent),at=siblings.indexOf(source.id);
    const wrapper=core.createBlock('paragraph',{props:{layout_container:true,indent:base,...Object.fromEntries(placement.filter(key=>context[key]!==undefined).map(key=>[key,clone(context[key])]))}});
    const additions=Array.from({length:count-1},(_,column)=>core.createBlock('paragraph',{props:{indent:base,layout_group:group,layout_column:column+1,layout_columns:count,...Object.fromEntries(placement.slice(3).filter(key=>context[key]!==undefined).map(key=>[key,clone(context[key])]))}}));
    // A slash command has already removed its own text. Never erase remaining
    // content, formatting, descendants or a populated source on conversion.
    if(clear&&!source.content&&!source.props.rich_text_html){source.type='paragraph';delete source.props.icon;}
    rebase(m,source.id,{...context,layout_group:group,layout_column:0,layout_columns:count},base);
    const added=model([wrapper,...additions]);
    for(const [id,node] of added.nodes){if(m.nodes.has(id))throw Error('추가할 블록의 번호가 이미 사용 중입니다.');m.nodes.set(id,node);m.children.set(id,[]);}
    m.parents.set(wrapper.id,parent);siblings.splice(at,1,wrapper.id);
    m.children.set(wrapper.id,[source.id,...additions.map(b=>b.id)]);
    [source,...additions].forEach(b=>m.parents.set(b.id,wrapper.id));
    return {blocks:serialize(m),focusId:source.id};
  }
  function fingerprint(block){
    function ordered(value){
      if(Array.isArray(value))return value.map(ordered);
      if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(key=>[key,ordered(value[key])]));
      return value;
    }
    return JSON.stringify(ordered({type:block?.type,content:block?.content,props:block?.props||{}}));
  }
  function wrapTabs(blocks,index,controller,emptyMembers){
    const m=model(blocks),source=m.body[index],tabs=controller.props?.tabs;
    if(!source||controller.type!=='tabs'||!Array.isArray(tabs)||tabs.length!==emptyMembers.length+1)throw Error('탭 구조를 확인할 수 없습니다.');
    const added=model([controller,...emptyMembers]);
    for(const id of added.nodes.keys())if(m.nodes.has(id))throw Error('추가할 블록의 번호가 이미 사용 중입니다.');
    const parent=m.parents.get(source.id),siblings=m.children.get(parent),at=siblings.indexOf(source.id),base=m.nodes.get(source.id).entry.indent;
    const context=clone(source.props);
    for(const [id,node] of added.nodes){m.nodes.set(id,node);m.parents.set(id,added.parents.get(id));m.children.set(id,added.children.get(id));}
    const owner=m.nodes.get(controller.id).block;
    for(const key of placement){if(context[key]!==undefined)owner.props[key]=clone(context[key]);else delete owner.props[key];}
    owner.props.indent=base;
    m.parents.set(controller.id,parent);siblings.splice(at,1,controller.id);
    rebase(m,source.id,{...context,tab_group:controller.id,tab_id:tabs[0].id},base);
    m.parents.set(source.id,controller.id);
    m.children.set(controller.id,[source.id,...emptyMembers.map(member=>member.id)]);
    emptyMembers.forEach((member,i)=>{
      rebase(m,member.id,{...context,tab_group:controller.id,tab_id:tabs[i+1].id},base);
      m.parents.set(member.id,controller.id);
    });
    return serialize(m);
  }
  function appendTab(blocks,index,tab,member){
    const m=model(blocks),controller=m.body[index],tabs=controller?.props?.tabs;
    if(controller?.type!=='tabs'||!Array.isArray(tabs)||tabs.length>=20||tabs.some(item=>item.id===tab.id))throw Error('탭을 추가할 수 없습니다.');
    const added=model([member]);
    if(m.nodes.has(member.id))throw Error('추가할 블록의 번호가 이미 사용 중입니다.');
    m.nodes.set(member.id,added.nodes.get(member.id));m.parents.set(member.id,controller.id);m.children.set(member.id,[]);
    rebase(m,member.id,{...controller.props,tab_group:controller.id,tab_id:tab.id},m.nodes.get(controller.id).entry.indent);
    controller.props.tabs.push(clone(tab));controller.props.active_tab_id=tab.id;
    m.children.get(controller.id).push(member.id);
    return serialize(m);
  }
  function removeTab(blocks,index,tabId){
    const m=model(blocks),controller=m.body[index],tabs=controller?.props?.tabs;
    if(controller?.type!=='tabs'||!Array.isArray(tabs)||tabs.length<=2||!tabs.some(tab=>tab.id===tabId))throw Error('이 탭을 삭제할 수 없습니다.');
    const roots=m.body.flatMap((block,i)=>block.props?.tab_group===controller.id&&block.props.tab_id===tabId?[i]:[]);
    controller.props.tabs=tabs.filter(tab=>tab.id!==tabId);
    if(controller.props.active_tab_id===tabId)controller.props.active_tab_id=controller.props.tabs[0].id;
    const ordered=serialize(m),ids=new Set(roots.map(i=>m.body[i].id));
    return core.removeBlocks(ordered,ordered.flatMap((block,i)=>ids.has(block.id)?[i]:[]));
  }
  function duplicateTree(blocks,index,mapClone){
    const wanted=core.expandBlockSelection(blocks,[index]),copies=core.cloneBlocks(wanted.map(i=>blocks[i]),mapClone);
    const tabIds=new Map();
    copies.forEach(block=>{
      if(block.type!=='tabs')return;
      const mapping=new Map((block.props.tabs||[]).map(tab=>[tab.id,core.createBlock('paragraph').id.replace(/^tmp_/,'tab_')]));
      tabIds.set(block.id,mapping);
      block.props.tabs=(block.props.tabs||[]).map(tab=>({...tab,id:mapping.get(tab.id)}));
      block.props.active_tab_id=mapping.get(block.props.active_tab_id)||block.props.tabs[0]?.id;
    });
    copies.forEach(block=>{const mapping=tabIds.get(block.props?.tab_group);if(mapping)block.props.tab_id=mapping.get(block.props.tab_id)||block.props.tab_id;});
    return insert(blocks,index,copies);
  }
  return {moveRelative,vertical,indent,removeColumnLayout,insert,replace,columns,fingerprint,wrapTabs,appendTab,removeTab,duplicateTree};
});

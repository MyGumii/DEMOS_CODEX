(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionDatabaseItemBlocks=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const clone=value=>structuredClone(value);
  function entries(blocks,parent=null,result=[]){
    blocks.forEach((block,index)=>{result.push({block,list:blocks,index,parent});if(Array.isArray(block.props?.children))entries(block.props.children,block,result);});return result;
  }
  function roots(blocks,ids){
    const requested=new Set(ids),result=[];
    const walk=list=>list.forEach(block=>{if(requested.has(block.id))result.push(block.id);else if(Array.isArray(block.props?.children))walk(block.props.children);});
    walk(blocks);return result;
  }
  function without(blocks,ids){return blocks.filter(block=>!ids.has(block.id)).map(block=>{
    if(Array.isArray(block.props?.children))block.props.children=without(block.props.children,ids);return block;
  });}
  function positions(blocks){blocks.forEach((block,index)=>{block.position=index;if(Array.isArray(block.props?.children))positions(block.props.children);});return blocks;}
  function tabScope(blocks,id){
    const all=entries(blocks),byId=new Map(all.map(e=>[e.block.id,e]));let entry=byId.get(id);
    while(entry?.parent){
      if(entry.parent.type==='tabs'){
        const tabs=entry.parent.props?.tabs||[],tab=tabs.find(t=>t.id===entry.block.props?.tab_id);
        return tab?{group:entry.parent.id,tab:tab.id}:null;
      }
      entry=byId.get(entry.parent.id);
    }
    return null;
  }
  function contextualize(blocks,scope){
    const result=clone(blocks),internal=new Set(entries(result).filter(e=>e.block.type==='tabs').map(e=>e.block.id));
    for(const {block,parent} of entries(result)){
      block.props??={};
      if(internal.has(block.props.tab_group))continue;
      if(!parent||block.props.tab_group){
        if(scope){block.props.tab_group=scope.group;block.props.tab_id=scope.tab;}
        else{delete block.props.tab_group;delete block.props.tab_id;}
      }
    }
    return result;
  }
  function cloneTrees(blocks,idFactory,usedIds=[],{preserveRootLayouts=false}={}){
    const result=clone(blocks),all=entries(result),used=new Set([...usedIds,...all.map(e=>e.block.id)]),ids=new Map(),layouts=new Map();
    const inherited=preserveRootLayouts?new Set(result.map(b=>b.props?.layout_group).filter(Boolean)):new Set();
    function fresh(){for(let i=0;i<100;i++){const id=idFactory();if(id&&!used.has(id)){used.add(id);return id;}}throw Error('새 블록 번호를 만들지 못했습니다.');}
    for(const {block} of all)ids.set(block.id,fresh());
    for(const {block} of all){
      block.id=ids.get(block.id);block.props??={};
      if(ids.has(block.props.tab_group))block.props.tab_group=ids.get(block.props.tab_group);
      if(block.props.layout_group&&!inherited.has(block.props.layout_group)){const key=block.props.layout_group;if(!layouts.has(key))layouts.set(key,fresh());block.props.layout_group=layouts.get(key);}
    }
    return result;
  }
  function remove(blocks,ids){return {blocks:positions(without(clone(blocks),new Set(roots(blocks,ids)))),ids:[]};}
  function duplicate(blocks,ids,idFactory){
    const result=clone(blocks),selected=new Set(roots(result,ids)),chosen=entries(result).filter(e=>selected.has(e.block.id));
    const copies=cloneTrees(chosen.map(e=>e.block),idFactory,entries(result).map(e=>e.block.id),{preserveRootLayouts:true}),created=[];
    chosen.forEach((entry,index)=>{const copy=copies[index];entry.list.splice(entry.list.indexOf(entry.block)+1,0,copy);created.push(copy.id);});
    return {blocks:positions(result),ids:created};
  }
  function move(blocks,ids,targetId,placement='before'){
    const selected=roots(blocks,ids),result=clone(blocks),all=entries(result),target=all.find(e=>e.block.id===targetId);
    if(!selected.length||!target||!['before','after','inside'].includes(placement))return null;
    const selectedSet=new Set(selected);let moving=all.filter(e=>selectedSet.has(e.block.id)).map(e=>e.block);
    if(moving.some(block=>entries([block]).some(e=>e.block.id===targetId)))return null;
    if(placement==='inside'&&!target.block.type.startsWith('toggle')&&target.block.type!=='tabs')return null;
    const targetTabs=target.block.type==='tabs'&&placement==='inside'?target.block:null;
    const scope=targetTabs?{group:targetTabs.id,tab:targetTabs.props?.active_tab_id||targetTabs.props?.tabs?.[0]?.id}:tabScope(result,targetId);
    if(targetTabs&&!scope.tab)return null;
    moving=contextualize(moving,scope);
    const remaining=without(result,selectedSet),destination=entries(remaining).find(e=>e.block.id===targetId);
    if(placement==='inside'){
      destination.block.props??={};destination.block.props.children??=[];if(!targetTabs)destination.block.props.open=true;destination.block.props.children.push(...moving);
    }else destination.list.splice(destination.index+(placement==='after'?1:0),0,...moving);
    positions(remaining);
    if(JSON.stringify(remaining)===JSON.stringify(positions(clone(blocks))))return null;
    return {blocks:remaining,ids:selected};
  }
  function step(blocks,ids,visible,delta){
    const selected=roots(blocks,ids),all=entries(blocks),chosen=all.filter(e=>selected.includes(e.block.id));
    if(!chosen.length||chosen.some(e=>e.list!==chosen[0].list))return null;
    const visibleIds=new Set(visible),list=chosen[0].list.filter(b=>visibleIds.has(b.id)),indices=chosen.map(e=>list.indexOf(e.block)),index=delta<0?Math.min(...indices)-1:Math.max(...indices)+1;
    if(indices.some(index=>index<0))return null;
    if(index<0||index>=list.length)return null;
    return move(blocks,selected,list[index].id,delta<0?'before':'after');
  }
  function text(blocks,ids){
    const selected=new Set(roots(blocks,ids));
    const cell=value=>{const text=String(value??'');return /["\t\r\n]/.test(text)?'"'+text.replaceAll('"','""')+'"':text;};
    return entries(blocks).filter(e=>selected.has(e.block.id)).map(e=>entries([e.block]).map(({block})=>block.type==='simple_table'?(block.props?.rows||[]).map(row=>row.map(cell).join('\t')).join('\n'):block.content||'').join('\n')).join('\n');
  }
  function insert(blocks,targetId,additions){
    const result=clone(blocks),target=entries(result).find(e=>e.block.id===targetId);
    if(!target||!additions.length)return null;
    const replace=target.block.type==='paragraph'&&!target.block.content&&!target.block.props?.children?.length;
    target.list.splice(target.index+(replace?0:1),replace?1:0,...contextualize(additions,tabScope(result,targetId)));
    return {blocks:positions(result),ids:additions.map(b=>b.id)};
  }
  return {entries,roots,remove,duplicate,move,step,text,insert,tabScope,contextualize,cloneTrees};
});

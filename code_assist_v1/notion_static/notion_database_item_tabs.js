(function(root,factory){const api=factory(typeof module==='object'&&module.exports?require('./notion_database_item_blocks'):root.NotionDatabaseItemBlocks);if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionDatabaseItemTabs=api;})(typeof globalThis==='object'?globalThis:this,function(ops){
  'use strict';
  const clone=value=>structuredClone(value);
  function describe(block,active){
    const seen=new Set(),tabs=(Array.isArray(block.props?.tabs)?block.props.tabs:[]).filter(t=>{
      if(!t||typeof t.id!=='string'||!t.id||seen.has(t.id))return false;seen.add(t.id);return true;
    }).map(t=>({...t,name:String(t.name||'탭')}));
    return {tabs,active:tabs.some(t=>t.id===active)?active:tabs.some(t=>t.id===block.props?.active_tab_id)?block.props.active_tab_id:tabs[0]?.id||''};
  }
  const belongs=(child,owner,tab)=>child.props?.tab_id===tab&&(!child.props.tab_group||child.props.tab_group===owner);
  function initialize(block,createBlock){
    const result=clone(block),defaults=createBlock('tabs'),contextKeys=['indent','visual_indent','layout_group','layout_column','layout_columns','tab_group','tab_id'];
    result.type='tabs';result.content='';result.props={...defaults.props};
    for(const key of contextKeys)if(block.props?.[key]!=null)result.props[key]=block.props[key];
    const first=result.props.active_tab_id,children=[];
    if(block.type!=='tabs'&&(block.content||block.props?.children?.length||block.type!=='paragraph')){
      const child=clone(block);child.id=createBlock().id;for(const key of ['indent','visual_indent','tab_group','tab_id'])delete child.props[key];children.push(child);
    }
    result.props.children=ops.contextualize(children,{group:result.id,tab:first});
    return result;
  }
  function unwrap(block){
    const result=clone(block);delete result.props.tabs;delete result.props.active_tab_id;
    result.props.children=ops.contextualize(result.props.children||[],null);return result;
  }
  function mutate(blocks,ownerId,action,{tabId,name,createBlock}={}){
    const result=clone(blocks),owner=ops.entries(result).find(e=>e.block.id===ownerId)?.block;
    if(owner?.type!=='tabs')return null;
    const info=describe(owner),selected=info.tabs.find(t=>t.id===tabId),props=owner.props;
    props.tabs??=[];props.children??=[];
    const fresh=()=>{const used=new Set([...ops.entries(result).map(e=>e.block.id),...info.tabs.map(t=>t.id)]);for(let n=0;n<100;n++){const id=createBlock().id;if(!used.has(id))return id;}throw Error('새 탭 번호를 만들지 못했습니다.');};
    let focusTabId=tabId||info.active;
    if(action==='add'){
      const id=fresh();props.tabs.push({id,name:'새 탭'});props.active_tab_id=id;focusTabId=id;
      props.children.push(...ops.contextualize([createBlock()],{group:owner.id,tab:id}));
    }else{
      if(!selected)return null;const index=props.tabs.findIndex(t=>t.id===tabId);
      if(action==='select'){if(props.active_tab_id===tabId)return null;props.active_tab_id=tabId;}
      else if(action==='rename'){const value=String(name||'').trim().slice(0,80);if(!value||value===props.tabs[index].name)return null;props.tabs[index]={...props.tabs[index],name:value};}
      else if(action==='left'||action==='right'){
        const adjacent=info.tabs[info.tabs.findIndex(t=>t.id===tabId)+(action==='left'?-1:1)];if(!adjacent)return null;
        const next=props.tabs.findIndex(t=>t.id===adjacent.id);[props.tabs[index],props.tabs[next]]=[props.tabs[next],props.tabs[index]];
      }else if(action==='duplicate'){
        const id=fresh(),children=props.children.filter(b=>belongs(b,owner.id,tabId));
        const copies=ops.cloneTrees(children,()=>fresh(),[id,...info.tabs.map(t=>t.id),...ops.entries(result).map(e=>e.block.id)]);
        props.tabs.splice(index+1,0,{...props.tabs[index],id,name:selected.name+' 사본'});
        props.children.push(...ops.contextualize(copies,{group:owner.id,tab:id}));props.active_tab_id=id;focusTabId=id;
      }else if(action==='remove'){
        if(info.tabs.length<2)return null;props.tabs.splice(index,1);props.children=props.children.filter(b=>!belongs(b,owner.id,tabId));
        if(props.active_tab_id===tabId){const at=info.tabs.findIndex(t=>t.id===tabId);props.active_tab_id=info.tabs[at>0?at-1:1].id;}
        focusTabId=props.active_tab_id;
      }else if(action==='content'){
        const child=createBlock();props.children.push(...ops.contextualize([child],{group:owner.id,tab:tabId}));props.active_tab_id=tabId;
        return {blocks:result,ids:[ownerId],focusTabId:tabId,focusBlockId:child.id};
      }else return null;
    }
    return {blocks:result,ids:[ownerId],focusTabId};
  }
  return {describe,belongs,initialize,unwrap,mutate};
});

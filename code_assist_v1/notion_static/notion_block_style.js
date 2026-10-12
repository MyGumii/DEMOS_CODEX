(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionBlockStyle=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const colors=Object.freeze(Object.entries({default:'기본',gray:'회색',brown:'갈색',orange:'주황색',yellow:'노란색',green:'초록색',blue:'파란색',purple:'보라색',pink:'분홍색',red:'빨간색'}).map(([value,label])=>Object.freeze({value,label})));
  const names=new Set(colors.map(color=>color.value)),keys=new Set(['text_color','background_color']);
  const value=(props,key)=>names.has(props?.[key])?props[key]:'default';
  function apply(row,block){
    for(const [key,variable,className,prefix] of [['text_color','--block-fg','has-text-color','text'],['background_color','--block-bg','has-bg-color','bg']]){
      const color=value(block.props,key),active=color!=='default';
      row.classList.toggle(className,active);row.style.removeProperty(variable);
      if(active)row.style.setProperty(variable,`var(--block-${prefix}-${color})`);
    }
  }
  function paint(blocks,ids,key,color){
    if(!keys.has(key)||!names.has(color))return null;
    const selected=new Set(ids),copy=structuredClone(blocks);let changed=false;
    function visit(list){for(const block of list){
      if(selected.has(block.id)&&value(block.props,key)!==color){
        block.props||={};if(color==='default')delete block.props[key];else block.props[key]=color;changed=true;
      }
      if(Array.isArray(block.props?.children))visit(block.props.children);
    }}
    visit(copy);return changed?{blocks:copy,ids:[...selected]}:null;
  }
  return {colors,value,apply,paint};
});

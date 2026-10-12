(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionTimelineHourWindow=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const DAY_WIDTH=96*72,EDGE_WIDTH=600;
  const windows=new Map();
  function shiftDate(value,days){
    const match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value||''));
    if(!match)return '';
    const date=new Date(0);
    date.setFullYear(Number(match[1]),Number(match[2])-1,Number(match[3]));
    date.setHours(12,0,0,0);
    if(date.getFullYear()!==Number(match[1])||date.getMonth()+1!==Number(match[2])||date.getDate()!==Number(match[3]))return '';
    date.setDate(date.getDate()+days);
    return `${String(date.getFullYear()).padStart(4,'0')}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
  }
  function start(key,anchor){
    const id=String(key||''),base=shiftDate(anchor,0);
    if(!id||!base)return base;
    const current=windows.get(id);
    if(!current||current.anchor!==base){windows.set(id,{anchor:base,start:base});return base;}
    return current.start;
  }
  function reset(key){windows.delete(String(key||''));}
  function shift(key,anchor,direction,left){
    const id=String(key||''),base=start(id,anchor),delta=direction==='right'?1:-1;
    const next=shiftDate(base,delta);
    if(!next)return null;
    windows.set(id,{anchor:shiftDate(anchor,0),start:next});
    return {start:next,left:Math.max(0,left-delta*DAY_WIDTH)};
  }
  function bind(scroll,key,anchor,redraw){
    if(!scroll||!key)return;
    let pending=false;
    scroll.addEventListener('scroll',()=>{
      if(pending||!scroll.isConnected)return;
      const max=scroll.scrollWidth-scroll.clientWidth;
      if(max<=0)return;
      const direction=scroll.scrollLeft<EDGE_WIDTH?'left'
        :max-scroll.scrollLeft<EDGE_WIDTH?'right':null;
      if(!direction)return;
      pending=true;
      const result=shift(key,anchor,direction,scroll.scrollLeft);
      if(result)redraw(result.left);
    },{passive:true});
  }
  return {DAY_WIDTH,EDGE_WIDTH,shiftDate,start,reset,shift,bind};
});

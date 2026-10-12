'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(){
  const listeners=[],frames=new Map(),overlays=new Set();let sequence=0,selected=[],active=true,subscribed=true,scroll=0;
  const eventTarget=()=>({addEventListener(type,fn,options){listeners.push({target:this,type,fn,options});},removeEventListener(type,fn){const index=listeners.findIndex(e=>e.target===this&&e.type===type&&e.fn===fn);if(index>=0)listeners.splice(index,1);}});
  const document={...eventTarget(),activeElement:{blur(){}},querySelector(){throw Error('custom item selection must not access the main page canvas');},
    createElement:()=>({style:{},setAttribute(){},remove(){overlays.delete(this);}}),body:{appendChild:e=>overlays.add(e),classList:{add(){},remove(){}}}};
  const window={...eventTarget(),innerWidth:1920,innerHeight:900,getSelection:()=>({removeAllRanges(){}})};
  const scroller={isConnected:true,hidden:false,clientHeight:856,scrollHeight:2200,
    get scrollTop(){return scroll;},set scrollTop(value){scroll=Math.max(0,Math.min(1344,value));},setPointerCapture(){},releasePointerCapture(){},
    getBoundingClientRect:()=>({left:1280,right:1880,top:44,bottom:900})};
  const surface={getBoundingClientRect:()=>({left:1160,right:1920,top:0,bottom:900})};
  const specs=[['heading',290,330],['paragraph',335,370],['parent',375,410],['child',415,450],['table',455,550]];
  const rows=specs.map(([id,top,bottom])=>({dataset:{bodyBlock:id},hidden:false,getClientRects:()=>[{}],
    content:{getBoundingClientRect:()=>({left:1320,right:1840,top:top-scroll,bottom:bottom-scroll})}}));
  const context={document,window,performance:{now:()=>1000},requestAnimationFrame:fn=>{frames.set(++sequence,fn);return sequence;},cancelAnimationFrame:id=>frames.delete(id)};
  vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../notion_static/notion_ui_interaction.js'),'utf8'),context);
  const control=context.NotionUiInteraction.bindGutterSelection({pageKey:()=>active?'row-a':null,rows:()=>rows,rowId:row=>row.dataset.bodyBlock,rowContent:row=>row.content,
    start:event=>active&&!event.control?{canvas:scroller,scroller,surface}:null,toolbarBottom:()=>44,marqueeClass:'item-body-marquee',selected:()=>selected,select:ids=>selected=Array.from(ids),
    subscribe:()=>()=>subscribed=false});
  const emit=(type,options={})=>{const event={button:0,isPrimary:true,pointerType:'mouse',pointerId:1,clientX:1900,clientY:60,target:{},preventDefault(){this.defaultPrevented=true;},stopImmediatePropagation(){},...options};
    for(const e of [...listeners].filter(e=>e.target===document&&e.type===type))e.fn(event);return event;};
  const tick=()=>{const pending=[...frames.values()];frames.clear();pending.forEach(fn=>fn());};
  return {control,emit,tick,overlays,frames,rows,listeners,scroller,get selected(){return selected;},setSelected:ids=>selected=ids,leave:()=>active=false,get subscribed(){return subscribed;}};
}
test('UI-ITEM-SELECTION full gutter and title-crossing rectangle selects body heading, child and table only',()=>{
  const f=fixture();f.emit('pointerdown');f.emit('pointermove',{clientX:1180,clientY:580});f.tick();
  assert.deepEqual(f.selected,['heading','paragraph','parent','child','table']);const box=[...f.overlays][0];
  assert.equal(box.className,'block-selection-marquee item-body-marquee');assert.deepEqual({...box.style},{left:'1180px',top:'60px',width:'720px',height:'520px'});
  f.emit('pointerup',{clientX:1180,clientY:580});assert.equal(f.overlays.size,0);assert.equal(f.selected.length,5);
});
test('UI-ITEM-SELECTION control/text starts stay native and wholly empty gutter selects nothing',()=>{
  const f=fixture();assert.equal(f.emit('pointerdown',{control:true}).defaultPrevented,undefined);assert.equal(f.control.active(),false);
  f.emit('pointerdown');f.emit('pointermove',{clientX:1850,clientY:600});f.tick();assert.deepEqual(f.selected,[]);assert.equal(f.overlays.size,1);
});
test('UI-ITEM-SELECTION Escape restores additive selection and a removed child cannot return',()=>{
  const f=fixture();f.setSelected(['table']);f.emit('pointerdown',{shiftKey:true});f.emit('pointermove',{clientX:1200,clientY:370});f.tick();
  assert.deepEqual(f.selected,['table','heading','paragraph']);f.rows.splice(4,1);f.control.refresh();f.tick();f.emit('keydown',{key:'Escape'});
  assert.deepEqual(f.selected,[]);assert.equal(f.overlays.size,0);assert.equal(f.frames.size,0);
});
test('UI-ITEM-SELECTION scrolling stays in the item scroller and release never adds another scroll',()=>{
  const f=fixture();f.emit('pointerdown');f.emit('pointermove',{clientX:1200,clientY:899});f.tick();assert.ok(f.scroller.scrollTop>0);
  const before=f.scroller.scrollTop;f.emit('pointerup',{clientX:1200,clientY:899});assert.equal(f.scroller.scrollTop,before);assert.equal(f.frames.size,0);
});

test('UI-ITEM-SELECTION scroll edges follow the visible viewport of a partially clipped item',()=>{
  const f=fixture();f.scroller.getBoundingClientRect=()=>({left:1280,right:1880,top:-100,bottom:1500});
  f.emit('pointerdown');f.emit('pointermove',{clientX:1200,clientY:899});f.tick();
  assert.ok(f.scroller.scrollTop>0,'scroll at the visible lower edge, not an offscreen element edge');
  f.emit('pointercancel');
});
test('UI-ITEM-SELECTION disposal removes a stationary overlay and every document/window subscription',()=>{
  const f=fixture();f.emit('pointerdown');f.emit('pointermove',{clientX:1200,clientY:500});f.tick();assert.equal(f.overlays.size,1);
  f.control.dispose();assert.equal(f.overlays.size,0);assert.equal(f.listeners.length,0);assert.equal(f.subscribed,false);assert.equal(f.control.active(),false);
  f.emit('pointerdown');assert.equal(f.control.active(),false);
});
test('UI-ITEM-SELECTION item identity loss cannot restore a previous row selection',()=>{
  const f=fixture();f.setSelected(['table']);f.emit('pointerdown');f.emit('pointermove',{clientX:1200,clientY:370});f.tick();
  f.leave();f.setSelected(['another-row']);f.control.refresh();f.tick();assert.equal(f.overlays.size,0);assert.deepEqual(f.selected,['another-row']);
});

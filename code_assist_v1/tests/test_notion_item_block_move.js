'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(){
  const listeners=[],frames=new Map(),moves=[],previews=[];let seq=0,selected=['a'],enabled=true,signature='original',scroll=0;
  const target=()=>({addEventListener(type,fn){listeners.push({target:this,type,fn});},removeEventListener(type,fn){const i=listeners.findIndex(e=>e.target===this&&e.type===type&&e.fn===fn);if(i>=0)listeners.splice(i,1);}});
  const document={...target(),activeElement:{blur(){}}},window={...target(),getSelection:()=>({removeAllRanges(){}})};
  const surface={...target(),getBoundingClientRect:()=>({left:200,right:1900})};
  const scroller={getBoundingClientRect:()=>({top:44,bottom:900}),get scrollTop(){return scroll;},set scrollTop(n){scroll=Math.max(0,Math.min(1000,n));}};
  const handle={id:'a',isConnected:true,setPointerCapture(){},releasePointerCapture(){}};
  const ctx={document,window,performance:{now:()=>1000},requestAnimationFrame:fn=>{frames.set(++seq,fn);return seq;},cancelAnimationFrame:id=>frames.delete(id)};
  vm.createContext(ctx);vm.runInContext(fs.readFileSync(require.resolve('../notion_static/notion_ui_interaction.js'),'utf8'),ctx);
  const control=ctx.NotionUiInteraction.bindBlockMove({surface,scroller,enabled:()=>enabled,signature:()=>signature,handle:t=>t===handle?t:null,id:h=>h.id,
    selected:()=>[...selected],select:ids=>selected=Array.from(ids),destination:p=>p.x<200?null:{id:p.y<450?'before':'after'},preview:p=>previews.push(p),move:(ids,p)=>moves.push({ids:Array.from(ids),...p})});
  const emit=(where,type,extra={})=>{const e={button:0,isPrimary:true,pointerId:1,clientX:700,clientY:400,target:handle,preventDefault(){this.defaultPrevented=true;},stopImmediatePropagation(){this.stopped=true;},...extra};for(const item of [...listeners].filter(i=>i.target===where&&i.type===type))item.fn(e);return e;};
  const tick=()=>{const pending=[...frames.values()];frames.clear();pending.forEach(fn=>fn());};
  return {control,document,window,surface,handle,frames,listeners,moves,previews,tick,scroller,emit,down:e=>emit(surface,'pointerdown',e),move:e=>emit(document,'pointermove',e),up:e=>emit(document,'pointerup',e),
    select:ids=>selected=ids,revoke:()=>enabled=false,change:()=>signature='new',get selected(){return selected;}};
}
test('UI-ITEM-MOVE a click selects a block but leaves its menu click available',()=>{
  const f=fixture();f.select(['b']);f.down();f.move({clientX:702});f.up();assert.deepEqual(f.selected,['a']);assert.equal(f.moves.length,0);
  assert.equal(f.emit(f.surface,'click').defaultPrevented,undefined);
});
test('UI-ITEM-MOVE selected group uses release coordinates and suppresses the following menu click',()=>{
  const f=fixture();f.select(['a','b']);f.down();f.move({clientY:500});f.tick();f.up({clientY:300});
  assert.deepEqual(f.moves,[{ids:['a','b'],id:'before'}]);assert.equal(f.frames.size,0);assert.equal(f.previews.at(-1),null);
  assert.equal(f.emit(f.surface,'click').stopped,true);assert.equal(f.emit(f.surface,'click').defaultPrevented,undefined);
});
test('UI-ITEM-MOVE release outside the surface never applies a stale destination',()=>{
  const f=fixture();f.down();f.move({clientY:500});f.tick();f.up({clientX:100,clientY:500});assert.equal(f.moves.length,0);
});
test('UI-ITEM-MOVE Escape and pointer capture loss restore the previous selection',()=>{
  for(const type of ['keydown','pointercancel','lostpointercapture']){const f=fixture();f.select(['b']);f.down();f.move({clientY:500});f.tick();f.emit(f.document,type,{key:'Escape'});
    assert.deepEqual(f.selected,['b']);assert.equal(f.moves.length,0);assert.equal(f.frames.size,0);assert.equal(f.control.active(),false);}
});
test('UI-ITEM-MOVE permission or body change cancels a held movement before commit',()=>{
  for(const mutate of [f=>f.revoke(),f=>f.change(),f=>f.handle.isConnected=false]){const f=fixture();f.down();f.move({clientY:500});mutate(f);f.tick();f.up();assert.equal(f.moves.length,0);assert.equal(f.control.active(),false);}
  const f=fixture();f.revoke();assert.equal(f.down().defaultPrevented,undefined);
});
test('UI-ITEM-MOVE scrolls the item only and release adds no extra scroll frame',()=>{
  const f=fixture();f.down();f.move({clientY:899});f.tick();assert.ok(f.scroller.scrollTop>0);const at=f.scroller.scrollTop;f.up({clientY:899});assert.equal(f.scroller.scrollTop,at);assert.equal(f.frames.size,0);
});
test('UI-ITEM-MOVE external scroll refreshes a stationary pointer and disposal removes every listener',()=>{
  const f=fixture();f.down();f.move({clientY:500});f.tick();assert.equal(f.frames.size,0);f.emit(f.window,'scroll');assert.equal(f.frames.size,1);
  f.control.dispose();assert.equal(f.frames.size,0);assert.equal(f.listeners.length,0);assert.equal(f.control.active(),false);assert.equal(f.moves.length,0);
});

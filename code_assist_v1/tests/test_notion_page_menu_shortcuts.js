"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../notion_static/notion.js'),'utf8');
const helper=source.slice(source.indexOf('function pageContextShortcutAction('),source.indexOf('function openPageMenu('));
const a=source.indexOf('  menu.onkeydown = event=>{',source.indexOf('function openPageMenu('));
const b=source.indexOf('  menu.hidden = false;',a);
assert(a>0&&b>a);
const calls=[],buttons=new Map(),page={id:'menu-child',can_edit:true,can_move:true},anchor={},search={};
for(const action of ['copy_link','rename','duplicate','move','new_tab'])buttons.set(action,{hidden:false,disabled:false,getAttribute:()=>null,click:()=>calls.push([action,page.id])});
const menu={querySelector:selector=>buttons.get(selector.match(/data-page-action="([^"]+)"/)?.[1]),querySelectorAll:()=>[...buttons.values()]};
const context={page,anchor,search,menu,State:{navigation:{collaborativeSharing:true},pageData:{page:{id:'different-main-page'}}},
  pageCanManageLifecycle:()=>true,pageCanDuplicate:()=>true,handlePageMenuAction:async(action,p)=>calls.push([action,p.id]),setStatus(){},
  pageIsLocked:p=>Boolean(p?.settings?.locked),
  document:{activeElement:null},closePageMenu(){}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function pageCanMove('),source.indexOf('function pageCanCreateChild(')),context);
vm.runInContext(helper+'\n'+source.slice(a,b),context);
function event(key,extra={}){return {key,ctrlKey:true,shiftKey:false,target:{closest:()=>null},preventDefault(){this.defaultPrevented=true;},stopPropagation(){this.stopped=true;},...extra};}
for(const [key,shiftKey,action]of [['r',true,'rename'],['d',false,'duplicate'],['p',true,'move'],['Enter',true,'new_tab']]){
  const e=event(key,{shiftKey});menu.onkeydown(e);
  assert.equal(e.defaultPrevented,true);assert.equal(e.stopped,true);
  assert.deepEqual(calls.at(-1),[action,'menu-child']);
}
calls.length=0;
const link=event('l',{altKey:true});menu.onkeydown(link);
assert.equal(link.stopped,true);assert.deepEqual(calls.pop(),['copy_link','menu-child']);
for(const fields of [{altKey:true,shiftKey:true},{altKey:true,getModifierState:()=>true},{altKey:true,isComposing:true},{altKey:true,target:{closest:()=>({})}}]){
  const e=event('l',fields);menu.onkeydown(e);assert.equal(calls.length,0);assert.equal(e.stopped,undefined);
}
for(const state of [{hidden:true},{disabled:true},{getAttribute:()=> 'true'}]){
  const item=buttons.get('duplicate'),old={...item};Object.assign(item,state);
  const e=event('d');menu.onkeydown(e);assert.equal(e.stopped,true);assert.equal(calls.length,0,'unavailable action must not fall through to main page');Object.assign(item,old);
}
buttons.delete('move');const missing=event('p',{shiftKey:true});menu.onkeydown(missing);assert.equal(missing.stopped,true);assert.equal(calls.length,0);
for(const fields of [{isComposing:true},{keyCode:229},{altKey:true},{defaultPrevented:true}]){
  const e=event('r',{shiftKey:true,...fields});menu.onkeydown(e);assert.equal(calls.length,0);
}
const held=event('d',{repeat:true});menu.onkeydown(held);assert.equal(held.stopped,true);assert.equal(calls.length,0);
const mac=event('r',{ctrlKey:false,metaKey:true,shiftKey:true});menu.onkeydown(mac);assert.deepEqual(calls.pop(),['rename','menu-child']);
const denied={id:'reader',can_edit:false,can_move:false};
for(const e of [event('r',{shiftKey:true}),event('p',{shiftKey:true}),event('d')]){
  assert.equal(context.handlePageContextShortcut(e,denied,anchor),true);assert.equal(calls.length,0);
}
context.handlePageContextShortcut(event('Enter',{shiftKey:true}),denied,anchor);assert.deepEqual(calls.pop(),['new_tab','reader']);
context.handlePageContextShortcut(event('l',{altKey:true}),denied,anchor);assert.deepEqual(calls.pop(),['copy_link','reader']);
for(const collaborativeSharing of [false,true]) {
  context.State.navigation.collaborativeSharing=collaborativeSharing;
  const locked={id:'locked-row',scope:'own',role:'owner',can_edit:true,can_move:true,settings:{locked:true}};
  for(const e of [event('r',{shiftKey:true}),event('p',{shiftKey:true}),event('d')]) {
    assert.equal(context.handlePageContextShortcut(e,locked,anchor),true);
    assert.equal(e.stopped,true);
    assert.equal(calls.length,0,'locked row shortcut cannot fall through to another page');
  }
  context.handlePageContextShortcut(event('l',{altKey:true}),locked,anchor);
  assert.deepEqual(calls.pop(),['copy_link','locked-row']);
  context.handlePageContextShortcut(event('Enter',{shiftKey:true}),locked,anchor);
  assert.deepEqual(calls.pop(),['new_tab','locked-row']);
  locked.settings.locked=false;
  context.handlePageContextShortcut(event('r',{shiftKey:true}),locked,anchor);
  assert.deepEqual(calls.pop(),['rename','locked-row']);
  context.handlePageContextShortcut(event('p',{shiftKey:true}),locked,anchor);
  assert.deepEqual(calls.pop(),['move','locked-row']);
}
console.log('PASS page menu shortcuts: menu/row target, unavailable action isolation, IME, repeat, platform keys and read-only access');

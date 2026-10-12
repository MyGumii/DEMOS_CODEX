"use strict";
const {test}=require('node:test'),assert=require('node:assert/strict');
const {create,changeSections}=require('../notion_static/notion_sidebar_tab_settings.js');
const clone=x=>JSON.parse(JSON.stringify(x));
const original=[{section:'private',hidden:false,collapsed:false,item_limit:20,display_order:0},{section:'recents',hidden:false,collapsed:true,item_limit:8,display_order:1}];
function fixture(){
  const document={activeElement:null,listeners:{},addEventListener(name,fn){this.listeners[name]=fn;}};
  class Node{
    constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.attrs={};this.listeners={};this.hidden=false;this.disabled=false;this.textContent='';this.classes=new Set();this.classList={add:x=>this.classes.add(x),remove:x=>this.classes.delete(x),toggle:(x,on)=>on?this.classes.add(x):this.classes.delete(x)};}
    append(...items){items.forEach(item=>{item.parent=this;this.children.push(item);});}
    replaceChildren(...items){if(this.contains(document.activeElement))document.activeElement=document.body;this.children.forEach(c=>c.parent=null);this.children=[];this.append(...items);}
    contains(node){return node===this||this.children.some(c=>c.contains(node));}
    get isConnected(){return document.body.contains(this);}
    setAttribute(k,v){this.attrs[k]=v;}
    getAttribute(k){return this.attrs[k]??null;}
    addEventListener(k,v){this.listeners[k]=v;}
    focus(){document.activeElement=this;}
    querySelectorAll(selector){const all=this.children.flatMap(c=>[c,...c.querySelectorAll('*')]);if(selector==='*')return all;
      if(selector==='[data-section-focus]')return all.filter(c=>c.dataset.sectionFocus);
      if(selector==='button:not(:disabled)')return all.filter(c=>c.tagName==='button'&&!c.disabled);
      return [];}
    querySelector(s){return this.querySelectorAll(s)[0]||null;}
  }
  document.body=new Node('body');document.activeElement=document.body;document.createElement=tag=>new Node(tag);
  const nodes={};for(const id of ['sidebarTabMenu','sidebarSectionsEditor','sidebarPagePanel','homeNavBtn','quickIcons']){nodes[id]=new Node();document.body.append(nodes[id]);}
  document.getElementById=id=>nodes[id];document.querySelector=()=>nodes.quickIcons;
  const tabs=['pages','chat','meetings'].map((mode,index)=>{const tab=index===0?nodes.homeNavBtn:new Node('button');tab.dataset.sidebarTab=mode;document.body.append(tab);return tab;});
  document.querySelectorAll=()=>tabs;
  const storage=new Map(),writes=[],requests=[];let key='owner/ws',mode='pages',sections=clone(original),failStorage=false;
  const api=create({document,window:{addEventListener(){}},storage:{getItem:k=>storage.get(k),setItem:(k,v)=>{if(failStorage)throw Error('denied');storage.set(k,v);writes.push([k,v]);}}});
  api.init({scope:()=>key,mode:()=>mode,labelKey:()=>key.split('/')[0],sections:()=>clone(sections),labels:{private:'개인 페이지',recents:'최근 항목'},locked:()=>false,
    closeMenus:()=>api.closeMenu(),place(){},saveSections:next=>new Promise(resolve=>requests.push({next,finish(ok=true){if(ok)sections=clone(next);resolve(ok);}}))});
  const button=text=>nodes.sidebarTabMenu.children.find(n=>n.tagName==='button'&&n.getAttribute('aria-label')===text);
  const custom=focus=>nodes.sidebarSectionsEditor.querySelectorAll('[data-section-focus]').find(n=>n.dataset.sectionFocus===focus);
  const start=()=>{api.activate(tabs[0]);button('섹션 사용자 지정').listeners.click();};
  return {api,document,nodes,tabs,storage,writes,requests,button,custom,start,sections:()=>sections,setScope:k=>key=k,setMode:m=>mode=m,failStorage:()=>failStorage=true};
}
test('UI-SIDEBAR-SETTINGS selected tab menus match their supported actions and Escape returns focus',()=>{
  const f=fixture();assert.equal(f.api.activate(f.tabs[1]),false);f.api.activate(f.tabs[0]);assert.ok(f.button('섹션 사용자 지정'));assert.ok(f.button('탭 이름 숨기기'));
  let stopped=0;f.nodes.sidebarTabMenu.listeners.keydown({key:'Escape',preventDefault(){},stopPropagation(){stopped++;}});
  assert.equal(stopped,1);assert.equal(f.document.activeElement,f.tabs[0]);assert.equal(f.tabs[0].getAttribute('aria-expanded'),'false');
  f.setMode('chat');f.api.activate(f.tabs[1]);assert.equal(f.button('섹션 사용자 지정'),undefined);assert.ok(f.button('탭 이름 숨기기'));
});
test('UI-SIDEBAR-SETTINGS label changes preserve accessible tab names and remain account scoped',()=>{
  const f=fixture();f.api.activate(f.tabs[0]);f.button('탭 이름 숨기기').listeners.click();
  assert.equal(f.storage.get('owner'),'hidden');assert.ok(f.nodes.quickIcons.classes.has('sidebar-tab-labels-hidden'));
  f.setMode('meetings');f.api.activate(f.tabs[2]);assert.ok(f.button('탭 이름 표시'));
  f.setScope('other/ws');f.api.sync();assert.equal(f.nodes.sidebarTabMenu.hidden,true);assert.equal(f.nodes.quickIcons.classes.has('sidebar-tab-labels-hidden'),false);
});
test('UI-SIDEBAR-SETTINGS storage failure leaves the visible label choice and provides feedback',()=>{
  const f=fixture();f.failStorage();f.api.activate(f.tabs[0]);f.button('탭 이름 숨기기').listeners.click();
  assert.equal(f.writes.length,0);assert.equal(f.nodes.sidebarTabMenu.hidden,false);assert.match(f.nodes.sidebarTabMenu.children.at(-1).textContent,/저장하지 못/);
});
test('UI-SIDEBAR-SETTINGS inline visibility and order preserve collapsed state and limits',async()=>{
  const f=fixture();f.start();assert.ok(f.nodes.sidebarPagePanel.classes.has('customizing-sections'));
  const pending=f.custom('visibility:recents').listeners.click();f.requests[0].finish();await pending;
  assert.equal(f.sections()[1].hidden,true);assert.equal(f.sections()[1].collapsed,true);assert.equal(f.sections()[1].item_limit,8);
  const move=f.custom('up:recents').listeners.click();f.requests[1].finish();await move;
  assert.deepEqual(f.sections().map(s=>[s.section,s.display_order]),[['recents',0],['private',1]]);
  f.custom('done').listeners.click();assert.equal(f.nodes.sidebarSectionsEditor.hidden,true);assert.equal(f.document.activeElement,f.tabs[0]);
});
test('UI-SIDEBAR-SETTINGS pending saves reject repeated actions and do not steal newly moved focus',async()=>{
  const f=fixture();f.start();const button=f.custom('visibility:recents'),pending=button.listeners.click();await button.listeners.click();assert.equal(f.requests.length,1);
  f.tabs[1].focus();f.requests[0].finish();await pending;assert.equal(f.document.activeElement,f.tabs[1]);
});
test('UI-SIDEBAR-SETTINGS an obsolete menu cannot write and late save completion cannot reopen another scope',async()=>{
  const f=fixture();f.api.activate(f.tabs[0]);const stale=f.button('탭 이름 숨기기');f.setScope('other/ws');stale.listeners.click();assert.equal(f.writes.length,0);
  f.start();const pending=f.custom('visibility:recents').listeners.click();f.setScope('third/ws');f.api.sync();f.tabs[2].focus();f.requests[0].finish(false);await pending;
  assert.equal(f.nodes.sidebarSectionsEditor.hidden,true);assert.equal(f.document.activeElement,f.tabs[2]);
});
test('UI-SIDEBAR-SETTINGS failed preference save keeps current settings and allows explicit retry',async()=>{
  const f=fixture();f.start();const pending=f.custom('visibility:recents').listeners.click();f.requests[0].finish(false);await pending;
  assert.deepEqual(f.sections(),original);assert.match(f.nodes.sidebarSectionsEditor.children.find(n=>n.getAttribute('role')==='status').textContent,/저장하지 못/);
  const retry=f.custom('visibility:recents').listeners.click();f.requests[1].finish();await retry;assert.equal(f.sections()[1].hidden,true);
});
test('UI-SIDEBAR-SETTINGS invalid changes and boundary moves do not create preference writes',()=>{
  for(const [key,action] of [['missing','visibility'],['private','up'],['recents','down'],['private','unknown']])assert.equal(changeSections(original,key,action),null);
  assert.equal(original[1].hidden,false);
});

test('UI-SIDEBAR-SETTINGS detached buttons from earlier renders cannot act in a reopened session',async()=>{
  const f=fixture();f.api.activate(f.tabs[0]);const stale=f.button('탭 이름 숨기기');f.api.closeMenu();f.api.activate(f.tabs[0]);stale.listeners.click();assert.equal(f.writes.length,0);assert.equal(f.nodes.sidebarTabMenu.hidden,false);
  f.api.closeMenu();f.start();const old=f.custom('visibility:recents'),pending=old.listeners.click();f.requests[0].finish();await pending;
  await old.listeners.click();assert.equal(f.requests.length,1);
});

test('UI-SIDEBAR-SETTINGS navigation read versions do not invalidate the active settings session',()=>{
  const fs=require('node:fs'),vm=require('node:vm'),source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');let host;
  const State={user:{id:'owner'},navigation:{workspaceId:'ws',sequence:1,enabled:true,collaborativeSharing:true}};
  const context={State,NAVIGATION_SECTION_LABELS:{},navigationControlsLocked:()=>false,closePageNavigationMenus(){},
    window:{NotionSidebarTabSettings:{init:options=>host=options}},document:{querySelectorAll:()=>[]}};
  vm.createContext(context);vm.runInContext(source.slice(source.indexOf('function bindSidebarTabs()'),source.indexOf('async function switchWorkspaceOwner(')),context);context.bindSidebarTabs();
  const scope=host.scope();State.navigation.sequence++;assert.equal(host.scope(),scope,'saving reloads the navigation snapshot');
  State.navigation.workspaceId='other';assert.notEqual(host.scope(),scope);
  State.navigation.workspaceId='ws';State.user.id='other';assert.notEqual(host.scope(),scope);
});

test('UI-SIDEBAR-SETTINGS ordinary workspace layout preserves older collapse preferences and isolates accounts',async()=>{
  const fs=require('node:fs'),vm=require('node:vm'),source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
  const State={user:{id:'owner'},sidebarMode:'pages',navigation:{workspaceId:'ws',enabled:false}};
  const stored=new Map([['notion.sidebarSections.owner.ws',JSON.stringify({private:true,recents:false})]]);
  let host,applied=0,denied=false;
  const context={State,NAVIGATION_SECTION_LABELS:{},navigationControlsLocked:()=>false,closePageNavigationMenus(){},
    applyNavigationSections(){applied++;},localStorage:{getItem:k=>stored.get(k),setItem:(k,v)=>{if(denied)throw Error('denied');stored.set(k,v);}},
    window:{NotionSidebarTabSettings:{init:options=>host=options}},document:{querySelectorAll:()=>[]}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function localSidebarSections()'),source.indexOf('const ExpandedRecentPages')),context);
  vm.runInContext(source.slice(source.indexOf('function bindSidebarTabs()'),source.indexOf('async function switchWorkspaceOwner(')),context);
  context.bindSidebarTabs();
  const before=clone(host.sections());assert.equal(before.find(s=>s.section==='private').collapsed,true);
  assert.equal(before.find(s=>s.section==='recents').collapsed,false);
  const next=changeSections(changeSections(before,'private','visibility'),'private','down');
  assert.equal(await host.saveSections(next),true);assert.equal(applied,1);
  const after=clone(host.sections());assert.equal(after.find(s=>s.section==='private').hidden,true);
  assert.equal(after.find(s=>s.section==='private').display_order,2);
  assert.equal(stored.get('notion.sidebarSections.owner.ws'),JSON.stringify({private:true,recents:false}));
  denied=true;assert.equal(await host.saveSections(before),false);assert.equal(applied,1);assert.deepEqual(clone(host.sections()),after);
  State.navigation.workspaceId='other';assert.equal(host.sections().find(s=>s.section==='private').hidden,false);
  State.navigation.workspaceId='ws';State.user.id='other';assert.equal(host.sections().find(s=>s.section==='private').hidden,false);
});

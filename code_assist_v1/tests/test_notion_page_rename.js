"use strict";
const assert = require("node:assert/strict");
const Rename = require("../notion_static/notion_page_rename.js");
const Bridge = require("../notion_static/notion_embedded_editor.js");
const clone = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture() {
  const data = {page:{id:"target",title:"Original",icon:"🚀"},blocks:[{id:"body",content:"Keep this body"}]};
  const state = {pageId:"target",userId:"owner",workspaceId:"ws",ready:true,canEdit:true,dirty:false};
  let ack = true, current = true, clock = 0, acquired = 0, disposed = 0, leaveCalls = 0, writes = 0;
  let gate = null, acquireGate = null, watcher = null;
  const saved = [], errors = [];
  const child = Bridge.createChild({
    state:()=>({...state}), snapshot:()=>clone(data),
    renameTitle(title) { data.page.title = title; state.dirty = true; writes++; },
    beforeLeave:async()=>{leaveCalls++;return false;},
    async save() { if (gate) await gate.promise; if (ack) state.dirty = false; },
  }, {now:()=>clock,wait:async ms=>{clock+=ms;}});
  const editor = {...child,dispose(){disposed++;}};
  const host = {
    current:()=>current,
    async acquire() {acquired++;if(acquireGate)await acquireGate.promise;return editor;},
    saved:value=>saved.push(clone(value)), onError:error=>errors.push(error),
    watch(callback) {watcher=callback;return()=>{watcher=null;};},
  };
  const session = Rename.createSession({pageId:"target",userId:"owner",workspaceId:"ws",title:"Original"},host);
  return {data,state,host,session,saved,errors,counts:()=>({acquired,disposed,leaveCalls,writes}),
    ack:value=>{ack=value;},scope:value=>{current=value;},gate:value=>{gate=value;},
    acquireGate:value=>{acquireGate=value;},observe:()=>watcher?.()};
}

function element() {
  const listeners = {};
  return {value:"",hidden:false,readOnly:false,textContent:"",listeners,
    addEventListener(name,fn){listeners[name]=fn;},setAttribute(name,value){this[name]=value;},
    focus(){this.focused=true;},select(){this.selected=true;},
    emit(name,props={}){const event={preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;},...props};listeners[name]?.(event);return event;}};
}

(async()=>{
  const f=fixture();
  await f.session.save("Original");assert.equal(f.counts().acquired,0,"unchanged close does not open an editor");
  await f.session.save("First");
  assert.equal(f.data.page.title,"First");assert.equal(f.data.page.icon,"🚀");
  assert.equal(f.data.blocks[0].content,"Keep this body");assert.equal(f.counts().leaveCalls,0,"rename never stops an ongoing activity");
  assert.equal(f.session.state().complete,true);assert.equal(f.counts().disposed,0,"keep the editor while typing continues");
  await f.session.save("Second");assert.equal(f.data.page.title,"Second");
  assert.equal(f.session.dismiss(),true);assert.equal(f.counts().disposed,1);

  const live=fixture(),gate=deferred();live.gate(gate);
  const first=live.session.save("One");await tick();
  const latest=live.session.save("One two");
  assert.equal(live.data.page.title,"One two","later input is visible before the previous ACK");
  assert.equal(latest,first);assert.equal(live.session.state().complete,false);
  assert.equal(live.session.dismiss(),false,"closing preserves an in-flight save");
  gate.resolve();await latest;
  assert.equal(live.saved.at(-1).page.title,"One two");assert.equal(live.counts().disposed,1);

  const loading=fixture(),load=deferred();loading.acquireGate(load);
  const loadingSave=loading.session.save("Earlier");await tick();
  loading.session.save("Latest");loading.session.dismiss();load.resolve();await loadingSave;
  assert.equal(loading.data.page.title,"Latest","Esc before editor startup still keeps the typed title");
  assert.equal(loading.counts().writes,1,"coalesce input while opening an offscreen editor");

  const failed=fixture();failed.ack(false);
  await assert.rejects(failed.session.save("Retained"),/완료되지/);
  assert.equal(failed.session.state().title,"Retained");assert.equal(failed.session.state().pending,true);
  assert.equal(failed.session.dismiss(),false);assert.equal(failed.counts().disposed,0);
  failed.state.dirty=false;failed.observe();
  assert.equal(failed.saved.at(-1).page.title,"Retained");assert.equal(failed.counts().disposed,1,"release only after the eventual ACK");

  const retry=fixture();retry.ack(false);
  await assert.rejects(retry.session.save("Pending"),/완료되지/);
  retry.ack(true);await retry.session.save("Pending");
  assert.equal(retry.counts().writes,1,"retry does not create another title edit");
  retry.session.dismiss();

  const conflict=fixture();await conflict.session.save("Local");
  conflict.data.page.title="Remote";
  await assert.rejects(conflict.session.save("Revised"),/다른 편집/);
  assert.equal(conflict.data.page.title,"Remote");assert.equal(conflict.session.state().title,"Revised");
  await conflict.session.save("Revised");assert.equal(conflict.data.page.title,"Revised");conflict.session.dismiss();

  for(const change of [
    f=>{f.state.canEdit=false;},f=>{f.state.blocked=true;},
    f=>{f.state.composing=true;},f=>{f.state.pageId="other";},
    f=>{f.state.userId="other";},f=>{f.state.workspaceId="other";},
  ]) {
    const denied=fixture();change(denied);
    await assert.rejects(denied.session.save("Denied"));assert.equal(denied.counts().writes,0);
    denied.session.dismiss();
  }
  const scope=fixture(),scopeGate=deferred();scope.acquireGate(scopeGate);
  const scoped=scope.session.save("Wrong workspace");await tick();scope.scope(false);scopeGate.resolve();
  await assert.rejects(scoped,/워크스페이스/);assert.equal(scope.counts().writes,0);scope.session.dismiss();
  const notification=fixture();notification.host.saved=()=>{throw Error("view notification failed");};
  await notification.session.save("Saved");assert.equal(notification.session.state().complete,true);
  assert.equal(notification.errors.length,1);notification.session.dismiss();

  const ui=fixture(),root=element(),input=element(),message=element(),button=element(),closed=[];
  root.querySelector=selector=>selector==="input"?input:selector==="button"?button:message;
  const view=Rename.mount(root,ui.session,{iconHtml:"<svg/>",close:restore=>closed.push(restore)});
  input.value="한";input.emit("compositionstart");input.emit("input");
  root.emit("keydown",{key:"Enter",isComposing:true,keyCode:229});
  assert.equal(ui.counts().writes,0);assert.equal(closed.length,0);
  input.value="한글 제목";input.emit("compositionend");await tick();
  assert.equal(ui.data.page.title,"한글 제목");assert.equal(closed.length,0,"save completion leaves the typing popover open");
  assert.equal(input.readOnly,false);
  const escape=root.emit("keydown",{key:"Escape"});
  assert.equal(escape.prevented,true);assert.equal(escape.stopped,true);assert.equal(closed.at(-1),true);
  assert.equal(ui.data.page.title,"한글 제목","Esc closes without reverting the title");
  view.dismissFromOutside();assert.equal(closed.at(-1),false,"outside click keeps the user's new focus");
  ui.data.page.title="동시 변경";
  input.value="보존할 입력";input.emit("input");await tick();
  assert.equal(ui.data.page.title,"동시 변경");
  root.emit("keydown",{key:"Escape"});await tick();
  assert.equal(ui.data.page.title,"동시 변경","closing a failed popover must not retry and overwrite a concurrent title");
  assert.equal(input.value,"보존할 입력");
  assert.equal(button.hidden,false);
  view.dispose();ui.session.dismiss();
  console.log("UI-RENAME-001: live title input, ACK wait, close, latest input, retry, conflict, scope, body preservation and IME passed");
})().catch(error=>{console.error(error);process.exitCode=1;});

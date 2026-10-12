"use strict";
const assert = require("node:assert/strict");
const Bridge = require("../notion_static/notion_embedded_editor.js");
const clone = value => JSON.parse(JSON.stringify(value));

function fixture() {
  const state = {pageId: "child", ready: true, canEdit: true, dirty: false, composing: false};
  let data = {page: {id: "child", title: "첫 제목"}, blocks: [{id: "blank", type: "paragraph", content: "", props: {}}]};
  let elapsed = 0, saves = 0, leave = true, applyCount = 0;
  const host = {
    state: () => ({...state}), snapshot: () => clone(data), focus() {},
    apply(next) { applyCount++; data = clone(next); state.dirty = true; },
    save: async () => { saves++; }, beforeLeave: async () => leave,
  };
  const child = Bridge.createChild(host, {now: () => elapsed, wait: async ms => {
    elapsed += ms;
    if (elapsed >= 200) { state.dirty = false; state.composing = false; }
  }});
  return {state, host, child, data: () => data, saves: () => saves, applied: () => applyCount,
    denyLeave() { leave = false; }};
}

(async () => {
  const f = fixture();
  const draft = {page: {id: "child", title: "추가 입력"}, blocks: [{id: "body", type: "paragraph", content: "최신 본문", props: {}}]};
  f.state.composing = true;
  assert.throws(() => f.child.applyInitial(draft, {pageId: "child", title: "첫 제목"}), /준비/);
  f.state.composing = false;
  f.child.applyInitial(draft, {pageId: "child", title: "첫 제목"});
  draft.blocks[0].content = "parent changed after transfer";
  assert.equal(f.data().blocks[0].content, "최신 본문", "handoff owns a detached snapshot");
  assert.throws(() => f.child.applyInitial(draft, {pageId: "child", title: "첫 제목"}), /이미/);
  const saved = await f.child.flush(1000);
  assert.equal(f.saves(), 3, "local save completion alone cannot close an unacknowledged editor");
  assert.equal(saved.page.title, "추가 입력");

  for (const mutation of [
    data => { data.page.title = "다른 사람의 제목"; },
    data => { data.blocks[0].content = "다른 사람의 본문"; },
    data => { data.blocks[0].props.rich_text_html = "<strong>다른 내용</strong>"; },
  ]) {
    const changed = fixture(); mutation(changed.data());
    assert.throws(() => changed.child.applyInitial(draft, {pageId: "child", title: "첫 제목"}), /다른 변경/);
    assert.equal(changed.applied(), 0);
  }
  const wrong = fixture();
  assert.throws(() => wrong.child.applyInitial(draft, {pageId: "other", title: "첫 제목"}), /다른 변경/);
  wrong.state.canEdit = false;
  assert.throws(() => wrong.child.applyInitial(draft, {pageId: "child", title: "첫 제목"}), /준비/);
  const blocked = fixture(); blocked.state.blocked = true; blocked.state.message = "권한 회수";
  await assert.rejects(blocked.child.flush(), /권한 회수/);
  const pending = fixture(); pending.state.dirty = true;
  await assert.rejects(pending.child.flush(100), /완료되지/);
  assert.equal(pending.state.dirty, true, "timeout retains pending edits");
  let finishSlowSave, slowSaves = 0;
  const slowState = {pageId:"child",ready:true,canEdit:true,dirty:true,composing:false};
  const slow = Bridge.createChild({state:()=>({...slowState}),
    snapshot:()=>({page:{id:"child",title:"보존할 제목"},blocks:[{content:"보존할 본문"}]}),
    beforeLeave:async()=>true,
    save:()=>{slowSaves++;return new Promise(resolve=>{finishSlowSave=resolve;});}});
  await assert.rejects(slow.flush(25), /완료되지/);
  assert.equal(slowSaves,1,"a stalled save must reach the deadline without dropping the draft");
  const retry=slow.flush(200);
  await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(slowSaves,1,"retry must wait for the already-running save");
  slowState.dirty=false;
  finishSlowSave();
  assert.equal((await retry).page.title,"보존할 제목");
  assert.equal(slowSaves,1);
  let finishLeave, leaveCalls=0, deferredSaves=0;
  const deferredLeave = Bridge.createChild({state:()=>({pageId:"child",ready:true,dirty:false,composing:false}),
    snapshot:()=>({page:{id:"child",title:"이탈 보류"},blocks:[]}),
    beforeLeave:()=>{leaveCalls++;return new Promise(resolve=>{finishLeave=resolve;});},
    save:async()=>{deferredSaves++;}});
  await assert.rejects(deferredLeave.flush(25), /완료되지/);
  assert.equal(deferredSaves,0,"save must not run while a leave guard is pending");
  const leaveRetry=deferredLeave.flush(200);
  await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(leaveCalls,1,"retry must reuse the pending leave guard");
  finishLeave(true);
  assert.equal((await leaveRetry).page.title,"이탈 보류");
  assert.equal(deferredSaves,1);
  const recording = fixture(); recording.denyLeave();
  await assert.rejects(recording.child.flush(), /진행 중인 작업/);
  assert.equal(recording.saves(), 0);
  const ime = fixture(); ime.state.composing = true;
  await ime.child.flush(500);
  assert.equal(ime.saves(), 3, "composition must finish even when the current snapshot is saved");

  let parentClosed = 0, removed = false;
  const attached = fixture();
  const frame = {isConnected: true, contentWindow: {NotionEmbeddedEditor: attached.child}, remove() { removed = true; this.isConnected = false; }};
  const connection = await Bridge.connect(frame, "child", {change() {}, close() { parentClosed++; }});
  attached.child.close(); assert.equal(parentClosed, 1);
  connection.dispose(); attached.child.close();
  assert.equal(parentClosed, 1); assert.equal(removed, true);
  await assert.rejects(Bridge.connect(frame, "child", {}), /닫혔습니다/);
  const otherAccount = {isConnected: true, contentWindow: {NotionEmbeddedEditor: fixture().child}};
  await assert.rejects(Bridge.connect(otherAccount, "child", {}, {userId: "expected-owner"}), /계정과 워크스페이스/);
  const moved = fixture(); moved.state.userId='owner'; moved.state.workspaceId='workspace';
  let refreshed=0;
  moved.host.refreshAfterMove=async epoch=>{refreshed=epoch;};
  const expected={userId:'owner',workspaceId:'workspace',pageId:'child',workspaceEpoch:8};
  await moved.child.refreshAfterMove(expected); assert.equal(refreshed,8);
  moved.state.pageId='other';
  await assert.rejects(moved.child.refreshAfterMove({...expected,workspaceEpoch:9}),/달라졌습니다/);
  assert.equal(refreshed,8,'a stale parent request must not reconnect another child page');
  console.log("UI-DRAFT-003: editor handoff, server-ack wait, IME, conflicting content, permissions and disposal passed");
})().catch(error => {console.error(error); process.exitCode = 1;});

"use strict";
const assert = require("node:assert/strict"), {test} = require("node:test");
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const transport = require("../notion_static/notion_ai_chat_transport.js");
const tick = () => new Promise(resolve => setImmediate(resolve));
const frame = event => "data: " + JSON.stringify(event) + "\r\n\r\n";
const conversation = {id: "chat", revision: 1, messages: [{role: "assistant", text: "한글 😀"}]};

function reopenFixture(read) {
  const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion_ai_home.js"), "utf8");
  const nodes = new Map(), paints = [];
  const context = {
    $: id => {if (!nodes.has(id)) nodes.set(id, {setAttribute(){}}); return nodes.get(id);},
    host: {enter:async()=>true, api:read, workspaceId:()=>"", route(){}},
    clearStream(){}, clearTimeout(){}, status(){}, pageChoices(){},
    capabilities:async()=>{}, history:async()=>{}, schedulePoll(){},
    render:()=>paints.push(context.mode), modeHelp:{chat:"",slides:"",spreadsheet:"",research:"",visualization:""},
    serial:0, timer:null, mode:"chat", scope:{}, conversation:null, visible:false,
  };
  context.$("aiHomeInput").focus = ()=>{};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("  function savedMode("), source.indexOf("  async function send()")), context);
  return {context, paints};
}

test("opening a saved artifact restores its latest assistant mode; new and legacy chats retain the chat default", async()=>{
  let record;
  const h = reopenFixture(async()=>record);
  for (const mode of ["slides","spreadsheet","research","visualization","chat"]) {
    record = {scope:{workspace_id:"",page_ids:[]},messages:[{role:"assistant",mode:"slides"},{role:"assistant",mode},{role:"user",mode:"chat"}]};
    await h.context.open("saved");
    assert.equal(h.context.mode,mode); assert.equal(h.paints.at(-1),mode);
  }
  await h.context.open(""); assert.equal(h.context.mode,"chat");
  record.messages = [{role:"assistant"}]; await h.context.open("legacy"); assert.equal(h.context.mode,"chat");
  record.messages = [{role:"assistant",mode:"__proto__"}]; await h.context.open("invalid"); assert.equal(h.context.mode,"chat");
});

test("a late saved-conversation response cannot restore another conversation's mode", async()=>{
  let finishOld;
  const h = reopenFixture(async path => path.endsWith("old") ? new Promise(resolve=>{finishOld=resolve;}) :
    {scope:{workspace_id:"",page_ids:[]},messages:[{role:"assistant",mode:"visualization"}]});
  const old = h.context.open("old"); await tick();
  await h.context.open("new");
  finishOld({scope:{workspace_id:"",page_ids:[]},messages:[{role:"assistant",mode:"slides"}]});
  await old;
  assert.equal(h.context.cid,"new"); assert.equal(h.context.mode,"visualization");
});

test("reopened conversations restore per-turn sources and new conversations turn web off", async()=>{
  const h = reopenFixture(async()=>({scope:{workspace_id:"",page_ids:[]},messages:[{role:"assistant",mode:"research",context_info:{source_options:{notion:false,web:true}}}]}));
  await h.context.open("saved");
  assert.equal(h.context.sourceOptions.web,true); assert.equal(h.context.sourceOptions.notion,false);
  await h.context.open("");
  assert.equal(h.context.sourceOptions.web,false); assert.equal(h.context.sourceOptions.notion,true);
});

test("refresh restores a pending deep research job before its first assistant message", async()=>{
  const h = reopenFixture(async()=>({scope:{workspace_id:"",page_ids:[]}, messages:[], pending:true,
    research_job:{state:"queued",source_options:{notion:false,web:true}}}));
  await h.context.open("saved-job");
  assert.equal(h.context.mode,"research");
  assert.equal(h.context.sourceOptions.web,true); assert.equal(h.context.sourceOptions.notion,false);
});

test("real incoming UTF-8 chunks are delivered before the terminal conversation", async () => {
  let controller, cancelled = false, settled = false;
  const events = [], encoder = new TextEncoder();
  const stream = new ReadableStream({start(c) {controller = c;}, cancel() {cancelled = true;}});
  const response = new Response(stream, {headers: {"Content-Type": "text/event-stream; charset=utf-8"}});
  const promise = transport.send({fetch: async (url, options) => {
    assert.equal(url, "/messages/stream"); assert.equal(options.method, "POST");
    assert.equal(JSON.parse(options.body).message, "question"); return response;
  }, path: "/messages/stream", body: {message: "question"}, onEvent: e => events.push(e)});
  promise.then(() => {settled = true;});
  const bytes = encoder.encode(frame({type: "delta", text: "한글 😀"}));
  // Split inside every Unicode code point and the CRLF frame separators.
  for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
  await tick();
  assert.equal(settled, false); assert.deepEqual(events, [{type: "delta", text: "한글 😀"}]);
  controller.enqueue(encoder.encode(frame({type: "done", conversation})));
  assert.deepEqual(await promise, conversation); assert.equal(cancelled, true);
});

test("server stream errors preserve the correct status and discard partial completion", async () => {
  const text = frame({type: "delta", text: "partial"}) + frame({type: "error", status: 409,
    code: "chat_conflict", message: "참고 페이지가 바뀌었습니다."});
  await assert.rejects(transport.send({fetch: async () => new Response(text,
    {headers: {"Content-Type": "text/event-stream"}}), path: "/stream", body: {}}),
    error => error.status === 409 && error.code === "chat_conflict");
});

test("lost terminal event keeps request replay possible instead of declaring a server failure", async () => {
  await assert.rejects(transport.send({fetch: async () => new Response(frame({type: "delta", text: "partial"}),
    {headers: {"Content-Type": "text/event-stream"}}), path: "/stream", body: {}}),
    error => error.status === undefined && error.message.includes("끊겼"));
});

test("HTTP authentication failures retain status and do not start a stream", async () => {
  await assert.rejects(transport.send({fetch: async () => Response.json({message: "로그인 필요"}, {status: 401}),
    path: "/stream", body: {}}), error => error.status === 401);
});

function uiFixture() {
  const nodes = new Map();
  const make = (tag = "div", text) => ({tagName: tag.toUpperCase(), textContent: text || "", dataset: {},
    children: [], value: "", scrollTop: 0, scrollHeight: 500, clientHeight: 500,
    append(...items) {for (const item of items) {item.parent = this; this.children.push(item);}},
    replaceChildren(...items) {this.children = []; this.append(...items);},
    replaceWith(next) {const i = this.parent.children.indexOf(this); this.parent.children[i] = next; next.parent = this.parent;},
    after(next) {const i = this.parent.children.indexOf(this); this.parent.children.splice(i+1,0,next); next.parent=this.parent;},
    setAttribute() {}, querySelectorAll() {return [];}, querySelector() {return null;}});
  const $ = id => {if (!nodes.has(id)) nodes.set(id, make()); return nodes.get(id);};
  const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion_ai_home.js"), "utf8");
  const part = (a, b) => source.slice(source.indexOf(a), source.indexOf(b, source.indexOf(a)));
  let resolve, callbacks, signal, key = "owner";
  const context = {$, node: make, AbortController, setTimeout, clearTimeout, cancelAnimationFrame() {},
    requestAnimationFrame: fn => {setImmediate(fn); return 1;}, uuid: () => "operation",
    answerBody: text => make("div", text), status: text => {$("status").textContent = text;},
    controls() {}, render: () => $("aiHomeMessages").replaceChildren(), history: async () => {}, schedulePoll() {},
    window: {NotionAiStream: {send: options => {callbacks = options.onEvent; signal = options.signal;
      return new Promise(r => {resolve = r;});}}},
    host: {key: () => key, route() {}, leave() {}, fetch() {}},
    models: [{id: "qwen"}], busy: false, visible: true, serial: 1, timer: null, mode: "chat", cid: "chat",
    sourceOptions:{notion:true,web:false},
    conversation: {id: "chat", revision: 0, messages: []}, pending: null,
    streamingEnabled: true, streamController: null, streamDraft: null, streamPaint: null};
  vm.createContext(context);
  vm.runInContext(part("  function clearStream()", "  function schedulePoll(")
    + part("  async function send()", "  window.NotionAiHome ="), context);
  $("aiHomeInput").value = "무슨 내용들이 정리되어있어?";
  return {context, $, delta: text => callbacks({type: "delta", text}),
    progress: event => callbacks({type:"research_progress",...event}),
    finish: () => resolve(conversation), signal: () => signal, changeAccount: () => {key = "someone-else";}};
}

test("AI home paints deltas while the request is pending and commits only the final response", async () => {
  const h = uiFixture(), task = h.context.send();
  h.delta("한글"); await tick();
  assert.equal(h.context.streamDraft.body.textContent, "한글");
  assert.equal(h.context.conversation.revision, 0);
  assert.equal(h.$("aiHomeMessages").children.length, 2);
  assert.equal(h.$("aiHomeMessages").children[1].children.length, 2); // No provisional download/copy actions.
  h.finish(); await task;
  assert.equal(h.context.conversation.revision, 1);
  assert.equal(h.$("aiHomeInput").value, "");
});

test("leaving the chat aborts the provider request and ignores stale account deltas", async () => {
  const h = uiFixture(), task = h.context.send();
  h.delta("first"); await tick();
  h.context.leave(); h.changeAccount();
  assert.equal(h.signal().aborted, true);
  h.delta("private-old-owner-data"); h.finish(); await task;
  assert.equal(h.context.streamDraft, null);
  assert.equal(h.context.conversation.revision, 0);
});

test("research progress appears before streamed text and old-account progress is ignored", async()=>{
  const h=uiFixture(), task=h.context.send();
  h.progress({stage:"web",label:"웹에서 검색하고 있습니다.",query:"GAT original paper"});
  assert.equal(h.context.streamDraft.progress.children[1].textContent,"GAT original paper");
  assert.ok(h.context.streamDraft.label.textContent.includes("웹에서 검색"));
  h.delta("보고서 첫 문장"); await tick();
  assert.equal(h.context.streamDraft.body.textContent,"보고서 첫 문장");
  h.context.leave(); h.changeAccount();
  h.progress({stage:"read",label:"이전 계정 자료",title:"private"});
  h.finish(); await task;
  assert.equal(h.context.streamDraft,null);
});

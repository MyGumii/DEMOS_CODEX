"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const history = source.slice(source.indexOf("let PageHistoryRequest ="),
  source.indexOf("async function restorePageVersion("));
assert.ok(history.startsWith("let PageHistoryRequest ="));
const restore = source.slice(source.indexOf("async function restorePageVersion("),
  source.indexOf("async function duplicatePage("));
assert.ok(restore.startsWith("async function restorePageVersion("));

function setup(api) {
  const root = {innerHTML:"", querySelectorAll:() => []};
  const State = {
    activePageId:"first", drawerMode:"history", versions:[],
    user:{id:"me"}, navigation:{workspaceId:"personal",collaborativeHistory:false},
  };
  const context = {
    State, $:selector => selector === "#historyList" ? root : null, api,
    escapeHtml:value => String(value).replace(/[&<>"]/g, char => ({
      "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;",
    })[char]),
    escapeAttr:value => String(value), formatTime:value => `time ${value}`,
    formatBytes:value => `${value} B`,
  };
  vm.createContext(context);
  vm.runInContext(history, context);
  return {root, State, context};
}

test("personal version history ignores an older page response", async () => {
  let resolveOld;
  const urls = [];
  const {root, State, context} = setup(url => {
    urls.push(url);
    return urls.length === 1 ? new Promise(resolve => {resolveOld = resolve;})
      : Promise.resolve({items:[{id:"new-version",title:"New page",created_at:12}]});
  });
  const old = context.loadPageHistory();
  State.activePageId = "new";
  root.innerHTML = "new page loading";
  resolveOld({items:[{id:"old-version",title:"Old page",created_at:11}]});
  await old;
  assert.equal(root.innerHTML, "new page loading");
  assert.equal(State.versions.length, 0);
  await context.loadPageHistory();
  assert.match(root.innerHTML, /New page/);
  assert.doesNotMatch(root.innerHTML, /Old page/);
  assert.match(urls[1], /\/pages\/new\/versions$/);
});

test("collaborative history ignores a failed request from another workspace", async () => {
  let rejectOld;
  const urls = [];
  const {root, State, context} = setup(url => {
    urls.push(url);
    return urls.length === 1 ? new Promise((_, reject) => {rejectOld = reject;})
      : Promise.resolve({items:[{revision:8,created_at:22}],current_revision:8});
  });
  State.navigation = {workspaceId:"workspace-a",collaborativeHistory:true};
  const old = context.loadPageHistory();
  State.navigation.workspaceId = "workspace-b";
  root.innerHTML = "new workspace loading";
  rejectOld(new Error("old workspace unavailable"));
  await old;
  assert.equal(root.innerHTML, "new workspace loading");
  await context.loadPageHistory();
  assert.match(root.innerHTML, /버전 8/);
  assert.doesNotMatch(root.innerHTML, /old workspace unavailable/);
  assert.match(urls[1], /\/workspaces\/workspace-b\/pages\/first\/history\?limit=100$/);
});

test("history response cannot refill the panel after it closes", async () => {
  let resolveOld;
  const {root, State, context} = setup(() =>
    new Promise(resolve => {resolveOld = resolve;}));
  const old = context.loadPageHistory();
  State.drawerMode = "";
  root.innerHTML = "closed";
  resolveOld({items:[{id:"old-version",title:"Old page",created_at:11}]});
  await old;
  assert.equal(root.innerHTML, "closed");
  assert.equal(State.versions.length, 0);
});

test("a history response cannot cross from personal to collaborative mode", async () => {
  let resolveOld;
  const {root, State, context} = setup(() =>
    new Promise(resolve => {resolveOld = resolve;}));
  const old = context.loadPageHistory();
  State.navigation.collaborativeHistory = true;
  root.innerHTML = "collaborative page loading";
  resolveOld({items:[{id:"personal-version",title:"Private title",created_at:11}]});
  await old;
  assert.equal(root.innerHTML, "collaborative page loading");
  assert.equal(State.versions.length, 0);
});

function setupRestore({collaborative = false, flush, api}) {
  const events = [];
  const State = {
    activePageId:"first", drawerMode:"history", user:{id:"me"},
    navigation:{workspaceId:"workspace-a",sequence:3,collaborativeHistory:collaborative},
    pageData:{page:{revision:7,document_sequence:9,workspace_epoch:2,document_epoch:4}},
    serverRevision:7,
  };
  const context = {
    State, PageHistoryRequest:0, window:{confirm:() => true}, flushAllOpenDrafts:flush, api,
    newOperationId:() => "restore-operation", PageFocusPolicy:{PRESERVE:"preserve"},
    stopLiveCollaboration:() => events.push("stop"),
    openPage:async () => {events.push("open");},
    refreshOpenPagesAfterMutation:async () => {events.push("refresh");},
    loadPages:async () => {events.push("pages");},
    loadPageHistory:async () => {events.push("history");},
    setStatus:message => events.push(message),
  };
  vm.createContext(context);
  vm.runInContext(restore, context);
  return {State, context, events};
}

test("personal restore does not send a request after navigation during draft flush", async () => {
  let finishFlush;
  const calls = [];
  const {State, context, events} = setupRestore({
    flush:() => new Promise(resolve => {finishFlush = resolve;}),
    api:url => {calls.push(url); return Promise.resolve({});},
  });
  const pending = context.restorePageVersion("version-1");
  State.activePageId = "second";
  State.navigation.sequence += 1;
  finishFlush(true);
  await pending;
  assert.deepEqual(calls, []);
  assert.deepEqual(events, []);
});

test("collaborative restore response cannot reset another page's editor", async () => {
  let finishRequest;
  const calls = [];
  const {State, context, events} = setupRestore({
    collaborative:true,
    flush:async () => true,
    api:url => {calls.push(url); return new Promise(resolve => {finishRequest = resolve;});},
  });
  const pending = context.restorePageVersion("5");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length, 1);
  assert.match(calls[0], /\/workspaces\/workspace-a\/pages\/first\/history\/revisions\/5\/restore$/);
  State.activePageId = "second";
  State.navigation.sequence += 1;
  finishRequest({});
  await pending;
  assert.deepEqual(events, []);
});

test("reopening the same history panel does not revive an old restore response", async () => {
  let finishRequest;
  const {context, events} = setupRestore({
    collaborative:true, flush:async () => true,
    api:() => new Promise(resolve => {finishRequest = resolve;}),
  });
  const pending = context.restorePageVersion("5");
  await new Promise(resolve => setImmediate(resolve));
  context.PageHistoryRequest += 1;
  finishRequest({});
  await pending;
  assert.deepEqual(events, []);
});

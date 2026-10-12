"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const meetingsSource = fs.readFileSync(path.join(__dirname, "../notion_static/notion_meetings.js"), "utf8");
const mainSource = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}
function nodes() {
  const all = new Map();
  const make = () => ({hidden: true, disabled: false, children: [], textContent: "", listeners: {},
    addEventListener(type, fn) { this.listeners[type] = fn; },
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; }, focus() {},
  });
  return {get(id) {if (!all.has(id)) all.set(id, make()); return all.get(id);}, make};
}
function fixture() {
  const dom = nodes(), timers = new Map(), requests = [], opens = [], routes = [];
  let timerId = 0, key = "workspace-a";
  const context = {module: {exports: {}}, document: {getElementById: dom.get, createElement: dom.make},
    setTimeout(fn) {timers.set(++timerId, fn); return timerId;}, clearTimeout(id) {timers.delete(id);}};
  vm.runInNewContext(meetingsSource, context);
  const meetings = context.module.exports();
  meetings.init({key: () => key, enter: async () => true, canCreate: () => false,
    route: push => routes.push(push), create: async () => {}, icon: () => "", relative: () => "now",
    openPage: item => opens.push(item.id),
    list: request => {const pending = deferred(); requests.push({...pending, request}); return pending.promise;},
  });
  return {dom, meetings, requests, opens, routes, timers,
    workspace(value) {key = value;},
    search(value) {dom.get("meetingSearch").listeners.input({target: {value}});},
    async tick() {const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn()); await new Promise(setImmediate);},
    async start() {
      const opened = meetings.open(); await new Promise(setImmediate);
      requests[0].resolve({items: [{id: "old-1", title: "Old", note_count: 1}], count: 2, has_more: true});
      await opened;
    },
  };
}

test("UI-NAV-004 search changes discard prior rows and cannot paginate before the first result", async () => {
  const f = fixture(); await f.start();
  f.search("new");
  assert.equal(f.dom.get("meetingList").children.length, 0);
  assert.equal(f.dom.get("meetingListMore").hidden, true);
  await f.dom.get("meetingListMore").listeners.click();
  assert.equal(f.requests.length, 1);
  await f.tick();
  assert.deepEqual({...f.requests[1].request}, {query: "new", offset: 0});
  f.requests[1].resolve({items: [{id: "new-1", note_count: 1}], count: 1, has_more: false});
  await new Promise(setImmediate);
  assert.equal(f.dom.get("meetingList").children.length, 1);
});

test("UI-NAV-004 manual refresh consumes a pending search instead of firing a second reset", async () => {
  const f = fixture(); await f.start(); f.search("new");
  const refreshed = f.dom.get("meetingRefreshBtn").listeners.click();
  assert.equal(f.requests.length, 2);
  f.requests[1].resolve({items: [{id: "new-1", note_count: 1}], count: 1, has_more: false});
  await refreshed; await f.tick();
  assert.equal(f.requests.length, 2);
  assert.equal(f.dom.get("meetingList").children.length, 1);
});

test("UI-NAV-004 leave clears cached rows and ignores detached controls", async () => {
  const f = fixture(); await f.start();
  const oldRow = f.dom.get("meetingList").children[0];
  f.meetings.leave();
  assert.equal(f.dom.get("meetingList").children.length, 0);
  await oldRow.listeners.click();
  await f.dom.get("meetingRefreshBtn").listeners.click();
  assert.deepEqual(f.opens, []);
  assert.equal(f.requests.length, 1);
});

test("UI-NAV-004 an old workspace row cannot initiate navigation in a new workspace", async () => {
  const f = fixture(); await f.start(); f.workspace("workspace-b");
  await f.dom.get("meetingList").children[0].listeners.click();
  assert.deepEqual(f.opens, []);
});

test("UI-NAV-004 pagination runs once per page and appends only to its matching query", async () => {
  const f = fixture(); await f.start();
  const more = f.dom.get("meetingListMore").listeners.click();
  const duplicate = f.dom.get("meetingListMore").listeners.click();
  assert.equal(f.requests.length, 2);
  f.search("replacement");
  f.requests[1].resolve({items: [{id: "old-2"}], count: 2, has_more: false});
  await more; await duplicate; await f.tick();
  assert.deepEqual({...f.requests[2].request}, {query: "replacement", offset: 0});
  assert.equal(f.dom.get("meetingList").children.length, 0);
});

test("UI-NAV-004 ordinary pagination retains rows and uses the next offset", async () => {
  const f = fixture(); await f.start();
  const more = f.dom.get("meetingListMore").listeners.click();
  assert.deepEqual({...f.requests[1].request}, {query: "", offset: 1});
  f.requests[1].resolve({items: [{id: "old-2", note_count: 1}], count: 2, has_more: false});
  await more;
  assert.equal(f.dom.get("meetingList").children.length, 2);
  assert.equal(f.dom.get("meetingListMore").hidden, true);
  assert.equal(f.dom.get("meetingListMore").disabled, false);
});

function chatHost(allowed) {
  const dom = nodes(), classes = new Set(), nav = [];
  const State = {activePageId: "", navigation: {workspaceId: ""}, user: {id: "owner"}};
  const context = {State, $: dom.get, document: {body: {classList: {
    add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value),
  }}}, goHome: async () => allowed, api() {}, authenticatedFetch() {}, visiblePages: () => [],
    switchSidebarMode: mode => nav.push(mode), history: {pushState() {}, replaceState() {}}};
  vm.runInNewContext(mainSource.slice(mainSource.indexOf("function aiHomeHost()"), mainSource.indexOf("let DrawerReturnFocus")), context);
  return {host: context.aiHomeHost(), dom, nav, classes, State};
}

test("UI-DRAFT-007 chat entry respects failed navigation even when the main page is empty", async () => {
  const f = chatHost(false);
  assert.equal(await f.host.enter(), false, "an unsaved side-peek on home must be allowed to prevent navigation");
  assert.equal(f.dom.get("#aiHomeView").hidden, true);
  assert.deepEqual(f.nav, []);
  assert.equal(f.classes.has("ai-home-open"), false);
});

test("UI-DRAFT-007 successful chat entry still opens the view and selects its navigation", async () => {
  const f = chatHost(true);
  assert.equal(await f.host.enter(), true);
  assert.equal(f.dom.get("#aiHomeView").hidden, false);
  assert.deepEqual(f.nav, ["chat"]);
});

for (const pending of ["composing", "dirty"]) {
  test(`UI-DRAFT-007 actual home and side-peek navigation preserve a ${pending} draft`, async () => {
    const dom = nodes(), classes = new Set();
    const draft = {draft: {}, composing: pending === "composing", dirty: pending === "dirty",
      data: {page: {title: "저장 전 초안"}, blocks: [{content: "입력 보존"}]}};
    const State = {activePageId: "", pageLoadSequence: 0, sidePeek: draft, navigation: {workspaceId: ""}};
    const context = {State, $: dom.get, LinkedDatabaseItems: null, EmbeddedEditorFrame: false,
      window: {NotionAiNotes: {beforeNavigate: async () => true}},
      document: {body: {classList: {
        contains: value => classes.has(value), add: value => classes.add(value), remove: value => classes.delete(value),
      }}}, api() {}, authenticatedFetch() {}, visiblePages: () => [],
      saveSidePeek: async () => {}, setStatus() {}, setActiveNav() {},
      flushIndependentDatabaseItemBodyBeforeNavigate: async () => true,
      confirmSelectedCommentDraftLeave: async () => true,
      emptyReservedSidePeek: () => false,
    };
    const slice = (start, end) => mainSource.slice(mainSource.indexOf(start), mainSource.indexOf(end, mainSource.indexOf(start)));
    vm.runInNewContext([
      slice("async function goHome(", "function cardHtml("),
      slice("async function closeSidePeek(", "function resolveSidePeekDatabaseContext("),
      slice("function aiHomeHost()", "let DrawerReturnFocus"),
    ].join("\n"), context);
    assert.equal(await context.aiHomeHost().enter(), false);
    assert.equal(State.sidePeek, draft);
    assert.equal(draft.data.blocks[0].content, "입력 보존");
    assert.equal(dom.get("#aiHomeView").hidden, true);
    assert.equal(classes.has("ai-home-open"), false);
  });
}

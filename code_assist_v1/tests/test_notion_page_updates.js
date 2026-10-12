"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const updates = source.slice(source.indexOf("let PageUpdatesRequest ="), source.indexOf("async function loadPageHistory()"));
assert.ok(updates.startsWith("let PageUpdatesRequest ="));

function setup(api) {
  const root = {innerHTML:"", insertAdjacentHTML:(_position,html) => {root.innerHTML += html;},
    querySelectorAll:() => []};
  const more = {hidden:true,disabled:false};
  const State = {
    activePageId:"first", drawerMode:"page-updates",
    user:{id:"me",display_name:"Me"}, users:[],
    navigation:{workspaceId:"personal",collaborativeHistory:false},
    pageData:{page:{title:"Current page"}},
  };
  const context = {
    State, $:selector => ({"#pageUpdatesList":root,"#pageUpdatesMoreBtn":more})[selector] || null, api,
    escapeHtml:value => String(value).replace(/[&<>"']/g, char => ({
      "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;",
    })[char]),
    escapeAttr:value => String(value).replace(/"/g,"&quot;"),
    formatTime:value => `time ${value}`,
    formatRelative:value => `relative ${value}`,
    pageUrl:id => `/page/${id}`,
    iconSvg:() => "<svg></svg>",
  };
  vm.createContext(context);
  vm.runInContext(updates, context);
  return {root, more, State, context};
}

test("page updates use the current personal page and discard an older response after navigation", async () => {
  let resolveOld;
  const calls = [];
  const {root,State,context} = setup(url => {
    calls.push(url);
    return calls.length === 1 ? new Promise(resolve => {resolveOld = resolve;})
      : Promise.resolve({items:[{id:"version-new",title:"New page",created_at:12,
        preview:{items:[{kind:"edited",text:"Changed paragraph"}],remaining:0}}]});
  });
  const oldRequest = context.loadPageUpdates();
  State.activePageId = "new";
  root.innerHTML = "new page loading";
  resolveOld({items:[{id:"version-old",title:"Old page",created_at:11}]});
  await oldRequest;
  assert.equal(root.innerHTML,"new page loading");
  await context.loadPageUpdates();
  assert.match(root.innerHTML,/New page/);
  assert.doesNotMatch(root.innerHTML,/Old page/);
  assert.match(root.innerHTML,/Changed paragraph/);
  assert.match(calls[1],/\/pages\/new\/updates\?limit=20$/);
});

test("collaborative updates respect the workspace and escape actor names", async () => {
  let resolveOld;
  const calls = [];
  const {root,State,context} = setup(url => {
    calls.push(url);
    return calls.length === 1 ? new Promise(resolve => {resolveOld = resolve;})
      : Promise.resolve({items:[{revision:7,actor_subject_id:"other",created_at:22,
        restored_from_revision:3,preview:{items:[{kind:"added",text:"<New block>"}],remaining:0}}]});
  });
  State.navigation = {workspaceId:"workspace-a",collaborativeHistory:true};
  State.users = [{id:"other",display_name:"<Reader>"}];
  const oldRequest = context.loadPageUpdates();
  State.navigation.workspaceId = "workspace-b";
  root.innerHTML = "new workspace loading";
  resolveOld({items:[{revision:6,actor_subject_id:"me",created_at:21}]});
  await oldRequest;
  assert.equal(root.innerHTML,"new workspace loading");
  await context.loadPageUpdates();
  assert.match(calls[1],/\/workspaces\/workspace-b\/pages\/first\/updates\?limit=20$/);
  assert.match(root.innerHTML,/&lt;Reader&gt;/);
  assert.doesNotMatch(root.innerHTML,/<Reader>/);
  assert.match(root.innerHTML,/&lt;New block&gt;/);
  assert.match(root.innerHTML,/이전 버전을 복원했습니다/);
});

test("page updates paginate without replacing earlier cards", async () => {
  const calls = [];
  const {root, more, context} = setup(url => {
    calls.push(url);
    return Promise.resolve(calls.length === 1
      ? {items:[{id:"v2",title:"Second",created_at:20}],next_before_id:"v2"}
      : {items:[{id:"v1",title:"First",created_at:10}],next_before_id:null});
  });
  await context.loadPageUpdates();
  assert.match(root.innerHTML,/Second/);
  assert.equal(more.hidden,false);
  await context.loadPageUpdates(true);
  assert.match(calls[1],/before=v2$/);
  assert.match(root.innerHTML,/Second/);
  assert.match(root.innerHTML,/First/);
  assert.equal(more.hidden,true);
});

test("a saved lock-only change is its own activity without an edit preview", async () => {
  const {root,context} = setup(() => Promise.resolve({items:[
    {id:"v-lock",title:"Page",created_at:22,
      preview:{items:[{kind:"locked",text:"페이지 잠금"}],remaining:0}},
    {id:"v-unlock",title:"Page",created_at:21,
      preview:{items:[{kind:"locked",text:"페이지 잠금 해제"}],remaining:0}},
  ]}));
  await context.loadPageUpdates();
  assert.match(root.innerHTML,/블록을 잠갔습니다/);
  assert.match(root.innerHTML,/블록을 잠금 해제했습니다/);
  assert.doesNotMatch(root.innerHTML,/페이지를 편집했습니다/);
  assert.doesNotMatch(root.innerHTML,/page-update-preview-locked/);
});

"use strict";
const assert = require("node:assert/strict");
const createMeetings = require("../notion_static/notion_meetings.js");
const nodes = new Map();
function element(tag = "div") {
  return {tag, hidden: false, disabled: false, textContent: "", listeners: {}, children: [],
    addEventListener(type, callback) { this.listeners[type] = callback; },
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; }, focus() {},
  };
}
const previousDocument = global.document;
global.document = {getElementById(id) {if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id);}, createElement: element};
const deferred = () => {let resolve; const promise = new Promise(done => resolve = done); return {resolve, promise};};
async function main() {
  const meetings = createMeetings();
  let key = "workspace-a", allowed = true, creates = 0, entryGate = null;
  const requests = [], routes = [], opens = [];
  meetings.init({
    key: () => key, enter: async current => {
      const permitted = entryGate ? await entryGate.promise : allowed;
      return permitted && current();
    },
    route: push => routes.push(push), canCreate: () => true,
    list: request => { const pending = deferred(); requests.push({...pending, request}); return pending.promise; },
    icon: () => "<svg></svg>", relative: () => "방금", openPage: item => opens.push(item.id),
    create: async () => { creates += 1; },
  });
  const first = meetings.open(); await Promise.resolve();
  assert.equal(creates, 0, "opening the tab must not create or record a meeting");
  meetings.leave();
  requests[0].resolve({items:[{id:"old",title:"old"}],count:1}); await first;
  assert.equal(nodes.get("meetingList").children.length, 0, "a closed view must ignore its response");
  assert.equal(nodes.get("meetingsView").hidden, true);
  const second = meetings.open(false); await Promise.resolve();
  key = "workspace-b";
  requests[1].resolve({items:[{id:"old-workspace"}],count:1}); await second;
  assert.equal(nodes.get("meetingList").children.length, 0, "a workspace switch fences stale metadata");
  const third = meetings.open(); await Promise.resolve();
  requests[2].resolve({items:[{id:"meeting",title:"회의",note_count:2}],count:1,has_more:false}); await third;
  nodes.get("meetingList").children[0].listeners.click();
  assert.deepEqual(opens,["meeting"]);
  await nodes.get("meetingNewBtn").listeners.click();
  assert.equal(creates,1);
  meetings.leave(); allowed = false;
  assert.equal(await meetings.open(), false, "navigation refusal must keep the new view closed");
  assert.equal(requests.length,3);
  allowed = true;
  entryGate = deferred();
  const cancelledEntry = meetings.open();
  meetings.leave();
  entryGate.resolve(true);
  assert.equal(await cancelledEntry, false, "leaving during navigation must not reopen meetings");
  assert.equal(nodes.get("meetingsView").hidden, true);
  assert.equal(requests.length,3);
  entryGate = deferred();
  const oldWorkspaceEntry = meetings.open();
  key = "workspace-c";
  entryGate.resolve(true);
  assert.equal(await oldWorkspaceEntry, false, "a completed old-workspace entry must not open a new workspace");
  assert.equal(nodes.get("meetingsView").hidden, true);
  assert.equal(requests.length,3);
  console.log("UI-SHELL-003 meetings: read-only entry, stale requests, explicit create, navigation guard passed");
}
main().finally(() => {global.document = previousDocument;}).catch(error => {console.error(error); process.exitCode=1;});

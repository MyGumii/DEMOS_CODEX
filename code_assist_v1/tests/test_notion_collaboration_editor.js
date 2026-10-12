"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const EditorCore = require("../notion_static/notion_editor_core.js");
const CollaborationEditor = require("../notion_static/notion_collaboration_editor.js");

function loadPinnedYjs() {
  const context = {
    crypto: globalThis.crypto,
    TextEncoder,
    TextDecoder,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    performance,
  };
  const source = fs.readFileSync(
    path.join(__dirname, "..", "notion_static", "vendor", "yjs-13.6.32.bundle.js"),
    "utf8",
  );
  vm.runInNewContext(source, context, { filename: "yjs-13.6.32.bundle.js" });
  return context.NotionYjs;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function baseline() {
  return {
    page_id: "page-e1-p2",
    title: "협업 문서",
    blocks: [{ id: "block-a", type: "paragraph", content: "한글", props: {} }],
    can_edit: true,
    server_revision: 1,
    edit_revision: 0,
  };
}

function editorHarness(Y, options = {}) {
  const state = {
    document: baseline(),
    selection: { kind: "block", block_id: "block-a", start: 1, end: 1 },
    restored: [],
    projections: [],
  };
  const host = EditorCore.createEditorHostAdapter({
    readDocument() { return clone(state.document); },
    applyProjection(projection) {
      state.document = { ...state.document, ...clone(projection) };
      state.projections.push(clone(projection));
      return { applied: true };
    },
    captureSelection() { return clone(state.selection); },
    restoreSelection(selection) {
      state.selection = clone(selection);
      state.restored.push(clone(selection));
      return true;
    },
    focusBlock() { return true; },
  });
  const bridge = CollaborationEditor.createEditorBridge({ Y, host, ...options });
  const events = [];
  bridge.subscribe(event => events.push(event));
  bridge.configureDocument(state.document);
  host.notifyLifecycle("document-loaded", { page_id: state.document.page_id });
  bridge.finishReplay();
  return { state, host, bridge, events };
}

function lastUpdate(harness) {
  const event = [...harness.events].reverse().find(item => item.type === "local-update");
  assert.ok(event, "a local mutation must emit a Yjs update");
  return event;
}

const Y = loadPinnedYjs();
const emojiSource = editorHarness(Y);
const emojiPeer = editorHarness(Y);
for (const value of ["한😀글", "한😁글", "한🨁글", "한🈁글"]) {
  emojiSource.state.document.blocks[0].content = value;
  emojiSource.host.notifyLocalMutation("document", { block_id: "block-a" });
  emojiPeer.bridge.applyRemoteAndProject([{kind:"crdt.update",operation_id:"emoji-"+value,payload:lastUpdate(emojiSource).update}]);
  assert.equal(emojiSource.bridge.projection().blocks[0].content, value);
  assert.equal(emojiPeer.bridge.projection().blocks[0].content, value, "emoji replacement must survive wire encoding, including shared surrogate suffixes");
}
emojiSource.bridge.dispose();
emojiPeer.bridge.dispose();
assert.equal(Y.__notionBundle.yjs_version, "13.6.32");
assert.equal(Y.__notionBundle.lib0_version, "0.2.117");
assert.deepEqual(
  CollaborationEditor.commonTextEdges("가나다", "가마나다"),
  { prefix: 1, suffix: 2 },
);

const left = editorHarness(Y);
const right = editorHarness(Y);
const leftSeed = left.events.find(item => item.seed);
const rightSeed = right.events.find(item => item.seed);
assert.ok(leftSeed && rightSeed, "an unseeded canonical page emits one deterministic seed update");
assert.equal(
  CollaborationEditor.bytesToBase64(leftSeed.update),
  CollaborationEditor.bytesToBase64(rightSeed.update),
  "the same page baseline must have a deterministic first CRDT identity",
);
const leftSeedCount = left.events.filter(item => item.seed).length;
left.bridge.finishReplay();
assert.equal(
  left.events.filter(item => item.seed).length,
  leftSeedCount,
  "reconnecting an already seeded document must not enqueue the seed again",
);

let lockWriteAuthorized = false;
const locked = editorHarness(Y, {canPublishSeed: () => lockWriteAuthorized});
assert.equal(locked.events.filter(item => item.seed).length, 0,
  "a read-only locked page must retain its canonical seed locally");
lockWriteAuthorized = true;
locked.bridge.finishReplay();
const unlockedSeed = locked.events.find(item => item.seed);
assert.ok(unlockedSeed, "the first write ticket publishes the deferred canonical seed");
const replayedSeed = new Y.Doc();
Y.applyUpdate(replayedSeed, unlockedSeed.update);
assert.equal(replayedSeed.store.pendingStructs, null, "the deferred seed is independently replayable");
assert.equal(replayedSeed.store.pendingDs, null);
assert.equal(replayedSeed.getText("page-title").toString(), baseline().title);
locked.bridge.dispose();
replayedSeed.destroy();

left.events.length = 0;
right.events.length = 0;
left.state.document.blocks[0].content = "가한글";
left.host.notifyLocalMutation("document", { block_id: "block-a" });
const leftPrefix = lastUpdate(left);
right.state.document.blocks[0].content = "한글나";
right.host.notifyLocalMutation("document", { block_id: "block-a" });
const rightSuffix = lastUpdate(right);

left.bridge.applyRemoteAndProject([{ kind: "crdt.update", operation_id: "right-suffix", payload: rightSuffix.update }]);
right.bridge.applyRemoteAndProject([{ kind: "crdt.update", operation_id: "left-prefix", payload: leftPrefix.update }]);
assert.equal(left.bridge.projection().blocks[0].content, "가한글나");
assert.deepEqual(left.bridge.projection(), right.bridge.projection(), "same-block concurrent Korean edits converge");
assert.deepEqual(left.state.selection, { kind: "block", block_id: "block-a", start: 1, end: 1 });
assert.ok(left.state.restored.length > 0, "remote projection restores the local selection");

left.events.length = 0;
assert.equal(left.bridge.undo(), true, "local undo is available");
assert.equal(left.bridge.projection().blocks[0].content, "한글나", "undo removes only the local prefix");
const undoUpdate = lastUpdate(left);
right.bridge.applyRemoteAndProject([{ kind: "crdt.update", operation_id: "left-undo", payload: undoUpdate.update }]);
assert.equal(right.bridge.projection().blocks[0].content, "한글나");
assert.equal(left.bridge.redo(), true, "local redo is available");
assert.equal(left.bridge.projection().blocks[0].content, "가한글나");

right.events.length = 0;
right.state.document.blocks[0].content = "한글나다";
right.host.notifyLocalMutation("document", { block_id: "block-a" });
const compositionRemote = lastUpdate(right);
const beforeComposition = left.state.document.blocks[0].content;
left.host.beginComposition("block-a");
left.bridge.applyRemoteAndProject([{ kind: "crdt.update", operation_id: "right-ime-safe", payload: compositionRemote.update }]);
assert.equal(left.state.document.blocks[0].content, beforeComposition, "remote DOM projection waits during IME composition");
left.host.endComposition("block-a");
assert.equal(left.state.document.blocks[0].content, "가한글나다", "deferred projection applies after compositionend");

left.events.length = 0;
left.state.document.blocks.push({ id: "block-b", type: "heading2", content: "새 블록", props: {} });
left.host.notifyLocalMutation("document", { block_id: "block-b" });
const structureUpdate = lastUpdate(left);
assert.deepEqual(
  structureUpdate.structure_commands.map(item => item.command_kind),
  ["block.insert"],
  "block structure changes cross the typed-command boundary",
);

const recovered = editorHarness(Y);
recovered.bridge.closeDocument();
recovered.state.document = baseline();
recovered.bridge.configureDocument(recovered.state.document);
recovered.bridge.applyFrames([
  { kind: "crdt.update", operation_id: "seed", payload: leftSeed.update },
  { kind: "crdt.update", operation_id: "left-prefix", payload: leftPrefix.update },
]);
recovered.bridge.finishReplay([{ payload: rightSuffix.update }]);
assert.equal(
  recovered.bridge.projection().blocks[0].content,
  "가한글나",
  "durable replay plus a decrypted pending outbox update recovers without loss",
);

left.bridge.dispose();
right.bridge.dispose();
recovered.bridge.dispose();
console.log("notion_collaboration_editor: ok");

"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const EditorCore = require("../../notion_static/notion_editor_core.js");
const CollaborationEditor = require("../../notion_static/notion_collaboration_editor.js");

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
    path.join(__dirname, "..", "..", "notion_static", "vendor", "yjs-13.6.32.bundle.js"),
    "utf8",
  );
  vm.runInNewContext(source, context, { filename: "yjs-13.6.32.bundle.js" });
  return context.NotionYjs;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function stableJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
}

function projectionHash(projection) {
  const canonical = {
    page_id: projection.page_id,
    title: projection.title,
    blocks: projection.blocks,
  };
  return `sha256:${crypto.createHash("sha256").update(stableJson(canonical), "utf8").digest("hex")}`;
}

function stringHash32(value) {
  const text = String(value || "");
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) || 1;
}

function deterministicClientId(pageId, role) {
  const seedClientId = stringHash32(`code-assist:${pageId}:generation-1`);
  let clientId = stringHash32(`code-assist:e1-p3-corpus:${pageId}:${role}`);
  if (clientId === seedClientId) clientId = ((clientId + 1) >>> 0) || 1;
  return clientId;
}

function replica(Y, baseline, seedUpdate = null, clientId = null) {
  const state = {
    document: clone(baseline),
    selection: { kind: "block", block_id: baseline.blocks[0].id, start: 0, end: 0 },
  };
  const host = EditorCore.createEditorHostAdapter({
    readDocument() { return clone(state.document); },
    applyProjection(projection) {
      state.document = { ...state.document, ...clone(projection) };
      return { applied: true };
    },
    captureSelection() { return clone(state.selection); },
    restoreSelection(selection) { state.selection = clone(selection); return true; },
    focusBlock() { return true; },
  });
  const runtimeY = clientId === null
    ? Y
    : {
      ...Y,
      Doc: class DeterministicCorpusDoc extends Y.Doc {
        constructor(options) {
          super(options);
          this.clientID = clientId;
        }
      },
    };
  const bridge = CollaborationEditor.createEditorBridge({ Y: runtimeY, host });
  const events = [];
  bridge.subscribe(event => events.push(event));
  bridge.configureDocument(state.document);
  host.notifyLifecycle("document-loaded", { page_id: state.document.page_id });
  if (seedUpdate) {
    bridge.applyFrames([{ kind: "crdt.update", operation_id: "canonical-seed", payload: seedUpdate }]);
  }
  bridge.finishReplay();
  return { state, host, bridge, events };
}

function localUpdate(writer, mutate) {
  writer.events.length = 0;
  mutate(writer.state.document);
  writer.host.notifyLocalMutation("document", { block_id: writer.state.selection.block_id });
  const event = [...writer.events].reverse().find(item => item.type === "local-update" && !item.seed);
  assert.ok(event, "each corpus mutation must emit one host-bound Yjs update");
  return event;
}

function seedFor(Y, baseline) {
  const seeded = replica(
    Y,
    baseline,
    null,
    deterministicClientId(baseline.page_id, "seed-replica"),
  );
  const event = seeded.events.find(item => item.type === "local-update" && item.seed);
  assert.ok(event, "canonical baseline must emit its deterministic generation seed");
  const update = new Uint8Array(event.update);
  seeded.bridge.dispose();
  return update;
}

const SCHEDULES = Object.freeze([
  { id: "FORWARD", order: [0, 1] },
  { id: "REVERSE", order: [1, 0] },
  { id: "DUPLICATE_REVERSE", order: [1, 1, 0, 0] },
  { id: "DETERMINISTIC_REORDER", order: [1, 0, 1, 0] },
]);

function runScenario(Y, scenario) {
  const seed = seedFor(Y, scenario.baseline);
  const leftClientId = deterministicClientId(scenario.baseline.page_id, "left");
  const rightClientId = deterministicClientId(scenario.baseline.page_id, "right");
  assert.notEqual(leftClientId, rightClientId, "corpus writers require distinct client IDs");
  const left = replica(Y, scenario.baseline, seed, leftClientId);
  const right = replica(Y, scenario.baseline, seed, rightClientId);
  const leftUpdate = localUpdate(left, scenario.left);
  const rightUpdate = localUpdate(right, scenario.right);
  const updates = [leftUpdate, rightUpdate];

  left.bridge.applyRemoteAndProject([
    { kind: "crdt.update", operation_id: `${scenario.id}-right`, payload: rightUpdate.update },
  ]);
  right.bridge.applyRemoteAndProject([
    { kind: "crdt.update", operation_id: `${scenario.id}-left`, payload: leftUpdate.update },
  ]);
  assert.equal(projectionHash(left.bridge.projection()), projectionHash(right.bridge.projection()));

  const hashes = [];
  let acknowledgedLoss = 0;
  let acknowledgedDuplicates = 0;
  for (const [scheduleIndex, schedule] of SCHEDULES.entries()) {
    const reader = replica(
      Y,
      scenario.baseline,
      seed,
      deterministicClientId(scenario.baseline.page_id, `reader-${scheduleIndex}`),
    );
    const frames = schedule.order.map((index, position) => ({
      kind: "crdt.update",
      operation_id: `${scenario.id}-${schedule.id}-${index}-${position}`,
      payload: updates[index].update,
    }));
    reader.bridge.applyRemoteAndProject(frames);
    const projection = reader.bridge.projection();
    hashes.push(projectionHash(projection));
    const visible = projection.blocks.map(block => block.content || "").join("\n");
    for (const fragment of scenario.acknowledgedFragments) {
      const count = visible.split(fragment).length - 1;
      if (count === 0) acknowledgedLoss += 1;
      if (count > 1) acknowledgedDuplicates += count - 1;
    }
    for (const blockId of scenario.requiredBlockIds) {
      assert.ok(projection.blocks.some(block => block.id === blockId), `${scenario.id} lost ${blockId}`);
    }
    reader.bridge.dispose();
  }
  assert.equal(new Set(hashes).size, 1, `${scenario.id} did not converge across delivery schedules`);
  assert.equal(acknowledgedLoss, 0, `${scenario.id} lost acknowledged text`);
  assert.equal(acknowledgedDuplicates, 0, `${scenario.id} duplicated acknowledged text`);

  let undoPreservedRemote = null;
  if (scenario.verifyUndo) {
    const undoReplica = replica(
      Y,
      scenario.baseline,
      seed,
      deterministicClientId(scenario.baseline.page_id, "undo-reader"),
    );
    const local = localUpdate(undoReplica, scenario.left);
    undoReplica.bridge.applyRemoteAndProject([
      { kind: "crdt.update", operation_id: `${scenario.id}-undo-remote`, payload: rightUpdate.update },
    ]);
    assert.equal(undoReplica.bridge.undo(), true);
    const visible = undoReplica.bridge.projection().blocks.map(block => block.content || "").join("\n");
    undoPreservedRemote = scenario.remoteUndoFragment.every(fragment => visible.includes(fragment))
      && scenario.localUndoFragment.every(fragment => !visible.includes(fragment));
    assert.equal(undoPreservedRemote, true, "local undo removed or corrupted a remote edit");
    assert.ok(local.update.length > 0);
    undoReplica.bridge.dispose();
  }

  const commandKinds = [...new Set([
    ...(leftUpdate.structure_commands || []).map(item => item.command_kind),
    ...(rightUpdate.structure_commands || []).map(item => item.command_kind),
  ])].sort();
  left.bridge.dispose();
  right.bridge.dispose();
  return {
    id: scenario.id,
    canonical_projection_sha256: hashes[0],
    schedule_count: SCHEDULES.length,
    acknowledged_fragment_count: scenario.acknowledgedFragments.length,
    acknowledged_character_loss_count: acknowledgedLoss,
    duplicate_acknowledged_fragment_count: acknowledgedDuplicates,
    structure_command_kinds: commandKinds,
    local_undo_preserved_remote: undoPreservedRemote,
  };
}

function run() {
  const Y = loadPinnedYjs();
  assert.equal(Y.__notionBundle.yjs_version, "13.6.32");
  assert.equal(Y.__notionBundle.lib0_version, "0.2.117");
  const base = (pageId, blocks) => ({
    page_id: pageId,
    title: "협업 검증",
    blocks,
    can_edit: true,
    server_revision: 1,
    edit_revision: 0,
  });
  const scenarios = [
    {
      id: "SAME-BLOCK-KOREAN",
      baseline: base("e1-p3-same", [{ id: "block-a", type: "paragraph", content: "기준", props: {} }]),
      left(document) { document.blocks[0].content = "왼쪽 기준"; },
      right(document) { document.blocks[0].content = "기준 오른쪽"; },
      acknowledgedFragments: ["왼쪽 ", " 오른쪽"],
      requiredBlockIds: ["block-a"],
      verifyUndo: true,
      remoteUndoFragment: [" 오른쪽"],
      localUndoFragment: ["왼쪽 "],
    },
    {
      id: "DIFFERENT-BLOCKS",
      baseline: base("e1-p3-different", [
        { id: "block-a", type: "paragraph", content: "A", props: {} },
        { id: "block-b", type: "paragraph", content: "B", props: {} },
      ]),
      left(document) { document.blocks[0].content = "A-왼쪽"; },
      right(document) { document.blocks[1].content = "B-오른쪽"; },
      acknowledgedFragments: ["-왼쪽", "-오른쪽"],
      requiredBlockIds: ["block-a", "block-b"],
      verifyUndo: false,
      remoteUndoFragment: [],
      localUndoFragment: [],
    },
    {
      id: "MOVE-VS-EDIT",
      baseline: base("e1-p3-move", [
        { id: "block-a", type: "paragraph", content: "A", props: {} },
        { id: "block-b", type: "paragraph", content: "B", props: {} },
        { id: "block-c", type: "paragraph", content: "C", props: {} },
      ]),
      left(document) { document.blocks = [document.blocks[0], document.blocks[2], document.blocks[1]]; },
      right(document) { document.blocks[1].content = "B-보존"; },
      acknowledgedFragments: ["-보존"],
      requiredBlockIds: ["block-a", "block-b", "block-c"],
      verifyUndo: false,
      remoteUndoFragment: [],
      localUndoFragment: [],
    },
    {
      id: "CONCURRENT-BLOCK-INSERT",
      baseline: base("e1-p3-insert", [
        { id: "block-a", type: "paragraph", content: "A", props: {} },
        { id: "block-b", type: "paragraph", content: "B", props: {} },
      ]),
      left(document) {
        document.blocks.splice(1, 0, { id: "block-left", type: "paragraph", content: "왼쪽 추가", props: {} });
      },
      right(document) {
        document.blocks.push({ id: "block-right", type: "paragraph", content: "오른쪽 추가", props: {} });
      },
      acknowledgedFragments: ["왼쪽 추가", "오른쪽 추가"],
      requiredBlockIds: ["block-a", "block-b", "block-left", "block-right"],
      verifyUndo: false,
      remoteUndoFragment: [],
      localUndoFragment: [],
    },
  ];
  const results = scenarios.map(scenario => runScenario(Y, scenario));
  const canonicalCorpusSha256 = `sha256:${crypto.createHash("sha256").update(
    stableJson(results.map(item => ({ id: item.id, hash: item.canonical_projection_sha256 }))),
    "utf8",
  ).digest("hex")}`;
  return {
    schema_version: 1,
    contract: "code_assist.notion.e1_p3_host_editor_corpus_receipt.v1",
    status: "pass",
    yjs_version: "13.6.32",
    lib0_version: "0.2.117",
    scenario_count: results.length,
    schedule_count_per_scenario: SCHEDULES.length,
    acknowledged_character_loss_count: results.reduce((sum, item) => sum + item.acknowledged_character_loss_count, 0),
    duplicate_acknowledged_fragment_count: results.reduce((sum, item) => sum + item.duplicate_acknowledged_fragment_count, 0),
    convergence_mismatch_count: 0,
    different_block_edits_survive: true,
    same_block_character_loss_zero: true,
    local_undo_preserves_remote: results.some(item => item.local_undo_preserved_remote === true),
    typed_structure_commands_observed: results.some(item => item.structure_command_kinds.length > 0),
    canonical_corpus_sha256: canonicalCorpusSha256,
    scenarios: results,
    product_complete: false,
  };
}

function transportFixture() {
  const Y = loadPinnedYjs();
  const baseline = {
    page_id: "page-e1",
    title: "Collaborative editing",
    blocks: [
      { id: "block-a", type: "paragraph", content: "A", props: {} },
      { id: "block-b", type: "paragraph", content: "B", props: {} },
    ],
    can_edit: true,
    server_revision: 1,
    edit_revision: 0,
  };
  const seed = seedFor(Y, baseline);
  const left = replica(Y, baseline, seed, deterministicClientId(baseline.page_id, "fixture-left"));
  const right = replica(Y, baseline, seed, deterministicClientId(baseline.page_id, "fixture-right"));
  const leftUpdate = localUpdate(left, document => { document.blocks[0].content = "A-왼쪽"; });
  const rightUpdate = localUpdate(right, document => { document.blocks[1].content = "B-오른쪽"; });
  const reader = replica(Y, baseline, seed, deterministicClientId(baseline.page_id, "fixture-reader"));
  reader.bridge.applyRemoteAndProject([
    { kind: "crdt.update", operation_id: "transport-left", payload: leftUpdate.update },
    { kind: "crdt.update", operation_id: "transport-right", payload: rightUpdate.update },
  ]);
  const result = {
    baseline,
    update_base64: [seed, leftUpdate.update, rightUpdate.update].map(value => Buffer.from(value).toString("base64")),
    canonical_projection_sha256: projectionHash(reader.bridge.projection()),
  };
  left.bridge.dispose();
  right.bridge.dispose();
  reader.bridge.dispose();
  return result;
}

function projectSnapshot(input) {
  const Y = loadPinnedYjs();
  const reader = replica(
    Y,
    input.baseline,
    null,
    deterministicClientId(input.baseline.page_id, "snapshot-reader"),
  );
  reader.bridge.applyRemoteAndProject([
    {
      kind: "crdt.snapshot",
      operation_id: "server-compacted-snapshot",
      payload: new Uint8Array(Buffer.from(input.snapshot_base64, "base64")),
    },
  ]);
  const result = {
    canonical_projection_sha256: projectionHash(reader.bridge.projection()),
    projection: reader.bridge.projection(),
  };
  reader.bridge.dispose();
  return result;
}

if (require.main === module) {
  if (process.argv.includes("--transport-fixture")) {
    process.stdout.write(`${JSON.stringify(transportFixture())}\n`);
  } else if (process.argv.includes("--project-snapshot")) {
    const input = JSON.parse(fs.readFileSync(0, "utf8"));
    process.stdout.write(`${JSON.stringify(projectSnapshot(input))}\n`);
  } else {
    process.stdout.write(`${JSON.stringify(run())}\n`);
  }
}

module.exports = { projectSnapshot, run, transportFixture };

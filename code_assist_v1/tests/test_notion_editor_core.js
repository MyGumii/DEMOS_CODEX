"use strict";

const assert = require("node:assert/strict");
const core = require("../notion_static/notion_editor_core.js");

assert.ok(core.BLOCK_DEFINITIONS.length >= 25, "slash menu exposes the supported block catalog");
assert.equal(core.filterBlockDefinitions("캘린더")[0].type, "calendar");
assert.equal(core.filterBlockDefinitions("타임라인")[0].type, "timeline");
assert.equal(core.filterBlockDefinitions("차트")[0].type, "chart");
assert.equal(core.filterBlockDefinitions("대시보드")[0].type, "dashboard");
assert.equal(core.filterBlockDefinitions("폼")[0].type, "form");
assert.equal(core.filterBlockDefinitions("피드")[0].type, "feed");
assert.equal(core.filterBlockDefinitions("지도")[0].type, "map");
assert.equal(core.filterBlockDefinitions("todo")[0].type, "todo");
assert.equal(core.filterBlockDefinitions("간단한 표")[0].type, "simple_table");
assert.equal(core.createBlock("simple_table").props.rows.length, 2);
assert.equal(core.createBlock("button").props.action, "insert_block");
assert.ok(core.createBlock("synced_block").props.sync_id.startsWith("sync_"));
assert.equal(core.createBlock("toggle_heading2").props.open, true);
assert.equal(core.createBlock("toggle_heading4").props.open, true);
assert.equal(core.createBlock("table").props.group_property, "");
assert.equal(core.createBlock("timeline").props.view, "timeline");
assert.equal(core.createBlock("chart").props.view, "chart");
assert.equal(core.createBlock("dashboard").props.view, "dashboard");
assert.equal(core.createBlock("form").props.view, "form");
assert.equal(core.createBlock("feed").props.view, "feed");
assert.equal(core.createBlock("map").props.view, "map");
assert.equal(core.createBlock("map").props.properties.at(-1).type, "place");
assert.equal(core.createBlock("form").props.form_submit_label, "제출");
assert.equal(core.createBlock("tabs").props.tabs.length, 2);
assert.ok(core.createBlock("tabs").props.active_tab_id.startsWith("tab_"));

assert.deepEqual(core.markdownShortcut("# "), { type: "heading1", content: "" });
assert.deepEqual(core.markdownShortcut("#### "), { type: "heading4", content: "" });
assert.deepEqual(core.markdownShortcut("- "), { type: "bullet", content: "" });
assert.deepEqual(core.markdownShortcut("> "), { type: "toggle", content: "" });
assert.deepEqual(core.markdownShortcut("\" "), { type: "quote", content: "" });
assert.deepEqual(core.markdownShortcut("a. "), { type: "numbered", content: "" });
assert.deepEqual(core.markdownShortcut("---"), { type: "divider", content: "" });
assert.equal(core.markdownShortcut("ordinary text"), null);

const heading = core.createBlock("heading1", { content: "Hello world" });
const [headingStart, headingEnd] = core.splitBlock(heading, 5);
assert.equal(headingStart.content, "Hello");
assert.equal(headingEnd.content, " world");
assert.equal(headingEnd.type, "heading1", "splitting inside a heading keeps its type");

const [, paragraphAfterHeading] = core.splitBlock(heading, heading.content.length);
assert.equal(paragraphAfterHeading.type, "paragraph", "Enter at the end of a heading creates text");
assert.equal(core.splitBlock(core.createBlock("heading4", {content:"Fourth"}), 6)[1].type, "paragraph");

const toggleHeading = core.createBlock("toggle_heading1", { content: "Section" });
const [, paragraphAfterToggleHeading] = core.splitBlock(toggleHeading, toggleHeading.content.length);
assert.equal(paragraphAfterToggleHeading.type, "paragraph", "Enter at the end of a toggle heading creates text");

const columnBlock = core.createBlock("paragraph", {
  content: "Column",
  props: { layout_group: "layout_a", layout_column: 1, layout_columns: 2 },
});
const [, columnTail] = core.splitBlock(columnBlock, 3);
assert.equal(columnTail.props.layout_group, "layout_a", "split blocks stay in their column");
assert.equal(columnTail.props.layout_column, 1);

const merged = core.mergeBlocks(
  core.createBlock("paragraph", { content: "Hello " }),
  core.createBlock("paragraph", { content: "world" }),
);
assert.equal(merged.block.content, "Hello world");
assert.equal(merged.caret, 6);

const source = [
  core.createBlock("paragraph", { content: "A", props: { nested: { value: 1 } } }),
  core.createBlock("todo", { content: "B", props: { checked: true } }),
];
const duplicated = core.duplicateBlocks(source, [0]);
assert.equal(duplicated.length, 3);
assert.equal(duplicated[1].content, "A");
assert.notEqual(duplicated[0].id, duplicated[1].id);
duplicated[1].props.nested.value = 2;
assert.equal(duplicated[0].props.nested.value, 1, "duplicates do not share nested props");

const moved = core.moveBlock(source, 0, 1);
assert.equal(moved[0].content, "B");
assert.equal(core.setIndent(source[0], 10).props.indent, core.MAX_INDENT);
assert.equal(core.setIndent(source[0], -10).props.indent, 0);

const pasted = core.blocksFromPlainText("# Title\n- [x] Done\n1. First\nPlain");
assert.deepEqual(pasted.map(block => block.type), ["heading1", "todo", "numbered", "paragraph"]);
assert.equal(pasted[1].props.checked, true);

let history = { entries: [], index: -1 };
history = core.pushHistory(history, { title: "A", blocks: source });
history = core.pushHistory(history, { title: "B", blocks: moved });
const undone = core.historyStep(history, -1);
assert.equal(undone.snapshot.title, "A");
const redone = core.historyStep(undone.history, 1);
assert.equal(redone.snapshot.title, "B");

let hostBacking = {
  page_id: "page-a",
  title: "Host title",
  blocks: [core.createBlock("paragraph", { id: "block-a", content: "local" })],
  can_edit: true,
  server_revision: 7,
  edit_revision: 3,
};
const restoredSelections = [];
const hostEvents = [];
const host = core.createEditorHostAdapter({
  readDocument: () => hostBacking,
  applyProjection: projection => {
    hostBacking = {
      ...hostBacking,
      title: projection.title,
      blocks: projection.blocks,
      server_revision: projection.server_revision,
    };
    return { applied: true };
  },
  captureSelection: () => ({ kind: "block", block_id: "block-a", start: 2, end: 2 }),
  restoreSelection: selection => restoredSelections.push(selection),
  focusBlock: (blockId, offset) => blockId === "block-a" && offset === 2,
});
assert.equal(host.contract, "code_assist.notion.editor_host.v1");
assert.equal(Object.isFrozen(host), true, "the collaboration surface is immutable");
assert.deepEqual(host.capabilities, [
  "document-snapshot",
  "lifecycle-events",
  "local-mutation-events",
  "composition-boundary",
  "selection-preservation",
  "remote-projection",
]);
assert.equal(Object.keys(host).some(key => /state|binding/i.test(key)), false, "private bindings are not exposed");

const detachedSnapshot = host.readDocument();
detachedSnapshot.blocks[0].content = "caller mutation";
assert.equal(hostBacking.blocks[0].content, "local", "host snapshots cannot mutate editor state");

const unsubscribeHost = host.subscribe(event => hostEvents.push(event));
host.notifyLifecycle("document-loaded", { source: "unit" });
host.notifyLocalMutation("document", { block_id: "block-a" });
host.beginComposition("block-a");
const deferredProjection = host.applyRemoteProjection({
  page_id: "page-a",
  title: "Remote title",
  blocks: [core.createBlock("paragraph", { id: "block-a", content: "remote" })],
  server_revision: 8,
});
assert.deepEqual(deferredProjection, { applied: false, deferred: true, reason: "composition-active" });
assert.equal(hostBacking.title, "Host title", "remote projection waits for IME composition to finish");
host.endComposition("block-a");
assert.equal(hostBacking.title, "Remote title");
assert.equal(hostBacking.blocks[0].content, "remote");
assert.equal(restoredSelections.length, 1, "selection is restored after a remote render");
assert.equal(hostEvents.some(event => event.type === "local-mutation"), true);
assert.equal(hostEvents.some(event => event.type === "remote-projection-deferred"), true);
assert.equal(hostEvents.some(event => event.type === "remote-projection-applied"), true);
assert.equal(hostEvents.every(event => event.contract === host.contract), true);
assert.equal(host.focusBlock("block-a", 2), true);

const rejectedProjection = host.applyRemoteProjection({
  page_id: "page-b",
  title: "Wrong page",
  blocks: [],
});
assert.deepEqual(rejectedProjection, { applied: false, deferred: false, reason: "page-mismatch" });
unsubscribeHost();
host.dispose();
assert.deepEqual(
  host.applyRemoteProjection({ page_id: "page-a", title: "Disposed", blocks: [] }),
  { applied: false, deferred: false, reason: "disposed" },
);

console.log("notion_editor_core: ok");

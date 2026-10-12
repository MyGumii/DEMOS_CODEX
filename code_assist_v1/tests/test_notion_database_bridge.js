"use strict";

const assert = require("node:assert/strict");
const bridge = require("../notion_static/notion_database_bridge.js");

assert.deepEqual(bridge.ADOPTED_VIEW_TYPES, [
  "table", "board", "list", "gallery", "calendar", "timeline",
]);
assert.deepEqual(bridge.ADOPTED_PROPERTY_TYPES, [
  "title", "text", "number", "checkbox", "date", "select", "multi_select",
  "status", "person", "files", "url", "email", "phone", "relation",
  "rollup", "formula", "created_time", "last_edited_time",
]);

const canonical = {
  id: "database-a",
  title: "Tasks",
  version: 3,
  properties: [
    { id: "title", name: "Name", type: "title", config: {}, version: 1 },
    { id: "estimate", name: "Estimate", type: "number", config: {}, version: 2 },
  ],
  views: [
    {
      id: "view-table",
      name: "All tasks",
      type: "table",
      state: "active",
      version: 4,
      config: { filters: [], sorts: [], property_visibility: { title: true, estimate: true } },
    },
  ],
  templates: [],
  rows: [
    {
      id: "row-a",
      version: 1,
      cells: { title: "Ship", estimate: 5 },
      cell_versions: { title: 2, estimate: 3 },
      computed: {},
    },
  ],
};

const projected = bridge.canonicalToBlock(canonical, {
  id: "block-a",
  type: "table",
  content: "Old title",
  props: { active_view_id: "view-table" },
});
assert.equal(projected.content, "Tasks");
assert.equal(projected.props.database_id, "database-a");
assert.equal(projected.props.rows[0].estimate, 5);
assert.equal(projected.props.rows[0]._canonical_cell_versions.estimate, 3);
assert.deepEqual(bridge.diffDatabase(canonical, bridge.blockToDesired(projected)), []);

const displaySettings = structuredClone(canonical);
displaySettings.views[0].config = {
  ...displaySettings.views[0].config,
  show_source_title: false,
  show_vertical_lines: false,
  show_page_icons: false,
  wrap_all_content: false,
};
const displayBlock = bridge.canonicalToBlock(displaySettings, projected);
for (const key of ["show_source_title", "show_vertical_lines", "show_page_icons", "wrap_all_content"]) {
  assert.equal(displayBlock.props[key], false);
  assert.equal(displayBlock.props.views[0][key], false);
}
assert.deepEqual(bridge.diffDatabase(displaySettings, bridge.blockToDesired(displayBlock)), []);
displayBlock.props.views[0].show_page_icons = true;
const displayActions = bridge.diffDatabase(displaySettings, bridge.blockToDesired(displayBlock));
assert.deepEqual(displayActions.map(item => item.command_kind), ["database.view.update"]);
assert.equal(displayActions[0].payload.config.show_page_icons, true);

const recoveredFromStaleEditorProjection = bridge.applyCanonicalDatabases([
  { id: "database-a", type: "paragraph", content: "stale", props: {} },
], [canonical])[0];
assert.equal(recoveredFromStaleEditorProjection.type, "table");
assert.equal(recoveredFromStaleEditorProjection.content, "Tasks");
assert.equal(recoveredFromStaleEditorProjection.props.database_id, "database-a");

const changed = structuredClone(projected);
changed.content = "Renamed tasks";
changed.props.properties[1].name = "Points";
changed.props.views[0].name = "Planning";
changed.props.rows[0].estimate = 8;
const changedActions = bridge.diffDatabase(canonical, bridge.blockToDesired(changed));
assert.deepEqual(changedActions.map(item => item.command_kind), [
  "database.update",
  "database.property.update",
  "database.view.update",
  "database.cell.set",
]);
assert.equal(changedActions.at(-1).expected_target_version, 3);
assert.equal(changedActions.at(-1).payload.value, 8);

for (const [type,firstValue] of [
  ["files",[{url:"https://example.com/first.pdf",name:"First"}]],
  ["relation",["related-row"]],
  ["multi_select",["Ready"]],
]) {
  const newlyAdded = structuredClone(canonical);
  const config=type==="relation"?{target_database_id:""}
    :type==="multi_select"?{options:[]}:{};
  newlyAdded.properties.push({id:type,name:type,type,config,version:1});
  const emptyBlock = bridge.canonicalToBlock(newlyAdded, projected);
  assert.deepEqual(bridge.diffDatabase(newlyAdded,bridge.blockToDesired(emptyBlock)),[],
    `an unstored empty ${type} cell must not create a competing first write`);
  emptyBlock.props.rows[0][type]=firstValue;
  const firstActions = bridge.diffDatabase(newlyAdded,bridge.blockToDesired(emptyBlock));
  assert.deepEqual(firstActions.map(item=>item.command_kind),["database.cell.set"]);
  assert.equal(firstActions[0].expected_target_version,0);
}

const templated = structuredClone(canonical);
templated.templates = [{id:"template-task", name:"Task", state:"active",
  default_values:{title:"From template", estimate:3}}];
const templatedBlock = bridge.canonicalToBlock(templated, projected);
templatedBlock.props.rows.push({page_id:"row-templated", _canonical_row_id:"row-templated",
  _template_id:"template-task", title:"From template", estimate:3});
const templateActions = bridge.diffDatabase(templated, bridge.blockToDesired(templatedBlock));
assert.deepEqual(templateActions.map(item => item.command_kind), ["database.row.create"]);
assert.deepEqual(templateActions[0].payload, {template_id:"template-task"});

const defaulted = structuredClone(templated);
defaulted.default_template = {template_id:"template-task", version:1};
const defaultedBlock = bridge.canonicalToBlock(defaulted, projected);
assert.deepEqual(defaultedBlock.props.default_template, defaulted.default_template);
defaultedBlock.props.rows.push({page_id:"row-explicit-empty", _canonical_row_id:"row-explicit-empty",
  _template_id:null, title:"새 페이지", estimate:0});
const emptyActions = bridge.diffDatabase(defaulted, bridge.blockToDesired(defaultedBlock));
assert.deepEqual(emptyActions[0].payload, {template_id:null});

const dateRange = {start: "2026-09-16", end: "2026-09-18"};
const serializedDate = bridge.cellValue({id: "due", type: "date"}, {due: dateRange});
assert.deepEqual(serializedDate, dateRange, "both ends of a date range are one canonical cell value");
assert.notEqual(serializedDate, dateRange, "editing the projection cannot mutate the saved value");
assert.equal(bridge.cellValue({id: "due", type: "date"}, {due: "2026-09-16"}), "2026-09-16");

const fresh = {
  id: "database-new",
  type: "table",
  content: "New database",
  props: {
    properties: [
      { id: "title", name: "Name", type: "title" },
      { id: "done", name: "Done", type: "checkbox" },
    ],
    views: [{ id: "view-new", name: "Table", type: "table", filters: [], sorts: [] }],
    rows: [{ page_id: "row-new", title: "First", done: false }],
  },
};
const freshActions = bridge.diffPage([fresh], []);
assert.deepEqual(freshActions.map(item => item.command_kind), [
  "database.create",
  "database.property.create",
  "database.property.create",
  "database.view.create",
  "database.row.create",
  "database.cell.set",
  "database.cell.set",
]);
assert.ok(freshActions.every(item => item.database_id === "database-new"));

for (const hiddenType of ["chart", "dashboard", "form", "feed", "map"]) {
  const hidden = { ...fresh, id: `hidden-${hiddenType}`, type: hiddenType };
  assert.equal(bridge.blockToDesired(hidden), null);
  assert.deepEqual(bridge.diffPage([hidden], []), []);
}

const paired = structuredClone(canonical);
paired.properties.push({
  id: "linked", name: "Linked", type: "relation", version: 2,
  config: {target_database_id: "database-b", reciprocal_property_id: "back"},
});
paired.rows[0].cells.linked = ["row-b"];
paired.rows[0].cell_versions.linked = 1;
const pairedBlock = bridge.canonicalToBlock(paired, {
  id: "block-a", type: "table", content: "Tasks", props: {},
});
assert.equal(pairedBlock.props.properties.at(-1).config.reciprocal_property_id, "back");
assert.deepEqual(bridge.diffDatabase(paired, bridge.blockToDesired(pairedBlock)), []);

console.log("notion_database_bridge: ok");

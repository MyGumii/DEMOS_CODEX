"use strict";
const assert = require("node:assert/strict");
const bridge = require("../notion_static/notion_database_bridge.js");
const config = {expression: 'prop("old-number") * 2', copy_property_ids: {"old-number": "copied-number"}};
const database = {id: "copied-db", title: "Copy", version: 1,
  properties: [{id: "copied-number", name: "Number", type: "number", position: 0, version: 1, config: {}},
    {id: "copied-formula", name: "Formula", type: "formula", position: 1, version: 1, config}],
  rows: [], views: [{id: "view", type: "table", name: "Table", position: 0, version: 1, state: "active", config: {}}], templates: []};
const block = bridge.canonicalToBlock(database, {id: "block", type: "table", props: {}});
const desired = bridge.blockToDesired(block);
assert.deepEqual(desired.properties[1].config, config, "schema round trip must not discard copied formula references");
block.props.properties[1].expression += " + 1";
const edited = bridge.blockToDesired(block);
assert.equal(edited.properties[1].config.expression, config.expression + " + 1");
assert.deepEqual(edited.properties[1].config.copy_property_ids, config.copy_property_ids);
edited.properties[1].config.copy_property_ids["old-number"] = "changed";
assert.equal(database.properties[1].config.copy_property_ids["old-number"], "copied-number");
console.log("PASS copied formula references survive canonical hydration and schema edits");

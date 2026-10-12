"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const bridge = require("../notion_static/notion_database_bridge.js");
const linked = require("../notion_static/notion_linked_databases.js");

function fixture() {
  const stored = { id: "linked-block", type: "table", content: "", props: {
    linked_database: { document_id: "source-page", database_id: "source-db" },
    views: [{ id: "local-view", name: "Local A", type: "table", config: {
      filters: [{ property: "title", operator: "contains", value: "A" }],
    }}], active_view_id: "local-view", indent: 1,
  }};
  const database = { id: "source-db", title: "Shared source", version: 3,
    properties: [{ id: "title", name: "Name", type: "title", config: {}, version: 1 }],
    rows: [{ id: "row-a", cells: { title: "A" }, cell_versions: { title: 1 }, version: 1 }],
    views: [{ id: "source-view", name: "All", type: "table", config: { filters: [] }, version: 2 }],
  };
  return { stored, result: { contract: "code_assist.notion.linked_database.v1",
    instance: { document_id: "instance-page", block_id: stored.id },
    source: structuredClone(stored.props.linked_database), database,
    views: structuredClone(stored.props.views), active_view_id: "local-view",
  }};
}

test("linked projection shares source rows and retains independent local filters", () => {
  const { stored, result } = fixture(), before = JSON.stringify({ stored, result });
  const projected = bridge.linkedToBlock(result, stored, "instance-page");
  assert.equal(projected.content, "Shared source");
  assert.equal(projected.props.rows[0].title, "A");
  assert.equal(projected.props.views[0].id, "local-view");
  assert.equal(projected.props.filters[0].value, "A");
  assert.equal(projected.props.indent, 1);
  projected.props.rows[0].title = "only local object changed";
  projected.props.filters[0].value = "B";
  assert.equal(JSON.stringify({ stored, result }), before);
  result.database.rows[0].cells.title = "A source changed";
  assert.equal(bridge.linkedToBlock(result, stored, "instance-page").props.rows[0].title, "A source changed");
});

test("database-scoped linked projection removes an inaccessible parent reference", () => {
  const { stored, result } = fixture();
  result.source = { database_id: "source-db" };
  result.instance_witness = { workspace_epoch: 1, document_sequence: 1,
    document_epoch: 1, generation: 1 };
  result.access = { workspace_epoch: 1, document_sequence: 2,
    document_epoch: 1, database_epoch: 1 };
  const projected = bridge.linkedToBlock(result, stored, "instance-page");
  assert.deepEqual(projected.props.linked_database, { database_id: "source-db" });
  assert.equal(JSON.stringify(projected).includes("source-page"), false);
  assert.equal(stored.props.linked_database.document_id, "source-page");
  result.source.database_id = "wrong-source";
  assert.throws(() => bridge.linkedToBlock(result, stored, "instance-page"));
});

test("linked local views cannot enter owned database schema or row diff", () => {
  const { stored, result } = fixture();
  const projected = bridge.linkedToBlock(result, stored, "instance-page");
  assert.deepEqual(bridge.diffPage([stored, projected], []), []);
  assert.deepEqual(bridge.diffPage([projected], [result.database]), []);
});

test("linked table display options remain local to the active view", () => {
  const { stored, result } = fixture();
  result.database.views[0].config = { show_page_icons: false };
  for (const key of ["show_source_title", "show_vertical_lines", "show_page_icons", "wrap_all_content"])
    stored.props.views[0].config[key] = false;
  result.views = structuredClone(stored.props.views);
  const projected = bridge.linkedToBlock(result, stored, "instance-page");
  for (const key of ["show_source_title", "show_vertical_lines", "show_page_icons", "wrap_all_content"])
    assert.equal(projected.props[key], false);
  assert.deepEqual(bridge.diffPage([projected], [result.database]), []);
  result.views[0].config.show_page_icons = true;
  assert.equal(bridge.linkedToBlock(result, stored, "instance-page").props.show_page_icons, true);
  assert.equal(result.database.views[0].config.show_page_icons, false);
});

test("property ordering keeps title fixed, appends new fields, and survives view projection", () => {
  const properties = [
    { id: "name", name: "Name", type: "title" },
    { id: "state", name: "State", type: "status" },
    { id: "due", name: "Due", type: "date" },
  ];
  const order = linked.movedPropertyOrder(properties, [], "due", "state");
  assert.deepEqual(order, ["name", "due", "state"]);
  assert.equal(linked.movedPropertyOrder(properties, order, "name", "state"), null);
  assert.equal(linked.movedPropertyOrder(properties, order, "due", "name"), null);
  assert.deepEqual(linked.orderedProperties([...properties, { id: "note", type: "text" }], order)
    .map(property => property.id), ["name", "due", "state", "note"]);

  const { stored, result } = fixture();
  result.database.properties = structuredClone(properties);
  stored.props.views[0].config.property_order = order;
  result.views = structuredClone(stored.props.views);
  const projected = bridge.linkedToBlock(result, stored, "instance-page");
  assert.deepEqual(projected.props.property_order, order);
  assert.deepEqual(bridge.diffPage([projected], [result.database]), []);
  const canonical = structuredClone(result.database);
  canonical.views[0].config.property_order = order;
  const owned = bridge.canonicalToBlock(canonical, { id: "owned", type: "table", props: {
    active_view_id: "source-view", properties: structuredClone(properties), rows: [],
  }});
  assert.deepEqual(owned.props.property_order, order);
  assert.equal(bridge.diffPage([owned], [canonical])
    .some(command => command.command_kind.startsWith("database.view.")), false);
});

test("group ordering applies per view and keeps manual moves after projection", () => {
  const property = {id:"state",name:"State",type:"status"};
  const rows = [{state:"진행"},{state:"시작"},{state:"진행"},{state:""}];
  const entries = linked.groupEntriesForRows(rows,property);
  assert.deepEqual(entries.map(([label])=>label),["진행","시작","비어 있음"]);
  assert.deepEqual(linked.groupEntriesForRows([{due:{start:"2026-09-16",end:"2026-09-17"}}],
    {id:"due",type:"date"}).map(([label])=>label),["2026-09-16 → 2026-09-17"]);
  assert.deepEqual(linked.sortGroupEntries(entries,"asc").map(([label])=>label),["시작","진행","비어 있음"]);
  assert.deepEqual(linked.sortGroupEntries(entries,"count_desc").map(([label])=>label),["진행","시작","비어 있음"]);
  const order = linked.movedGroupOrder(entries,"manual",[],"진행",1);
  assert.deepEqual(order,["시작","진행","비어 있음"]);
  assert.deepEqual(linked.sortGroupEntries(entries,"manual",order).map(([label])=>label),order);
  const board=linked.boardGroupEntriesForRows(rows,[property],{board_group_property:"state"});
  assert.equal(board.property.id,"state");
  assert.deepEqual(linked.sortGroupEntries(board.entries,"count_desc").map(([label])=>label)[0],"진행");
  const {stored,result} = fixture();
  stored.props.views[0].config = {group_property:"state",group_sort:"manual",group_order:order};
  result.views = structuredClone(stored.props.views);
  result.database.properties.push(property);
  result.database.rows[0].cells.state = "진행";
  const projected = bridge.linkedToBlock(result,stored,"instance-page");
  assert.deepEqual(projected.props.group_order,order);
  assert.equal(projected.props.group_sort,"manual");
  assert.deepEqual(bridge.diffPage([projected],[result.database]),[]);
});

test("filter value controls follow property types without discarding existing values", () => {
  const control = (property, value, operator = "contains") => linked.filterValueHtml(
    property, { property: property.id, operator, value }, 0, "data-linked-pop-filter-value");
  assert.match(control({ id: "status", name: "Status", type: "status", options: ["진행", "완료"] }, "진행"),
    /<select[^>]*data-linked-pop-filter-value="0"[^>]*>[\s\S]*value="진행" selected/);
  assert.match(control({ id: "status", name: "Status", type: "status", options: ["완료"] }, "예전 상태"),
    /value="예전 상태" selected/);
  assert.match(control({ id: "done", name: "Done", type: "checkbox" }, false), /value="false" selected/);
  assert.match(control({ id: "due", name: "Due", type: "date" }, "2026-09-16"), /type="date"/);
  assert.match(control({ id: "due", name: "Due", type: "date" }, "next week"), /type="text"/);
  assert.match(control({ id: "count", name: "Count", type: "number" }, 0), /type="number"[^>]*value="0"/);
  assert.match(control({ id: "title", name: "Name", type: "title" }, "A", "empty"), / disabled>/);
});

test("nested filter groups keep AND/OR semantics and project with local views", () => {
  const group = { logic: "and", children: [
    { property: "state", operator: "equals", value: "진행" },
    { logic: "or", children: [
      { property: "name", operator: "contains", value: "Alpha" },
      { logic: "and", children: [
        { property: "due", operator: "on_or_before", value: "2026-09-16" },
        { property: "done", operator: "equals", value: false },
      ]},
    ]},
  ]};
  const comparable = value => String(value ?? "");
  assert.equal(linked.filterMatchesGroup({state:"진행",name:"Alpha",due:"2026-09-17"}, group, comparable), true);
  assert.equal(linked.filterMatchesGroup({state:"진행",name:"Beta",due:"2026-09-16",done:false}, group, comparable), true);
  assert.equal(linked.filterMatchesGroup({state:"완료",name:"Alpha",due:"2026-09-16"}, group, comparable), false);
  assert.equal(linked.filterMatchesGroup({state:"진행",name:"Beta",due:"2026-09-17",done:false}, group, comparable), false);
  assert.equal(linked.filterGroupRuleCount(group), 4);
  assert.equal(linked.validFilterGroup(group, [
    {id:"state"},{id:"name"},{id:"due"},{id:"done"}]), true);
  assert.equal(linked.validFilterGroup({logic:"or",children:[{logic:"and",children:[
    {logic:"or",children:[{logic:"and",children:[]}]},
  ]}]},[{id:"name"}]), false);
  const withoutDue = linked.filterGroupWithoutProperty(group,"due");
  assert.equal(linked.filterGroupRuleCount(withoutDue), 3);
  assert.equal(linked.filterGroupRuleCount(group), 4);
  const {stored,result} = fixture();
  stored.props.views[0].config.filter_group = structuredClone(group);
  result.views = structuredClone(stored.props.views);
  const projected = bridge.linkedToBlock(result,stored,"instance-page");
  assert.deepEqual(projected.props.filter_group,group);
  assert.deepEqual(bridge.diffPage([projected],[result.database]),[]);
});

test("owned hydration cannot substitute source views into linked instances", () => {
  const { stored, result } = fixture();
  stored.props.database_id = "source-db";
  assert.deepEqual(bridge.applyCanonicalDatabases([stored], [result.database]), [stored]);
});

test("linked projection rejects responses from a different instance or source", () => {
  const { stored, result } = fixture();
  for (const mutate of [
    r => { r.instance.document_id = "other-page"; },
    r => { r.instance.block_id = "other-block"; },
    r => { r.source.document_id = "other-source-page"; },
    r => { r.database.id = "other-source-db"; },
    r => { r.active_view_id = "missing-view"; },
  ]) {
    const stale = structuredClone(result); mutate(stale);
    assert.throws(() => bridge.linkedToBlock(stale, stored, "instance-page"));
  }
  assert.throws(() => bridge.linkedToBlock(result, stored));
});

test("switching to a view without a filter clears the previous active filter", () => {
  const { result } = fixture();
  const database = result.database;
  database.views[0].config = {};
  const projected = bridge.canonicalToBlock(database, { id: "owned", type: "table", props: {
    active_view_id: "source-view", filters: [{ property: "title", value: "stale" }],
    sorts: [{ property: "title", direction: "desc" }],
  }});
  assert.equal(Object.hasOwn(projected.props, "filters"), false);
  assert.equal(Object.hasOwn(projected.props, "sorts"), false);
  assert.deepEqual(bridge.diffPage([projected], [database]), []);
});

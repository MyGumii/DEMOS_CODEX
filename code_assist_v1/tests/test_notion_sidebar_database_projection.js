"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const start = source.indexOf("function projectSidebarPageBlocks(");
const end = source.indexOf("function sidebarPageBlockOutline(", start);
const orderStart = source.indexOf("function sidebarOrderedPageChildren(", end);
const orderEnd = source.indexOf("function observeSidebarPageForDatabases(", orderStart);
const context = {
  DATABASE_BLOCK_TYPES: new Set(["table", "board", "calendar", "list", "gallery", "timeline"]),
  DATABASE_VIEW_LABELS: {table:"표", board:"보드", calendar:"캘린더"},
};
vm.createContext(context);
vm.runInContext(source.slice(start, end) + source.slice(orderStart, orderEnd), context);

test("R5 sidebar children use authorized page blocks and canonical database view titles", () => {
  const blocks = [
    {id:"ordinary", type:"paragraph", content:"본문"},
    {id:"db", type:"table", content:"오래된 제목", props:{database_id:"canonical-db"}},
    {id:"hidden", type:"child_page", props:{page_id:"denied", inaccessible:true}},
    {id:"child", type:"child_page", props:{page_id:"allowed"}},
    {id:"linked", type:"table", props:{linked_database:{database_id:"elsewhere"}}},
  ];
  const canonical = [{id:"canonical-db", title:"현재 표", views:[
    {id:"view-one", type:"table", name:"표 보기", state:"active"},
    {id:"view-old", type:"board", name:"숨긴 보기", state:"archived"},
  ]}];
  const outline = context.projectSidebarPageBlocks(blocks, canonical);
  assert.equal(outline.length, 2);
  assert.equal(outline[0].title, "현재 표");
  assert.deepEqual(JSON.parse(JSON.stringify(outline[0].views)), [{id:"view-one", name:"표 보기"}]);
  assert.equal(outline[1].id, "allowed");
  const ordered = context.sidebarOrderedPageChildren([
    {id:"unreferenced", title:"나머지", position:1},
    {id:"allowed", title:"하위", position:2},
  ], outline);
  assert.deepEqual(JSON.parse(JSON.stringify(ordered.map(item => item.kind === "database" ? "db" : item.page.id))),
    ["db", "allowed", "unreferenced"]);
});

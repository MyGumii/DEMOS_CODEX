"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const start = source.indexOf("const RemoteRelationTargets = new WeakMap();");
const end = source.indexOf("function calculateDatabaseRollup(", start);
assert(start >= 0 && end > start);

const pageData = {blocks: [{id: "source-db", type: "table", props: {database_id: "source-db"}}]};
const calls = [];
let renders = 0;
const context = {
  State: {pageData},
  window: {NotionLinkedDatabases: {reference: block => Boolean(block.props?.linked_database)}},
  DATABASE_BLOCK_TYPES: new Set(["table"]),
  databaseRows: block => block.props.rows || [],
  databaseRowTitle: (_block, row) => row.title,
  visiblePages: () => [],
  collaborativeDatabaseActive: () => true,
  linkedDatabaseScope: () => ({data: pageData, workspaceId: "workspace-test"}),
  linkedDatabaseScopeCurrent: scope => scope.data === pageData,
  renderBlocks: () => { renders += 1; },
  api: async url => {
    calls.push(url);
    if (url.includes("denied-db")) throw new Error("denied");
    if (url.endsWith("/database-sources/remote-db"))
      return {source: {id: "remote-db", document_id: "other-page"}};
    if (url.endsWith("/pages/other-page/databases/remote-db"))
      return {database: {id: "remote-db", properties: [{id: "title", type: "title"}],
        rows: [{id: "row-a", cells: {title: "Actual remote row"}},
          {id: "row-hidden", archived: true, cells: {title: "Hidden row"}}]}};
    throw new Error("unexpected URL: " + url);
  },
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);

(async () => {
  const remote = {relation_database_block_id: "remote-db"};
  assert.equal(context.databaseRelationOptions(remote).length, 0);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(JSON.parse(JSON.stringify(context.databaseRelationOptions(remote))),
    [{id: "row-a", label: "Actual remote row"}]);
  assert.equal(calls.length, 2);
  assert.equal(renders, 1);
  context.databaseRelationOptions(remote);
  assert.equal(calls.length, 2, "a settled target must not trigger another fetch per cell");
  pageData.blocks.push({id: "linked-view", type: "table", props: {
    database_id: "remote-db", linked_database: {document_id: "other-page", database_id: "remote-db"},
  }});
  assert.deepEqual(JSON.parse(JSON.stringify(context.databaseRelationOptions(remote))),
    [{id: "row-a", label: "Actual remote row"}], "a linked view is not the owned target database");

  const denied = {relation_database_block_id: "denied-db"};
  assert.equal(context.databaseRelationOptions(denied).length, 0);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(context.databaseRelationOptions(denied).length, 0);
  assert.equal(calls.filter(url => url.includes("denied-db")).length, 1);

  pageData.blocks.push({id: "presentation-block", type: "table", props: {
    database_id: "local-db", rows: [{page_id: "local-row", title: "Local row"}],
  }});
  assert.deepEqual(JSON.parse(JSON.stringify(context.databaseRelationOptions({relation_database_block_id: "local-db"}))),
    [{id: "local-row", label: "Local row"}]);
  console.log("notion_relation_targets: ok");
})().catch(error => { console.error(error); process.exitCode = 1; });

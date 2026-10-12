"use strict";

const assert = require("node:assert/strict");
const {create} = require("../notion_static/notion_database_relation_pair.js");

function memoryStorage() {
  const values = new Map();
  return {
    get length() { return values.size; },
    key(index) { return [...values.keys()][index] ?? null; },
    getItem(key) { return values.get(key) ?? null; },
    setItem(key, value) { values.set(key, String(value)); },
    removeItem(key) { values.delete(key); },
  };
}

const scope = {userId:"owner", workspaceId:"workspace", pageId:"source-page",
  propertyId:"related"};
const body = {database_id:"source-db", command_kind:"database.relation_pair.enable",
  target_id:"related", payload:{target_document_id:"target-page",
    target_database_id:"target-db", inverse_property_id:"inverse",
    inverse_name:"Back", expected_target_sequence:2, expected_target_epoch:1},
  expected_document_sequence:3, expected_workspace_epoch:1,
  expected_document_epoch:1, expected_target_version:1,
  operation_id:"pair-one"};
const result = {contract:"code_assist.notion.database_relation_pair.v1",
  command_kind:body.command_kind, workspace_id:scope.workspaceId,
  document_id:scope.pageId, database_id:body.database_id,
  property_id:scope.propertyId, document_sequence:4};

async function main() {
  const storage = memoryStorage(), sent = [];
  const locks = {request: async (_key, _options, action) => action(true)};
  let replyLost = true;
  const transport = async (_url, options) => {
    sent.push(options.body);
    if (replyLost) { replyLost = false; throw new Error("response lost"); }
    return result;
  };
  const first = create({storage:() => storage, locks:() => locks, transport});
  await assert.rejects(first.run(scope, {body}), /response lost/);
  assert.deepEqual(first.read(scope).body, body);
  const restarted = create({storage:() => storage, locks:() => locks, transport});
  assert.equal(restarted.list({userId:scope.userId, workspaceId:scope.workspaceId}).length, 1);
  await assert.rejects(restarted.run(scope, {body:{...body, operation_id:"pair-two"}}),
    /확인되지 않은 다른 관계 요청/);
  assert.equal(sent.length, 1);
  assert.equal((await restarted.run(scope)).state, "committed");
  assert.deepEqual(sent, [body, body]);
  assert.equal(restarted.read(scope), null);

  const blocked = create({storage:() => ({...memoryStorage(),
    setItem() { throw new Error("quota"); }}), locks:() => locks,
    transport:async () => { throw new Error("request must not be sent"); }});
  await assert.rejects(blocked.run(scope, {body}), /보관하지 못해 전송하지 않았습니다/);
  console.log("relation pair journal: restart replay, request guard, storage failure passed");
}

main().catch(error => { console.error(error); process.exitCode = 1; });

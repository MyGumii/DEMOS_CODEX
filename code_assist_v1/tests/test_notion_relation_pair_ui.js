"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const start = source.indexOf("function databaseRelationPairScope(");
const end = source.indexOf("function databaseFilterDraftFromDom(", start);
assert(start >= 0 && end > start);

const relation = {id: "relation-source", type: "relation", name: "Related",
  relation_database_block_id: "database-target", _canonical_version: 2, config: {target_database_id: "database-target"}};
const data = {page: {document_sequence: 11}, blocks: [{id: "database-source", type: "table",
  props: {database_id: "database-source", properties: [relation]}}], databases: []};
const modal = {hidden: false};
const statusNode = {textContent: ""};
const modalBody = {contains: node => node === statusNode};
const name = {value: "Reverse", disabled: false};
const primary = {disabled: false};
const elements = {"#modalBg": modal, "#modalBody": modalBody,
  "#dbRelationPairStatus": statusNode, "#dbRelationPairName": name,
  "#modalActions .primary": primary};
let modalActions = [], posted = null, reloaded = [], rendered = 0, uuid = 0;
let mode = "enable";
let loseFirstReply = true;
const attempts = [];
const values = new Map();
const storage = {get length() { return values.size; },
  key: index => [...values.keys()][index] ?? null,
  getItem: key => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: key => values.delete(key)};
const locks = {request: async (_key, _options, action) => action(true)};
const snapshots = () => {
  const paired = mode === "disable";
  return {
    source: {page: {id: "page-source", workspace_id: "workspace-a",
      document_sequence: paired ? 12 : 11, workspace_epoch: 5, document_epoch: 3},
      databases: [{id: "database-source", properties: [{id: relation.id, type: "relation",
        version: paired ? 3 : 2, config: {target_database_id: "database-target",
          ...(paired ? {reciprocal_property_id: "relation-back"} : {})}}]}]},
    target: {page: {id: "page-target", workspace_id: "workspace-a",
      document_sequence: paired ? 8 : 7, workspace_epoch: 5, document_epoch: 4},
      databases: [{id: "database-target", properties: paired
        ? [{id: "relation-back", type: "relation", version: 1,
          config: {target_database_id: "database-source", reciprocal_property_id: relation.id}}]
        : []}]},
  };
};
const context = {
  DatabaseRelationPairController: null,
  State: {user: {id: "owner"}, navigation: {collaborativeDatabaseRelationPair: true,
      workspaceId: "workspace-a"}, pageData: data,
    liveCollaboration: {durableCursor: 0}},
  window: {NotionDatabaseRelationPair:
    require("../notion_static/notion_database_relation_pair.js"),
    localStorage: storage, navigator: {locks}},
  $: key => elements[key],
  crypto: {randomUUID: () => `uuid-${++uuid}`},
  collaborativeDatabaseActive: () => true,
  canEditDatabaseSchema: () => true,
  linkedDatabaseScope: () => ({data, pageId: "page-source", workspaceId: "workspace-a"}),
  linkedDatabaseScopeCurrent: scope => scope.data === data,
  databaseProperties: block => block.props.properties,
  escapeAttr: String,
  openModal: (_title, _html, actions) => {modal.hidden = false; modalActions = actions;},
  closeModal: () => {modal.hidden = true;},
  flushPageDraft: async () => true,
  collaborativeDatabaseSyncPending: () => false,
  DatabaseBridge: {diffPage: () => []},
  flushCollaborativeDatabaseSync: async () => true,
  reloadCanonicalDatabases: async ids => {reloaded = ids;},
  renderBlocks: () => {rendered += 1;},
  setStatus: () => {},
  api: async (url, options) => {
    if (options?.method === "POST") {
      posted = options.body;
      attempts.push(JSON.stringify(options.body));
      if (mode === "enable" && loseFirstReply) {
        loseFirstReply = false;
        throw new Error("response lost");
      }
      return {contract: "code_assist.notion.database_relation_pair.v1",
        command_kind: options.body.command_kind, workspace_id: "workspace-a",
        document_id: "page-source", database_id: "database-source",
        property_id: relation.id,
        document_sequence: mode === "enable" ? 12 : 13,
        ...(mode === "enable"
          ? {inverse_document_id: "page-target"}
          : {deleted_inverse_document_id: "page-target"})};
    }
    if (url.endsWith("/database-sources/database-target"))
      return {source: {id: "database-target", document_id: "page-target", effective_role: "edit"}};
    if (url.endsWith("/pages/page-source/collaboration")) return snapshots().source;
    if (url.endsWith("/pages/page-target/collaboration")) return snapshots().target;
    throw new Error("unexpected URL: " + url);
  },
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);

const settle = () => new Promise(resolve => setTimeout(resolve, 20));
(async () => {
  context.openDatabaseRelationPairDialog("database-source", relation.id);
  assert.equal(modalActions[1].label, "양방향 연결");
  modalActions[1].action();
  await settle();
  assert.equal(modal.hidden, false);
  assert.equal(primary.disabled, false);
  modalActions[1].action();
  await settle();
  assert.equal(attempts[0], attempts[1], "uncertain responses must retry the exact command and operation ID");
  assert.equal(posted.command_kind, "database.relation_pair.enable");
  assert.equal(posted.payload.target_document_id, "page-target");
  assert.equal(posted.payload.expected_target_sequence, 7);
  assert.equal(posted.expected_target_version, 2);
  assert.equal(posted.payload.inverse_name, "Reverse");
  assert.equal(posted.operation_id, "db-relation-pair-uuid2");
  assert.deepEqual(JSON.parse(JSON.stringify(reloaded)), ["database-source"]);
  assert.equal(modal.hidden, true);
  assert.equal(data.page.document_sequence, 12);
  assert.equal(rendered, 1);

  mode = "disable";
  relation.config.reciprocal_property_id = "relation-back";
  context.openDatabaseRelationPairDialog("database-source", relation.id);
  assert.equal(modalActions[1].label, "양방향 해제");
  modalActions[1].action();
  await settle();
  assert.equal(posted.command_kind, "database.relation_pair.disable");
  assert.equal(posted.payload.expected_inverse_sequence, 8);
  assert.equal(posted.payload.expected_inverse_property_version, 1);
  assert.equal(posted.expected_target_version, 3);
  assert.equal(data.page.document_sequence, 13);
  assert.equal(modal.hidden, true);
  console.log("notion_relation_pair_ui: ok");
})().catch(error => { console.error(error); process.exitCode = 1; });

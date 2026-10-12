"use strict";

// Disposable E0-P2 Yjs compactor adapter. No socket or service is created.
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const readline = require("readline");
const Y = require("yjs");

const ADAPTER_ID = "PY_OPAQUE_NODE_COMPACTOR_YJS";

function emit(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || index + 1 >= process.argv.length) {
    throw new Error(`missing ${name}`);
  }
  return path.resolve(process.argv[index + 1]);
}

function loadDocument(statePath) {
  const document = new Y.Doc();
  if (fs.existsSync(statePath)) {
    const update = fs.readFileSync(statePath);
    if (update.length) Y.applyUpdate(document, update);
  }
  return document;
}

function persist(document, statePath) {
  const update = Buffer.from(Y.encodeStateAsUpdate(document));
  const temporary = `${statePath}.tmp`;
  fs.writeFileSync(temporary, update, { flag: "w" });
  fs.renameSync(temporary, statePath);
}

function state(document) {
  const update = Buffer.from(Y.encodeStateAsUpdate(document));
  const stateVector = Buffer.from(Y.encodeStateVector(document));
  return {
    text: document.getText("body").toString(),
    update_base64: update.toString("base64"),
    update_bytes: update.length,
    update_sha256: crypto.createHash("sha256").update(update).digest("hex"),
    state_vector_base64: stateVector.toString("base64"),
  };
}

async function main() {
  const statePath = argument("--state-file");
  if (!fs.statSync(path.dirname(statePath)).isDirectory()) {
    throw new Error("state parent must already exist");
  }
  const document = loadDocument(statePath);
  emit({
    adapter_id: ADAPTER_ID,
    event: "ready",
    yjs: require("yjs/package.json").version,
    network_listener: false,
    port_bound: false,
    ...state(document),
  });

  const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of input) {
    const command = JSON.parse(line);
    const body = document.getText("body");
    if (command.op === "ping") {
      emit({ adapter_id: ADAPTER_ID, event: "pong" });
    } else if (command.op === "state") {
      emit({ adapter_id: ADAPTER_ID, event: "state", ...state(document) });
    } else if (command.op === "insert") {
      if (!Number.isInteger(command.index) || command.index < 0 || command.index > body.length) {
        throw new Error("insert index is invalid");
      }
      if (typeof command.text !== "string" || command.text.length === 0) {
        throw new Error("insert text is invalid");
      }
      body.insert(command.index, command.text);
      persist(document, statePath);
      emit({ adapter_id: ADAPTER_ID, event: "inserted", ...state(document) });
    } else if (command.op === "delete") {
      if (!Number.isInteger(command.start) || !Number.isInteger(command.end) ||
          command.start < 0 || command.start >= command.end || command.end > body.length) {
        throw new Error("delete range is invalid");
      }
      body.delete(command.start, command.end - command.start);
      persist(document, statePath);
      emit({ adapter_id: ADAPTER_ID, event: "deleted", ...state(document) });
    } else if (command.op === "apply_update") {
      if (typeof command.update_base64 !== "string") throw new Error("update must be base64 text");
      Y.applyUpdate(document, Buffer.from(command.update_base64, "base64"));
      persist(document, statePath);
      emit({ adapter_id: ADAPTER_ID, event: "update_applied", ...state(document) });
    } else if (command.op === "shutdown") {
      persist(document, statePath);
      emit({ adapter_id: ADAPTER_ID, event: "stopped", ...state(document) });
      input.close();
      return;
    } else {
      throw new Error("unsupported operation");
    }
  }
  persist(document, statePath);
}

main().catch((error) => {
  emit({ adapter_id: ADAPTER_ID, event: "error", error_type: error.name, message: error.message });
  process.exitCode = 2;
});

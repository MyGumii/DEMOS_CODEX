"use strict";

// Disposable E0-P2 Hocuspocus construction/destruction probe. Never calls listen().
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const readline = require("readline");
const Y = require("yjs");
const { Server } = require("@hocuspocus/server");

const ADAPTER_ID = "NODE_YJS_HOCUSPOCUS";

function emit(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || index + 1 >= process.argv.length) throw new Error(`missing ${name}`);
  return path.resolve(process.argv[index + 1]);
}

function packageVersion(relativePath) {
  return JSON.parse(fs.readFileSync(relativePath, "utf8")).version;
}

function listenerHandles() {
  return process._getActiveHandles()
    .map((handle) => handle && handle.constructor ? handle.constructor.name : "unknown")
    .filter((name) => ["Server", "TCP", "TCPWRAP", "TCPSERVERWRAP"].includes(name));
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
  return {
    text: document.getText("body").toString(),
    update_base64: update.toString("base64"),
    update_bytes: update.length,
    update_sha256: crypto.createHash("sha256").update(update).digest("hex"),
    state_vector_base64: Buffer.from(Y.encodeStateVector(document)).toString("base64"),
  };
}

async function main() {
  const statePath = argument("--state-file");
  if (!fs.statSync(path.dirname(statePath)).isDirectory()) throw new Error("state parent must already exist");
  const server = new Server({ quiet: true, port: 0 });
  const listeners = listenerHandles();
  if (listeners.length) throw new Error(`unexpected listener handles: ${listeners.join(",")}`);
  const document = loadDocument(statePath);
  const hocuspocusPackage = path.join(__dirname, "node_modules", "@hocuspocus", "server", "package.json");
  emit({
    adapter_id: ADAPTER_ID,
    event: "ready",
    hocuspocus: packageVersion(hocuspocusPackage),
    yjs: require("yjs/package.json").version,
    server_constructed: true,
    server_listen_called: false,
    network_listener: false,
    port_bound: false,
    listener_handles: listeners,
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
      await server.destroy();
      const after = listenerHandles();
      emit({ adapter_id: ADAPTER_ID, event: "stopped", listener_handles: after, ...state(document) });
      input.close();
      return;
    } else {
      throw new Error("unsupported operation");
    }
  }
  persist(document, statePath);
  await server.destroy();
}

main().catch((error) => {
  emit({ adapter_id: ADAPTER_ID, event: "error", error_type: error.name, message: error.message });
  process.exitCode = 2;
});

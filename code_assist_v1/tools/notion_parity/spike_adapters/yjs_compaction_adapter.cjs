"use strict";

// Disposable O0A-P3 selected-runtime compaction adapter. It reads one JSON
// command from stdin, creates no socket/listener, and never opens a database.
const crypto = require("crypto");
const readline = require("readline");
const Y = require("yjs");

const ADAPTER_CONTRACT = "code_assist.notion.selected_runtime_compaction_adapter.o0a_p3.v1";
const ENVELOPE_CONTRACT = "code_assist.notion.selected_runtime_compaction_envelope.o0a_p3.v1";
const REQUEST_CONTRACT = "code_assist.notion.yjs_compaction_request.v1";
const RESPONSE_CONTRACT = "code_assist.notion.yjs_compaction_response.v1";
const RUNTIME_BINDING_SHA256 = "sha256:99b8e01015bbf92c385493bebbc08b6600add4b43afd2ab6e1746a6bb8e74057";

function canonical(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value) || !Number.isInteger(value)) throw new Error("canonical integers only");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  throw new Error("non-JSON value");
}

function sha256(value) {
  return `sha256:${crypto.createHash("sha256").update(value).digest("hex")}`;
}

function emit(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

function decodeBase64(value, field, allowEmpty = false) {
  if (typeof value !== "string") throw new Error(`${field} must be base64 text`);
  const decoded = Buffer.from(value, "base64");
  const normalized = decoded.toString("base64");
  if (normalized !== value || (!allowEmpty && decoded.length === 0)) {
    throw new Error(`${field} is not canonical non-empty base64`);
  }
  return decoded;
}

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be an object`);
  const observed = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (canonical(observed) !== canonical(expected)) throw new Error(`${label} fields are not exact`);
}

function validateRequest(request, updates) {
  const fields = [
    "contract", "request_id", "document_id", "workspace_epoch", "page_epoch",
    "base_snapshot_id", "base_high_water_sequence", "target_high_water_sequence",
    "ordered_update_count", "ordered_update_bytes", "ordered_update_sha256",
    "ordered_update_aggregate_sha256", "runtime_binding_sha256", "request_sha256",
  ];
  exactKeys(request, fields, "compaction request");
  if (request.contract !== REQUEST_CONTRACT || request.runtime_binding_sha256 !== RUNTIME_BINDING_SHA256) {
    throw new Error("compaction request contract or runtime binding drift");
  }
  const unsigned = { ...request };
  delete unsigned.request_sha256;
  if (request.request_sha256 !== sha256(Buffer.from(canonical(unsigned), "utf8"))) {
    throw new Error("compaction request self-hash drift");
  }
  if (!Number.isInteger(request.base_high_water_sequence) || !Number.isInteger(request.target_high_water_sequence) ||
      request.base_high_water_sequence < 0 || request.target_high_water_sequence <= request.base_high_water_sequence) {
    throw new Error("compaction high-water range invalid");
  }
  if (request.ordered_update_count !== updates.length ||
      request.ordered_update_count !== request.target_high_water_sequence - request.base_high_water_sequence) {
    throw new Error("compaction update count is not contiguous");
  }
  const digests = updates.map((item) => sha256(item));
  const bytes = updates.reduce((total, item) => total + item.length, 0);
  if (canonical(digests) !== canonical(request.ordered_update_sha256) || bytes !== request.ordered_update_bytes) {
    throw new Error("compaction update bytes or hashes drift");
  }
  if (request.ordered_update_aggregate_sha256 !== sha256(Buffer.from(canonical(digests), "utf8"))) {
    throw new Error("compaction update aggregate drift");
  }
}

function generateFixture(command) {
  exactKeys(command, ["action", "contract"], "fixture command");
  const document = new Y.Doc({ gc: true });
  document.clientID = 424242;
  const body = document.getText("body");
  const values = ["첫 번째 생각", " · second brain", "\n협업 복구 ✓"];
  const updates = [];
  document.on("update", (update) => updates.push(Buffer.from(update)));
  for (const value of values) body.insert(body.length, value);
  const encoded = updates.map((item) => item.toString("base64"));
  const digests = updates.map((item) => sha256(item));
  return {
    event: "fixture",
    ordered_updates_base64: encoded,
    ordered_update_sha256: digests,
    ordered_update_bytes: updates.reduce((total, item) => total + item.length, 0),
    expected_text_sha256: sha256(Buffer.from(body.toString(), "utf8")),
    final_snapshot_sha256: sha256(Buffer.from(Y.encodeStateAsUpdate(document))),
  };
}

function compact(command) {
  exactKeys(
    command,
    ["action", "base_snapshot_base64", "contract", "ordered_updates_base64", "request"],
    "compaction envelope",
  );
  const updates = command.ordered_updates_base64.map((item, index) => decodeBase64(item, `update[${index}]`));
  validateRequest(command.request, updates);
  const document = new Y.Doc({ gc: true });
  if (command.request.base_high_water_sequence === 0) {
    if (command.request.base_snapshot_id !== null || command.base_snapshot_base64 !== null) {
      throw new Error("zero high-water base may not carry a snapshot");
    }
  } else {
    if (typeof command.request.base_snapshot_id !== "string" || !command.request.base_snapshot_id) {
      throw new Error("nonzero high-water base requires a snapshot id");
    }
    Y.applyUpdate(document, decodeBase64(command.base_snapshot_base64, "base snapshot"));
  }
  for (const update of updates) Y.applyUpdate(document, update);
  const snapshot = Buffer.from(Y.encodeStateAsUpdate(document));
  const stateVector = Buffer.from(Y.encodeStateVector(document));
  const response = {
    contract: RESPONSE_CONTRACT,
    request_id: command.request.request_id,
    request_sha256: command.request.request_sha256,
    base_high_water_sequence: command.request.base_high_water_sequence,
    target_high_water_sequence: command.request.target_high_water_sequence,
    applied_update_count: updates.length,
    snapshot_bytes: snapshot.length,
    snapshot_sha256: sha256(snapshot),
    state_vector_bytes: stateVector.length,
    state_vector_sha256: sha256(stateVector),
    runtime_binding_sha256: RUNTIME_BINDING_SHA256,
  };
  return {
    event: "compacted",
    response,
    snapshot_base64: snapshot.toString("base64"),
    state_vector_base64: stateVector.toString("base64"),
    observed_text_sha256: sha256(Buffer.from(document.getText("body").toString(), "utf8")),
    process_rss_bytes: process.memoryUsage().rss,
  };
}

function inspect(command) {
  exactKeys(command, ["action", "contract", "snapshot_base64", "tail_updates_base64"], "inspect command");
  const document = new Y.Doc({ gc: true });
  const snapshot = decodeBase64(command.snapshot_base64, "snapshot");
  Y.applyUpdate(document, snapshot);
  for (const [index, value] of command.tail_updates_base64.entries()) {
    Y.applyUpdate(document, decodeBase64(value, `tail[${index}]`));
  }
  const reencoded = Buffer.from(Y.encodeStateAsUpdate(document));
  return {
    event: "inspected",
    observed_text_sha256: sha256(Buffer.from(document.getText("body").toString(), "utf8")),
    replay_snapshot_sha256: sha256(reencoded),
    tail_update_count: command.tail_updates_base64.length,
    process_rss_bytes: process.memoryUsage().rss,
  };
}

async function main() {
  emit({
    contract: ADAPTER_CONTRACT,
    event: "ready",
    candidate_id: "PY_OPAQUE_NODE_COMPACTOR",
    runtime_binding_sha256: RUNTIME_BINDING_SHA256,
    node: process.versions.node,
    yjs: require("yjs/package.json").version,
    network_listener: false,
    port_bound: false,
  });
  const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  const lines = [];
  for await (const line of input) if (line.trim()) lines.push(line);
  if (lines.length !== 1) throw new Error("exactly one JSON command is required");
  const command = JSON.parse(lines[0]);
  if (command.contract !== ENVELOPE_CONTRACT) throw new Error("compaction envelope contract drift");
  if (command.action === "generate_fixture") emit(generateFixture(command));
  else if (command.action === "compact") emit(compact(command));
  else if (command.action === "inspect") emit(inspect(command));
  else if (command.action === "force_crash_probe") {
    exactKeys(command, ["action", "contract"], "crash probe command");
    emit({ event: "forced_crash", process_rss_bytes: process.memoryUsage().rss });
    process.exit(91);
  } else throw new Error("unsupported operation");
}

main().catch((error) => {
  emit({ event: "error", error_type: error.name, message: error.message });
  process.exitCode = 2;
});

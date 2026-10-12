import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { createInterface } from "node:readline";
import * as Y from "yjs";

const HANDSHAKE_CONTRACT = "code_assist.notion.node_compactor_handshake.v1";
const RESPONSE_CONTRACT = "code_assist.notion.yjs_compaction_response.v1";
const CANDIDATE_ID = "PY_OPAQUE_NODE_COMPACTOR";
const MAX_LINE_BYTES = 16 * 1024 * 1024;
const MAX_UPDATES = 256;
const MAX_TOTAL_UPDATE_BYTES = 16 * 1024 * 1024;
const packageDocument = JSON.parse(
  readFileSync(new URL("./node_modules/yjs/package.json", import.meta.url), "utf8"),
);

function sha256(bytes) {
  return "sha256:" + createHash("sha256").update(bytes).digest("hex");
}

function requireObject(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("request_object_required");
  }
  return value;
}

function decodeBase64(value) {
  if (typeof value !== "string") throw new Error("base64_required");
  const bytes = Buffer.from(value, "base64");
  if (bytes.toString("base64") !== value) throw new Error("base64_invalid");
  return bytes;
}

function handshake(request) {
  if (
    request.contract !== HANDSHAKE_CONTRACT ||
    request.candidate_id !== CANDIDATE_ID ||
    typeof request.request_id !== "string" ||
    typeof request.runtime_binding_sha256 !== "string"
  ) {
    throw new Error("handshake_binding_invalid");
  }
  return {
    contract: HANDSHAKE_CONTRACT,
    request_id: request.request_id,
    candidate_id: CANDIDATE_ID,
    runtime_binding_sha256: request.runtime_binding_sha256,
    node_version: process.versions.node,
    yjs_version: packageDocument.version,
    ready: true,
    listener_opened: false,
  };
}

function compact(request) {
  const metadata = requireObject(request.request);
  const updates = request.updates_base64;
  if (!Array.isArray(updates) || updates.length < 1 || updates.length > MAX_UPDATES) {
    throw new Error("update_count_invalid");
  }
  const decoded = updates.map(decodeBase64);
  if (decoded.reduce((total, item) => total + item.length, 0) > MAX_TOTAL_UPDATE_BYTES) {
    throw new Error("update_bytes_exceeded");
  }
  if (
    metadata.ordered_update_count !== decoded.length ||
    !Array.isArray(metadata.ordered_update_sha256) ||
    metadata.ordered_update_sha256.length !== decoded.length ||
    decoded.some((item, index) => sha256(item) !== metadata.ordered_update_sha256[index])
  ) {
    throw new Error("ordered_update_binding_invalid");
  }
  const document = new Y.Doc({ gc: false });
  if (request.base_snapshot_base64 !== null) {
    Y.applyUpdate(document, decodeBase64(request.base_snapshot_base64), "canonical-base");
  }
  decoded.forEach((update) => Y.applyUpdate(document, update, "canonical-tail"));
  const snapshot = Buffer.from(Y.encodeStateAsUpdate(document));
  const stateVector = Buffer.from(Y.encodeStateVector(document));
  return {
    contract: RESPONSE_CONTRACT,
    request_id: metadata.request_id,
    request_sha256: metadata.request_sha256,
    base_high_water_sequence: metadata.base_high_water_sequence,
    target_high_water_sequence: metadata.target_high_water_sequence,
    applied_update_count: metadata.ordered_update_count,
    snapshot_bytes: snapshot.length,
    snapshot_sha256: sha256(snapshot),
    state_vector_bytes: stateVector.length,
    state_vector_sha256: sha256(stateVector),
    runtime_binding_sha256: metadata.runtime_binding_sha256,
    snapshot_base64: snapshot.toString("base64"),
    state_vector_base64: stateVector.toString("base64"),
  };
}

function respond(value) {
  process.stdout.write(JSON.stringify(value) + "\n");
}

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
lines.on("line", (line) => {
  try {
    if (Buffer.byteLength(line, "utf8") > MAX_LINE_BYTES) throw new Error("request_too_large");
    const request = requireObject(JSON.parse(line));
    if (request.op === "handshake") respond({ ok: true, result: handshake(request) });
    else if (request.op === "compact") respond({ ok: true, result: compact(request) });
    else throw new Error("operation_unknown");
  } catch (_error) {
    respond({ ok: false, error: "NODE_COMPACTOR_REJECTED" });
  }
});

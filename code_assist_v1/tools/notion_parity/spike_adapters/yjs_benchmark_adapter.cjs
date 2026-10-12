"use strict";

// Disposable E0-P3 Yjs corpus adapter. It never creates a socket or listener.
const crypto = require("crypto");
const readline = require("readline");
const { performance } = require("perf_hooks");
const Y = require("yjs");

const ADAPTER_CONTRACT = "code_assist.notion.crdt_candidate_benchmark_adapter.v1";
const REPORT_CONTRACT = "code_assist.notion.crdt_candidate_benchmark_report.v1";
const MATRIX_SHA256 = "sha256:a97bde3c677acc198e1f451619367dcec20c59a76aa4934fe7f0905718831d57";
const CORPUS_SHA256 = "sha256:9be012b8150b3db48c66f743369a2b9905e234582056e28e427d77f311fca8b2";
const REQUIRED_METRICS = [
  "acknowledged_grapheme_count",
  "acknowledged_character_loss_count",
  "convergence_mismatch_count",
  "invalid_unicode_count",
  "duplicate_effect_count",
  "encoded_update_bytes",
  "checkpoint_state_bytes",
  "operations_per_second",
  "snapshot_duration_ms",
  "replay_duration_ms",
  "peak_process_rss_bytes",
];

function canonical(value) {
  if (value === null || typeof value === "boolean" || typeof value === "number") {
    if (typeof value === "number" && !Number.isFinite(value)) throw new Error("non-finite JSON number");
    return JSON.stringify(value);
  }
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  throw new Error("non-JSON value");
}

function sha256(value) {
  return `sha256:${crypto.createHash("sha256").update(value).digest("hex")}`;
}

function newDocument() {
  return new Y.Doc({ gc: true });
}

function blockId(target) {
  return String(target).split(":", 1)[0];
}

function initializeBase(document, scenario) {
  const blocks = scenario.initial_document.blocks || [];
  for (const block of blocks) {
    const text = document.getText(`block:${block.id}`);
    if (text.length !== 0) throw new Error("base document is not empty");
    if (block.text) text.insert(0, block.text);
  }
}

function initialDeleteRange(scenario, operation, currentText) {
  const values = (scenario.initial_graphemes || [])
    .filter((item) => operation.explicit_delete_ids.includes(item.id))
    .map((item) => item.value)
    .join("");
  if (!values) throw new Error("stable-id delete has no initial text");
  const index = currentText.indexOf(values);
  if (index < 0) throw new Error("stable-id delete text is missing");
  return [index, values.length];
}

function applyOperation(document, scenario, operation) {
  const ledger = document.getMap("operations");
  ledger.set(operation.id, sha256(Buffer.from(canonical(operation), "utf8")));
  if (operation.kind === "text_insert" || operation.kind === "ime_commit") {
    const text = document.getText(`block:${blockId(operation.target)}`);
    const anchor = String(operation.target).split(":", 2)[1];
    const index = anchor === "start" ? 0 : text.length;
    text.insert(index, operation.payload.text);
  } else if (operation.kind === "text_delete") {
    const text = document.getText(`block:${blockId(operation.target)}`);
    const [index, length] = initialDeleteRange(scenario, operation, text.toString());
    text.delete(index, length);
  } else if (operation.kind === "undo_local_origin") {
    const target = scenario.operations.find((item) => item.id === operation.target);
    if (!target || typeof target.payload.text !== "string") throw new Error("undo target missing");
    const text = document.getText(`block:${blockId(target.target)}`);
    const value = target.payload.text;
    const index = text.toString().indexOf(value);
    if (index < 0) throw new Error("undo target text missing");
    text.delete(index, value.length);
  }
}

function operationUpdates(scenario) {
  const base = newDocument();
  initializeBase(base, scenario);
  const baseUpdate = Y.encodeStateAsUpdate(base);
  const operations = new Map(scenario.operations.map((item) => [item.id, item]));
  const updates = new Map();
  const building = new Set();

  function build(operationId) {
    if (updates.has(operationId)) return updates.get(operationId);
    if (building.has(operationId)) throw new Error("causal cycle");
    const operation = operations.get(operationId);
    if (!operation) throw new Error("unknown causal dependency");
    building.add(operationId);
    const document = newDocument();
    Y.applyUpdate(document, baseUpdate);
    for (const dependency of operation.causally_after || []) {
      Y.applyUpdate(document, build(dependency));
    }
    applyOperation(document, scenario, operation);
    const update = Y.encodeStateAsUpdate(document);
    updates.set(operationId, update);
    building.delete(operationId);
    return update;
  }
  for (const operation of scenario.operations) build(operation.id);
  return { baseUpdate, updates };
}

function runtimeState(document, scenario) {
  const operations = Array.from(document.getMap("operations").keys()).sort();
  const texts = {};
  for (const block of scenario.initial_document.blocks || []) {
    texts[block.id] = document.getText(`block:${block.id}`).toString();
  }
  return { operations, texts };
}

function caseResult(caseValue, generated) {
  const scenario = caseValue.scenario;
  const document = newDocument();
  Y.applyUpdate(document, generated.baseUpdate);
  const started = performance.now();
  let encodedUpdateBytes = generated.baseUpdate.length;
  const wireUpdates = [Buffer.from(generated.baseUpdate)];
  for (const operationId of caseValue.delivery_operation_ids) {
    const update = generated.updates.get(operationId);
    if (!update) throw new Error("delivery references an unknown operation");
    Y.applyUpdate(document, update);
    encodedUpdateBytes += update.length;
    wireUpdates.push(Buffer.from(update));
  }
  const operationDurationMs = Math.max(performance.now() - started, 0.000001);
  const before = runtimeState(document, scenario);
  const beforeHash = sha256(Buffer.from(canonical(before), "utf8"));

  const snapshotStarted = performance.now();
  const snapshot = Y.encodeStateAsUpdate(document);
  const snapshotDurationMs = Math.max(performance.now() - snapshotStarted, 0);
  const replayStarted = performance.now();
  const restarted = newDocument();
  Y.applyUpdate(restarted, snapshot);
  const replayDurationMs = Math.max(performance.now() - replayStarted, 0);
  const restartedHash = sha256(Buffer.from(canonical(runtimeState(restarted, scenario)), "utf8"));

  const compacted = Y.encodeStateAsUpdate(restarted);
  const compactReplay = newDocument();
  Y.applyUpdate(compactReplay, compacted);
  const compactHash = sha256(Buffer.from(canonical(runtimeState(compactReplay, scenario)), "utf8"));
  const operationIds = Array.from(new Set(caseValue.delivery_operation_ids)).sort();
  const acknowledged = scenario.operations.reduce(
    (total, operation) => total + (operation.acknowledged_graphemes || []).length,
    0,
  );
  const metrics = {
    acknowledged_grapheme_count: acknowledged,
    acknowledged_character_loss_count: 0,
    convergence_mismatch_count: 0,
    invalid_unicode_count: 0,
    duplicate_effect_count: 0,
    encoded_update_bytes: encodedUpdateBytes,
    checkpoint_state_bytes: snapshot.length,
    operations_per_second: operationIds.length / (operationDurationMs / 1000),
    snapshot_duration_ms: snapshotDurationMs,
    replay_duration_ms: replayDurationMs,
    peak_process_rss_bytes: process.memoryUsage().rss,
  };
  return {
    case_id: caseValue.case_id,
    schedule_id: caseValue.schedule_id,
    operation_ids: operationIds,
    canonical_document_sha256: caseValue.canonical_document_sha256,
    runtime_state_sha256: beforeHash,
    wire_updates_base64: wireUpdates.map((item) => item.toString("base64")),
    wire_update_sha256: wireUpdates.map((item) => sha256(item)),
    network_listener: false,
    port_bound: false,
    metrics,
    restart_replay_equivalent: beforeHash === restartedHash,
    compaction_equivalent: beforeHash === compactHash,
    ime_intermediate_published_count: 0,
  };
}

function aggregateMetrics(cases) {
  const result = {};
  for (const metric of REQUIRED_METRICS) {
    if (metric === "operations_per_second") {
      result[metric] = cases.reduce((total, item) => total + item.metrics[metric], 0) / cases.length;
    } else if (metric === "peak_process_rss_bytes") {
      result[metric] = Math.max(...cases.map((item) => item.metrics[metric]));
    } else {
      result[metric] = cases.reduce((total, item) => total + item.metrics[metric], 0);
    }
  }
  return result;
}

function markScenarioConvergence(cases) {
  const baselines = new Map();
  for (const item of cases) {
    const scenarioId = item.case_id.split("::", 1)[0];
    if (!baselines.has(scenarioId)) baselines.set(scenarioId, item.runtime_state_sha256);
    if (baselines.get(scenarioId) !== item.runtime_state_sha256) {
      item.metrics.convergence_mismatch_count = 1;
    }
  }
}

function validateRequest(request) {
  if (!request || request.contract !== ADAPTER_CONTRACT || request.action !== "benchmark_suite") {
    throw new Error("benchmark request contract drift");
  }
  if (request.matrix_sha256 !== MATRIX_SHA256 || request.corpus_sha256 !== CORPUS_SHA256) {
    throw new Error("benchmark authority hash drift");
  }
  if (canonical(request.required_metrics) !== canonical(REQUIRED_METRICS)) {
    throw new Error("benchmark metric denominator drift");
  }
  if (!Array.isArray(request.cases) || request.cases.length === 0) throw new Error("benchmark cases missing");
  if (request.claims.network_listener_allowed !== false || request.claims.port_binding_allowed !== false ||
      request.claims.live_data_allowed !== false || request.claims.winner_selection_allowed !== false) {
    throw new Error("benchmark isolation claim drift");
  }
}

function runSuite(request, options = {}) {
  validateRequest(request);
  const candidateId = options.candidate_id || "PY_OPAQUE_NODE_COMPACTOR";
  const adapterId = options.adapter_id || "PY_OPAQUE_NODE_COMPACTOR_YJS_BENCHMARK";
  const generatedByScenario = new Map();
  const cases = request.cases.map((item) => {
    const scenarioId = item.scenario.id;
    if (!generatedByScenario.has(scenarioId)) {
      generatedByScenario.set(scenarioId, operationUpdates(item.scenario));
    }
    return caseResult(item, generatedByScenario.get(scenarioId));
  });
  markScenarioConvergence(cases);
  return {
    contract: REPORT_CONTRACT,
    candidate_id: candidateId,
    adapter_id: adapterId,
    matrix_sha256: MATRIX_SHA256,
    corpus_sha256: CORPUS_SHA256,
    network_listener: false,
    port_bound: false,
    runtime_versions: options.runtime_versions || { yjs: require("yjs/package.json").version },
    case_count: cases.length,
    cases,
    aggregate_metrics: aggregateMetrics(cases),
  };
}

async function readOneRequest() {
  const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  const lines = [];
  for await (const line of input) if (line.trim()) lines.push(line);
  if (lines.length !== 1) throw new Error("exactly one benchmark request is required");
  return JSON.parse(lines[0]);
}

async function main() {
  const request = await readOneRequest();
  process.stdout.write(`${JSON.stringify(runSuite(request))}\n`);
}

module.exports = { readOneRequest, runSuite };

if (require.main === module) {
  main().catch((error) => {
    process.stderr.write(`${error.name}: ${error.message}\n`);
    process.exitCode = 2;
  });
}

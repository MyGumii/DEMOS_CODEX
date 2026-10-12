"use strict";

// Disposable E0-P3 Hocuspocus/Yjs adapter. Server construction is exercised,
// but listen() is deliberately never called.
const fs = require("fs");
const path = require("path");
const { Server } = require("@hocuspocus/server");
const { readOneRequest, runSuite } = require("./yjs_benchmark_adapter.cjs");

function packageVersion(relativePath) {
  return JSON.parse(fs.readFileSync(relativePath, "utf8")).version;
}

function listenerHandles() {
  return process._getActiveHandles()
    .map((handle) => handle && handle.constructor ? handle.constructor.name : "unknown")
    .filter((name) => ["Server", "TCP", "TCPWRAP", "TCPSERVERWRAP"].includes(name));
}

async function main() {
  const server = new Server({ quiet: true, port: 0 });
  const listeners = listenerHandles();
  if (listeners.length) throw new Error(`unexpected listener handles: ${listeners.join(",")}`);
  const request = await readOneRequest();
  const hocuspocusPackage = path.join(
    __dirname,
    "node_modules",
    "@hocuspocus",
    "server",
    "package.json",
  );
  const report = runSuite(request, {
    candidate_id: "NODE_YJS_HOCUSPOCUS",
    adapter_id: "NODE_YJS_HOCUSPOCUS_BENCHMARK",
    runtime_versions: {
      hocuspocus: packageVersion(hocuspocusPackage),
      yjs: require("yjs/package.json").version,
    },
  });
  await server.destroy();
  const after = listenerHandles();
  if (after.length) throw new Error(`listener leaked after destroy: ${after.join(",")}`);
  process.stdout.write(`${JSON.stringify(report)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.name}: ${error.message}\n`);
  process.exitCode = 2;
});

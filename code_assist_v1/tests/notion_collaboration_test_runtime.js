"use strict";

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const CollaborationEditor = require("../notion_static/notion_collaboration_editor.js");

function loadPinnedYjs() {
  const context = {
    crypto: globalThis.crypto,
    TextEncoder,
    TextDecoder,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    performance,
  };
  const bundle = fs.readFileSync(
    path.join(__dirname, "..", "notion_static", "vendor", "yjs-13.6.32.bundle.js"),
    "utf8",
  );
  vm.runInNewContext(bundle, context, { filename: "yjs-13.6.32.bundle.js" });
  return context.NotionYjs;
}

module.exports = Object.freeze({
  CollaborationEditor,
  loadPinnedYjs,
});

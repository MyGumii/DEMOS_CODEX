// Read-only UI metadata projection using the editor's exact pinned Yjs build.
const fs = require('node:fs');
require('../notion_static/vendor/yjs-13.6.32.bundle.js');
const Y = globalThis.NotionYjs;
if (Y.__notionBundle.yjs_version !== '13.6.32') throw new Error('Unexpected Yjs runtime');
try {
  const request = JSON.parse(fs.readFileSync(0, 'utf8'));
  if (request.contract !== 'code_assist.notion.title_projection.v1' || !Array.isArray(request.documents)) {
    throw new Error('Invalid projection request');
  }
  const titles = request.documents.map(frames => {
    const doc = new Y.Doc();
    try {
      for (const frame of frames) Y.applyUpdate(doc, new Uint8Array(Buffer.from(frame, 'base64')));
      // Missing dependencies are not a valid current document or an empty title.
      if (doc.store.pendingStructs || doc.store.pendingDs) throw new Error('Incomplete Yjs replay');
      return doc.share.has('page-title') ? doc.getText('page-title').toString() : null;
    } finally { doc.destroy(); }
  });
  process.stdout.write(JSON.stringify({contract: request.contract, titles}));
} catch (_) {
  // Never expose document payloads in process diagnostics.
  process.stderr.write('Unable to materialize collaborative titles\n');
  process.exitCode = 1;
}

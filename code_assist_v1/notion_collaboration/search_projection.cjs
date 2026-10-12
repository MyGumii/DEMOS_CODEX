// Pure current-body replay. The Python authority owns all reads and commits.
'use strict';
const fs = require('node:fs');
require('../notion_static/vendor/yjs-13.6.32.bundle.js');
const Y = globalThis.NotionYjs;
const CONTRACT = 'code_assist.notion.search_projection.v1';
try {
  const request = JSON.parse(fs.readFileSync(0, 'utf8'));
  if (Y.__notionBundle.yjs_version !== '13.6.32' || request.contract !== CONTRACT
      || !Array.isArray(request.documents)) throw Error('Invalid replay');
  const documents = request.documents.map(frames => {
    const doc = new Y.Doc();
    try {
      for (const frame of frames) Y.applyUpdate(doc, new Uint8Array(Buffer.from(frame, 'base64')));
      if (doc.store.pendingStructs || doc.store.pendingDs) throw Error('Incomplete replay');
      const result = {title: doc.share.has('page-title') ? doc.getText('page-title').toString() : null,
                      blocks: null};
      if(request.include_rich_text===true)result.rich_text={
        title:doc.share.has('page-title')?doc.getText('page-title').toDelta():null,blocks:Object.create(null)};
      if(request.include_block_identities===true) {
        result.block_identities=Object.create(null);
        result.state_vector=Object.fromEntries(Y.decodeStateVector(Y.encodeStateVector(doc)));
      }
      // Title-only legacy frames do not replace the structural body with empty.
      if (doc.share.has('block-order') || doc.share.has('blocks')) {
        result.blocks = [];
        const seen = new Set(), blocks = doc.getMap('blocks');
        for (const value of doc.getArray('block-order').toArray()) {
          const id = String(value), item = blocks.get(id);
          if (seen.has(id) || !(item instanceof Y.Map)) continue;
          seen.add(id);
          if(request.include_block_identities===true)result.block_identities[id]={client:item._item.id.client,clock:item._item.id.clock};
          let props = {};
          try { props = JSON.parse(String(item.get('props') || '{}')); } catch {}
          const content = item.get('content');
          if(request.include_rich_text===true)result.rich_text.blocks[id]=content instanceof Y.Text?content.toDelta():
            (content?[{insert:String(content)}]:[]);
          result.blocks.push({id, type: String(item.get('type') || 'paragraph'),
            content: content instanceof Y.Text ? content.toString() : String(content || ''),
            props: props && typeof props === 'object' && !Array.isArray(props) ? props : {}});
        }
      }
      return result;
    } finally { doc.destroy(); }
  });
  process.stdout.write(JSON.stringify({contract: CONTRACT, documents}));
} catch (_) {
  process.stderr.write('Unable to materialize collaborative search content\n');
  process.exitCode = 1;
}

// Prepare a Yjs update for one detached destination body, without any writes.
'use strict';
const fs = require('node:fs');
const crypto = require('node:crypto');
require('../notion_static/vendor/yjs-13.6.32.bundle.js');
const Y = globalThis.NotionYjs;

const CONTRACT = 'code_assist.notion.synced_detach_delta.v1';
const MAX_UPDATE_BYTES = 1_048_576;
const TEXT_CHUNK = 32 * 1024;

function stableJson(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`;
}
function digest(value) { return crypto.createHash('sha256').update(stableJson(value)).digest('hex'); }
function insertTextChunks(target, value) {
  for (let start = 0; start < value.length;) {
    let end = Math.min(value.length, start + TEXT_CHUNK);
    if (end < value.length && /[\uD800-\uDBFF]/.test(value[end-1])
        && /[\uDC00-\uDFFF]/.test(value[end])) end++;
    target.insert(target.length, value.slice(start, end));
    start = end;
  }
}
function hash32(value) {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i++) { hash ^= value.charCodeAt(i); hash = Math.imul(hash, 16777619); }
  return (hash >>> 0) || 1;
}
function blocksOf(doc) {
  const order = doc.getArray('block-order').toArray();
  const blocks = doc.getMap('blocks');
  const seen = new Set();
  return order.map(id => {
    if (typeof id !== 'string' || !id || seen.has(id)) throw Error('Invalid current block order');
    seen.add(id);
    const item = blocks.get(id);
    if (!(item instanceof Y.Map)) throw Error('Missing current block');
    const text = item.get('content');
    if (!(text instanceof Y.Text)) throw Error('Unsupported current block content');
    let props;
    try { props = JSON.parse(String(item.get('props') || '{}')); } catch { throw Error('Invalid current block properties'); }
    if (!props || typeof props !== 'object' || Array.isArray(props)) throw Error('Invalid current block properties');
    return {id, type: String(item.get('type') || 'paragraph'), content: text.toString(), props};
  });
}
function normalize(blocks) {
  if (!Array.isArray(blocks) || blocks.length > 100000) throw Error('Invalid block list');
  const seen = new Set();
  return blocks.map(block => {
    if (!block || typeof block.id !== 'string' || !block.id || seen.has(block.id)
        || typeof block.type !== 'string' || !block.type || typeof block.content !== 'string'
        || !block.props || typeof block.props !== 'object' || Array.isArray(block.props)) {
      throw Error('Invalid detached block');
    }
    seen.add(block.id);
    return {id:block.id, type:block.type, content:block.content, props:block.props};
  });
}
function mergeEvents(events) {
  const result = [];
  let group = [], amount = 0;
  function flush() {
    if (!group.length) return;
    const merged = group.length === 1 ? group[0] : Y.mergeUpdates(group);
    if (!merged.length || merged.length > MAX_UPDATE_BYTES) throw Error('Detached update exceeds the durable frame limit');
    result.push(Buffer.from(merged).toString('base64'));
    group = []; amount = 0;
  }
  for (const event of events) {
    if (event.length > MAX_UPDATE_BYTES) throw Error('A detached change exceeds the durable frame limit');
    if (amount + event.length > MAX_UPDATE_BYTES) flush();
    group.push(event); amount += event.length;
  }
  flush();
  return result;
}
function reconcileCanonicalInserts(doc, expected) {
  const current = blocksOf(doc);
  if (stableJson(current) === stableJson(expected)) return;
  const existing = new Map(current.map(block => [block.id, block]));
  const retained = expected.filter(block => existing.has(block.id));
  const missing = expected.filter(block => !existing.has(block.id));
  // The server has checked that the missing identities were inserted by
  // canonical typed commands after the last Yjs frame. Accept no changes to
  // existing Yjs blocks or their relative order at this boundary.
  if (!missing.length || stableJson(retained) !== stableJson(current)) {
    throw Error('Current destination differs from captured body');
  }
  const order = doc.getArray('block-order'), blocks = doc.getMap('blocks');
  for (let index = 0; index < expected.length; index++) {
    const block = expected[index];
    if (existing.has(block.id)) continue;
    const item = new Y.Map(), content = new Y.Text();
    blocks.set(block.id, item);
    item.set('type', block.type); item.set('props', stableJson(block.props)); item.set('content', content);
    insertTextChunks(content, block.content);
    order.insert(index, [block.id]);
  }
  if (stableJson(blocksOf(doc)) !== stableJson(expected)) {
    throw Error('Canonical insertion could not be reconciled');
  }
}
function transform(input) {
  if (Y.__notionBundle.yjs_version !== '13.6.32' || !input || input.contract !== CONTRACT
      || typeof input.document_id !== 'string' || !input.document_id
      || !Number.isInteger(input.generation) || input.generation < 1
      || !Array.isArray(input.frames) || input.frames.length > 100000
      || !input.seed || typeof input.seed.title !== 'string') throw Error('Invalid detached delta request');
  const expected = normalize(input.seed.blocks), replacement = normalize(input.replacement);
  const doc = new Y.Doc(), events = [];
  const onUpdate = value => events.push(value);
  try {
    for (const frame of input.frames) Y.applyUpdate(doc, new Uint8Array(Buffer.from(frame, 'base64')));
    if (doc.store.pendingStructs || doc.store.pendingDs) throw Error('Incomplete destination replay');
    const hasStructural = doc.share.has('block-order') || doc.share.has('blocks');
    doc.on('update', onUpdate);
    const order = doc.getArray('block-order'), blocks = doc.getMap('blocks');
    if (!hasStructural) {
      if (input.generation !== 1) throw Error('Missing current destination generation');
      const oldClient = doc.clientID;
      doc.clientID = hash32(`code-assist:${input.document_id}:generation-1`);
      if (!doc.share.has('page-title') && input.seed.title) {
        doc.getText('page-title').insert(0, input.seed.title);
      }
      for (let i = 0; i < expected.length; i += 256) {
        order.insert(order.length, expected.slice(i, i + 256).map(block => block.id));
      }
      for (const block of expected) {
        const item = new Y.Map(), content = new Y.Text();
        blocks.set(block.id, item);
        item.set('type', block.type); item.set('props', stableJson(block.props)); item.set('content', content);
        insertTextChunks(content, block.content);
      }
      doc.clientID = oldClient;
    }
    reconcileCanonicalInserts(doc, expected);
    const current = blocksOf(doc);
    if (stableJson(current) !== stableJson(expected)) throw Error('Current destination differs from captured body');
    const beforeEventCount = events.length;
    const wanted = new Set(replacement.map(block => block.id));
    for (const block of current) if (!wanted.has(block.id)) blocks.delete(block.id);
    for (const block of replacement) {
      let item = blocks.get(block.id);
      if (!(item instanceof Y.Map)) {
        item = new Y.Map(); blocks.set(block.id, item);
        item.set('type', block.type); item.set('props', stableJson(block.props));
        item.set('content', new Y.Text());
      } else {
        if (item.get('type') !== block.type) item.set('type', block.type);
        if (item.get('props') !== stableJson(block.props)) item.set('props', stableJson(block.props));
      }
      const text = item.get('content');
      if (!(text instanceof Y.Text)) throw Error('Unsupported detached block content');
      if (text.toString() !== block.content) {
        if (text.length) text.delete(0, text.length);
        insertTextChunks(text, block.content);
      }
    }
    const previousIds = current.map(block => block.id), nextIds = replacement.map(block => block.id);
    let left = 0, right = 0;
    while (left < previousIds.length && left < nextIds.length && previousIds[left] === nextIds[left]) left++;
    while (right < previousIds.length - left && right < nextIds.length - left
           && previousIds[previousIds.length - 1 - right] === nextIds[nextIds.length - 1 - right]) right++;
    if (left + right < previousIds.length) order.delete(left, previousIds.length - left - right);
    const middle = nextIds.slice(left, nextIds.length - right);
    for (let i = 0; i < middle.length; i += 256) order.insert(left + i, middle.slice(i, i + 256));
    const updates = mergeEvents(events);
    if (events.length === beforeEventCount) throw Error('Detached body did not change');
    if (stableJson(blocksOf(doc)) !== stableJson(replacement)) throw Error('Detached Yjs body mismatch');
    const replay = new Y.Doc();
    try {
      for (const frame of input.frames) Y.applyUpdate(replay, new Uint8Array(Buffer.from(frame, 'base64')));
      for (const frame of updates) Y.applyUpdate(replay, new Uint8Array(Buffer.from(frame, 'base64')));
      if (replay.store.pendingStructs || replay.store.pendingDs
          || stableJson(blocksOf(replay)) !== stableJson(replacement)) throw Error('Detached Yjs replay mismatch');
    } finally { replay.destroy(); }
    const existing = new Set(previousIds);
    return {contract:CONTRACT, document_id:input.document_id, generation:input.generation,
      before_sha256:digest(current), after_sha256:digest(replacement),
      updates, source_update_count:input.frames.length,
      added_ids:nextIds.filter(id => !existing.has(id)),
      removed_ids:previousIds.filter(id => !wanted.has(id))};
  } finally { doc.off('update', onUpdate); doc.destroy(); }
}
if (require.main === module) {
  try {
    const request = JSON.parse(fs.readFileSync(0, 'utf8'));
    process.stdout.write(JSON.stringify(transform(request)));
  } catch (_) { process.stderr.write('Unable to prepare detached collaborative body\n'); process.exitCode = 1; }
}
module.exports = {transform, CONTRACT};

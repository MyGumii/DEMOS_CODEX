// Pure Yjs retargeting of linked views after an owned database changes document.
'use strict';
const fs = require('node:fs');
require('../notion_static/vendor/yjs-13.6.32.bundle.js');
const Y = globalThis.NotionYjs;
const {reconcileStructure} = require('./page_move_delta.cjs');
const CONTRACT = 'code_assist.notion.database_move_linked_delta.v1';
const DATABASE_BLOCKS = new Set(['table','board','list','gallery','calendar','timeline',
  'chart','dashboard','form','feed','map']);
if(Y.__notionBundle.yjs_version!=='13.6.32') throw new Error('Unexpected Yjs runtime');

function stableJson(value) {
  if(value===null||typeof value!=='object') return JSON.stringify(value);
  if(Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  return `{${Object.keys(value).sort().map(k=>`${JSON.stringify(k)}:${stableJson(value[k])}`).join(',')}}`;
}
function hash32(value) {
  let hash=2166136261;
  for(let i=0;i<value.length;i++) {hash^=value.charCodeAt(i);hash=Math.imul(hash,16777619);}
  return (hash>>>0)||1;
}
function bootstrap(doc, input) {
  if(doc.share.has('block-order')||doc.share.has('blocks')) return;
  if(input.generation!==1) throw new Error('Missing current structural generation');
  const previous=doc.clientID;
  doc.clientID=hash32(`code-assist:${input.document_id}:generation-1`);
  doc.transact(()=>{
    if(input.seed.title&&!doc.share.has('page-title'))
      doc.getText('page-title').insert(0,input.seed.title);
    const order=doc.getArray('block-order'), blocks=doc.getMap('blocks');
    order.insert(0,input.seed.blocks.map(block=>block.id));
    for(const block of input.seed.blocks) {
      const item=new Y.Map(), content=new Y.Text();
      blocks.set(block.id,item);
      item.set('type',block.type);item.set('props',stableJson(block.props||{}));
      item.set('content',content);
      if(block.content) content.insert(0,block.content);
    }
  });
  doc.clientID=previous;
}
function targetProps(item, id, input) {
  if(!(item instanceof Y.Map)||!DATABASE_BLOCKS.has(item.get('type')))
    throw new Error('Linked block is missing or has wrong type');
  const props=JSON.parse(String(item.get('props')||'{}'));
  if(!props||Array.isArray(props)||typeof props!=='object')
    throw new Error('Linked block props are invalid');
  const source=props.linked_database;
  if(!source||source.document_id!==input.source_document_id
      ||source.database_id!==input.database_id)
    throw new Error('Linked source changed');
  const witness=input.references.find(ref=>ref.block_id===id);
  if(!witness||stableJson(props)!==stableJson(witness.expected_props))
    throw new Error('Linked view settings changed');
  return {...props,linked_database:{...source,document_id:input.destination_document_id}};
}
function transform(input) {
  if(!input||typeof input.document_id!=='string'||!Number.isInteger(input.generation)
      ||!Array.isArray(input.frames)||!Array.isArray(input.seed?.blocks)
      ||!Array.isArray(input.references)||!input.references.length)
    throw new Error('Invalid linked document');
  const wanted=input.references.map(ref=>ref.block_id);
  if(wanted.some(id=>typeof id!=='string'||!id)||new Set(wanted).size!==wanted.length)
    throw new Error('Duplicate or invalid linked identity');
  const doc=new Y.Doc();
  try {
    for(const frame of input.frames)
      Y.applyUpdate(doc,new Uint8Array(Buffer.from(frame,'base64')));
    if(input.base_update)
      Y.applyUpdate(doc,new Uint8Array(Buffer.from(input.base_update,'base64')));
    if(doc.store.pendingStructs||doc.store.pendingDs) throw new Error('Incomplete replay');
    const before=Y.encodeStateVector(doc);
    const hadBody=doc.share.has('block-order')||doc.share.has('blocks');
    bootstrap(doc,input);
    // Source/destination base updates already reconcile their structural history.
    // Other hosts reconcile their own history before retargeting local views.
    const reconciled=input.base_update||!hadBody?[]:reconcileStructure(input,doc,doc.getArray('block-order'),doc.getMap('blocks')).inserts;
    const order=doc.getArray('block-order').toArray();
    if(new Set(order).size!==order.length) throw new Error('Duplicate block order');
    const blocks=doc.getMap('blocks'), actual=[];
    for(const id of order) {
      const item=blocks.get(id);
      if(!(item instanceof Y.Map)||!DATABASE_BLOCKS.has(item.get('type'))) continue;
      const props=JSON.parse(String(item.get('props')||'{}'));
      if(props?.linked_database?.document_id===input.source_document_id
          &&props.linked_database.database_id===input.database_id) actual.push(id);
    }
    if(actual.length!==wanted.length||actual.some(id=>!wanted.includes(id)))
      throw new Error('Linked reference set changed');
    doc.transact(()=>{
      for(const id of wanted) blocks.get(id).set('props',stableJson(targetProps(blocks.get(id),id,input)));
    });
    const update=Y.encodeStateAsUpdate(doc,before);
    if(!update.length||update.length>1048576) throw new Error('Invalid linked delta size');
    return {document_id:input.document_id,retargeted:wanted,reconciled_insert_ids:reconciled,
      update:Buffer.from(update).toString('base64')};
  } finally {doc.destroy();}
}
if(require.main===module) {
  try {
    const request=JSON.parse(fs.readFileSync(0,'utf8'));
    if(request.contract!==CONTRACT||!Array.isArray(request.documents)
        ||!request.documents.length||request.documents.length>64)
      throw new Error('Invalid linked move preparation');
    process.stdout.write(JSON.stringify({contract:CONTRACT,
      documents:request.documents.map(transform)}));
  } catch(_) {process.stderr.write('Unable to retarget linked database views\n');process.exitCode=1;}
}
module.exports={transform,CONTRACT};

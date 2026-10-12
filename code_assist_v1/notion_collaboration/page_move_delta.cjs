// Pure link transformation, optionally reconciling proven parent typed history.
// Python owns authorization, independent replay checks and persistence.
'use strict';
const fs = require('node:fs');
require('../notion_static/vendor/yjs-13.6.32.bundle.js');
const Y = globalThis.NotionYjs;
const CONTRACT = 'code_assist.notion.page_move_delta.v1';
const historicalStructure = require('./parent_historical_structure.cjs');
const restoreRecovery = require('./parent_restore_recovery.cjs');
if (Y.__notionBundle.yjs_version !== '13.6.32') throw new Error('Unexpected Yjs runtime');

function stableJson(value) {
  if(value===null||typeof value!=='object') return JSON.stringify(value);
  if(Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  return `{${Object.keys(value).sort().map(k=>`${JSON.stringify(k)}:${stableJson(value[k])}`).join(',')}}`;
}
function hash32(value) {
  let hash=2166136261;
  for(let i=0;i<value.length;i++) { hash^=value.charCodeAt(i); hash=Math.imul(hash,16777619); }
  return (hash>>>0)||1;
}
function put(blocks, block) {
  const item=new Y.Map(); item.set('content',new Y.Text()); blocks.set(block.id,item);
  item.set('type',block.type); item.set('props',stableJson(block.props||{}));
  if(block.content) item.get('content').insert(0,block.content);
}
const DATABASE_BLOCKS=new Set(['table','board','list','gallery','calendar','timeline',
  'chart','dashboard','form','feed','map']);
function matchesLink(item, id, kind, identity) {
  if(!(item instanceof Y.Map)) return false;
  const type=item.get('type');
  const props=JSON.parse(String(item.get('props')||'{}'));
  if(kind==='child_page') return type==='child_page'&&props.page_id===identity;
  if(kind==='owned_database') return DATABASE_BLOCKS.has(type)&&!props.linked_database
    &&(props.database_id||id)===identity;
  throw new Error('Unsupported link kind');
}
function reconcileInserts(input, doc, order, blocks) {
  if(input.post_frame_insert_ids===undefined) return [];
  const proof=input.post_frame_insert_ids;
  if(!Array.isArray(proof)||proof.length>100000
    ||proof.some(id=>typeof id!=='string'||!id)||new Set(proof).size!==proof.length)
    throw new Error('Invalid parent insert proof');
  const canonical=input.seed.blocks, canonicalIds=canonical.map(block=>block.id), current=order.toArray();
  if(new Set(canonicalIds).size!==canonicalIds.length||new Set(current).size!==current.length)
    throw new Error('Ambiguous parent body identities');
  const currentIds=new Set(current), canonicalSet=new Set(canonicalIds), allowed=new Set(proof);
  if(proof.some(id=>!canonicalSet.has(id))) throw new Error('Parent insert proof outside canonical body');
  const missing=canonicalIds.filter(id=>!currentIds.has(id));
  if(!missing.length) return [];
  const expected=proof.filter(id=>!currentIds.has(id));
  const common=canonicalIds.filter(id=>currentIds.has(id));
  if(missing.length!==expected.length||missing.some(id=>!allowed.has(id))
    ||common.length!==current.length||common.some((id,index)=>id!==current[index]))
    throw new Error('Parent block structure requires reconciliation');
  if(missing.some(id=>blocks.has(id))) throw new Error('Parent insert identity conflicts');
  doc.transact(()=>{
    for(let index=0;index<canonical.length;index++) {
      const block=canonical[index];
      if(!currentIds.has(block.id)) { put(blocks,block);order.insert(index,[block.id]); }
    }
  });
  return missing;
}
function reconcileStructure(input, doc, order, blocks) {
  const proof=input.post_frame_structure;
  if(proof===undefined) {
    const inserts=reconcileInserts(input,doc,order,blocks);
    return {inserts,changed:inserts.length>0};
  }
  if(proof.contract!=='code_assist.notion.parent_structure_witness.v1'
    ||proof.document_id!==input.document_id||proof.generation!==input.generation
    ||!Number.isInteger(proof.through_document_sequence)||proof.through_document_sequence<0
    ||!Array.isArray(proof.commands)||proof.commands.length>4096)
    throw new Error('Invalid parent structural witness');
  // Bootstrap already uses the complete current canonical body.
  if(!input.frames.length) return {inserts:[],changed:false};
  const original=order.toArray(), canonical=input.seed.blocks, final=canonical.map(b=>b.id);
  const restorations=restoreRecovery.prepare(input,Y);
  if(historicalStructure.needsRecovery(input,original,final)) {
    const inserts=historicalStructure.recover(input,original,final);
    if(inserts.some(id=>blocks.has(id)&&!restorations.has(id))) throw new Error('Historical parent insert identity conflicts');
    applyStructuralOrder(doc,order,blocks,original,canonical,restorations);
    const bodyChanged=restoreRecovery.apply(input,Y,doc,order,blocks,restorations,put);
    return {inserts,changed:true,bodyChanged};
  }
  if(!proof.commands.length) {
    const current=order.toArray(), canonicalIds=input.seed.blocks.map(block=>block.id), common=new Set(current);
    if(!Array.isArray(proof.retired_ids)||!Array.isArray(proof.historical_move_ids)
      ||proof.retired_ids.some(id=>common.has(id))) throw new Error('Older parent deletion requires recovery');
    const known=new Set(canonicalIds), commonCanonical=canonicalIds.filter(id=>common.has(id));
    const commonCurrent=current.filter(id=>known.has(id));
    if(proof.historical_move_ids.length&&commonCanonical.some((id,index)=>id!==commonCurrent[index]))
      throw new Error('Older parent order requires recovery');
    const inserts=reconcileInserts(input,doc,order,blocks);
    const bodyChanged=restoreRecovery.apply(input,Y,doc,order,blocks,restorations,put);
    return {inserts,changed:inserts.length>0,bodyChanged};
  }
  if(new Set(original).size!==original.length||new Set(final).size!==final.length)
    throw new Error('Ambiguous parent structural identities');
  const wanted=original.slice(), seen=new Set();
  let sequence=proof.through_document_sequence;
  for(const entry of proof.commands) {
    const {kind,target_id:id,after_block_id:anchor}=entry;
    if(typeof entry.command_id!=='string'||!entry.command_id||seen.has(entry.command_id)
      ||!Number.isInteger(entry.document_sequence)||entry.document_sequence<=sequence
      ||!['block.insert','block.delete','block.move'].includes(kind)
      ||typeof id!=='string'||!id||typeof entry.restore_deleted!=='boolean'
      ||anchor!==null&&(typeof anchor!=='string'||!anchor))
      throw new Error('Parent structural history requires recovery');
    if(entry.restore_deleted)restoreRecovery.entry(input,entry);
    seen.add(entry.command_id);sequence=entry.document_sequence;
    const at=wanted.indexOf(id);
    if(kind==='block.delete') {if(at>=0) wanted.splice(at,1);continue;}
    if(kind==='block.insert'&&at>=0) continue;
    if(kind==='block.move') {
      if(at<0) throw new Error('Parent move target requires recovery');
      wanted.splice(at,1);
    }
    if(anchor!==null&&!wanted.includes(anchor)) throw new Error('Parent structural anchor requires recovery');
    wanted.splice(anchor===null?0:wanted.indexOf(anchor)+1,0,id);
  }
  if(wanted.length!==final.length||wanted.some((id,index)=>id!==final[index]))
    throw new Error('Parent structural history does not explain current order');
  const originalSet=new Set(original), canonicalSet=new Set(final), proofIds=input.post_frame_insert_ids;
  if(!Array.isArray(proofIds)||new Set(proofIds).size!==proofIds.length
    ||proofIds.some(id=>typeof id!=='string'||!canonicalSet.has(id)))
    throw new Error('Invalid parent insert witness');
  const inserts=final.filter(id=>!originalSet.has(id)), allowed=proofIds.filter(id=>!originalSet.has(id));
  if(inserts.length!==allowed.length||inserts.some(id=>!allowed.includes(id)||blocks.has(id)&&!restorations.has(id)))
    throw new Error('Parent insert history differs');
  const changed=original.length!==final.length||original.some((id,index)=>id!==final[index]);
  applyStructuralOrder(doc,order,blocks,original,canonical,restorations);
  const bodyChanged=restoreRecovery.apply(input,Y,doc,order,blocks,restorations,put);
  return {inserts,changed,bodyChanged};
}
function applyStructuralOrder(doc,order,blocks,original,canonical,restorations=new Map()) {
  const originalSet=new Set(original), final=canonical.map(b=>b.id), canonicalSet=new Set(final);
  doc.transact(()=>{
    for(let index=order.length-1;index>=0;index--) {
      const id=order.get(index);
      if(!canonicalSet.has(id)) {order.delete(index,1);blocks.delete(id);}
    }
    for(let index=0;index<final.length;index++) {
      const id=final[index];
      if(!originalSet.has(id)) {
        const recovery=restorations.get(id);
        if(recovery?.clone)blocks.set(id,recovery.clone);else put(blocks,canonical[index]);
      }
      const at=order.toArray().indexOf(id);
      if(at===index) continue;
      if(at>=0) order.delete(at,1);
      order.insert(index,[id]);
    }
  });
}
function transform(input) {
  const doc=new Y.Doc();
  try {
    const kind=input.match_kind||'child_page';
    const identity=input.match_id||input.child_id;
    if(!['child_page','owned_database'].includes(kind)||typeof identity!=='string'||!identity)
      throw new Error('Invalid link identity');
    for(const frame of input.frames) Y.applyUpdate(doc,new Uint8Array(Buffer.from(frame,'base64')));
    if(doc.store.pendingStructs||doc.store.pendingDs) throw new Error('Incomplete replay');
    const before=Y.encodeStateVector(doc), order=doc.getArray('block-order'), blocks=doc.getMap('blocks');
    if(!input.frames.length) {
      // Match the browser's deterministic first-generation bootstrap exactly.
      if(input.generation!==1) throw new Error('Missing current generation state');
      const random=doc.clientID; doc.clientID=hash32(`code-assist:${input.document_id}:generation-1`);
      doc.transact(()=>{
        if(input.seed.title) doc.getText('page-title').insert(0,input.seed.title);
        order.insert(0,input.seed.blocks.map(b=>b.id));
        for(const block of input.seed.blocks) put(blocks,block);
      });
      doc.clientID=random;
    }
    const {inserts:reconciled,changed:structureChanged,bodyChanged:restorationChanged}=reconcileStructure(input,doc,order,blocks);
    if(input.insert_many!==undefined) {
      const inserts=input.insert_many;
      if(input.insert||!Array.isArray(inserts)||!inserts.length||inserts.length>100)
        throw new Error('Invalid batch child-page links');
      const ids=new Set(),children=new Set();
      for(const entry of inserts) {
        const child=entry?.props?.page_id;
        if(typeof entry?.id!=='string'||!entry.id||entry.type!=='child_page'
          ||typeof entry.content!=='string'||typeof child!=='string'||!child
          ||ids.has(entry.id)||children.has(child)||blocks.has(entry.id)
          ||[...blocks].some(([id,item])=>matchesLink(item,id,'child_page',child)))
          throw new Error('Batch child-page link identity conflicts');
        ids.add(entry.id);children.add(child);
      }
      const original=order.toArray(),canonicalIds=new Set(input.seed.blocks.map(block=>block.id));
      const placed_many=[];
      let after=original.slice().reverse().find(id=>canonicalIds.has(id))||null;
      for(const entry of inserts) {
        placed_many.push({id:entry.id,after_block_id:after,page_id:entry.props.page_id});
        after=entry.id;
      }
      doc.transact(()=>{
        for(const entry of inserts) put(blocks,entry);
        order.insert(order.length,inserts.map(entry=>entry.id));
      });
      const update=Y.encodeStateAsUpdate(doc,before);
      if(update.length>1048576) throw new Error('Link delta exceeds durable update limit');
      return {document_id:input.document_id,removed:[],placed:null,inserted:false,
        after_block_id:null,placed_many,changed:true,restore_anchor:null,
        update:Buffer.from(update).toString('base64'),reconciled_insert_ids:reconciled,
        reconciled_structure_changed:structureChanged};
    }
    const removed=[];
    let placed=null, inserted=false, after=null, changed=structureChanged||!!restorationChanged, restoreAnchor=null;
    doc.transact(()=>{
      const original=order.toArray();
      const matches=[];
      for(const [id,item] of blocks) {
        if(matchesLink(item,id,kind,identity)) matches.push(id);
      }
      if(kind==='owned_database'&&matches.length>1) throw new Error('Ambiguous owned database');
      if(input.expected_match_count!==null&&input.expected_match_count!==undefined
        &&(!Number.isInteger(input.expected_match_count)||!([0,1].includes(input.expected_match_count))
          ||matches.length!==input.expected_match_count))
        throw new Error('Owned link witness changed');
      const keep=input.relocate?original.find(id=>matches.includes(id)):null;
      const firstMatch=original.findIndex(id=>matches.includes(id));
      if(firstMatch>=0) restoreAnchor={
        previous_block_id:original.slice(0,firstMatch).reverse().find(id=>!matches.includes(id))||null,
        next_block_id:original.slice(firstMatch+1).find(id=>!matches.includes(id))||null,
        index:firstMatch,
      };
      for(const id of matches) if(id!==keep) {removed.push(id);blocks.delete(id);}
      const ids=order.toArray();
      for(let i=ids.length-1;i>=0;i--) if(removed.includes(ids[i])) order.delete(i,1);
      if(input.insert) {
        const remaining=order.toArray().filter(id=>id!==keep);
        let at=remaining.length;
        if(input.relative_page_id) {
          const anchor=remaining.find(id=>{
            const item=blocks.get(id);
            return item instanceof Y.Map&&item.get('type')==='child_page'
              &&JSON.parse(String(item.get('props')||'{}')).page_id===input.relative_page_id;
          });
          if(!anchor) throw new Error('Relative page link is no longer present');
          at=remaining.indexOf(anchor)+(input.relative_position==='after'?1:0);
        } else if(input.restore_anchor) {
          const anchor=input.restore_anchor;
          if(anchor.next_block_id&&remaining.includes(anchor.next_block_id)) at=remaining.indexOf(anchor.next_block_id);
          else if(anchor.previous_block_id&&remaining.includes(anchor.previous_block_id)) at=remaining.indexOf(anchor.previous_block_id)+1;
          else at=Math.max(0,Math.min(remaining.length,anchor.index));
        }
        placed=keep||input.insert.id;
        if(!keep) {
          if(blocks.has(placed)||order.toArray().includes(placed)) throw new Error('Existing link identity');
          put(blocks,input.insert);inserted=true;
        }
        remaining.splice(at,0,placed);
        const linkChanged=original.length!==remaining.length||original.some((id,index)=>id!==remaining[index]);
        changed ||= linkChanged;
        if(linkChanged) {
          if(keep) for(let i=order.length-1;i>=0;i--) if(order.get(i)===keep) order.delete(i,1);
          order.insert(at,[placed]);
        }
        const canonicalIds=new Set(input.seed.blocks.map(block=>block.id));
        after=remaining.slice(0,at).reverse().find(id=>canonicalIds.has(id))||null;
      }
      changed ||= removed.length>0;
    });
    const update=Y.encodeStateAsUpdate(doc,before);
    if(update.length>1048576) throw new Error('Link delta exceeds durable update limit');
    return {document_id:input.document_id,removed,placed,inserted,after_block_id:after,changed,restore_anchor:restoreAnchor,update:Buffer.from(update).toString('base64'),reconciled_insert_ids:reconciled,reconciled_structure_changed:structureChanged};
  } finally { doc.destroy(); }
}
if(require.main===module) {
  try {
    const request=JSON.parse(fs.readFileSync(0,'utf8'));
    if(request.contract!==CONTRACT||!Array.isArray(request.documents)||request.documents.length>2) throw new Error('Invalid move preparation');
    process.stdout.write(JSON.stringify({contract:CONTRACT,documents:request.documents.map(transform)}));
  } catch(_) { process.stderr.write('Unable to prepare collaborative page links\n'); process.exitCode=1; }
}
module.exports={transform,CONTRACT,reconcileInserts,reconcileStructure};

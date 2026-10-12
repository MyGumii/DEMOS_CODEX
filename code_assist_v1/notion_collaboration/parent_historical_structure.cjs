// Independently validate the internal immutable anchor-to-current certificate.
'use strict';
const CONTRACT='code_assist.notion.parent_historical_structure.v1';
const restoreRecovery=require('./parent_restore_recovery.cjs');
function ids(values) {
  if(!Array.isArray(values)||values.length>100000
    ||values.some(id=>typeof id!=='string'||!id)||new Set(values).size!==values.length)
    throw new Error('Historical parent identities differ');
  return values.slice();
}
function recover(input, original, canonical) {
  const proof=input.post_frame_structure.historical_structure, anchor=proof.anchor;
  if(proof.contract!==CONTRACT||proof.document_id!==input.document_id||proof.generation!==input.generation
    ||!Number.isInteger(anchor.document_sequence)||anchor.document_sequence<1
    ||!Number.isInteger(anchor.revision)||anchor.revision<1
    ||typeof anchor.checkpoint_id!=='string'||!anchor.checkpoint_id
    ||typeof anchor.content_sha256!=='string'||!/^sha256:[a-f0-9]{64}$/.test(anchor.content_sha256)
    ||!Number.isInteger(proof.through_document_sequence)||proof.through_document_sequence<anchor.document_sequence
    ||!Array.isArray(proof.commands)||proof.commands.length>4096)
    throw new Error('Historical parent scope differs');
  const wanted=ids(anchor.block_ids), known=new Set(wanted), positioned=new Set(), deleted=new Set(), inserted=new Set(), seen=new Set();
  const lastDeletions=new Map();
  let sequence=anchor.document_sequence;
  for(const entry of proof.commands) {
    const {kind,target_id:id,after_block_id:after}=entry;
    if(typeof entry.command_id!=='string'||!entry.command_id||seen.has(entry.command_id)
      ||!Number.isInteger(entry.document_sequence)||entry.document_sequence<=sequence||entry.document_sequence>proof.through_document_sequence
      ||!['block.insert','block.delete','block.move'].includes(kind)||typeof id!=='string'||!id
      ||typeof entry.restore_deleted!=='boolean'||after!==null&&(typeof after!=='string'||!after))
      throw new Error('Historical parent command differs');
    seen.add(entry.command_id);sequence=entry.document_sequence;
    if(kind==='block.insert') {
      if(entry.restore_deleted) {
        const restoration=restoreRecovery.entry({...input,post_frame_structure:{restores:proof.restore_bindings||[]}},entry,{bindingOnly:true});
        const deletion=lastDeletions.get(id);
        if(!known.has(id)||wanted.includes(id)||!deletion||deletion.command_id!==restoration.deletion_command_id
          ||deletion.document_sequence!==restoration.deletion_document_sequence||deletion.target_version!==restoration.deleted_version)
          throw Error('Historical restoration deletion/version differs');
      } else if(known.has(id)) throw new Error('Historical parent identity restoration requires recovery');
      known.add(id);positioned.add(id);inserted.add(id);
    } else {
      if(entry.restore_deleted)throw Error('Historical restoration kind differs');
      const at=wanted.indexOf(id);
      if(at<0) throw new Error('Historical parent target differs');
      wanted.splice(at,1);
      if(kind==='block.delete') {deleted.add(id);lastDeletions.set(id,entry);continue;}
      positioned.add(id);
    }
    if(after!==null&&!wanted.includes(after)) throw new Error('Historical parent anchor differs');
    wanted.splice(after===null?0:wanted.indexOf(after)+1,0,id);
  }
  ids(canonical);ids(original);
  if(wanted.length!==canonical.length||wanted.some((id,index)=>id!==canonical[index]))
    throw new Error('Historical parent commands do not explain canonical order');
  const current=new Set(original), final=new Set(canonical);
  if(original.some(id=>!known.has(id)||!final.has(id)&&!deleted.has(id)))
    throw new Error('Historical parent projected identities are unexplained');
  const proofIds=input.post_frame_insert_ids;
  if(!Array.isArray(proofIds)||new Set(proofIds).size!==proofIds.length
    ||proofIds.some(id=>typeof id!=='string'||!final.has(id))) throw new Error('Invalid parent insert witness');
  const missing=canonical.filter(id=>!current.has(id));
  const allowed=[...new Set([...proofIds,...inserted])].filter(id=>final.has(id)&&!current.has(id));
  if(missing.length!==allowed.length||missing.some(id=>!allowed.includes(id)))
    throw new Error('Historical parent missing content requires recovery');
  const stable=id=>current.has(id)&&final.has(id)&&!positioned.has(id);
  const oldStable=original.filter(stable), newStable=canonical.filter(stable);
  if(oldStable.length!==newStable.length||oldStable.some((id,index)=>id!==newStable[index]))
    throw new Error('Historical parent untouched order differs');
  return missing;
}
function needsRecovery(input, original, canonical) {
  const structure=input.post_frame_structure, proof=structure.historical_structure;
  if(proof===undefined) return false;
  const current=new Set(original), final=new Set(canonical), commonOld=original.filter(id=>final.has(id));
  const commonNew=canonical.filter(id=>current.has(id));
  return structure.retired_ids.some(id=>current.has(id))
    ||proof.commands.some(entry=>entry.kind==='block.delete'&&!final.has(entry.target_id)&&current.has(entry.target_id))
    ||structure.historical_move_ids.length>0&&commonOld.some((id,index)=>id!==commonNew[index])
    ||proof.commands.some(entry=>entry.kind==='block.insert'&&final.has(entry.target_id)&&!current.has(entry.target_id));
}
module.exports={recover,needsRecovery,CONTRACT};

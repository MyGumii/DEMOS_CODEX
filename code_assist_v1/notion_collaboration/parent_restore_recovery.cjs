'use strict';
const crypto=require('node:crypto');
const CONTRACT='code_assist.notion.parent_restore_recovery.v1';
function stable(value) {
  if(value===null||typeof value!=='object')return JSON.stringify(value);
  if(Array.isArray(value))return `[${value.map(stable).join(',')}]`;
  return `{${Object.keys(value).sort().map(key=>`${JSON.stringify(key)}:${stable(value[key])}`).join(',')}}`;
}
function fields(Y,id,item) {
  if(!(item instanceof Y.Map))throw Error('Deleted rich parent map is unavailable');
  return {id,type:item.get('type')||'paragraph',content:item.get('content') instanceof Y.Text?item.get('content').toString():String(item.get('content')||''),
    props:JSON.parse(String(item.get('props')||'{}'))};
}
function entry(input,command,{bindingOnly=false}={}) {
  const values=(input.post_frame_structure.restores||[]).filter(value=>value.command_id===command.command_id);
  if(values.length!==1)throw Error('Parent restore witness is missing');
  const proof=values[0];
  if(proof.contract!==CONTRACT||proof.document_id!==input.document_id||proof.generation!==input.generation
    ||proof.target_id!==command.target_id||proof.document_sequence!==command.document_sequence
    ||typeof proof.deletion_command_id!=='string'||!proof.deletion_command_id
    ||!Number.isInteger(proof.deletion_document_sequence)||proof.deletion_document_sequence>=proof.document_sequence
    ||!Number.isInteger(proof.deleted_version)||proof.deleted_version<1||proof.restored_version!==proof.deleted_version+1
    ||!bindingOnly&&(!Array.isArray(proof.frames)||proof.frames.length>4097)
    ||!Number.isInteger(proof.through_update_sequence)||proof.through_update_sequence<0)
    throw Error('Parent restore scope/version differs');
  if(!bindingOnly&&(proof.historical??false)!==(proof.document_sequence<=input.post_frame_structure.through_document_sequence))
    throw Error('Parent restore frame boundary differs');
  if(!bindingOnly) {
    const bindings=input.post_frame_structure.historical_structure?.restore_bindings||[];
    const matches=bindings.filter(value=>value.command_id===command.command_id);
    const header=Object.fromEntries(Object.entries(proof).filter(([key])=>!['frames','frame_bytes','historical'].includes(key)));
    if(matches.length&&(matches.length!==1||stable(matches[0])!==stable(header)))throw Error('Parent historical restore binding differs');
  }
  return proof;
}
function commands(input) {
  const structure=input.post_frame_structure||{},entries=new Map();
  for(const command of [...(structure.historical_structure?.commands||[]),...(structure.commands||[])])entries.set(command.command_id,command);
  return [...entries.values()].sort((a,b)=>a.document_sequence-b.document_sequence);
}
function keepCurrent(Y,id,current,value) {
  if(!current)return false;
  if(stable(fields(Y,id,current))===stable(value.proof.values))return true;
  if(value.proof.historical!==true)return false;
  if(stable(value.archivedFields)===stable(value.proof.values))return true;
  const identity=current._item?.id;
  if(identity&&identity.clock>=(value.vector.get(identity.client)||0))return true;
  throw Error('Historical replacement has an unexplained older map');
}
function prepare(input,Y) {
  const restorations=new Map(),docs=[];
  let bytes=0;
  try {
    for(const command of commands(input))if(command.restore_deleted) {
      if(command.kind!=='block.insert')throw Error('Invalid restored command');
      const proof=entry(input,command),doc=new Y.Doc();docs.push(doc);
      let size=0;
      for(const frame of proof.frames) {
        const payload=Buffer.from(frame.payload,'base64');size+=payload.length;bytes+=payload.length;
        if(bytes>8388608||'sha256:'+crypto.createHash('sha256').update(payload).digest('hex')!==frame.sha256)
          throw Error('Parent restore range checksum/budget differs');
        Y.applyUpdate(doc,payload);
      }
      if(size!==proof.frame_bytes||doc.store.pendingStructs||doc.store.pendingDs)
        throw Error('Parent restore range is incomplete');
      const item=doc.getMap('blocks').get(proof.target_id);
      if(proof.frames.length&&(!item||!doc.getArray('block-order').toArray().includes(proof.target_id)))
        throw Error('Deleted rich parent body is unavailable');
      const archivedFields=item?fields(Y,proof.target_id,item):null;
      const clone=item&&stable(archivedFields)===stable(proof.values)?item.clone():null;
      restorations.set(proof.target_id,{proof,clone,archivedFields,vector:Y.decodeStateVector(Y.encodeStateVector(doc))});
    }
    const canonical=new Map(input.seed.blocks.map(block=>[block.id,block]));
    for(const [id,value]of restorations)if(canonical.has(id)&&stable({id,type:canonical.get(id).type,content:canonical.get(id).content,props:canonical.get(id).props})!==stable(value.proof.values))
      throw Error('Restored canonical parent fields changed');
    return restorations;
  } finally {for(const doc of docs)doc.destroy();}
}
function apply(input,Y,doc,order,blocks,restorations,put) {
  let changed=false;
  doc.transact(()=>{
    for(const id of order.toArray()) {
      const value=restorations.get(id);if(!value)continue;
      const current=blocks.get(id);
      if(keepCurrent(Y,id,current,value))continue;
      if(value.clone)blocks.set(id,value.clone);else put(blocks,value.proof.values);
      changed=true;
    }
  });
  return changed;
}
module.exports={entry,prepare,apply,CONTRACT};

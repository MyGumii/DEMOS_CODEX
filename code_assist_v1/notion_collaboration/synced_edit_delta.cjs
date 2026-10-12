// Service-owned text-only Yjs edits; Python owns the subtree and access fence.
'use strict';
const fs=require('node:fs');
require('../notion_static/vendor/yjs-13.6.32.bundle.js');
const Y=globalThis.NotionYjs;
const {commonTextEdges}=require('../notion_static/notion_collaboration_editor.js');
const CONTRACT='code_assist.notion.synced_text_delta.v1';
function stable(value){if(value===null||typeof value!=='object')return JSON.stringify(value);if(Array.isArray(value))return `[${value.map(stable).join(',')}]`;return `{${Object.keys(value).sort().map(k=>`${JSON.stringify(k)}:${stable(value[k])}`).join(',')}}`;}
function hash32(value){let hash=2166136261;for(let i=0;i<value.length;i++){hash^=value.charCodeAt(i);hash=Math.imul(hash,16777619);}return (hash>>>0)||1;}
function replace(text,value){const old=text.toString(),{prefix,suffix}=commonTextEdges(old,value);const count=old.length-prefix-suffix;if(count)text.delete(prefix,count);const added=value.slice(prefix,value.length-suffix);if(added)text.insert(prefix,added);}
function transform(input){
  if(Y.__notionBundle.yjs_version!=='13.6.32')throw Error('Wrong runtime');
  const doc=new Y.Doc();
  try{
    for(const frame of input.frames)Y.applyUpdate(doc,new Uint8Array(Buffer.from(frame,'base64')));
    if(doc.store.pendingStructs||doc.store.pendingDs)throw Error('Incomplete replay');
    const vector=Y.encodeStateVector(doc);
    if(!input.frames.length){
      if(input.generation!==1)throw Error('Missing generation state');
      const random=doc.clientID;doc.clientID=hash32(`code-assist:${input.document_id}:generation-1`);
      doc.transact(()=>{
        const title=doc.getText('page-title'),order=doc.getArray('block-order'),blocks=doc.getMap('blocks');
        if(input.seed.title)title.insert(0,input.seed.title);
        order.insert(0,input.seed.blocks.map(b=>b.id));
        for(const block of input.seed.blocks){
          const item=new Y.Map();item.set('content',new Y.Text());blocks.set(block.id,item);
          item.set('type',block.type);item.set('props',stable(block.props||{}));
          if(block.content)item.get('content').insert(0,block.content);
        }
      });doc.clientID=random;
    }
    if(!doc.share.has('block-order')||!doc.share.has('blocks'))throw Error('Body requires reconciliation');
    const changes=new Map(input.updates.map(value=>[value.id,value])),found=new Set(),blocks=doc.getMap('blocks');
    function props(block,depth){
      if(depth>64)throw Error('Deep children');
      const p=block.props||{};
      if(changes.has(block.id)){
        if(found.has(block.id))throw Error('Duplicate identity');found.add(block.id);
        const value=changes.get(block.id);block.content=value.content;
        if(value.rich_text_html===null)delete p.rich_text_html;else p.rich_text_html=value.rich_text_html;
      }
      if(Array.isArray(p.children))for(const child of p.children)if(child&&typeof child==='object')props(child,depth+1);
      block.props=p;
    }
    doc.transact(()=>{
      for(const id of doc.getArray('block-order').toArray()){
        const item=blocks.get(id);if(!(item instanceof Y.Map))throw Error('Missing block');
        const text=item.get('content');if(!(text instanceof Y.Text))throw Error('Invalid text leaf');
        const block={id,content:text.toString(),props:JSON.parse(String(item.get('props')||'{}'))};
        const before=stable(block.props);props(block,0);
        if(changes.has(id))replace(text,block.content);
        const after=stable(block.props);if(before!==after)item.set('props',after);
      }
    });
    if(found.size!==changes.size)throw Error('Missing edited leaf');
    const delta=Y.encodeStateAsUpdate(doc,vector);
    if(delta.length>1048576)throw Error('Update too large');
    return {contract:CONTRACT,update:Buffer.from(delta).toString('base64')};
  }finally{doc.destroy();}
}
if(require.main===module){try{const input=JSON.parse(fs.readFileSync(0,'utf8'));if(input.contract!==CONTRACT)throw Error('Invalid contract');process.stdout.write(JSON.stringify(transform(input)));}catch(_){process.stderr.write('Unable to prepare synced text edit\n');process.exitCode=1;}}
module.exports={CONTRACT,transform};

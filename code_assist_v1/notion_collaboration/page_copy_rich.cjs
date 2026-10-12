'use strict';
const fs=require('node:fs');
require('../notion_static/vendor/yjs-13.6.32.bundle.js');
const Y=globalThis.NotionYjs,CONTRACT='code_assist.notion.page_copy_rich.v1';
function hash32(value) {
  let hash=2166136261;
  for(let i=0;i<value.length;i++){hash^=value.charCodeAt(i);hash=Math.imul(hash,16777619);}
  return (hash>>>0)||1;
}
function stable(value) {
  if(value===null||typeof value!=='object')return JSON.stringify(value);
  if(Array.isArray(value))return '['+value.map(stable).join(',')+']';
  return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+stable(value[key])).join(',')+'}';
}
function put(text,delta,content) {
  const runs=delta===undefined?(content?[{insert:content}]:[]):delta;
  if(!Array.isArray(runs)||runs.some(run=>typeof run.insert!=='string')
      ||runs.map(run=>run.insert).join('')!==content)throw Error('Invalid text runs');
  text.applyDelta(runs);
}
try {
  const input=JSON.parse(fs.readFileSync(0,'utf8'));
  if(input.contract!==CONTRACT||Y.__notionBundle.yjs_version!=='13.6.32')throw Error('Invalid runtime');
  const doc=new Y.Doc();
  try {
    doc.clientID=hash32(`code-assist:${input.document_id}:generation-1`);
    const order=doc.getArray('block-order'),blocks=doc.getMap('blocks');
    doc.transact(()=>{
      put(doc.getText('page-title'),input.rich.title,input.title);
      order.insert(0,input.blocks.map(block=>block.id));
      for(const block of input.blocks){
        const item=new Y.Map();blocks.set(block.id,item);
        item.set('type',block.type);item.set('props',stable(block.props));
        const text=new Y.Text();item.set('content',text);
        put(text,Object.hasOwn(input.rich.blocks,block.id)?input.rich.blocks[block.id]:undefined,block.content);
      }
    });
    process.stdout.write(JSON.stringify({contract:CONTRACT,update:Buffer.from(Y.encodeStateAsUpdate(doc)).toString('base64')}));
  } finally {doc.destroy();}
} catch(_) {process.stderr.write('Unable to prepare copied rich generation\n');process.exitCode=1;}

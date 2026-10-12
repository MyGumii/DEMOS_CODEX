'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const clipboard=require('../notion_static/notion_synced_clipboard'),core=require('../notion_static/notion_editor_core');
const origin='http://localhost:40191',source={workspace_id:'ws',document_id:'original-page',block_id:'original-block'};
const descriptor=()=>({version:1,contract:clipboard.CONTRACT,application_origin:origin,source:{...source}});
const read=()=>({contract:'code_assist.notion.synced_block.v1',instance:{document_id:source.document_id,block_id:source.block_id},origin:{document_id:source.document_id,block_id:source.block_id},block:{id:source.block_id,type:'synced_block',content:'secret body',props:{synced_from:null}},witnesses:[{workspace_id:'ws',document_id:source.document_id}]});
test('UI-SYNC-CLIP descriptor is bounded exact and carries no body or grants',()=>{
 const d=clipboard.fromRead(read(),read().instance,read().origin,origin),data=new Map();
 clipboard.write({setData:(key,value)=>data.set(key,value)},d);
 assert.deepEqual(clipboard.decode(data.get(clipboard.MIME),origin),descriptor());
 assert.match(data.get('text/html'),/data-notion-synced-reference/);assert.doesNotMatch([...data.values()].join(''),/secret body|witness|grant/);
 assert.deepEqual(clipboard.read({getData:key=>data.get(key)}),{encoded:data.get(clipboard.MIME)});
});
test('UI-SYNC-CLIP foreign malformed extra fields invalid identifiers and oversized input fail',()=>{
 for(const value of [{...descriptor(),extra:'grant'},{...descriptor(),version:2},{...descriptor(),application_origin:origin+'/'},{...descriptor(),application_origin:'http://elsewhere'},
  {...descriptor(),source:{...source,block_id:' spaced '}},{...descriptor(),source:{...source,block_id:'x\n'}},{...descriptor(),source:{...source,body:'cached'}}])assert.throws(()=>clipboard.decode(JSON.stringify(value),origin));
 assert.throws(()=>clipboard.decode('x'.repeat(clipboard.MAX_BYTES+1),origin));assert.throws(()=>clipboard.decode('{',origin));
 assert.deepEqual(clipboard.read({getData:()=>'',types:[clipboard.MIME]}),{encoded:''});
});
test('UI-SYNC-CLIP a reference copies its real original and rejects changed source witnesses',()=>{
 const data=read(),instance={document_id:'peer-page',block_id:'peer'};data.instance=instance;
 assert.deepEqual(clipboard.fromRead(data,instance,read().origin,origin),descriptor());
 for(const changed of [{...data,origin:instance},{...data,block:{...data.block,props:{synced_from:instance}}},{...data,witnesses:[]},{...data,witnesses:[...data.witnesses,...data.witnesses]}])assert.throws(()=>clipboard.fromRead(changed,instance,read().origin,origin));
});
test('UI-SYNC-CLIP failed clipboard and obsolete scopes cannot report success',async()=>{
 const doc={addEventListener(){},removeEventListener(){},execCommand:()=>false};
 await assert.rejects(clipboard.copy(descriptor(),{document:doc,current:()=>true}));
 await assert.rejects(clipboard.copy(descriptor(),{document:doc,current:()=>false}));
 let current=true;
 await assert.rejects(clipboard.copy(descriptor(),{document:doc,current:()=>current,Blob,ClipboardItem:class{constructor(value){this.value=value;}},navigator:{clipboard:{write:async()=>{current=false;}}}}));
});
test('UI-SYNC-CLIP native copy captures before generic body serialization and always removes its listener',async()=>{
 let listener,capture,removed,stopped=false;const data=new Map();
 const doc={addEventListener(type,fn,phase){listener=fn;capture=phase;},removeEventListener(type,fn,phase){removed=listener===fn&&phase===true;},execCommand(){
  listener({clipboardData:{setData:(key,value)=>data.set(key,value)},preventDefault(){},stopImmediatePropagation(){stopped=true;}});return true;
 }};
 await clipboard.copy(descriptor(),{document:doc});assert.equal(capture,true);assert.equal(removed,true);assert.equal(stopped,true);
 assert.deepEqual([...data.keys()],[clipboard.MIME,'text/html','text/plain']);assert.doesNotMatch([...data.values()].join(''),/secret body/);
});
const main=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
test('UI-SYNC-CLIP unchanged main redraw keeps only the exact visible original copy intent',()=>{
 const block={id:'peer',type:'synced_block',content:'',props:{synced_from:read().origin}};
 const scope={realm:origin,userId:'actor',workspaceId:'ws',pageId:'peer-page',navigationSequence:3,data:{}};
 const state={user:{id:'actor'},activePageId:'peer-page',navigation:{sequence:3},pageData:{page:{workspace_id:'ws',collaborative_synced_read:true},blocks:[block]}};
 const row={isConnected:true,closest:()=>null,querySelector:()=>({})};
 const c={State:state,window:{NotionSyncedReferences:{explicit:b=>b?.type==='synced_block'&&Object.hasOwn(b.props,'synced_from'),reference:b=>Boolean(b.props.synced_from)},NotionSyncedActions:{sameEndpoint:(a,b)=>a.document_id===b.document_id&&a.block_id===b.block_id}},location:{origin},CSS:{escape:value=>value},document:{querySelector:()=>row},EditorHost:{isComposing:()=>false}};
 vm.createContext(c);vm.runInContext(main.slice(main.indexOf('function syncedClipboardPageCurrent('),main.indexOf('\nfunction syncedLocationsHost(')),c);
 const current=()=>c.syncedClipboardScopeCurrent(scope,'peer',read().origin);
 assert.equal(current(),true);state.pageData=structuredClone(state.pageData);assert.equal(current(),true);
 for(const kind of ['actor','navigation','workspace','origin','duplicate','removed','hidden','composition','unsupported']){
  state.user.id='actor';state.navigation.sequence=3;state.pageData={page:{workspace_id:'ws',collaborative_synced_read:true},blocks:[structuredClone(block)]};row.isConnected=true;row.closest=()=>null;c.EditorHost.isComposing=()=>false;
  if(kind==='actor')state.user.id='other';if(kind==='navigation')state.navigation.sequence++;
  if(kind==='workspace')state.pageData.page.workspace_id='other';if(kind==='origin')state.pageData.blocks[0].props.synced_from.block_id='other';
  if(kind==='duplicate')state.pageData.blocks.push(structuredClone(block));if(kind==='removed')row.isConnected=false;
  if(kind==='hidden')row.closest=()=>({});if(kind==='composition')c.EditorHost.isComposing=()=>true;if(kind==='unsupported')state.pageData.page.collaborative_synced_read=false;
  assert.equal(current(),false,kind);
 }
});
function harness(){
 let resolve;const notices=[],node={isConnected:true,closest:()=>null,dataset:{}};
 const state={user:{id:'actor'},activePageId:'page',navigation:{sequence:1},selectedBlockIds:new Set(),pageData:{page:{workspace_id:'ws',collaborative_synced_read:true},blocks:[core.createBlock('paragraph',{id:'target',content:''})]}};
 const c={State:state,window:{NotionSyncedClipboard:clipboard,NotionBlockStructure:require('../notion_static/notion_block_structure')},location:{origin},EditorCore:core,EditorHost:{isComposing:()=>false},document:{activeElement:null},$ :()=>({focus(){}}),
 syncedTextScope:()=>({realm:origin,userId:'actor',pageId:'page',workspaceId:'ws',navigationSequence:state.navigation.sequence}),syncedScopeCurrent:()=>true,activePageCanEdit:()=>true,api:()=>new Promise(done=>{resolve=done;}),
 captureEditorHistory(){},clearBlockSelection(){state.selectedBlockIds.clear();},markDirty(){c.dirty++;},dirty:0,renderBlocks(){},setStatus:message=>notices.push(message),JSON};
 vm.createContext(c);vm.runInContext(main.slice(main.indexOf('function syncedClipboardPageCurrent('),main.indexOf('\nfunction syncedClipboardScopeCurrent(')),c);vm.runInContext(main.slice(main.indexOf('let SyncedClipboardPasteSequence='),main.indexOf('\nfunction pasteStructuredBlocks(')),c);
 const event={target:node,clipboardData:{getData:key=>key===clipboard.MIME?JSON.stringify(descriptor()):''},preventDefault(){},stopPropagation(){}};
 return {c,state,node,notices,event,resolve:()=>resolve(read())};
}
test('UI-SYNC-CLIP actual main paste creates only a new empty original reference',async()=>{
 const h=harness();assert.equal(h.c.pasteSyncedReference(h.event,0),true);assert.equal(h.c.dirty,0);h.resolve();await new Promise(setImmediate);
 assert.equal(h.c.dirty,1);const copy=h.state.pageData.blocks[0];assert.equal(copy.type,'synced_block');assert.equal(copy.content,'');assert.equal(copy.props.children,undefined);
 assert.deepEqual(copy.props.synced_from,read().origin);assert.notEqual(copy.id,source.block_id);
});
test('UI-SYNC-CLIP composition denied destination navigation replacement and concurrent text preserve blocks',async()=>{
 for(const kind of ['composing','denied','replay','not-authorized','navigation','replaced','typed']){
  const h=harness();if(kind==='composing')h.c.EditorHost.isComposing=()=>true;if(kind==='denied')h.c.activePageCanEdit=()=>false;
  if(['replay','not-authorized'].includes(kind)){h.state.pageData.page.collaborative_editing=true;h.state.liveCollaboration={connected:true,initialReplay:kind==='replay',writeAuthorized:kind!=='not-authorized'};}
  h.c.pasteSyncedReference(h.event,0);
  if(kind==='navigation'){h.state.navigation.sequence++;h.state.pageData=structuredClone(h.state.pageData);}if(kind==='replaced')h.node.isConnected=false;if(kind==='typed')h.state.pageData.blocks[0].content='입력 보존';
  if(!['composing','denied','replay','not-authorized'].includes(kind))h.resolve();await new Promise(setImmediate);assert.equal(h.c.dirty,0);assert.equal(h.state.pageData.blocks.length,1);
  if(kind==='typed')assert.equal(h.state.pageData.blocks[0].content,'입력 보존');
 }
});
test('UI-SYNC-CLIP identical projections retain exact target until read ends but edits release redraw',async()=>{
 const h=harness();h.c.pasteSyncedReference(h.event,0);h.state.pageData=structuredClone(h.state.pageData);
 assert.equal(h.c.deferSyncedClipboardRender(),true);h.resolve();await new Promise(setImmediate);
 assert.equal(h.c.dirty,1);assert.equal(h.c.deferSyncedClipboardRender(),false);
 const changed=harness();changed.c.pasteSyncedReference(changed.event,0);changed.state.pageData.blocks[0].content='보존';
 assert.equal(changed.c.deferSyncedClipboardRender(),false);changed.resolve();await new Promise(setImmediate);assert.equal(changed.c.dirty,0);
});

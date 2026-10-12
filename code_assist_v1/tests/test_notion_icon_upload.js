'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const icons = require('../notion_static/notion_icons.js');
const source = fs.readFileSync(require.resolve('../notion_static/notion_icons.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const png = name => new File([new Uint8Array([137,80,78,71,13,10,26,10])], name, {type:'image/png'});
function harness() {
  const nodes = new Map(), handlers = {}, decoded = [], revoked = [], calls = [], statuses = [];
  const ownerDocument = {activeElement:null};
  const node = key => {
    if (!nodes.has(key)) nodes.set(key, {disabled:false, hidden:true, value:'', children:[],
      replaceChildren(...children){this.children=children;}, addEventListener(name, fn){this[name]=fn;}, focus(){ownerDocument.activeElement=this;}});
    return nodes.get(key);
  };
  let resolveUpload;
  const context = {busy:false, disposed:false, selectedFile:null, uploadedValue:'', preview:'', selectionVersion:0,
    tab:'upload', colorsOpen:false, skinOpen:false, group:'', remoteAbort:null, AbortController, host:{
      upload:async file=>{calls.push(['upload',file.name]);return new Promise(resolve=>resolveUpload=resolve);},
      save:async value=>calls.push(['save',value]), close:()=>calls.push(['close'])},
    abort:new AbortController(), validateImage:icons.validateImage, fileValue:icons.fileValue, clipboardImage:icons.clipboardImage, clipboardUrl:icons.clipboardUrl,
    find:node, status:(text,error=false)=>statuses.push({text,error}),
    URL:{createObjectURL:file=>'blob:'+file.name,revokeObjectURL:url=>revoked.push(url)},
    Image:class {decode(){return new Promise((resolve,reject)=>decoded.push({image:this,resolve,reject}));}},
    root:{ownerDocument,setAttribute(){}, querySelectorAll:()=>[...nodes.values()], addEventListener:(name,fn)=>handlers[name]=fn}};
  vm.createContext(context);
  const load = (start,end) => {const from=source.indexOf(start),to=source.indexOf(end,from);assert(from>=0&&to>from);vm.runInContext(source.slice(from,to),context);};
  load('    function setBusy(', '    function setActive(');
  load('    root.addEventListener("click", event => {', '    find(\'input[type="search"]\').addEventListener("input"');
  load('    function resetImageSelection(', '    root.addEventListener("keydown", event => {');
  const paste = (data, target={}) => {const result={prevented:0,stopped:0};handlers.paste({target,clipboardData:data,
    preventDefault:()=>result.prevented++,stopPropagation:()=>result.stopped++});return result;};
  const click = name => handlers.click({target:{closest:()=>({dataset:{},hasAttribute:key=>key===name})}});
  return {context,node,handlers,decoded,revoked,calls,statuses,paste,click,finishUpload:id=>resolveUpload(id)};
}

test('UI-ICON-UPLOAD image clipboard supports files/items and leaves text alone',()=>{
  const file=png('clipboard.png');
  assert.equal(icons.clipboardImage({files:[file]}),file);
  assert.equal(icons.clipboardImage({items:[{kind:'file',type:'image/png',getAsFile:()=>null},{kind:'file',type:'image/png',getAsFile:()=>file}]}),file);
  assert.equal(icons.clipboardImage({files:[new File(['url'],'url.txt',{type:'text/plain'})]}),null);
  assert.equal(icons.clipboardImage(null),null);
});
test('UI-ICON-UPLOAD paste previews without saving until explicit save, then uses existing upload',async()=>{
  const h=harness(),file=png('pasted.png');
  assert.deepEqual(h.paste({files:[file]}),{prevented:1,stopped:1});
  await tick(); assert(h.node('[data-icon-apply]').disabled); assert.deepEqual(h.calls,[]);
  h.decoded[0].resolve(); await tick();
  assert.equal(h.context.selectedFile,file); assert.equal(h.node('.icon-upload-preview').hidden,false);
  assert.equal(h.node('[data-icon-apply]').disabled,false);
  h.click('data-icon-apply'); await tick(); assert.deepEqual(h.calls,[['upload','pasted.png']]);
  h.finishUpload('file_abc123'); await tick();
  assert.deepEqual(h.calls,[['upload','pasted.png'],['save','nf:file_abc123'],['close']]);
});
test('UI-ICON-UPLOAD stale decoding cannot replace a newer selection',async()=>{
  const h=harness();const first=h.context.selectImage(png('old.png'));await tick();
  const second=h.context.selectImage(png('new.png'));await tick();
  h.decoded[1].resolve();await second;h.decoded[0].resolve();await first;
  assert.equal(h.context.selectedFile.name,'new.png');assert.deepEqual(h.revoked,['blob:old.png']);
  assert.equal(h.node('.icon-upload-samples').children[0].src,'blob:new.png');
});
test('UI-ICON-UPLOAD cancel while decoding never publishes a file or saves',async()=>{
  const h=harness();const pending=h.context.selectImage(png('cancel.png'));await tick();
  h.click('data-icon-cancel');h.decoded[0].resolve();await pending;
  assert.deepEqual(h.calls,[['close']]);assert.equal(h.context.selectedFile,null);
  assert.deepEqual(h.revoked,['blob:cancel.png']);assert(h.node('[data-icon-apply]').disabled);
});
test('UI-ICON-UPLOAD invalid/undecodable images remain unsaved with a recoverable message',async()=>{
  const h=harness();await h.context.selectImage(new File(['bad'],'bad.png',{type:'image/png'}));
  assert(h.statuses.at(-1).error);assert.equal(h.decoded.length,0);
  const pending=h.context.selectImage(png('broken.png'));await tick();h.decoded[0].reject(new Error('decode'));await pending;
  assert.equal(h.context.selectedFile,null);assert(h.node('[data-icon-apply]').disabled);
  assert(h.node('.icon-upload-preview').hidden);assert.deepEqual(h.revoked,['blob:broken.png']);assert.deepEqual(h.calls,[]);
});
test('UI-ICON-UPLOAD file input can reselect the same file and cancelled chooser retains selection',async()=>{
  const h=harness(),input=h.node('input[type="file"]'),file=png('again.png');
  input.value='same-path';input.files=[file];input.change({target:input});await tick();h.decoded[0].resolve();await tick();
  assert.equal(input.value,'');input.files=[];input.change({target:input});assert.equal(h.context.selectedFile,file);
  input.files=[file];input.change({target:input});await tick();h.decoded[1].resolve();await tick();
  assert.deepEqual(h.revoked,['blob:again.png']);assert.equal(h.context.selectedFile,file);
});
test('UI-ICON-UPLOAD pending save, disallowed upload and other tabs do not replace the request',async()=>{
  const h=harness(),file=png('blocked.png');
  h.context.tab='emoji';assert.equal(h.paste({files:[file]}).prevented,0);
  h.context.tab='upload';h.context.host.allowUpload=false;assert.equal(h.paste({files:[file]}).prevented,0);
  h.context.host.allowUpload=true;h.context.busy=true;assert.equal(h.paste({files:[file]}).prevented,1);
  await tick();assert.equal(h.context.selectedFile,null);assert.equal(h.decoded.length,0);
  h.context.busy=false;h.context.disposed=true;assert.equal(h.paste({files:[file]}).prevented,0);
  h.context.disposed=false;assert.equal(h.paste({files:[]}).prevented,0);
});
test('UI-ICON-UPLOAD failed appearance save reuses its uploaded file on retry',async()=>{
  const h=harness();const selected=h.context.selectImage(png('retry.png'));await tick();h.decoded[0].resolve();await selected;
  let attempts=0;h.context.host.save=async value=>{h.calls.push(['save',value]);if(!attempts++)throw new Error('retry');};
  const first=h.context.apply('',h.context.selectedFile);await tick();h.finishUpload('file_retry');await first;
  assert(h.statuses.at(-1).error);await h.context.apply('',h.context.selectedFile);
  assert.equal(h.calls.filter(c=>c[0]==='upload').length,1);assert.equal(h.calls.filter(c=>c[0]==='save').length,2);
  assert.deepEqual(h.calls.at(-1),['close']);
});

function uploadHost() {
  const main=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
  const data={page:{document_sequence:9},blocks:[{content:'unsaved body'}]};
  const State={pageData:data,activePageId:'page',navigation:{workspaceId:'ws'},user:{id:'owner'},liveCollaboration:{durableCursor:8}};
  const receipt={contract:'code_assist.notion.collaborative_file_authority.v1',document_id:'page',id:'file_image',file_id:'file_image',state:'active',document_sequence:10};
  let finish;
  const context={State,window:{NotionIcons:icons},iconScope:()=>()=>State.navigation.workspaceId==='ws',
    collaborativeFileBasePath:()=>'/files',pageIsLocked:()=>false,pageCanChangeAppearance:()=>true,
    api:async()=>({page:{can_edit:true,document_sequence:9,workspace_epoch:3,document_epoch:1}}),authenticatedFetch:()=>{},
    ResumableFileUpload:{upload:()=>new Promise(resolve=>finish=()=>resolve({file:receipt}))}};
  vm.createContext(context);
  for(const [from,to] of [['async function uploadPageIcon(', 'async function openIconPicker('],['function currentDatabaseDocumentSequence()', 'async function sendCollaborativeDatabaseCommand(']]) {
    const start=main.indexOf(from),end=main.indexOf(to,start);assert(start>=0&&end>start);vm.runInContext(main.slice(start,end),context);
  }
  return {context,State,data,receipt,finish:()=>finish()};
}
test('UI-ICON-UPLOAD item appearance receives the file commit version without skipping body replay',async()=>{
  const h=uploadHost(),pending=h.context.uploadPageIcon(png('row.png'),'page',new AbortController().signal,{item:true});
  await tick();h.finish();assert.equal(await pending,'file_image');
  assert.equal(h.context.currentDatabaseDocumentSequence(),10);
  assert.equal(h.data.page.document_sequence,9);assert.equal(h.State.liveCollaboration.durableCursor,8);
  assert.deepEqual(h.data.blocks,[{content:'unsaved body'}]);
  h.receipt.document_sequence=7;
  const replay=h.context.uploadPageIcon(png('row.png'),'page',new AbortController().signal,{item:true});await tick();h.finish();await replay;
  assert.equal(h.context.currentDatabaseDocumentSequence(),10);
});
test('UI-ICON-UPLOAD late file receipts cannot set a different page or replacement page witness',async()=>{
  for(const change of [h=>h.State.activePageId='other',h=>h.State.navigation.workspaceId='other',h=>h.State.pageData={page:{document_sequence:1},blocks:[]}]) {
    const h=uploadHost(),pending=h.context.uploadPageIcon(png('late.png'),'page',new AbortController().signal,{item:true});
    await tick();change(h);h.finish();await pending;assert.equal(h.data.fileUploadSequence,undefined);assert.equal(h.State.pageData.fileUploadSequence,undefined);
  }
});
test('UI-ICON-UPLOAD mismatched or malformed receipt cannot advance the next item write',async()=>{
  for(const change of [r=>r.contract='wrong',r=>r.document_id='other',r=>r.file_id='other',r=>r.state='pending',r=>r.document_sequence=NaN,r=>r.document_sequence=0]) {
    const h=uploadHost();change(h.receipt);const pending=h.context.uploadPageIcon(png('invalid.png'),'page',new AbortController().signal,{item:true});
    await tick();h.finish();await assert.rejects(pending);assert.equal(h.data.fileUploadSequence,undefined);
  }
});

test('UI-ICON-UPLOAD changed page during upload shows a retryable Korean message',async()=>{
  const h=uploadHost();
  h.context.ResumableFileUpload.upload=async()=>{
    const error=new Error('upload source fence changed');error.status=409;throw error;
  };
  await assert.rejects(
    h.context.uploadPageIcon(png('changed.png'),'page',new AbortController().signal,{item:true}),
    error=>error.message==='업로드 중 페이지가 변경되었습니다. 다시 시도해 주세요.');
  assert.equal(h.data.fileUploadSequence,undefined);
});

test('UI-ICON-LINK plain URLs normalize and credentials or unrelated text are ignored',()=>{
  const parse=text=>icons.clipboardUrl({getData:kind=>kind==='text/plain'?text:''});
  assert.equal(parse(' https://example.com/한글.png '),'https://example.com/%ED%95%9C%EA%B8%80.png');
  for(const value of ['text','file:///x','data:image/png;base64,a','https://u:p@example.com/x','https://example.com/a b','https://example.com\\x','https://']) assert.equal(parse(value),'');
  assert.equal(icons.clipboardUrl(null),'');
});

test('UI-ICON-LINK pasted URL previews then saves through the established file host',async()=>{
  const h=harness();let signal;
  h.context.host.loadUrl=async(url,s)=>{assert.equal(url,'https://example.com/a.png');signal=s;return png('link.png');};
  assert.deepEqual(h.paste({getData:()=> 'https://example.com/a.png'}),{prevented:1,stopped:1});
  await tick();assert.equal(signal.aborted,false);assert.deepEqual(h.calls,[]);assert(h.node('[data-icon-apply]').disabled);
  h.decoded[0].resolve();await tick();assert.equal(h.context.selectedFile.name,'link.png');
  h.click('data-icon-apply');await tick();h.finishUpload('file_url');await tick();
  assert.deepEqual(h.calls,[['upload','link.png'],['save','nf:file_url'],['close']]);
});

test('UI-ICON-LINK newer file aborts old URL and ignores a response even if abort is ignored',async()=>{
  const h=harness();let finish,signal;
  h.context.host.loadUrl=(url,s)=>{signal=s;return new Promise(resolve=>finish=resolve);};
  const old=h.context.selectImage(null,'https://example.com/old.png');await tick();
  const next=h.context.selectImage(png('new.png'));await tick();assert(signal.aborted);
  h.decoded[0].resolve();await next;finish(png('old.png'));await old;
  assert.equal(h.context.selectedFile.name,'new.png');assert.equal(h.decoded.length,1);
});

test('UI-ICON-LINK cancel aborts URL and cannot surface late failures or persist a file',async()=>{
  const h=harness();let fail,signal;
  h.context.host.loadUrl=(url,s)=>{signal=s;return new Promise((resolve,reject)=>fail=reject);};
  const pending=h.context.selectImage(null,'https://example.com/x');await tick();
  const before=h.statuses.length;h.click('data-icon-cancel');fail(new Error('late'));await pending;
  assert(signal.aborted);assert.equal(h.statuses.length,before);assert.equal(h.context.selectedFile,null);
  assert.deepEqual(h.calls,[['close']]);
});

test('UI-ICON-LINK import failure permits retry, image clipboard takes precedence over text',async()=>{
  const h=harness();let calls=0;
  h.context.host.loadUrl=async()=>{calls++;throw new Error('공개된 이미지 주소인지 확인해 주세요.');};
  await h.context.selectImage(null,'https://example.com/private');
  assert(h.node('[data-icon-apply]').disabled);assert(h.statuses.at(-1).error);assert.deepEqual(h.calls,[]);
  h.paste({files:[png('actual.png')],getData:()=> 'https://example.com/url'});await tick();
  h.decoded[0].resolve();await tick();assert.equal(calls,1);assert.equal(h.context.selectedFile.name,'actual.png');
});

test('UI-ICON-LINK host uses authenticated preview only, validates bytes and carries cancellation',async()=>{
  const main=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
  const from=main.indexOf('async function loadIconImageUrl('),to=main.indexOf('async function uploadPageIcon(',from);
  const requests=[];let response={ok:true,blob:async()=>png('remote.png')};
  const context={File,window:{NotionIcons:icons},authenticatedFetch:async(...args)=>{requests.push(args);return response;}};
  vm.createContext(context);vm.runInContext(main.slice(from,to),context);
  const signal=new AbortController().signal,file=await context.loadIconImageUrl('https://example.com/x',signal);
  assert.equal(file.name,'링크 이미지.png');assert.equal(file.type,'image/png');
  assert.equal(requests[0][0],'/api/notion/icon-image-preview');assert.equal(requests[0][1].signal,signal);
  assert.equal(requests[0][1].body,JSON.stringify({url:'https://example.com/x'}));
  response={ok:false,json:async()=>({message:'이미지 오류'})};
  await assert.rejects(context.loadIconImageUrl('https://example.com/x',signal),/이미지 오류/);
  response={ok:true,blob:async()=>new Blob(['html'],{type:'text/html'})};
  await assert.rejects(context.loadIconImageUrl('https://example.com/x',signal));
});

test('UI-ICON-PREVIEW light and dark samples share original bytes and selection moves keyboard focus to Back',async()=>{
  const h=harness(),file=png('wide.png');h.node('input[type="file"]').focus();
  const pending=h.context.selectImage(file);await tick();h.decoded[0].resolve();await pending;
  const images=h.node('.icon-upload-samples').children;
  assert.equal(images.length,2);assert.equal(images[0].src,images[1].src);
  assert.equal(images[0].alt,'밝은 배경 아이콘 미리보기');assert.equal(images[1].alt,'어두운 배경 아이콘 미리보기');
  assert.equal(h.context.selectedFile,file);assert(h.node('[data-icon-upload-input]').hidden);
  assert(h.node('[data-icon-cancel]').hidden);assert(!h.node('[data-icon-back]').hidden);
  assert.equal(h.context.root.ownerDocument.activeElement,h.node('[data-icon-back]'));
});

test('UI-ICON-PREVIEW Back discards only the selection, frees preview and returns to upload without saving or closing',async()=>{
  const h=harness();const pending=h.context.selectImage(png('back.png'));await tick();h.decoded[0].resolve();await pending;
  h.context.uploadedValue='nf:file_old';h.click('data-icon-back');
  assert.deepEqual(h.calls,[]);assert.equal(h.context.selectedFile,null);assert.equal(h.context.uploadedValue,'');
  assert.deepEqual(h.revoked,['blob:back.png']);assert.equal(h.node('.icon-upload-samples').children.length,0);
  assert(h.node('.icon-upload-preview').hidden);assert(!h.node('[data-icon-upload-input]').hidden);
  assert(!h.node('[data-icon-cancel]').hidden);assert(h.node('[data-icon-back]').hidden);assert(h.node('[data-icon-apply]').disabled);
  assert.equal(h.context.root.ownerDocument.activeElement,h.node('input[type="file"]'));
  h.click('data-icon-cancel');assert.deepEqual(h.calls,[['close']]);
});

test('UI-ICON-PREVIEW replacement and Back ignore a late URL and cannot reuse its uploaded value',async()=>{
  const h=harness();let finish,signal;h.context.host.loadUrl=(url,s)=>{signal=s;return new Promise(resolve=>finish=resolve);};
  const pending=h.context.selectImage(null,'https://example.com/old');await tick();h.click('data-icon-back');
  assert(signal.aborted);finish(png('old.png'));await pending;assert.equal(h.decoded.length,0);assert.equal(h.context.selectedFile,null);
  const next=h.context.selectImage(png('new.png'));await tick();h.decoded[0].resolve();await next;
  h.click('data-icon-apply');await tick();h.click('data-icon-back');
  assert.equal(h.context.selectedFile.name,'new.png');h.finishUpload('file_new');await tick();
  assert.deepEqual(h.calls,[['upload','new.png'],['save','nf:file_new'],['close']]);
});

test('UI-ICON-PREVIEW decoding after the user changes tabs or focus does not take focus back',async()=>{
  const h=harness();h.node('input[type="file"]').focus();
  const pending=h.context.selectImage(png('late.png'));await tick();
  h.context.tab='emoji';h.node('emoji-tab').focus();h.decoded[0].resolve();await pending;
  assert.equal(h.context.root.ownerDocument.activeElement,h.node('emoji-tab'));
  assert.equal(h.context.selectedFile.name,'late.png');
});

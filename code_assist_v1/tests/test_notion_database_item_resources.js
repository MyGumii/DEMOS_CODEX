'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const media=require('../notion_static/notion_database_item_resources.js');
const escape=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
const block=(type='image',props={})=>({id:'image-a',type,content:'원래 캡션',props});
const host={escape,fileUrl:id=>'/file/'+encodeURIComponent(id),html:b=>escape(b.content),editable:true};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('UI-ITEM-RESOURCES renders supported media and retains authorized file lookup',()=>{
  for(const [type,tag] of [['image','img'],['audio','audio'],['video','video'],['pdf','iframe'],['embed','iframe'],['file','a'],['bookmark','a']]){
    const html=media.render(block(type,{file_id:'file_a',name:'자료 <one>'}),host);
    assert.match(html,new RegExp('<'+tag+'\\b'));assert.match(html,/\/file\/file_a/);assert.match(html,/원래 캡션/);
    assert.doesNotMatch(html,/<one>/);assert.match(html,/data-item-resource=/);
    if(type==='embed'){assert.match(html,/sandbox="allow-scripts allow-popups"/);assert.doesNotMatch(html,/allow-same-origin/);}
    if(type==='pdf'){assert.match(html,/data-item-pdf=/);assert.doesNotMatch(html,/<iframe[^>]+src=/);}
  }
});
test('UI-ITEM-RESOURCES unsafe URLs never enter src or href and ordinary links preserve queries',()=>{
  for(const value of ['javascript:alert(1)','data:text/html,a','file:///C:/x','//example.com/x','https://user:pass@example.com/x','https://example.com/\na','https://example.com\\x']){
    assert.equal(media.url(value),'');assert.doesNotMatch(media.render(block('embed',{url:value}),host),/<iframe/);
  }
  assert.equal(media.url(' https://example.com/a?q=한글 '),'https://example.com/a?q=%ED%95%9C%EA%B8%80');
});
test('UI-ITEM-RESOURCES read-only views expose playback and links without editing controls',()=>{
  const output=media.render(block('image',{file_id:'file_a'}),{...host,editable:false});
  assert.match(output,/<img/);assert.match(output,/contenteditable="false"/);assert.doesNotMatch(output,/data-item-resource=/);
  const empty=media.render({...block(),content:''},{...host,editable:false});assert.doesNotMatch(empty,/<button|contenteditable/);
});
test('UI-ITEM-RESOURCES replacing a source preserves captions, nested content and unrelated props',()=>{
  const input=block('image',{file_id:'old',url:'https://old.example',name:'old',original_name:'old',width:300,children:[block('paragraph')]});
  const before=structuredClone(input),link=media.attachment(input,{url:'https://example.com/new'});
  assert.deepEqual(input,before);assert.equal(link.content,input.content);assert.equal(link.props.width,300);assert.deepEqual(link.props.children,input.props.children);
  assert.equal(link.props.file_id,undefined);assert.equal(link.props.name,undefined);
  const file=media.attachment(link,{file_id:'new',name:'new.png'});assert.equal(file.props.url,undefined);assert.equal(file.props.file_id,'new');
});
function fixture(){
  let current=block(),editable=true;const pending=[],applied=[],status=[];
  const api=media.transfer({get:()=>current,editable:()=>editable,upload:(file,signal)=>new Promise((resolve,reject)=>pending.push({resolve,reject,file,signal})),
    apply:(id,value)=>{applied.push({id,value});current=value;return true;},status:value=>status.push(value)});
  return {api,pending,applied,status,get current(){return current;},set current(v){current=v;},revoke:()=>editable=false};
}
test('UI-ITEM-RESOURCES completed upload attaches to the same block and keeps edits made during upload',async()=>{
  const f=fixture(),promise=f.api.upload('image-a',{name:'photo.png'});await tick();f.current.content='새 캡션';
  f.pending[0].resolve({id:'file_a',original_name:'photo.png'});assert.equal(await promise,true);
  assert.equal(f.applied.length,1);assert.equal(f.current.content,'새 캡션');assert.equal(f.current.props.file_id,'file_a');
});
test('UI-ITEM-RESOURCES deleted, converted, replaced and revoked targets reject late attachments',async()=>{
  for(const mutate of [f=>f.current=null,f=>f.current.type='paragraph',f=>f.current.props.url='https://other.example',f=>f.revoke()]){
    const f=fixture(),promise=f.api.upload('image-a',{name:'photo.png'});await tick();mutate(f);f.pending[0].resolve({id:'file_a'});
    assert.equal(await promise,false);assert.equal(f.applied.length,0);assert.ok(f.status.some(s=>s.error));
  }
});
test('UI-ITEM-RESOURCES cancel and disposal abort upload and ignore even a successful late response',async()=>{
  for(const method of ['cancel','dispose']){
    const f=fixture(),promise=f.api.upload('image-a',{name:'photo.png'});await tick();f.api[method]();
    assert.equal(f.pending[0].signal.aborted,true);f.pending[0].resolve({id:'file_a'});assert.equal(await promise,false);assert.equal(f.applied.length,0);
  }
});
test('UI-ITEM-RESOURCES replacement upload and failure retry cannot attach an older result',async()=>{
  const f=fixture(),first=f.api.upload('image-a',{name:'one.png'});await tick();
  const second=f.api.upload('image-a',{name:'two.png'});await tick();assert.equal(f.pending[0].signal.aborted,true);
  f.pending[0].resolve({id:'old'});assert.equal(await first,false);f.pending[1].reject(Error('offline'));assert.equal(await second,false);
  assert.equal(f.applied.length,0);const retry=f.api.upload('image-a',{name:'two.png'});await tick();f.pending[2].resolve({id:'new'});assert.equal(await retry,true);assert.equal(f.current.props.file_id,'new');
});
test('UI-ITEM-RESOURCES a pasted non-image cannot be uploaded into an image block',async()=>{
  const f=fixture();assert.equal(await f.api.upload('image-a',{name:'manual.pdf',type:'application/pdf'}),false);
  assert.equal(f.pending.length,0);assert.equal(f.applied.length,0);assert.ok(f.status.some(s=>s.error?.includes('이미지')));
});
test('UI-ITEM-RESOURCES PDF preview accepts only bounded PDF bytes, not an executable response',async()=>{
  const signal=new AbortController().signal;
  const valid=await media.pdfPreview('/file/safe',signal,async()=>new Response('%PDF-1.4\nfixture',{headers:{'content-type':'application/pdf'}}));
  assert.equal(valid.type,'application/pdf');assert.equal(await valid.text(),'%PDF-1.4\nfixture');
  for(const response of [new Response('<script>alert(1)</script>',{headers:{'content-type':'text/html'}}),new Response('<script>alert(1)</script>',{headers:{'content-type':'application/pdf'}}),new Response('%PDF-',{headers:{'content-type':'application/pdf','content-length':String(65*1024*1024)}}),new Response('denied',{status:403})]){
    await assert.rejects(media.pdfPreview('/file/unsafe',signal,async()=>response));
  }
  const aborted=new AbortController();aborted.abort();await assert.rejects(media.pdfPreview('/file/safe',aborted.signal,async()=>new Response('%PDF-1.4',{headers:{'content-type':'application/pdf'}})));
});
test('UI-ITEM-RESOURCES failed picker upload keeps its retry action after pending state clears',async()=>{
  const previous={document:global.document,CSS:global.CSS};
  const listeners={},status={textContent:''},retry={hidden:true},input={focus(){},disabled:false};
  const panel={setAttribute(){},replaceChildren(){},remove(){},addEventListener:(name,fn)=>listeners[name]=fn,
    querySelector:selector=>selector==='[role=status]'?status:selector==='[data-resource-retry]'?retry:input,querySelectorAll:()=>[input]};
  const root={append(){},querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){},removeEventListener(){}};
  global.document={createElement:()=>panel,addEventListener(){},removeEventListener(){}};global.CSS={escape:value=>value};
  let attempts=0;
  try{
    const api=media.create(root,{...host,editable:()=>true,get:()=>block(),upload:async()=>{attempts++;throw Error('offline');},place(){}});
    api.open('image-a');listeners.change({target:{type:'file',files:[{name:'photo.png',type:'image/png'}]}});await tick();
    assert.equal(status.textContent,'offline');assert.equal(retry.hidden,false);assert.equal(input.disabled,false);assert.equal(attempts,1);
    api.dispose();
  }finally{global.document=previous.document;global.CSS=previous.CSS;}
});
